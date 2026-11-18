"""그래프 시험 fixture. 시험 DB(tests/conftest.py 의 clean_db)에 작은 점포 1개를 SQL 로 넣는다."""
from __future__ import annotations

import json
from dataclasses import dataclass

import psycopg
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from psycopg.rows import dict_row

from app.graph import build_graph

NAMES = {1: "실명가나", 2: "실명다라", 3: "실명마바", 4: "실명사아"}


@dataclass
class World:
    url: str
    store_id: int = 1
    manager_id: int = 1
    period_id: int = 1

    def q(self, sql: str, params=()):
        with psycopg.connect(self.url, row_factory=dict_row, autocommit=True) as c:
            cur = c.execute(sql, params)
            return cur.fetchall() if cur.description else None

    def one(self, sql: str, params=()):
        rows = self.q(sql, params)
        return rows[0] if rows else None

    def count(self, table: str, where: str = "true", params=()) -> int:
        return self.one(f"SELECT count(*) AS n FROM {table} WHERE {where}", params)["n"]

    def add_rules(self, rules: list[dict], free_text: list[str]) -> int:
        return self.one(
            """INSERT INTO manager_actions (period_id, manager_id, action, instruction, rules)
               VALUES (%s, %s, 'rules_confirmed', 'src', %s::jsonb) RETURNING action_id""",
            (self.period_id, self.manager_id, json.dumps({"rules": rules, "free_text": free_text})))["action_id"]


@pytest.fixture
def world(clean_db) -> World:
    """점포 1 / 관리자 1 / 2026-11 기간 1 / 직원 4 (사원 2: 1・2, 알바 2: 3・4) / 요일마다 시간대 2개."""
    w = World(clean_db)
    w.q("INSERT INTO manager_accounts (email, password_hash) VALUES ('m1@example.com', 'x')")
    w.q("INSERT INTO stores (manager_id, name) VALUES (1, '시험점')")
    for eid, (reg, qual, closing) in {1: (True, True, True), 2: (True, False, True), 3: (False, False, False),
                                       4: (False, False, True)}.items():
        w.q("""INSERT INTO employees (store_id, name, is_regular, has_qualification, closing_ok)
               VALUES (1, %s, %s, %s, %s)""", (NAMES[eid], reg, qual, closing))
    w.q("""INSERT INTO shift_periods (store_id, target_month, submission_deadline, designated_off_days)
           VALUES (1, '2026-11-01', '2026-10-20T00:00+09', 9)""")
    for wd in range(1, 8):
        w.q("""INSERT INTO store_time_rules (store_id, weekday, start_time, end_time, min_headcount, min_qualified)
               VALUES (1, %s, '09:00', '15:00', 2, 1), (1, %s, '15:00', '22:30', 2, 1)""", (wd, wd))
    w.q("""INSERT INTO availability_submissions (employee_id, work_date, kind, start_time, end_time) VALUES
           (3, '2026-11-02', 'available', '09:00', '15:00'),
           (2, '2026-11-05', 'paid_leave', NULL, NULL),
           (4, '2026-11-03', 'available', '15:00', '22:30')""")
    return w


@pytest.fixture
def mem_graph():
    """build(deps) → InMemorySaver 를 붙인 그래프."""
    def make(deps):
        return build_graph(InMemorySaver(), deps)
    return make

