"""러너 계약과 진짜 그래프를 맞물려 본다: app.runner.LocalRunner + app.graph_seung.build_graph (가짜 LLM・가짜 검증).

runtime 에이전트의 시험은 dummy 그래프를 쓴다 → 이 시험이 「진짜 그래프의 interrupt 모양이 러너・API 의 RunStatus 로
그대로 나가는가」를 본다 (API_CONTRACT 「편성 제어」・「러너 계약」).
"""
from __future__ import annotations

import time as _time

import psycopg
import pytest

from app.graph_seung import build_graph
from tests.graph.fakes import ScriptedComposer, draft, make_deps

SCHEMA = "graph_runner_ckpt"


def settle(runner, period_id, tid, limit=15.0) -> dict:
    t0 = _time.monotonic()
    while (st := runner.status(period_id, tid))["state"] == "running":
        assert _time.monotonic() - t0 < limit, "run 이 끝나지 않는다"
        _time.sleep(0.05)
    return st


@pytest.fixture
def runner_factory(db_url):
    made = []

    def make(deps):
        from app.runner import LocalRunner
        r = LocalRunner(db_url, graph_factory=build_graph, deps=deps, checkpoint_schema=SCHEMA, pool_max=4)
        made.append(r)
        return r
    yield make
    for r in made:
        r.close()
    with psycopg.connect(db_url, autocommit=True) as c:
        c.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")


@pytest.mark.req("M-1", "F-11")
def test_local_runner_drives_real_graph(world, runner_factory):
    from app.runner import Busy, NotWaiting
    r = runner_factory(make_deps(ScriptedComposer([draft()])))
    tid = "period-1"
    assert r.start(1, tid)["state"] == "running"
    st = settle(r, 1, tid)
    assert st["state"] == "waiting_review" and st["waiting"]["type"] == "review" and st["waiting"]["draft_id"] == 1

    with pytest.raises(NotWaiting):  # 옛 안에 대한 승인 → 러너가 먼저 막는다 (그래프도 버린다: test_graph_review)
        r.resume(1, tid, {"decision": "approved", "manager_id": 1, "draft_id": 99, "waived_keys": []})
    r.resume(1, tid, {"decision": "approved", "manager_id": 1, "draft_id": 1, "waived_keys": []})
    with pytest.raises((Busy, NotWaiting)):  # 같은 thread 에 겹친 두 번째 클릭 (도는 중이면 Busy, 이미 끝났으면 NotWaiting)
        r.resume(1, tid, {"decision": "approved", "manager_id": 1, "draft_id": 1, "waived_keys": []})
    st = settle(r, 1, tid)
    assert st["state"] == "confirmed" and st["waiting"] is None
    assert world.count("shift_drafts", "status = 'confirmed'") == 1
    assert world.count("line_outbox", "kind = 'shift_confirmed'") == 4
