"""LINE 스텁 (A-17, M-5, D6). 실제로 보내지 않고 line_outbox 에 기록한다. 메인 세션 소유.

dedupe_key 가 같으면 두 번째부터는 기록하지 않고 False (F-9 「통지 1회」, 명세 1-2 notified).
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger("line_stub")


def send(conn, *, store_id: int, employee_id: int | None, kind: str, dedupe_key: str, body: dict) -> bool:
    row = conn.execute(
        """INSERT INTO line_outbox (store_id, employee_id, kind, dedupe_key, body)
           VALUES (%s, %s, %s, %s, %s::jsonb)
           ON CONFLICT (dedupe_key) DO NOTHING RETURNING message_id""",
        (store_id, employee_id, kind, dedupe_key, json.dumps(body, ensure_ascii=False, default=str)),
    ).fetchone()
    sent = row is not None
    log.info("LINE stub %s kind=%s employee=%s key=%s body=%s", "SEND" if sent else "SKIP(dup)", kind, employee_id,
             dedupe_key, body)
    return sent
