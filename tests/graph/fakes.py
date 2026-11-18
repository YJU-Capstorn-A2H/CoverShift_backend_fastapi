"""그래프 시험용 작은 가짜 (진짜 app.validate / app.llm 은 다른 에이전트가 쓰는 중).

가짜 검증의 규칙(시험에서만 쓰는 표시):
  근무 시작 10:00 → REGULAR_ABSENT (required, 예외 허용 가능)
  근무 시작 11:00 → QUAL_MISSING   (required, 예외 불가)
  근무 시작 12:00 → HEADCOUNT_SHORTAGE (warning)
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date, time

from app.domain import Assignment, ComposeResult, NON_WAIVABLE_CODES, Violation, ViolationGroup
from app.graph_seung.deps import default_deps

D2, D3, D4 = date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4)

BASE = [Assignment(1, D2, "work", time(9), time(18))]
MARK = {
    "absent": Assignment(2, D3, "work", time(10), time(15)),
    "qual": Assignment(1, D4, "work", time(11), time(15)),
    "warn": Assignment(4, D3, "work", time(12), time(16)),
}
CODES = {time(10): ("REGULAR_ABSENT", "required"), time(11): ("QUAL_MISSING", "required"),
         time(12): ("HEADCOUNT_SHORTAGE", "warning")}


def draft(*marks: str, shortages=None, notes=None) -> ComposeResult:
    return ComposeResult(ok=True, assignments=BASE + [MARK[m] for m in marks], shortages=shortages or [],
                         notes=notes or [])


def empty(error: str = "timeout") -> ComposeResult:
    return ComposeResult(ok=False, error=error)


def marker_validate(ctx, assignments) -> list[Violation]:
    out = []
    for a in assignments:
        if a.kind == "work" and a.start_time in CODES:
            code, sev = CODES[a.start_time]
            out.append(Violation(code, sev, a.work_date, a.employee_id, {"time": a.start_time.strftime("%H:%M")}))
    return out


def spec_blocking(violations, waived_keys) -> list[Violation]:
    """V9_ko 87・479 그대로: 예외 불가 코드 전부 + 허용 안 된 required."""
    return [v for v in violations
            if v.check_code in NON_WAIVABLE_CODES or (v.severity == "required" and v.key not in waived_keys)]


def simple_group_history(rounds) -> list[ViolationGroup]:
    seen: dict[str, ViolationGroup] = {}
    for i, rnd in enumerate(rounds, start=1):
        for v in rnd:
            g = seen.get(v.key)
            seen[v.key] = ViolationGroup(v.key, v.check_code, v.severity, ((g.rounds if g else ()) + (i,)), v)
    return list(seen.values())


class ScriptedComposer:
    """compose 를 부를 때마다 script 를 하나씩 꺼낸다 (끝나면 마지막 것을 되풀이). 항목은 ComposeResult 또는 ctx -> ComposeResult."""

    def __init__(self, script, on_compose: Callable[[int], None] | None = None):
        self.script = list(script)
        self.calls: list[dict] = []
        self.on_compose = on_compose

    def compose(self, ctx, *, previous, history, instruction):
        self.calls.append({"ctx": ctx, "previous": previous, "history": list(history), "instruction": instruction})
        if self.on_compose:
            self.on_compose(len(self.calls))
        item = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        return item(ctx) if callable(item) else item

    def instruction_to_rules(self, ctx, text):  # 그래프는 부르지 않는다 (G18: 입구 API 몫)
        raise AssertionError("graph must not call instruction_to_rules")


def make_deps(composer, *, validate=marker_validate, impossible=lambda ctx: [], **kw):
    return default_deps(composer=composer, validate_draft=validate, impossible_slots=impossible,
                        group_history=simple_group_history, blocking=spec_blocking, **kw)


# ---------------------------------------------------------------- 실행 도우미

def cfg(tid: str) -> dict:
    return {"configurable": {"thread_id": tid}}


def waiting(graph, tid: str):
    snap = graph.get_state(cfg(tid))
    return snap.interrupts[0].value if snap.interrupts else None


def values(graph, tid: str) -> dict:
    return graph.get_state(cfg(tid)).values
