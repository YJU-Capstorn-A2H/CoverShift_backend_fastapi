"""편성 제어 (G1, A-14, A-15, A-16, F-7, F-10, M-3). 러너는 FakeRunner — 러너 계약대로 부르는지만 본다."""
from __future__ import annotations

import io
from datetime import date

import psycopg
import re

import pytest

from app.api.deps import ENTRANCE_LOCK_NS, AlreadyStarted, Busy, NotWaiting, StaleDraft, _lock_key
from app.domain import Violation
from ops import issue_invite

from ._util import BUSY_MESSAGE, PW, err


@pytest.fixture
def started(two_stores):
    api = two_stores
    api.h = api.stores["A"]["h"]
    api.eid = api.employee(api.h)
    api.pid = api.period(api.h)
    assert api.c.post(f"/api/shift-periods/{api.pid}/start-now", headers=api.h).status_code == 202
    api.tid = api.sql("SELECT thread_id FROM shift_periods WHERE period_id = %s", (api.pid,))[0]["thread_id"]
    return api


def _review(api, **body):
    return api.c.post(f"/api/shift-periods/{api.pid}/review", headers=api.h, json=body)


def _hold_entrance_lock(api, thread_id):
    c = psycopg.connect(api.url, autocommit=True)
    c.execute("SELECT pg_advisory_lock(%s, %s)", (ENTRANCE_LOCK_NS, _lock_key(thread_id)))
    return c


@pytest.mark.req("F-10", "A-14", "M-1")
def test_start_now_stores_thread_id_and_starts_runner_once(two_stores):
    api = two_stores
    h = api.stores["A"]["h"]
    pid = api.period(h)
    # 깨뜨리는 쪽: JWT 없이 → 401, 러너까지 가지 않는다 (A-14)
    assert err(api.c.post(f"/api/shift-periods/{pid}/start-now")) == (401, "UNAUTHORIZED")
    assert api.runner.calls == []
    r = api.c.post(f"/api/shift-periods/{pid}/start-now", headers=h)
    assert r.status_code == 202
    tid = r.json()["thread_id"]
    assert re.fullmatch(rf"period-{pid}-[0-9a-f]{{8}}", tid)  # G22: 기간 번호 + 임의 꼬리
    assert r.json() == {"period_id": pid, "thread_id": tid, "state": "running", "waiting": None, "error": None}
    assert api.sql("SELECT thread_id FROM shift_periods WHERE period_id = %s", (pid,)) == [{"thread_id": tid}]
    assert ("start", pid, tid) in api.runner.calls
    # 두 번째 지금 시작 → 409, 러너 start 는 1번뿐
    assert err(api.c.post(f"/api/shift-periods/{pid}/start-now", headers=h)) == (409, "ALREADY_STARTED")
    assert api.runner.names().count("start") == 1


@pytest.mark.req("F-10")
def test_start_now_can_retry_same_thread_when_runner_never_started(two_stores):
    api = two_stores
    h = api.stores["A"]["h"]
    pid = api.period(h)
    api.runner.raise_on["start"] = RuntimeError("runner down")
    with pytest.raises(RuntimeError):  # TestClient 는 500 원인을 다시 던진다
        api.c.post(f"/api/shift-periods/{pid}/start-now", headers=h)
    tid = api.sql("SELECT thread_id FROM shift_periods")[0]["thread_id"]  # thread 는 남는다
    assert tid.startswith(f"period-{pid}-")
    del api.runner.raise_on["start"]
    assert api.c.post(f"/api/shift-periods/{pid}/start-now", headers=h).status_code == 202  # 러너가 not_started → 다시
    assert [c for c in api.runner.calls if c[0] == "start"] == [("start", pid, tid)] * 2  # 같은 thread 로 다시


@pytest.mark.req("A-15")
def test_start_now_busy_is_409_with_fixed_message(two_stores):
    api = two_stores
    h = api.stores["A"]["h"]
    pid = api.period(h)
    api.runner.raise_on["start"] = Busy()
    r = api.c.post(f"/api/shift-periods/{pid}/start-now", headers=h)
    assert err(r) == (409, "BUSY") and r.json()["error"]["message"] == BUSY_MESSAGE
    api.runner.raise_on["start"] = AlreadyStarted("x")
    assert err(api.c.post(f"/api/shift-periods/{pid}/start-now", headers=h)) == (409, "ALREADY_STARTED")
    # 입구 잠금을 다른 요청이 쥐고 있으면 러너를 부르기 전에 409
    del api.runner.raise_on["start"]
    holder = _hold_entrance_lock(api, f"period-{pid}")
    try:
        n = len(api.runner.calls)
        r = api.c.post(f"/api/shift-periods/{pid}/start-now", headers=h)
        assert err(r) == (409, "BUSY") and r.json()["error"]["message"] == BUSY_MESSAGE
        assert len(api.runner.calls) == n
    finally:
        holder.close()
    assert api.c.post(f"/api/shift-periods/{pid}/start-now", headers=h).status_code == 202  # 풀리면 통과


