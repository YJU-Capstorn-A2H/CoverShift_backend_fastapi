"""V-1 ~ V-10: 규칙마다 「지키는 경우」와 「일부러 깨뜨리는 경우」 (BRIEF 「만드는 방식」)."""
from __future__ import annotations

import pytest

from app.validate import blocking, impossible_slots, validate_draft

from ._factory import MON, TUE, NEXT_MON, av, emp, make_ctx, only, paid, part, rule, times, tmpl, work

ALL_SLOTS = ["08:00", "08:30", "09:00", "09:30", "10:00", "10:30", "11:00", "11:30"]


# ---------------------------------------------------------------- V-1 REGULAR_ABSENT
@pytest.mark.req("V-1")
def test_v1_regular_covers_all_business_slots_passes():
    vs = validate_draft(make_ctx([emp(1)]), [work(1, "08:00", "12:00")])
    assert only(vs, "REGULAR_ABSENT", work_date=MON) == []


@pytest.mark.req("V-1")
def test_v1_part_timer_does_not_count_as_regular():
    ctx = make_ctx([emp(1), part(2)], [av(2, "available", "10:00", "12:00")])
    vs = validate_draft(ctx, [work(1, "08:00", "10:00"), work(2, "10:00", "12:00")])
    got = only(vs, "REGULAR_ABSENT", work_date=MON)
    assert times(got) == ["10:00", "10:30", "11:00", "11:30"]
    assert got[0].severity == "required" and got[0].waivable and got[0].detail == {"time": "10:00", "need": 1, "have": 0}


@pytest.mark.req("V-1")
def test_v1_business_hours_are_earliest_start_to_latest_end_including_gap():
    """G6: 규칙 08~10, 11~12 → 영업시간 08:00~12:00. 사이 10~11 도 영업시간이다. 규칙 없는 요일은 휴업."""
    ctx = make_ctx([emp(1)], rules=[rule("08:00", "10:00"), rule("11:00", "12:00")])
    vs = validate_draft(ctx, [work(1, "08:00", "10:00")])
    assert times(only(vs, "REGULAR_ABSENT", work_date=MON)) == ["10:00", "10:30", "11:00", "11:30"]
    assert only(vs, "REGULAR_ABSENT", work_date=TUE) == []  # 화요일은 규칙 없음 = 칸 없음


@pytest.mark.req("V-1", "F-16")
def test_v1_paid_leave_assignment_does_not_cover_slots():
    vs = validate_draft(make_ctx([emp(1)], [av(1, "paid_leave")]), [paid(1)])
    assert times(only(vs, "REGULAR_ABSENT", work_date=MON)) == ALL_SLOTS


# ---------------------------------------------------------------- V-2 QUAL_MISSING
@pytest.mark.req("V-2")
def test_v2_qualified_person_present_passes():
    ctx = make_ctx([emp(1, qual=True)], rules=[rule(mq=1)])
    assert only(validate_draft(ctx, [work(1, "08:00", "12:00")]), "QUAL_MISSING", work_date=MON) == []


@pytest.mark.req("V-2")
def test_v2_missing_qualified_is_caught_and_not_waivable():
    ctx = make_ctx([emp(1, qual=False), emp(2, qual=True)], rules=[rule(mq=1)])
    vs = validate_draft(ctx, [work(1, "08:00", "12:00"), work(2, "08:00", "10:00")])
    got = only(vs, "QUAL_MISSING", work_date=MON)
    assert times(got) == ["10:00", "10:30", "11:00", "11:30"]
    assert got[0].detail == {"time": "10:00", "need": 1, "have": 0} and not got[0].waivable


@pytest.mark.req("V-2")
def test_v2_uses_min_qualified_count_and_zero_means_no_check():
    """G10: min_qualified 를 그대로 쓴다. 2 이면 1명으로는 부족, 0 이면 검사 안 함."""
    two = make_ctx([emp(1, qual=True)], rules=[rule(mq=2)])
    got = only(validate_draft(two, [work(1, "08:00", "12:00")]), "QUAL_MISSING", work_date=MON)
    assert len(got) == 8 and got[0].detail["need"] == 2 and got[0].detail["have"] == 1
    zero = make_ctx([emp(1, qual=False)], rules=[rule(mq=0)])
    assert only(validate_draft(zero, [work(1, "08:00", "12:00")]), "QUAL_MISSING") == []


