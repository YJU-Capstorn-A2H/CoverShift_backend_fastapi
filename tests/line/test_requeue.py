"""실패 행 재투입 스크립트 (A-21): ops/requeue_line.py."""
from __future__ import annotations

import io

import pytest

from app.line_dispatcher import dispatch_one
from ops import requeue_line


def run(*argv: str) -> str:
    out = io.StringIO()
    assert requeue_line.main(list(argv), stdout=out) == 0
    return out.getvalue()


def fail(outbox, message_id: int) -> None:
    outbox.sql("""UPDATE line_outbox SET status = 'failed', attempts = 3, last_error = 'HTTP 500: down',
                                         next_attempt_at = NULL WHERE message_id = %s""", (message_id,))


@pytest.mark.req("A-21")
def test_default_only_lists_and_changes_nothing(outbox):
    failed, pending = outbox.add(), outbox.add()
    fail(outbox, failed)
    before = outbox.row(failed)
    text = run()
    assert f"message_id={failed} " in text and f"message_id={pending} " not in text  # failed 행만
    assert "HTTP 500: down" in text and "failed 1건 대상입니다 (바꾸지 않았습니다. 바꾸려면 --apply)" in text
    assert outbox.row(failed) == before | {"due_in": None}


@pytest.mark.req("A-21")
def test_apply_requeues_failed_rows_and_the_dispatcher_sends_them(outbox, stub_settings):
    a, b = outbox.add(), outbox.add()
    fail(outbox, a), fail(outbox, b)
    outbox.age(a, "7 hours")
    key = outbox.row(a)["retry_key"]
    assert "failed 1건 대기로 돌렸습니다" in run("--message-id", str(a), "--apply")
    row = outbox.row(a)
    assert (row["status"], row["attempts"], row["retry_key"]) == ("pending", 3, key)  # 24시간 안: 같은 재시도 키
    assert row["queued_at"] > row["created_at"]  # 재시도 기한은 지금부터 다시 센다. 처음 적은 시각은 그대로
    assert outbox.row(b)["status"] == "failed"  # 고르지 않은 행은 그대로
    with outbox.connect() as conn:
        assert dispatch_one(conn, stub_settings) is True
    assert outbox.row(a)["status"] == "sent"


@pytest.mark.req("A-21")
def test_old_row_gets_a_new_retry_key_with_a_duplicate_warning(outbox):
    young, old = outbox.add(), outbox.add()
    fail(outbox, young), fail(outbox, old)
    outbox.age(young, "17 hours")  # 남은 키 유효 시간(7시간) ≥ 재시도 기한(6시간) → 키 그대로
    outbox.age(old, "19 hours")    # 남은 키 유효 시간(5시간) < 재시도 기한 → 새 키
    keys = {m: outbox.row(m)["retry_key"] for m in (young, old)}
    text = run("--apply")
    assert outbox.row(young)["retry_key"] == keys[young] and outbox.row(old)["retry_key"] != keys[old]
    assert f"경고: message_id={old} " in text and "한 번 더 간다" in text
    assert f"경고: message_id={young} " not in text


@pytest.mark.req("A-21")
def test_no_recipient_rows_are_requeued_only_when_asked(outbox, stub_settings):
    """나중에 LINE 을 연결한 직원 앞 메시지: --status no_recipient 로 되돌리면 이번에는 나간다."""
    mid = outbox.add(employee_id=2)
    with outbox.connect() as conn:
        assert dispatch_one(conn, stub_settings) is True
    assert outbox.row(mid)["status"] == "no_recipient"
    assert "failed 0건" in run("--apply") and outbox.row(mid)["status"] == "no_recipient"  # 기본(failed)은 건드리지 않는다
    outbox.sql("UPDATE employees SET line_user_id = 'U-2' WHERE employee_id = 2")
    text = run("--status", "no_recipient", "--apply")
    assert "no_recipient 1건 대기로 돌렸습니다" in text and "경고" not in text
    with outbox.connect() as conn:
        assert dispatch_one(conn, stub_settings) is True
    assert outbox.row(mid)["status"] == "sent"


@pytest.mark.req("A-21")
def test_sent_rows_cannot_be_requeued(outbox, stub_settings):
    mid = outbox.add()
    with outbox.connect() as conn:
        assert dispatch_one(conn, stub_settings) is True
    assert "failed 0건" in run("--message-id", str(mid), "--apply")
    assert outbox.row(mid)["status"] == "sent"
    with pytest.raises(SystemExit):
        requeue_line.main(["--status", "sent", "--apply"], stdout=io.StringIO())
