"""재시작 복구: 승인 대기를 Postgres 체크포인트에 남기고, 새 그래프・새 saver 로 이어서 확정까지 (F-11, D-3)."""
from __future__ import annotations

import psycopg
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command

from app.graph_seung import build_graph
from tests.graph.fakes import ScriptedComposer, cfg, draft, make_deps, waiting

SCHEMA = "graph_ckpt"


@pytest.fixture
def ckpt_url(db_url):
    """체크포인트 표는 별도 schema 에 둔다 → public 의 표 수(test_harness 의 18개)를 건드리지 않는다."""
    with psycopg.connect(db_url, autocommit=True) as c:
        c.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        c.execute(f"CREATE SCHEMA {SCHEMA}")
    sep = "&" if "?" in db_url else "?"
    yield f"{db_url}{sep}options=-csearch_path%3D{SCHEMA}"
    with psycopg.connect(db_url, autocommit=True) as c:
        c.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")


@pytest.mark.req("F-11", "D-3", "D-2")
def test_review_wait_survives_restart(world, ckpt_url):
    tid = "period-1"
    with PostgresSaver.from_conn_string(ckpt_url) as saver:
        saver.setup()
        g1 = build_graph(saver, make_deps(ScriptedComposer([draft()])))
        g1.invoke({"store_id": 1, "period_id": 1}, cfg(tid))
        w1 = waiting(g1, tid)
        assert w1["type"] == "review" and w1["draft_id"] == 1
    del g1, saver  # 「프로세스가 죽었다」: 그래프 객체・연결을 버린다

    # 깨뜨리는 쪽: 메모리 체크포인터로 새로 만들면 대기는 없다 → 복구는 Postgres 덕분
    assert waiting(build_graph(InMemorySaver(), make_deps(ScriptedComposer([draft()]))), tid) is None

    comp2 = ScriptedComposer([draft()])
    with PostgresSaver.from_conn_string(ckpt_url) as saver2:
        g2 = build_graph(saver2, make_deps(comp2))
        assert waiting(g2, tid) == w1  # 같은 대기가 그대로 보인다
        g2.invoke(Command(resume={"decision": "approved", "manager_id": 1, "draft_id": 1, "waived_keys": []}), cfg(tid))
        assert waiting(g2, tid) is None and g2.get_state(cfg(tid)).values["notified"] is True
    assert comp2.calls == []  # 재개는 LLM 을 다시 부르지 않는다
    assert world.one("SELECT status FROM shift_drafts WHERE draft_id = 1")["status"] == "confirmed"
    assert world.count("shift_drafts") == 1 and world.count("line_outbox", "kind = 'shift_confirmed'") == 4
    tables = {r["table_schema"] for r in world.q(
        "SELECT DISTINCT table_schema FROM information_schema.tables WHERE table_name = 'checkpoints'")}
    assert tables == {SCHEMA}


@pytest.mark.req("F-11", "M-1")
def test_aegra_entry_graph_runs_async_with_injected_checkpointer(world, ckpt_url, monkeypatch):
    """Aegra 0.10.8 이 하는 것을 흉내: 체크포인터 없는 graph 를 copy(update={"checkpointer": ...}) 하고 비동기로 돌린다
    (aegra_api/services/langgraph_service.py). 노드는 동기 함수 → 비동기 실행에서도 interrupt・재개가 되는지 본다.
    Aegra 서버 자체는 띄우지 않았다 (5441 포트 금지)."""
    import asyncio
    import copy
    import json

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    monkeypatch.setenv("LLM_MODE", "fake")
    import app.graph_seung.aegra_entry as entry
    assert entry.graph.checkpointer is None  # 체크포인터는 Aegra 몫
    # 같은 모양의 그래프를 deps 만 시험 가짜로 바꿔 만든다 (진짜 검증・FakeComposer 경로는 test_graph_integration)
    base = build_graph(None, make_deps(ScriptedComposer([draft()])))
    assert set(base.nodes) == set(entry.graph.nodes) and base.config == entry.graph.config

    async def go():
        async with AsyncPostgresSaver.from_conn_string(ckpt_url) as saver:
            await saver.setup()
            g = base.copy(update={"checkpointer": saver, "store": None, "config": copy.deepcopy(base.config)})
            c = cfg("aegra-1")
            await g.ainvoke({"period_id": 1}, c)
            snap = await g.aget_state(c)
            w = snap.interrupts[0].value
            json.dumps(snap.values), json.dumps(w)  # Aegra 가 그대로 JSON 으로 돌려줄 수 있어야 한다
            await g.ainvoke(Command(resume={"decision": "approved", "manager_id": 1, "draft_id": w["draft_id"],
                                            "waived_keys": []}), c)
            return w, await g.aget_state(c)
    w, snap = asyncio.run(go())
    assert w["type"] == "review" and snap.next == () and snap.values["notified"] is True
    assert world.count("shift_drafts", "status = 'confirmed'") == 1
