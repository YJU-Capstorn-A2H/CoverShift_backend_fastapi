# test_db.py -- B1-2 のテスト: DB から読んで collect に入れる(period_id を渡す)
# 実行前に、テスト用に使ってよい DB の URL を、環境変数 TEST_DATABASE_URL に入れる(無ければ、このテストは何もせず終わる)
#   例(PowerShell):  $env:TEST_DATABASE_URL = "postgresql://covershift:パスワード@localhost:5433/covershift"
#   ・テストは cs_test_b12 という専用の schema(フォルダのようなもの)を作って、その中だけで動く。終わったら消す
#   ・既存のテーブルは、読みも書きもしない(public のテーブルの「形」だけをコピーする)
# 実行: test\graph フォルダで  python test_db.py
import os, json
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "app" / "graph"))

BASE_URL = os.environ.get("TEST_DATABASE_URL")
if not BASE_URL:
    print("SKIP: 環境変数 TEST_DATABASE_URL がないので、DB のテストは行いません")
    sys.exit(0)

SCHEMA = "cs_test_b12"
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, quote
parts = urlsplit(BASE_URL)
query = dict(parse_qsl(parts.query))
query["options"] = f"-c search_path={SCHEMA}"                       # 接続したら、テスト用の schema だけを見る
TEST_URL = urlunsplit(parts._replace(query=urlencode(query, quote_via=quote)))   # 空白は + でなく %20 にする

with open("staff_test.json", "w", encoding="utf-8") as staff_file:   # settings を import するために必要(中身は使わない)
    json.dump([{"staff_id": 1, "staff_name": "X", "employment": "社員", "qualification": None,
                "staff_skill": "a", "admin_rate": "a", "closing_ok": True, "requests": []}], staff_file)
os.environ.update(DATABASE_URL=TEST_URL, ANTHROPIC_API_KEY="dummy", STAFF_FILE="staff_test.json")

# import はここにまとめる(上の「app/graph を探す設定」と「環境変数」のあとでないと動かない)
import datetime
import psycopg
import graph as graph_module
import node.checks as checks
import node.db as db
import node.llm
import settings
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

failed = []
def check(name, ok):
    print(("OK  " if ok else "NG  ") + name)
    if not ok:
        failed.append(name)

DATE = datetime.date
TABLES = ["shift_periods", "store_time_rules", "employees", "availability_submissions"]
QUAL = "登録販売者(管理者)"


