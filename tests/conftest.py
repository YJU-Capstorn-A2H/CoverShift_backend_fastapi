"""시험 하네스 공통 (메인 세션 소유 — 서브에이전트는 고치지 않는다. 필요한 fixture 는 각자 tests/<모듈>/conftest.py 에).

- 시험 DB: compose.test.yaml 의 Postgres(127.0.0.1:5443). 세션마다 새 DB `t_<임의>` 를 만들고 끝나면 지운다
  → 여러 에이전트가 동시에 pytest 를 돌려도 서로의 데이터를 건드리지 않는다.
- DDL: db/*.sql 을 이름 순서대로 적용 (001 = 명세 v17 그대로, 002 = (가정) 추가 표).
- 기존 shift-agent-db(5433) 에는 접속하지 않는다.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[1]
ADMIN_URL = os.environ.get("MVP_TEST_ADMIN_URL", "postgresql://mvp:mvp@127.0.0.1:5443/mvp")

TABLES = [
    "contact_log", "line_outbox", "manager_actions", "validation_violations", "shift_assignments", "shift_drafts",
    "evaluations", "store_task_needs", "employee_skills", "tasks", "availability_submissions", "submission_links",
    "shift_periods", "employees", "store_time_rules", "invite_codes", "stores", "manager_accounts",
]


def _url_for(dbname: str) -> str:
    base, _, _ = ADMIN_URL.rpartition("/")
    return f"{base}/{dbname}"


@pytest.fixture(scope="session")
def db_url() -> str:
    """이 테스트 세션 전용 DB 의 접속 문자열. 시험 Postgres 가 안 떠 있으면 바로 실패한다(건너뛰지 않는다)."""
    name = f"t_{uuid.uuid4().hex[:10]}"
    try:
        admin = psycopg.connect(ADMIN_URL, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError as e:  # 일부러 skip 하지 않는다: DB 없이 「통과」로 보이면 안 된다 (R5)
        pytest.fail(f"시험 DB 에 접속 못 함 ({ADMIN_URL}). 먼저: docker compose -f compose.test.yaml -p mvp-test up -d --wait\n{e}")
    with admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    url = _url_for(name)
    with psycopg.connect(url, autocommit=True) as c:
        for sql in sorted((ROOT / "db").glob("[0-9][0-9][0-9]_*.sql")):
            c.execute(sql.read_text())
    os.environ["DATABASE_URL"] = url  # app.config.get_settings() 가 이 DB 를 보게
    yield url
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def clean_db(db_url: str) -> str:
    """테스트 하나 전에 모든 표를 비운다 (IDENTITY 도 1부터)."""
    with psycopg.connect(db_url, autocommit=True) as c:
        c.execute("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE")
    return db_url


@pytest.fixture
def conn(clean_db: str):
    with psycopg.connect(clean_db) as c:
        yield c
