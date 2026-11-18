"""아즈키 시연 데이터(2026-11, 17명, 희망 286행)로 돌리는 현실 크기 시험 (DB 없음)."""
from __future__ import annotations

import time as _time
from collections import Counter
from datetime import date, time

import pytest

from app.domain import Assignment, ValidateOptions
from app.seed import load_azuki_context
from app.validate import impossible_slots, validate_draft

SLOTS_PER_DAY = 29  # 08:00~22:30, 30분 칸


@pytest.mark.req("M-1", "V-1", "V-2", "V-5", "V-9")
def test_empty_draft_on_azuki_context_is_caught_fast():
    ctx = load_azuki_context()
    t0 = _time.perf_counter()
    vs = validate_draft(ctx, [])
    sec = _time.perf_counter() - t0
    assert sec < 2.0, f"{sec:.3f}s"
    c = Counter(v.check_code for v in vs)
    assert set(c) == {"REGULAR_ABSENT", "QUAL_MISSING", "REGULAR_DAYS_MISMATCH", "HEADCOUNT_SHORTAGE"}
    assert c["REGULAR_ABSENT"] == 30 * SLOTS_PER_DAY
    assert c["QUAL_MISSING"] == 30 * SLOTS_PER_DAY
    regulars = [e.employee_id for e in ctx.employees if e.is_regular]
    assert sorted(v.employee_id for v in vs if v.check_code == "REGULAR_DAYS_MISMATCH") == sorted(regulars) == [1, 2, 3, 4, 16]
    # 빈 안: 출근 0 + 희망의 유급 일수 = have (아즈키 check_shain_days 와 같은 셈). 사원 1 은 유급 2일
    v1 = next(v for v in vs if v.check_code == "REGULAR_DAYS_MISMATCH" and v.employee_id == 1)
    assert v1.detail == {"need": 21, "have": 2, "work_days": 0, "paid_leave_days": 2}
    assert {v.severity for v in vs if v.check_code == "HEADCOUNT_SHORTAGE"} == {"warning"}  # 지시 틀 없음 → 설정 = 경고


@pytest.mark.req("V-9")
def test_azuki_impossible_slots_match_prototype_impossible_cases():
    """아즈키 impossible_cases(11/07 실험 RESULTS.md): 11/01 09~13 (최대 3<4) · 11/20 08~09 (2<3) · 11/20 09~13 (3<4).
    여기서는 08~09 의 「薬剤師 제외」를 표현할 열이 없어 11/20 08~09 는 못 채움으로 나오지 않는다 (알려진 차이)."""
    imp = impossible_slots(load_azuki_context())
    got = sorted({(s["work_date"], s["time"], s["need"], s["max_possible"]) for s in imp})
    expect = sorted((d, f"{h:02d}:{m:02d}", 4, 3) for d in ("2026-11-01", "2026-11-20") for h in range(9, 13) for m in (0, 30))
    assert got == expect
    # 뺀 칸은 HEADCOUNT_SHORTAGE 에 나오지 않는다
    vs = validate_draft(load_azuki_context(), [])
    short = {(v.work_date.isoformat(), v.detail["time"]) for v in vs if v.check_code == "HEADCOUNT_SHORTAGE"}
    assert not short & {(d, t) for d, t, _, _ in got}


@pytest.mark.req("V-6", "V-10")
def test_azuki_options_on_add_only_their_codes():
    ctx = load_azuki_context(options=ValidateOptions(enable_surplus=True, enable_half_leave_check=True,
                                                     enable_regular_availability_warning=True))
    c = Counter(v.check_code for v in validate_draft(ctx, []))
    half_rows = sum(1 for a in ctx.availability if a.kind == "half_leave")
    assert half_rows == 6 and c["HALF_LEAVE_MISMATCH"] == half_rows  # 빈 안 → 반휴 6건 모두 「배치 없음」
    assert c["HEADCOUNT_EXCESS"] == 0  # 빈 안에는 余り 가 없다


@pytest.mark.req("V-1", "V-2", "V-4")
def test_azuki_regular_closing_shift_is_allowed_but_part_without_closing_ok_is_not():
    """시드: 사원은 closing_ok=true (가정 신규), 파트 9・10 만 원본 closing_ok=true. 22:30 까지 남는 파트 5 는 위반."""
    ctx = load_azuki_context()
    d = date(2026, 11, 3)  # 사원 1: 13:30~22:30 희망 / 파트 5: 그 날 희망 확인은 V-3 이 따로 본다
    vs = validate_draft(ctx, [Assignment(1, d, "work", time(13, 30), time(22, 30)),
                              Assignment(5, d, "work", time(17, 0), time(22, 30))])
    closing = [v for v in vs if v.check_code == "CLOSING_NOT_ALLOWED"]
    assert [(v.employee_id, v.detail["closing"]) for v in closing] == [(5, "22:30")]
    absent = [v.detail["time"] for v in vs if v.check_code == "REGULAR_ABSENT" and v.work_date == d]
    assert absent == [f"{h:02d}:{m:02d}" for h in range(8, 13) for m in (0, 30)] + ["13:00"]
