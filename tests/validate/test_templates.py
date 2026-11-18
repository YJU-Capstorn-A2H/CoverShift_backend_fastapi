"""T-1 · T-2 · T-4 (T-3 은 test_rules.py 의 V-9 테스트)."""
from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.domain import MinHeadcountParams, TemplateRule, parse_template_rule
from app.validate import impossible_slots, validate_draft

from ._factory import MON, NEXT_MON, THU, TUE, WED, av, emp, make_ctx, only, paid, rule, tmpl, work

EVERY_DAY = [rule("08:00", "12:00", wd=wd) for wd in range(1, 8)]  # 매일 08~12, 폐점 12:00


# ---------------------------------------------------------------- T-1 not_available
@pytest.mark.req("T-1")
def test_t1_working_on_other_day_passes():
    ctx = make_ctx([emp(1, closing=True)], rules=EVERY_DAY, templates=[tmpl("not_available", "r1", staff_id=1, weekday=1)])
    assert only(validate_draft(ctx, [work(1, "08:00", "12:00", TUE)]), "RULE_VIOLATION") == []


@pytest.mark.req("T-1")
def test_t1_working_on_forbidden_weekday_is_caught():
    ctx = make_ctx([emp(1, closing=True), emp(2, closing=True)], rules=EVERY_DAY,
                   templates=[tmpl("not_available", "r1", staff_id=1, weekday=1)])
    vs = validate_draft(ctx, [work(1, "08:00", "12:00", MON), work(1, "08:00", "12:00", NEXT_MON),
                              work(2, "08:00", "12:00", MON)])
    got = only(vs, "RULE_VIOLATION")
    assert [(v.work_date, v.employee_id) for v in got] == [(MON, 1), (NEXT_MON, 1)]
    assert got[0].severity == "required" and got[0].waivable
    assert got[0].detail["rule_key"] == "r1" and got[0].key.endswith("|r1")


@pytest.mark.req("T-1")
def test_t1_date_variant():
    ctx = make_ctx([emp(1, closing=True)], rules=EVERY_DAY, templates=[tmpl("not_available", "d", staff_id=1, date="2026-11-03")])
    vs = validate_draft(ctx, [work(1, "08:00", "12:00", MON), work(1, "08:00", "12:00", TUE)])
    assert [v.work_date for v in only(vs, "RULE_VIOLATION")] == [TUE]


@pytest.mark.req("T-1", "F-16")
def test_t1_paid_leave_row_is_not_work():
    ctx = make_ctx([emp(1, closing=True)], [av(1, "paid_leave")], rules=EVERY_DAY,
                   templates=[tmpl("not_available", "r1", staff_id=1, weekday=1)])
    assert only(validate_draft(ctx, [paid(1, MON)]), "RULE_VIOLATION") == []


# ---------------------------------------------------------------- T-2 max_consecutive
@pytest.mark.req("T-2")
def test_t2_within_limit_passes():
    ctx = make_ctx([emp(1, closing=True)], rules=EVERY_DAY, templates=[tmpl("max_consecutive", "c", shift="any", limit=2)])
    vs = validate_draft(ctx, [work(1, "08:00", "12:00", d) for d in (MON, TUE, THU)])  # 수요일 쉼
    assert only(vs, "RULE_VIOLATION") == []


@pytest.mark.req("T-2")
def test_t2_any_shift_over_limit_is_caught():
    ctx = make_ctx([emp(1, closing=True)], rules=EVERY_DAY, templates=[tmpl("max_consecutive", "c", shift="any", limit=2)])
    got = only(validate_draft(ctx, [work(1, "08:00", "12:00", d) for d in (MON, TUE, WED, THU)]), "RULE_VIOLATION")
    assert len(got) == 1
    v = got[0]
    assert (v.work_date, v.employee_id, v.severity) == (WED, 1, "required")  # limit+1 번째 날
    assert v.detail == {"rule_key": "c", "template_key": "max_consecutive", "shift": "any", "limit": 2,
                        "run_start": "2026-11-02", "run_end": "2026-11-05", "run_length": 4}


