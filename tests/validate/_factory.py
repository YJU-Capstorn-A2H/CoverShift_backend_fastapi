"""검증 테스트용 작은 PeriodContext 만들기.

기간은 2026-11 (30일). 시간대 규칙은 기본으로 월요일(weekday=1) 08:00~12:00 하나만 → 영업일은 월요일뿐이고
다른 요일은 휴업(칸 없음)이라 잡음이 적다. 단언은 날짜(MON)와 코드로 걸러서 한다.
"""
from __future__ import annotations

from datetime import date, time

from app.domain import (
    Assignment,
    Availability,
    Employee,
    Period,
    PeriodContext,
    TimeRule,
    ValidateOptions,
    Violation,
    parse_template_rule,
)

MON = date(2026, 11, 2)   # 월요일
TUE = date(2026, 11, 3)
WED = date(2026, 11, 4)
THU = date(2026, 11, 5)
NEXT_MON = date(2026, 11, 9)


def T(s: str) -> time:
    return time.fromisoformat(s)


def emp(i: int, *, regular: bool = True, qual: bool = False, closing: bool = False) -> Employee:
    return Employee(employee_id=i, is_regular=regular, has_qualification=qual, closing_ok=closing)


def part(i: int, **kw) -> Employee:
    return emp(i, regular=False, **kw)


def av(i: int, kind: str, s: str | None = None, e: str | None = None, d: date = MON) -> Availability:
    return Availability(i, d, kind, T(s) if s else None, T(e) if e else None)  # type: ignore[arg-type]


_rid = iter(range(1, 10_000))


def rule(s: str = "08:00", e: str = "12:00", *, wd: int = 1, mh: int = 1, mq: int = 0) -> TimeRule:
    return TimeRule(next(_rid), wd, T(s), T(e), mh, mq)


def work(i: int, s: str, e: str, d: date = MON) -> Assignment:
    return Assignment(i, d, "work", T(s), T(e))


def paid(i: int, d: date = MON) -> Assignment:
    return Assignment(i, d, "paid_leave")


def tmpl(template_key: str, rule_key: str = "r1", **params):
    return parse_template_rule(rule_key, template_key, params, "점장 원문")


def make_ctx(emps, avail=(), rules=None, templates=(), off_days: int = 0, **opts) -> PeriodContext:
    return PeriodContext(
        period=Period(period_id=1, store_id=1, target_month=date(2026, 11, 1), designated_off_days=off_days),
        employees=tuple(emps),
        availability=tuple(avail),
        time_rules=tuple(rules if rules is not None else (rule(),)),
        template_rules=tuple(templates),
        options=ValidateOptions(**opts),
    )


def only(vs: list[Violation], code: str, **attrs) -> list[Violation]:
    return [v for v in vs if v.check_code == code and all(getattr(v, k) == x for k, x in attrs.items())]


def times(vs: list[Violation]) -> list[str]:
    return [v.detail["time"] for v in vs]
