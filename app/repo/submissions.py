"""제출 링크 · 직원 희망 (A-10, A-11, A-12, W-7)."""
from __future__ import annotations

import secrets
from datetime import date

import psycopg

from .periods import month_range


def ensure_links(conn: psycopg.Connection, store_id: int, period_id: int) -> list[dict]:
    """직원마다 (직원, 월) 링크 1개. 이미 있으면 같은 토큰을 그대로 쓴다 (V9_ko 161, UNIQUE(employee_id, period_id))."""
    emps = conn.execute("SELECT employee_id, line_user_id FROM employees WHERE store_id = %s ORDER BY employee_id",
                        (store_id,)).fetchall()
    for e in emps:
        conn.execute(
            """INSERT INTO submission_links (token, employee_id, period_id) VALUES (%s, %s, %s)
               ON CONFLICT (employee_id, period_id) DO NOTHING""",
            (secrets.token_urlsafe(24), e["employee_id"], period_id))
    return conn.execute(
        """SELECT l.employee_id, l.token, (e.line_user_id IS NOT NULL) AS line_linked
           FROM submission_links l JOIN employees e USING (employee_id)
           WHERE l.period_id = %s AND e.store_id = %s ORDER BY l.employee_id""", (period_id, store_id)).fetchall()


def by_token(conn: psycopg.Connection, token: str, *, for_update: bool = False) -> dict | None:
    """토큰 → 직원 · 기간. closed = 마감이 지났는가 (DB 시계로 판정, V9_ko 163 「submission_deadline 이 지나면 410」).

    for_update: 같은 (직원, 월) 의 PUT 이 겹치면 줄을 세운다 → 상한 세기가 두 요청 사이에서 어긋나지 않는다.
    """
    q = """SELECT l.employee_id, l.period_id, e.name AS employee_name, e.is_regular, e.store_id,
                  p.target_month, p.submission_deadline, p.off_request_limit, p.work_request_limit,
                  (now() > p.submission_deadline) AS closed
           FROM submission_links l JOIN employees e USING (employee_id) JOIN shift_periods p USING (period_id)
           WHERE l.token = %s"""
    return conn.execute(q + (" FOR UPDATE OF l" if for_update else ""), (token,)).fetchone()


def items(conn: psycopg.Connection, employee_id: int, target_month: date) -> list[dict]:
    start, end = month_range(target_month)
    return conn.execute(
        """SELECT work_date, kind, start_time, end_time FROM availability_submissions
           WHERE employee_id = %s AND work_date >= %s AND work_date < %s ORDER BY work_date""",
        (employee_id, start, end)).fetchall()


def upsert(conn: psycopg.Connection, employee_id: int, item: dict) -> None:
    """1명 1일 1행. 같은 날 재입력은 덮어쓰기 (V9_ko 165, PK(employee_id, work_date))."""
    conn.execute(
        """INSERT INTO availability_submissions (employee_id, work_date, kind, start_time, end_time)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (employee_id, work_date) DO UPDATE
             SET kind = EXCLUDED.kind, start_time = EXCLUDED.start_time, end_time = EXCLUDED.end_time""",
        (employee_id, item["work_date"], item["kind"], item.get("start_time"), item.get("end_time")))


def count_kinds(conn: psycopg.Connection, employee_id: int, target_month: date) -> dict[str, int]:
    start, end = month_range(target_month)
    rows = conn.execute(
        """SELECT kind, count(*) AS n FROM availability_submissions
           WHERE employee_id = %s AND work_date >= %s AND work_date < %s GROUP BY kind""",
        (employee_id, start, end)).fetchall()
    return {r["kind"]: r["n"] for r in rows}


def delete(conn: psycopg.Connection, employee_id: int, work_date: date) -> None:
    conn.execute("DELETE FROM availability_submissions WHERE employee_id = %s AND work_date = %s",
                 (employee_id, work_date))