@pytest.mark.req("M-1")
def test_run_status_passes_runner_status_and_not_started_without_thread(two_stores):
    api = two_stores
    h = api.stores["A"]["h"]
    pid = api.period(h)
    assert api.c.get(f"/api/shift-periods/{pid}/run-status", headers=h).json()["state"] == "not_started"
    assert api.runner.calls == []
    api.c.post(f"/api/shift-periods/{pid}/start-now", headers=h)
    api.runner.wait_review(pid, 3)
    st = api.c.get(f"/api/shift-periods/{pid}/run-status", headers=h).json()
    assert st["state"] == "waiting_review" and st["waiting"]["draft_id"] == 3


@pytest.mark.req("F-7", "A-14", "M-3")
def test_review_payload_uses_manager_id_from_jwt_for_all_three_decisions(started):
    api = started
    # 교대 뒤 관리자(id ≠ 1)로 승인 → payload 의 manager_id 가 JWT 의 관리자인지 (본문에서 받지 않는다)
    buf = io.StringIO()
    issue_invite.main(["--store-id", str(api.stores["A"]["store_id"])], stdout=buf)
    code = buf.getvalue().split("invite_code=")[1].split()[0]
    new_id = api.c.post("/api/auth/register", json={"invite_code": code, "email": "new@a.test",
                                                    "password": PW}).json()["manager_id"]
    assert new_id != api.stores["A"]["manager_id"]
    api.h = api.login("new@a.test")

    api.runner.wait_review(api.pid, 7)
    r = _review(api, decision="approved", draft_id=7, waived_keys=["REGULAR_ABSENT|2026-12-01||09:00|"])
    assert r.status_code == 202
    assert api.runner.calls[-1] == ("resume", api.pid, api.tid, {
        "decision": "approved", "manager_id": new_id, "draft_id": 7,
        "waived_keys": ["REGULAR_ABSENT|2026-12-01||09:00|"]})

    api.runner.wait_review(api.pid, 8)
    assert _review(api, decision="redraft_requested", draft_id=8, instruction="오전 인원을 늘려라").status_code == 202
    assert api.runner.calls[-1][3] == {"decision": "redraft_requested", "manager_id": new_id, "draft_id": 8,
                                       "instruction": "오전 인원을 늘려라"}

    api.runner.wait_review(api.pid, 9)
    a = [{"employee_id": api.eid, "work_date": "2026-12-01", "kind": "work", "start_time": "09:00",
          "end_time": "17:30", "break_start": "12:00", "break_end": "13:00"},
         {"employee_id": api.eid, "work_date": "2026-12-02", "kind": "paid_leave"}]
    assert _review(api, decision="edit_saved", draft_id=9, assignments=a).status_code == 202
    assert api.runner.calls[-1][3] == {"decision": "edit_saved", "manager_id": new_id, "draft_id": 9, "assignments": [
        {**a[0]}, {**a[1], "start_time": None, "end_time": None, "break_start": None, "break_end": None}]}
    # 깨뜨리는 쪽: 본문에 manager_id 를 넣으면 받지 않는다 / JWT 없음 → 401
    api.runner.wait_review(api.pid, 10)
    assert err(_review(api, decision="approved", draft_id=10, waived_keys=[], manager_id=1)) == (
        422, "VALIDATION_ERROR")
    assert err(api.c.post(f"/api/shift-periods/{api.pid}/review", json={"decision": "approved", "draft_id": 10})) == (
        401, "UNAUTHORIZED")
    assert err(_review(api, decision="maybe", draft_id=10)) == (422, "VALIDATION_ERROR")


