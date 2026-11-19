"""설정 CRUD (A-5 ~ A-9, M-8)."""
from __future__ import annotations

import pytest

from app.domain import InstructionResult

from ._util import FUTURE, err


def _rule(wd=1, s="09:00", e="13:00", hc=2, q=1):
    return {"weekday": wd, "start_time": s, "end_time": e, "min_headcount": hc, "min_qualified": q}


@pytest.mark.req("A-5")
def test_store_get_and_patch_come_from_jwt(two_stores):
    api = two_stores
    a, b = api.stores["A"], api.stores["B"]
    assert api.c.get("/api/store", headers=a["h"]).json() == {"store_id": a["store_id"], "name": "아즈키점"}
    assert api.c.patch("/api/store", headers=a["h"], json={"name": "아즈키 본점"}).json()["name"] == "아즈키 본점"
    assert api.c.get("/api/store", headers=b["h"]).json()["name"] == "다른점"  # 다른 점포는 그대로
    # 깨뜨리는 쪽: 본문에 store_id 를 넣어도 받지 않는다 (A-5)
    assert err(api.c.patch("/api/store", headers=a["h"], json={"name": "x", "store_id": b["store_id"]})) == (
        422, "VALIDATION_ERROR")


@pytest.mark.req("A-6", "M-8")
def test_time_rule_crud_and_overlap_is_409(two_stores):
    api = two_stores
    h = api.stores["A"]["h"]
    r = api.c.post("/api/store/time-rules", headers=h, json=_rule())
    assert r.status_code == 201
    rule = r.json()
    assert rule == {"rule_id": rule["rule_id"], "weekday": 1, "start_time": "09:00", "end_time": "13:00",
                    "min_headcount": 2, "min_qualified": 1}
    # 이어 붙는 시간대 · 다른 요일 · 다른 점포의 같은 시간대는 겹침이 아니다
    assert api.c.post("/api/store/time-rules", headers=h, json=_rule(s="13:00", e="17:00")).status_code == 201
    assert api.c.post("/api/store/time-rules", headers=h, json=_rule(wd=2)).status_code == 201
    assert api.c.post("/api/store/time-rules", headers=api.stores["B"]["h"], json=_rule()).status_code == 201
    # 깨뜨리는 쪽: 겹침 → DB EXCLUDE → 409 (V9_ko 172)
    assert err(api.c.post("/api/store/time-rules", headers=h, json=_rule(s="12:00", e="15:00"))) == (
        409, "TIME_RULE_OVERLAP")
    assert err(api.c.patch(f"/api/store/time-rules/{rule['rule_id']}", headers=h, json={"end_time": "14:00"})) == (
        409, "TIME_RULE_OVERLAP")
    # DB CHECK (end > start, min_headcount >= 1, weekday 1..7) → 422
    assert err(api.c.post("/api/store/time-rules", headers=h, json=_rule(wd=3, s="13:00", e="09:00"))) == (
        422, "CHECK_VIOLATION")
    assert err(api.c.post("/api/store/time-rules", headers=h, json=_rule(wd=3, hc=0))) == (422, "CHECK_VIOLATION")
    assert err(api.c.post("/api/store/time-rules", headers=h, json=_rule(wd=8))) == (422, "CHECK_VIOLATION")
    # 30분 단위가 아닌 시각 → 422
    assert err(api.c.post("/api/store/time-rules", headers=h, json=_rule(wd=3, s="09:15"))) == (
        422, "VALIDATION_ERROR")
    # 부분 수정 · 삭제
    p = api.c.patch(f"/api/store/time-rules/{rule['rule_id']}", headers=h, json={"min_headcount": 3})
    assert p.status_code == 200 and p.json()["min_headcount"] == 3 and p.json()["end_time"] == "13:00"
    assert api.c.delete(f"/api/store/time-rules/{rule['rule_id']}", headers=h).status_code == 204
    assert err(api.c.delete(f"/api/store/time-rules/{rule['rule_id']}", headers=h)) == (404, "NOT_FOUND")
    got = api.c.get("/api/store/time-rules", headers=h).json()
    assert [(x["weekday"], x["start_time"]) for x in got] == [(1, "13:00"), (2, "09:00")]


@pytest.mark.req("A-6")
def test_time_rule_copy_replaces_target_weekdays(two_stores):
    api = two_stores
    h = api.stores["A"]["h"]
    for body in (_rule(1, "08:00", "13:00"), _rule(1, "13:00", "22:30", hc=3), _rule(2, "10:00", "11:00")):
        assert api.c.post("/api/store/time-rules", headers=h, json=body).status_code == 201
    r = api.c.post("/api/store/time-rules/copy", headers=h, json={"from_weekday": 1, "to_weekdays": [2, 3, 3]})
    assert r.status_code == 200 and r.json() == {"created": 4}
    got = api.c.get("/api/store/time-rules", headers=h).json()
    by_wd = {}
    for x in got:
        by_wd.setdefault(x["weekday"], []).append((x["start_time"], x["end_time"], x["min_headcount"]))
    assert by_wd[2] == by_wd[3] == by_wd[1] == [("08:00", "13:00", 2), ("13:00", "22:30", 3)]  # 2요일의 옛 규칙은 지워짐
    # 깨뜨리는 쪽: 원본을 대상에 넣음 / 원본이 비어 있음 → 422, 아무것도 안 바뀐다
    assert err(api.c.post("/api/store/time-rules/copy", headers=h, json={"from_weekday": 1, "to_weekdays": [1]})) == (
        422, "INVALID_COPY")
    assert err(api.c.post("/api/store/time-rules/copy", headers=h, json={"from_weekday": 5, "to_weekdays": [2]})) == (
        422, "NO_SOURCE_RULES")
    assert len(api.c.get("/api/store/time-rules", headers=h).json()) == 6


