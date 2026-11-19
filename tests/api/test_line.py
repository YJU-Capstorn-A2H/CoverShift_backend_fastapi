"""LINE 연결 · 발송함 · 직원 답장 (A-13, A-17, M-5, M-6 의 입구 부분, G16)."""
from __future__ import annotations

import pytest

from app.runner import Busy

from ._util import BUSY_MESSAGE, err


@pytest.mark.req("A-13")
def test_link_token_then_line_link_stores_user_and_clears_token(two_stores):
    api = two_stores
    h = api.stores["A"]["h"]
    eid = api.employee(h)
    r = api.c.post(f"/api/employees/{eid}/link-token", headers=h)
    assert r.status_code == 200
    token = r.json()["link_url"].rsplit("/", 1)[1]
    row = api.sql("SELECT link_token, extract(epoch FROM link_token_expires_at - now()) AS ttl FROM employees")[0]
    assert row["link_token"] == token and 7 * 86400 - 60 < float(row["ttl"]) <= 7 * 86400
    box = api.c.get("/api/dev/line-outbox", headers=h).json()
    assert [(m["kind"], m["employee_id"]) for m in box] == [("link_token", eid)]

    assert api.c.post("/api/line/link", json={"link_token": token, "line_user_id": "U-1"}).json() == {
        "employee_id": eid}
    assert api.sql("SELECT line_user_id, link_token, link_token_expires_at FROM employees") == [
        {"line_user_id": "U-1", "link_token": None, "link_token_expires_at": None}]
    assert api.c.get(f"/api/employees/{eid}", headers=h).json()["line_linked"] is True
    # 깨뜨리는 쪽: 같은 토큰 다시 → 400 / 이미 연결된 직원에게 새 토큰 → 409
    assert err(api.c.post("/api/line/link", json={"link_token": token, "line_user_id": "U-1"})) == (
        400, "INVALID_TOKEN")
    assert err(api.c.post(f"/api/employees/{eid}/link-token", headers=h)) == (409, "LINE_ALREADY_LINKED")


@pytest.mark.req("A-13")
def test_line_link_rejects_expired_unknown_and_taken_line_user(two_stores):
    api = two_stores
    h = api.stores["A"]["h"]
    e1, e2 = api.employee(h, "A"), api.employee(h, "B")
    api.sql("UPDATE employees SET line_user_id = 'U-taken' WHERE employee_id = %s", (e1,))
    t2 = api.c.post(f"/api/employees/{e2}/link-token", headers=h).json()["link_url"].rsplit("/", 1)[1]
    assert err(api.c.post("/api/line/link", json={"link_token": t2, "line_user_id": "U-taken"})) == (
        409, "LINE_ALREADY_LINKED")
    assert api.sql("SELECT link_token FROM employees WHERE employee_id = %s", (e2,))[0]["link_token"] == t2  # 그대로
    assert err(api.c.post("/api/line/link", json={"link_token": "nope", "line_user_id": "U-2"})) == (
        400, "INVALID_TOKEN")
    api.sql("UPDATE employees SET link_token_expires_at = now() - interval '1 second' WHERE employee_id = %s", (e2,))
    assert err(api.c.post("/api/line/link", json={"link_token": t2, "line_user_id": "U-2"})) == (
        400, "INVALID_TOKEN")


@pytest.mark.req("A-17", "A-5")
def test_outbox_needs_manager_newest_first_and_is_scoped_to_store(two_stores):
    api = two_stores
    ha, hb = api.stores["A"]["h"], api.stores["B"]["h"]
    e1, e2 = api.employee(ha, "A"), api.employee(ha, "B")
    api.c.post(f"/api/employees/{e1}/link-token", headers=ha)
    api.c.post(f"/api/employees/{e2}/link-token", headers=ha)
    box = api.c.get("/api/dev/line-outbox", headers=ha).json()
    assert [m["employee_id"] for m in box] == [e2, e1]  # 최신 순
    assert set(box[0]) == {"message_id", "employee_id", "employee_name", "line_user_id", "kind", "body", "created_at",
                           "status", "attempts", "sent_at", "last_error"}
    assert (box[0]["status"], box[0]["attempts"], box[0]["sent_at"], box[0]["last_error"]) == ("pending", 0, None, None)
    assert [m["employee_id"] for m in api.c.get(f"/api/dev/line-outbox?employee_id={e1}", headers=ha).json()] == [e1]
    assert api.c.get("/api/dev/line-outbox", headers=hb).json() == []
    assert err(api.c.get("/api/dev/line-outbox")) == (401, "UNAUTHORIZED")


@pytest.mark.req("M-5")
def test_reply_button_resumes_with_contact_id_and_answer(contact):
    api = contact
    r = api.c.post("/api/line/reply", json={"contact_id": api.cid, "line_user_id": "U-me", "answer": "yes"})
    assert r.status_code == 202 and r.json()["state"] == "running"
    assert api.runner.calls[-1] == ("resume", api.pid, api.runner._tid(api.pid), {"contact_id": api.cid, "answer": "yes"})


@pytest.mark.req("M-5")
def test_reply_rejects_other_person_unknown_contact_answered_and_not_waiting(contact):
    api = contact
    other = api.c.post("/api/line/reply", json={"contact_id": api.cid, "line_user_id": "U-other", "answer": "yes"})
    unknown = api.c.post("/api/line/reply", json={"contact_id": 999, "line_user_id": "U-me", "answer": "yes"})
    assert err(other) == err(unknown) == (403, "NOT_YOUR_CONTACT")
    assert other.json() == unknown.json()  # 있는 연락인지 알 수 없게
    assert err(api.c.post("/api/line/reply", json={"contact_id": api.cid, "line_user_id": "U-me",
                                                   "answer": "maybe"})) == (422, "VALIDATION_ERROR")
    api.runner.wait_review(api.pid, 5)  # 지금은 다른 대기
    assert err(api.c.post("/api/line/reply", json={"contact_id": api.cid, "line_user_id": "U-me",
                                                   "answer": "no"})) == (409, "NOT_WAITING")
    api.runner.wait_contact(api.pid, api.cid)
    api.runner.raise_on["resume"] = Busy()
    r = api.c.post("/api/line/reply", json={"contact_id": api.cid, "line_user_id": "U-me", "answer": "no"})
    assert err(r) == (409, "BUSY") and r.json()["error"]["message"] == BUSY_MESSAGE
    api.sql("UPDATE contact_requests SET status = 'accepted', answered_at = now()")
    assert err(api.c.post("/api/line/reply", json={"contact_id": api.cid, "line_user_id": "U-me",
                                                   "answer": "no"})) == (409, "ALREADY_ANSWERED")
    assert api.runner.names().count("resume") == 1  # 러너까지 간 것은 Busy 를 낸 1번뿐
