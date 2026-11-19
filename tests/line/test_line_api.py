"""LINE 과 맞닿는 모양 (A-20, A-21): 서명 · 버튼 답 · 메시지 변환 · push 요청. 가짜 전송 계층만 쓴다."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json

import httpx
import pytest

from app import line_api


# ---------------------------------------------------------------- 서명

@pytest.mark.req("A-20")
def test_signature_is_base64_hmac_sha256_of_raw_body():
    body = '{"destination":"U1","events":[]}'.encode()
    expected = base64.b64encode(hmac.new(b"secret", body, hashlib.sha256).digest()).decode()
    assert line_api.sign("secret", body) == expected
    assert line_api.verify_signature("secret", body, expected) is True
    # 깨뜨리는 쪽: 다른 키 · 바뀐 본문 · 없는 서명 · 빈 키(누구나 만들 수 있는 서명)
    assert line_api.verify_signature("other", body, expected) is False
    assert line_api.verify_signature("secret", body + b" ", expected) is False
    assert line_api.verify_signature("secret", body, None) is False
    assert line_api.verify_signature("secret", body, "서명") is False
    assert line_api.verify_signature("", body, line_api.sign("", body)) is False


# ---------------------------------------------------------------- 버튼 답

@pytest.mark.req("A-20")
def test_postback_data_round_trip_and_rejects_other_shapes():
    assert line_api.postback_data(12, "yes") == "contact:12:yes"
    assert line_api.parse_postback_data("contact:12:yes") == (12, "yes")
    assert line_api.parse_postback_data("contact:12:no") == (12, "no")
    for bad in ("contact:12:maybe", "contact:-1:yes", "contact:x:yes", "contact:12", "other:12:yes", "", None, 12,
                "contact:１２:yes", "contact:12:yes:extra"):
        assert line_api.parse_postback_data(bad) is None, bad


# ---------------------------------------------------------------- 발송함 본문 → 메시지

@pytest.mark.req("A-21")
def test_each_kind_becomes_one_readable_message():
    link = line_api.to_messages("link_token", {"text": "연결해 주세요", "url": "https://x/l/t",
                                               "expires_at": "2026-11-26T10:00:00+09:00"})
    assert link == [{"type": "text", "text": "연결해 주세요\nhttps://x/l/t\n(기한 2026-11-26)"}]
    submit = line_api.to_messages("submission_link", {"text": "2026-12 근무 희망을 제출해 주세요", "url": "https://x/s/t",
                                                      "submission_deadline": "2026-11-25T23:59:00+09:00"})
    assert submit == [{"type": "text",
                       "text": "2026-12 근무 희망을 제출해 주세요\nhttps://x/s/t\n(마감 2026-11-25 23:59)"}]
    confirmed = line_api.to_messages("shift_confirmed", {
        "text": "2026-11 시프트가 확정되었습니다. 출근 2일.", "draft_id": 3,
        "work_days": [{"work_date": "2026-11-02", "start_time": "09:00:00", "end_time": "18:00:00"},
                      {"work_date": "2026-11-03", "start_time": "13:00", "end_time": "22:30"}],
        "paid_leave_days": ["2026-11-05"]})
    assert confirmed == [{"type": "text", "text": "2026-11 시프트가 확정되었습니다. 출근 2일.\n"
                                                  "2026-11-02 09:00~18:00\n2026-11-03 13:00~22:30\n2026-11-05 유급"}]
    assert line_api.to_messages("manager_notice", {"text": "알림"}) == [{"type": "text", "text": "알림"}]
    with pytest.raises(KeyError):
        line_api.to_messages("unknown_kind", {"text": "x"})


@pytest.mark.req("A-21", "M-5")
def test_contact_ask_has_yes_no_postback_buttons_that_the_webhook_can_read():
    question = "2026-11-03 13:00~13:30 근무 가능하신가요?"
    [message] = line_api.to_messages("contact_ask", {"text": question, "contact_id": 7, "buttons": ["yes", "no"]})
    assert (message["type"], message["altText"], message["template"]["type"]) == ("template", question, "buttons")
    assert message["template"]["text"] == question
    actions = message["template"]["actions"]
    assert [(a["type"], a["label"]) for a in actions] == [("postback", "가능"), ("postback", "불가")]
    assert [line_api.parse_postback_data(a["data"]) for a in actions] == [(7, "yes"), (7, "no")]


@pytest.mark.req("A-20", "M-5")
def test_button_answers_are_answers_the_graph_accepts():
    """버튼의 답 값은 한 벌이다: LINE 버튼(line_api) = 그래프가 받는 답(contact.ANSWER_STATUS)."""
    from app.graph.nodes.contact import ANSWER_STATUS
    assert set(line_api.ANSWER_LABELS) == set(ANSWER_STATUS) - {"expired"}  # expired 는 버튼이 아니다 (타이머용)


@pytest.mark.req("A-20")
def test_official_account_link_percent_encodes_id_and_text():
    url = line_api.official_account_message_url("@cover", "a-b_c/d e")
    assert url == "https://line.me/R/oaMessage/%40cover/?a-b_c%2Fd%20e"


# ---------------------------------------------------------------- push 요청 모양

@pytest.mark.req("A-21")
def test_push_request_shape_and_status_passthrough():
    seen: list[httpx.Request] = []

    def server(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(409, json={"message": "The retry key is already accepted"})

    messages = [{"type": "text", "text": "안내"}]
    result = line_api.push("tok", to="U-1", messages=messages, retry_key="123e4567-e89b-12d3-a456-426614174000",
                           transport=httpx.MockTransport(server))
    [request] = seen
    assert (request.method, str(request.url)) == ("POST", "https://api.line.me/v2/bot/message/push")
    assert request.headers["authorization"] == "Bearer tok"
    assert request.headers["x-line-retry-key"] == "123e4567-e89b-12d3-a456-426614174000"  # 첫 요청부터 넣는다
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {"to": "U-1", "messages": messages}
    assert result.status_code == 409 and "already accepted" in result.text


@pytest.mark.req("A-21")
def test_push_without_a_fake_transport_is_blocked_in_tests():
    """tests/conftest.py 의 no_outside_network 가 실제로 막는지 — 막지 못하면 시험이 조용히 진짜 LINE 을 부를 수 있다."""
    with pytest.raises(AssertionError, match="api.line.me"):
        line_api.push("tok", to="U-1", messages=[], retry_key="k")


@pytest.mark.req("A-21")
def test_outside_network_is_blocked_but_local_addresses_are_not():
    import socket
    for outside in ("api.line.me", "api.anthropic.com", "8.8.8.8"):
        with pytest.raises(AssertionError, match="바깥 네트워크"):
            socket.getaddrinfo(outside, 443)
    with pytest.raises(AssertionError, match="바깥 네트워크"), socket.socket() as s:
        s.connect(("203.0.113.1", 443))  # 이름 풀이 없이 IP 로 바로
    assert socket.getaddrinfo("127.0.0.1", 5443) and socket.getaddrinfo("localhost", 80)  # 지키는 쪽: 로컬은 통과
