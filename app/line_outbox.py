"""LINE 발송함에 적기 (A-17, A-21, M-5, D6). 여기서는 보내지 않는다 — line_outbox 에 「대기(pending)」 행을 넣기만 한다.

부르는 쪽의 트랜잭션 안에서 돈다 → 확정 · 질문 기록과 통지가 같이 커밋되거나 같이 취소된다.
실제 발송은 커밋 뒤 발송기(app/line_dispatcher.py)가 한다. LINE 장애가 확정을 막지 않는다 (결정 2026-11-19 승혁).

dedupe_key 가 같으면 두 번째부터는 기록하지 않고 False (F-9 「통지 1회」, 명세 1-2 notified).
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger("line_outbox")


def enqueue(conn, *, store_id: int, employee_id: int | None, kind: str, dedupe_key: str, body: dict) -> bool:
    """새로 넣었으면 True, 같은 dedupe_key 가 이미 있으면 False."""
    row = conn.execute(
        """INSERT INTO line_outbox (store_id, employee_id, kind, dedupe_key, body)
           VALUES (%s, %s, %s, %s, %s::jsonb)
           ON CONFLICT (dedupe_key) DO NOTHING RETURNING message_id""",
        (store_id, employee_id, kind, dedupe_key, json.dumps(body, ensure_ascii=False, default=str)),
    ).fetchone()
    queued = row is not None
    log.info("LINE outbox %s kind=%s employee=%s key=%s", "QUEUED" if queued else "SKIP(dup)", kind, employee_id,
             dedupe_key)
    return queued
