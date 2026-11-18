"""검증 규칙 V-1 ~ V-10 (V9_ko 466-479 표가 기준, 코드표 5-1 943-964).

- 규칙마다 함수 하나. 입력은 ContextIndex + 배정, 출력은 Violation 목록.
- detail 은 JSON 으로 바꿀 수 있는 값만 (시각 "HH:MM" 문자열 · int · bool · None).
- Violation.key 는 detail 의 "time"(없으면 "start")과 "rule_key" 를 쓴다 (app/domain.py).
  · 칸 검사(V-1 · V-2 · V-9 · V-10)는 detail["time"] = 칸 시작.
  · 배정 1건에 대한 검사(V-3 · V-4 · V-6 · V-7)는 시각을 "assigned_start"/"assigned_end" 로 넣고 "start" 키는 쓰지 않는다.
    → key = 코드|날짜|직원 이 되어, LLM 이 다음 회차에 시각만 옮겨도 「같은 위반」으로 묶인다 (C3, V9_ko 486).
    같은 key 가 두 번 나오지 않게 (직원, 날짜)마다 하나만 낸다. 하루 2행은 V-8 이 따로 잡는다.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date

from app.domain import SLOT_MINUTES, Assignment, Violation

from ._index import ContextIndex, Work, fmt, hhmm, people_at, to_min


def _a_detail(a: Assignment) -> dict:
    return {"assigned_start": fmt(a.start_time), "assigned_end": fmt(a.end_time)}


# ---------------------------------------------------------------- V-1 사원 1명 이상 (V9_ko 468, G9)
def check_regular_present(idx: ContextIndex, by_date: dict[date, list[Work]]) -> list[Violation]:
    """영업시간 동안 30분마다 사원(is_regular) 1명 이상. 아즈키 check_coverage(시제품 348-365)와 같은 뜻.
    아즈키는 08:00~22:30 을 코드에 박아 두었고, 여기서는 그 요일 store_time_rules 에서 읽는다 (G6)."""
    out: list[Violation] = []
    for d in idx.dates:
        day = by_date.get(d, [])
        for t in idx.business_slots(d):
            have = sum(1 for e in people_at(day, t) if idx.emps[e].is_regular)
            if have == 0:
                out.append(Violation("REGULAR_ABSENT", "required", d, None, {"time": hhmm(t), "need": 1, "have": 0}))
    return out


# ---------------------------------------------------------------- V-2 자격자 수 (V9_ko 469, G10) — 예외 불가
def check_qualified(idx: ContextIndex, by_date: dict[date, list[Work]]) -> list[Violation]:
    """30분마다 자격자 수 >= 그 칸을 덮는 시간대 규칙의 min_qualified.
    G10: min_qualified 를 그대로 쓴다 → 0 이거나 그 칸을 덮는 규칙이 없으면 검사하지 않는다.
    이론상 못 채우는 칸이어도 빼지 않는다 (빼는 것은 최소 인원뿐, V9_ko 476). 예외 불가 (V9_ko 469, 479)."""
    out: list[Violation] = []
    for d in idx.dates:
        day = by_date.get(d, [])
        for t in idx.business_slots(d):
            tr = idx.time_rule_at(d, t)
            if tr is None or tr.min_qualified <= 0:
                continue
            have = sum(1 for e in people_at(day, t) if idx.emps[e].has_qualification)
            if have < tr.min_qualified:
                out.append(Violation("QUAL_MISSING", "required", d, None,
                                     {"time": hhmm(t), "need": tr.min_qualified, "have": have}))
    return out


# ---------------------------------------------------------------- V-3 파트・알바 신청 안에서만 (V9_ko 470, 959)
def check_part_availability(idx: ContextIndex, work: Sequence[Work]) -> list[Violation]:
    """파트・알바(is_regular=false)의 출근 배정은 신청한 날・신청 시간 안에서만.

    - 행 없음 = 불가 (V9_ko 309, G19) → reason "no_submission"
    - off / paid_leave 인 날 → reason "off" / "paid_leave"
    - available / half_leave 의 시각 범위 밖 → reason "outside_time" (범위 = 신청 시작 <= 배정 시작, 배정 종료 <= 신청 종료)
    아즈키 check_requests(시제품 387-406)와 다른 점: 아즈키는 「신청한 날인가」만 본다. 표(V9_ko 470)는 「신청 시간 안」까지라 시각도 본다.
    (가정 신규) half_leave 의 시각도 「신청 시간」으로 보고 범위 검사를 한다. 「범위 안이지만 시각이 다름」은 V-6 이 본다(5-1 V9_ko 962).
    """
    out: list[Violation] = []
    seen: set[tuple[int, date]] = set()
    for w in work:
        e = idx.emps[w.employee_id]
        if e.is_regular or (w.employee_id, w.work_date) in seen:
            continue
        a = idx.avail.get((w.employee_id, w.work_date))
        if a is None:
            reason = "no_submission"
        elif a.kind in ("off", "paid_leave"):
            reason = a.kind
        elif a.start_time is None or a.end_time is None:
            reason = "no_submission"  # DB CHECK 로 생길 수 없는 모양 (V9_ko 313). 불가 쪽으로 본다
        elif to_min(a.start_time) <= w.start and w.end <= to_min(a.end_time):
            continue
        else:
            reason = "outside_time"
        seen.add((w.employee_id, w.work_date))
        out.append(Violation("OUTSIDE_AVAILABILITY", "required", w.work_date, w.employee_id, {
            "kind": "part", "reason": reason, **_a_detail(w.src),
            "avail_kind": a.kind if a else None,
            "avail_start": fmt(a.start_time) if a else None, "avail_end": fmt(a.end_time) if a else None,
        }))
    return out


# ---------------------------------------------------------------- V-4 폐점까지는 closing_ok 만 (V9_ko 471, G6, G9)
def stays_to_close(idx: ContextIndex, w: Work) -> bool:
    """그 날 폐점 시각까지 남는 배정인가 = 마지막 칸 [폐점-30, 폐점) 에 걸치도록 끝나는가.
    아즈키 check_closing 의 `end > "22:00"`(폐점 22:30) 과 같은 판정. 휴업 요일(규칙 없음)은 False."""
    h = idx.hours(w.work_date)
    return h is not None and w.end > h[1] - SLOT_MINUTES


def check_closing(idx: ContextIndex, work: Sequence[Work]) -> list[Violation]:
    """폐점 시각(그 요일 가장 늦은 종료, G6)까지 남는 사람은 closing_ok 만.

    (가정 신규) 사원에게도 적용한다. 표(V9_ko 471)와 열 설명(V9_ko 255)이 고용 형태를 가리지 않기 때문.
    아즈키 check_closing(시제품 409-422)은 파트・알바만 본다 — 다른 점. 시연 데이터(app/seed.py)는 사원을 closing_ok=true 로 넣는다.
    """
    out: list[Violation] = []
    seen: set[tuple[int, date]] = set()
    for w in work:
        if idx.emps[w.employee_id].closing_ok or (w.employee_id, w.work_date) in seen:
            continue
        if stays_to_close(idx, w):
            seen.add((w.employee_id, w.work_date))
            out.append(Violation("CLOSING_NOT_ALLOWED", "required", w.work_date, w.employee_id,
                                 {"closing": hhmm(idx.hours(w.work_date)[1]), **_a_detail(w.src)}))  # type: ignore[index]
    return out


# ---------------------------------------------------------------- V-5 사원 출근일 (V9_ko 472, G9)
def check_regular_days(idx: ContextIndex, work: Sequence[Work]) -> list[Violation]:
    """사원 출근일(유급 포함) = 기간 일수 − designated_off_days.

    아즈키 check_shain_days(시제품 425-444)와 같은 셈:
    - 출근 배정이 있는 날을 1일씩 (같은 날 여러 행이어도 1일). 반휴(half_leave) 날도 출근 배정이 있으면 1일 (V9_ko 340, 472).
    - 희망(availability)이 paid_leave 인 날을 더한다(이미 출근으로 센 날은 겹쳐 세지 않음).
    - paid_leave 배정 행은 세지 않는다: 유급 행은 코드가 희망에서 채우는 것이라(F-16) 희망에 없는 유급 행은 승인된 유급이 아니다 (보수 쪽).
    - 기간 밖 날짜의 배정은 세지 않는다.
    """
    work_days: dict[int, set[date]] = defaultdict(set)
    for w in work:
        if w.work_date in idx.date_set:
            work_days[w.employee_id].add(w.work_date)
    paid: dict[int, set[date]] = defaultdict(set)
    for a in idx.ctx.availability:
        if a.kind == "paid_leave" and a.work_date in idx.date_set:
            paid[a.employee_id].add(a.work_date)
    need = max(0, len(idx.dates) - idx.ctx.period.designated_off_days)
    out: list[Violation] = []
    for e in idx.ctx.employees:
        if not e.is_regular:
            continue
        w_days = work_days.get(e.employee_id, set())
        p_days = paid.get(e.employee_id, set()) - w_days
        have = len(w_days) + len(p_days)
        if have != need:
            out.append(Violation("REGULAR_DAYS_MISMATCH", "required", None, e.employee_id,
                                 {"need": need, "have": have, "work_days": len(w_days), "paid_leave_days": len(p_days)}))
    return out


# ---------------------------------------------------------------- V-6 반휴는 신청 시각 그대로 (V9_ko 473, 961-964, G5) — 기본 꺼 둠
def check_half_leave(idx: ContextIndex, work: Sequence[Work]) -> list[Violation]:
    """half_leave 를 낸 날(정직원・파트・알바 모두)은 신청 시각과 같은 출근 배정 1건.
    - 배정 없음 → reason "missing" (V9_ko 964 「배치가 없는 경우는 위반」 추측 → REQUIREMENTS V-6 이 위반으로 정함)
    - 신청 시각과 다른 출근 배정이 있음 → reason "mismatch" (같은 날 2행이면 하나가 맞아도 다른 하나로 mismatch. 2행 자체는 V-8)."""
    by_key: dict[tuple[int, date], list[Work]] = defaultdict(list)
    for w in work:
        by_key[(w.employee_id, w.work_date)].append(w)
    out: list[Violation] = []
    for a in idx.ctx.availability:
        if a.kind != "half_leave" or a.employee_id not in idx.emps or a.start_time is None or a.end_time is None:
            continue
        ws = by_key.get((a.employee_id, a.work_date), [])
        base = {"avail_start": fmt(a.start_time), "avail_end": fmt(a.end_time)}
        if not ws:
            out.append(Violation("HALF_LEAVE_MISMATCH", "required", a.work_date, a.employee_id,
                                 {"reason": "missing", **base, "assigned_start": None, "assigned_end": None}))
            continue
        s, e = to_min(a.start_time), to_min(a.end_time)
        bad = [w for w in ws if (w.start, w.end) != (s, e)]
        if bad:
            out.append(Violation("HALF_LEAVE_MISMATCH", "required", a.work_date, a.employee_id,
                                 {"reason": "mismatch", **base, **_a_detail(bad[0].src)}))
    return out


# ---------------------------------------------------------------- V-7 사원 available 시각 (V9_ko 474, 960, G5) — 기본 꺼 둠, 경고
def check_regular_availability(idx: ContextIndex, work: Sequence[Work]) -> list[Violation]:
    """사원이 available(출근 희망일)을 낸 날, 배정이 그 시각 범위 밖이면 경고. detail.kind="regular" 로 V-3 과 구분.
    off · paid_leave 인 날의 사원 배정은 이 규칙이 보지 않는다 (표 V9_ko 474 가 available 만 말한다)."""
    out: list[Violation] = []
    seen: set[tuple[int, date]] = set()
    for w in work:
        if not idx.emps[w.employee_id].is_regular or (w.employee_id, w.work_date) in seen:
            continue
        a = idx.avail.get((w.employee_id, w.work_date))
        if a is None or a.kind != "available" or a.start_time is None or a.end_time is None:
            continue
        if to_min(a.start_time) <= w.start and w.end <= to_min(a.end_time):
            continue
        seen.add((w.employee_id, w.work_date))
        out.append(Violation("OUTSIDE_AVAILABILITY", "warning", w.work_date, w.employee_id, {
            "kind": "regular", "reason": "outside_time", **_a_detail(w.src),
            "avail_kind": a.kind, "avail_start": fmt(a.start_time), "avail_end": fmt(a.end_time),
        }))
    return out


# ---------------------------------------------------------------- V-8 중복 배치 (V9_ko 475, 952) — 예외 불가
def check_double_booking(known: Sequence[Assignment]) -> list[Violation]:
    """같은 사람・같은 날 배정 행이 2개 이상이면 위반 (work · paid_leave 를 가리지 않는다).
    DB 는 PK(편성안, 직원, 날짜)로 1인 1일 1행이라(V9_ko 353) 2행이면 저장도 못 한다. overlap = 출근 시각끼리 겹치는가."""
    rows: dict[tuple[int, date], list[Assignment]] = defaultdict(list)
    for a in known:
        rows[(a.employee_id, a.work_date)].append(a)
    out: list[Violation] = []
    for (emp, d), rs in rows.items():
        if len(rs) < 2:
            continue
        iv = sorted((to_min(a.start_time), to_min(a.end_time)) for a in rs
                    if a.kind == "work" and a.start_time is not None and a.end_time is not None)
        overlap = any(iv[i + 1][0] < iv[i][1] for i in range(len(iv) - 1))
        out.append(Violation("DOUBLE_BOOKING", "required", d, emp, {"count": len(rs), "overlap": overlap}))
    out.sort(key=lambda v: (v.work_date, v.employee_id))
    return out


# ---------------------------------------------------------------- V-9 최소 인원 / V-10 余り (V9_ko 476-477, 514, G3, G12)
def check_headcount(idx: ContextIndex, by_date: dict[date, list[Work]], *, surplus: bool) -> list[Violation]:
    """V-9: 칸마다 have < need 이면 HEADCOUNT_SHORTAGE. need 와 종류는 ContextIndex.need_at (틀=required / 설정=warning).
    이론상 못 채우는 칸(희망으로 본 최대 인원 < need)은 빼고 impossible_slots() 로 보고한다 (V9_ko 476).
    아즈키 check_headcount(시제품 507-525)와 다른 점: 아즈키는 시간대(규칙) 단위로 「가장 적은 칸」 1건을 내고,
    시간대 안에 못 채우는 칸이 하나라도 있으면 그 시간대를 통째로 뺀다. 여기서는 칸 단위로 보고 빼는 것도 그 칸만.

    V-10(surplus=True 일 때만, G3): want = 같은 need. have − want > 0 이면 HEADCOUNT_EXCESS (required).
    need 가 0 인 칸(틀도 설정도 없는 빈 시간)은 want 가 없으므로 보지 않는다 (G3 「없으면 store_time_rules」 → 그것도 없으면 없음).
    """
    out: list[Violation] = []
    for d in idx.dates:
        day = by_date.get(d, [])
        for t in idx.headcount_slots(d):
            need = idx.need_at(d, t)
            if need.count <= 0:
                continue
            have = len(people_at(day, t))
            base = {"time": hhmm(t), "source": need.source}
            if need.rule_key is not None:
                base["rule_key"] = need.rule_key
            if have < need.count and idx.max_possible(d, t) >= need.count:
                out.append(Violation("HEADCOUNT_SHORTAGE", need.severity, d, None,  # type: ignore[arg-type]
                                     {**base, "need": need.count, "have": have}))
            if surplus and have > need.count:
                out.append(Violation("HEADCOUNT_EXCESS", "required", d, None,
                                     {**base, "want": need.count, "have": have, "excess": have - need.count}))
    return out


def impossible(idx: ContextIndex) -> list[dict]:
    """V-9 에서 뺀 칸 목록 (부족 보고용, V9_ko 476). 칸 1개 = dict 1개, 날짜・시각 순."""
    out: list[dict] = []
    for d in idx.dates:
        for t in idx.headcount_slots(d):
            need = idx.need_at(d, t)
            if need.count <= 0:
                continue
            mx = idx.max_possible(d, t)
            if mx < need.count:
                out.append({
                    "work_date": d.isoformat(), "time": hhmm(t),
                    "start_time": hhmm(t), "end_time": hhmm(t + SLOT_MINUTES),
                    "need": need.count, "max_possible": mx,
                    "severity": need.severity, "source": need.source, "rule_key": need.rule_key,
                    "reason": "theoretical_max_below_need",
                })
    return out

