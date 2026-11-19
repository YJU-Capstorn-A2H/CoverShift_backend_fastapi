"""끝에서 끝까지 — 아즈키의 그래프(GRAPH_IMPL=azuki): 입구 API(승혁) → LocalRunner → 包み(AzukiGraph) → アズキのグラフ → 승혁 の検査 → 승인 → 확정.
LLM は、アズキの node.llm.LLM に、偽物を入れる(승혁 の FakeComposer の greedy 案を、アズキの行の形にして返す)。本物は呼ばない。
tests/test_e2e.py(승혁 のグラフ版)と、同じ流れを、同じ確認項目で見る。"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row


def _wait(cl, pid, h, until=("running", "not_started"), timeout=90.0):
    end = time.monotonic() + timeout
    while True:
        st = cl.get(f"/api/shift-periods/{pid}/run-status", headers=h).json()
        if st["state"] not in until or time.monotonic() > end:
            return st
        time.sleep(0.2)


class GreedyAsAzukiLLM:
    """승혁 の greedy 案を、アズキのグラフが読む JSON({"assignments": [{staff_id, date, start, end}]})にして返す偽LLM。"""

    def __init__(self):
        self.period_id = None
        self.calls = 0

    def invoke(self, prompt):
        from app.graph_seung import nodes as N
        from app.graph_seung.deps import default_deps
        from app.graph_seung.state import ctx_from_state
        from app.llm.fake import FakeComposer

        self.calls += 1
        deps = default_deps()
        st = {"period_id": self.period_id}
        st.update(N.collect(st, deps))
        res = FakeComposer("greedy").compose(ctx_from_state(st, deps.settings), previous=None, history=[], instruction=None)
        rows = [{"staff_id": a.employee_id, "date": a.work_date.isoformat(), "start": a.start_time.strftime("%H:%M"),
                 "end": a.end_time.strftime("%H:%M")} for a in res.assignments]
        text = "```json\n" + json.dumps({"assignments": rows}) + "\n```"
        return SimpleNamespace(content=text, usage_metadata={}, response_metadata={"stop_reason": "end_turn"})


@pytest.fixture
def api(clean_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # アズキのグラフが書く llm_raw_output.txt などの置き場
    monkeypatch.setenv("JWT_SECRET", "e2e-only-secret-0123456789abcdefghij")
    monkeypatch.setenv("DEV_SKIP_AUTH", "false")
    monkeypatch.setenv("LLM_MODE", "fake")
    monkeypatch.setenv("FAKE_LLM_SCENARIO", "greedy")
    monkeypatch.setenv("RUNNER", "local")
    monkeypatch.setenv("MCP_MODE", "inprocess")
    monkeypatch.setenv("GRAPH_IMPL", "azuki")
    monkeypatch.setenv("DATABASE_URL", clean_db)
    import app.graph_azuki_bridge as bridge

    if bridge._GRAPH_DIR not in sys.path:
        sys.path.append(bridge._GRAPH_DIR)
    import graph as G  # アズキの app/graph/graph.py
    import node.llm as L
    import settings as S

    monkeypatch.setattr(S, "DB_URL", clean_db)
    monkeypatch.setattr(G, "DB_URL", clean_db)
    fake = GreedyAsAzukiLLM()
    monkeypatch.setattr(L, "LLM", fake)

    from app.runner import reset_runner
    from app.seed import seed_db

    reset_runner()
    with psycopg.connect(clean_db, row_factory=dict_row) as c:
        seed_db(c, manager_email="e2e@example.test", password="e2e-password-1")
        c.commit()
    from app.api_seung.main import app

    with TestClient(app) as cl:
        tok = cl.post("/api/auth/manager/login",
                      json={"email": "e2e@example.test", "password": "e2e-password-1"}).json()["access_token"]
        yield cl, {"Authorization": f"Bearer {tok}"}, clean_db, fake
    reset_runner()


def test_whole_flow_through_api_with_azuki_graph(api):
    cl, h, url, fake = api
    pid = cl.get("/api/shift-periods", headers=h).json()[0]["period_id"]
    fake.period_id = pid

    r = cl.post(f"/api/shift-periods/{pid}/start-now", headers=h)
    assert r.status_code == 202, r.text
    st = _wait(cl, pid, h)
    assert st["state"] == "waiting_review", f"承認待ちで止まらなかった: {st}"
    w = st["waiting"]
    assert w["type"] == "review" and w["draft_id"] is not None

    # 승혁 の画面が使う API が、DB の下書きを、読める
    d = cl.get(f"/api/drafts/{w['draft_id']}", headers=h).json()
    assert sorted((v["key"], v["severity"]) for v in d["violations"]) == sorted((v["key"], v["severity"]) for v in w["violations"])
    with psycopg.connect(url) as c:
        assert c.execute("SELECT count(*) FROM validation_violations WHERE severity IS NULL").fetchone()[0] == 0

    # 知らない draft_id の承認は、拒む(STALE_DRAFT)
    bad = cl.post(f"/api/shift-periods/{pid}/review", headers=h,
                  json={"decision": "approved", "draft_id": w["draft_id"] + 100, "waived_keys": []})
    assert (bad.status_code, bad.json()["error"]["code"]) == (409, "STALE_DRAFT")

    waivable = [v["key"] for v in w["violations"] if v["waivable"]]
    r = cl.post(f"/api/shift-periods/{pid}/review", headers=h,
                json={"decision": "approved", "draft_id": w["draft_id"], "waived_keys": waivable})
    assert r.status_code == 202, r.text
    st = _wait(cl, pid, h)
    if st["state"] == "waiting_review":
        pytest.fail(f"承認が止められた。blocked={st['waiting']['blocked'][:3]} notes={st['waiting']['notes'][:3]}")
    assert st["state"] == "confirmed", st
    assert st.get("confirmed_draft_id") == w["draft_id"]

    with psycopg.connect(url) as c:
        confirmed = c.execute("SELECT draft_id, confirmed_by FROM shift_drafts WHERE status = 'confirmed'").fetchall()
        n_emp = c.execute("SELECT count(*) FROM employees").fetchone()[0]
    assert confirmed == [(w["draft_id"], 1)]
    box = cl.get("/api/dev/line-outbox", headers=h).json()
    confirms = [m for m in box if m["kind"] == "shift_confirmed"]
    assert len(confirms) == n_emp