def make_test_data():
    """専用 schema を作り、テスト用の行を入れる。戻り値: (period_id, {名前: employee_id})"""
    with psycopg.connect(BASE_URL, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")     # 前回の残りを消す(このテスト専用の名前)
        conn.execute(f"CREATE SCHEMA {SCHEMA}")
        for table in TABLES:                                        # public のテーブルと同じ形の空のテーブルを作る
            conn.execute(f"CREATE TABLE {SCHEMA}.{table} (LIKE public.{table} INCLUDING ALL)")

        period_id = conn.execute(
            f"INSERT INTO {SCHEMA}.shift_periods (store_id, target_month, submission_deadline, designated_off_days) "
            "VALUES (7, '2026-12-01', '2026-11-25 23:59+09', 9) RETURNING period_id").fetchone()[0]
        for weekday in range(1, 8):                                 # 店7: 毎日 09-13 に 1人以上、うち資格者1人
            conn.execute(f"INSERT INTO {SCHEMA}.store_time_rules (store_id, weekday, start_time, end_time, min_headcount, min_qualified) "
                         "VALUES (7, %s, '09:00', '13:00', 1, 1)", (weekday,))
        conn.execute(f"INSERT INTO {SCHEMA}.store_time_rules (store_id, weekday, start_time, end_time, min_headcount, min_qualified) "
                     "VALUES (8, 1, '09:00', '13:00', 9, 9)")        # 別の店のルール(読まれてはいけない)

        ids = {}
        for store_id, name, is_regular, has_qual, closing_ok in [
                (7, "社員A", True, True, True), (7, "社員B", True, True, True), (7, "バイトC", False, False, True), (8, "別店D", False, False, False)]:
            ids[name] = conn.execute(
                f"INSERT INTO {SCHEMA}.employees (store_id, name, is_regular, has_qualification, closing_ok) VALUES (%s, %s, %s, %s, %s) RETURNING employee_id",
                (store_id, name, is_regular, has_qual, closing_ok)).fetchone()[0]

        for name, work_date, kind, start, end in [
                ("バイトC", DATE(2026, 12, 2), "available", "13:30", "22:00"),
                ("バイトC", DATE(2026, 11, 30), "available", "09:00", "13:00"),    # 前の月(読まれてはいけない)
                ("バイトC", DATE(2027, 1, 1), "available", "09:00", "13:00"),      # 次の月(読まれてはいけない)
                ("社員A", DATE(2026, 12, 24), "off", None, None),
                ("別店D", DATE(2026, 12, 3), "available", "09:00", "13:00")]:      # 別の店の人(読まれてはいけない)
            conn.execute(f"INSERT INTO {SCHEMA}.availability_submissions (employee_id, work_date, kind, start_time, end_time) VALUES (%s, %s, %s, %s, %s)",
                         (ids[name], work_date, kind, start, end))
    return period_id, ids


class FakeReply:
    def __init__(self, text):
        self.content = text
        self.usage_metadata = {}
        self.response_metadata = {"stop_reason": "end_turn"}


class FakeLLM:                                   # 案を1つ返す。渡された文章(prompt)も覚えておく
    def __init__(self, draft):
        self.draft = draft
        self.prompts = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return FakeReply("```json\n" + json.dumps({"assignments": self.draft}, ensure_ascii=False) + "\n```")


period_id, ids = make_test_data()
try:
    # --- D1) DB から読む ------------------------------------------------------------------
    data = db.load_period_data(TEST_URL, period_id)
    by_id = {staff["staff_id"]: staff for staff in data["staff_list"]}
    by_name = {name: by_id.get(ids[name]) for name in ids}       # テストで見やすいように、作った名前 → 読んだデータ
    check("D1a: 店7 の 3人だけ読む(別の店の人は入らない)", [staff["staff_id"] for staff in data["staff_list"]] == [ids["社員A"], ids["社員B"], ids["バイトC"]])
    check("D1b: store_id と period(12月・31日)", data["store_id"] == 7 and data["period"] == {"start": "2026-12-01", "days": 31})
    check("D1c: バイトC の希望は 12/2 だけ(前の月・次の月は入らない)", by_name["バイトC"]["requests"] == [{"date": "2026-12-02", "start": "13:30", "end": "22:00"}])
    check("D1d: 社員A の希望休が入る", by_name["社員A"]["requests"] == [{"date": "2026-12-24", "off": True}])
    check("D1e: 社員・バイト・資格者・closing_ok が入る",
          by_name["社員A"]["employment"] == "社員" and by_name["バイトC"]["employment"] == "バイト"
          and by_name["社員A"]["qualification"] == QUAL and by_name["バイトC"]["qualification"] is None and by_name["バイトC"]["closing_ok"] is True)
    check("D1f: 人数ルール(毎日 09-13 に1人。別の店の9人は入らない)", data["store_settings"]["min_headcount"] == [{"start": "09:00", "end": "13:00", "min": 1}])
    check("D1g: 資格者ルールと公休日数", data["store_settings"]["qualification_rules"] == [{"qualification": [QUAL], "start": "09:00", "end": "13:00", "min": 1}]
          and data["store_settings"]["shain_off_days"] == 9)
    try:
        db.load_period_data(TEST_URL, period_id + 999)
        check("D2: 無い period_id はエラー", False)
    except ValueError:
        check("D2: 無い period_id はエラー", True)
    check("D3: 名前は読まない(staff_name は全員 None)", all(staff["staff_name"] is None for staff in data["staff_list"]))

    # --- D4) collect ----------------------------------------------------------------------
    update = graph_module.collect({"period_id": period_id})
    check("D4a: collect が store_id・period・store_settings を入れる", update["store_id"] == 7 and update["period"]["days"] == 31 and update["store_settings"]["shain_off_days"] == 9)
    check("D4b: llm_all_staff は3人で、名前が入っていない", len(update["llm_all_staff"]) == 3 and "社員A" not in json.dumps(update["llm_all_staff"], ensure_ascii=False))
    check("D4c: カウンターの初期値も、今までどおり入る", update["return_count"] == 0 and update["contact_limit"] == 3 and update["draft_store_shift"] == [])
    try:
        graph_module.collect({"period_id": period_id, "store_id": 8})
        check("D5: period_id と store_id の店が違うとエラー", False)
    except ValueError:
        check("D5: period_id と store_id の店が違うとエラー", True)
    check("D6: period_id が無ければ、今までどおり staff_data.json(テストでは staff_test.json)", graph_module.collect({"store_id": 101})["llm_all_staff"][0]["staff_id"] == 1)

    # --- D7) 名前 -------------------------------------------------------------------------
    check("D7a: get_name は、period_id があれば「ID」の形(DB を見ない)", graph_module.get_name({"period_id": period_id}, ids["バイトC"]) == f"ID{ids['バイトC']}")
    check("D7b: get_name は、period_id が無ければ staff_data.json の表から", graph_module.get_name({}, 1) == "X" and graph_module.get_name({}, 99) == "ID99")

    # --- D8) グラフを最後まで動かす(store_id=7 は settings.STORE_SETTINGS に無い店) ---------------
    dates = checks.all_dates(data["period"])
    draft = [{"staff_id": ids["社員A"], "date": date, "start": "08:00", "end": "22:30"} for date in dates[:22]]      # 12/1〜12/22
    draft += [{"staff_id": ids["社員B"], "date": date, "start": "08:00", "end": "22:30"} for date in dates[9:]]       # 12/10〜12/31
    llm = FakeLLM(draft)
    node.llm.LLM = llm
    graph = graph_module.builder.compile(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "db-test"}}
    path = []
    graph_input = {"period_id": period_id}
    for answer in ["approved", None]:
        for chunk in graph.stream(graph_input, config, stream_mode="updates"):
            for node_name, node_update in chunk.items():
                path.append("止まる" if node_name == "__interrupt__" else node_name)
        if answer is None:
            break
        graph_input = Command(resume=answer)
    final_state = graph.get_state(config)
    check("D8a: DB から読んで、承認まで進む", path[:4] == ["collect", "auto_draft", "validate", "止まる"] and path[-1] == "send_final_shift")
    check("D8b: LLM は1回だけ(DB の店7 の設定で、案が通った)", len(llm.prompts) == 1)
    check("D8c: LLM に渡した文章に、名前が入っていない", all(name not in llm.prompts[0] for name in ("社員A", "社員B", "バイトC", "別店D")))
    check("D8d: LLM に渡した文章に、12月の公休ルール(22日出勤)と DB の人数ルールが入る", "22日" in llm.prompts[0] and "09:00〜13:00" in llm.prompts[0])
    check("D8e: 最後の状態に、store_id=7 と period_id が残る", final_state.values["store_id"] == 7 and final_state.values["period_id"] == period_id)
    check("D8f: State に名前が入っていない", all(name not in json.dumps(final_state.values, ensure_ascii=False, default=str) for name in ("社員A", "社員B", "バイトC")))
    check("D8g: 店7 は settings.STORE_SETTINGS に無い(DB の設定を使った証拠)", 7 not in settings.STORE_SETTINGS)
finally:
    with psycopg.connect(BASE_URL, autocommit=True) as conn:        # テスト用の schema を消す
        conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")

if failed:
    print("失敗:", failed)
    sys.exit(1)
print("ALL PASS")