@pytest.mark.req("M-3")
def test_edit_saved_rejects_bad_assignments_before_runner(started):
    api = started
    api.runner.wait_review(api.pid, 9)
    other = api.employee(api.stores["B"]["h"], "남")
    ok = {"employee_id": api.eid, "work_date": "2026-12-01", "kind": "work", "start_time": "09:00", "end_time": "13:00"}
    cases = [
        ({**ok, "start_time": "09:15"}, "VALIDATION_ERROR"),  # 30분 단위 (V9_ko 447)
        ({**ok, "end_time": "09:00"}, "VALIDATION_ERROR"),
        ({**ok, "kind": "paid_leave"}, "VALIDATION_ERROR"),  # 유급에 시각
        ({**ok, "break_start": "08:00", "break_end": "09:00"}, "VALIDATION_ERROR"),  # 근무 밖 휴게
        ({**ok, "employee_id": other}, "INVALID_ITEM"),  # 남의 점포 직원 (A-5)
        ({**ok, "work_date": "2027-01-01"}, "INVALID_ITEM"),  # 다른 달
    ]
    n = api.runner.names().count("resume")
    for a, code in cases:
        assert err(_review(api, decision="edit_saved", draft_id=9, assignments=[a])) == (422, code), a
    assert err(_review(api, decision="edit_saved", draft_id=9, assignments=[ok, ok])) == (422, "INVALID_ITEM")
    assert api.runner.names().count("resume") == n


@pytest.mark.req("A-15", "A-16")
def test_review_stale_not_waiting_and_busy(started):
    api = started
    # 러너가 아직 도는 중 → BUSY (같은 문장)
    r = _review(api, decision="approved", draft_id=7, waived_keys=[])
    assert err(r) == (409, "BUSY") and r.json()["error"]["message"] == BUSY_MESSAGE
    api.runner.wait_review(api.pid, 7)
    assert err(_review(api, decision="approved", draft_id=6, waived_keys=[])) == (409, "STALE_DRAFT")
    api.runner.wait_contact(api.pid, 1)
    assert err(_review(api, decision="approved", draft_id=7, waived_keys=[])) == (409, "NOT_WAITING")
    api.runner.states[api.tid] = api.runner.rs(api.pid, api.tid, "confirmed")
    assert err(_review(api, decision="approved", draft_id=7, waived_keys=[])) == (409, "NOT_WAITING")
    assert "resume" not in api.runner.names()
    # 러너가 resume 에서 막은 경우도 같은 모양
    api.runner.wait_review(api.pid, 7)
    for exc, code in ((Busy(), "BUSY"), (StaleDraft("x"), "STALE_DRAFT"), (NotWaiting("x"), "NOT_WAITING")):
        api.runner.raise_on["resume"] = exc
        assert err(_review(api, decision="approved", draft_id=7, waived_keys=[])) == (409, code)
    del api.runner.raise_on["resume"]
    # 버튼 두 번 누르기: 앞 요청이 입구 잠금을 쥐고 있으면 뒤 요청은 러너를 부르지 않고 409 BUSY (V9_ko 505)
    holder = _hold_entrance_lock(api, f"period-{api.pid}")
    try:
        n = len(api.runner.calls)
        r = _review(api, decision="approved", draft_id=7, waived_keys=[])
        assert err(r) == (409, "BUSY") and r.json()["error"]["message"] == BUSY_MESSAGE
        assert len(api.runner.calls) == n
    finally:
        holder.close()
    assert _review(api, decision="approved", draft_id=7, waived_keys=[]).status_code == 202


@pytest.mark.req("A-14")
def test_review_before_start_is_not_waiting(two_stores):
    api = two_stores
    h = api.stores["A"]["h"]
    pid = api.period(h)
    r = api.c.post(f"/api/shift-periods/{pid}/review", headers=h,
                   json={"decision": "approved", "draft_id": 1, "waived_keys": []})
    assert err(r) == (409, "NOT_WAITING") and api.runner.calls == []


