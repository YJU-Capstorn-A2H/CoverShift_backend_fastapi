"""편성 그래프 조립 (담당: graph 에이전트). 흐름: V9_ko 59-89 (1-1・1-2), State: 1-3.

    START → collect → auto_draft ⇄ validate ─┬→ (contact ⇄ contact_wait ⏸) ─┐
                     ↑   (자동 재편성 ≤3)      └──────────────────────────────┴→ save_draft → manager_review ⏸
                     │                                                                         │
                     └── request_redraft ←── redraft_requested (≤2) ──────────────────────────┤
                                save_draft ←── apply_edit ←── edit_saved ─────────────────────┤
                                manager_review ←(막힘)── check_approval ←── approved ──────────┘
                                                              └→ commit_notify → END

⏸ = interrupt (체크포인트에 상태만 남기고 멈춘다. 재개는 같은 thread 에 Command(resume=...), F-11, V9_ko 89).
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.graph import nodes as N
from app.graph.deps import Deps, default_deps
from app.graph.state import GraphInput, ShiftState

# invoke 한 번(멈출 때까지 한 토막)의 최대 단계 수. 기본값 10,007 이면 고리 버그 때 LLM 을 수천 번 부른다
# (handwritten/src/shift_graph.py 10/30 E7). 정상 최장 토막 ≈ manager_review → request_redraft → (auto_draft+validate)×4
# → save_draft → manager_review = 12단계 → 넉넉히 50.
RECURSION_LIMIT = 50


def build_graph(checkpointer=None, deps: Deps | None = None):
    """컴파일된 그래프를 돌려준다. checkpointer=None 이면 붙이지 않는다 (Aegra 가 자기 것을 넣는다)."""
    d = deps or default_deps()

    def bind(fn):
        def run(state):
            return fn(state, d)
        run.__name__ = fn.__name__
        return run

    g = StateGraph(ShiftState, input_schema=GraphInput)
    g.add_node("collect", bind(N.collect))
    g.add_node("auto_draft", bind(N.auto_draft), destinations=("auto_draft", "validate"))
    g.add_node("validate", bind(N.validate), destinations=("auto_draft", "contact", "save_draft"))
    g.add_node("contact", bind(N.contact), destinations=("contact_wait", "save_draft"))
    g.add_node("contact_wait", bind(N.contact_wait), destinations=("contact", "contact_wait"))
    g.add_node("save_draft", bind(N.save_draft))
    g.add_node("manager_review", bind(N.manager_review),
               destinations=("manager_review", "check_approval", "request_redraft", "apply_edit"))
    g.add_node("check_approval", bind(N.check_approval), destinations=("manager_review", "commit_notify"))
    g.add_node("request_redraft", bind(N.request_redraft))
    g.add_node("apply_edit", bind(N.apply_edit))
    g.add_node("commit_notify", bind(N.commit_notify))

    g.add_edge(START, "collect")
    g.add_edge("collect", "auto_draft")
    g.add_edge("save_draft", "manager_review")
    g.add_edge("request_redraft", "auto_draft")
    g.add_edge("apply_edit", "save_draft")
    g.add_edge("commit_notify", END)
    return g.compile(checkpointer=checkpointer).with_config(recursion_limit=RECURSION_LIMIT)
