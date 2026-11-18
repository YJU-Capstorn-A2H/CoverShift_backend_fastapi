"""영업시간 · 30분 칸 계산 (LLM 경로 안에서만 쓰는 작은 도구).

- 영업시간 = 그 요일 store_time_rules 의 가장 이른 시작 ~ 가장 늦은 종료 (ASSUMPTIONS G6, V9_ko 228-229).
- 폐점 시각 = 가장 늦은 종료 (G6). 규칙이 없는 요일 = 휴업일로 본다.
- validate 쪽에도 같은 계산이 있을 수 있다(담당이 다름). 여기 것은 프롬프트 문장 · 가짜 LLM 용이다.
"""
from __future__ import annotations

from datetime import date, time

from app.domain import SLOT_MINUTES, PeriodContext

WEEKDAY_JA = {1: "月", 2: "火", 3: "水", 4: "木", 5: "金", 6: "土", 7: "日"}


def to_min(t: time) -> int:
    return t.hour * 60 + t.minute


def hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def business_hours(ctx: PeriodContext) -> dict[int, tuple[int, int]]:
    """요일(1=월…7=일) → (개점 분, 폐점 분). 규칙 없는 요일은 키가 없다 (G6)."""
    out: dict[int, tuple[int, int]] = {}
    for r in ctx.time_rules:
        s, e = to_min(r.start_time), to_min(r.end_time)
        if r.weekday in out:
            o, c = out[r.weekday]
            out[r.weekday] = (min(o, s), max(c, e))
        else:
            out[r.weekday] = (s, e)
    return out


def hours_of(ctx: PeriodContext, d: date) -> tuple[int, int] | None:
    return business_hours(ctx).get(d.isoweekday())


def slot_marks(ctx: PeriodContext) -> list[str]:
    """출력 형식(enum)에 쓰는 시각 목록: 모든 요일의 가장 이른 개점 ~ 가장 늦은 폐점, 30분 간격 (V9_ko 165, 447)."""
    bh = business_hours(ctx)
    if not bh:
        lo, hi = 0, 24 * 60 - SLOT_MINUTES
    else:
        lo = min(o for o, _ in bh.values())
        hi = max(c for _, c in bh.values())
    lo -= lo % SLOT_MINUTES
    return [hhmm(m) for m in range(lo, hi + 1, SLOT_MINUTES)]


def fmt_weekdays(wds: set[int] | frozenset[int]) -> str:
    """{1..7} → 「毎日」, 그 밖 → 「月・水・金曜日」."""
    if len(wds) == 7:
        return "毎日"
    return "・".join(WEEKDAY_JA[w] for w in sorted(wds)) + "曜日"
