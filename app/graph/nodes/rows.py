"""노드들이 같이 쓰는 도우미: 배정 행 다듬기 · 검증 부르기 · 부족 보고.

배정 행 = {employee_id, work_date, kind, start_time, end_time, break_start, break_end}.
State 와 DB 에 그대로 넣을 수 있는 JSON 모양이다 (날짜 "YYYY-MM-DD", 시각 "HH:MM", 없으면 None).
"""
from __future__ import annotations

from datetime import date, time

from app.graph.state import ShiftState, asg_to_dict, ctx_from_state, dict_to_asg, jsonable, viol_to_dict


# ---------------------------------------------------------------- 값 읽기


def to_int(value) -> int | None:
    """정수로 읽을 수 있으면 정수, 아니면 None. True / False 는 정수로 치지 않는다."""
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def hhmm(t: time) -> str:
    return t.strftime("%H:%M")


def _as_time(value):
    """글자("09:30")면 시각으로 바꾼다. 그 밖의 값은 그대로 돌려준다."""
    return time.fromisoformat(value) if isinstance(value, str) else value


def _slot_time(value, field: str) -> time:
    """근무 시작・종료 시각. 30분 단위가 아니면 ValueError.

    근거: V9_ko 165, 447 · DB CHECK (shift_assignments)
    """
    t = _as_time(value)
    if not isinstance(t, time):
        raise ValueError(f"{field} 시각이 아님: {value!r}")
    if t.minute not in (0, 30) or t.second or t.microsecond:
        raise ValueError(f"{field} 는 30분 단위: {value!r}")
    return t


# ---------------------------------------------------------------- 배정 행


def make_row(employee_id: int, work_date: str, kind: str, start_time: str | None = None,
             end_time: str | None = None, break_start: str | None = None,
             break_end: str | None = None) -> dict:
    """배정 행 하나. 시각을 주지 않으면 전부 None (유급 행)."""
    return {"employee_id": employee_id, "work_date": work_date, "kind": kind,
            "start_time": start_time, "end_time": end_time, "break_start": break_start, "break_end": break_end}


def normalize_rows(rows, state: ShiftState) -> list[dict]:
    """배정 목록을 배정 행 모양으로 다듬는다. 넣을 수 없는 행이 하나라도 있으면 ValueError.

    DB CHECK 와 같은 것을 여기서 먼저 본다. 저장 단계에서 그래프가 죽지 않게 하기 위해서다.
    같은 사람・같은 날 두 행은 여기서 막지 않는다. 그것은 검증의 DOUBLE_BOOKING 이 잡는다.

    근거: V9_ko 344-360 (DB CHECK), V-8 (DOUBLE_BOOKING)
    """
    if not isinstance(rows, list):
        raise ValueError("assignments 는 목록이어야 한다")
    staff_ids = {employee["employee_id"] for employee in state["llm_all_staff"]}
    target_month = date.fromisoformat(state["period"]["target_month"])

    normalized = []
    for position, row in enumerate(rows, start=1):
        try:
            normalized.append(_normalize_row(row, staff_ids, target_month))
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise ValueError(f"{position}번째 행: {error}") from None
    return normalized


def _normalize_row(row, staff_ids: set[int], target_month: date) -> dict:
    if not isinstance(row, dict):
        row = asg_to_dict(row)

    employee_id = to_int(row.get("employee_id"))
    if employee_id not in staff_ids:
        raise ValueError(f"이 점포의 직원이 아님: {row.get('employee_id')!r}")

    work_date = row["work_date"]
    if isinstance(work_date, str):
        work_date = date.fromisoformat(work_date)
    in_target_month = (isinstance(work_date, date)
                       and (work_date.year, work_date.month) == (target_month.year, target_month.month))
    if not in_target_month:
        raise ValueError(f"기간 밖의 날짜: {row['work_date']!r}")

    kind = row.get("kind") or "work"
    if kind == "work":
        return make_row(employee_id, work_date.isoformat(), kind, *_work_times(row))
    if kind == "paid_leave":
        if any(row.get(field) is not None for field in ("start_time", "end_time", "break_start", "break_end")):
            raise ValueError("유급(paid_leave)은 시각이 없다")
        return make_row(employee_id, work_date.isoformat(), kind)
    raise ValueError(f"kind 는 work / paid_leave: {kind!r}")


