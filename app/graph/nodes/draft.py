"""편성 고리: collect → auto_draft ⇄ validate.

    collect → auto_draft ─────(읽을 수 있는 초안)─────→ validate ──→ contact 또는 save_draft
                ↑  └─(초안 0건)─┘                          │
                └──────────────(required 위반)─────────────┘

되돌아가는 두 화살표는 settings.auto_retry_limit 번까지만 돈다.
"""
from __future__ import annotations

import logging

from langgraph.types import Command

from app.domain import parse_template_rule
from app.graph.nodes.contact import CONTACT_ASK_LIMIT
from app.graph.nodes.rows import has_required, normalize_rows, run_validation, shortage_report, with_paid_leave
from app.graph.state import ShiftState, ctx_from_state, dict_to_asg, dict_to_viol, jsonable
from app.repo import drafts as repo

log = logging.getLogger("graph")


# ---------------------------------------------------------------- collect


def collect(state: ShiftState, deps) -> dict:
    """편성에 쓸 자료를 읽어 State 에 고정한다: 직원 정보(이름 없음) · 기간 · 시간대 규칙 · 마지막으로 확인된 틀 규칙.

    근거: F-17, V9_ko 73, M-7 (직원 정보는 이름 열을 못 읽는 DB 롤로 읽는다 — app/mcp_tools.py)
    """
    period_id = state["period_id"]
    with deps.connect() as conn:
        period = repo.load_period(conn, period_id)
        store_id = period["store_id"]  # 점포는 기간에서 정한다. 입력에 없어도 된다 (GraphInput 참조)
        expected_store_id = state.get("store_id")
        if expected_store_id is not None and expected_store_id != store_id:
            raise ValueError(f"period {period_id} 는 store {expected_store_id} 의 기간이 아니다")
        time_rules = repo.load_time_rules(conn, store_id)
        confirmed_rules = repo.latest_rules(conn, period_id)
    staff = deps.get_staff_info(period_id)

    extra_rules = jsonable(confirmed_rules["rules"]) if confirmed_rules else []
    for rule in extra_rules:
        # 틀에 맞지 않는 규칙이면 여기서 멈춘다. 입구 API 가 이미 검사했으므로 걸리면 버그다. 근거: T-4, V9_ko 1170
        parse_template_rule(rule["rule_key"], rule["template_key"], rule["params"], rule.get("source_text", ""))

    collected: ShiftState = {
        "period": jsonable(period), "time_rules": jsonable(time_rules),
        "llm_all_staff": jsonable(staff["employees"]), "staff_availability": jsonable(staff["availability"]),
        "extra_rules": extra_rules,
        "free_text": list(confirmed_rules["free_text"]) if confirmed_rules else [],
        "rules_action_id": confirmed_rules["action_id"] if confirmed_rules else None,
    }
    impossible = jsonable(deps.impossible_slots(ctx_from_state(collected, deps.settings)))

    log.info("collect period=%s staff=%d rules=%d impossible=%d", period_id, len(collected["llm_all_staff"]),
             len(extra_rules), len(impossible))
    return {**collected, "store_id": store_id, "impossible": impossible, **_empty_progress()}


def _empty_progress() -> dict:
    """편성을 시작할 때의 진행 상태. 아직 아무것도 하지 않은 값들이다."""
    return {
        # 초안
        "instruction": None, "draft_store_shift": [], "shortages": [], "notes": [], "compose_error": None,
        # 검증과 자동 재편성
        "problems": [], "violation_history": [], "auto_retry_count": 0, "result": "", "draft_id": None,
        # 직원 연락
        "contact_asks": 0, "contact_asked": [], "contact_pending": None,
        # 점장 확인
        "manager_request_count": 0, "decision": None, "reviewer_id": None, "review_input": None,
        "waived_violations": [], "blocked": [], "review_error": None, "notified": False,
    }


# ---------------------------------------------------------------- auto_draft


