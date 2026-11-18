# test_mcp.py -- D5 のテスト: MCP サーバー(app/mcp/server.py)の道具 get_staff_availability と、collect の切り替え(STAFF_SOURCE)
# 実行前に、テスト用に使ってよい DB の URL を、環境変数 TEST_DATABASE_URL に入れる(無ければ、このテストは何もせず終わる)
#   例(PowerShell):  $env:TEST_DATABASE_URL = "postgresql://covershift:パスワード@localhost:5433/covershift"
#   ・テストは cs_test_d5 という専用の schema を作って、その中だけで動く。終わったら消す
#   ・既存のテーブルは、読みも書きもしない(public のテーブルの「形」だけをコピーする)
# 実行: test\graph フォルダで  python test_mcp.py
import os, json
import sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app" / "graph"))

BASE_URL = os.environ.get("TEST_DATABASE_URL")
if not BASE_URL:
    print("SKIP: 環境変数 TEST_DATABASE_URL がないので、MCP のテストは行いません")
    sys.exit(0)

SCHEMA = "cs_test_d5"
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, quote
parts = urlsplit(BASE_URL)
query = dict(parse_qsl(parts.query))
query["options"] = f"-c search_path={SCHEMA}"                       # 接続したら、テスト用の schema だけを見る
TEST_URL = urlunsplit(parts._replace(query=urlencode(query, quote_via=quote)))

with open("staff_test.json", "w", encoding="utf-8") as staff_file:   # settings を import するために必要(中身は使わない)
    json.dump([{"staff_id": 1, "staff_name": "X", "employment": "社員", "qualification": None,
                "staff_skill": "a", "admin_rate": "a", "closing_ok": True, "requests": []}], staff_file)
os.environ.update(DATABASE_URL=TEST_URL, ANTHROPIC_API_KEY="dummy-not-for-mcp", STAFF_FILE="staff_test.json")

# import はここにまとめる(上の「app/graph を探す設定」と「環境変数」のあとでないと動かない)
import asyncio
import datetime
import importlib.util
import psycopg
from mcp import Client
import graph as graph_module
import node.db as db
import node.mcp_tools as mcp_tools
from node.convert import split_names
import settings

failed = []
def check(name, ok):
    print(("OK  " if ok else "NG  ") + name)
    if not ok:
        failed.append(name)

DATE = datetime.date
TABLES = ["shift_periods", "store_time_rules", "employees", "availability_submissions"]
STAFF_KEYS = {"staff_id", "employment", "qualification", "staff_skill", "admin_rate", "closing_ok", "requests"}   # C2f のキーから staff_name を除いたもの
SERVER = settings.SERVER