# ---------------------------------------------------------------- V-3 OUTSIDE_AVAILABILITY (파트・알바)
@pytest.mark.req("V-3")
def test_v3_part_inside_submitted_time_passes():
    ctx = make_ctx([emp(1), part(2)], [av(2, "available", "09:00", "12:00")])
    assert only(validate_draft(ctx, [work(1, "08:00", "12:00"), work(2, "09:00", "12:00")]), "OUTSIDE_AVAILABILITY") == []


@pytest.mark.req("V-3")
@pytest.mark.parametrize("avail, assigned, reason", [
    ([av(2, "available", "09:00", "12:00")], ("08:00", "12:00"), "outside_time"),
    ([av(2, "available", "09:00", "12:00")], ("09:00", "12:30"), "outside_time"),
    ([], ("09:00", "12:00"), "no_submission"),             # 행 없음 = 불가 (G19, V9_ko 309)
    ([av(2, "off")], ("09:00", "12:00"), "off"),
    ([av(2, "paid_leave")], ("09:00", "12:00"), "paid_leave"),
    ([av(2, "half_leave", "09:00", "11:00")], ("08:00", "11:00"), "outside_time"),
])
def test_v3_part_outside_submission_is_caught(avail, assigned, reason):
    vs = validate_draft(make_ctx([part(2)], avail), [work(2, *assigned)])
    got = only(vs, "OUTSIDE_AVAILABILITY")
    assert len(got) == 1
    v = got[0]
    assert (v.severity, v.work_date, v.employee_id) == ("required", MON, 2)
    assert v.detail["kind"] == "part" and v.detail["reason"] == reason
    assert (v.detail["assigned_start"], v.detail["assigned_end"]) == assigned
    assert v.waivable


@pytest.mark.req("V-3")
def test_v3_regular_without_row_is_not_checked():
    """G19: 사원은 행 없음 = 배치 가능."""
    assert only(validate_draft(make_ctx([emp(1)]), [work(1, "08:00", "12:00")]), "OUTSIDE_AVAILABILITY") == []


# ---------------------------------------------------------------- V-4 CLOSING_NOT_ALLOWED
@pytest.mark.req("V-4")
def test_v4_closing_ok_or_leaving_before_last_slot_passes():
    ctx = make_ctx([part(2, closing=True), part(3)],
                   [av(2, "available", "08:00", "12:00"), av(3, "available", "08:00", "12:00")])
    vs = validate_draft(ctx, [work(2, "08:00", "12:00"), work(3, "08:00", "11:30")])
    assert only(vs, "CLOSING_NOT_ALLOWED") == []


@pytest.mark.req("V-4")
def test_v4_non_closing_person_staying_to_close_is_caught():
    ctx = make_ctx([part(3)], [av(3, "available", "08:00", "12:00")])
    got = only(validate_draft(ctx, [work(3, "09:00", "12:00")]), "CLOSING_NOT_ALLOWED")
    assert len(got) == 1 and got[0].employee_id == 3 and got[0].work_date == MON
    assert got[0].detail == {"closing": "12:00", "assigned_start": "09:00", "assigned_end": "12:00"}


@pytest.mark.req("V-4")
def test_v4_closing_time_is_latest_end_of_that_weekday():
    """G6: 폐점 = 그 요일 가장 늦은 종료. 08~10 + 10~14 → 14:00. 12:00 에 끝나면 위반 아님, 14:00 이면 위반."""
    ctx = make_ctx([part(3)], [av(3, "available", "08:00", "14:00")], rules=[rule("08:00", "10:00"), rule("10:00", "14:00")])
    assert only(validate_draft(ctx, [work(3, "08:00", "12:00")]), "CLOSING_NOT_ALLOWED") == []
    got = only(validate_draft(ctx, [work(3, "10:00", "14:00")]), "CLOSING_NOT_ALLOWED")
    assert got[0].detail["closing"] == "14:00"


