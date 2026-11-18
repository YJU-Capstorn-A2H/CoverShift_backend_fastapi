"""틀 규칙 검사 T-1 · T-2 (M-2, V9_ko 478, 965-975). T-3(min_headcount)은 V-9 의 필요 인원 계산(_index.need_at)에 들어가 있다.

- 위반 코드는 RULE_VIOLATION (5-1 V9_ko 954). 점장이 확인한 틀 = 「보장」이므로 required, 예외 허용 가능.
  (5-1 의 「status=trial 은 경고만」은 MVP 틀에 status 가 없어서 쓰지 않는다.)
- detail["rule_key"] 를 넣어 Violation.key 에 규칙이 들어가게 한다.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date, timedelta

from app.domain import MaxConsecutiveParams, NotAvailableParams, Violation

from ._index import ContextIndex, Work, fmt
from .rules import stays_to_close


# ---------------------------------------------------------------- T-1 not_available (V9_ko 970)
def check_not_available(idx: ContextIndex, work: Sequence[Work]) -> list[Violation]:
    """「이 사람은 그 요일(또는 그 날) 불가」. 그 사람의 출근 배정이 그 날에 있으면 위반.
    유급(paid_leave) 행은 출근이 아니므로 위반이 아니다 (F-16)."""
    out: list[Violation] = []
    for r in idx.templates:
        if r.template_key != "not_available":
            continue
        p: NotAvailableParams = r.params  # type: ignore[assignment]
        seen: set[date] = set()
        for w in work:
            if w.employee_id != p.staff_id or w.work_date in seen:
                continue
            hit = (p.date == w.work_date) if p.date is not None else (p.weekday == w.work_date.isoweekday())
            if hit:
                seen.add(w.work_date)
                out.append(Violation("RULE_VIOLATION", "required", w.work_date, w.employee_id, {
                    "rule_key": r.rule_key, "template_key": r.template_key,
                    "assigned_start": fmt(w.src.start_time), "assigned_end": fmt(w.src.end_time),
                }))
    return out


# ---------------------------------------------------------------- T-2 max_consecutive (V9_ko 967, G11)
def check_max_consecutive(idx: ContextIndex, work: Sequence[Work]) -> list[Violation]:
    """연속 출근(shift="any") 또는 연속 늦은 근무(shift="late") 상한.

    - G11: late = 그 날 폐점 시각에 끝나는 근무. 판정은 V-4 와 같은 stays_to_close (마지막 칸에 걸치도록 끝남).
    - 「연속」= 달력상 붙은 날. 유급・휴무・배정 없음은 연속을 끊는다 (유급은 출근이 아님, F-16).
    - 기간 안의 날짜만 본다. 지난달 말의 근무는 ctx 에 없어서 이어 세지 못한다 (알려진 한계).
    - 연속 구간 하나가 limit 을 넘으면 위반 1건. work_date = 처음으로 limit 을 넘은 날 (limit+1 번째 날),
      detail 에 구간 처음・끝・길이. "start" 키는 쓰지 않는다 (key 를 코드|날짜|직원|규칙 으로 두기 위해).
    """
    out: list[Violation] = []
    for r in idx.templates:
        if r.template_key != "max_consecutive":
            continue
        p: MaxConsecutiveParams = r.params  # type: ignore[assignment]
        days: dict[int, set[date]] = defaultdict(set)
        for w in work:
            if w.work_date not in idx.date_set:
                continue
            if p.staff_id is not None and w.employee_id != p.staff_id:
                continue
            if p.shift == "late" and not stays_to_close(idx, w):
                continue
            days[w.employee_id].add(w.work_date)
        for emp in sorted(days):
            ds = sorted(days[emp])
            run_start = prev = ds[0]
            run_len = 1
            for d in ds[1:] + [None]:  # 끝에 None 을 붙여 마지막 구간도 닫는다
                if d is not None and d == prev + timedelta(days=1):
                    run_len += 1
                    prev = d
                    continue
                if run_len > p.limit:
                    out.append(Violation("RULE_VIOLATION", "required", run_start + timedelta(days=p.limit), emp, {
                        "rule_key": r.rule_key, "template_key": r.template_key, "shift": p.shift, "limit": p.limit,
                        "run_start": run_start.isoformat(), "run_end": prev.isoformat(), "run_length": run_len,
                    }))
                if d is not None:
                    run_start = prev = d
                    run_len = 1
    return out
