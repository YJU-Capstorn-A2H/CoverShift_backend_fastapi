"""Aegra 0.10.8 이 읽는 그래프 (aegra.json 의 "graphs": {"shift": "./app/graph/aegra_entry.py:graph"}).

- 컴파일된 그래프를 체크포인터 없이 내보낸다. Aegra 가 자기 Postgres 체크포인터를 넣은 사본을 만든다
  (aegra_api/services/langgraph_service.py: base_graph.copy(update={"checkpointer": ...}), prototype_aegra/graph.py 와 같은 방식).
  명세 2-6 ⑥ 「체크포인트는 Aegra가 Postgres에 저장(PostgresSaver 직접 사용은 뺌, D1)」(V9_ko 507).
- Aegra 는 이 파일을 경로로 import 한다 → `app` 패키지가 sys.path 에 있어야 한다 (aegra.json "dependencies": ["."]).
- 바깥 의존(LLM・검증)은 처음 부를 때 고른다 (app/graph/deps.py) → import 할 때 LLM 키가 없어도 서버는 뜬다.
"""
from app.graph_seung.build import build_graph

graph = build_graph()
