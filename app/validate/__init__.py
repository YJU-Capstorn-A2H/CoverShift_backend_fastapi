"""검증(validate) — 담당: validate 에이전트. 서명은 바꾸지 않는다 (graph 가 이 이름으로 부른다).

코드 검증이 유일한 보증이다. LLM 출력은 그대로 믿지 않는다 (V9_ko 1185).
규칙 표 = V9_ko 466-479 (REQUIREMENTS.md V-1~V-10, T-1~T-4). 세부 구현은 하위 모듈:
- _index.py    30분 칸 · 영업시간(G6) · 필요 인원(V-9/T-3) · 이론상 최대 인원
- rules.py     V-1 ~ V-10
- templates.py T-1 · T-2 (T-4 재검사는 _index.checked_templates)
- history.py   group_history(C3) · blocking(check_approval)
"""
from __future__ import annotations

from collections.abc import Sequence

from app.domain import Assignment, PeriodContext, Violation, ViolationGroup

from . import rules as _r
from . import templates as _t
from ._index import ContextIndex, coverage, split_assignments
from .history import blocking, group_history

__all__ = ["validate_draft", "impossible_slots", "group_history", "blocking"]


def validate_draft(ctx: PeriodContext, assignments: Sequence[Assignment]) -> list[Violation]:
    """편성안 1개를 검사해 위반 목록을 돌려준다 (순서: 규칙 순 → 날짜 → 시각).

    - 틀 규칙이 틀렸으면(T-4) ValueError — 조용히 건너뛰지 않는다.
    - ctx 에 없는 직원 ID 의 배정은 건너뛴다 (LLM 층이 먼저 버린다. _index.split_assignments 참고).
    - V-6 · V-7 · V-10 은 ctx.options 로 켰을 때만 (기본 꺼 둠, G3 · G5).
    """
    idx = ContextIndex(ctx)
    known, work = split_assignments(idx, assignments)
    by_date = coverage(work)
    opt = ctx.options
    out: list[Violation] = []
    out += _r.check_regular_present(idx, by_date)          # V-1
    out += _r.check_qualified(idx, by_date)                # V-2
    out += _r.check_part_availability(idx, work)           # V-3
    out += _r.check_closing(idx, work)                     # V-4
    out += _r.check_regular_days(idx, work)                # V-5
    if opt.enable_half_leave_check:
        out += _r.check_half_leave(idx, work)              # V-6
    if opt.enable_regular_availability_warning:
        out += _r.check_regular_availability(idx, work)    # V-7
    out += _r.check_double_booking(known)                  # V-8
    out += _r.check_headcount(idx, by_date, surplus=opt.enable_surplus)  # V-9 (+ T-3), V-10
    out += _t.check_not_available(idx, work)               # T-1
    out += _t.check_max_consecutive(idx, work)             # T-2
    return out


def impossible_slots(ctx: PeriodContext) -> list[dict]:
    """이론상 못 채우는 최소 인원 칸. 검사에서 빼고 부족 보고로 (V9_ko 476).

    칸(30분) 1개 = dict 1개: {work_date, time, start_time, end_time, need, max_possible, severity, source, rule_key, reason}.
    need 는 V-9 와 같은 값(지시 틀이 덮는 칸은 틀의 숫자). 이어지는 칸을 하나로 합치지는 않는다 (화면 쪽에서 합친다).
    """
    return _r.impossible(ContextIndex(ctx))
