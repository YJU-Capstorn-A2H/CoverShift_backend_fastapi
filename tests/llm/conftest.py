"""LLM 시험 공통 재료. DB 를 쓰지 않는다 (app.seed 는 다른 에이전트가 쓰는 중이라 기대지 않는다).

아즈키 staff_data(11/04, seed/azuki_staff_data_1104.json) → PeriodContext 를 여기서 직접 만든다.
- 社員 → is_regular. 資格: 登録販売者(管理者) · 薬剤師 → has_qualification (研修中은 아님 — 시험용 가정).
- closing_ok: 원본에 있는 값, 없으면 社員=True · 그 밖=False (아즈키 check_closing 이 사원을 보지 않던 것과 맞춤, 시험용 가정).
- 시간대 규칙: 아즈키 STORE_SETTINGS[101] (08-09 최소 3 · 09-13 최소 4 · 자격 08:00-22:30 1명). 13-22:30 최소 인원 2 는 시험용 가정.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path

import pytest

from app.domain import Availability, Employee, Period, PeriodContext, TimeRule

ROOT = Path(__file__).resolve().parents[2]
AZUKI_JSON = ROOT / "seed" / "azuki_staff_data_1104.json"
QUALIFIED = {"登録販売者(管理者)", "薬剤師"}


def _t(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


@dataclass(frozen=True)
class SlicePeriod(Period):
    """앞 n 일만 쓰는 기간 (1주 실측용). 프롬프트 · 스키마 코드는 period.dates() 만 본다."""

    days: int = 7

    def dates(self) -> list[date]:
        return super().dates()[: self.days]


def azuki_rules() -> tuple[TimeRule, ...]:
    out, rid = [], 1
    for wd in range(1, 8):
        for s, e, hc in (("08:00", "09:00", 3), ("09:00", "13:00", 4), ("13:00", "22:30", 2)):
            out.append(TimeRule(rule_id=rid, weekday=wd, start_time=_t(s), end_time=_t(e), min_headcount=hc, min_qualified=1))
            rid += 1
    return tuple(out)


def ctx_from_azuki(raw: list[dict], *, period: Period | None = None) -> PeriodContext:
    period = period or Period(period_id=1, store_id=1, target_month=date(2026, 11, 1), designated_off_days=9)
    keep = set(period.dates())
    emps, avs = [], []
    for s in raw:
        regular = s["employment"] == "社員"
        closing = s.get("closing_ok")
        emps.append(Employee(employee_id=s["staff_id"], is_regular=regular,
                             has_qualification=s.get("qualification") in QUALIFIED,
                             closing_ok=bool(closing) if closing is not None else regular))
        for r in s.get("requests", []):
            d = date.fromisoformat(r["date"])
            if d not in keep:
                continue
            if r.get("paid_leave") is True:
                avs.append(Availability(s["staff_id"], d, "paid_leave"))
            elif r.get("paid_leave") == "half":
                avs.append(Availability(s["staff_id"], d, "half_leave", _t(r["start"]), _t(r["end"])))
            elif r.get("off"):
                avs.append(Availability(s["staff_id"], d, "off"))
            elif "start" in r:
                avs.append(Availability(s["staff_id"], d, "available", _t(r["start"]), _t(r["end"])))
    return PeriodContext(period=period, employees=tuple(emps), availability=tuple(avs), time_rules=azuki_rules())


@pytest.fixture(scope="session")
def azuki_raw() -> list[dict]:
    return json.loads(AZUKI_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def azuki_names(azuki_raw) -> list[str]:
    return [s["staff_name"] for s in azuki_raw]


@pytest.fixture
def azuki_ctx(azuki_raw) -> PeriodContext:
    return ctx_from_azuki(azuki_raw)


@pytest.fixture
def small_ctx() -> PeriodContext:
    """손으로 만든 작은 기간 (2026-11, 사원 2 · 파트 1)."""
    period = Period(period_id=7, store_id=1, target_month=date(2026, 11, 1), designated_off_days=9)
    emps = (
        Employee(1, is_regular=True, has_qualification=True, closing_ok=True),
        Employee(2, is_regular=True, has_qualification=False, closing_ok=True),
        Employee(3, is_regular=False, has_qualification=False, closing_ok=False, weekly_days=3),
    )
    avs = (
        Availability(3, date(2026, 11, 2), "available", time(17, 0), time(22, 30)),
        Availability(1, date(2026, 11, 3), "off"),
        Availability(2, date(2026, 11, 4), "paid_leave"),
        Availability(1, date(2026, 11, 5), "half_leave", time(13, 0), time(17, 0)),
    )
    rules = tuple(TimeRule(rule_id=wd, weekday=wd, start_time=time(8, 0), end_time=time(22, 30), min_headcount=2,
                           min_qualified=1) for wd in range(1, 8))
    return PeriodContext(period=period, employees=emps, availability=avs, time_rules=rules)
