"""가짜 LLM (C5) · get_composer 선택."""
from __future__ import annotations

import dataclasses
import time
from collections import Counter, defaultdict
from datetime import time as dtime

import pytest

from app.domain import ComposeResult, parse_template_rule
from app.llm import get_composer
from app.llm.anthropic_composer import AnthropicComposer
from app.llm.fake import FakeComposer


def _compose(c, ctx, previous=None, history=(), instruction=None):
    return c.compose(ctx, previous=previous, history=list(history), instruction=instruction)


def _same_day_dupes(assignments) -> list[tuple]:
    cnt = Counter((a.employee_id, a.work_date) for a in assignments)
    return [k for k, n in cnt.items() if n > 1]


def _avail_index(ctx):
    return {(a.employee_id, a.work_date): a for a in ctx.availability}


# ---------------------------------------------------------------- greedy

@pytest.mark.req("M-1")
def test_greedy_on_azuki_30_days_is_fast_and_plausible(azuki_ctx):
    t0 = time.perf_counter()
    r = _compose(FakeComposer("greedy"), azuki_ctx)
    assert time.perf_counter() - t0 < 1.0
    assert r.ok and r.error is None and len(r.assignments) > 100
    assert r.usage["model"] == "fake" and r.usage["n_dropped"] == 0
    assert _same_day_dupes(r.assignments) == []  # V-8 을 만들지 않는다

    av = _avail_index(azuki_ctx)
    emp = {e.employee_id: e for e in azuki_ctx.employees}
    for a in r.assignments:
        assert a.kind == "work" and a.start_time < a.end_time
        assert dtime(8) <= a.start_time and a.end_time <= dtime(22, 30)
        if not emp[a.employee_id].is_regular:  # V-3: 파트・알바는 신청 안에서만
            req = av.get((a.employee_id, a.work_date))
            assert req is not None and req.kind in ("available", "half_leave")
            assert req.start_time <= a.start_time and a.end_time <= req.end_time
        if a.end_time == dtime(22, 30):  # V-4
            assert emp[a.employee_id].closing_ok

    # V-5: 사원 출근일 + 유급 = 30 − 9
    work_days = defaultdict(set)
    for a in r.assignments:
        work_days[a.employee_id].add(a.work_date)
    for e in azuki_ctx.employees:
        if e.is_regular:
            paid = sum(1 for x in azuki_ctx.availability if x.employee_id == e.employee_id and x.kind == "paid_leave")
            assert len(work_days[e.employee_id]) + paid == 21, e.employee_id

    # V-1 · V-2: 30분마다 사원 1명 이상 · 자격자 1명 이상 (이 시험이 직접 센다 — app.validate 에 기대지 않음)
    gaps = []
    for d in azuki_ctx.period.dates():
        for m in range(8 * 60, 22 * 60 + 30, 30):
            t = dtime(m // 60, m % 60)
            here = [emp[a.employee_id] for a in r.assignments if a.work_date == d and a.start_time <= t < a.end_time]
            if not any(x.is_regular for x in here) or not any(x.has_qualification for x in here):
                gaps.append((d, t))
    assert gaps == []


@pytest.mark.req("T-1")
def test_greedy_respects_not_available_rule(azuki_ctx):
    rule = parse_template_rule("r1", "not_available", {"staff_id": 9, "weekday": 7}, "9番は日曜不可")
    ctx = dataclasses.replace(azuki_ctx, template_rules=(rule,))
    before = [a for a in _compose(FakeComposer(), azuki_ctx).assignments if a.employee_id == 9 and a.work_date.isoweekday() == 7]
    after = [a for a in _compose(FakeComposer(), ctx).assignments if a.employee_id == 9 and a.work_date.isoweekday() == 7]
    assert before and not after  # 규칙이 없으면 일요일에 들어가고, 있으면 빠진다


# ---------------------------------------------------------------- 시나리오

@pytest.mark.req("F-2")
def test_bad_then_good(azuki_ctx):
    c = FakeComposer("bad_then_good")
    first = _compose(c, azuki_ctx)
    assert first.ok and len(_same_day_dupes(first.assignments)) == 1  # 일부러 넣은 DOUBLE_BOOKING 1개
    second = _compose(c, azuki_ctx, previous=first.assignments)
    assert second.ok and _same_day_dupes(second.assignments) == []
    # 새 인스턴스여도 previous 로 가른다 (그래프가 노드마다 get_composer() 를 불러도 같은 동작)
    assert _same_day_dupes(_compose(FakeComposer("bad_then_good"), azuki_ctx, previous=first.assignments).assignments) == []
    assert c.calls[1]["previous"] == first.assignments


@pytest.mark.req("F-3")
def test_always_bad(azuki_ctx):
    c = FakeComposer("always_bad")
    for prev in (None, []):
        r = _compose(c, azuki_ctx, previous=prev)
        assert r.ok and len(_same_day_dupes(r.assignments)) == 1


@pytest.mark.req("F-4")
def test_empty_scenario(azuki_ctx):
    r = _compose(FakeComposer("empty"), azuki_ctx)
    assert not r.ok and r.error == "empty" and r.assignments == []


@pytest.mark.req("F-4")
def test_script_returns_in_order_then_fails_loudly(azuki_ctx):
    script = [ComposeResult(ok=False, error="timeout"), ComposeResult(ok=True, notes=["second"])]
    c = FakeComposer(script=script)
    assert c.scenario == "script"
    assert _compose(c, azuki_ctx).error == "timeout"
    assert _compose(c, azuki_ctx).notes == ["second"]
    with pytest.raises(RuntimeError, match="exhausted"):
        _compose(c, azuki_ctx)


@pytest.mark.req("M-1")
def test_fake_builds_the_real_prompt_each_call(azuki_ctx, azuki_names):
    c = FakeComposer()
    _compose(c, azuki_ctx, instruction="土曜の午前を厚くして")
    system, messages = c.last_request
    assert "【守れないとき】" in system[0]["text"]
    assert not [n for n in azuki_names if n in system[0]["text"] + messages[0]["content"][0]["text"]]


@pytest.mark.req("M-1")
def test_unknown_scenario_is_rejected(monkeypatch):
    with pytest.raises(ValueError):
        FakeComposer("perfect")
    with pytest.raises(ValueError):
        FakeComposer("script")  # script 없이 script 시나리오
    monkeypatch.setenv("FAKE_LLM_SCENARIO", "always_bad")
    assert FakeComposer().scenario == "always_bad"


# ---------------------------------------------------------------- get_composer

@pytest.mark.req("M-1")
def test_get_composer_follows_llm_mode(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "fake")
    monkeypatch.setenv("FAKE_LLM_SCENARIO", "empty")
    c = get_composer()
    assert isinstance(c, FakeComposer) and c.scenario == "empty"
    monkeypatch.setenv("LLM_MODE", "real")
    real = get_composer()
    assert isinstance(real, AnthropicComposer) and real._client is None  # 만들기만 하고 부르지 않는다
    monkeypatch.setenv("LLM_MODE", "Real ")
    with pytest.raises(ValueError):  # 모르는 값으로 조용히 진짜 API 에 가지 않는다
        get_composer()


# ---------------------------------------------------------------- 가짜 지시문 파서 (M-2, T-4)

@pytest.mark.req("M-2", "T-1")
@pytest.mark.parametrize("text", ["3番は日曜不可", "3번은 일요일 불가", "ID3は日曜日NG"])
def test_fake_instruction_not_available(small_ctx, text):
    res = FakeComposer().instruction_to_rules(small_ctx, text)
    assert [(r.rule_key, r.template_key, r.params.staff_id, r.params.weekday) for r in res.rules] == [("r1", "not_available", 3, 7)]
    assert res.free_text == [] and res.rejected == []


@pytest.mark.req("M-2", "T-3")
def test_fake_instruction_min_headcount_and_consecutive(small_ctx):
    res = FakeComposer().instruction_to_rules(small_ctx, "金曜17:00-22:30は3人。連勤は4日まで。ベテランと新人を組ませて")
    hc, mc = res.rules
    assert (hc.rule_key, hc.template_key, hc.params.count, hc.params.weekday) == ("r1", "min_headcount", 3, 5)
    assert (hc.params.start, hc.params.end) == (dtime(17), dtime(22, 30))
    assert (mc.rule_key, mc.template_key, mc.params.limit, mc.params.shift) == ("r2", "max_consecutive", 4, "any")
    assert res.free_text == ["ベテランと新人を組ませて"]  # 틀 없는 말 (T-4)
    assert hc.source_text == "金曜17:00-22:30は3人"


@pytest.mark.req("T-4")
@pytest.mark.parametrize("text,reason", [("金曜17:15-22:30は3人", "30분"), ("99番は日曜不可", "unknown staff_id"),
                                         ("連勤は40日まで", "limit")])
def test_fake_instruction_rejects_invalid_template(small_ctx, text, reason):
    res = FakeComposer().instruction_to_rules(small_ctx, text)
    assert res.rules == [] and len(res.rejected) == 1 and reason in res.rejected[0]["reason"]