def auto_draft(state: ShiftState, deps) -> Command:
    """LLM 에게 편성을 1번 시킨다. DB 에는 쓰지 않는다. 결과는 반드시 validate 를 거친다.

    읽을 수 있는 초안이 나오지 않은 것(실패 · 0행 · 저장할 수 없는 행)을 「초안 0건」이라 부르고,
    자동 재편성 1회로 센다.

    근거: V9_ko 482-488, 83 (반드시 검증), F-1 (DB 에 쓰지 않음), F-4 (초안 0건)
    """
    settings = deps.settings
    retries_used = state.get("auto_retry_count", 0)
    composed = _compose(state, deps)
    rows, error = _readable_rows(composed, state)

    if error is None:
        log.info("auto_draft ok rows=%d (auto_retry=%d)", len(rows), retries_used)
        return Command(goto="validate", update={
            "draft_store_shift": with_paid_leave(rows, state["staff_availability"]),
            "shortages": shortage_report(state, composed.shortages),
            "notes": [str(note) for note in composed.notes or []], "compose_error": None})

    if retries_used < settings.auto_retry_limit:
        log.info("auto_draft 초안 0건 (%s) → 재편성 %d/%d", error, retries_used + 1, settings.auto_retry_limit)
        # draft_store_shift 는 그대로 둔다. 다음 호출의 「직전 안」이 마지막으로 읽을 수 있었던 안이 되게.
        return Command(goto="auto_draft", update={
            "auto_retry_count": retries_used + 1, "result": "draft_empty", "compose_error": error})

    # (가정 신규) 상한에서도 초안 0건이면 유급 행만 있는 빈 안을 검사・저장해서 승인 화면으로 보낸다.
    # 점장이 직접 편집(수동 편성)하거나 재편성을 요청한다. 승인 화면(ReviewWait)에는 늘 draft_id 가 있어야 한다.
    # 근거: V9_ko 517 「아즈키 안: 0건이면 실패 → 수동 편성 + 점장에게 메시지」
    log.warning("auto_draft 초안 0건 (%s) — 자동 재편성 상한 %d 도달", error, settings.auto_retry_limit)
    return Command(goto="validate", update={
        "draft_store_shift": with_paid_leave([], state["staff_availability"]),
        "shortages": shortage_report(state, []), "compose_error": error,
        "notes": [f"자동 편성이 초안을 만들지 못했다 ({error}). 직접 편집하거나 재편성을 요청한다."]})


def _compose(state: ShiftState, deps):
    """LLM 호출 1번. 재시도일 때의 입력 = 직전 안(근무 행만) + 지금까지의 위반 이력 + 점장 지시문.

    근거: F-5, V9_ko 486
    """
    ctx = ctx_from_state(state, deps.settings)
    previous_work_rows = [dict_to_asg(row) for row in state.get("draft_store_shift") or [] if row["kind"] == "work"]
    violation_rounds = [[dict_to_viol(violation) for violation in one_round]
                        for one_round in state.get("violation_history") or []]
    return deps.composer.compose(ctx, previous=previous_work_rows or None,
                                 history=deps.group_history(violation_rounds), instruction=state.get("instruction"))


def _readable_rows(composed, state: ShiftState) -> tuple[list[dict], str | None]:
    """LLM 결과에서 근무 행을 꺼낸다. (행 목록, None) 또는 초안 0건이면 ([], 이유).

    유급 행은 코드가 싣기 때문에(F-16) LLM 이 낸 것은 근무 행만 쓴다.
    """
    if not composed.ok:
        return [], composed.error or "failed"
    work_assignments = [a for a in composed.assignments if getattr(a, "kind", "work") == "work"]
    try:
        rows = normalize_rows(work_assignments, state)
    except ValueError as error:
        return [], f"invalid_rows: {error}"  # 읽을 수 없음
    if not rows:
        return [], "empty"  # 0행
    return rows, None


# ---------------------------------------------------------------- validate


def validate(state: ShiftState, deps) -> Command:
    """초안을 코드로 검증하고 다음 갈 곳을 정한다.

    - required 위반이 있고 상한 전이면 auto_draft 로 되돌린다.
    - 상한에 닿으면 위반을 안고 승인 화면 쪽으로 보낸다.

    근거: V9_ko 466-479, F-2 (자동 상한), F-3 (상한이면 위반을 안고 승인 화면)
    """
    settings = deps.settings
    problems = run_validation(state, state["draft_store_shift"], deps)
    update: dict = {"problems": problems, "blocked": [], "review_error": None}

    if state.get("compose_error"):
        # 상한에서 초안 0건이었던 빈 안이다 (auto_draft 참조). 빈 안의 위반은 LLM 에게 줄 이력에 넣지 않는다.
        update["result"] = "draft_empty"
        return Command(goto="save_draft", update=update)

    update["violation_history"] = list(state.get("violation_history") or []) + [problems]
    retries_used = state.get("auto_retry_count", 0)

    if has_required(problems) and retries_used < settings.auto_retry_limit:
        required_count = sum(problem["severity"] == "required" for problem in problems)
        log.info("validate required 위반 %d건 → 재편성 %d/%d", required_count,
                 retries_used + 1, settings.auto_retry_limit)
        update["result"] = "retry"
        update["auto_retry_count"] = retries_used + 1
        return Command(goto="auto_draft", update=update)

    update["result"] = "limit_reached" if has_required(problems) else "valid"
    log.info("validate result=%s 위반 %d건", update["result"], len(problems))
    return Command(goto=_after_check(state, problems, settings), update=update)


def _after_check(state: ShiftState, problems: list[dict], settings) -> str:
    """검증이 끝난 안이 갈 곳. 인원 부족이 있고 직원 연락을 켰으면 contact, 아니면 save_draft."""
    contact_allowed = settings.enable_contact and state.get("contact_asks", 0) < CONTACT_ASK_LIMIT
    has_shortage = any(problem["check_code"] == "HEADCOUNT_SHORTAGE" for problem in problems)
    return "contact" if contact_allowed and has_shortage else "save_draft"