@pytest.mark.req("V-4")
def test_v4_applies_to_regulars_too():
    """(가정 신규) 사원도 closing_ok 가 false 면 위반. 아즈키 check_closing 은 파트・알바만 본다 — 다른 점."""
    got = only(validate_draft(make_ctx([emp(1, closing=False)]), [work(1, "08:00", "12:00")]), "CLOSING_NOT_ALLOWED")
    assert [v.employee_id for v in got] == [1]
    assert only(validate_draft(make_ctx([emp(1, closing=True)]), [work(1, "08:00", "12:00")]), "CLOSING_NOT_ALLOWED") == []


# ---------------------------------------------------------------- V-5 REGULAR_DAYS_MISMATCH
@pytest.mark.req("V-5")
def test_v5_exact_work_days_passes():
    """30일 − 공휴일 28 = 출근 2일."""
    ctx = make_ctx([emp(1, closing=True)], off_days=28)
    assert only(validate_draft(ctx, [work(1, "08:00", "12:00", MON), work(1, "08:00", "12:00", TUE)]),
                "REGULAR_DAYS_MISMATCH") == []


@pytest.mark.req("V-5")
def test_v5_too_few_and_too_many_days_are_caught():
    ctx = make_ctx([emp(1, closing=True), part(2)], off_days=28)
    few = only(validate_draft(ctx, [work(1, "08:00", "12:00", MON)]), "REGULAR_DAYS_MISMATCH")
    assert len(few) == 1 and few[0].employee_id == 1 and few[0].work_date is None
    assert few[0].detail == {"need": 2, "have": 1, "work_days": 1, "paid_leave_days": 0}
    many = only(validate_draft(ctx, [work(1, "08:00", "12:00", d) for d in (MON, TUE, NEXT_MON)]), "REGULAR_DAYS_MISMATCH")
    assert many[0].detail["have"] == 3
    assert all(v.employee_id != 2 for v in few + many)  # 파트는 세지 않는다


@pytest.mark.req("V-5", "F-16")
def test_v5_paid_leave_from_availability_counts_as_a_day():
    """아즈키 check_shain_days 와 같이 희망의 유급(paid_leave)을 출근일에 1일로 센다."""
    ctx = make_ctx([emp(1, closing=True)], [av(1, "paid_leave", d=TUE)], off_days=28)
    assert only(validate_draft(ctx, [work(1, "08:00", "12:00", MON), paid(1, TUE)]), "REGULAR_DAYS_MISMATCH") == []
    assert only(validate_draft(ctx, [work(1, "08:00", "12:00", MON)]), "REGULAR_DAYS_MISMATCH") == []  # 유급 행이 없어도 희망에서 센다


@pytest.mark.req("V-5", "F-16")
def test_v5_paid_leave_row_without_request_is_not_counted():
    ctx = make_ctx([emp(1, closing=True)], off_days=28)
    got = only(validate_draft(ctx, [work(1, "08:00", "12:00", MON), paid(1, TUE)]), "REGULAR_DAYS_MISMATCH")
    assert got[0].detail["have"] == 1


@pytest.mark.req("V-5")
def test_v5_half_leave_day_with_work_counts_as_one_day():
    ctx = make_ctx([emp(1, closing=True)], [av(1, "half_leave", "09:00", "11:00", d=TUE)], off_days=28)
    vs = validate_draft(ctx, [work(1, "08:00", "12:00", MON), work(1, "09:00", "11:00", TUE)])
    assert only(vs, "REGULAR_DAYS_MISMATCH") == []


# ---------------------------------------------------------------- V-6 HALF_LEAVE_MISMATCH (기본 꺼 둠)
@pytest.mark.req("V-6")
def test_v6_is_off_by_default():
    ctx = make_ctx([part(2)], [av(2, "half_leave", "09:00", "11:00")])
    assert only(validate_draft(ctx, []), "HALF_LEAVE_MISMATCH") == []


@pytest.mark.req("V-6")
def test_v6_exact_half_leave_time_passes():
    ctx = make_ctx([part(2)], [av(2, "half_leave", "09:00", "11:00")], enable_half_leave_check=True)
    assert only(validate_draft(ctx, [work(2, "09:00", "11:00")]), "HALF_LEAVE_MISMATCH") == []


