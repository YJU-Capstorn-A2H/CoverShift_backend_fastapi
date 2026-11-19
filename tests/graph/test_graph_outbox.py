"""핵심 회귀 (A-21, F-9): 확정(commit_notify) 안에서는 LINE 을 부르지 않는다 — 발송이 전부 실패해도 확정은 남는다."""
from __future__ import annotations

import psycopg
import pytest
from langgraph.types import Command
from psycopg.rows import dict_row

from app import line_api
from app.config import get_settings
from app.line_dispatcher import dispatch_one
from tests.graph.fakes import ScriptedComposer, cfg, draft, make_deps, waiting

START = {"store_id": 1, "period_id": 1}


@pytest.mark.req("A-21", "F-9")
def test_confirm_succeeds_and_leaves_pending_rows_even_when_every_send_fails(world, mem_graph, monkeypatch):
    monkeypatch.setenv("LINE_MODE", "line")
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "test-channel-secret")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "test-access-token")
    monkeypatch.setenv("LINE_OFFICIAL_ACCOUNT_ID", "@test-account")
    pushes: list[str] = []

    def line_is_down(access_token, *, to, messages, retry_key):
        pushes.append(to)
        return line_api.PushResponse(500, '{"message": "Internal Server Error"}')

    monkeypatch.setattr(line_api, "push", line_is_down)
    world.q("UPDATE employees SET line_user_id = 'U-' || employee_id")  # 전원 LINE 연결됨 = 전원에게 실제로 보낼 상황

    g = mem_graph(make_deps(ScriptedComposer([draft()])))
    g.invoke(START, cfg("t"))
    draft_id = waiting(g, "t")["draft_id"]
    g.invoke(Command(resume={"decision": "approved", "manager_id": 1, "draft_id": draft_id, "waived_keys": []}),
             cfg("t"))

    # 확정은 끝났고, 그 사이 LINE 은 한 번도 불리지 않았다. 통지는 전부 대기로 남았다
    assert world.count("shift_drafts", "status = 'confirmed'") == 1
    assert pushes == []
    assert world.count("line_outbox", "kind = 'shift_confirmed' AND status = 'pending' AND attempts = 0") == 4

    # 발송기를 돌려도(전부 500) 확정은 그대로, 통지는 대기로 남아 다시 시도된다
    with psycopg.connect(world.url, row_factory=dict_row) as conn:
        while dispatch_one(conn, get_settings()):
            pass
    assert sorted(pushes) == ["U-1", "U-2", "U-3", "U-4"]
    assert world.count("shift_drafts", "status = 'confirmed'") == 1
    assert world.count("line_outbox", "status = 'pending' AND attempts = 1 AND last_error LIKE 'HTTP 500%%'") == 4


@pytest.mark.req("A-21", "F-9")
def test_confirm_that_fails_midway_leaves_no_outbox_rows_and_no_confirmation(world, mem_graph):
    """확정과 통지 기록은 한 트랜잭션: 세 번째 직원의 통지를 적다가 실패하면 앞의 두 건도, 확정도 남지 않는다."""
    from app.line_outbox import enqueue
    written: list[int] = []

    def enqueue_then_break(conn, **kw) -> bool:
        if len(written) == 2:
            raise RuntimeError("세 번째 통지를 적다가 실패")
        written.append(kw["employee_id"])
        return enqueue(conn, **kw)

    g = mem_graph(make_deps(ScriptedComposer([draft()]), enqueue=enqueue_then_break))
    g.invoke(START, cfg("t"))
    draft_id = waiting(g, "t")["draft_id"]
    with pytest.raises(RuntimeError, match="세 번째 통지"):
        g.invoke(Command(resume={"decision": "approved", "manager_id": 1, "draft_id": draft_id, "waived_keys": []}),
                 cfg("t"))
    assert len(written) == 2  # 두 건은 실제로 INSERT 까지 갔었다
    assert world.count("line_outbox") == 0
    assert world.count("shift_drafts", "status = 'confirmed'") == 0