@pytest.mark.req("T-2")
def test_t2_late_counts_only_shifts_ending_at_closing():
    """G11: late = 그 날 폐점 시각(12:00)에 끝나는 근무."""
    c = tmpl("max_consecutive", "late1", shift="late", limit=1)
    ctx = make_ctx([emp(1, closing=True)], rules=EVERY_DAY, templates=[c])
    ok = validate_draft(ctx, [work(1, "08:00", "12:00", MON), work(1, "08:00", "11:30", TUE), work(1, "08:00", "12:00", WED)])
    assert only(ok, "RULE_VIOLATION") == []
    bad = validate_draft(ctx, [work(1, "09:00", "12:00", MON), work(1, "10:00", "12:00", TUE)])
    got = only(bad, "RULE_VIOLATION")
    assert [(v.work_date, v.detail["run_length"]) for v in got] == [(TUE, 2)]


@pytest.mark.req("T-2")
def test_t2_staff_filter_and_paid_leave_breaks_run():
    ctx = make_ctx([emp(1, closing=True), emp(2, closing=True)], [av(1, "paid_leave", d=TUE)], rules=EVERY_DAY,
                   templates=[tmpl("max_consecutive", "c", shift="any", limit=1, staff_id=2)])
    vs = validate_draft(ctx, [work(1, "08:00", "12:00", MON), work(1, "08:00", "12:00", WED),          # 1 은 대상 아님
                              work(2, "08:00", "12:00", MON), paid(2, TUE), work(2, "08:00", "12:00", WED)])  # 유급이 끊는다
    assert only(vs, "RULE_VIOLATION") == []
    vs2 = validate_draft(ctx, [work(2, "08:00", "12:00", MON), work(2, "08:00", "12:00", TUE)])
    assert [v.employee_id for v in only(vs2, "RULE_VIOLATION")] == [2]


# ---------------------------------------------------------------- T-4 틀 검사 (거부)
@pytest.mark.req("T-4")
def test_t4_parse_rejects_unknown_template_and_bad_params():
    with pytest.raises(ValueError):
        parse_template_rule("x", "max_hours", {"limit": 20}, "주 20시간까지")  # MVP 틀 3개에 없음
    with pytest.raises(ValueError):
        parse_template_rule("x", "min_headcount", {"count": 2, "start": "08:15", "end": "09:00"}, "")  # 30분 단위 아님
    with pytest.raises(ValueError):
        parse_template_rule("x", "not_available", {"staff_id": 1, "weekday": 1, "date": "2026-11-02"}, "")  # 둘 다
    ok = parse_template_rule("x", "min_headcount", {"count": 2, "start": "08:00", "end": "09:00"}, "8시대는 2명")
    assert isinstance(ok.params, MinHeadcountParams)


@pytest.mark.req("T-4")
@pytest.mark.parametrize("bad", [
    TemplateRule("r1", "max_hours", MinHeadcountParams(count=1, start="08:00", end="09:00"), ""),  # type: ignore[arg-type]
    TemplateRule("r1", "min_headcount", {"count": 0, "start": "08:00", "end": "09:00"}, ""),        # type: ignore[arg-type]
    TemplateRule("r1", "not_available", MinHeadcountParams(count=1, start="08:00", end="09:00"), ""),
])
def test_t4_validate_refuses_malformed_rule_instead_of_skipping(bad):
    """검증이 유일한 보증이라 틀이 틀린 규칙을 조용히 건너뛰지 않는다 (ValueError)."""
    ctx = make_ctx([emp(1)], templates=[bad])
    with pytest.raises(ValueError):
        validate_draft(ctx, [])
    with pytest.raises(ValueError):
        impossible_slots(ctx)


@pytest.mark.req("T-4")
def test_t4_duplicate_rule_key_is_refused():
    ctx = make_ctx([emp(1)], templates=[tmpl("not_available", "r1", staff_id=1, weekday=1),
                                        tmpl("not_available", "r1", staff_id=1, weekday=2)])
    with pytest.raises(ValueError, match="duplicate"):
        validate_draft(ctx, [])


@pytest.mark.req("T-4")
def test_t4_params_given_as_dict_are_revalidated_and_used():
    raw = TemplateRule("r1", "not_available", {"staff_id": 1, "date": "2026-11-02"}, "")  # type: ignore[arg-type]
    ctx = make_ctx([emp(1, closing=True)], templates=[raw])
    got = only(validate_draft(ctx, [work(1, "08:00", "12:00", MON)]), "RULE_VIOLATION")
    assert [(v.work_date, v.detail["rule_key"]) for v in got] == [(date(2026, 11, 2), "r1")]


@pytest.mark.req("T-4")
def test_pydantic_validation_error_is_value_error():
    """T-4 의 「ValueError 로 거부」가 pydantic 오류도 잡는다는 전제 확인."""
    assert issubclass(ValidationError, ValueError)