@pytest.mark.req("V-6")
def test_v6_mismatch_and_missing_are_caught_for_everyone():
    ctx = make_ctx([emp(1, closing=True), part(2)],
                   [av(1, "half_leave", "08:00", "10:00"), av(2, "half_leave", "09:00", "11:00")],
                   enable_half_leave_check=True)
    vs = only(validate_draft(ctx, [work(2, "09:00", "10:30")]), "HALF_LEAVE_MISMATCH")  # 사원 1 은 배정 없음
    by_emp = {v.employee_id: v for v in vs}
    assert by_emp[1].detail["reason"] == "missing" and by_emp[1].severity == "required"
    assert by_emp[2].detail == {"reason": "mismatch", "avail_start": "09:00", "avail_end": "11:00",
                                "assigned_start": "09:00", "assigned_end": "10:30"}


# ---------------------------------------------------------------- V-7 OUTSIDE_AVAILABILITY (사원, 경고, 기본 꺼 둠)
@pytest.mark.req("V-7")
def test_v7_is_off_by_default():
    ctx = make_ctx([emp(1, closing=True)], [av(1, "available", "09:00", "11:00")])
    assert only(validate_draft(ctx, [work(1, "08:00", "12:00")]), "OUTSIDE_AVAILABILITY") == []


@pytest.mark.req("V-7")
def test_v7_regular_inside_available_passes():
    ctx = make_ctx([emp(1, closing=True)], [av(1, "available", "08:00", "12:00")], enable_regular_availability_warning=True)
    assert only(validate_draft(ctx, [work(1, "08:00", "12:00")]), "OUTSIDE_AVAILABILITY") == []


@pytest.mark.req("V-7")
def test_v7_regular_outside_available_is_warning_and_does_not_block():
    ctx = make_ctx([emp(1, closing=True)], [av(1, "available", "09:00", "11:00")], enable_regular_availability_warning=True)
    vs = validate_draft(ctx, [work(1, "08:00", "12:00")])
    got = only(vs, "OUTSIDE_AVAILABILITY")
    assert len(got) == 1 and got[0].severity == "warning" and got[0].detail["kind"] == "regular"
    assert got[0] not in blocking(vs, set())


# ---------------------------------------------------------------- V-8 DOUBLE_BOOKING
@pytest.mark.req("V-8")
def test_v8_one_row_per_person_per_day_passes():
    vs = validate_draft(make_ctx([emp(1), emp(2)]), [work(1, "08:00", "12:00"), work(2, "08:00", "12:00")])
    assert only(vs, "DOUBLE_BOOKING") == []


@pytest.mark.req("V-8", "F-3")
@pytest.mark.parametrize("rows, overlap", [
    ([work(1, "08:00", "10:00"), work(1, "10:00", "12:00")], False),   # 겹치지 않아도 같은 날 2행
    ([work(1, "08:00", "11:00"), work(1, "10:00", "12:00")], True),
    ([work(1, "08:00", "12:00"), paid(1)], False),                     # 출근 + 유급 행
])
def test_v8_two_rows_same_day_is_caught_and_cannot_be_waived(rows, overlap):
    vs = validate_draft(make_ctx([emp(1, closing=True)]), rows)
    got = only(vs, "DOUBLE_BOOKING")
    assert len(got) == 1 and got[0].detail == {"count": 2, "overlap": overlap}
    assert not got[0].waivable and got[0] in blocking(vs, {got[0].key})


# ---------------------------------------------------------------- V-9 HEADCOUNT_SHORTAGE (+ T-3)
@pytest.mark.req("V-9")
def test_v9_setting_headcount_met_passes():
    ctx = make_ctx([emp(1), emp(2)], rules=[rule(mh=2)])
    assert only(validate_draft(ctx, [work(1, "08:00", "12:00"), work(2, "08:00", "12:00")]), "HEADCOUNT_SHORTAGE",
                work_date=MON) == []


@pytest.mark.req("V-9")
def test_v9_setting_shortage_is_warning():
    ctx = make_ctx([emp(1), emp(2)], rules=[rule(mh=2)])
    got = only(validate_draft(ctx, [work(1, "08:00", "12:00"), work(2, "08:00", "10:00")]), "HEADCOUNT_SHORTAGE",
               work_date=MON)
    assert times(got) == ["10:00", "10:30", "11:00", "11:30"]
    assert got[0].severity == "warning" and got[0].detail == {"time": "10:00", "source": "setting", "need": 2, "have": 1}


