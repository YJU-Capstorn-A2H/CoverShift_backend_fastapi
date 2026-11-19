"""끝에서 끝까지 (메인 세션 소유): 입구 API → 진짜 LocalRunner → 진짜 그래프 → 진짜 검증 → 가짜 LLM(LLM_MODE=fake)
→ 승인 대기 → 승인 → 확정 → LINE 스텁 통지. 각 모듈 에이전트가 따로 만든 것이 계약대로 붙는지 본다.

데이터: 아즈키 staff_data(2026-11, 17명) → app.seed.seed_db. LLM 은 부르지 않는다.
"""
from __future__ import annotations

import time

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row


def _wait(cl, pid, h, until=("running", "not_started"), timeout=60.0):
    end = time.monotonic() + timeout
    while True:
        st = cl.get(f"/api/shift-periods/{pid}/run-status", headers=h).json()
        if st["state"] not in until or time.monotonic() > end:
            return st
        time.sleep(0.2)


@pytest.fixture
def api(clean_db, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "e2e-only-secret-0123456789abcdefghij")
    monkeypatch.setenv("DEV_SKIP_AUTH", "false")
    monkeypatch.setenv("LLM_MODE", "fake")
    monkeypatch.setenv("FAKE_LLM_SCENARIO", "greedy")
    monkeypatch.setenv("RUNNER", "local")
    monkeypatch.setenv("MCP_MODE", "inprocess")
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
        yield cl, {"Authorization": f"Bearer {tok}"}, clean_db
    reset_runner()


@pytest.mark.req("M-1", "F-1", "F-7", "F-9", "F-10", "A-14", "A-16", "A-17")
def test_whole_flow_through_api_with_real_graph_and_fake_llm(api):
    cl, h, url = api
    pid = cl.get("/api/shift-periods", headers=h).json()[0]["period_id"]

    # 시작 전: 편성안 0건 (F-1 의 출발점)
    with psycopg.connect(url) as c:
        assert c.execute("SELECT count(*) FROM shift_drafts").fetchone()[0] == 0

    r = cl.post(f"/api/shift-periods/{pid}/start-now", headers=h)
    assert r.status_code == 202, r.text
    # 깨뜨리는 쪽: 두 번째 「지금 시작」은 막힌다 (F-10)
    assert cl.post(f"/api/shift-periods/{pid}/start-now", headers=h).json()["error"]["code"] in ("ALREADY_STARTED", "BUSY")

    st = _wait(cl, pid, h)
    assert st["state"] == "waiting_review", st
    w = st["waiting"]
    assert w["type"] == "review" and w["result"] in ("valid", "limit_reached")

    # 그래프가 보낸 위반과 API 가 DB 에서 읽은 위반이 key · severity 까지 같다 (G21: severity 를 저장한다)
    d = cl.get(f"/api/drafts/{w['draft_id']}", headers=h).json()
    assert sorted((v["key"], v["severity"]) for v in d["violations"]) == \
        sorted((v["key"], v["severity"]) for v in w["violations"])
    with psycopg.connect(url) as c:
        assert c.execute("SELECT count(*) FROM validation_violations WHERE severity IS NULL").fetchone()[0] == 0

    # 깨뜨리는 쪽: 본 적 없는 편성안 번호로 승인 → 409 STALE_DRAFT, 아무것도 확정되지 않음
    bad = cl.post(f"/api/shift-periods/{pid}/review", headers=h,
                  json={"decision": "approved", "draft_id": w["draft_id"] + 100, "waived_keys": []})
    assert (bad.status_code, bad.json()["error"]["code"]) == (409, "STALE_DRAFT")

    waivable = [v["key"] for v in w["violations"] if v["waivable"]]
    r = cl.post(f"/api/shift-periods/{pid}/review", headers=h,
                json={"decision": "approved", "draft_id": w["draft_id"], "waived_keys": waivable})
    assert r.status_code == 202, r.text
    st = _wait(cl, pid, h)
    if st["state"] == "waiting_review":  # 예외 불가 위반이 남아 막힌 경우 — 그 사실을 그대로 드러낸다
        pytest.fail(f"승인이 막힘 (blocked): {st['waiting']['blocked'][:3]}")
    assert st["state"] == "confirmed", st
    assert st.get("confirmed_draft_id") == w["draft_id"]  # 화면이 확정표를 다시 열 수 있게 (web 계약 변경 요청 1)

    with psycopg.connect(url) as c:
        confirmed = c.execute("SELECT draft_id, confirmed_by FROM shift_drafts WHERE status = 'confirmed'").fetchall()
        n_emp = c.execute("SELECT count(*) FROM employees").fetchone()[0]
        approvals = c.execute("SELECT count(*) FROM manager_actions WHERE action = 'approved'").fetchone()[0]
    assert confirmed == [(w["draft_id"], 1)]  # 확정 1건 · 승인자 = JWT 의 관리자 (F-7)
    assert approvals == 1  # A-16

    box = cl.get("/api/dev/line-outbox", headers=h).json()
    confirms = [m for m in box if m["kind"] == "shift_confirmed"]
    assert len(confirms) == n_emp and len({m["employee_id"] for m in confirms}) == n_emp  # 직원마다 1번 (F-9)
    assert "line_user_id" in confirms[0]  # 스텁 화면의 답장 버튼이 쓸 값 (web 계약 변경 요청 2)

    # 깨뜨리는 쪽: 확정 뒤 같은 승인을 다시 보내도 두 번 반영되지 않는다
    again = cl.post(f"/api/shift-periods/{pid}/review", headers=h,
                    json={"decision": "approved", "draft_id": w["draft_id"], "waived_keys": waivable})
    assert again.status_code == 409
    assert len([m for m in cl.get("/api/dev/line-outbox", headers=h).json() if m["kind"] == "shift_confirmed"]) == n_emp
