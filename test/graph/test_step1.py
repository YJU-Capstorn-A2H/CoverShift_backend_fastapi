# test_step1.py -- B3(重複配置の検査)・B5(承認ブロック・店長のやり直し回数)・B7(整理)・B9(確定後のLINE通知)のテスト
# ニセのLLM + メモリ保存。DB・API・お金は使わない
# 実行: test\graph フォルダで  python test_step1.py
# 실행: test\graph 폴더에서  python test_step1.py
import os, json
import sys, pathlib   # app/graph を探せるようにする(テストは test/graph に置いたため) / app/graph를 찾을 수 있게 한다(테스트를 test/graph에 두었기 때문)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "app" / "graph"))

# --- 1) テスト用のスタッフデータ / 테스트용 스태프 데이터 -------------------------------------
dates = [f"2026-11-{day:02d}" for day in range(1, 31)]
staff_list = [
    {"staff_id": 1, "staff_name": "社員A", "employment": "社員", "qualification": "登録販売者(管理者)",
     "staff_skill": "a", "admin_rate": "a", "closing_ok": True, "requests": []},
    {"staff_id": 2, "staff_name": "パートB", "employment": "パート", "qualification": None,
     "staff_skill": "a", "admin_rate": "a", "closing_ok": False,
     "requests": [{"date": date, "start": "09:00", "end": "13:00"} for date in dates]},
]
with open("staff_test.json", "w", encoding="utf-8") as staff_file:
    json.dump(staff_list, staff_file, ensure_ascii=False)

os.environ.update(DATABASE_URL="postgresql://dummy", ANTHROPIC_API_KEY="dummy", STAFF_FILE="staff_test.json")
import shift_graph
import node.llm, node.checks   # LLM は node/llm.py に、検査は node/checks.py に移った / LLM은 node/llm.py로, 검사는 node/checks.py로 이동
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

# 検査を、資格者ルールだけにする(人数ルール・公休ルールは切る)
# 검사를 자격자 규칙만으로(인원 규칙·공휴 규칙은 끈다)
shift_graph.STORE_SETTINGS[101].update(
    shain_off_days=None, min_headcount=[],
    qualification_rules=[{"qualification": ["登録販売者(管理者)"], "start": "08:00", "end": "22:30", "min": 1}])

# --- 2) 案の見本 / 안 견본 -----------------------------------------------------------------
def clean_draft():                       # 問題のない案 / 문제 없는 안
    draft = [{"staff_id": 1, "date": date, "start": "08:00", "end": "22:30"} for date in dates]
    draft += [{"staff_id": 2, "date": date, "start": "09:00", "end": "13:00"} for date in dates]
    return draft

def overlap_draft():                     # パートBが 11/3 に 9-13 と 12-15 で重なる / 파트B가 11/3에 겹침
    draft = clean_draft()
    draft.append({"staff_id": 2, "date": "2026-11-03", "start": "12:00", "end": "15:00"})
    return draft

def no_qualified_draft():                # 11/5 に、資格者(社員A)がいない / 11/5에 자격자(사원A)가 없음
    return [work for work in clean_draft() if not (work["staff_id"] == 1 and work["date"] == "2026-11-05")]


class FakeReply:
    def __init__(self, text):
        self.content = text
        self.usage_metadata = {}
        self.response_metadata = {"stop_reason": "end_turn"}


class FakeLLM:
    """drafts を順に返す。足りなくなったら、最後の案を返し続ける / drafts를 차례로 반환, 모자라면 마지막 안을 계속 반환"""
    def __init__(self, drafts):
        self.drafts = drafts
        self.call_count = 0

    def invoke(self, prompt):
        draft = self.drafts[min(self.call_count, len(self.drafts) - 1)]
        self.call_count += 1
        return FakeReply("```json\n" + json.dumps({"assignments": draft}, ensure_ascii=False) + "\n```")


