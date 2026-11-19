"""검증 공통 바탕: 30분 칸 · 영업시간 · 필요 인원 · 이론상 최대 인원.

- 칸은 30분 (V9_ko 165, 447). 칸 t 는 [t, t+30) 이고, 배정 [start, end) 이 칸을 덮는 조건은 start <= t 이고 t+30 <= end.
- 영업시간 = 그 요일 store_time_rules 의 가장 이른 시작 ~ 가장 늦은 종료, 폐점 시각 = 가장 늦은 종료 (가정 G6, V9_ko 228-229).
- 시간은 전부 「자정부터의 분(int)」으로 다룬다. 시각 문자열은 detail 에 넣을 때만 "HH:MM" 으로 바꾼다.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, time

from pydantic import BaseModel

from app.domain import (
    SLOT_MINUTES,
    TEMPLATE_PARAMS,
    Assignment,
    Availability,
    Employee,
    MinHeadcountParams,
    PeriodContext,
    TemplateRule,
    TimeRule,
)


def to_min(t: time) -> int:
    return t.hour * 60 + t.minute


def hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def fmt(t: time | None) -> str | None:
    return None if t is None else t.strftime("%H:%M")


def covers(start: int, end: int, t: int) -> bool:
    """[start, end) 이 칸 [t, t+30) 을 통째로 덮는가."""
    return start <= t and t + SLOT_MINUTES <= end


def checked_templates(ctx: PeriodContext) -> tuple[TemplateRule, ...]:
    """T-4: 틀 규칙을 검증 직전에 한 번 더 검사한다 (V9_ko 478, 1170 「틀 없는 틀 규칙은 거부」).

    입구(parse_template_rule)에서 이미 걸렀어야 하지만, 검증이 유일한 보증이므로 (V9_ko 1185) 여기서도 본다.
    틀 이름이 없거나 params 가 틀리거나 rule_key 가 겹치면 ValueError 를 낸다.
    조용히 건너뛰면 「보장」이라고 보여 준 규칙이 검사 없이 승인되므로, 그래프를 멈추는 쪽을 골랐다 (가정 신규 — 보수 쪽).
    """
    out: list[TemplateRule] = []
    seen: set[str] = set()
    for r in ctx.template_rules:
        cls = TEMPLATE_PARAMS.get(r.template_key)
        if cls is None:
            raise ValueError(f"unknown template_key: {r.template_key!r} (rule_key={r.rule_key!r})")
        if r.rule_key in seen:
            raise ValueError(f"duplicate rule_key: {r.rule_key!r}")
        seen.add(r.rule_key)
        params = r.params
        if not isinstance(params, cls):
            raw = params.model_dump() if isinstance(params, BaseModel) else params
            params = cls.model_validate(raw)  # 틀리면 pydantic.ValidationError (ValueError 의 하위형)
            r = TemplateRule(rule_key=r.rule_key, template_key=r.template_key, params=params, source_text=r.source_text)
        out.append(r)
    return tuple(out)


@dataclass(frozen=True)
class Need:
    """칸 하나의 필요 인원. source = "template"(지시 틀, 보장) / "setting"(store_time_rules, 경고) / "none"."""

    count: int
    severity: str  # "required" | "warning"
    source: str
    rule_key: str | None = None


NO_NEED = Need(0, "warning", "none")


class ContextIndex:
    """PeriodContext 를 검사하기 쉬운 모양으로 한 번만 펼쳐 둔다 (검증 1회마다 새로 만든다)."""

    def __init__(self, ctx: PeriodContext):
        self.ctx = ctx
        self.dates: list[date] = ctx.period.dates()
        self.date_set = set(self.dates)
        self.emps: dict[int, Employee] = {e.employee_id: e for e in ctx.employees}
        self.avail: dict[tuple[int, date], Availability] = {(a.employee_id, a.work_date): a for a in ctx.availability}
        self.rules_by_wd: dict[int, list[TimeRule]] = defaultdict(list)
        for r in ctx.time_rules:
            self.rules_by_wd[r.weekday].append(r)
        self.templates = checked_templates(ctx)
        self.min_headcount_rules = [r for r in self.templates if r.template_key == "min_headcount"]

    # ------------------------------------------------------------ 영업시간 (G6)
    def hours(self, d: date) -> tuple[int, int] | None:
        """(개점 분, 폐점 분). 그 요일 규칙이 없으면 None = 휴업일 (가정 G6)."""
        rules = self.rules_by_wd.get(d.isoweekday())
        if not rules:
            return None
        return min(to_min(r.start_time) for r in rules), max(to_min(r.end_time) for r in rules)

    def business_slots(self, d: date) -> list[int]:
        """영업시간 안의 칸 시작 분. 규칙 사이에 빈 시간이 있어도 「가장 이른 시작 ~ 가장 늦은 종료」 전체가 영업시간이다 (G6).
        store_time_rules 의 시각은 30분 단위라고 본다 (DB 에 CHECK 는 없다 — 어긋나면 칸 경계가 개점 시각을 따른다)."""
        h = self.hours(d)
        if h is None:
            return []
        return list(range(h[0], h[1] - SLOT_MINUTES + 1, SLOT_MINUTES))

    def time_rule_at(self, d: date, t: int) -> TimeRule | None:
        """칸 t 를 통째로 덮는 그 요일의 시간대 규칙. EXCLUDE 제약(V9_ko 243)으로 겹치지 않으므로 많아야 1개."""
        for r in self.rules_by_wd.get(d.isoweekday(), ()):
            if covers(to_min(r.start_time), to_min(r.end_time), t):
                return r
        return None

    # ------------------------------------------------------------ 최소 인원 (V-9, T-3)
    def _template_applies(self, p: MinHeadcountParams, d: date) -> bool:
        if p.date is not None:
            return p.date == d
        if p.weekday is not None:
            return p.weekday == d.isoweekday()
        return True  # 둘 다 None = 매일 (G12)

    def headcount_slots(self, d: date) -> list[int]:
        """V-9 를 볼 칸 = 영업시간 칸 ∪ 그 날에 걸리는 min_headcount 틀의 칸.
        (가정 신규) 틀이 영업시간 밖이나 휴업 요일에 걸려 있어도 점장이 명시한 「보장」이므로 그 칸도 본다."""
        slots = set(self.business_slots(d))
        for r in self.min_headcount_rules:
            p: MinHeadcountParams = r.params  # type: ignore[assignment]
            if self._template_applies(p, d):
                slots.update(range(to_min(p.start), to_min(p.end), SLOT_MINUTES))
        return sorted(slots)

    def need_at(self, d: date, t: int) -> Need:
        """칸의 필요 인원 (V9_ko 476, 514).

        - 지시 틀(min_headcount)이 덮는 칸: 틀의 숫자로 덮어쓴다(설정보다 적어도) → required.
          (가정 신규) 틀 여러 개가 같은 칸을 덮으면 가장 큰 숫자를 쓴다(같으면 먼저 나온 틀). 보수 쪽.
        - 틀이 없는 칸: 그 칸을 덮는 store_time_rules.min_headcount → warning.
        - 둘 다 없는 칸(규칙 사이 빈 시간): 0.
        """
        best: TemplateRule | None = None
        for r in self.min_headcount_rules:
            p: MinHeadcountParams = r.params  # type: ignore[assignment]
            if self._template_applies(p, d) and covers(to_min(p.start), to_min(p.end), t):
                if best is None or p.count > best.params.count:  # type: ignore[attr-defined]
                    best = r
        if best is not None:
            return Need(best.params.count, "required", "template", best.rule_key)  # type: ignore[attr-defined]
        tr = self.time_rule_at(d, t)
        if tr is not None:
            return Need(tr.min_headcount, "warning", "setting")
        return NO_NEED

    def max_possible(self, d: date, t: int) -> int:
        """희망만으로 본 그 칸의 이론상 최대 인원 (V9_ko 476 「이론상 못 채우는 칸」).

        아즈키 max_headcount(시제품 447-465)와 같은 뜻:
        - 사원: 행 없음 = 배치 가능(G19). off · paid_leave = 불가. available · half_leave = 그 시각이 칸을 덮을 때만.
        - 파트・알바: available · half_leave 의 시각이 칸을 덮을 때만 (행 없음 = 불가, V9_ko 309).
        아즈키와 다른 점: 시간대별 exclude(예 08~09시 薬剤師 제외)는 db/001 표에 열이 없어서 보지 않는다.
        틀 규칙 not_available 도 보지 않는다(희망만 본다) — 그만큼 「못 채움」으로 빠지는 칸이 적어진다(검사가 더 엄해지는 쪽).
        """
        n = 0
        for e in self.ctx.employees:
            a = self.avail.get((e.employee_id, d))
            if a is None:
                n += 1 if e.is_regular else 0
            elif a.kind in ("available", "half_leave") and a.start_time and a.end_time:
                n += 1 if covers(to_min(a.start_time), to_min(a.end_time), t) else 0
        return n


@dataclass(frozen=True)
class Work:
    """칸 계산에 쓰는 출근 배정 1건 (분 단위)."""

    employee_id: int
    work_date: date
    start: int
    end: int
    src: Assignment


def split_assignments(idx: ContextIndex, assignments: Sequence[Assignment]) -> tuple[list[Assignment], list[Work]]:
    """(아는 직원의 배정 전부, 그중 출근 배정).

    - ctx 에 없는 직원 ID 의 배정은 건너뛴다. LLM 층이 검증 전에 버리기로 했고(작업 지시), 여기서 터뜨리면 그래프가 멈춘다.
      (이름 없는 ID 라 어느 규칙으로도 판정할 수 없다.)
    - paid_leave 는 출근이 아니다 → 칸을 덮지 않는다 (F-16, V9_ko 301, 338).
    - 시각이 비었거나 end <= start 인 work 는 칸 계산에서 뺀다 (DB CHECK 로 저장도 안 됨, V9_ko 354-359).
      행 자체는 V-8(같은 날 2행 이상) 셈에는 들어간다.
    """
    known: list[Assignment] = []
    work: list[Work] = []
    for a in assignments:
        if a.employee_id not in idx.emps:
            continue
        known.append(a)
        if a.kind == "work" and a.start_time is not None and a.end_time is not None:
            s, e = to_min(a.start_time), to_min(a.end_time)
            if e > s:
                work.append(Work(a.employee_id, a.work_date, s, e, a))
    return known, work


def coverage(work: Sequence[Work]) -> dict[date, list[Work]]:
    by_date: dict[date, list[Work]] = defaultdict(list)
    for w in work:
        by_date[w.work_date].append(w)
    return by_date


def people_at(day_work: Sequence[Work], t: int) -> set[int]:
    """칸 t 에 있는 사람(서로 다른 직원 ID). 같은 사람이 두 번 배정돼도 1명으로 센다
    (아즈키 check_coverage 는 배정 행 수를 센다 — 중복 배치는 V-8 이 따로 잡으므로 여기서는 사람 수로 센다)."""
    return {w.employee_id for w in day_work if covers(w.start, w.end, t)}
