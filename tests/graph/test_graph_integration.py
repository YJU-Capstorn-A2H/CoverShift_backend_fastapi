"""통합 1개: 진짜 app.validate + app.llm.FakeComposer (LLM_MODE=fake). 가짜로 바꿔 끼우지 않는다.

다른 에이전트의 모듈이 아직 스텁(NotImplementedError)이면 이 시험은 실패한다 — 일부러 그대로 둔다 (통과로 꾸미지 않는다).
데이터 두 벌: small = tests/graph/conftest.py 의 작은 점포 (자격자 1명뿐 → 예외 불가 위반으로 막히는 쪽),
demo_seed = app.seed.seed_db (아즈키 2026-11 시연 데이터, G20).
"""
from __future__ import annotations

import json

import psycopg
import pytest
from langgraph.types import Command
from psycopg.rows import dict_row

from app.domain import NON_WAIVABLE_CODES
from app.graph.deps import default_deps
from tests.graph.conftest import World
from tests.graph.fakes import cfg, waiting

REVIEW_KEYS = {"type", "draft_id", "result", "violations", "blocked", "shortages", "notes", "free_text_guidelines",
               "auto_retry_count", "manager_request_count", "manager_request_limit"}


@pytest.fixture(params=["small", "demo_seed"])
def any_world(request, clean_db):
    if request.param == "small":
        return request.getfixturevalue("world")
    from app.seed import seed_db
    with psycopg.connect(clean_db, row_factory=dict_row) as c:
        ids = seed_db(c, manager_email="graph-test@example.com", password="test-only-not-secret")
    return World(clean_db, store_id=ids["store_id"], manager_id=ids["manager_id"], period_id=ids["period_id"])


@pytest.mark.req("M-1")
def test_real_validate_with_fake_composer(any_world, mem_graph, monkeypatch):
    w0 = any_world
    monkeypatch.setenv("LLM_MODE", "fake")
    monkeypatch.delenv("FAKE_LLM_SCENARIO", raising=False)
    monkeypatch.delenv("ENABLE_CONTACT", raising=False)
    g = mem_graph(default_deps())  # composer = app.llm.get_composer(), 검증 = app.validate 그대로
    g.invoke({"store_id": w0.store_id, "period_id": w0.period_id}, cfg("t"))
    w = waiting(g, "t")
    assert w is not None and set(w) == REVIEW_KEYS and w["result"] in ("valid", "limit_reached", "draft_empty")
    assert w0.count("shift_drafts") == 1
    assert w0.count("validation_violations", "draft_id = %s", (w["draft_id"],)) == len(w["violations"])
    json.dumps(g.get_state(cfg("t")).values)  # 진짜 검증의 detail 까지 State 가 JSON 그대로 (체크포인터・Aegra)

    waivable = [v["key"] for v in w["violations"] if v["waivable"]]
    g.invoke(Command(resume={"decision": "approved", "manager_id": w0.manager_id, "draft_id": w["draft_id"],
                             "waived_keys": waivable}), cfg("t"))
    after = waiting(g, "t")
    if any(v["check_code"] in NON_WAIVABLE_CODES for v in w["violations"]):
        assert after is not None and after["blocked"]
        assert {v["check_code"] for v in after["blocked"]} <= NON_WAIVABLE_CODES
        assert w0.count("shift_drafts", "status = 'confirmed'") == 0
    else:
        assert after is None
        assert w0.count("shift_drafts", "status = 'confirmed'") == 1
        assert w0.count("line_outbox", "kind = 'shift_confirmed'") == w0.count("employees")
        assert w0.count("validation_violations", "allowed_by IS NOT NULL") == len(waivable)
