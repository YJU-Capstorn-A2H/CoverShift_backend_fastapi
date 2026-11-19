"""LINE 웹훅 · 모드 분기 (A-20, A-22). 진짜 LINE 은 부르지 않는다 — 가짜 시크릿으로 서명을 직접 만든다."""
from __future__ import annotations

import json
import threading
import time

import psycopg
import pytest
from fastapi.testclient import TestClient

from app import line_api
from app.api import routes_line_webhook
from app.api.main import app
from app.runner import Busy

from ._util import err

SECRET = "test-channel-secret"  # 시험용 가짜 값


LINK_PREFIX = "https://line.me/R/oaMessage/%40cover-shift/?"  # 토큰이 미리 채워진 대화창 링크 (G24)


@pytest.fixture
def line_mode(monkeypatch):
    monkeypatch.setenv("LINE_MODE", "line")
    monkeypatch.setenv("LINE_CHANNEL_SECRET", SECRET)
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "test-access-token")
    monkeypatch.setenv("LINE_OFFICIAL_ACCOUNT_ID", "@cover-shift")


def event(kind: str, user_id: str, **extra) -> dict:
    return {"type": kind, "mode": "active", "timestamp": 1, "source": {"type": "user", "userId": user_id},
            "webhookEventId": "01TESTEVENT", "deliveryContext": {"isRedelivery": False}, **extra}


def postback(user_id: str, contact_id: int, answer: str) -> dict:
    return event("postback", user_id, postback={"data": line_api.postback_data(contact_id, answer)})


def text(user_id: str, message: str) -> dict:
    return event("message", user_id, message={"type": "text", "id": "1", "text": message})


def raw(events: list) -> bytes:
    return json.dumps({"destination": "Ubot", "events": events}).encode()


def send(api, body: bytes, *, signature: str | None = "", secret: str = SECRET):
    """signature="" → 맞는 서명을 만들어 붙인다. None → 헤더 없음."""
    if signature == "":
        signature = line_api.sign(secret, body)
    headers = {"Content-Type": "application/json"} | ({"X-Line-Signature": signature} if signature else {})
    return api.c.post("/api/line/webhook", content=body, headers=headers)


def issue(api, employee_id: int) -> str:
    """점포 A 의 직원에게 연결 토큰을 발급하고 토큰을 돌려준다 (line 모드의 링크에서 꺼낸다)."""
    url = api.c.post(f"/api/employees/{employee_id}/link-token", headers=api.stores["A"]["h"]).json()["link_url"]
    assert url.startswith(LINK_PREFIX), url
    return url.removeprefix(LINK_PREFIX)


def resumes(api) -> list[tuple]:
    return [c for c in api.runner.calls if c[0] == "resume"]


# ---------------------------------------------------------------- 서명

@pytest.mark.req("A-20")
def test_valid_signature_is_accepted_and_empty_events_is_200(api, line_mode):
    r = send(api, raw([]))  # 콘솔 Verify 가 보내는 모양
    assert r.status_code == 200


@pytest.mark.req("A-20")
def test_bad_signature_missing_header_or_changed_body_changes_nothing(contact, line_mode):
    api = contact
    e2 = api.employee(api.stores["A"]["h"], "B")
    token = issue(api, e2)
    body = raw([postback("U-me", api.cid, "yes"), text("U-new", token)])
    before = api.sql("SELECT employee_id, line_user_id, link_token FROM employees ORDER BY employee_id")
    good = line_api.sign(SECRET, body)
    changed = body.replace(b"U-new", b"U-neW")  # 본문 1바이트 변경, 서명은 원래 것
    for r in (send(api, body, signature="AAAA" + good[4:]), send(api, body, signature=None),
              send(api, changed, signature=good), send(api, body, secret="other-secret")):
        assert err(r) == (401, "INVALID_SIGNATURE")
    assert resumes(api) == []
    assert api.sql("SELECT employee_id, line_user_id, link_token FROM employees ORDER BY employee_id") == before
    # 지키는 쪽: 같은 본문에 맞는 서명이면 둘 다 반영된다 → 위의 거부가 서명 때문임을 보인다
    assert send(api, body, signature=good).status_code == 200
    assert len(resumes(api)) == 1
    assert api.sql("SELECT line_user_id FROM employees WHERE employee_id = %s", (e2,)) == [{"line_user_id": "U-new"}]