@pytest.mark.req("A-7")
def test_employee_crud_and_weekday_mismatch_is_check_422(two_stores):
    api = two_stores
    h = api.stores["A"]["h"]
    r = api.c.post("/api/employees", headers=h, json={"name": "Aさん", "is_regular": False, "weekly_days": 3,
                                                      "fixed_weekdays": [5, 1, 3], "closing_ok": True})
    assert r.status_code == 201
    e = r.json()
    assert e == {"employee_id": e["employee_id"], "name": "Aさん", "is_regular": False, "has_qualification": False,
                 "closing_ok": True, "weekly_days": 3, "fixed_weekdays": [1, 3, 5], "line_linked": False}
    # 깨뜨리는 쪽: weekly_days ↔ fixed_weekdays 불일치 → DB CHECK → 422 (V9_ko 178)
    assert err(api.c.post("/api/employees", headers=h, json={"name": "B", "is_regular": True, "weekly_days": 2,
                                                             "fixed_weekdays": [1, 3, 5]})) == (422, "CHECK_VIOLATION")
    assert err(api.c.patch(f"/api/employees/{e['employee_id']}", headers=h, json={"weekly_days": 2})) == (
        422, "CHECK_VIOLATION")
    assert err(api.c.post("/api/employees", headers=h, json={"name": "B", "is_regular": True,
                                                             "fixed_weekdays": [1, 8]})) == (422, "CHECK_VIOLATION")
    # 고정 요일 해제(null) 와 함께 일수만 남기기
    p = api.c.patch(f"/api/employees/{e['employee_id']}", headers=h, json={"fixed_weekdays": None, "weekly_days": 2})
    assert p.status_code == 200 and p.json()["fixed_weekdays"] is None and p.json()["weekly_days"] == 2
    assert err(api.c.patch(f"/api/employees/{e['employee_id']}", headers=h, json={"name": None})) == (
        422, "VALIDATION_ERROR")
    assert [x["employee_id"] for x in api.c.get("/api/employees", headers=h).json()] == [e["employee_id"]]
    # 삭제 API 는 없다 (V9_ko 180)
    assert err(api.c.delete(f"/api/employees/{e['employee_id']}", headers=h)) == (405, "METHOD_NOT_ALLOWED")


@pytest.mark.req("A-8")
def test_task_crud_and_duplicate_name(two_stores):
    api = two_stores
    h = api.stores["A"]["h"]
    t = api.c.post("/api/tasks", headers=h, json={"name": "레지", "sort_order": 2}).json()
    assert t == {"task_id": t["task_id"], "name": "레지", "sort_order": 2}
    assert err(api.c.post("/api/tasks", headers=h, json={"name": "레지"})) == (409, "TASK_EXISTS")
    assert api.c.post("/api/tasks", headers=api.stores["B"]["h"], json={"name": "레지"}).status_code == 201  # 점포별 유일
    assert api.c.patch(f"/api/tasks/{t['task_id']}", headers=h, json={"name": "계산대"}).json()["name"] == "계산대"
    assert api.c.delete(f"/api/tasks/{t['task_id']}", headers=h).status_code == 204
    assert api.c.get("/api/tasks", headers=h).json() == []