def _work_times(row: dict) -> tuple[str, str, str | None, str | None]:
    """근무 행의 (시작, 종료, 휴게 시작, 휴게 종료). 규칙에 맞지 않으면 ValueError."""
    start, end = row.get("start_time"), row.get("end_time")
    if start is None or end is None:
        raise ValueError("근무(work)는 시작・종료가 필요")
    start, end = _slot_time(start, "start_time"), _slot_time(end, "end_time")
    if end <= start:
        raise ValueError("end_time > start_time 이어야 한다")

    break_start, break_end = row.get("break_start"), row.get("break_end")
    if (break_start is None) != (break_end is None):
        raise ValueError("휴게는 시작・종료를 같이")
    if break_start is None:
        return hhmm(start), hhmm(end), None, None

    break_start, break_end = _as_time(break_start), _as_time(break_end)
    if not (start <= break_start < break_end <= end):
        raise ValueError("휴게는 근무 시간 안")
    return hhmm(start), hhmm(end), hhmm(break_start), hhmm(break_end)


def with_paid_leave(rows: list[dict], availability: list[dict]) -> list[dict]:
    """유급(paid_leave) 신청 날을 배정 목록에 더한다. 유급 행은 LLM 이 아니라 코드가 싣는다.

    (가정 신규) 그 날에 근무(work) 행이 이미 있어도 유급 행을 더한다. 그러면 같은 사람・같은 날 두 행이 되어
    검증이 DOUBLE_BOOKING(예외 불가)으로 잡는다. 유급이 조용히 사라지지 않는 쪽을 골랐다 (점장 편집도 같다).

    근거: F-16, V9_ko 300 「시프트에는 실리지만 출근은 아니다」, V-8
    """
    already_on_leave = {(row["employee_id"], row["work_date"]) for row in rows if row["kind"] == "paid_leave"}
    result = list(rows)
    for request in availability:
        person_day = (request["employee_id"], request["work_date"])
        if request["kind"] == "paid_leave" and person_day not in already_on_leave:
            result.append(make_row(request["employee_id"], request["work_date"], "paid_leave"))
            already_on_leave.add(person_day)
    return result


# ---------------------------------------------------------------- 검증


def run_validation(state: ShiftState, rows: list[dict], deps) -> list[dict]:
    """배정 목록을 코드 검증에 넣고 위반 목록(JSON 모양)을 돌려준다."""
    ctx = ctx_from_state(state, deps.settings)
    assignments = [dict_to_asg(row) for row in rows]
    return [viol_to_dict(violation) for violation in deps.validate_draft(ctx, assignments)]


def has_required(problems: list[dict]) -> bool:
    """반드시 고쳐야 하는(required) 위반이 하나라도 있는가."""
    return any(problem["severity"] == "required" for problem in problems)


def result_without_retry(problems: list[dict]) -> str:
    """자동 재편성으로 되돌리지 않는 안(직원 연락・점장 편집)에 붙이는 결과 이름.

    required 위반이 남으면 limit_reached 다. 위반을 안고 승인 화면으로 간다는 뜻으로 쓴다
    (ReviewWait.result 에 「편집안」 값이 따로 없다).
    """
    return "limit_reached" if has_required(problems) else "valid"


# ---------------------------------------------------------------- 부족 보고


def shortage_report(state: ShiftState, llm_shortages) -> list[dict]:
    """화면 참고용 부족 보고. 코드가 계산한 「이론상 못 채우는 칸」 + LLM 이 낸 shortages.

    근거: F-14
    """
    return _merge_impossible(state.get("impossible") or []) + list(jsonable(llm_shortages or []))


def _merge_impossible(slots) -> list[dict]:
    """30분마다 1줄인 impossible_slots 행을 화면 계약 모양으로 바꾸고, 이어지는 칸을 한 줄로 합친다.

    계약 모양 = {work_date, start_time, end_time, need, have, reason}.
    have = 그 칸에 이론상 최대로 넣을 수 있는 인원 (max_possible).
    합치는 조건 = 같은 날이고, 앞 줄의 끝이 이 칸의 시작이고, need · have 가 같다.

    근거: 리뷰 B M2 (계약과 모양이 달라 화면의 「配置」 칸이 비고 30분마다 수십 줄이 됐다)
    """
    merged: list[dict] = []
    by_date_and_start = sorted((slot for slot in slots if isinstance(slot, dict)),
                               key=lambda slot: (slot["work_date"], slot["start_time"]))
    for slot in by_date_and_start:
        have = slot.get("max_possible", slot.get("have"))
        last = merged[-1] if merged else None
        continues_last = (last is not None
                          and last["work_date"] == slot["work_date"] and last["end_time"] == slot["start_time"]
                          and last["need"] == slot["need"] and last["have"] == have)
        if continues_last:
            last["end_time"] = slot["end_time"]
            continue
        merged.append({"work_date": slot["work_date"], "start_time": slot["start_time"],
                       "end_time": slot["end_time"], "need": slot["need"], "have": have,
                       "reason": "impossible"})
    return merged