@pytest.mark.req("V-9", "T-3")
def test_v9_template_overrides_upwards_and_is_required():
    ctx = make_ctx([emp(1), emp(2), emp(3)], rules=[rule(mh=1)],
                   templates=[tmpl("min_headcount", "r1", count=3, start="08:00", end="10:00", weekday=1)])
    got = only(validate_draft(ctx, [work(1, "08:00", "12:00"), work(2, "08:00", "12:00")]), "HEADCOUNT_SHORTAGE",
               work_date=MON)
    assert times(got) == ["08:00", "08:30", "09:00", "09:30"]
    assert {v.severity for v in got} == {"required"}
    assert got[0].detail == {"time": "08:00", "source": "template", "rule_key": "r1", "need": 3, "have": 2}
    assert "r1" in got[0].key


@pytest.mark.req("V-9", "T-3")
def test_v9_template_overrides_even_when_lower():
    """V9_ko 514: 지시가 있는 칸은 지시의 숫자로 덮어쓴다(설정보다 적어도)."""
    ctx = make_ctx([emp(1), emp(2), emp(3)], rules=[rule(mh=3)],
                   templates=[tmpl("min_headcount", "r1", count=1, start="08:00", end="10:00")])
    got = only(validate_draft(ctx, [work(1, "08:00", "12:00")]), "HEADCOUNT_SHORTAGE", work_date=MON)
    assert times(got) == ["10:00", "10:30", "11:00", "11:30"]  # 08~10 은 need 1 → 충족
    assert {v.severity for v in got} == {"warning"}


@pytest.mark.req("V-9", "T-3")
def test_v9_template_applies_only_to_its_date_or_weekday():
    ctx = make_ctx([emp(1), emp(2)], rules=[rule(mh=1)],
                   templates=[tmpl("min_headcount", "rd", count=2, start="08:00", end="09:00", date="2026-11-09")])
    vs = validate_draft(ctx, [work(1, "08:00", "12:00", MON), work(1, "08:00", "12:00", NEXT_MON)])
    assert only(vs, "HEADCOUNT_SHORTAGE", work_date=MON) == []
    assert times(only(vs, "HEADCOUNT_SHORTAGE", work_date=NEXT_MON)) == ["08:00", "08:30"]


@pytest.mark.req("V-9", "T-3")
def test_v9_overlapping_templates_use_largest_count():
    ctx = make_ctx([emp(i) for i in range(1, 5)], rules=[rule(mh=1)], templates=[
        tmpl("min_headcount", "a", count=2, start="08:00", end="09:00"),
        tmpl("min_headcount", "b", count=3, start="08:00", end="09:00"),
    ])
    got = only(validate_draft(ctx, [work(1, "08:00", "12:00"), work(2, "08:00", "12:00")]), "HEADCOUNT_SHORTAGE",
               work_date=MON)
    assert [(v.detail["time"], v.detail["need"], v.detail["rule_key"]) for v in got] == [("08:00", 3, "b"), ("08:30", 3, "b")]


@pytest.mark.req("V-9", "T-3")
def test_v9_template_outside_business_hours_is_still_checked():
    """(가정 신규) 07:00~08:00 지시는 영업시간 밖이어도 본다."""
    ctx = make_ctx([emp(1)], rules=[rule(mh=1)], templates=[tmpl("min_headcount", "early", count=1, start="07:00", end="08:00")])
    got = only(validate_draft(ctx, [work(1, "08:00", "12:00")]), "HEADCOUNT_SHORTAGE", work_date=MON)
    assert times(got) == ["07:00", "07:30"] and got[0].severity == "required"