@pytest.mark.req("A-9")
def test_period_crud_duplicate_month_409_and_checks(two_stores):
    api = two_stores
    h = api.stores["A"]["h"]
    r = api.c.post("/api/shift-periods", headers=h, json={
        "target_month": "2026-12-01", "submission_deadline": "2026-11-20T23:59:00+09:00", "designated_off_days": 9,
        "off_request_limit": 3})
    assert r.status_code == 201
    p = r.json()
    assert p == {"period_id": p["period_id"], "target_month": "2026-12-01",
                 "submission_deadline": p["submission_deadline"], "designated_off_days": 9, "off_request_limit": 3,
                 "work_request_limit": None, "thread_id": None, "run_state": "not_started",
                 "confirmed_draft_id": None}
    assert p["submission_deadline"].startswith("2026-11-20T") and p["submission_deadline"][-6] in "+-"
    # 깨뜨리는 쪽: 같은 달 → 409, 1일이 아님 → DB CHECK 422, 시간대 없는 마감 → 422
    assert err(api.c.post("/api/shift-periods", headers=h, json={
        "target_month": "2026-12-01", "submission_deadline": FUTURE, "designated_off_days": 9})) == (
        409, "PERIOD_EXISTS")
    assert err(api.c.post("/api/shift-periods", headers=h, json={
        "target_month": "2027-01-15", "submission_deadline": FUTURE, "designated_off_days": 9})) == (
        422, "CHECK_VIOLATION")
    assert err(api.c.post("/api/shift-periods", headers=h, json={
        "target_month": "2027-01-01", "submission_deadline": "2026-12-20T00:00:00", "designated_off_days": 9})) == (
        422, "VALIDATION_ERROR")
    # 다른 점포는 같은 달을 따로 가진다
    assert api.c.post("/api/shift-periods", headers=api.stores["B"]["h"], json={
        "target_month": "2026-12-01", "submission_deadline": FUTURE, "designated_off_days": 9}).status_code == 201
    # 수정은 마감 · 상한만. target_month · thread_id 는 받지 않는다
    q = api.c.patch(f"/api/shift-periods/{p['period_id']}", headers=h,
                    json={"off_request_limit": None, "work_request_limit": 5})
    assert q.status_code == 200 and q.json()["off_request_limit"] is None and q.json()["work_request_limit"] == 5
    assert err(api.c.patch(f"/api/shift-periods/{p['period_id']}", headers=h,
                           json={"target_month": "2027-02-01"})) == (422, "VALIDATION_ERROR")
    assert err(api.c.patch(f"/api/shift-periods/{p['period_id']}", headers=h,
                           json={"thread_id": "x"})) == (422, "VALIDATION_ERROR")
    assert [x["period_id"] for x in api.c.get("/api/shift-periods", headers=h).json()] == [p["period_id"]]


@pytest.mark.req("A-9", "F-10")
def test_period_run_state_comes_from_db_and_runner(two_stores):
    api = two_stores
    h = api.stores["A"]["h"]
    pid = api.period(h)
    assert api.c.get(f"/api/shift-periods/{pid}", headers=h).json()["run_state"] == "not_started"
    assert api.runner.calls == []  # thread 가 없으면 러너를 부르지 않는다
    api.c.post(f"/api/shift-periods/{pid}/start-now", headers=h)
    api.runner.wait_review(pid, 7)
    got = api.c.get(f"/api/shift-periods/{pid}", headers=h).json()
    assert got["run_state"] == "waiting_review" and got["thread_id"] == api.runner._tid(pid)  # G22
    api.runner.raise_on["status"] = RuntimeError("runner down")
    assert api.c.get("/api/shift-periods", headers=h).json()[0]["run_state"] == "error"  # 목록은 깨지지 않는다


@pytest.mark.req("A-5")
def test_other_store_resources_are_404(two_stores):
    api = two_stores
    api.composer.result = InstructionResult(rules=[], free_text=[])
    ha, hb = api.stores["A"]["h"], api.stores["B"]["h"]
    eid = api.employee(ha)
    rid = api.c.post("/api/store/time-rules", headers=ha, json=_rule()).json()["rule_id"]
    tid = api.c.post("/api/tasks", headers=ha, json={"name": "레지"}).json()["task_id"]
    pid = api.period(ha)
    did = api.sql("INSERT INTO shift_drafts (period_id) VALUES (%s) RETURNING draft_id", (pid,))[0]["draft_id"]
    calls = [
        ("get", f"/api/employees/{eid}", None), ("patch", f"/api/employees/{eid}", {"name": "x"}),
        ("patch", f"/api/store/time-rules/{rid}", {"min_headcount": 5}), ("delete", f"/api/store/time-rules/{rid}", None),
        ("patch", f"/api/tasks/{tid}", {"name": "x"}), ("delete", f"/api/tasks/{tid}", None),
        ("get", f"/api/shift-periods/{pid}", None), ("patch", f"/api/shift-periods/{pid}", {"work_request_limit": 1}),
        ("get", f"/api/shift-periods/{pid}/submissions", None),
        ("post", f"/api/shift-periods/{pid}/submission-links", None),
        ("post", f"/api/employees/{eid}/link-token", None),
        ("post", f"/api/shift-periods/{pid}/instructions/preview", {"text": "x"}),
        ("post", f"/api/shift-periods/{pid}/instructions/confirm", {"text": "x", "rules": [], "free_text": []}),
        ("get", f"/api/shift-periods/{pid}/instructions", None),
        ("post", f"/api/shift-periods/{pid}/start-now", None), ("get", f"/api/shift-periods/{pid}/run-status", None),
        ("post", f"/api/shift-periods/{pid}/review", {"decision": "approved", "draft_id": did, "waived_keys": []}),
        ("get", f"/api/drafts/{did}", None),
    ]
    for method, path, body in calls:
        kw = {"json": body} if body is not None else {}
        assert err(getattr(api.c, method)(path, headers=hb, **kw)) == (404, "NOT_FOUND"), (method, path)
        # 지키는 경우: 자기 점포 관리자는 404 가 아니다 (경로가 맞는지 같이 확인)
        assert api.c.request(method.upper(), path, headers=ha, **kw).status_code != 404, (method, path)
    assert api.runner.names().count("start") == 1  # B 의 요청은 러너까지 가지 않았다
    assert api.c.get("/api/employees", headers=hb).json() == []
