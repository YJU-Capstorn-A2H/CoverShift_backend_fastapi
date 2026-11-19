"""입구 API 시험용 fixture. 러너 · LLM 은 가짜(FakeRunner · FakeComposer)를 dependency_overrides 로 끼운다.

- DB 는 tests/conftest.py 의 clean_db (세션 전용 DB, 테스트마다 비움).
- bcrypt 비용을 4로 낮춘다(시험 속도). 실제 값은 app/repo/managers.py BCRYPT_ROUNDS.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app.api.main import app
from app.llm import get_composer
from app.repo import managers
from app.runner import get_runner

from ._util import FUTURE, PW


class FakeRunner:
    """러너 계약(API_CONTRACT 「러너 계약」)만 흉내. 부른 기록을 calls 에 남긴다."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.states: dict[str, dict] = {}
        self.raise_on: dict[str, BaseException] = {}

    @staticmethod
    def rs(pid, tid, state, waiting=None, error=None) -> dict:
        return {"period_id": pid, "thread_id": tid, "state": state, "waiting": waiting, "error": error}

    def _tid(self, period_id: int) -> str:
        """G22: thread_id 는 「period-{id}-<임의>」. 그 기간으로 start 된 마지막 thread 를 쓴다."""
        for c in reversed(self.calls):
            if c[0] == "start" and c[1] == period_id:
                return c[2]
        return f"period-{period_id}-00000000"

    def start(self, period_id: int, thread_id: str) -> dict:
        self.calls.append(("start", period_id, thread_id))
        if "start" in self.raise_on:
            raise self.raise_on["start"]
        self.states[thread_id] = self.rs(period_id, thread_id, "running")
        return self.states[thread_id]

    def status(self, period_id: int, thread_id: str | None) -> dict:
        self.calls.append(("status", period_id, thread_id))
        if "status" in self.raise_on:
            raise self.raise_on["status"]
        return self.states.get(thread_id) or self.rs(period_id, thread_id, "not_started")

    def resume(self, period_id: int, thread_id: str, payload: dict) -> dict:
        self.calls.append(("resume", period_id, thread_id, payload))
        if "resume" in self.raise_on:
            raise self.raise_on["resume"]
        self.states[thread_id] = self.rs(period_id, thread_id, "running")
        return self.states[thread_id]

    # 시험에서 상태를 만드는 도우미
    def wait_review(self, period_id: int, draft_id: int):
        tid = self._tid(period_id)
        self.states[tid] = self.rs(period_id, tid, "waiting_review", {
            "type": "review", "draft_id": draft_id, "result": "valid", "violations": [], "blocked": [],
            "shortages": [], "notes": [], "free_text_guidelines": [], "auto_retry_count": 0,
            "manager_request_count": 0, "manager_request_limit": 2})

    def wait_contact(self, period_id: int, contact_id: int):
        tid = self._tid(period_id)
        self.states[tid] = self.rs(period_id, tid, "waiting_contact", {
            "type": "contact", "contact_id": contact_id, "employee_id": 1, "work_date": "2026-12-01",
            "start_time": "09:00", "end_time": "13:00"})

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]


class FakeComposer:
    def __init__(self):
        self.calls: list[tuple] = []
        self.result = None
        self.error: BaseException | None = None

    def instruction_to_rules(self, ctx, text):
        self.calls.append((ctx, text))
        if self.error:
            raise self.error
        return self.result

    def compose(self, *a, **k):  # API 는 compose 를 부르지 않는다
        raise AssertionError("API 가 compose 를 불렀다")


@dataclass
class Api:
    c: TestClient
    runner: FakeRunner
    composer: FakeComposer
    url: str
    stores: dict[str, dict] = field(default_factory=dict)

    def db(self) -> psycopg.Connection:
        return psycopg.connect(self.url, row_factory=dict_row, autocommit=True)

    def sql(self, q: str, args: Any = None) -> list[dict]:
        with self.db() as c:
            cur = c.execute(q, args)
            return cur.fetchall() if cur.description else []

    def login(self, email: str, password: str = PW) -> dict:
        r = self.c.post("/api/auth/manager/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    def make_store(self, key: str, name: str, email: str) -> dict:
        with psycopg.connect(self.url, row_factory=dict_row) as c:
            r = managers.create_store(c, store_name=name, email=email, password=PW)
        s = {**r, "email": email, "h": self.login(email)}
        self.stores[key] = s
        return s

    # 자주 쓰는 설정
    def employee(self, h: dict, name: str = "Aさん", is_regular: bool = True, **kw) -> int:
        r = self.c.post("/api/employees", headers=h, json={"name": name, "is_regular": is_regular, **kw})
        assert r.status_code == 201, r.text
        return r.json()["employee_id"]

    def period(self, h: dict, month: str = "2026-12-01", deadline: str = FUTURE, **kw) -> int:
        body = {"target_month": month, "submission_deadline": deadline, "designated_off_days": 9, **kw}
        r = self.c.post("/api/shift-periods", headers=h, json=body)
        assert r.status_code == 201, r.text
        return r.json()["period_id"]


@pytest.fixture
def api(clean_db, monkeypatch) -> Api:
    monkeypatch.setenv("JWT_SECRET", "test-only-secret-0123456789abcdef")
    monkeypatch.setenv("JWT_TTL_MINUTES", "60")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://web.test")
    monkeypatch.delenv("DEV_SKIP_AUTH", raising=False)
    for name in ("LINE_MODE", "LINE_CHANNEL_SECRET", "LINE_CHANNEL_ACCESS_TOKEN", "LINE_OFFICIAL_ACCOUNT_ID"):
        monkeypatch.delenv(name, raising=False)  # 기본 = stub. 셸에 남은 값이 시험을 line 모드로 돌리지 않게
    monkeypatch.setattr(managers, "BCRYPT_ROUNDS", 4)
    monkeypatch.setattr(managers, "_dummy_hash", None)
    runner, composer = FakeRunner(), FakeComposer()
    app.dependency_overrides[get_runner] = lambda: runner
    app.dependency_overrides[get_composer] = lambda: composer
    try:
        with TestClient(app) as c:
            yield Api(c, runner, composer, clean_db)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def two_stores(api: Api) -> Api:
    """점포 A(관리자 1) · 점포 B(관리자 2)."""
    api.make_store("A", "아즈키점", "a@store.test")
    api.make_store("B", "다른점", "b@store.test")
    return api


@pytest.fixture
def contact(two_stores: Api) -> Api:
    """점포 A 의 직원 1명(LINE 'U-me' 연결)에게 출근 요청 1건이 나가 있고, 그래프가 그 답을 기다린다."""
    api = two_stores
    h = api.stores["A"]["h"]
    api.eid = api.employee(h)
    api.sql("UPDATE employees SET line_user_id = 'U-me' WHERE employee_id = %s", (api.eid,))
    api.pid = api.period(h)
    api.c.post(f"/api/shift-periods/{api.pid}/start-now", headers=h)
    api.cid = api.sql("""INSERT INTO contact_requests (period_id, employee_id, work_date, start_time, end_time)
                         VALUES (%s, %s, '2026-12-01', '09:00', '13:00') RETURNING contact_id""",
                      (api.pid, api.eid))[0]["contact_id"]
    api.runner.wait_contact(api.pid, api.cid)
    return api
