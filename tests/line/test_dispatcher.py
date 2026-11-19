"""발송기 (A-21): 대기 행을 한 건씩 보내고 결과를 적는다. LINE 은 가짜(fake_push)다."""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time

import httpx
import pytest

from app import line_dispatcher
from app.line_dispatcher import BACKOFF_BASE_SEC, BACKOFF_MAX_SEC, RETRY_WINDOW_SEC, backoff_sec, dispatch_one

from .conftest import ACCESS_TOKEN, CHANNEL_SECRET


class ProcessKilled(BaseException):
    """보내는 도중 프로세스가 죽은 것을 흉내 낸다 (Exception 이 아니라서 발송기가 잡지 않는다)."""


def dispatch(outbox, settings) -> bool:
    with outbox.connect() as conn:
        return dispatch_one(conn, settings)


# ---------------------------------------------------------------- 보냄

@pytest.mark.req("A-21")
@pytest.mark.parametrize("status", [200, 409])  # 409 = 같은 재시도 키가 이미 접수됨 = 이미 보내졌다
def test_200_and_409_mark_the_row_sent(outbox, line_settings, fake_push, status):
    fake_push.script = [status]
    mid = outbox.add()
    assert dispatch(outbox, line_settings) is True
    row = outbox.row(mid)
    assert (row["status"], row["attempts"], row["next_attempt_at"], row["last_error"]) == ("sent", 1, None, None)
    assert row["sent_at"] is not None
    assert fake_push.calls == [{"access_token": ACCESS_TOKEN, "to": "U-1", "retry_key": str(row["retry_key"]),
                                "messages": [{"type": "text", "text": "안내입니다"}]}]
    assert dispatch(outbox, line_settings) is False and len(fake_push.calls) == 1  # 보낸 행은 다시 잡지 않는다


@pytest.mark.req("A-21", "A-22")
def test_stub_mode_marks_sent_without_calling_line(outbox, stub_settings, fake_push, caplog):
    mid = outbox.add()
    with caplog.at_level(logging.INFO, logger="line_dispatcher"):
        assert dispatch(outbox, stub_settings) is True
    assert outbox.row(mid)["status"] == "sent" and fake_push.calls == []
    assert "LINE stub SEND" in caplog.text and "안내입니다" in caplog.text  # 스텁은 로그에 찍는다 (D6)


# ---------------------------------------------------------------- 재시도 · 실패

@pytest.mark.req("A-21")
@pytest.mark.parametrize("failure", [500, 503, 429, httpx.ConnectTimeout("t"), httpx.ConnectError("c")])
def test_5xx_429_timeout_and_connection_failure_stay_pending_and_back_off(outbox, line_settings, fake_push, failure):
    fake_push.script = [failure]
    mid = outbox.add()
    assert dispatch(outbox, line_settings) is True
    row = outbox.row(mid)
    assert (row["status"], row["attempts"], row["sent_at"]) == ("pending", 1, None)
    assert BACKOFF_BASE_SEC - 5 < row["due_in"] <= BACKOFF_BASE_SEC  # 다음 시도 시각이 뒤로
    assert row["last_error"]
    assert dispatch(outbox, line_settings) is False and len(fake_push.calls) == 1  # 때가 되기 전에는 다시 안 잡는다


@pytest.mark.req("A-21")
def test_retry_uses_same_retry_key_recipient_and_messages_then_succeeds(outbox, line_settings, fake_push):
    fake_push.script = [500, 500, 200]
    mid = outbox.add(kind="contact_ask", body={"text": "11/03 13:00~13:30 근무 가능하신가요?", "contact_id": 7})
    for _ in range(3):
        outbox.make_due()
        assert dispatch(outbox, line_settings) is True
    first, *rest = fake_push.calls
    assert len(rest) == 2 and all(call == first for call in rest)  # 재시도 키 · 수신자 · 내용이 원래와 같다
    row = outbox.row(mid)
    assert (row["status"], row["attempts"], row["last_error"]) == ("sent", 3, None)
    assert first["retry_key"] == str(row["retry_key"])


@pytest.mark.req("A-21")
def test_each_row_has_its_own_retry_key(outbox, line_settings, fake_push):
    outbox.add(), outbox.add()
    assert dispatch(outbox, line_settings) and dispatch(outbox, line_settings)
    assert fake_push.calls[0]["retry_key"] != fake_push.calls[1]["retry_key"]


