"""group_history (C3, F-5) · blocking (check_approval, F-8 · F-3) · 편집 후 재검증 (F-15) · detail 의 JSON 모양."""
from __future__ import annotations

import json
from datetime import date

import pytest

from app.domain import Violation
from app.validate import blocking, group_history, validate_draft

from ._factory import MON, TUE, av, emp, make_ctx, only, part, rule, tmpl, work


def V(code: str, sev: str = "required", d: date | None = MON, e: int | None = None, **detail) -> Violation:
    return Violation(code, sev, d, e, detail)  # type: ignore[arg-type]


# ---------------------------------------------------------------- group_history
@pytest.mark.req("F-5")
def test_group_history_groups_by_key_with_rounds_in_first_appearance_order():
    a1 = V("REGULAR_ABSENT", time="10:00", need=1, have=0)
    b = V("QUAL_MISSING", time="08:00", need=1, have=0)
    c = V("OUTSIDE_AVAILABILITY", e=2, kind="part", reason="no_submission")
    a3 = V("REGULAR_ABSENT", time="10:00", need=1, have=0)
    groups = group_history([[a1, b], [b, c, b], [a3]])
    assert [(g.check_code, g.rounds) for g in groups] == [
        ("REGULAR_ABSENT", (1, 3)), ("QUAL_MISSING", (1, 2)), ("OUTSIDE_AVAILABILITY", (2,))]
    assert groups[0].key == a1.key and groups[0].example is a3  # 가장 최근 회차의 것
    assert group_history([]) == [] and group_history([[], []]) == []


@pytest.mark.req("F-5")
def test_group_history_example_shows_latest_detail():
    r1 = V("HEADCOUNT_SHORTAGE", "warning", time="09:00", need=3, have=0)
    r2 = V("HEADCOUNT_SHORTAGE", "warning", time="09:00", need=3, have=2)
    (g,) = group_history([[r1], [r2]])
    assert g.rounds == (1, 2) and g.example.detail["have"] == 2 and g.severity == "warning"


@pytest.mark.req("F-5", "V-3")
def test_same_person_day_violation_keeps_key_when_llm_moves_the_time():
    """배정 1건 검사는 key 에 시각을 넣지 않는다 → 다음 회차에 시각만 바뀌어도 같은 묶음 (V9_ko 486)."""
    ctx = make_ctx([part(2)], [av(2, "available", "09:00", "11:00")])
    r1 = validate_draft(ctx, [work(2, "08:00", "11:00")])
    r2 = validate_draft(ctx, [work(2, "09:00", "12:00")])
    (g,) = [g for g in group_history([r1, r2]) if g.check_code == "OUTSIDE_AVAILABILITY"]
    assert g.rounds == (1, 2) and g.example.detail["assigned_start"] == "09:00"


# ---------------------------------------------------------------- blocking (check_approval)
@pytest.mark.req("F-8", "F-3")
def test_blocking_non_waivable_always_blocks_even_if_waived():
    q = V("QUAL_MISSING", time="08:00", need=1, have=0)
    d = V("DOUBLE_BOOKING", e=1, count=2, overlap=False)
    assert blocking([q, d], {q.key, d.key}) == [q, d]


@pytest.mark.req("F-8", "F-3")
def test_blocking_waived_required_passes_and_warning_never_blocks():
    r = V("REGULAR_ABSENT", time="10:00", need=1, have=0)
    r2 = V("REGULAR_ABSENT", time="10:30", need=1, have=0)
    w = V("HEADCOUNT_SHORTAGE", "warning", time="10:00", need=2, have=1)
    assert blocking([r, r2, w], set()) == [r, r2]
    assert blocking([r, r2, w], {r.key}) == [r2]
    assert blocking([r, r2, w], {r.key, r2.key}) == []
    assert blocking([], set()) == []


# ---------------------------------------------------------------- 편집 저장 → 재검증 (F-15)
@pytest.mark.req("F-15")
def test_revalidating_an_edited_draft_reflects_the_edit():
    """편집 모드(30분 단위)로 고친 안을 다시 넣으면 그 위반이 사라진다. 같은 입력이면 같은 결과(순수 함수)."""
    ctx = make_ctx([emp(1, closing=True), part(2)], [av(2, "available", "09:00", "11:00")])
    before = [work(1, "08:00", "12:00"), work(2, "09:00", "11:30")]
    after = [work(1, "08:00", "12:00"), work(2, "09:00", "11:00")]
    v1 = validate_draft(ctx, before)
    assert len(only(v1, "OUTSIDE_AVAILABILITY")) == 1
    assert only(validate_draft(ctx, after), "OUTSIDE_AVAILABILITY") == []
    assert validate_draft(ctx, before) == v1


# ---------------------------------------------------------------- 모양: 모든 코드가 한 번에 나오는 안
@pytest.mark.req("V-1", "V-2", "V-3", "V-4", "V-5", "V-6", "V-7", "V-8", "V-9", "V-10", "T-1", "T-2")
def test_every_code_detail_is_json_serialisable_and_keys_are_distinct():
    ctx = make_ctx(
        [emp(1, qual=False), emp(3, closing=True), part(2)],
        [av(2, "available", "09:00", "10:00"), av(3, "half_leave", "08:00", "10:00", d=TUE),
         av(1, "available", "10:00", "11:00")],
        rules=[rule(mh=2, mq=1), rule("08:00", "12:00", wd=2, mh=1)],
        templates=[tmpl("not_available", "na", staff_id=1, weekday=1),
                   tmpl("max_consecutive", "mc", shift="any", limit=1, staff_id=1),
                   tmpl("min_headcount", "mh", count=3, start="08:00", end="09:00", weekday=1)],
        off_days=0, enable_surplus=True, enable_half_leave_check=True, enable_regular_availability_warning=True,
    )
    draft = [work(1, "08:00", "12:00", MON), work(1, "08:00", "12:00", TUE), work(2, "09:00", "12:00", MON),
             work(3, "08:00", "09:00", MON), work(3, "11:00", "12:00", MON)]
    vs = validate_draft(ctx, draft)
    codes = {v.check_code for v in vs}
    assert codes >= {"REGULAR_ABSENT", "QUAL_MISSING", "OUTSIDE_AVAILABILITY", "CLOSING_NOT_ALLOWED",
                     "REGULAR_DAYS_MISMATCH", "HALF_LEAVE_MISMATCH", "DOUBLE_BOOKING", "HEADCOUNT_SHORTAGE",
                     "HEADCOUNT_EXCESS", "RULE_VIOLATION"}
    assert {v.detail.get("kind") for v in only(vs, "OUTSIDE_AVAILABILITY")} == {"part", "regular"}
    for v in vs:
        json.dumps(v.detail)
        assert v.severity in ("required", "warning")
    keys = [v.key for v in vs]
    assert len(keys) == len(set(keys))
