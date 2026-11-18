"""편성 그래프의 State 와 JSON ↔ domain 변환 (담당: graph 에이전트).

- 항목 이름은 명세 1-3 표(V9_ko 97-111)를 따른다. 1-3 에 없는 항목은 「(추가)」로 표시했다.
- 값은 전부 JSON 그대로(dict・list・str・int・bool・None). 체크포인터와 Aegra 가 그대로 저장・반환한다.
- 모양을 나중에 바꾸면 대기 중인 thread 의 재개가 깨질 수 있다 (V9_ko 117) → 평평하게, 지우지 말고 더하기만.
"""
from __future__ import annotations

import json
from datetime import date, time
from typing import NotRequired, TypedDict

from app.domain import (Assignment, Availability, Employee, Period, PeriodContext, TimeRule, ValidateOptions,
                        Violation, parse_template_rule)


class GraphInput(TypedDict):
    """입구 API 가 넘기는 값 (V9_ko 98-99). 1기간 = 1 thread (F-10).

    store_id 는 없어도 된다 — app/runner.py 의 start_input 이 {"period_id"} 만 넘긴다 (runtime 에이전트의 가정 신규).
    없으면 shift_periods.store_id 를 쓰고, 있으면 그 기간의 점포와 같은지 확인한다.
    """

    store_id: NotRequired[int]
    period_id: int


class ShiftState(TypedDict, total=False):
    # ---- 입구 API
    store_id: int
    period_id: int
    # ---- collect: 시작 시점에 고정 (F-17, V9_ko 73 「편성 시작 시점에 고정한 점장 지침 버전」)
    period: dict  # (추가) shift_periods 행
    time_rules: list  # (추가) store_time_rules 행들
    llm_all_staff: list  # 직원 (이름 없이 ID 만, MCP) — V9_ko 100
    staff_availability: list  # (추가) 희망 행들 (MCP). 연락에서 「가능」이면 그 칸이 더해진다
    extra_rules: list  # 틀 규칙 [{rule_key, template_key, params, source_text}] — V9_ko 101
    free_text: list  # (추가) 틀에 안 맞는 점장 말. 프롬프트에 그대로 + 화면에 「참고・보장 없음」(V9_ko 478)
    rules_action_id: int | None  # (추가) 고정한 rules_confirmed 행 (1-2 의 guideline_set_id)
    impossible: list  # (추가) 이론상 못 채우는 칸 (impossible_slots, V9_ko 476) — 부족 보고에 붙인다
    # ---- auto_draft
    instruction: str | None  # 재편성 요청의 지시문 — V9_ko 102
    draft_store_shift: list  # 검사 중인 안. 검사가 끝날 때까지 State 에만 (F-1) — V9_ko 103
    shortages: list  # 화면 참고용. 코드 검사는 안 봄 (F-14) — V9_ko 104
    notes: list
    compose_error: str | None  # (추가) 초안 0건의 이유 (timeout / parse_error / invalid_rows ...)
    # ---- validate
    problems: list  # 이번 안의 위반 [V] (API_CONTRACT V 모양) — V9_ko 105
    violation_history: list  # 회차별 위반 [[V], [V], ...]. 묶기(C3)는 compose 직전에 group_history 로 — V9_ko 106
    auto_retry_count: int  # 자동 재편성 횟수. 상한 settings.auto_retry_limit (3) — V9_ko 107
    result: str  # valid / retry / limit_reached / draft_empty — V9_ko 109
    draft_id: int | None  # (추가) DB 에 저장된 지금 안 (검사 뒤에만 생긴다)
    # ---- contact (M-6, 기본 꺼 둠)
    contact_asks: int  # (추가) 지금까지 물어본 횟수 (thread 전체 합)
    contact_asked: list  # (추가) 이미 물어본 [employee_id, work_date, start_time]
    contact_pending: dict | None  # (추가) 지금 기다리는 ContactWait
    # ---- manager_review
    manager_request_count: int  # 점장 재편성 요청 횟수. 상한 settings.manager_request_limit (2) — V9_ko 108
    decision: str | None  # approved / redraft_requested / edit_saved — V9_ko 110
    reviewer_id: int | None  # (추가) 그 결정을 한 관리자 (JWT 의 관리자 ID, V9_ko 54)
    review_input: dict | None  # (추가) 받아들인 resume 값 (instruction / assignments)
    waived_violations: list  # 점장이 허용한 위반 key — V9_ko 111
    blocked: list  # (추가) check_approval 이 막은 위반 [V]
    review_error: str | None  # (추가) 받아들이지 않은 조작의 이유 (재편성 상한 등). 화면에 보인다
    # ---- commit_notify
    notified: bool  # 확정 통지를 보냈는가 (1-2 notified, V9_ko 78)


# ---------------------------------------------------------------- JSON 도우미


