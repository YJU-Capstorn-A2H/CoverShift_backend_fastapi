"""점포 · 시간대 규칙 (A-5, A-6, M-8). 모든 쿼리는 store_id 로 범위를 묶는다 (V9_ko 167 「다른 점포 데이터 접근 불가」)."""
from __future__ import annotations

import psycopg
from psycopg import sql

RULE_COLS = "rule_id, weekday, start_time, end_time, min_headcount, min_qualified"


def get_store(conn: psycopg.Connection, store_id: int) -> dict:
    return conn.execute("SELECT store_id, name FROM stores WHERE store_id = %s", (store_id,)).fetchone()


def update_store(conn: psycopg.Connection, store_id: int, name: str) -> dict:
    return conn.execute("UPDATE stores SET name = %s WHERE store_id = %s RETURNING store_id, name",
                        (name, store_id)).fetchone()


def list_time_rules(conn: psycopg.Connection, store_id: int) -> list[dict]:
    return conn.execute(f"SELECT {RULE_COLS} FROM store_time_rules WHERE store_id = %s ORDER BY weekday, start_time",
                        (store_id,)).fetchall()


def create_time_rule(conn: psycopg.Connection, store_id: int, data: dict) -> dict:
    """겹침은 DB EXCLUDE 가 거부한다 → ExclusionViolation → 409 (V9_ko 172). 여기서 미리 세지 않는다."""
    return conn.execute(
        f"""INSERT INTO store_time_rules (store_id, weekday, start_time, end_time, min_headcount, min_qualified)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING {RULE_COLS}""",
        (store_id, data["weekday"], data["start_time"], data["end_time"], data["min_headcount"],
         data["min_qualified"])).fetchone()


def update_time_rule(conn: psycopg.Connection, store_id: int, rule_id: int, fields: dict) -> dict | None:
    if not fields:
        return conn.execute(f"SELECT {RULE_COLS} FROM store_time_rules WHERE store_id = %s AND rule_id = %s",
                            (store_id, rule_id)).fetchone()
    sets = sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(k)) for k in fields)
    q = sql.SQL("UPDATE store_time_rules SET {} WHERE store_id = %s AND rule_id = %s RETURNING " + RULE_COLS).format(
        sets)
    return conn.execute(q, (*fields.values(), store_id, rule_id)).fetchone()


def delete_time_rule(conn: psycopg.Connection, store_id: int, rule_id: int) -> bool:
    """store_time_rules 를 참조하는 표는 없다 (store_task_needs 는 추천안에서 삭제, db/001)."""
    cur = conn.execute("DELETE FROM store_time_rules WHERE store_id = %s AND rule_id = %s", (store_id, rule_id))
    return cur.rowcount == 1


def count_rules(conn: psycopg.Connection, store_id: int, weekday: int) -> int:
    return conn.execute("SELECT count(*) AS n FROM store_time_rules WHERE store_id = %s AND weekday = %s",
                        (store_id, weekday)).fetchone()["n"]


def copy_time_rules(conn: psycopg.Connection, store_id: int, from_weekday: int, to_weekdays: list[int]) -> int:
    """요일 복사 (V9_ko 176). 대상 요일의 기존 규칙은 지우고 원본 요일 규칙을 그대로 넣는다 (API_CONTRACT). 넣은 행 수."""
    conn.execute("DELETE FROM store_time_rules WHERE store_id = %s AND weekday = ANY(%s::smallint[])",
                 (store_id, to_weekdays))
    cur = conn.execute(
        """INSERT INTO store_time_rules (store_id, weekday, start_time, end_time, min_headcount, min_qualified)
           SELECT r.store_id, t.wd, r.start_time, r.end_time, r.min_headcount, r.min_qualified
           FROM store_time_rules r CROSS JOIN unnest(%s::smallint[]) AS t(wd)
           WHERE r.store_id = %s AND r.weekday = %s""",
        (to_weekdays, store_id, from_weekday))
    return cur.rowcount