def run(drafts, answers):
    """グラフを動かし、answers を順に渡して再開する。
    戻り値: (通ったノード, 承認待ちで出た内容の一覧, 最後の状態, LLM)"""
    llm = FakeLLM(drafts)
    node.llm.LLM = llm
    graph = shift_graph.builder.compile(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "t"}}
    graph_input = {"store_id": 101}
    path, approve_views = [], []
    for answer in answers + [None]:
        for chunk in graph.stream(graph_input, config, stream_mode="updates"):
            for node_name, update in chunk.items():
                if node_name == "__interrupt__":
                    value = update[0].value
                    path.append("止まる:" + str(value.get("ask")).split(" ")[0])
                    if "blocked_reasons" in value:
                        approve_views.append(value)
                else:
                    path.append(node_name)
        last_state = graph.get_state(config)
        if answer is None:
            break
        graph_input = Command(resume=answer)
    return path, approve_views, last_state, llm


def check(label, condition):
    print(("OK  " if condition else "NG  ") + label)
    if not condition:
        sys.exit(1)

# --- T1) check_double_booking の単体テスト ---------------------------------------------------
works = lambda *items: [{"staff_id": sid, "date": "2026-11-01", "start": s, "end": e} for sid, s, e in items]
check("T1a: 重ならない(9-13 と 13-17)→ 問題なし", node.checks.check_double_booking(works((1, "09:00", "13:00"), (1, "13:00", "17:00"))) == [])
check("T1b: 重なる(9-13 と 12-15)→ 1件", len(node.checks.check_double_booking(works((1, "09:00", "13:00"), (1, "12:00", "15:00")))) == 1)
check("T1c: 順番が逆でも見つかる", len(node.checks.check_double_booking(works((1, "12:00", "15:00"), (1, "09:00", "13:00")))) == 1)
check("T1d: 別の人なら重なっても問題なし", node.checks.check_double_booking(works((1, "09:00", "13:00"), (2, "09:00", "13:00"))) == [])
check("T1e: 別の日なら問題なし", node.checks.check_double_booking(
    [{"staff_id": 1, "date": "2026-11-01", "start": "09:00", "end": "13:00"}, {"staff_id": 1, "date": "2026-11-02", "start": "09:00", "end": "13:00"}]) == [])
check("T1f: 空の案でも動く", node.checks.check_double_booking([]) == [])

# --- T2) 重なった案 → 作り直し → きれいな案 ---------------------------------------------------
path, views, state, llm = run([overlap_draft(), clean_draft()], [])
print(path)
check("T2: 重なった案は検査で落ち、LLMが2回呼ばれる", llm.call_count == 2)
check("T2: 最後は承認待ちに止まる", path[-1] == "止まる:approved")
check("T2: 承認画面に、重なりの問題は残っていない", views[-1]["problems"] == [] and views[-1]["blocked_reasons"] == [])

# --- T3) 重なりが直らない → 手動編成 → 承認はブロック → 直して保存 → 承認 --------------------
path, views, state, llm = run([overlap_draft()], [
    "approved",
    {"decision": "edit_saved", "assignments": clean_draft()},
    "approved"])
print(path)
check("T3: 重なりが直らず、承認画面に問題つきで止まる(manual_draft は通らない)", "manual_draft" not in path and views[0]["problems"] != [])
check("T3: 1回目の承認はブロックされ、承認待ちに戻る", path.count("止まる:approved") >= 3)
blocked = [view for view in views if view["blocked_reasons"]]
check("T3: ブロックの理由に「重なっています」が出る", len(blocked) == 1 and "重なっています" in blocked[0]["blocked_reasons"][0])
check("T3: 直して保存→承認で、最後まで進む", path[-1] == "send_final_shift" and state.next == ())

# --- T4) 資格者なしでも、承認はブロック ---------------------------------------------------------
path, views, state, llm = run([no_qualified_draft()], [
    "approved",
    {"decision": "edit_saved", "assignments": clean_draft()},
    "approved"])
print(path)
blocked = [view for view in views if view["blocked_reasons"]]
check("T4: 資格者なしの案は、承認でブロックされる", len(blocked) == 1 and "資格者" in blocked[0]["blocked_reasons"][0])
check("T4: 直して保存→承認で、最後まで進む", path[-1] == "send_final_shift")

