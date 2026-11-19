"""LINE 발송기 (A-21). 실행: python -m app.line_dispatcher

line_outbox 의 「대기(pending)」 행을 한 건씩 잡아 보내고 결과를 적는다. 그래프 · API 의 트랜잭션 밖에서 돈다.

    pending ─┬─ 200 · 409 ───────────────────────────→ sent
             ├─ 5xx · 429 · 시간 초과 · 연결 실패 ───→ pending (시도 +1, 다음 시도 시각을 뒤로) … 기한이 지나면 failed
             ├─ 그 밖의 4xx · 메시지를 못 만듦 · 예외 → failed (다시 보내도 결과가 같다)
             └─ 받는 사람의 line_user_id 없음 ───────→ no_recipient

한 건 = 한 트랜잭션: 행을 FOR UPDATE SKIP LOCKED 로 잡고 → 보내고 → 결과를 적고 → 커밋.
- 발송기가 둘 떠 있어도 같은 행을 같이 잡지 않는다 (잡힌 행은 건너뛴다).
- 보내는 도중 프로세스가 죽으면 잠금이 풀리고 행은 pending 그대로 → 다음에 다시 잡힌다.
  이미 LINE 에 닿은 뒤였다면 같은 retry_key 로 다시 가서 409 를 받고 sent 가 된다 (LINE 문서 · 재시도 키).
  이 중복 방지는 재시도 키가 유효한 24시간 안에서만 선다 → 재시도 기한(RETRY_WINDOW_SEC)을 그 안쪽에 둔다.
- 한 행에서 무슨 일이 나도 그 행만 failed 로 끝낸다. 한 행이 발송기를 죽이면 다시 떠도 같은 행을 또 잡아 뒤가 전부 막힌다.
"""
from __future__ import annotations

import logging
import signal
import threading

import httpx
import psycopg

from app import line_api
from app.config import Settings, get_settings
from app.db import connect

log = logging.getLogger("line_dispatcher")

# (가정 신규, G25) 재시도 숫자. 명세 · LINE 문서에 권장값이 없다(재시도 키 유효 24시간만 있다).
# 대기에 들어간 뒤(queued_at) 6시간까지 다시 보낸다. LINE 의 짧은 장애 · 순간 속도 제한은 넘기고,
# 같은 키로 다시 보내도 LINE 이 중복을 걸러 주는 24시간 안쪽에서 끝나게 한 값이다.
RETRY_WINDOW_SEC = 6 * 3600
BACKOFF_BASE_SEC = 30  # 30초 → 1분 → 2분 → … 두 배씩
BACKOFF_MAX_SEC = 900  # 간격 상한 15분
IDLE_SLEEP_SEC = 2.0  # 대기 행이 없을 때 · DB 오류 뒤에 쉬는 시간
ERROR_MAX_LEN = 200  # last_error 에 남기는 글자 수

ALREADY_ACCEPTED = 409  # 같은 재시도 키의 요청이 이미 접수됨 = 이미 보내졌다 (LINE 문서 · 재시도 키)
TOO_MANY_REQUESTS = 429  # 순간 속도 제한일 수 있다 → 5xx 처럼 다시 보낸다. 월 한도 초과라면 기한 뒤 failed 로 끝난다

# 한 번 보내 본 결과. RETRY 만 표의 status 가 아니다 (기한 안이면 pending 으로 남고, 지났으면 failed 가 된다)
SENT, RETRY, FAILED, NO_RECIPIENT = "sent", "retry", "failed", "no_recipient"

CLAIM_SQL = """
    SELECT o.message_id, o.kind, o.body, o.attempts, o.retry_key, e.line_user_id,
           extract(epoch FROM now() - o.queued_at)::float AS queued_sec
    FROM line_outbox o LEFT JOIN employees e ON e.employee_id = o.employee_id
    WHERE o.status = 'pending' AND o.next_attempt_at <= now()
    ORDER BY o.next_attempt_at, o.message_id
    LIMIT 1
    FOR UPDATE OF o SKIP LOCKED"""


def backoff_sec(attempts: int) -> int:
    """attempts 번 실패한 뒤 다음 시도까지 기다릴 초. 두 배씩 늘고 BACKOFF_MAX_SEC 에서 멈춘다."""
    return min(BACKOFF_BASE_SEC * 2 ** (attempts - 1), BACKOFF_MAX_SEC)


def dispatch_one(conn: psycopg.Connection, settings: Settings) -> bool:
    """보낼 때가 된 대기 행 하나를 처리하고 커밋한다. 처리했으면 True, 잡을 행이 없으면 False."""
    row = conn.execute(CLAIM_SQL).fetchone()
    if row is None:
        conn.rollback()  # 트랜잭션을 닫는다. 열어 두면 now() 가 멈춰 다음 조회가 같은 시각으로 본다
        return False
    result, error = _deliver(row, settings)
    retry_in = backoff_sec(row["attempts"] + 1)
    if result == RETRY and row["queued_sec"] + retry_in >= RETRY_WINDOW_SEC:
        result = FAILED  # 다음 시도가 기한 밖이다 → 여기서 그만둔다
    _record(conn, row, result, error, retry_in)
    conn.commit()
    # 실패는 아무도 다시 보지 않는 끝 상태다 → ERROR 로 남겨 로그 감시에 걸리게 한다 (되돌리기: ops/requeue_line.py)
    log.log(logging.ERROR if result == FAILED else logging.INFO, "LINE dispatch message_id=%s kind=%s → %s%s",
            row["message_id"], row["kind"], result, f" ({error})" if error else "")
    return True


