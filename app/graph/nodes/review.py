"""저장 · 점장 확인 · 확정.

    save_draft → manager_review ⏸ ─┬─ 승인 ────────→ check_approval ─┬→ commit_notify → 끝
                    ↑               ├─ 재편성 요청 ─→ request_redraft  └→ (막힘) manager_review
                    │               └─ 편집 저장 ───→ apply_edit → save_draft
                    └─ 받을 수 없는 조작이면 다시 기다린다

manager_review 는 State 만 바꾼다. DB 기록은 그다음 노드(request_redraft · apply_edit · commit_notify)가 한다.
interrupt 가 있는 노드는 재개 때 처음부터 다시 돌기 때문이다.
"""
from __future__ import annotations

import logging

from langgraph.types import Command, interrupt

from app.graph.nodes.rows import (normalize_rows, result_without_retry, run_validation, shortage_report, to_int,
                                  with_paid_leave)
from app.graph.state import ShiftState, dict_to_viol, review_wait, viol_to_dict
from app.repo import drafts as repo

log = logging.getLogger("graph")


# ---------------------------------------------------------------- 저장


def save_draft(state: ShiftState, deps) -> dict:
    """검사가 끝난 안을 위반과 함께 DB 에 저장한다. 부모(parent) = 바로 전에 저장된 안.

    근거: F-1, V9_ko 93, G4 (위반도 같이 저장)
    """
    previous_draft_id = state.get("draft_id")
    with deps.connect() as conn:
        draft_id = repo.save_draft(conn, period_id=state["period_id"], assignments=state["draft_store_shift"],
                                   violations=state["problems"], parent_draft_id=previous_draft_id)
    log.info("save_draft draft_id=%s parent=%s result=%s", draft_id, previous_draft_id, state.get("result"))
    return {"draft_id": draft_id}


# ---------------------------------------------------------------- 점장 확인


def manager_review(state: ShiftState, deps) -> Command:
    """점장의 조작을 기다린다 (interrupt). 재개 값 = {"decision", "manager_id", "draft_id", ...} (API_CONTRACT).

    지금 안이 아닌 draft_id 로 온 조작(늦게 온 클릭 · 두 번 클릭)은 아무것도 바꾸지 않고 다시 기다린다.

    근거: V9_ko 86, F-7 · V9_ko 54 (승인자 ID 는 반드시 남긴다)
    """
    settings = deps.settings
    resumed = interrupt(review_wait(state, settings))
    reply = resumed if isinstance(resumed, dict) else {}

    if to_int(reply.get("draft_id")) != state.get("draft_id"):
        log.info("manager_review 지금 안이 아닌 draft_id=%r → 무시", reply.get("draft_id"))
        return Command(goto="manager_review")
    manager_id = to_int(reply.get("manager_id"))
    if manager_id is None:
        return _wait_again("manager_id 가 없는 조작은 받지 않는다")

    reviewer = {"reviewer_id": manager_id, "review_error": None}
    decision = reply.get("decision")
    if decision == "approved":
        return _approved(reply, reviewer)
    if decision == "redraft_requested":
        return _redraft_requested(reply, reviewer, state, settings)
    if decision == "edit_saved":
        return _edit_saved(reply, reviewer, state)
    return _wait_again(f"알 수 없는 조작: {decision!r}")


def _wait_again(error: str) -> Command:
    """조작을 받지 않고 이유를 화면에 남긴 채 다시 기다린다."""
    return Command(goto="manager_review", update={"review_error": error})


def _approved(reply: dict, reviewer: dict) -> Command:
    waived_keys = sorted({str(key) for key in reply.get("waived_keys") or []})
    return Command(goto="check_approval", update={
        **reviewer, "decision": "approved", "waived_violations": waived_keys, "review_input": None})


def _redraft_requested(reply: dict, reviewer: dict, state: ShiftState, settings) -> Command:
    """재편성 요청. 점장 요청 횟수 상한을 넘으면 받지 않는다.

    근거: F-2, V9_ko 479
    """
    if state.get("manager_request_count", 0) >= settings.manager_request_limit:
        return _wait_again(f"재편성 요청은 {settings.manager_request_limit}회까지다. 승인하거나 직접 편집한다.")
    instruction = reply.get("instruction")
    instruction = instruction.strip() if isinstance(instruction, str) and instruction.strip() else None
    return Command(goto="request_redraft", update={
        **reviewer, "decision": "redraft_requested", "blocked": [], "review_input": {"instruction": instruction}})


def _edit_saved(reply: dict, reviewer: dict, state: ShiftState) -> Command:
    try:
        rows = normalize_rows(reply.get("assignments"), state)
    except ValueError as error:
        return _wait_again(f"편집 내용을 받지 않았다: {error}")
    return Command(goto="apply_edit", update={
        **reviewer, "decision": "edit_saved", "blocked": [], "review_input": {"assignments": rows}})


# ---------------------------------------------------------------- 점장 조작의 뒤처리