@pytest.mark.req("A-20")
def test_empty_channel_secret_never_verifies(contact):
    """stub 모드 · 시크릿 없음: 빈 키로 만든 서명(누구나 만들 수 있다)을 받지 않는다."""
    api = contact
    body = raw([postback("U-me", api.cid, "yes")])
    assert err(send(api, body, secret="")) == (401, "INVALID_SIGNATURE")
    assert resumes(api) == []


@pytest.mark.req("A-20")
def test_signed_body_that_is_not_a_webhook_is_400(api, line_mode):
    assert err(send(api, b"not json")) == (400, "INVALID_WEBHOOK")
    assert err(send(api, b'{"events": null}')) == (400, "INVALID_WEBHOOK")


# ---------------------------------------------------------------- 버튼 답 (postback)

@pytest.mark.req("A-20", "M-5")
def test_postback_from_the_asked_employee_resumes_the_graph(contact, line_mode):
    api = contact
    assert send(api, raw([postback("U-me", api.cid, "no")])).status_code == 200
    assert resumes(api) == [("resume", api.pid, api.runner._tid(api.pid), {"contact_id": api.cid, "answer": "no"})]


@pytest.mark.req("A-20", "M-5")
def test_postback_from_other_user_unknown_contact_or_already_answered_is_ignored(contact, line_mode):
    api = contact
    odd = event("postback", "U-me", postback={"data": f"contact:{api.cid}:maybe"})
    r = send(api, raw([postback("U-other", api.cid, "yes"), postback("U-me", 999, "yes"), odd]))
    assert r.status_code == 200 and resumes(api) == []
    api.sql("UPDATE contact_requests SET status = 'accepted', answered_at = now()")
    assert send(api, raw([postback("U-me", api.cid, "yes")])).status_code == 200
    assert resumes(api) == []


@pytest.mark.req("A-20")
def test_same_event_twice_is_applied_once(contact, line_mode):
    """재전송: 같은 요청이 또 와도, 한 요청 안에 같은 이벤트가 둘 있어도 답은 한 번만 넘어간다.

    그래프가 그 답을 처리하는 동안 온 것은 503(다시 보내라), 처리가 끝난 뒤 온 것은 200(이미 답함)."""
    api = contact
    body = raw([postback("U-me", api.cid, "yes")])
    assert send(api, body).status_code == 200
    assert err(send(api, body)) == (503, "TRY_AGAIN")  # 러너가 아직 도는 중
    assert err(send(api, raw([postback("U-me", api.cid, "yes")] * 2))) == (503, "TRY_AGAIN")
    api.sql("UPDATE contact_requests SET status = 'accepted', answered_at = now()")  # 그래프가 답을 기록했다
    api.runner.wait_review(api.pid, 5)
    assert send(api, body).status_code == 200  # 재전송: 이미 답함 → 끝
    assert len(resumes(api)) == 1


@pytest.mark.req("A-20")
def test_standby_and_unknown_event_types_are_skipped(contact, line_mode):
    api = contact
    standby = {**postback("U-me", api.cid, "yes"), "mode": "standby"}
    r = send(api, raw([standby, event("follow", "U-me"), event("message", "U-me", message={"type": "sticker"})]))
    assert r.status_code == 200 and resumes(api) == []


# ---------------------------------------------------------------- 연결 (토큰 메시지)

@pytest.mark.req("A-20", "A-13")
def test_text_message_with_valid_token_links_the_sender(two_stores, line_mode):
    api = two_stores
    eid = api.employee(api.stores["A"]["h"])
    token = issue(api, eid)
    assert send(api, raw([text("U-1", f" {token}\n")])).status_code == 200
    assert api.sql("SELECT line_user_id, link_token, link_token_expires_at FROM employees") == [
        {"line_user_id": "U-1", "link_token": None, "link_token_expires_at": None}]
    # 같은 메시지가 또 와도(재전송) 그대로 — 토큰은 1회용
    assert send(api, raw([text("U-2", token)])).status_code == 200
    assert api.sql("SELECT line_user_id FROM employees") == [{"line_user_id": "U-1"}]