def make_test_data():
    """専用 schema を作り、テスト用の行を入れる。戻り値: (店7の period_id, ルールの無い店9の period_id)"""
    with psycopg.connect(BASE_URL, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        conn.execute(f"CREATE SCHEMA {SCHEMA}")
        for table in TABLES:
            conn.execute(f"CREATE TABLE {SCHEMA}.{table} (LIKE public.{table} INCLUDING ALL)")
        period_ids = []
        for store_id in (7, 9):
            period_ids.append(conn.execute(
                f"INSERT INTO {SCHEMA}.shift_periods (store_id, target_month, submission_deadline, designated_off_days) "
                "VALUES (%s, '2026-12-01', '2026-11-25 23:59+09', 9) RETURNING period_id", (store_id,)).fetchone()[0])
        conn.execute(f"INSERT INTO {SCHEMA}.store_time_rules (store_id, weekday, start_time, end_time, min_headcount, min_qualified) "
                     "VALUES (7, 1, '09:00', '13:00', 1, 1)")          # 店9 にはルールを入れない(注意の print が出る)
        ids = {}
        for store_id, name, is_regular, has_qual in [(7, "社員A", True, True), (7, "バイトC", False, False), (8, "別店D", False, False), (9, "店9E", False, False)]:
            ids[name] = conn.execute(
                f"INSERT INTO {SCHEMA}.employees (store_id, name, is_regular, has_qualification, closing_ok) VALUES (%s, %s, %s, %s, true) RETURNING employee_id",
                (store_id, name, is_regular, has_qual)).fetchone()[0]
        for name, work_date, kind, start, end in [
                ("バイトC", DATE(2026, 12, 2), "available", "13:30", "22:00"),
                ("バイトC", DATE(2027, 1, 1), "available", "09:00", "13:00"),      # 次の月(読まれてはいけない)
                ("社員A", DATE(2026, 12, 24), "off", None, None),
                ("別店D", DATE(2026, 12, 3), "available", "09:00", "13:00")]:      # 別の店の人(読まれてはいけない)
            conn.execute(f"INSERT INTO {SCHEMA}.availability_submissions (employee_id, work_date, kind, start_time, end_time) VALUES (%s, %s, %s, %s, %s)",
                         (ids[name], work_date, kind, start, end))
    return period_ids


def load_server_module():          # app/mcp/server.py を読み込む(同じプロセスの中でつなぐテスト用)
    spec = importlib.util.spec_from_file_location("covershift_mcp_server", SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def list_tools_in_process(server):
    async with Client(server) as client:
        return (await client.list_tools()).tools


period_id, period_id_no_rules = make_test_data()
try:
    # --- M1) 道具の一覧 -------------------------------------------------------------------
    tools = asyncio.run(list_tools_in_process(load_server_module().server))
    check("M1a: 道具は get_staff_availability の1つだけ", [tool.name for tool in tools] == ["get_staff_availability"])
    check("M1b: 読むだけの印(read_only_hint)が付いている", tools[0].annotations.read_only_hint is True and tools[0].annotations.destructive_hint is False)
    check("M1c: LLM に見せる道具は無い(LLM_TOOLS が空)", mcp_tools.LLM_TOOLS == set())

    # --- M2) 子プロセス(stdio)で呼ぶ ----------------------------------------------------------
    result = mcp_tools.call_mcp_tool(SERVER, "get_staff_availability", {"period_id": period_id})
    _, expected = split_names(db.load_period_data(TEST_URL, period_id)["staff_list"])
    check("M2a: store_id は店7", result["store_id"] == 7)
    check("M2b: DB 直読みと同じ従業員データ(店7 の2人)", result["staff"] == expected and len(result["staff"]) == 2)
    check("M2c: キーは staff_data.json と同じ(staff_name だけ無い)", all(set(staff) == STAFF_KEYS for staff in result["staff"]))
    check("M2d: 名前が入っていない", all(name not in json.dumps(result, ensure_ascii=False) for name in ("社員A", "バイトC", "別店D")))
    by_id = {staff["staff_id"]: staff for staff in result["staff"]}
    check("M2e: その月の希望だけ(次の月・別の店は入らない)", sorted(len(staff["requests"]) for staff in result["staff"]) == [1, 1])

    # --- M3) エラー・出力 -----------------------------------------------------------------
    try:
        mcp_tools.call_mcp_tool(SERVER, "get_staff_availability", {"period_id": period_id + 999})
        check("M3a: 無い period_id はエラー(理由つき)", False)
    except RuntimeError as error:
        check("M3a: 無い period_id はエラー(理由つき)", "shift_periods にありません" in str(error))
    result_no_rules = mcp_tools.call_mcp_tool(SERVER, "get_staff_availability", {"period_id": period_id_no_rules})
    check("M3b: ルールの無い店9 でも呼べる(注意の print は標準エラーへ)", result_no_rules["store_id"] == 9 and len(result_no_rules["staff"]) == 1)

    # --- M4) 子プロセスに渡す環境変数 ------------------------------------------------------------
    check("M4: サーバーに渡すのは DATABASE_URL だけ(API キーは渡さない)", mcp_tools.SERVER_ENV_KEYS == ["DATABASE_URL"])

    # --- M5) collect の切り替え(STAFF_SOURCE) ------------------------------------------------------
    graph_module.STAFF_SOURCE = "mcp"
    update_mcp = graph_module.collect({"period_id": period_id})
    graph_module.STAFF_SOURCE = "db"
    update_db = graph_module.collect({"period_id": period_id})
    graph_module.STAFF_SOURCE = settings.STAFF_SOURCE
    check("M5a: mcp と db で、collect の結果がまったく同じ", update_mcp == update_db)
    check("M5b: mcp のとき、collect が store_id・period・store_settings を入れる", update_mcp["store_id"] == 7 and update_mcp["period"]["days"] == 31 and update_mcp["store_settings"]["shain_off_days"] == 9)
    check("M5c: 既定(環境変数なし)は mcp", "STAFF_SOURCE" in os.environ or settings.STAFF_SOURCE == "mcp")
    check("M5d: with_staff=False なら従業員を読まない", db.load_period_data(TEST_URL, period_id, with_staff=False)["staff_list"] is None)
finally:
    with psycopg.connect(BASE_URL, autocommit=True) as conn:        # テスト用の schema を消す
        conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")

if failed:
    print("失敗:", failed)
    sys.exit(1)
print("ALL PASS")