@pytest.mark.req("A-21")
def test_keeps_retrying_inside_the_window_whatever_the_attempt_count(outbox, line_settings, fake_push):
    """그만두는 기준은 횟수가 아니라 기한이다. 간격은 두 배씩 늘다가 상한(15분)에서 멈춘다."""
    fake_push.script = [500]
    mid = outbox.add()
    for attempt in range(1, 9):  # 옛 상한(5번)을 넘겨도
        outbox.make_due()
        assert dispatch(outbox, line_settings) is True
        row = outbox.row(mid)
        assert (row["status"], row["attempts"]) == ("pending", attempt)
    assert BACKOFF_MAX_SEC - 5 < row["due_in"] <= BACKOFF_MAX_SEC  # 30초 × 2^7 이 아니라 상한


@pytest.mark.req("A-21")
def test_gives_up_as_failed_with_error_log_when_the_window_is_over(outbox, line_settings, fake_push, caplog):
    fake_push.script = [500]
    inside, outside = outbox.add(), outbox.add()
    # 다음 시도(30초 뒤)가 기한 안에 들어가는 행 / 기한 밖으로 나가는 행
    outbox.age(inside, f"{RETRY_WINDOW_SEC - 120} seconds")
    outbox.age(outside, f"{RETRY_WINDOW_SEC - 10} seconds")
    with caplog.at_level(logging.INFO, logger="line_dispatcher"):
        assert dispatch(outbox, line_settings) and dispatch(outbox, line_settings)
    assert outbox.row(inside)["status"] == "pending"
    row = outbox.row(outside)
    assert (row["status"], row["attempts"], row["next_attempt_at"], row["sent_at"]) == ("failed", 1, None, None)
    assert row["last_error"].startswith("HTTP 500")
    levels = {r.args[0]: r.levelname for r in caplog.records if r.msg.startswith("LINE dispatch")}
    assert levels == {inside: "INFO", outside: "ERROR"}  # 실패로 끝날 때만 ERROR
    assert dispatch(outbox, line_settings) is False and len(fake_push.calls) == 2  # 실패 행은 다시 잡지 않는다


@pytest.mark.req("A-21")
def test_retry_window_ends_inside_the_retry_key_lifetime():
    assert RETRY_WINDOW_SEC < 24 * 3600  # 같은 키의 중복 방지는 24시간 (LINE 문서 · 재시도 키)


@pytest.mark.req("A-21")
@pytest.mark.parametrize("status", [400, 401, 403])
def test_4xx_fails_at_once_without_retry(outbox, line_settings, fake_push, status, caplog):
    fake_push.script = [(status, '{"message": "The request body has 1 error(s)"}')]
    mid = outbox.add()
    with caplog.at_level(logging.ERROR, logger="line_dispatcher"):
        assert dispatch(outbox, line_settings) is True
    assert f"message_id={mid}" in caplog.text and "failed" in caplog.text
    row = outbox.row(mid)
    assert (row["status"], row["attempts"], row["next_attempt_at"]) == ("failed", 1, None)
    assert row["last_error"] == f'HTTP {status}: {{"message": "The request body has 1 error(s)"}}'
    assert dispatch(outbox, line_settings) is False and len(fake_push.calls) == 1


@pytest.mark.req("A-21")
def test_backoff_doubles_and_is_capped():
    assert [backoff_sec(n) for n in (1, 2, 3, 4)] == [30, 60, 120, 240]
    assert backoff_sec(50) == BACKOFF_MAX_SEC


# ---------------------------------------------------------------- 보낼 곳 없음 · 못 만드는 메시지

@pytest.mark.req("A-21")
@pytest.mark.parametrize("employee_id", [2, None])  # 2 = LINE 미연결 직원, None = 점장 앞
def test_no_line_user_id_ends_as_no_recipient_without_calling_line(outbox, line_settings, fake_push, employee_id):
    mid = outbox.add(employee_id=employee_id)
    assert dispatch(outbox, line_settings) is True
    row = outbox.row(mid)
    assert (row["status"], row["attempts"], row["next_attempt_at"], row["sent_at"]) == ("no_recipient", 0, None, None)
    assert fake_push.calls == []


