"""LINE 연결 토큰 · 발송함 조회 · 직원 연락 기록 (A-13, A-17, M-5, G14, G16)."""
from __future__ import annotations

import secrets
from datetime import datetime

import psycopg

# (가정 신규) 연결 링크의 유효 기간. 명세는 「link_token + 만료」(V9_ko 157)만 있고 길이가 없다.
# 초대 코드(1일)보다 길게 둔 이유: 직원이 링크를 받고 바로 열지 않을 수 있다. 짧게 바꿔도 다른 곳은 안 바뀐다
LINK_TOKEN_TTL_SQL = "interval '7 days'"


def issue_link_token(conn: psycopg.Connection, store_id: int, employee_id: int) -> tuple[str, datetime] | None:
    """새 토큰을 넣는다(이전 토큰은 덮어써서 무효). 이미 연결된 직원 · 남의 점포 직원은 None 대신 호출 쪽이 먼저 거른다."""
    token = secrets.token_urlsafe(24)
    row = conn.execute(
        f"""UPDATE employees SET link_token = %s, link_token_expires_at = now() + {LINK_TOKEN_TTL_SQL}
            WHERE store_id = %s AND employee_id = %s RETURNING link_token_expires_at""",
        (token, store_id, employee_id)).fetchone()
    return None if row is None else (token, row["link_token_expires_at"])


def find_by_link_token(conn: psycopg.Connection, token: str) -> dict | None:
    """유효(기한 안)한 토큰만. FOR UPDATE: 같은 토큰으로 두 번 동시에 연결해도 한 번만."""
    return conn.execute(
        """SELECT employee_id, store_id, line_user_id FROM employees
           WHERE link_token = %s AND link_token_expires_at > now() FOR UPDATE""", (token,)).fetchone()


def link(conn: psycopg.Connection, employee_id: int, line_user_id: str) -> None:
    """line_user_id 저장 + 토큰 비움 (V9_ko 157-159). 다른 직원이 이미 쓰는 line_user_id 면 UNIQUE → 409."""
    conn.execute(
        """UPDATE employees SET line_user_id = %s, link_token = NULL, link_token_expires_at = NULL
           WHERE employee_id = %s""", (line_user_id, employee_id))


def outbox(conn: psycopg.Connection, store_id: int, employee_id: int | None) -> list[dict]:
    """발송함 (A-17, W-8). 최신 순. employee_id 가 NULL 인 행은 점장 앞 (db/002).
    status · attempts · sent_at · last_error = 발송기가 적은 발송 결과 (A-21)."""
    q = """SELECT o.message_id, o.employee_id, e.name AS employee_name, e.line_user_id, o.kind, o.body, o.created_at,
                  o.status, o.attempts, o.sent_at, o.last_error
           FROM line_outbox o LEFT JOIN employees e ON e.employee_id = o.employee_id
           WHERE o.store_id = %s"""
    args: list = [store_id]
    if employee_id is not None:
        q += " AND o.employee_id = %s"
        args.append(employee_id)
    return conn.execute(q + " ORDER BY o.created_at DESC, o.message_id DESC", args).fetchall()


def contact_for_reply(conn: psycopg.Connection, contact_id: int) -> dict | None:
    """답장 버튼(G16): 연락 행 + 그 직원의 line_user_id + 기간의 thread_id.

    행 잠금(FOR UPDATE)은 걸지 않는다: resume 뒤 그래프가 같은 contact_requests 행을 고칠 수 있어 서로 기다릴 수 있다.
    겹침은 입구 잠금(app/api/deps.entrance_lock)과 러너 Busy 가 막는다.
    """
    return conn.execute(
        """SELECT c.contact_id, c.period_id, c.employee_id, c.status, e.line_user_id, p.thread_id
           FROM contact_requests c JOIN employees e ON e.employee_id = c.employee_id
                                   JOIN shift_periods p ON p.period_id = c.period_id
           WHERE c.contact_id = %s""", (contact_id,)).fetchone()
