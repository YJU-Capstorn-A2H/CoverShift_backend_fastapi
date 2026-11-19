"""LINE 웹훅 수신 (A-20). LINE 플랫폼이 부른다 — 관리자 JWT 가 아니라 요청 서명으로 확인한다.

순서 (LINE 문서 · 웹훅 서명): 본문 원문 바이트로 서명 검증 → 통과한 뒤에만 JSON 을 읽는다.
서명이 틀리면 이벤트를 하나도 처리하지 않는다. 보낸 쪽 IP 로는 거르지 않는다 (문서가 하지 말라고 한다).

받는 이벤트:
- postback (답장 버튼 [가능][불가]) → 보낸 사람(source.userId)이 그 연락의 직원일 때만 답으로 받는다.
- 텍스트 메시지가 유효한 연결 토큰 → 보낸 사람을 그 직원에 연결한다 (G24).
- 그 밖의 타입 · mode=standby 는 건너뛴다.

응답 (G26):
- 200 = 이벤트를 전부 끝냈다. 「끝」에는 반영 · 건너뜀 · **영구 거절**(남의 연락, 이미 답함, 모르는 토큰 …)이 들어간다.
  다시 보내도 결과가 같은 것을 LINE 이 다시 보내게 하지 않는다.
- 503 = 하나라도 **일시 장애**(DB 접속 실패, 러너 오류, 그래프가 도는 중이라 못 받음)였다 → LINE 이 요청을 다시 보내게 한다.
  같은 요청 안의 다른 이벤트는 그대로 처리한다.

같은 이벤트가 두 번 올 수 있다 (재전송). webhookEventId 를 저장해 거르지 않는다 — 처리 자체가 한 번만 반영되기 때문이다:
- 답: 첫 답이 그래프를 그 질문에서 떠나게 한다 → 다음 것은 reply_to_contact 가 거절한다.
- 연결: 첫 처리가 토큰을 비운다 → 다음 것은 「모르는 토큰」이다.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from functools import partial
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, Header, Request

from app import line_api
from app.config import get_settings
from app.db import connect
from app.repo import line as line_repo

from .deps import Runner
from .errors import ApiError
from .routes_control import reply_to_contact

router = APIRouter(prefix="/api", tags=["line-webhook"])
log = logging.getLogger("api.line_webhook")

# (가정 신규) 웹훅 본문 상한. 서명을 검증하려면 본문을 통째로 메모리에 읽어야 하므로, 서명 없는 큰 요청을 읽기 전에 끊는다.
# LINE 이 보내는 본문의 최대 크기는 문서에서 확인하지 못했다 — 이벤트 하나가 1KB 안팎이라 넉넉한 값으로 두었다
MAX_BODY_BYTES = 1024 * 1024


async def raw_body(request: Request) -> bytes:
    """받은 그대로의 바이트. 서명은 이 값으로만 맞는다 (다시 직렬화한 JSON 은 바이트가 달라진다).

    MAX_BODY_BYTES 를 넘으면 413. Content-Length 가 넘으면 읽지 않고, 없거나 거짓이면 읽다가 넘는 순간 그만둔다.
    """
    too_large = ApiError(413, "PAYLOAD_TOO_LARGE", "웹훅 본문이 너무 큽니다")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise too_large
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            raise too_large
    return bytes(body)


@router.post("/line/webhook")
def line_webhook(runner: Runner, body: Annotated[bytes, Depends(raw_body)],
                 x_line_signature: Annotated[str | None, Header()] = None):
    """서명이 맞고 이벤트를 전부 끝냈으면 200 (이벤트가 0개인 콘솔 Verify 요청 포함). 일시 장애가 있었으면 503.

    처리는 요청 안에서 순서대로 한다. 이벤트마다 DB 조회 몇 번이고, 버튼 답이면 러너에 넘긴다 — RUNNER=aegra 에서는
    Aegra 로 HTTP 호출이 몇 번 나가고, 그 thread 에 도는 run 이 있으면 러너가 최대 3초 기다린다 (app/runner.py
    AegraRunner idle_wait_sec). 편성 자체는 러너가 뒤에서 돌린다.
    """
    if not line_api.verify_signature(get_settings().line_channel_secret, body, x_line_signature):
        raise ApiError(401, "INVALID_SIGNATURE", "LINE 서명이 올바르지 않습니다")
    try:
        events = json.loads(body)["events"]
    except (ValueError, KeyError, TypeError):
        events = None
    if not isinstance(events, list):
        raise ApiError(400, "INVALID_WEBHOOK", "웹훅 본문 모양이 올바르지 않습니다")
    finished = [_finish(event, runner) for event in events]  # 하나가 실패해도 나머지를 끝까지 처리한다
    if not all(finished):
        raise ApiError(503, "TRY_AGAIN", "일부 이벤트를 지금 처리하지 못했습니다")
    return {}


def _finish(event: object, runner) -> bool:
    """이벤트 1개를 처리한다. 끝났으면(반영 · 건너뜀 · 영구 거절) True, 일시 장애라 다시 받아야 하면 False.

    이벤트 1개 = DB 연결 1개 = 트랜잭션 1개 → 한 이벤트의 실패가 다른 이벤트의 기록을 취소하지 않는다.
    """
    event_id = event.get("webhookEventId") if isinstance(event, dict) else None
    try:
        action = _read(event)
    except (AttributeError, KeyError, TypeError):  # 우리가 아는 모양이 아니다. 다시 받아도 같다
        log.warning("LINE 웹훅 이벤트 %s: 읽을 수 없는 모양 → 건너뜀", event_id)
        return True
    if action is None:
        return True
    try:
        with connect() as conn:
            action(conn, runner)
    except ApiError as e:
        if e.code == "BUSY":  # 그래프가 도는 중 · 같은 기간의 다른 요청 처리 중 — 잠시 뒤에는 받을 수 있다
            log.warning("LINE 웹훅 이벤트 %s: 지금은 받을 수 없음(BUSY) → 재전송 요청", event_id)
            return False
        log.info("LINE 웹훅 이벤트 %s 거절: %s", event_id, e.code)  # 남의 연락 · 이미 답함 · 기다리지 않음
        return True
    except psycopg.IntegrityError as e:  # 이 LINE 계정이 이미 다른 직원에 연결돼 있다 (UNIQUE)
        log.info("LINE 웹훅 이벤트 %s 거절: %s", event_id, type(e).__name__)
        return True
    except Exception:  # DB 접속 실패 · 러너 오류 등
        log.exception("LINE 웹훅 이벤트 %s 처리 실패 → 재전송 요청", event_id)
        return False
    return True


def _read(event: dict) -> Callable | None:
    """이벤트에서 할 일을 읽는다 → (conn, runner) 를 받는 함수. 건너뛸 이벤트면 None. 모양이 틀리면 예외."""
    kind, user_id = event.get("type"), (event.get("source") or {}).get("userId")
    if event.get("mode") == "standby" or not user_id:
        log.info("LINE 웹훅 이벤트 건너뜀: type=%s mode=%s", kind, event.get("mode"))
        return None
    if kind == "postback":
        parsed = line_api.parse_postback_data(event["postback"]["data"])
        if parsed is None:
            log.info("LINE 웹훅: 모르는 postback data")
            return None
        return partial(_reply, user_id=user_id, contact_id=parsed[0], answer=parsed[1])
    if kind == "message" and event["message"].get("type") == "text":
        return partial(_link, user_id=user_id, text=str(event["message"]["text"]))
    log.info("LINE 웹훅 이벤트 건너뜀: type=%s", kind)
    return None


def _reply(conn, runner, *, user_id: str, contact_id: int, answer: str) -> None:
    reply_to_contact(conn, runner, contact_id=contact_id, line_user_id=user_id, answer=answer)
    log.info("LINE 웹훅: 연락 %s 의 답 %s 을 받았다", contact_id, answer)


def _link(conn, runner, *, user_id: str, text: str) -> None:
    """메시지가 유효한 연결 토큰이면 보낸 사람을 그 직원에 연결한다. 토큰은 1회용 · 기한 있음 (app/repo/line.py).

    이 LINE 계정이 이미 다른 직원에 연결돼 있으면 UNIQUE 위반 → 이 이벤트의 트랜잭션만 취소된다.
    메시지 원문은 로그에 찍지 않는다 (토큰이거나 직원의 사적인 말이다).
    """
    employee = line_repo.find_by_link_token(conn, text.strip())
    if employee is None:
        log.info("LINE 웹훅: 연결 토큰이 아닌 메시지")
        return
    line_repo.link(conn, employee["employee_id"], user_id)
    log.info("LINE 웹훅: 직원 %s 연결", employee["employee_id"])
