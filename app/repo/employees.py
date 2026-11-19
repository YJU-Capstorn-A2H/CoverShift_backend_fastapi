"""직원 · 업무 (A-7, A-8). 이름(name)은 관리자 화면용으로만 돌려준다 — LLM 쪽 문맥(PeriodContext)에는 넣지 않는다 (F-6)."""
from __future__ import annotations

import psycopg
from psycopg import sql

EMP_COLS = """employee_id, name, is_regular, has_qualification, closing_ok, weekly_days, fixed_weekdays,
              (line_user_id IS NOT NULL) AS line_linked"""
EMP_FIELDS = ("name", "is_regular", "has_qualification", "closing_ok", "weekly_days", "fixed_weekdays")
TASK_COLS = "task_id, name, sort_order"


def _fix(row: dict | None) -> dict | None:
    if row is not None and row.get("fixed_weekdays") is not None:
        row["fixed_weekdays"] = list(row["fixed_weekdays"])
    return row


def list_employees(conn: psycopg.Connection, store_id: int) -> list[dict]:
    rows = conn.execute(f"SELECT {EMP_COLS} FROM employees WHERE store_id = %s ORDER BY employee_id",
                        (store_id,)).fetchall()
    return [_fix(r) for r in rows]


def get_employee(conn: psycopg.Connection, store_id: int, employee_id: int) -> dict | None:
    return _fix(conn.execute(f"SELECT {EMP_COLS} FROM employees WHERE store_id = %s AND employee_id = %s",
                             (store_id, employee_id)).fetchone())


def create_employee(conn: psycopg.Connection, store_id: int, data: dict) -> dict:
    """weekly_days ↔ fixed_weekdays 불일치는 DB CHECK 가 거부 → 422 CHECK_VIOLATION (V9_ko 178)."""
    return _fix(conn.execute(
        f"""INSERT INTO employees (store_id, name, is_regular, has_qualification, closing_ok, weekly_days,
                                   fixed_weekdays)
            VALUES (%s, %s, %s, %s, %s, %s, %s::smallint[]) RETURNING {EMP_COLS}""",
        (store_id, data["name"], data["is_regular"], data["has_qualification"], data["closing_ok"],
         data["weekly_days"], data["fixed_weekdays"])).fetchone())


def update_employee(conn: psycopg.Connection, store_id: int, employee_id: int, fields: dict) -> dict | None:
    if not fields:
        return get_employee(conn, store_id, employee_id)
    parts = []
    for k in fields:
        cast = "::smallint[]" if k == "fixed_weekdays" else ""
        parts.append(sql.SQL("{} = %s" + cast).format(sql.Identifier(k)))
    q = sql.SQL("UPDATE employees SET {} WHERE store_id = %s AND employee_id = %s RETURNING " + EMP_COLS).format(
        sql.SQL(", ").join(parts))
    return _fix(conn.execute(q, (*fields.values(), store_id, employee_id)).fetchone())


def employee_ids(conn: psycopg.Connection, store_id: int) -> set[int]:
    return {r["employee_id"] for r in conn.execute("SELECT employee_id FROM employees WHERE store_id = %s",
                                                   (store_id,)).fetchall()}


# ---------------------------------------------------------------- 업무 (남기되 검사에는 안 씀, V9_ko 182-184 · 454)

def list_tasks(conn: psycopg.Connection, store_id: int) -> list[dict]:
    return conn.execute(f"SELECT {TASK_COLS} FROM tasks WHERE store_id = %s ORDER BY sort_order, task_id",
                        (store_id,)).fetchall()


def create_task(conn: psycopg.Connection, store_id: int, name: str, sort_order: int) -> dict:
    return conn.execute(f"INSERT INTO tasks (store_id, name, sort_order) VALUES (%s, %s, %s) RETURNING {TASK_COLS}",
                        (store_id, name, sort_order)).fetchone()


def update_task(conn: psycopg.Connection, store_id: int, task_id: int, fields: dict) -> dict | None:
    if not fields:
        return conn.execute(f"SELECT {TASK_COLS} FROM tasks WHERE store_id = %s AND task_id = %s",
                            (store_id, task_id)).fetchone()
    sets = sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(k)) for k in fields)
    q = sql.SQL("UPDATE tasks SET {} WHERE store_id = %s AND task_id = %s RETURNING " + TASK_COLS).format(sets)
    return conn.execute(q, (*fields.values(), store_id, task_id)).fetchone()


def delete_task(conn: psycopg.Connection, store_id: int, task_id: int) -> bool:
    """tasks 를 참조하는 표는 없다 (employee_skills · store_task_needs · evaluations 는 추천안에서 삭제, db/001)."""
    return conn.execute("DELETE FROM tasks WHERE store_id = %s AND task_id = %s", (store_id, task_id)).rowcount == 1