@pytest.mark.req("A-20", "A-13")
def test_expired_unknown_or_taken_does_not_link_and_does_not_block_other_events(two_stores, line_mode):
    api = two_stores
    h = api.stores["A"]["h"]
    e1, e2, e3, e4 = (api.employee(h, n) for n in "ABCD")
    api.sql("UPDATE employees SET line_user_id = 'U-taken' WHERE employee_id = %s", (e1,))
    t2, t3, t4 = (issue(api, e) for e in (e2, e3, e4))
    api.sql("UPDATE employees SET link_token_expires_at = now() - interval '1 second' WHERE employee_id = %s", (e3,))
    r = send(api, raw([text("U-taken", t2),   # 이미 다른 직원에 연결된 LINE 계정 → UNIQUE 위반
                       text("U-3", t3),       # 만료
                       text("U-x", "nope"),   # 모르는 토큰
                       text("U-4", t4)]))     # 앞의 실패와 상관없이 처리된다
    assert r.status_code == 200
    rows = api.sql("SELECT employee_id, line_user_id, link_token FROM employees ORDER BY employee_id")
    assert [(x["employee_id"], x["line_user_id"]) for x in rows] == [
        (e1, "U-taken"), (e2, None), (e3, None), (e4, "U-4")]
    assert rows[1]["link_token"] == t2  # 실패한 연결은 토큰을 쓰지 않았다


# ---------------------------------------------------------------- 일시 장애는 다시 받는다 (G26)

@pytest.mark.req("A-20")
def test_permanent_rejections_alone_are_200(contact, line_mode):
    """지키는 쪽의 반대편: 다시 보내도 결과가 같은 거절만 있으면 200 — LINE 이 다시 보내지 않게."""
    api = contact
    r = send(api, raw([postback("U-other", api.cid, "yes"), text("U-x", "nope"), {"type": "postback"}, "not-an-event"]))
    assert r.status_code == 200 and resumes(api) == []


@pytest.mark.req("A-20")
@pytest.mark.parametrize("failure", [RuntimeError("Aegra 가 응답하지 않는다"), Busy()])
def test_runner_failure_or_busy_is_503_and_the_redelivered_event_is_applied(contact, line_mode, failure):
    api = contact
    body = raw([postback("U-me", api.cid, "yes")])
    api.runner.raise_on["resume"] = failure
    assert err(send(api, body)) == (503, "TRY_AGAIN")
    assert api.sql("SELECT status FROM contact_requests") == [{"status": "asked"}]  # 답은 아직 반영되지 않았다
    assert len(resumes(api)) == 1  # 러너까지 갔다가 실패한 1번
    del api.runner.raise_on["resume"]  # 장애가 풀린 뒤 LINE 이 같은 요청을 다시 보낸다
    assert send(api, body).status_code == 200
    assert len(resumes(api)) == 2 and api.runner.states[api.runner._tid(api.pid)]["state"] == "running"  # 이번에는 넘어갔다


@pytest.mark.req("A-20")
def test_graph_still_running_is_503(contact, line_mode):
    """질문은 나갔는데 그래프가 아직 「기다림」에 닿지 않았다(state=running) → 잠시 뒤 다시 받는다."""
    api = contact
    tid = api.runner._tid(api.pid)
    api.runner.states[tid] = api.runner.rs(api.pid, tid, "running")
    body = raw([postback("U-me", api.cid, "yes")])
    assert err(send(api, body)) == (503, "TRY_AGAIN") and resumes(api) == []
    api.runner.wait_contact(api.pid, api.cid)
    assert send(api, body).status_code == 200 and len(resumes(api)) == 1


@pytest.mark.req("A-20")
def test_db_connection_failure_is_503_and_the_redelivered_event_is_applied(two_stores, line_mode, monkeypatch):
    api = two_stores
    eid = api.employee(api.stores["A"]["h"])
    body = raw([text("U-1", issue(api, eid))])
    real_connect = routes_line_webhook.connect

    def db_is_down():
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(routes_line_webhook, "connect", db_is_down)
    assert err(send(api, body)) == (503, "TRY_AGAIN")
    assert api.sql("SELECT line_user_id FROM employees") == [{"line_user_id": None}]
    monkeypatch.setattr(routes_line_webhook, "connect", real_connect)
    assert send(api, body).status_code == 200
    assert api.sql("SELECT line_user_id FROM employees") == [{"line_user_id": "U-1"}]


