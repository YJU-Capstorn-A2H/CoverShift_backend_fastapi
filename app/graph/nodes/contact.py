"""직원 연락: 인원이 부족한 칸에 한 명씩 「근무 가능하신가요?」를 묻는다. ENABLE_CONTACT=true 일 때만 돈다.

    contact (후보 1명에게 묻기) ⇄ contact_wait (답 기다리기 ⏸)
       └─ 물어볼 사람이 없거나 상한이면 → save_draft

묻기와 기다리기를 두 노드로 나눈 이유: interrupt 가 있는 노드는 재개 때 처음부터 다시 돈다.
한 노드에 두면 답이 올 때마다 같은 질문을 또 보낸다.

근거: M-6, G8
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta

from langgraph.types import Command, interrupt

from app.graph.nodes.rows import hhmm, make_row, result_without_retry, run_validation, to_int
from app.graph.state import ShiftState
from app.repo import drafts as repo

log = logging.getLogger("graph")

# (가정 신규) 직원 연락은 편성 한 건(thread) 전체에서 3번까지 묻고 그만둔다. 명세에 횟수가 없다.
# 직원 연락은 「맨 마지막・기본 꺼 둠」이라 작게 둔다. 무한히 묻지 않게 하는 가장 작은 상한이다.
# 근거: V9_ko 63, 85 「다음 단계」, 450
CONTACT_ASK_LIMIT = 3

# 직원의 답 → contact_requests.status (db/002)
ANSWER_STATUS = {"yes": "accepted", "no": "declined", "expired": "expired"}

HALF_HOUR = timedelta(minutes=30)


# ---------------------------------------------------------------- 노드


def contact(state: ShiftState, deps) -> Command:
    """부족한 칸 하나에 후보 1명에게 묻는 메시지를 발송함에 적는다(발송은 발송기). 답은 contact_wait 가 기다린다."""
    asks_so_far = state.get("contact_asks", 0)
    if asks_so_far >= CONTACT_ASK_LIMIT:
        return Command(goto="save_draft")
    with deps.connect() as conn:
        pick = _pick_contact(state, repo.line_linked_employee_ids(conn, state["store_id"]))
        if pick is None:
            log.info("contact 물어볼 후보 없음 → 부족을 안고 점장에게")
            return Command(goto="save_draft")
        question = f"{pick['work_date']} {pick['start_time']}~{pick['end_time']} 근무 가능하신가요?"
        contact_id = repo.ask_contact(conn, period_id=state["period_id"], **pick)
        deps.enqueue(conn, store_id=state["store_id"], employee_id=pick["employee_id"], kind="contact_ask",
                     dedupe_key=f"contact:{contact_id}",
                     body={"text": question, "contact_id": contact_id, "buttons": ["yes", "no"], **pick})
    log.info("contact ask contact_id=%s employee=%s %s %s~%s", contact_id, pick["employee_id"],
             pick["work_date"], pick["start_time"], pick["end_time"])

    asked_slot = [pick["employee_id"], pick["work_date"], pick["start_time"]]
    return Command(goto="contact_wait", update={
        "contact_pending": {"type": "contact", "contact_id": contact_id, **pick},  # ContactWait (API_CONTRACT)
        "contact_asks": asks_so_far + 1,
        "contact_asked": list(state.get("contact_asked") or []) + [asked_slot],
    })


def contact_wait(state: ShiftState, deps) -> Command:
    """직원의 답을 기다린다 (interrupt). 재개 값 = {"contact_id", "answer": "yes" | "no"} (API_CONTRACT).

    다른 질문의 답이나 모양이 틀린 값은 버리고 같은 질문으로 다시 기다린다.
    답 기록은 이 노드가 한다. 「아직 asked 인 행만 고친다」라서 다시 돌아도 결과가 같다.
    """
    pending = state["contact_pending"]
    resumed = interrupt(pending)
    reply = resumed if isinstance(resumed, dict) else {}
    answer = reply.get("answer")
    is_answer_to_this_question = to_int(reply.get("contact_id")) == pending["contact_id"]
    if not is_answer_to_this_question or answer not in ANSWER_STATUS:
        log.info("contact_wait 버린 답: %r", resumed)
        return Command(goto="contact_wait")

    with deps.connect() as conn:
        repo.answer_contact(conn, contact_id=pending["contact_id"], status=ANSWER_STATUS[answer])

    update: dict = {"contact_pending": None}
    if answer == "yes":
        update.update(_add_accepted_shift(state, pending, deps))
    return Command(goto="contact", update=update)


def _add_accepted_shift(state: ShiftState, pending: dict, deps) -> dict:
    """「가능」이라고 답한 사람을 그 칸의 근무 행으로 초안에 더하고 다시 검증한다. State 에 넣을 값을 돌려준다.

    연락으로 바뀐 안은 auto_draft 로 되돌리지 않는다. 되돌리면 받은 「가능」이 사라진다.
    """
    employee_id, work_date = pending["employee_id"], pending["work_date"]
    new_row = make_row(employee_id, work_date, "work", pending["start_time"], pending["end_time"])
    rows = list(state["draft_store_shift"]) + [new_row]

    availability = list(state["staff_availability"])
    has_request_that_day = any(request["employee_id"] == employee_id and request["work_date"] == work_date
                               for request in availability)
    if not has_request_that_day:
        # (가정 신규) 「가능」 답 = 그 칸의 출근 가능 신청으로 본다. State 에만 더한다
        # (DB 의 희망 행은 고치지 않는다. 답의 기록은 contact_requests 에 있다).
        # 이것이 없으면 희망을 안 낸 알바는 「희망 밖 배정」(OUTSIDE_AVAILABILITY) 위반으로 걸린다.
        # 근거: V-3, G19
        availability.append({"employee_id": employee_id, "work_date": work_date, "kind": "available",
                             "start_time": pending["start_time"], "end_time": pending["end_time"]})

    problems = run_validation({**state, "staff_availability": availability}, rows, deps)
    return {"draft_store_shift": rows, "staff_availability": availability, "problems": problems,
            "result": result_without_retry(problems)}


# ---------------------------------------------------------------- 누구에게 물을지


def _pick_contact(state: ShiftState, line_linked: set[int]) -> dict | None:
    """부족한 창 하나와 물어볼 후보 1명을 고른다. 물어볼 사람이 없으면 None. line_linked = LINE 이 연결된 직원 ID.

    근거: 10/26 「한 명씩 순서대로」, G8
    """
    for work_date, start, end in _shortage_windows(state.get("problems") or []):
        candidates = _candidates(state, work_date, start, end, line_linked)
        if candidates:
            return {"employee_id": candidates[0], "work_date": work_date,
                    "start_time": hhmm(start), "end_time": hhmm(end)}
    return None


def _candidates(state: ShiftState, work_date: str, start: time, end: time, line_linked: set[int]) -> list[int]:
    """그 창에 물어볼 수 있는 사람들. 앞에 있을수록 먼저 묻는다.

    순서: ① 그 날 희망을 안 낸 사람 ② 그 창을 덮는 「가능」(available)을 낸 사람. 같은 순위는 employee_id 순.
    빼는 사람:
    - 사원 — (가정 신규) 출근일 수가 정해져 있어 하루를 더하면 REGULAR_DAYS_MISMATCH 가 된다 (V-5)
    - LINE 이 연결되지 않은 사람 — 질문이 닿지 않아(발송함에서 no_recipient) 그래프가 오지 않을 답을 기다리게 된다
    - 그 날 이미 배정된 사람
    - 같은 칸을 이미 물어본 사람
    - 폐점 시각까지인 창인데 마감 근무(closing_ok)가 안 되는 사람 (V-4)
    - 그 날 희망이 off · paid_leave · half_leave 이거나, 「가능」이지만 그 창을 덮지 못하는 사람

    근거: 10/26 연락 대상 1단계
    """
    already_asked = {tuple(slot) for slot in state.get("contact_asked") or []}
    assigned = {(row["employee_id"], row["work_date"]) for row in state["draft_store_shift"]}
    requests = {(request["employee_id"], request["work_date"]): request
                for request in state["staff_availability"]}
    closing = _closing_time(state["time_rules"], work_date)
    window_runs_to_closing = closing is not None and end >= closing

    no_request, available = [], []
    for employee in sorted(state["llm_all_staff"], key=lambda employee: employee["employee_id"]):
        employee_id = employee["employee_id"]
        if employee["is_regular"] or (employee_id, work_date) in assigned:
            continue
        if employee_id not in line_linked:
            continue
        if (employee_id, work_date, hhmm(start)) in already_asked:
            continue
        if window_runs_to_closing and not employee["closing_ok"]:
            continue

        request = requests.get((employee_id, work_date))
        if request is None:
            no_request.append(employee_id)
        elif request["kind"] == "available" and _covers(request, start, end):
            available.append(employee_id)
    return no_request + available


def _closing_time(time_rules: list[dict], work_date: str) -> time | None:
    """그 날 요일의 시간대 규칙 가운데 가장 늦은 종료 시각. 규칙이 없으면 None."""
    weekday = date.fromisoformat(work_date).isoweekday()
    ends = [time.fromisoformat(rule["end_time"]) for rule in time_rules if rule["weekday"] == weekday]
    return max(ends) if ends else None


def _covers(request: dict, start: time, end: time) -> bool:
    """출근 가능 신청이 창 전체를 덮는가."""
    return time.fromisoformat(request["start_time"]) <= start and end <= time.fromisoformat(request["end_time"])


# ---------------------------------------------------------------- 부족한 창


def _shortage_windows(problems: list[dict]) -> list[tuple[str, time, time]]:
    """인원 부족(HEADCOUNT_SHORTAGE) 위반 → (날짜, 시작, 끝) 창 목록. 이어지는 30분 칸은 한 창으로 묶는다."""
    slots_by_date = _short_slots_by_date(problems)
    windows = []
    for work_date in sorted(slots_by_date):
        for start, end in _join_consecutive(sorted(slots_by_date[work_date])):
            windows.append((work_date, start, end))
    return windows


def _short_slots_by_date(problems: list[dict]) -> dict[str, set[time]]:
    """날짜 → 인원이 부족한 30분 칸의 시작 시각들."""
    slots: dict[str, set[time]] = {}
    for problem in problems:
        if problem["check_code"] != "HEADCOUNT_SHORTAGE" or not problem.get("work_date"):
            continue
        detail = problem.get("detail") or {}
        start_text = detail.get("time") or detail.get("start")
        if not start_text:
            continue
        # 위반 하나가 한 칸(끝 없음)일 수도, 여러 칸(끝 있음)일 수도 있다
        cursor = datetime.combine(date.min, time.fromisoformat(start_text))
        if detail.get("end"):
            stop = datetime.combine(date.min, time.fromisoformat(detail["end"]))
        else:
            stop = cursor + HALF_HOUR
        while cursor < stop:
            slots.setdefault(problem["work_date"], set()).add(cursor.time())
            cursor += HALF_HOUR
    return slots


def _join_consecutive(slot_starts: list[time]) -> list[tuple[time, time]]:
    """시각 순으로 놓인 30분 칸들을, 끊기지 않고 이어지는 것끼리 (시작, 끝) 으로 묶는다."""
    windows = []
    window_start = last_slot = slot_starts[0]
    for slot in slot_starts[1:]:
        if slot == _half_hour_after(last_slot):
            last_slot = slot
            continue
        windows.append((window_start, _half_hour_after(last_slot)))
        window_start = last_slot = slot
    windows.append((window_start, _half_hour_after(last_slot)))
    return windows


def _half_hour_after(t: time) -> time:
    return (datetime.combine(date.min, t) + HALF_HOUR).time()