@pytest.mark.req("A-21")
def test_body_that_cannot_become_a_message_fails_and_does_not_block_the_queue(outbox, line_settings, fake_push):
    broken, ok = outbox.add(kind="shift_confirmed", body={"text": "확정"}), outbox.add()  # work_days 없음
    assert dispatch(outbox, line_settings) and dispatch(outbox, line_settings)
    assert outbox.row(broken)["status"] == "failed" and "KeyError" in outbox.row(broken)["last_error"]
    assert outbox.row(ok)["status"] == "sent" and len(fake_push.calls) == 1


@pytest.mark.req("A-21")
@pytest.mark.parametrize("error", [UnicodeEncodeError("ascii", "Bearer 토큰", 7, 9, "ordinal not in range(128)"),
                                   ValueError("Bearer SECRET-IN-MESSAGE")])
def test_any_exception_while_sending_fails_that_row_only(outbox, line_settings, fake_push, error):
    """독 행: 한 행에서 예외가 나도 발송기는 죽지 않고, 그 행은 failed 로 끝나고, 다음 행이 나간다."""
    fake_push.script = [error, 200]
    poison, ok = outbox.add(), outbox.add()
    assert dispatch(outbox, line_settings) and dispatch(outbox, line_settings)
    row = outbox.row(poison)
    assert (row["status"], row["attempts"]) == ("failed", 1)
    assert row["last_error"] == f"보낼 수 없음: {type(error).__name__}"  # 형 이름만 — 예외 문장은 싣지 않는다
    assert outbox.row(ok)["status"] == "sent"
    assert dispatch(outbox, line_settings) is False and len(fake_push.calls) == 2  # 독 행을 다시 잡지 않는다


@pytest.mark.req("A-21")
def test_non_ascii_token_really_makes_httpx_raise_and_ends_as_failed(outbox, line_settings, monkeypatch):
    """가짜 push 가 아니라 진짜 line_api.push 로: 헤더에 못 싣는 토큰 값은 httpx.HTTPError 가 아닌 예외를 낸다."""
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "토큰-값")
    from app.config import get_settings
    mid = outbox.add()
    assert dispatch(outbox, get_settings()) is True
    row = outbox.row(mid)
    assert (row["status"], row["last_error"]) == ("failed", "보낼 수 없음: UnicodeEncodeError")


# ---------------------------------------------------------------- 시크릿을 남기지 않는다

@pytest.mark.req("A-21")
def test_token_and_secret_never_reach_last_error_or_logs(outbox, line_settings, fake_push, caplog):
    echo = f'{{"message": "bad Authorization: Bearer {ACCESS_TOKEN} / {CHANNEL_SECRET}"}}'  # 응답이 되비쳐도
    fake_push.script = [(500, echo), (400, echo), httpx.ConnectError(f"Bearer {ACCESS_TOKEN}")]
    a, b = outbox.add(), outbox.add()
    with caplog.at_level(logging.DEBUG):
        assert dispatch(outbox, line_settings) and dispatch(outbox, line_settings)
        outbox.make_due()
        assert dispatch(outbox, line_settings)
    recorded = " ".join(r["last_error"] for r in (outbox.row(a), outbox.row(b)))
    assert outbox.row(a)["attempts"] == 2 and "HTTP 400" in recorded  # 세 번 다 기록 · 로그까지 갔다
    for secret in (ACCESS_TOKEN, CHANNEL_SECRET):
        assert secret not in recorded and secret not in caplog.text
    assert "***" in recorded


# ---------------------------------------------------------------- 죽어도 · 둘이 돌아도

@pytest.mark.req("A-21")
def test_crash_while_sending_leaves_the_row_pending(outbox, line_settings, fake_push):
    fake_push.script = [ProcessKilled(), 200]
    mid = outbox.add()
    with pytest.raises(ProcessKilled):
        dispatch(outbox, line_settings)  # 연결이 닫히며 트랜잭션이 취소된다
    row = outbox.row(mid)
    assert (row["status"], row["attempts"]) == ("pending", 0)
    assert dispatch(outbox, line_settings) is True and outbox.row(mid)["status"] == "sent"
    assert fake_push.calls[0]["retry_key"] == fake_push.calls[1]["retry_key"]  # 다시 갈 때도 같은 키


