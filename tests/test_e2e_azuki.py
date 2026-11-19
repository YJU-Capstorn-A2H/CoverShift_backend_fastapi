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


def _make_shortage(url):
    """パート・バイトの希望(availability)を、全部消す → 「理論上、人が足りない日」が出て、その人たちが打診の候補になる(あなたの checks.py の仕様)。"""
    with psycopg.connect(url) as c:
        c.execute("DELETE FROM availability_submissions WHERE employee_id IN (SELECT employee_id FROM employees WHERE NOT is_regular)")
        c.execute("UPDATE employees SET line_user_id = 'U' || employee_id::text WHERE line_user_id IS NULL")  # 答えるボタンの本人確認(line_user_id)に要る
        c.commit()


def test_contact_flow_through_api_with_azuki_graph(api, monkeypatch):
    """人が足りない日がある → グラフが contact で止まる → 仮LINEの打診 → 答え(断り→可能)で再開 → 最後は承認待ち。
    見るのは: 止まる種類(waiting_contact)・contact_log の記録・仮LINE・答えの本人確認・二重回答の拒否。"""
    cl, h, url, fake = api
    pid = cl.get("/api/shift-periods", headers=h).json()[0]["period_id"]
    fake.period_id = pid
    _make_shortage(url)
    # 偽の案は、アズキの検査をたくさん破る(希望を消したので)→ valid にならず、打診に進めない。
    # ここは「打診の配線」を見るテストなので、個別の検査だけ空にして案を valid にする。
    # 「人が足りない日」の判定(impossible_cases / make_shortage_report / build_contact_queue)は、本物のまま使う。
    import graph as G

    for name in ("check_coverage", "check_qualification", "check_requests", "check_closing",
                 "check_shain_days", "check_headcount", "check_double_booking"):
        monkeypatch.setattr(G, name, lambda *a, **k: [])

    assert cl.post(f"/api/shift-periods/{pid}/start-now", headers=h).status_code == 202
    st = _wait(cl, pid, h)
    if st["state"] != "waiting_contact":
        pytest.fail(f"打診で止まらなかった(ここが、知りたいこと)。state={st['state']} waiting={str(st.get('waiting'))[:400]}")
    w = st["waiting"]
    assert w["type"] == "contact" and w["contact_id"]

    with psycopg.connect(url, row_factory=dict_row) as c:
        log = c.execute("SELECT * FROM contact_log WHERE contact_id = %s", (w["contact_id"],)).fetchone()
        box = [r for r in c.execute("SELECT kind, body FROM line_outbox WHERE kind = 'contact_ask'").fetchall()]
        line_user = c.execute("SELECT line_user_id FROM employees WHERE employee_id = %s", (w["employee_id"],)).fetchone()["line_user_id"]
    assert log["status"] == "asked" and log["employee_id"] == w["employee_id"]
    assert any(m["body"]["contact_id"] == w["contact_id"] for m in box)

    # 他人の line_user_id では、答えられない(403)
    bad = cl.post("/api/line/reply", json={"contact_id": w["contact_id"], "line_user_id": "someone-else", "answer": "yes"})
    assert bad.status_code == 403, bad.text

    # 1人目: 断り → 次の人へ(次の打診で止まる、または、候補が尽きて承認待ち)
    first = w["contact_id"]
    r = cl.post("/api/line/reply", json={"contact_id": first, "line_user_id": line_user, "answer": "no"})
    assert r.status_code == 202, r.text
    st = _wait(cl, pid, h)
    assert st["state"] in ("waiting_contact", "waiting_review"), st
    with psycopg.connect(url) as c:
        assert c.execute("SELECT status FROM contact_log WHERE contact_id = %s", (first,)).fetchone()[0] == "declined"

    # 同じ打診への2回目の答えは、拒否(409)
    again = cl.post("/api/line/reply", json={"contact_id": first, "line_user_id": line_user, "answer": "yes"})
    assert again.status_code == 409, again.text

    # 次の打診が来ていれば「可能」と答える。足りれば、承認待ちへ進む
    if st["state"] == "waiting_contact":
        w2 = st["waiting"]
        assert w2["contact_id"] != first
        with psycopg.connect(url) as c:
            lu = c.execute("SELECT line_user_id FROM employees WHERE employee_id = %s", (w2["employee_id"],)).fetchone()[0]
        r = cl.post("/api/line/reply", json={"contact_id": w2["contact_id"], "line_user_id": lu, "answer": "yes"})
        assert r.status_code == 202, r.text
        st = _wait(cl, pid, h)
        with psycopg.connect(url) as c:
            assert c.execute("SELECT status FROM contact_log WHERE contact_id = %s", (w2["contact_id"],)).fetchone()[0] == "accepted"
        assert st["state"] in ("waiting_contact", "waiting_review"), st
