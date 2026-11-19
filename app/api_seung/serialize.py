"""응답 값 모양 맞추기 (API_CONTRACT 공통): 날짜 YYYY-MM-DD, 시각 HH:MM, 시점 ISO 8601(시간대 포함)."""
from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any


def hm(t: time | None) -> str | None:
    return None if t is None else t.strftime("%H:%M")


def out(v: Any) -> Any:
    """DB 행(dict) · 러너가 돌려준 dict 를 JSON 값으로. 시각은 초를 버리지 않도록 30분 단위 입력만 받는다(검사는 schemas)."""
    if isinstance(v, datetime):  # datetime 은 date 의 하위 클래스라 먼저 본다
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, time):
        return hm(v)
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, dict):
        return {k: out(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set, frozenset)):
        return [out(x) for x in v]
    return v
