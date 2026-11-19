"""발송기 · LINE 설정 시험용 fixture. 진짜 LINE 서버로는 한 번도 나가지 않는다 (tests/conftest.py no_outside_network).

- `outbox`: 점포 1 · 직원 2(1 = LINE 연결됨 'U-1', 2 = 미연결)와 발송함 도우미.
- `line_settings`: LINE_MODE=line + 가짜 시크릿 · 토큰. `fake_push`: line_api.push 를 대신하는 가짜 서버.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import psycopg
import pytest
from psycopg.rows import dict_row

from app import line_api
from app.config import get_settings
from app.line_outbox import enqueue

ACCESS_TOKEN = "test-access-token-DO-NOT-LOG"  # 시험용 가짜 값
CHANNEL_SECRET = "test-channel-secret-DO-NOT-LOG"


@pytest.fixture
def line_settings(monkeypatch, clean_db):
    monkeypatch.setenv("LINE_MODE", "line")
    monkeypatch.setenv("LINE_CHANNEL_SECRET", CHANNEL_SECRET)
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", ACCESS_TOKEN)
    monkeypatch.setenv("LINE_OFFICIAL_ACCOUNT_ID", "@test-account")
    return get_settings()


@pytest.fixture
def stub_settings(monkeypatch, clean_db):
    for name in ("LINE_MODE", "LINE_CHANNEL_SECRET", "LINE_CHANNEL_ACCESS_TOKEN", "LINE_OFFICIAL_ACCOUNT_ID"):
        monkeypatch.delenv(name, raising=False)
    return get_settings()


@dataclass
class FakePush:
    """line_api.push 자리에 끼운다. script 를 앞에서부터 하나씩 쓴다(끝나면 마지막 것을 되풀이).
    항목 = 상태 코드(int) | (상태 코드, 본문) | 던질 예외."""
    script: list = field(default_factory=lambda: [200])
    calls: list[dict] = field(default_factory=list)

    def __call__(self, access_token, *, to, messages, retry_key):
        self.calls.append({"access_token": access_token, "to": to, "messages": messages, "retry_key": retry_key})
        item = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if isinstance(item, BaseException):
            raise item
        status, text = item if isinstance(item, tuple) else (item, "{}")
        return line_api.PushResponse(status, text)


@pytest.fixture
def fake_push(monkeypatch) -> FakePush:
    fake = FakePush()
    monkeypatch.setattr(line_api, "push", fake)
    return fake


@dataclass
class Outbox:
    url: str

    def connect(self) -> psycopg.Connection:
        return psycopg.connect(self.url, row_factory=dict_row)

    def sql(self, q: str, args=None) -> list[dict]:
        with psycopg.connect(self.url, row_factory=dict_row, autocommit=True) as c:
            cur = c.execute(q, args)
            return cur.fetchall() if cur.description else []

    def add(self, employee_id: int | None = 1, kind: str = "manager_notice", body: dict | None = None,
            key: str | None = None) -> int:
        """발송함에 대기 행 1건을 넣고 message_id 를 돌려준다."""
        with self.connect() as c:
            n = c.execute("SELECT count(*) AS n FROM line_outbox").fetchone()["n"]
            assert enqueue(c, store_id=1, employee_id=employee_id, kind=kind, dedupe_key=key or f"k{n + 1}",
                           body=body or {"text": "안내입니다"})
            return c.execute("SELECT max(message_id) AS id FROM line_outbox").fetchone()["id"]

    def row(self, message_id: int) -> dict:
        return self.sql("""SELECT *, extract(epoch FROM next_attempt_at - now())::float AS due_in
                           FROM line_outbox WHERE message_id = %s""", (message_id,))[0]

    def make_due(self) -> None:
        """백오프를 기다리지 않게 대기 행의 다음 시도 시각을 지금으로 당긴다."""
        self.sql("UPDATE line_outbox SET next_attempt_at = now() WHERE status = 'pending'")

    def age(self, message_id: int, interval: str) -> None:
        """그 행이 interval 전에 만들어져 대기에 들어간 것처럼 시각을 옮긴다."""
        self.sql("""UPDATE line_outbox SET created_at = now() - %(i)s::interval, queued_at = now() - %(i)s::interval
                    WHERE message_id = %(id)s""", {"i": interval, "id": message_id})


@pytest.fixture
def outbox(clean_db) -> Outbox:
    o = Outbox(clean_db)
    o.sql("INSERT INTO manager_accounts (email, password_hash) VALUES ('m1@example.com', 'x')")
    o.sql("INSERT INTO stores (manager_id, name) VALUES (1, '시험점')")
    o.sql("""INSERT INTO employees (store_id, name, is_regular, line_user_id)
             VALUES (1, '연결됨', true, 'U-1'), (1, '미연결', false, NULL)""")
    return o
