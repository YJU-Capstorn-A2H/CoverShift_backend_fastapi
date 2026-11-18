"""러너 시험용 작은 그래프 (진짜 편성 그래프 app/graph 와 무관). prototype_aegra/slow_graph.py · parallel_graph.py 와 같은 생각.

  START → prepare(초안 번호 +1) → review ⏸ interrupt({"type": "review" | "contact", ...})
        ─ approved → commit(바깥 동작 1번 기록) → END
        └ 그 밖   → prepare (재편성: draft_id 가 하나 늘어난 새 안으로 다시 대기)

이 파일은 langgraph 만 import 한다 — Aegra 가 경로로 직접 읽어도(tests/runtime/aegra_e2e.json) 돌게.
"""
from __future__ import annotations

import operator
import os
import threading
import time
from collections import defaultdict
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

# 바깥 동작(확정 저장 · 통지 자리) 기록 — 같은 프로세스 안에서만 센다. 두 번이면 「두 번 반영」
_COMMITS: dict[str, int] = defaultdict(int)
_MU = threading.Lock()


def commits(thread_id: str) -> int:
    with _MU:
        return _COMMITS[thread_id]


class DummyState(TypedDict, total=False):
    period_id: int
    draft_id: int
    decision: str
    log: Annotated[list, operator.add]


def review_wait(draft_id: int) -> dict:
    """ReviewWait (API_CONTRACT.md) 모양."""
    return {"type": "review", "draft_id": draft_id, "result": "valid", "violations": [], "blocked": [],
            "shortages": [], "notes": [], "free_text_guidelines": [], "auto_retry_count": 0,
            "manager_request_count": 0, "manager_request_limit": 2}


CONTACT_WAIT = {"type": "contact", "contact_id": 7, "employee_id": 3, "work_date": "2026-11-05",
                "start_time": "17:00", "end_time": "22:30"}


def make_factory(*, prepare_delay: float = 0.0, review_delay: float = 0.0, fail_in: str | None = None,
                 wait: str = "review"):
    """build_graph(checkpointer=None, deps=None) 와 같은 모양의 함수를 돌려준다 (app.graph_seung.build 계약과 같게)."""

    def build_graph(checkpointer=None, deps=None):
        def prepare(state: DummyState) -> dict:
            if prepare_delay:
                time.sleep(prepare_delay)
            if fail_in == "prepare":
                raise RuntimeError("일부러 실패 (prepare)")
            return {"draft_id": state.get("draft_id", 0) + 1, "log": ["prepare"]}

        def review(state: DummyState) -> dict:
            if wait == "contact":
                ans = interrupt(dict(CONTACT_WAIT))
                return {"decision": "approved" if ans.get("answer") == "yes" else "redraft_requested",
                        "log": [f"contact:{ans.get('answer')}"]}
            ans = interrupt(review_wait(state["draft_id"]))
            if review_delay:
                time.sleep(review_delay)
            if fail_in == "review":
                raise RuntimeError("일부러 실패 (review 뒤)")
            return {"decision": ans["decision"], "log": [f"review:{ans['decision']}"]}

        def commit(state: DummyState, config) -> dict:
            tid = config["configurable"]["thread_id"]
            with _MU:
                _COMMITS[tid] += 1
            return {"log": ["commit"]}

        g = StateGraph(DummyState)
        g.add_node("prepare", prepare)
        g.add_node("review", review)
        g.add_node("commit", commit)
        g.add_edge(START, "prepare")
        g.add_edge("prepare", "review")
        g.add_conditional_edges("review", lambda s: "commit" if s.get("decision") == "approved" else "prepare",
                                ["commit", "prepare"])
        g.add_edge("commit", END)
        return g.compile(checkpointer=checkpointer)

    return build_graph


# Aegra e2e 용 (체크포인트는 Aegra 가 넣는다). 지연은 Aegra 프로세스의 환경 변수로 준다
graph = make_factory(prepare_delay=float(os.environ.get("DUMMY_PREPARE_DELAY", "0")),
                     review_delay=float(os.environ.get("DUMMY_REVIEW_DELAY", "0")))()
graph_fail = make_factory(fail_in="prepare")()
