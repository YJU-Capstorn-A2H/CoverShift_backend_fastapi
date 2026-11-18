"""편성 기간 (A-9, F-10) · 기간별 제출 현황 · 지시 미리보기용 가벼운 PeriodContext."""
from __future__ import annotations

from datetime import date

import psycopg
from psycopg import sql

from app.domain import Employee, Period, PeriodContext, TimeRule

PERIOD_COLS = """period_id, store_id, target_month, submission_deadline, designated_off_days, off_request_limit,
                 work_request_limit, thread_id"""
PERIOD_FIELDS = ("submission_deadline", "designated_off_days", "off_request_limit", "work_request_limit")


def month_range(target_month: date) -> tuple[date, date]:
    """[그 달 1일, 다음 달 1일)."""
    start = target_month.replace(day=1)
    end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    return start, end


def list_periods(conn: psycopg.Connection, store_id: int) -> list[dict]:
    return conn.execute(f"SELECT {PERIOD_COLS} FROM shift_periods WHERE store_id = %s ORDER BY target_month DESC",
                        (store_id,)).fetchall()


def get_period(conn: psycopg.Connection, store_id: int, period_id: int, *, for_update: bool = False) -> dict | None:
    q = f"SELECT {PERIOD_COLS} FROM shift_periods WHERE store_id = %s AND period_id = %s"
    return conn.execute(q + (" FOR UPDATE" if for_update else ""), (store_id, period_id)).fetchone()


def create_period(conn: psycopg.Connection, store_id: int, data: dict) -> dict:
    """UNIQUE(store_id, target_month) → UniqueViolation → 409 PERIOD_EXISTS (V9_ko 192). 1일이 아니면 CHECK → 422."""
    return conn.execute(
        f"""INSERT INTO shift_periods (store_id, target_month, submission_deadline, designated_off_days,
                                       off_request_limit, work_request_limit)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING {PERIOD_COLS}""",
        (store_id, data["target_month"], data["submission_deadline"], data["designated_off_days"],
         data["off_request_limit"], data["work_request_limit"])).fetchone()


def update_period(conn: psycopg.Connection, store_id: int, period_id: int, fields: dict) -> dict | None:
    """마감일 · 상한만 (V9_ko 194). 마감일을 바꾸면 제출 링크의 유효 기간도 따라간다(링크 자체에는 기한 열이 없다)."""
    if not fields:
        return get_period(conn, store_id, period_id)
    sets = sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(k)) for k in fields)
    q = sql.SQL("UPDATE shift_periods SET {} WHERE store_id = %s AND period_id = %s RETURNING " + PERIOD_COLS).format(
        sets)
    return conn.execute(q, (*fields.values(), store_id, period_id)).fetchone()


def set_thread_id(conn: psycopg.Connection, period_id: int, thread_id: str) -> None:
    """F-10: 편성 기간 1개 = thread 1개 (V9_ko 98, 275). thread_id 열은 UNIQUE."""
    conn.execute("UPDATE shift_periods SET thread_id = %s WHERE period_id = %s", (thread_id, period_id))


def confirmed_draft_id(conn: psycopg.Connection, period_id: int) -> int | None:
    """확정안 번호 (기간당 최대 1개, one_confirmed_draft_per_period). 화면이 확정표를 다시 열 때 쓴다 (web 계약 변경 요청 1)."""
    r = conn.execute("SELECT draft_id FROM shift_drafts WHERE period_id = %s AND status = 'confirmed'",
                     (period_id,)).fetchone()
    return None if r is None else (r["draft_id"] if isinstance(r, dict) else r[0])


def has_confirmed_draft(conn: psycopg.Connection, period_id: int) -> bool:
    return conn.execute("SELECT 1 FROM shift_drafts WHERE period_id = %s AND status = 'confirmed'",
                        (period_id,)).fetchone() is not None


def submissions(conn: psycopg.Connection, store_id: int, period: dict) -> list[dict]:
    """GET /api/shift-periods/{id}/submissions: 점포 직원 전원 + 그 달 희망. 미제출 직원은 items=[] (G19)."""
    start, end = month_range(period["target_month"])
    emps = conn.execute("SELECT employee_id, name, is_regular FROM employees WHERE store_id = %s ORDER BY employee_id",
                        (store_id,)).fetchall()
    rows = conn.execute(
        """SELECT a.employee_id, a.work_date, a.kind, a.start_time, a.end_time
           FROM availability_submissions a JOIN employees e USING (employee_id)
           WHERE e.store_id = %s AND a.work_date >= %s AND a.work_date < %s
           ORDER BY a.employee_id, a.work_date""", (store_id, start, end)).fetchall()
    by_emp: dict[int, list[dict]] = {}
    for r in rows:
        by_emp.setdefault(r.pop("employee_id"), []).append(r)
    return [{**e, "items": by_emp.get(e["employee_id"], [])} for e in emps]


def light_context(conn: psycopg.Connection, store_id: int, period: dict) -> PeriodContext:
    """점장 지시 → 틀 규칙 변환(M-2)에 넘기는 문맥. 기간 + 직원(이름 없음, F-6) + 시간대 규칙. 희망은 넣지 않는다."""
    emps = conn.execute(
        """SELECT employee_id, is_regular, has_qualification, closing_ok, weekly_days, fixed_weekdays
           FROM employees WHERE store_id = %s ORDER BY employee_id""", (store_id,)).fetchall()
    rules = conn.execute(
        """SELECT rule_id, weekday, start_time, end_time, min_headcount, min_qualified
           FROM store_time_rules WHERE store_id = %s ORDER BY weekday, start_time""", (store_id,)).fetchall()
    return PeriodContext(
        period=Period(period_id=period["period_id"], store_id=store_id, target_month=period["target_month"],
                      designated_off_days=period["designated_off_days"],
                      off_request_limit=period["off_request_limit"], work_request_limit=period["work_request_limit"]),
        employees=tuple(Employee(e["employee_id"], e["is_regular"], e["has_qualification"], e["closing_ok"],
                                 e["weekly_days"], tuple(e["fixed_weekdays"]) if e["fixed_weekdays"] else None)
                        for e in emps),
        availability=(),
        time_rules=tuple(TimeRule(r["rule_id"], r["weekday"], r["start_time"], r["end_time"], r["min_headcount"],
                                  r["min_qualified"]) for r in rules),
    )
