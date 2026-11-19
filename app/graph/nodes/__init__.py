"""편성 그래프의 노드. build.py 가 여기서 노드를 꺼내 그래프에 올린다.

노드 = (state, deps) -> dict 또는 Command. deps 는 build.py 가 묶어 준다.

파일:
- draft.py    편성 고리 (collect → auto_draft ⇄ validate)
- contact.py  직원 연락 (contact ⇄ contact_wait)
- review.py   저장 · 점장 확인 · 확정
- rows.py     노드들이 같이 쓰는 도우미 (배정 행 다듬기 · 검증 부르기 · 부족 보고)

노드를 나눈 원칙:
- 밖에 흔적을 남기는 일(DB 에 새 행 · LINE 보내기)은 interrupt 가 있는 노드에 두지 않는다.
  interrupt 노드는 재개 때 처음부터 다시 돌기 때문이다 (prototype_aegra E2 「노드는 처음부터 다시 돎」).
  그래서 묻기(contact)와 기다리기(contact_wait)를 나눴고,
  기다리기(manager_review)와 기록(request_redraft · apply_edit · commit_notify)을 나눴다.
  예외는 contact_wait 의 답 기록 하나다 (아직 asked 인 행만 고치므로 다시 돌아도 결과가 같다).
- 밖에 흔적을 남기는 일은 전부 다시 돌아도 결과가 같게 만들었다 (dedupe_key, ON CONFLICT, 「이미 있으면 그 행」).
- 노드 이름은 아즈키 시제품(collect / auto_draft / validate / contact)과
  명세 1-2 표(manager_review / check_approval / commit_notify)를 따른다.

근거: V9_ko 59-121 (1-1 · 1-2 · 1-3, 1-3 이 우선), 466-489
"""
from app.graph.nodes.contact import contact, contact_wait
from app.graph.nodes.draft import auto_draft, collect, validate
from app.graph.nodes.review import (apply_edit, check_approval, commit_notify, manager_review, request_redraft,
                                    save_draft)
from app.graph.nodes.rows import _merge_impossible  # tests/test_review_fixes.py 가 여기서 꺼내 쓴다

__all__ = [
    "_merge_impossible",
    "collect", "auto_draft", "validate", "contact", "contact_wait",
    "save_draft", "manager_review", "check_approval", "request_redraft", "apply_edit", "commit_notify",
]