def jsonable(v):
    """date・time 등을 문자열로 바꿔 JSON 만 남긴다. LLM・검증이 주는 dict 를 State 에 넣기 전에 통과시킨다."""
    def default(o):
        if isinstance(o, time):
            return o.strftime("%H:%M")
        if isinstance(o, date):
            return o.isoformat()
        return str(o)
    return json.loads(json.dumps(v, ensure_ascii=False, default=default))


def _hm(v: time | None) -> str | None:
    return v.strftime("%H:%M") if v is not None else None


def _t(v: str | None) -> time | None:
    return time.fromisoformat(v) if v else None


def asg_to_dict(a: Assignment) -> dict:
    return {"employee_id": a.employee_id, "work_date": a.work_date.isoformat(), "kind": a.kind,
            "start_time": _hm(a.start_time), "end_time": _hm(a.end_time),
            "break_start": _hm(a.break_start), "break_end": _hm(a.break_end)}


def dict_to_asg(d: dict) -> Assignment:
    return Assignment(int(d["employee_id"]), date.fromisoformat(d["work_date"]), d.get("kind") or "work",
                      _t(d.get("start_time")), _t(d.get("end_time")), _t(d.get("break_start")), _t(d.get("break_end")))


def viol_to_dict(v: Violation) -> dict:
    """domain Violation → API_CONTRACT 의 V 모양. key 는 JSON 으로 바꾼 detail 기준으로 다시 계산한다
    (DB 의 validation_violations 에서 다시 계산한 key 와 같아야 허용(waive)이 맞는 행에 붙는다)."""
    detail = jsonable(v.detail or {})
    nv = Violation(v.check_code, v.severity, v.work_date, v.employee_id, detail)
    return {"key": nv.key, "check_code": nv.check_code, "severity": nv.severity, "waivable": nv.waivable,
            "work_date": nv.work_date.isoformat() if nv.work_date else None, "employee_id": nv.employee_id,
            "detail": detail}


def dict_to_viol(d: dict) -> Violation:
    return Violation(d["check_code"], d["severity"], date.fromisoformat(d["work_date"]) if d.get("work_date") else None,
                     d.get("employee_id"), d.get("detail") or {})


# ---------------------------------------------------------------- PeriodContext (검증・LLM 입력)


def options_from(settings) -> ValidateOptions:
    return ValidateOptions(enable_surplus=settings.enable_surplus,
                           enable_half_leave_check=settings.enable_half_leave_check,
                           enable_regular_availability_warning=settings.enable_regular_availability_warning)


def ctx_from_state(state: ShiftState, settings) -> PeriodContext:
    """State 에 고정한 값만으로 PeriodContext 를 만든다 (DB 를 다시 읽지 않는다 → 재개 뒤에도 같은 입력, F-17)."""
    p = state["period"]
    period = Period(p["period_id"], p["store_id"], date.fromisoformat(p["target_month"]), p["designated_off_days"],
                    p.get("off_request_limit"), p.get("work_request_limit"))
    emps = tuple(Employee(e["employee_id"], e["is_regular"], e["has_qualification"], e["closing_ok"],
                          e.get("weekly_days"), tuple(e["fixed_weekdays"]) if e.get("fixed_weekdays") else None)
                 for e in state["llm_all_staff"])
    av = tuple(Availability(a["employee_id"], date.fromisoformat(a["work_date"]), a["kind"], _t(a.get("start_time")),
                            _t(a.get("end_time"))) for a in state["staff_availability"])
    rules = tuple(TimeRule(r["rule_id"], r["weekday"], _t(r["start_time"]), _t(r["end_time"]), r["min_headcount"],
                           r.get("min_qualified", 0)) for r in state["time_rules"])
    tmpl = tuple(parse_template_rule(r["rule_key"], r["template_key"], r["params"], r.get("source_text", ""))
                 for r in state.get("extra_rules") or [])
    return PeriodContext(period, emps, av, rules, tmpl, tuple(state.get("free_text") or []), options_from(settings))


# ---------------------------------------------------------------- interrupt 값 (API_CONTRACT 「편성 제어」)


def review_wait(state: ShiftState, settings) -> dict:
    """ReviewWait (API_CONTRACT). 키를 더하거나 빼지 않는다 — runtime・api 에이전트가 이 모양에 기댄다.
    받아들이지 않은 조작의 이유(review_error)는 모양을 지키려고 notes 끝에 붙인다."""
    notes = list(state.get("notes") or [])
    if state.get("review_error"):
        notes.append(state["review_error"])
    return {
        "type": "review",
        "draft_id": state.get("draft_id"),
        "result": state.get("result"),
        "violations": list(state.get("problems") or []),
        "blocked": list(state.get("blocked") or []),
        "shortages": list(state.get("shortages") or []),
        "notes": notes,
        "free_text_guidelines": list(state.get("free_text") or []),
        "auto_retry_count": state.get("auto_retry_count", 0),
        "manager_request_count": state.get("manager_request_count", 0),
        "manager_request_limit": settings.manager_request_limit,
    }
