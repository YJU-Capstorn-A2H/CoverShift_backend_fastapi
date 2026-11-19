"""운영 스크립트: 끝난 LINE 발송함 행을 다시 「대기」로 돌린다 (A-21).

발송기는 기한(6시간) 안에 못 보낸 행을 failed 로 끝내고 더 보지 않는다. 원인(LINE 장애 · 토큰 · 월 한도)을 고친 뒤 이 스크립트로 되돌린다.

실행: uv run python -m ops.requeue_line                       ← 목록만 보여 준다. 아무것도 바꾸지 않는다
      uv run python -m ops.requeue_line --apply               ← 목록의 행을 전부 대기로 돌린다
      uv run python -m ops.requeue_line --message-id 12 --message-id 15 --apply
      uv run python -m ops.requeue_line --status no_recipient --apply   ← 나중에 LINE 을 연결한 직원 앞 메시지

- 돌린 행은 queued_at 이 지금으로 바뀌어 발송기가 다시 기한만큼 시도한다. 본문은 그대로다.
- 재시도 키: LINE 은 같은 키의 요청을 24시간 동안만 걸러 준다. 행이 오래돼 「남은 유효 시간 < 재시도 기한」이면 키를 새로 만든다
  (옛 키는 재시도 도중에 효력이 끝난다). 새 키로 보내면 LINE 이 앞의 요청과 같은 것인지 알 수 없다 →
  failed 행이 사실은 전달됐던 경우(응답만 못 받음) 같은 메시지가 한 번 더 간다. 그 행에는 경고를 찍는다.
- retry_key · queued_at 은 앱 롤(covershift_app)이 고칠 수 없는 열이다 (db/002) → 표 소유자 권한의 DATABASE_URL 로 돌린다.
"""
from __future__ import annotations

import argparse
import sys

from app.db import connect
from app.line_dispatcher import RETRY_WINDOW_SEC

RETRY_KEY_VALID_SEC = 24 * 3600  # LINE 문서 · 재시도 키: 유효 24시간
# 이보다 오래된 행은 옛 키로 재시도 기한을 다 채울 수 없다. 키를 처음 쓴 시각은 적어 두지 않으므로 created_at 으로 잰다(더 이른 쪽)
KEEP_KEY_MAX_AGE_SEC = RETRY_KEY_VALID_SEC - RETRY_WINDOW_SEC
REQUEUEABLE = ("failed", "no_recipient")

LIST_SQL = """
    SELECT message_id, employee_id, kind, attempts, last_error, created_at,
           created_at < now() - make_interval(secs => %(keep_key_max_age)s) AS needs_new_key
    FROM line_outbox
    WHERE status = %(status)s AND (%(ids)s::bigint[] IS NULL OR message_id = ANY(%(ids)s))
    ORDER BY message_id"""

REQUEUE_SQL = """
    UPDATE line_outbox
    SET status = 'pending', queued_at = now(), next_attempt_at = now(),
        retry_key = CASE WHEN message_id = ANY(%(new_key_ids)s) THEN gen_random_uuid() ELSE retry_key END
    WHERE status = %(status)s AND message_id = ANY(%(ids)s)"""


def main(argv: list[str] | None = None, stdout=None) -> int:
    stdout = stdout or sys.stdout
    ap = argparse.ArgumentParser(prog="ops.requeue_line", description="끝난 LINE 발송함 행을 다시 대기로")
    ap.add_argument("--status", choices=REQUEUEABLE, default="failed", help="되돌릴 행의 상태 (기본 failed)")
    ap.add_argument("--message-id", type=int, action="append", help="이 행만 (여러 번 줄 수 있다). 없으면 그 상태의 전부")
    ap.add_argument("--apply", action="store_true", help="실제로 바꾼다. 없으면 목록만 보여 준다")
    a = ap.parse_args(argv)

    with connect() as conn:
        rows = conn.execute(LIST_SQL, {"status": a.status, "ids": a.message_id,
                                       "keep_key_max_age": KEEP_KEY_MAX_AGE_SEC}).fetchall()
        for row in rows:
            print(_line(row), file=stdout)
            if row["needs_new_key"] and a.status == "failed":
                print(f"  경고: message_id={row['message_id']} 은 재시도 키를 새로 만든다 — 이미 전달된 메시지였다면 한 번 더 간다",
                      file=stdout)
        if a.apply and rows:
            conn.execute(REQUEUE_SQL, {"status": a.status, "ids": [row["message_id"] for row in rows],
                                       "new_key_ids": [row["message_id"] for row in rows if row["needs_new_key"]]})
    done = "대기로 돌렸습니다" if a.apply else "대상입니다 (바꾸지 않았습니다. 바꾸려면 --apply)"
    print(f"{a.status} {len(rows)}건 {done}", file=stdout)
    return 0


def _line(row: dict) -> str:
    key = "새로 만듦" if row["needs_new_key"] else "그대로"
    return (f"message_id={row['message_id']} kind={row['kind']} employee_id={row['employee_id']} "
            f"created_at={row['created_at'].isoformat()} attempts={row['attempts']} retry_key={key} "
            f"last_error={row['last_error']}")


if __name__ == "__main__":
    raise SystemExit(main())