# --- T5) 店長のやり直しは2回まで ---------------------------------------------------------------
redraft = {"decision": "redraft_requested", "instruction": "もう少し減らして"}
path, views, state, llm = run([clean_draft()], [redraft, redraft, redraft, "approved"])
print(path)
check("T5: やり直しを頼めたのは2回 → LLMは 1+2=3回", llm.call_count == 3)
check("T5: 残り回数の表示が 2 → 1 → 0 → 0", [view["redraft_left"] for view in views] == [2, 1, 0, 0])
blocked = [view for view in views if view["blocked_reasons"]]
check("T5: 3回目はブロックされ、理由に「2回まで」が出る", len(blocked) == 1 and "2回まで" in blocked[0]["blocked_reasons"][0])
check("T5: そのあと承認で、最後まで進む", path[-1] == "send_final_shift")

# --- T6) ふつうの承認は、いつもどおり -----------------------------------------------------------
path, views, state, llm = run([clean_draft()], ["approved"])
print(path)
check("T6: 問題がなければ、1回の承認で確定する", path[-1] == "send_final_shift" and llm.call_count == 1)
check("T6: 承認画面に理由は出ない", views[0]["blocked_reasons"] == [])

# --- T7) 店長が直した案に問題が残っても、LLMに作り直させない ------------------------------------
path, views, state, llm = run([clean_draft()], [
    {"decision": "edit_saved", "assignments": no_qualified_draft()},
    {"decision": "edit_saved", "assignments": clean_draft()},
    "approved"])
print(path)
check("T7: 編集して保存しても、LLMは呼ばれない(1回のまま)", llm.call_count == 1)
check("T7: 問題が残る編集は、そのまま承認画面に戻る(manual_draft は通らない)", "manual_draft" not in path and path.count("止まる:approved") >= 3)
check("T7: 承認画面に、残った問題(資格者)が出る", any("資格者" in problem for problem in views[1]["problems"]))
check("T7: 直して保存→承認で確定し、案は店長が保存したものになる",
      path[-1] == "send_final_shift" and state.values["draft_store_shift"] == clean_draft())

# --- N) B9: 確定したら、勤務のある人に1通ずつ LINE(スタブ)が送られる ---------------------------
import node.line
node.line.SENT.clear()
path, views, state, llm = run([clean_draft()], ["approved"])
sent = {item["staff_id"]: item["text"] for item in node.line.SENT}
check("N1: 確定すると、勤務のある2人に、1通ずつ送られる", len(node.line.SENT) == 2 and sorted(sent) == [1, 2])
check("N2: 本文は、その人の勤務だけ(社員Aは 8:00〜22:30 の30日分、パートBは 9:00〜13:00 の30日分)",
      sent[1].count("08:00〜22:30") == 30 and "09:00〜13:00" not in sent[1]
      and sent[2].count("09:00〜13:00") == 30 and "08:00〜22:30" not in sent[2])
check("N3: 本文に、名前が入らない", all("社員A" not in text and "パートB" not in text for text in sent.values()))
check("N4: 最後まで進み、結果のメッセージに人数が出る", path[-1] == "send_final_shift" and "2人" in state.values["final_message"])
node.line.SENT.clear()
path, views, state, llm = run([overlap_draft()], ["approved"])
check("N5: 承認がブロックされて止まっている間は、1通も送られない", path[-1] == "止まる:approved" and node.line.SENT == [])
text = node.line.make_final_text([{"staff_id": 1, "date": "2026-11-03", "start": "09:00", "end": "13:00"},
                                  {"staff_id": 1, "date": "2026-11-01", "start": "08:00", "end": "12:00"}])
check("N6: 本文は日付の順で、曜日がつく", text.splitlines()[1] == "11/1(日) 08:00〜12:00" and text.splitlines()[2] == "11/3(火) 09:00〜13:00")

# --- S) B7: 抜いたものが、グラフに無い ---------------------------------------------------------
import routes
check("S1: skill_check / manual_draft は、グラフのノードに無い", not ({"skill_check", "manual_draft"} & set(shift_graph.builder.nodes)))
check("S2: route_1 は、routes.py に無い", not hasattr(routes, "route_1"))

print("ALL PASS")