@pytest.mark.req("A-20")
def test_one_transient_failure_makes_the_response_503_but_other_events_are_still_applied(contact, line_mode):
    api = contact
    e2 = api.employee(api.stores["A"]["h"], "B")
    body = raw([postback("U-me", api.cid, "yes"), text("U-2", issue(api, e2))])
    api.runner.raise_on["resume"] = RuntimeError("일시 장애")
    assert err(send(api, body)) == (503, "TRY_AGAIN")
    assert api.sql("SELECT line_user_id FROM employees WHERE employee_id = %s", (e2,)) == [{"line_user_id": "U-2"}]
    del api.runner.raise_on["resume"]
    # 재전송: 이미 반영된 연결은 「모르는 토큰」으로 끝나고, 못 받았던 답만 반영된다
    assert send(api, body).status_code == 200
    assert len(resumes(api)) == 2 and api.runner.states[api.runner._tid(api.pid)]["state"] == "running"
    assert api.sql("SELECT count(*) AS n FROM employees WHERE line_user_id = 'U-2'") == [{"n": 1}]


@pytest.mark.req("A-20", "A-15")
def test_same_postback_in_two_concurrent_requests_resumes_once(contact, line_mode):
    """실제로 겹치게 보낸다: 먼저 들어간 요청이 러너에 넘기는 동안(0.3초) 다른 요청이 같은 답을 들고 온다."""
    api = contact
    real_resume = api.runner.resume

    def slow_resume(period_id, thread_id, payload):
        time.sleep(0.3)
        return real_resume(period_id, thread_id, payload)

    api.runner.resume = slow_resume
    body = raw([postback("U-me", api.cid, "yes")])
    headers = {"Content-Type": "application/json", "X-Line-Signature": line_api.sign(SECRET, body)}
    start, codes = threading.Barrier(2), []

    def deliver():
        with TestClient(app) as client:
            start.wait(5)
            codes.append(client.post("/api/line/webhook", content=body, headers=headers).status_code)

    threads = [threading.Thread(target=deliver) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(15)
    assert sorted(codes) == [200, 503]  # 하나는 반영, 하나는 「지금 처리 중」
    assert len(resumes(api)) == 1


# ---------------------------------------------------------------- 본문 크기

@pytest.mark.req("A-20")
def test_body_over_the_limit_is_refused_before_signature_check(contact, line_mode, monkeypatch):
    api = contact
    body = raw([postback("U-me", api.cid, "yes")])
    monkeypatch.setattr(routes_line_webhook, "MAX_BODY_BYTES", len(body) - 1)
    headers = {"X-Line-Signature": line_api.sign(SECRET, body)}  # 서명이 맞아도
    assert err(api.c.post("/api/line/webhook", content=body, headers=headers)) == (413, "PAYLOAD_TOO_LARGE")
    chunks = iter([body[:10], body[10:]])  # Content-Length 없이(chunked) 와도 읽다가 끊는다
    assert err(api.c.post("/api/line/webhook", content=chunks, headers=headers)) == (413, "PAYLOAD_TOO_LARGE")
    assert resumes(api) == []
    monkeypatch.setattr(routes_line_webhook, "MAX_BODY_BYTES", len(body))  # 지키는 쪽: 딱 상한이면 받는다
    assert api.c.post("/api/line/webhook", content=iter([body[:10], body[10:]]), headers=headers).status_code == 200
    assert len(resumes(api)) == 1


# ---------------------------------------------------------------- 모드 (A-22)

@pytest.mark.req("A-22")
def test_body_trusting_routes_are_404_in_line_mode_and_open_in_stub_mode(contact, monkeypatch):
    api = contact
    reply = {"contact_id": api.cid, "line_user_id": "U-me", "answer": "yes"}
    link = {"link_token": "x", "line_user_id": "U-1"}
    assert err(api.c.post("/api/line/link", json=link)) == (400, "INVALID_TOKEN")  # stub: 경로가 열려 있다
    monkeypatch.setenv("LINE_MODE", "line")
    monkeypatch.setenv("LINE_CHANNEL_SECRET", SECRET)
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "test-access-token")
    monkeypatch.setenv("LINE_OFFICIAL_ACCOUNT_ID", "@cover-shift")
    assert err(api.c.post("/api/line/reply", json=reply)) == (404, "NOT_FOUND")
    assert err(api.c.post("/api/line/link", json=link)) == (404, "NOT_FOUND")
    assert err(api.c.post("/api/line/reply", json={})) == (404, "NOT_FOUND")  # 본문 검사보다 먼저 닫힌다
    assert resumes(api) == []
    monkeypatch.setenv("LINE_MODE", "stub")
    assert api.c.post("/api/line/reply", json=reply).status_code == 202 and len(resumes(api)) == 1
