"""점장 지시 → 틀 규칙 (M-2, W-9, G18, V9_ko 446 · 478).

미리보기: LLM 1번(그래프 밖), 저장 안 함. 확정: 점장이 본 규칙을 코드가 다시 검사(parse_template_rule)하고 저장.
LLM 이 낸 규칙도, 화면이 보낸 규칙도 믿지 않는다 — 틀 검사를 통과한 것만 manager_actions 에 들어간다 (V9_ko 1170).
"""
from __future__ import annotations

from app.privacy import mask_names, store_employees

import logging
from datetime import date

from fastapi import APIRouter

from app.domain import TemplateRule, parse_template_rule
from app.repo import actions as action_repo
from app.repo import employees as emp_repo
from app.repo import periods as period_repo

from .deps import ComposerDep, Conn, Manager
from .errors import ApiError
from .routes_settings import load_period
from .schemas import ConfirmIn, InstructionIn
from .serialize import out

router = APIRouter(prefix="/api/shift-periods/{period_id}/instructions", tags=["instructions"])
log = logging.getLogger("api")


def rule_out(r: TemplateRule) -> dict:
    """화면 · DB 에 내보내는 Rule 모양. 시각은 계약의 HH:MM 으로 (parse_template_rule 이 그대로 다시 읽는다)."""
    return {"rule_key": r.rule_key, "template_key": r.template_key,
            "params": out(r.params.model_dump(exclude_none=True)), "source_text": r.source_text}


def scope_problem(r: TemplateRule, employee_ids: set[int], start: date, end: date) -> str | None:
    """틀 모양 검사(parse_template_rule) 다음의 점포 · 기간 검사. 문제가 있으면 사유 문장.

    (가정 신규) parse_template_rule 은 staff_id 가 이 점포 직원인지, date 가 이 달인지 보지 않는다.
    남의 점포 직원 ID 나 다른 달 날짜가 틀 규칙으로 저장되지 않게 여기서 막는다 (A-5).
    """
    staff_id = getattr(r.params, "staff_id", None)
    if staff_id is not None and staff_id not in employee_ids:
        return f"staff_id {staff_id} 는 이 점포의 직원이 아닙니다"
    d = getattr(r.params, "date", None)
    if d is not None and not (start <= d < end):
        return f"date {d} 는 이 기간의 날짜가 아닙니다"
    return None


@router.post("/preview")
def preview(period_id: int, body: InstructionIn, m: Manager, conn: Conn, composer: ComposerDep):
    p = load_period(conn, m, period_id)
    ctx = period_repo.light_context(conn, m.store_id, p)
    try:
        # 리뷰 A #5: 실명을 ID 로 바꾼 문장만 LLM 에 보낸다 (V9_ko 1148). 화면에도 바뀐 문장이 돌아간다
        res = composer.instruction_to_rules(ctx, mask_names(body.text, store_employees(conn, m.store_id)))
    except Exception as e:  # noqa: BLE001 — LLM 쪽 예외 형은 llm 에이전트 소유
        log.warning("instruction_to_rules 실패 period=%s: %r", period_id, e)
        raise ApiError(502, "LLM_ERROR", "지시를 틀 규칙으로 바꾸지 못했습니다. 잠시 뒤 다시 시도해 주세요")
    start, end = period_repo.month_range(p["target_month"])
    ids = {e.employee_id for e in ctx.employees}
    rules, rejected, keys = [], list(res.rejected), set()
    for r in res.rules:
        why = scope_problem(r, ids, start, end) or (f"rule_key {r.rule_key} 가 겹칩니다" if r.rule_key in keys else None)
        if why:
            rejected.append({"raw": rule_out(r), "reason": why})
            continue
        keys.add(r.rule_key)
        rules.append(rule_out(r))
    return out({"rules": rules, "free_text": list(res.free_text), "rejected": rejected})


@router.post("/confirm", status_code=201)
def confirm(period_id: int, body: ConfirmIn, m: Manager, conn: Conn):
    p = load_period(conn, m, period_id)
    start, end = period_repo.month_range(p["target_month"])
    ids = emp_repo.employee_ids(conn, m.store_id)
    checked, keys = [], set()
    for i, r in enumerate(body.rules):
        try:
            tr = parse_template_rule(r.rule_key, r.template_key, r.params, r.source_text)
        except ValueError as e:  # pydantic ValidationError 도 ValueError 의 하위 클래스
            raise ApiError(422, "INVALID_RULE", f"rules[{i}] ({r.rule_key}): 틀에 맞지 않습니다 — {_short(e)}")
        why = scope_problem(tr, ids, start, end)
        if why:
            raise ApiError(422, "INVALID_RULE", f"rules[{i}] ({r.rule_key}): {why}")
        if tr.rule_key in keys:
            raise ApiError(422, "INVALID_RULE", f"rules[{i}]: rule_key {tr.rule_key} 가 겹칩니다")  # 기간 안에서 유일
        keys.add(tr.rule_key)
        checked.append(rule_out(tr))
    action_id = action_repo.insert_rules_confirmed(conn, period_id=period_id, manager_id=m.manager_id,
                                                   text=body.text, rules=checked,
                                                   free_text=[mask_names(t, store_employees(conn, m.store_id)) for t in body.free_text])
    return {"action_id": action_id}


@router.get("")
def latest(period_id: int, m: Manager, conn: Conn):
    load_period(conn, m, period_id)
    a = action_repo.latest_rules_confirmed(conn, period_id)
    if a is None:
        return None
    rules = a["rules"] or {}
    return out({"action_id": a["action_id"], "text": a["instruction"], "rules": rules.get("rules", []),
                "free_text": rules.get("free_text", []), "created_at": a["created_at"]})


def _short(e: Exception) -> str:
    s = str(e).replace("\n", " ")
    return s if len(s) <= 300 else s[:300] + "…"