@pytest.mark.req("V-9")
def test_v9_theoretically_impossible_slot_is_excluded_and_reported():
    """V9_ko 476: 희망으로 본 최대 인원 < need 인 칸은 검사에서 빼고 impossible_slots() 로."""
    ctx = make_ctx([emp(1), part(2), part(3)], [av(2, "available", "08:00", "10:00"), av(3, "off")], rules=[rule(mh=3)])
    vs = validate_draft(ctx, [work(1, "08:00", "12:00")])
    # 08~10: 최대 2명(사원 1 + 파트 2) < 3 → 뺀다. 10~12: 최대 1명 < 3 → 뺀다. 결국 월요일 부족 위반은 없다.
    assert only(vs, "HEADCOUNT_SHORTAGE", work_date=MON) == []
    imp = [s for s in impossible_slots(ctx) if s["work_date"] == MON.isoformat()]
    assert [(s["time"], s["max_possible"]) for s in imp] == [
        ("08:00", 2), ("08:30", 2), ("09:00", 2), ("09:30", 2), ("10:00", 1), ("10:30", 1), ("11:00", 1), ("11:30", 1)]
    assert imp[0] == {"work_date": "2026-11-02", "time": "08:00", "start_time": "08:00", "end_time": "08:30", "need": 3,
                      "max_possible": 2, "severity": "warning", "source": "setting", "rule_key": None,
                      "reason": "theoretical_max_below_need"}


@pytest.mark.req("V-9")
def test_v9_fillable_slot_is_not_excluded():
    """최대 인원이 need 이상이면(배치만 안 했을 뿐) 위반으로 남는다."""
    ctx = make_ctx([emp(1), emp(2), part(3)], [av(3, "available", "08:00", "12:00")], rules=[rule(mh=3)])
    assert len(only(validate_draft(ctx, [work(1, "08:00", "12:00")]), "HEADCOUNT_SHORTAGE", work_date=MON)) == 8
    assert [s for s in impossible_slots(ctx) if s["work_date"] == MON.isoformat()] == []


# ---------------------------------------------------------------- V-10 HEADCOUNT_EXCESS (기본 꺼 둠)
@pytest.mark.req("V-10")
def test_v10_is_off_by_default():
    ctx = make_ctx([emp(1), emp(2)], rules=[rule(mh=1)])
    assert only(validate_draft(ctx, [work(1, "08:00", "12:00"), work(2, "08:00", "12:00")]), "HEADCOUNT_EXCESS") == []


@pytest.mark.req("V-10")
def test_v10_have_equal_want_passes():
    ctx = make_ctx([emp(1), emp(2)], rules=[rule(mh=2)], enable_surplus=True)
    assert only(validate_draft(ctx, [work(1, "08:00", "12:00"), work(2, "08:00", "12:00")]), "HEADCOUNT_EXCESS") == []


@pytest.mark.req("V-10")
def test_v10_excess_over_want_is_required_violation():
    ctx = make_ctx([emp(1), emp(2), emp(3)], rules=[rule(mh=1)], enable_surplus=True,
                   templates=[tmpl("min_headcount", "r1", count=2, start="08:00", end="10:00")])
    vs = validate_draft(ctx, [work(i, "08:00", "12:00") for i in (1, 2, 3)])
    got = only(vs, "HEADCOUNT_EXCESS", work_date=MON)
    assert len(got) == 8 and {v.severity for v in got} == {"required"}
    # G3: want = 지시 틀(2), 없으면 설정(1)
    assert got[0].detail == {"time": "08:00", "source": "template", "rule_key": "r1", "want": 2, "have": 3, "excess": 1}
    assert got[4].detail == {"time": "10:00", "source": "setting", "want": 1, "have": 3, "excess": 2}


# ---------------------------------------------------------------- M-8 휴게 포함 필요 인원 · 휴게 시각은 기록만
@pytest.mark.req("M-8")
def test_break_times_are_ignored_by_validation():
    """V9_ko 351: break_start/end 는 기록만, validate 는 보지 않는다 → 휴게 중인 칸도 그 사람을 센다 (min_headcount 는 휴게 포함)."""
    from app.domain import Assignment

    from ._factory import T

    ctx = make_ctx([emp(1, closing=True), emp(2, closing=True)], rules=[rule(mh=2, mq=0)])
    with_break = [Assignment(1, MON, "work", T("08:00"), T("12:00"), T("10:00"), T("11:00")), work(2, "08:00", "12:00")]
    no_break = [work(1, "08:00", "12:00"), work(2, "08:00", "12:00")]
    assert validate_draft(ctx, with_break) == validate_draft(ctx, no_break)
    assert only(validate_draft(ctx, with_break), "HEADCOUNT_SHORTAGE", work_date=MON) == []
