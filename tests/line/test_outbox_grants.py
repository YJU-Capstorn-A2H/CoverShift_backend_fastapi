"""line_outbox 의 제약과 covershift_app 권한 (A-21, db/002). 시험 DB 에서 SET ROLE 로 앱 롤이 되어 본다."""
from __future__ import annotations

import psycopg
import pytest

from app.line_dispatcher import dispatch_one


@pytest.fixture
def as_app(outbox):
    """covershift_app 롤로 바꾼 연결 (접속 계정은 시험 DB 의 mvp)."""
    with outbox.connect() as conn:
        conn.execute("SET ROLE covershift_app")
        conn.commit()  # 롤을 세션에 남기고 트랜잭션을 닫는다 (열어 두면 now() 가 여기서 멈춘다)
        yield conn


@pytest.mark.req("A-21")
def test_app_role_can_enqueue_and_update_only_delivery_columns(outbox, as_app):
    mid = outbox.add()
    as_app.execute("""UPDATE line_outbox SET status = 'failed', attempts = 1, last_error = 'HTTP 400',
                                             next_attempt_at = NULL, sent_at = NULL WHERE message_id = %s""", (mid,))
    as_app.commit()
    assert outbox.row(mid)["status"] == "failed"
    # 깨뜨리는 쪽: 본문 · 수신자 · 중복 방지 키 · 재시도 키는 못 고치고, 지우지도 못한다
    for forbidden in ("UPDATE line_outbox SET body = '{}'::jsonb", "UPDATE line_outbox SET dedupe_key = 'x'",
                      "UPDATE line_outbox SET employee_id = 2", "UPDATE line_outbox SET retry_key = gen_random_uuid()",
                      "UPDATE line_outbox SET kind = 'manager_notice'", "UPDATE line_outbox SET queued_at = now()",
                      "DELETE FROM line_outbox",
                      "TRUNCATE line_outbox"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            as_app.execute(forbidden)
        as_app.rollback()
    assert outbox.row(mid)["body"] == {"text": "안내입니다"}


@pytest.mark.req("A-21")
def test_dispatcher_works_with_app_role_privileges(outbox, as_app, stub_settings):
    """발송기의 조회(FOR UPDATE SKIP LOCKED) · 결과 기록이 열 단위 권한만으로 돈다."""
    sent, unlinked = outbox.add(employee_id=1), outbox.add(employee_id=2)
    assert dispatch_one(as_app, stub_settings) and dispatch_one(as_app, stub_settings)
    assert (outbox.row(sent)["status"], outbox.row(unlinked)["status"]) == ("sent", "no_recipient")


@pytest.mark.req("A-21")
def test_new_row_starts_pending_and_status_cannot_contradict_other_columns(outbox):
    mid = outbox.add()
    row = outbox.row(mid)
    assert (row["status"], row["attempts"], row["sent_at"], row["last_error"]) == ("pending", 0, None, None)
    assert row["next_attempt_at"] is not None and row["retry_key"] is not None
    for contradiction in ("status = 'sent'",                                   # 보냈는데 보낸 시각이 없다
                          "sent_at = now()",                                   # 대기인데 보낸 시각이 있다
                          "next_attempt_at = NULL",                            # 대기인데 다음 시도 시각이 없다
                          "status = 'no_recipient'",                           # 끝났는데 다음 시도 시각이 남았다
                          "status = 'failed', next_attempt_at = NULL",         # 실패인데 오류가 없다
                          "status = 'done', next_attempt_at = NULL",           # 모르는 상태
                          "attempts = -1"):
        with pytest.raises(psycopg.errors.CheckViolation):
            outbox.sql(f"UPDATE line_outbox SET {contradiction} WHERE message_id = %s", (mid,))
    outbox.sql("UPDATE line_outbox SET status = 'sent', sent_at = now(), next_attempt_at = NULL")  # 지키는 쪽
