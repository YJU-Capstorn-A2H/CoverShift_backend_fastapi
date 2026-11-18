"""runtime 시험 공통. 시험 DB 는 tests/conftest.py 의 db_url (세션마다 새 DB)."""
from __future__ import annotations

import time
import uuid

import pytest

from app.runner import LocalRunner
from tests.runtime import dummy_graph


def new_thread() -> str:
    return f"t-{uuid.uuid4()}"


def wait_state(runner, period_id: int, thread_id: str, states: set[str], timeout: float = 15.0) -> dict:
    """state 가 states 중 하나가 될 때까지 status 를 본다. 시간 안에 안 되면 마지막 status 와 함께 실패."""
    deadline = time.monotonic() + timeout
    st = runner.status(period_id, thread_id)
    while st["state"] not in states:
        if time.monotonic() > deadline:
            pytest.fail(f"{timeout}초 안에 {states} 가 안 됨. 마지막 status={st}")
        time.sleep(0.05)
        st = runner.status(period_id, thread_id)
    return st


@pytest.fixture
def make_local(db_url):
    """LocalRunner(dummy graph) 를 만들고 시험이 끝나면 닫는다. make_local(**make_factory 인자)."""
    made: list[LocalRunner] = []

    def make(**kw) -> LocalRunner:
        r = LocalRunner(db_url, graph_factory=dummy_graph.make_factory(**kw))
        made.append(r)
        return r

    yield make
    for r in made:
        r.close(timeout=10)
