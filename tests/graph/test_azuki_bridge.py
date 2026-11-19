"""아즈키 그래프(app/graph)를 runner 계약의 build_graph 모양으로 건네는 다리 — 컴파일되고, 환경 변수로 고를 수 있는지만 본다.
(접속 숙제: 대기 값 변환 · DB 저장 · 위반 변환 · 가짜 LLM 은 아직 없다. 이 시험은 그 전 단계다.)"""
from langgraph.checkpoint.memory import InMemorySaver

AZUKI_NODES = {"collect", "auto_draft", "validate", "contact", "approve", "route_3", "finalize", "send_final_shift"}


def test_azuki_graph_compiles_through_bridge(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://mvp:mvp@127.0.0.1:5443/mvp")  # app/graph/settings.py 가 import 때 읽는다
    from app.graph_azuki_bridge import build_graph

    g = build_graph(InMemorySaver())
    assert AZUKI_NODES <= set(g.nodes)
    assert g.checkpointer is not None
    assert build_graph(None).checkpointer is None  # 체크포인터 없이도 만들어진다 (Aegra 용)


def test_runner_picks_graph_by_env(monkeypatch):
    from app.runner import default_graph_factory

    monkeypatch.delenv("GRAPH_IMPL", raising=False)
    assert default_graph_factory().__module__ == "app.graph_seung.build"  # 기본 = 승혁
    monkeypatch.setenv("GRAPH_IMPL", "azuki")
    assert default_graph_factory().__module__ == "app.graph_azuki_bridge"