def check_approval(state: ShiftState, deps) -> Command:
    """승인 전 마지막 검사. 예외 불가 위반이나 허용하지 않은 required 위반이 남았으면 확정하지 않고 승인 화면으로 되돌린다.

    근거: F-8, V9_ko 87
    """
    violations = [dict_to_viol(problem) for problem in state.get("problems") or []]
    waived_keys = set(state.get("waived_violations") or [])
    blocked = deps.blocking(violations, waived_keys)
    if blocked:
        log.info("check_approval 막힘 %d건", len(blocked))
        return Command(goto="manager_review", update={"blocked": [viol_to_dict(v) for v in blocked]})
    return Command(goto="commit_notify", update={"blocked": []})


def request_redraft(state: ShiftState, deps) -> dict:
    """점장의 재편성 요청을 기록한다. 다음은 auto_draft.

    (가정 신규) 자동 재편성 횟수(auto_retry_count)를 0으로 되돌린다. 새 지시로 만드는 안도 자동으로 고칠 기회를
    처음부터 다시 준다. 명세는 「자동 3회 · 점장 요청 2회를 따로 센다」까지만 쓴다.
    최악의 LLM 호출 수 = (1 + 3) × (1 + 2) = 12회.

    근거: F-2, F-7, V9_ko 479
    """
    instruction = (state.get("review_input") or {}).get("instruction")
    with deps.connect() as conn:
        repo.record_action_once(conn, period_id=state["period_id"], draft_id=state["draft_id"],
                                manager_id=state["reviewer_id"], action="redraft_requested",
                                instruction=instruction)
    return {"manager_request_count": state.get("manager_request_count", 0) + 1, "instruction": instruction,
            "auto_retry_count": 0, "waived_violations": [], "blocked": []}


def apply_edit(state: ShiftState, deps) -> dict:
    """점장이 편집한 안을 다시 검증하고 기록한다. 다음은 save_draft (새 안으로 저장, 부모 = 지금 안).

    점장 요청 횟수는 세지 않는다.

    근거: F-15, G17
    """
    rows = with_paid_leave(state["review_input"]["assignments"], state["staff_availability"])
    problems = run_validation(state, rows, deps)
    with deps.connect() as conn:
        repo.record_action_once(conn, period_id=state["period_id"], draft_id=state["draft_id"],
                                manager_id=state["reviewer_id"], action="edit_saved")
    return {"draft_store_shift": rows, "problems": problems, "compose_error": None,
            "violation_history": list(state.get("violation_history") or []) + [problems],
            "result": result_without_retry(problems),
            "shortages": shortage_report(state, []), "notes": [], "waived_violations": [], "blocked": []}


def commit_notify(state: ShiftState, deps) -> dict:
    """확정 저장 + 직원마다 통지 1건을 발송함에 「대기」로 적는다. 한 트랜잭션.

    여기서는 LINE 을 부르지 않는다. 실제 발송은 커밋 뒤 발송기(app/line_dispatcher.py)가 한다
    → LINE 이 실패해도 확정은 남고, 확정이 취소되면 통지도 같이 사라진다 (A-21).

    다시 돌아도 결과가 같다:
    - 확정은 「이미 확정이면 그대로」
    - 승인 기록은 부분 유일 인덱스 + ON CONFLICT
    - 통지는 dedupe_key = confirm:{draft_id}:{employee_id} (line_outbox)

    근거: F-9, A-21, V9_ko 88
    """
    draft_id, manager_id = state["draft_id"], state["reviewer_id"]
    waivable_keys = {problem["key"] for problem in state.get("problems") or [] if problem["waivable"]}
    waived_keys = waivable_keys & set(state.get("waived_violations") or [])
    month = state["period"]["target_month"][:7]

    with deps.connect() as conn:
        repo.confirm_draft(conn, period_id=state["period_id"], draft_id=draft_id, manager_id=manager_id,
                           waived_keys=sorted(waived_keys))
        confirmed_rows = repo.load_assignments(conn, draft_id)  # 통지는 State 가 아니라 DB 에 확정된 그대로
        newly_queued = 0
        for employee in state["llm_all_staff"]:
            employee_id = employee["employee_id"]
            newly_queued += deps.enqueue(conn, store_id=state["store_id"], employee_id=employee_id,
                                         kind="shift_confirmed", dedupe_key=f"confirm:{draft_id}:{employee_id}",
                                         body=_confirmation_body(employee_id, confirmed_rows, month, draft_id))
    log.info("commit_notify draft_id=%s confirmed_by=%s 통지 %d건(새로 적은 것)", draft_id, manager_id, newly_queued)
    return {"notified": True}


def _confirmation_body(employee_id: int, confirmed_rows: list[dict], month: str, draft_id: int) -> dict:
    """한 직원에게 보낼 확정 통지 내용: 그 사람의 출근일과 유급일."""
    own_rows = [row for row in confirmed_rows if row["employee_id"] == employee_id]
    work_days = [{"work_date": row["work_date"], "start_time": row["start_time"], "end_time": row["end_time"]}
                 for row in own_rows if row["kind"] == "work"]
    paid_leave_days = [row["work_date"] for row in own_rows if row["kind"] == "paid_leave"]
    return {"text": f"{month} 시프트가 확정되었습니다. 출근 {len(work_days)}일.", "draft_id": draft_id,
            "work_days": work_days, "paid_leave_days": paid_leave_days}