@pytest.mark.req("A-21")
def test_two_dispatchers_send_one_row_only_once(outbox, line_settings, monkeypatch):
    """실제 연결 2개: 첫 발송기가 행을 잡고 보내는 동안, 둘째 발송기는 그 행을 건너뛴다 (SKIP LOCKED)."""
    from app import line_api

    sending, release, calls = threading.Event(), threading.Event(), []

    def slow_push(access_token, *, to, messages, retry_key):
        calls.append(retry_key)
        sending.set()
        assert release.wait(10)
        return line_api.PushResponse(200, "{}")

    monkeypatch.setattr(line_api, "push", slow_push)
    mid = outbox.add()
    results: list[bool] = []
    first = threading.Thread(target=lambda: results.append(dispatch(outbox, line_settings)))
    first.start()
    try:
        assert sending.wait(10)
        assert dispatch(outbox, line_settings) is False  # 둘째: 잡을 행이 없다
    finally:
        release.set()
        first.join(10)
    assert results == [True] and len(calls) == 1 and outbox.row(mid)["status"] == "sent"
    assert dispatch(outbox, line_settings) is False and len(calls) == 1


# ---------------------------------------------------------------- 실행 루프

@pytest.mark.req("A-21")
def test_run_loop_reconnects_after_the_db_connection_is_lost(outbox, stub_settings, monkeypatch, caplog):
    """발송기의 DB 연결을 서버 쪽에서 끊는다(시험 DB 의 이 세션 전용 DB 안에서만). 다시 연결해 다음 행을 보낸다."""
    monkeypatch.setattr(line_dispatcher, "IDLE_SLEEP_SEC", 0.05)
    stop = threading.Event()
    loop = threading.Thread(target=line_dispatcher.run, args=(stop,))

    def wait_sent(message_id: int) -> None:
        deadline = time.monotonic() + 10
        while outbox.row(message_id)["status"] != "sent" and time.monotonic() < deadline:
            time.sleep(0.05)

    with caplog.at_level(logging.ERROR, logger="line_dispatcher"):
        loop.start()
        try:
            before = outbox.add()
            wait_sent(before)
            killed = outbox.sql("""SELECT pg_terminate_backend(pid) AS ok FROM pg_stat_activity
                                   WHERE datname = current_database() AND pid <> pg_backend_pid()""")
            after = outbox.add()
            wait_sent(after)
        finally:
            stop.set()
            loop.join(10)
    assert killed and all(k["ok"] for k in killed)  # 끊을 연결이 실제로 있었다
    assert outbox.row(before)["status"] == "sent" and outbox.row(after)["status"] == "sent"
    assert "DB 오류 → 잠시 뒤 다시 연결" in caplog.text and "sqlstate=57P01" in caplog.text  # admin_shutdown

@pytest.mark.req("A-21")
def test_run_loop_sends_new_rows_until_stopped(outbox, stub_settings, monkeypatch):
    monkeypatch.setattr(line_dispatcher, "IDLE_SLEEP_SEC", 0.05)
    stop = threading.Event()
    loop = threading.Thread(target=line_dispatcher.run, args=(stop,))
    loop.start()
    try:
        mid = outbox.add()  # 루프가 돈 뒤에 들어온 행
        deadline = time.monotonic() + 10
        while outbox.row(mid)["status"] != "sent" and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        stop.set()
        loop.join(10)
    assert outbox.row(mid)["status"] == "sent" and not loop.is_alive()


@pytest.mark.req("A-21")
def test_process_exits_cleanly_on_sigterm(outbox):
    """python -m app.line_dispatcher 를 실제로 띄운다 (stub 모드 · 시험 DB)."""
    mid = outbox.add()
    env = {k: v for k, v in os.environ.items() if not k.startswith("LINE_")} | {"DATABASE_URL": outbox.url}
    proc = subprocess.Popen([sys.executable, "-m", "app.line_dispatcher"], env=env, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 20
        while outbox.row(mid)["status"] != "sent" and time.monotonic() < deadline:
            time.sleep(0.1)
        proc.send_signal(signal.SIGTERM)
        _, stderr = proc.communicate(timeout=10)
    finally:
        proc.kill()
    assert outbox.row(mid)["status"] == "sent"
    assert proc.returncode == 0 and "LINE 발송기 종료" in stderr and "Traceback" not in stderr
