"""진짜 LLM 1회 (1주 분량). 기본 실행에서 빠진다 (pyproject addopts = -m 'not llm').

실행은 승혁이 직접: `uv run pytest tests/llm/test_real_llm.py -m llm -q -s`
- 비용이 든다 (1주 · 1회. 11/06 1주 측정 3.2분 기준). ANTHROPIC_API_KEY 는 환경 변수로 (.env 를 이 코드가 읽지 않는다).
- 결과 숫자는 logs/llm_calls.jsonl 에 1줄 남는다 (KPI 측정 기록).
"""
from __future__ import annotations

from datetime import date

import pytest

from app.config import get_settings
from app.domain import Period
from app.llm.anthropic_composer import AnthropicComposer
from tests.llm.conftest import SlicePeriod, ctx_from_azuki


@pytest.mark.llm
@pytest.mark.req("M-1", "F-13")
def test_real_compose_one_week(azuki_raw):
    period = SlicePeriod(period_id=1, store_id=1, target_month=date(2026, 11, 1), designated_off_days=2, days=7)
    assert isinstance(period, Period) and len(period.dates()) == 7
    ctx = ctx_from_azuki(azuki_raw, period=period)
    settings = get_settings()
    r = AnthropicComposer(settings=settings).compose(ctx, previous=None, history=[], instruction=None)
    print({k: r.usage.get(k) for k in ("seconds", "input_tokens", "output_tokens", "cache_read_input_tokens",
                                        "model", "stop_reason", "request_id", "cost_usd_est", "n_dropped")})
    assert r.ok, f"error={r.error} usage={r.usage}"
    assert r.assignments and all(a.work_date in set(period.dates()) for a in r.assignments)
    assert r.usage["seconds"] <= settings.llm_timeout_sec + 5