@pytest.mark.req("M-3", "F-6")
def test_get_draft_returns_names_for_manager_and_violation_keys_match_domain(started):
    api = started
    e2 = api.employee(api.h, "Bさん", False)
    did = api.sql("INSERT INTO shift_drafts (period_id) VALUES (%s) RETURNING draft_id", (api.pid,))[0]["draft_id"]
    api.sql("""INSERT INTO shift_assignments (draft_id, employee_id, work_date, kind, start_time, end_time,
                                              break_start, break_end)
               VALUES (%s, %s, '2026-12-01', 'work', '09:00', '17:00', '12:00', '12:30'),
                      (%s, %s, '2026-12-01', 'paid_leave', NULL, NULL, NULL, NULL)""", (did, api.eid, did, e2))
    api.sql("""INSERT INTO validation_violations (draft_id, check_code, work_date, employee_id, detail)
               VALUES (%s, 'QUAL_MISSING', '2026-12-01', NULL, '{"time": "09:00", "need": 1, "have": 0}'),
                      (%s, 'HEADCOUNT_SHORTAGE', '2026-12-01', NULL, '{"time": "13:00", "need": 3, "have": 2}'),
                      (%s, 'HEADCOUNT_SHORTAGE', '2026-12-01', NULL,
                       '{"time": "18:00", "need": 3, "have": 2, "rule_key": "r2"}'),
                      (%s, 'OUTSIDE_AVAILABILITY', '2026-12-01', %s, '{"kind": "part"}')""",
            (did, did, did, did, e2))
    d = api.c.get(f"/api/drafts/{did}", headers=api.h).json()
    assert {k: d[k] for k in ("draft_id", "period_id", "status", "parent_draft_id", "confirmed_at",
                              "confirmed_by")} == {"draft_id": did, "period_id": api.pid, "status": "draft",
                                                   "parent_draft_id": None, "confirmed_at": None,
                                                   "confirmed_by": None}
    assert d["assignments"] == [
        {"employee_id": api.eid, "employee_name": "Aさん", "work_date": "2026-12-01", "kind": "work",
         "start_time": "09:00", "end_time": "17:00", "break_start": "12:00", "break_end": "12:30"},
        {"employee_id": e2, "employee_name": "Bさん", "work_date": "2026-12-01", "kind": "paid_leave",
         "start_time": None, "end_time": None, "break_start": None, "break_end": None}]
    want_key = Violation("QUAL_MISSING", "required", date(2026, 12, 1), None, {"time": "09:00"}).key
    v = d["violations"]
    assert v[0]["key"] == want_key and v[0]["waivable"] is False and v[0]["severity"] == "required"
    assert [x["severity"] for x in v] == ["required", "warning", "required", "required"]
    assert v[2]["key"].endswith("|r2") and v[3]["employee_id"] == e2 and v[3]["waivable"] is True
    assert set(v[0]) == {"key", "check_code", "severity", "waivable", "work_date", "employee_id", "detail",
                         "allowed_by", "allowed_at"}
    assert err(api.c.get("/api/drafts/9999", headers=api.h)) == (404, "NOT_FOUND")


@pytest.mark.req("A-16")
def test_db_blocks_second_approval_and_second_confirmed_draft(started):
    """승인 두 번 · 확정안 두 개는 DB 가 막는다 (V9_ko 505). API 는 그 오류를 409 로 바꾸는 표를 갖는다."""
    from app.api.errors import UNIQUE_CODES
    api = started
    m = api.stores["A"]["manager_id"]
    d1, d2 = [api.sql("INSERT INTO shift_drafts (period_id) VALUES (%s) RETURNING draft_id", (api.pid,))[0]["draft_id"]
              for _ in range(2)]
    api.sql("INSERT INTO manager_actions (period_id, draft_id, manager_id, action) VALUES (%s, %s, %s, 'approved')",
            (api.pid, d1, m))
    with pytest.raises(psycopg.errors.UniqueViolation) as e:
        api.sql("INSERT INTO manager_actions (period_id, draft_id, manager_id, action) VALUES (%s,%s,%s,'approved')",
                (api.pid, d1, m))
    assert UNIQUE_CODES[e.value.diag.constraint_name][0] == "ALREADY_APPROVED"
    api.sql("UPDATE shift_drafts SET status='confirmed', confirmed_at=now(), confirmed_by=%s WHERE draft_id=%s",
            (m, d1))
    with pytest.raises(psycopg.errors.UniqueViolation) as e:
        api.sql("UPDATE shift_drafts SET status='confirmed', confirmed_at=now(), confirmed_by=%s WHERE draft_id=%s",
                (m, d2))
    assert UNIQUE_CODES[e.value.diag.constraint_name][0] == "ALREADY_CONFIRMED"
    # 확정안이 있으면 run_state 는 러너를 부르지 않고 confirmed
    n = len(api.runner.calls)
    assert api.c.get(f"/api/shift-periods/{api.pid}", headers=api.h).json()["run_state"] == "confirmed"
    assert len(api.runner.calls) == n
