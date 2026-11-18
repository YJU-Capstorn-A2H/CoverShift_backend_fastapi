# test_contact.py -- contact ノードのテスト (ニセのLLM + メモリ保存。DB・API・お金は使わない)
# 実行: app\graph フォルダに置いて  python test_contact.py
# 실행: app\graph 폴더에 두고  python test_contact.py
import os, json
import sys, pathlib   # app/graph を探せるようにする(テストは test/graph に置いたため) / app/graph를 찾을 수 있게 한다(테스트를 test/graph에 두었기 때문)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "app" / "graph"))

# --- 1) テスト用のスタッフデータを作る / 테스트용 스태프 데이터 만들기 ---------------------------
# 社員A(ID1) 1人、パートB(ID2) 1人、バイトC・D・E(ID3〜5)。
# パートB は 11/4 だけ休み → 11/4 の 9:00〜13:00 は、最大1人しか出られない(最低2人)。
# → バイトC・D・E に、順番に連絡する。
dates = [f"2026-11-{day:02d}" for day in range(1, 31)]
staff_list = [
    {"staff_id": 1, "staff_name": "社員A", "employment": "社員", "qualification": "登録販売者(管理者)",
     "staff_skill": "a", "admin_rate": "a", "closing_ok": True, "requests": []},
    {"staff_id": 2, "staff_name": "パートB", "employment": "パート", "qualification": None,
     "staff_skill": "a", "admin_rate": "a", "closing_ok": False,
     "requests": [{"date": date, "off": True} if date == "2026-11-04" else {"date": date, "start": "09:00", "end": "13:00"}
                  for date in dates]},
]
for staff_id, name in [(3, "バイトC"), (4, "バイトD"), (5, "バイトE")]:
    staff_list.append({"staff_id": staff_id, "staff_name": name, "employment": "バイト", "qualification": None,
                       "staff_skill": "a", "admin_rate": "a", "closing_ok": False, "requests": []})
with open("staff_test.json", "w", encoding="utf-8") as staff_file:
    json.dump(staff_list, staff_file, ensure_ascii=False)

# --- 2) shift_graph を読み込む(本物のDBにはつながない) / shift_graph 불러오기(실제 DB에는 연결하지 않음) ---
os.environ.update(DATABASE_URL="postgresql://dummy", ANTHROPIC_API_KEY="dummy", STAFF_FILE="staff_test.json")
import shift_graph
import node.llm, node.checks   # LLM は node/llm.py に、検査は node/checks.py に移った / LLM은 node/llm.py로, 검사는 node/checks.py로 이동
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

# 検査を「最低人数」だけに絞る / 검사를 '최저 인원'만으로 좁힌다
shift_graph.STORE_SETTINGS[101].update(
    qualification_rules=[], shain_off_days=None,
    min_headcount=[{"start": "09:00", "end": "13:00", "min": 2, "exclude": []}])

# --- 3) ニセのLLM: いつも同じ案を返す / 가짜 LLM: 항상 같은 안을 돌려준다 ----------------------------
fake_assignments = [{"staff_id": 1, "date": date, "start": "08:00", "end": "22:30"} for date in dates]
fake_assignments += [{"staff_id": 2, "date": date, "start": "09:00", "end": "13:00"} for date in dates if date != "2026-11-04"]


class FakeReply:
    def __init__(self, text):
        self.content = text
        self.usage_metadata = {}
        self.response_metadata = {"stop_reason": "end_turn"}


class FakeLLM:
    call_count = 0

    def invoke(self, prompt):
        FakeLLM.call_count += 1
        return FakeReply("```json\n" + json.dumps({"assignments": fake_assignments}, ensure_ascii=False) + "\n```")


node.llm.LLM = FakeLLM()


# --- 4) 実行の道具 / 실행 도구 ----------------------------------------------------------------
def run(thread_id, answers):
    """グラフを動かし、answers の返事を順に渡して再開する。
    그래프를 실행하고, answers의 답변을 차례로 넘겨 재개한다.
    戻り値: (通ったノードの一覧, 最後の状態)"""
    graph = shift_graph.builder.compile(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": thread_id}}
    graph_input = {"store_id": 101}
    path = []
    for answer in answers + [None]:
        for chunk in graph.stream(graph_input, config, stream_mode="updates"):
            for node_name, update in chunk.items():
                if node_name == "__interrupt__":                  # 止まった場所 / 멈춘 곳
                    path.append("止まる:" + str(update[0].value.get("ask")) + ":" + str(update[0].value.get("staff_id", "")))
                else:
                    path.append(node_name)
        last_state = graph.get_state(config)
        if answer is None:
            break
        graph_input = Command(resume=answer)
    return path, last_state


def check(label, condition):
    print(("OK  " if condition else "NG  ") + label)
    if not condition:
        sys.exit(1)


# --- 5) テスト / 테스트 ---------------------------------------------------------------------
# A) 1人目(バイトC)が「可能」
path, state = run("A", ["可能"])
print(path)
check("A: 可能 → validate → approve で、承認待ちに止まる", path[-1].startswith("止まる:approved"))
check("A: 案に、バイトC(ID3)の11/4が足された",
      {"staff_id": 3, "date": "2026-11-04", "start": "09:00", "end": "13:00"} in state.values["draft_store_shift"])
check("A: 連絡は1人だけ", state.values["contact_asked"] == 1 and state.values["contact_log"][0]["answer"] == "可能")
check("A: LLMは1回だけ(作り直しなし)", FakeLLM.call_count == 1)

# B) 断り → 不確か → 断り (3人使い切る)
FakeLLM.call_count = 0
path, state = run("B", ["断り", "不確か", "断り"])
print(path)
check("B: 3人に聞いて、承認画面に進む(manual_draft は通らない)",
      path.count("contact") == 3 and "manual_draft" not in path and path[-1].startswith("止まる:approved"))
check("B: 記録は3件で、聞いた順は ID3, 4, 5",
      [log_item["staff_id"] for log_item in state.values["contact_log"]] == [3, 4, 5])
check("B: 案は変わらない(誰も足されない)",
      all(not (work["staff_id"] == 3 and work["date"] == "2026-11-04") for work in state.values["draft_store_shift"]))

# C) 1人目が「断り」、2人目(バイトD)が「可能」
path, state = run("C", ["断り", "可能"])
print(path)
check("C: 2人目(ID4)で決まって、承認待ちに止まる",
      state.values["contact_log"][-1]["staff_id"] == 4 and path[-1].startswith("止まる:approved"))

# D) 不正な返事
try:
    run("D", ["たぶん"])
    check("D: 不正な返事は、エラーになる", False)
except ValueError as error:
    check("D: 不正な返事は、エラーになる: " + str(error), True)

# E) 可能 → 承認 まで通して、最後まで進む
graph = shift_graph.builder.compile(checkpointer=MemorySaver())
config = {"configurable": {"thread_id": "E"}}
for graph_input in [{"store_id": 101}, Command(resume="可能"), Command(resume="approved")]:
    list(graph.stream(graph_input, config, stream_mode="updates"))
check("E: 可能 → 承認 で最後まで進み、次のノードが空", graph.get_state(config).next == ())

print("ALL PASS")