def _deliver(row: dict, settings: Settings) -> tuple[str, str | None]:
    """한 건을 보내 보고 (결과, 오류 문장) 을 돌려준다. 결과 = SENT | RETRY | FAILED | NO_RECIPIENT.

    예외를 밖으로 내보내지 않는다. 오류 문장에는 예외의 형 이름만 싣는다 (문장에 토큰이 섞일 수 있다).
    """
    if row["line_user_id"] is None:
        return NO_RECIPIENT, None
    try:
        messages = line_api.to_messages(row["kind"], row["body"])
        if settings.line_mode == "stub":
            log.info("LINE stub SEND message_id=%s to=%s messages=%s", row["message_id"], row["line_user_id"], messages)
            return SENT, None
        response = line_api.push(settings.line_channel_access_token, to=row["line_user_id"], messages=messages,
                                 retry_key=str(row["retry_key"]))
    except httpx.HTTPError as e:  # 시간 초과 · 연결 실패 등 — LINE 에 닿았는지 모른다
        return RETRY, f"연결 실패: {type(e).__name__}"
    except Exception as e:  # 못 만드는 본문, 헤더에 못 싣는 토큰 값 등 — 다시 해도 같다
        return FAILED, f"보낼 수 없음: {type(e).__name__}"
    if response.status_code in (200, ALREADY_ACCEPTED):
        return SENT, None
    retryable = response.status_code >= 500 or response.status_code == TOO_MANY_REQUESTS
    # 그 밖의 4xx 는 다시 보내도 결과가 같다 (LINE 문서 · 재시도 키)
    return (RETRY if retryable else FAILED), _safe_error(response, settings)


def _safe_error(response: line_api.PushResponse, settings: Settings) -> str:
    """last_error · 로그에 남길 문장: 상태 코드 + 응답 본문 앞부분. 토큰 · 시크릿이 섞여 있으면 가린다."""
    text = " ".join(response.text.split())
    for secret in (settings.line_channel_access_token, settings.line_channel_secret):
        if secret:
            text = text.replace(secret, "***")
    return f"HTTP {response.status_code}: {text}"[:ERROR_MAX_LEN]


def _record(conn: psycopg.Connection, row: dict, result: str, error: str | None, retry_in: int) -> None:
    """발송 결과를 그 행에 적는다. 고치는 열은 covershift_app 에 UPDATE 를 준 5개뿐이다 (db/002)."""
    message_id = row["message_id"]
    if result == NO_RECIPIENT:  # 보내 보지 않았으므로 시도 횟수는 그대로
        conn.execute("UPDATE line_outbox SET status = 'no_recipient', next_attempt_at = NULL WHERE message_id = %s",
                     (message_id,))
        return
    attempts = row["attempts"] + 1  # 기록용. 그만둘지는 횟수가 아니라 기한으로 정한다
    if result == SENT:
        conn.execute(
            """UPDATE line_outbox SET status = 'sent', attempts = %s, sent_at = now(), next_attempt_at = NULL,
                                      last_error = NULL
               WHERE message_id = %s""", (attempts, message_id))
    elif result == RETRY:
        conn.execute(
            """UPDATE line_outbox SET attempts = %s, last_error = %s,
                                      next_attempt_at = now() + make_interval(secs => %s)
               WHERE message_id = %s""", (attempts, error, retry_in, message_id))
    else:
        conn.execute(
            """UPDATE line_outbox SET status = 'failed', attempts = %s, last_error = %s, next_attempt_at = NULL
               WHERE message_id = %s""", (attempts, error, message_id))


def run(stop: threading.Event) -> None:
    """stop 이 켜질 때까지 돈다. 대기 행이 없으면 잠깐 쉬고 다시 본다. DB 가 끊기면 쉬었다가 다시 연결한다."""
    settings = get_settings()
    log.info("LINE 발송기 시작 mode=%s", settings.line_mode)
    while not stop.is_set():
        try:
            with connect() as conn:
                while not stop.is_set():
                    if not dispatch_one(conn, settings):
                        stop.wait(IDLE_SLEEP_SEC)
        except psycopg.Error as e:
            # SQLSTATE 로 원인을 가른다: 08xxx 연결 끊김 / 42703 열 없음(옛 스키마) / 42501 권한 없음. 접속 실패는 None
            log.error("DB 오류 → 잠시 뒤 다시 연결: %s sqlstate=%s", type(e).__name__, e.sqlstate)
            stop.wait(IDLE_SLEEP_SEC)
    log.info("LINE 발송기 종료")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # 발송 기록은 위 「LINE dispatch」 한 줄로 남긴다
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())  # 보내던 한 건은 마치고 끝난다 (compose 의 stop_grace_period)
    run(stop)


if __name__ == "__main__":
    main()
