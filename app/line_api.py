"""LINE Messaging API 와 맞닿는 곳 한 장: 웹훅 서명 · 메시지 모양 · push HTTP (A-20, A-21).

근거는 LINE 공식 문서(developers.line.biz, 2026-11-19 확인). 확인하지 못하고 넣은 곳에는 `(미확인: ...)` 을 달았다.
이 파일은 DB 를 모른다. 발송함 행을 고르고 결과를 적는 것은 app/line_dispatcher.py 가 한다.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass
from urllib.parse import quote

import httpx

PUSH_URL = "https://api.line.me/v2/bot/message/push"  # LINE 문서 · push 메시지
# push 의 단계별 시간 한도(초). httpx 는 연결 · 보내기 · 받기에 **각각** 이 값을 건다 → 한 번의 push 는 최악 약 3배(24초).
# 발송기는 SIGTERM 뒤 보내던 한 건을 마치고 끝나므로 compose 의 stop_grace_period 를 그보다 길게 둔다
HTTP_TIMEOUT_SEC = 8.0
# 버튼 답(postback.data)의 모양: contact:{contact_id}:{yes|no}. 짧게 둔다 (data 길이 제한은 미확인)
POSTBACK_PREFIX = "contact"
# 버튼의 답 값 → 버튼 글자. 답 값은 그래프가 받는 값(app/graph/nodes/contact.py ANSWER_STATUS 의 키)과 같아야 한다
# — 이 파일은 그래프를 import 하지 않으므로 시험이 맞춘다 (tests/line/test_line_api.py)
ANSWER_LABELS = {"yes": "가능", "no": "불가"}


# ---------------------------------------------------------------- 웹훅 서명

def sign(channel_secret: str, body: bytes) -> str:
    """채널 시크릿을 키로 요청 본문 **원문 바이트**를 HMAC-SHA256 → base64 (LINE 문서 · 웹훅 서명)."""
    return base64.b64encode(hmac.new(channel_secret.encode(), body, hashlib.sha256).digest()).decode()


def verify_signature(channel_secret: str, body: bytes, signature: str | None) -> bool:
    """x-line-signature 가 이 본문의 서명과 같은가. 본문을 파싱하기 전에 부른다.

    시크릿이 비어 있으면 항상 False: 빈 키의 서명은 누구나 만들 수 있다. 비교는 상수 시간(hmac.compare_digest).
    """
    if not channel_secret or not signature:
        return False
    return hmac.compare_digest(sign(channel_secret, body).encode(), signature.strip().encode())


# ---------------------------------------------------------------- 버튼 답 (postback.data)

def postback_data(contact_id: int, answer: str) -> str:
    return f"{POSTBACK_PREFIX}:{contact_id}:{answer}"


def parse_postback_data(data: object) -> tuple[int, str] | None:
    """postback_data 의 반대. 우리가 만든 모양이 아니면 None (다른 버튼 · 손으로 만든 값)."""
    if not isinstance(data, str):
        return None
    prefix, _, rest = data.partition(":")
    contact_id, _, answer = rest.partition(":")
    if prefix != POSTBACK_PREFIX or not contact_id.isascii() or not contact_id.isdigit() or answer not in ANSWER_LABELS:
        return None
    return int(contact_id), answer


# ---------------------------------------------------------------- 발송함 본문 → LINE 메시지

def to_messages(kind: str, body: dict) -> list[dict]:
    """line_outbox 의 (kind, body) 를 push 의 messages 로. 종류마다 사람이 읽을 문장 1개를 만든다.

    모르는 kind 는 KeyError — 발송기가 그 행을 「실패」로 끝낸다 (조용히 빈 메시지를 보내지 않는다).
    """
    return [_BUILDERS[kind](body)]


def _text(text: str) -> dict:
    return {"type": "text", "text": text}


def _hhmm(value: str) -> str:
    return value[:5]  # 발송함 JSON 의 시각은 "09:00" 또는 "09:00:00"


def _link_token(body: dict) -> dict:
    return _text(f"{body['text']}\n{body['url']}\n(기한 {body['expires_at'][:10]})")


def _submission_link(body: dict) -> dict:
    return _text(f"{body['text']}\n{body['url']}\n(마감 {body['submission_deadline'][:16].replace('T', ' ')})")


def _contact_ask(body: dict) -> dict:
    """[가능][불가] 버튼. 누르면 웹훅으로 postback 이벤트가 온다 (app/api/routes_line_webhook.py)."""
    # (미확인: LINE 문서 #postback-action 대조 필요) postback action 의 필드(type · label · data)와 data 길이 제한,
    # buttons 템플릿 text 의 길이 제한을 문서로 확인하지 못했다
    actions = [{"type": "postback", "label": label, "data": postback_data(body["contact_id"], answer)}
               for answer, label in ANSWER_LABELS.items()]
    return {"type": "template", "altText": body["text"],
            "template": {"type": "buttons", "text": body["text"], "actions": actions}}


def _shift_confirmed(body: dict) -> dict:
    lines = [body["text"]]
    lines += [f"{day['work_date']} {_hhmm(day['start_time'])}~{_hhmm(day['end_time'])}" for day in body["work_days"]]
    lines += [f"{work_date} 유급" for work_date in body["paid_leave_days"]]
    return _text("\n".join(lines))


def _manager_notice(body: dict) -> dict:
    return _text(body["text"])


_BUILDERS = {"link_token": _link_token, "submission_link": _submission_link, "contact_ask": _contact_ask,
             "shift_confirmed": _shift_confirmed, "manager_notice": _manager_notice}


# ---------------------------------------------------------------- 연결 링크

def official_account_message_url(line_id: str, text: str) -> str:
    """공식 계정 대화창을 text 가 미리 채워진 채로 여는 링크 (LINE 문서 · URL scheme oaMessage)."""
    return f"https://line.me/R/oaMessage/{quote(line_id, safe='')}/?{quote(text, safe='')}"


# ---------------------------------------------------------------- push (실제 HTTP 는 여기 한 곳)

@dataclass(frozen=True)
class PushResponse:
    status_code: int
    text: str  # 응답 본문 원문. 기록 · 로그에 쓸 때는 발송기가 줄이고 가린다


def push(access_token: str, *, to: str, messages: list[dict], retry_key: str,
         transport: httpx.BaseTransport | None = None) -> PushResponse:
    """push 한 번. 연결 실패 · 시간 초과는 httpx.HTTPError 로 올라간다 (재시도 여부는 발송기가 정한다).

    X-Line-Retry-Key 는 첫 요청부터 넣는다. 같은 키로 다시 보내 이미 접수된 요청이면 LINE 이 409 를 준다 (LINE 문서 · 재시도 키).
    transport 는 시험에서 가짜 서버를 끼우는 자리다.
    """
    headers = {"Authorization": f"Bearer {access_token}", "X-Line-Retry-Key": retry_key}
    with httpx.Client(timeout=HTTP_TIMEOUT_SEC, transport=transport) as client:
        response = client.post(PUSH_URL, headers=headers, json={"to": to, "messages": messages})
    return PushResponse(response.status_code, response.text)
