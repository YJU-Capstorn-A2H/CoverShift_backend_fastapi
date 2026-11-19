"""アズキのグラフを、包み(AzukiGraph)越しに、DB つきで動かす試験。偽の LLM を、アズキの node.llm.LLM に入れる(本物は呼ばない)。
承認待ちで止まる → 下書きと違反が DB に入り、승혁 の ReviewWait の形で見える → 編集を再開で渡すと、新しい下書きができる。"""
import json
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

ROWS = [{"staff_id": 1, "date": "2026-11-02", "start": "09:00", "end": "13:00"},
        {"staff_id": 3, "date": "2026-11-02", "start": "09:00", "end": "13:00"}]


class FakeLLM:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def invoke(self, prompt):
        self.calls += 1
        text = "```json\n" + json.dumps({"assignments": self.rows}) + "\n```"
        return SimpleNamespace(content=text, usage_metadata={}, response_metadata={"stop_reason": "end_turn"})


@pytest.fixture
def azuki(world, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # アズキのグラフは llm_raw_output.txt などを、いまのフォルダに書く
    monkeypatch.setenv("DATABASE_URL", world.url)
    from app.graph_azuki_bridge import build_graph

    g = build_graph(InMemorySaver())
    import graph as G  # app/graph/graph.py (橋が、探す場所に足した)
    import node.llm as L
    import settings as S

    monkeypatch.setattr(S, "DB_URL", world.url)  # アズキの settings は import の時に DB_URL を決めるので、この試験の DB に向け直す
    monkeypatch.setattr(G, "DB_URL", world.url)
    fake = FakeLLM(ROWS)
    monkeypatch.setattr(L, "LLM", fake)
    return g, fake


def _cfg(t):
    return {"configurable": {"thread_id": t}}


def test_runs_to_review_and_saves_draft_with_violations(azuki, world):
    g, fake = azuki
    cfg = _cfg("az-1")
    g.invoke({"period_id": world.period_id}, cfg)
    snap = g.get_state(cfg)
    w = snap.interrupts[0].value
    assert w["type"] == "review" and w["draft_id"] is not None
    assert w["manager_request_limit"] == 2 and w["manager_request_count"] == 0
    assert world.count("shift_drafts") == 1
    assert world.count("shift_assignments", "draft_id = %s", (w["draft_id"],)) >= 2
    assert isinstance(w["violations"], list) and w["violations"], "승혁 の検査で、違反が出るはず(案が、ほとんど空のため)"
    assert all({"key", "check_code", "severity", "waivable"} <= set(v) for v in w["violations"])
    assert world.count("validation_violations", "draft_id = %s", (w["draft_id"],)) == len(w["violations"])
    # もう一度読んでも、下書きは増えない
    g.get_state(cfg)
    assert world.count("shift_drafts") == 1


def test_edit_saved_resume_creates_child_draft(azuki, world):
    g, fake = azuki
    cfg = _cfg("az-2")
    g.invoke({"period_id": world.period_id}, cfg)
    w1 = g.get_state(cfg).interrupts[0].value
    edited = [{"employee_id": 1, "work_date": "2026-11-02", "kind": "work", "start_time": "09:00", "end_time": "14:00"}]
    g.invoke(Command(resume={"decision": "edit_saved", "manager_id": 1, "draft_id": w1["draft_id"], "assignments": edited}), cfg)
    w2 = g.get_state(cfg).interrupts[0].value
    assert w2["type"] == "review" and w2["draft_id"] != w1["draft_id"]
    assert world.count("shift_drafts") == 2
    assert world.one("SELECT parent_draft_id FROM shift_drafts WHERE draft_id = %s", (w2["draft_id"],))["parent_draft_id"] == w1["draft_id"]
    assert world.count("manager_actions", "action = 'edit_saved'") == 1
    assert fake.calls >= 1
