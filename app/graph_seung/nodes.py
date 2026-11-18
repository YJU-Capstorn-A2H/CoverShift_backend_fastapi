"""편성 그래프의 노드 (담당: graph 에이전트). 근거: V9_ko 59-121 (1-1・1-2・1-3, 1-3 이 우선), 466-489.

모양: 노드 = (state, deps) -> dict | Command. build.py 가 deps 를 묶어 StateGraph 에 올린다.
- 바깥 동작(새 행 만들기・LINE 스텁)은 interrupt 가 있는 노드에 두지 않는다. interrupt 노드는 재개 때 처음부터 다시 돌기
  때문이다 (prototype_aegra E2 「노드는 처음부터 다시 돎」). 그래서 묻기(contact)와 기다리기(contact_wait),
  기다리기(manager_review)와 기록(request_redraft / apply_edit / commit_notify)을 나눴다.
  예외 하나: contact_wait 의 답 기록 (UPDATE ... WHERE status='asked' — 다시 돌아도 결과가 같다).
- 바깥 동작은 전부 다시 돌아도 결과가 같게 만들었다 (dedupe_key, ON CONFLICT, 「이미 있으면 그 행」).
- 노드 이름은 아즈키 시제품(collect / auto_draft / validate / contact)과 1-2 표(manager_review / check_approval /
  commit_notify)를 따른다.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta

from langgraph.types import Command, interrupt

from app.domain import parse_template_rule
from app.graph_seung.state import (ShiftState, asg_to_dict, ctx_from_state, dict_to_asg, dict_to_viol, jsonable,
                             review_wait, viol_to_dict)
from app.repo import drafts as repo

log = logging.getLogger("graph")

# (가정 신규) 직원 연락은 thread 전체에서 3번까지 묻고 그만둔다. 명세에 횟수가 없다 (V9_ko 63, 85 「다음 단계」).
#   이유: M-6 은 「맨 마지막・기본 꺼 둠」(V9_ko 450) 이라 작게 둔다. 무한히 묻지 않게 하는 가장 작은 상한.
CONTACT_ASK_LIMIT = 3

ANSWER_STATUS = {"yes": "accepted", "no": "declined", "expired": "expired"}  # db/002 contact_log.status


# ================================================================ 작은 도우미 (순수 함수)


def _required(problems: list[dict]) -> bool:
    return any(p["severity"] == "required" for p in problems)


def _int(v) -> int | None:
    if isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _hm(t: time) -> str:
    return t.strftime("%H:%M")


def _slot_time(v, what: str) -> time:
    t = time.fromisoformat(v) if isinstance(v, str) else v
    if not isinstance(t, time):
        raise ValueError(f"{what} 시각이 아님: {v!r}")
    if t.minute not in (0, 30) or t.second or t.microsecond:
        raise ValueError(f"{what} 는 30분 단위: {v!r}")  # V9_ko 165, 447 · DB CHECK (shift_assignments)
    return t


def normalize_rows(rows, state: ShiftState) -> list[dict]:
    """배정 목록을 DB 에 그대로 넣을 수 있는 JSON 모양으로 다듬는다. 넣을 수 없는 행이 하나라도 있으면 ValueError.

    DB CHECK (V9_ko 344-360) 와 같은 것을 먼저 본다 → 저장 단계에서 그래프가 죽지 않게.
    같은 사람・같은 날 두 행은 여기서 막지 않는다 — 그것은 검증의 DOUBLE_BOOKING(V-8) 이 잡는다.
    """
    if not isinstance(rows, list):
        raise ValueError("assignments 는 목록이어야 한다")
    ids = {e["employee_id"] for e in state["llm_all_staff"]}
    month = date.fromisoformat(state["period"]["target_month"])
    out = []
    for i, r in enumerate(rows):
        try:
            if not isinstance(r, dict):
                r = asg_to_dict(r)
            emp = _int(r.get("employee_id"))
            if emp not in ids:
                raise ValueError(f"이 점포의 직원이 아님: {r.get('employee_id')!r}")
            wd = date.fromisoformat(r["work_date"]) if isinstance(r["work_date"], str) else r["work_date"]
            if not isinstance(wd, date) or (wd.year, wd.month) != (month.year, month.month):
                raise ValueError(f"기간 밖의 날짜: {r['work_date']!r}")
            kind = r.get("kind") or "work"
            st, et = r.get("start_time"), r.get("end_time")
            bs, be = r.get("break_start"), r.get("break_end")
            if kind == "work":
                if st is None or et is None:
                    raise ValueError("근무(work)는 시작・종료가 필요")
                st, et = _slot_time(st, "start_time"), _slot_time(et, "end_time")
                if et <= st:
                    raise ValueError("end_time > start_time 이어야 한다")
                if (bs is None) != (be is None):
                    raise ValueError("휴게는 시작・종료를 같이")
                if bs is not None:
                    bs = time.fromisoformat(bs) if isinstance(bs, str) else bs
                    be = time.fromisoformat(be) if isinstance(be, str) else be
                    if not (st <= bs < be <= et):
                        raise ValueError("휴게는 근무 시간 안")
            elif kind == "paid_leave":
                if any(x is not None for x in (st, et, bs, be)):
                    raise ValueError("유급(paid_leave)은 시각이 없다")
            else:
                raise ValueError(f"kind 는 work / paid_leave: {kind!r}")
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            raise ValueError(f"{i + 1}번째 행: {e}") from None
        out.append({"employee_id": emp, "work_date": wd.isoformat(), "kind": kind,
                    "start_time": _hm(st) if st else None, "end_time": _hm(et) if et else None,
                    "break_start": _hm(bs) if bs else None, "break_end": _hm(be) if be else None})
    return out


def with_paid_leave(rows: list[dict], availability: list[dict]) -> list[dict]:
    """F-16: 유급(paid_leave) 신청 날은 코드가 시프트에 싣는다 (V9_ko 300 「시프트에는 실리지만 출근은 아니다」).

    (가정 신규) 그 날에 근무(work) 행이 이미 있어도 유급 행을 더한다 → 같은 사람・같은 날 두 행 = DOUBLE_BOOKING(V-8,
    예외 불가)으로 검증이 잡는다. 유급이 조용히 사라지지 않게 하는 쪽을 골랐다 (점장 편집도 같다).
    """
    have = {(r["employee_id"], r["work_date"]) for r in rows if r["kind"] == "paid_leave"}
    out = list(rows)
    for a in availability:
        if a["kind"] == "paid_leave" and (a["employee_id"], a["work_date"]) not in have:
            out.append({"employee_id": a["employee_id"], "work_date": a["work_date"], "kind": "paid_leave",
                        "start_time": None, "end_time": None, "break_start": None, "break_end": None})
            have.add((a["employee_id"], a["work_date"]))
    return out


def _validate(state: ShiftState, rows: list[dict], deps) -> list[dict]:
    ctx = ctx_from_state(state, deps.settings)
    return [viol_to_dict(v) for v in deps.validate_draft(ctx, [dict_to_asg(r) for r in rows])]


def _shortages(state: ShiftState, llm_shortages) -> list[dict]:
    """화면 참고용 부족 보고 (F-14). 코드가 계산한 「이론상 못 채우는 칸」+ LLM 이 낸 shortages."""
    return _merge_impossible(state.get("impossible") or []) + list(jsonable(llm_shortages or []))


def _merge_impossible(rows) -> list[dict]:
    """impossible_slots 행(30분마다 1줄, max_possible) → 계약 모양 {work_date, start_time, end_time, need, have, reason}.
    have = 그 칸에 이론상 최대로 넣을 수 있는 인원(max_possible). 같은 날 이어지고 need · have 가 같은 칸은 한 줄로 합친다.
    (리뷰 B M2: 계약과 모양이 달라 화면의 「配置」 칸이 비고 30분마다 수십 줄이 됐다)"""
    out: list[dict] = []
    for s in sorted((r for r in rows if isinstance(r, dict)), key=lambda r: (r["work_date"], r["start_time"])):
        have = s.get("max_possible", s.get("have"))
        p = out[-1] if out else None
        if p and p["work_date"] == s["work_date"] and p["end_time"] == s["start_time"] \
                and p["need"] == s["need"] and p["have"] == have:
            p["end_time"] = s["end_time"]
            continue
        out.append({"work_date": s["work_date"], "start_time": s["start_time"], "end_time": s["end_time"],
                    "need": s["need"], "have": have, "reason": "impossible"})
    return out


# ================================================================ 노드


def collect(state: ShiftState, deps) -> dict:
    """직원 정보(MCP, 이름 없음) + 기간・시간대 규칙 + 마지막으로 확인된 틀 규칙을 읽어 State 에 고정한다 (F-17, V9_ko 73)."""
    period_id, given = state["period_id"], state.get("store_id")
    with deps.connect() as conn:
        period = repo.load_period(conn, period_id)
        store_id = period["store_id"]  # 점포는 기간에서 정한다 (입력에 없어도 된다 — GraphInput 참조)
        if given is not None and given != store_id:
            raise ValueError(f"period {period_id} 는 store {given} 의 기간이 아니다")
        time_rules = repo.load_time_rules(conn, store_id)
        rules = repo.latest_rules(conn, period_id)
    staff = deps.get_staff_info(period_id)  # M-7: 이름 열을 못 읽는 DB 롤 (app/mcp_tools.py)
    extra_rules = jsonable(rules["rules"]) if rules else []
    for r in extra_rules:  # T-4: 틀이 틀린 규칙은 고정하지 않는다 (V9_ko 1170). 입구 API 가 이미 검사했으므로 여기서 걸리면 버그
        parse_template_rule(r["rule_key"], r["template_key"], r["params"], r.get("source_text", ""))
    st: ShiftState = {
        "period": jsonable(period), "time_rules": jsonable(time_rules),
        "llm_all_staff": jsonable(staff["employees"]), "staff_availability": jsonable(staff["availability"]),
        "extra_rules": extra_rules, "free_text": list(rules["free_text"]) if rules else [],
        "rules_action_id": rules["action_id"] if rules else None,
    }
    impossible = jsonable(deps.impossible_slots(ctx_from_state(st, deps.settings)))
    log.info("collect period=%s staff=%d rules=%d impossible=%d", period_id, len(st["llm_all_staff"]),
             len(extra_rules), len(impossible))
    return {**st, "store_id": store_id, "impossible": impossible,
            "instruction": None, "draft_store_shift": [], "shortages": [], "notes": [], "compose_error": None,
            "problems": [], "violation_history": [], "auto_retry_count": 0, "result": "", "draft_id": None,
            "contact_asks": 0, "contact_asked": [], "contact_pending": None,
            "manager_request_count": 0, "decision": None, "reviewer_id": None, "review_input": None,
            "waived_violations": [], "blocked": [], "review_error": None, "notified": False}


def auto_draft(state: ShiftState, deps) -> Command:
    """LLM 편성 1회 (V9_ko 482-488). 결과는 반드시 validate 를 거친다 (V9_ko 83). DB 에는 쓰지 않는다 (F-1).

    재시도 입력 = 직전 안(근무 행만) + 이력 전부를 묶은 것 + 점장 지시문 (F-5, V9_ko 486).
    초안 0건(ok=False・0행・저장할 수 없는 행)은 자동 재편성 1회로 센다 (F-4).
    """
    s = deps.settings
    ctx = ctx_from_state(state, s)
    previous = [dict_to_asg(r) for r in state.get("draft_store_shift") or [] if r["kind"] == "work"] or None
    rounds = [[dict_to_viol(v) for v in rnd] for rnd in state.get("violation_history") or []]
    res = deps.composer.compose(ctx, previous=previous, history=deps.group_history(rounds),
                                instruction=state.get("instruction"))
    err = None if res.ok else (res.error or "failed")
    rows: list[dict] = []
    if err is None:
        try:
            # 유급 행은 코드 몫 (F-16) → LLM 이 낸 것은 근무 행만 쓴다
            rows = normalize_rows([a for a in res.assignments if getattr(a, "kind", "work") == "work"], state)
        except ValueError as e:
            err = f"invalid_rows: {e}"  # F-4 「읽을 수 없음」
        else:
            if not rows:
                err = "empty"  # F-4 「0행」
    n = state.get("auto_retry_count", 0)
    if err is None:
        log.info("auto_draft ok rows=%d (auto_retry=%d)", len(rows), n)
        return Command(goto="validate", update={
            "draft_store_shift": with_paid_leave(rows, state["staff_availability"]),
            "shortages": _shortages(state, res.shortages), "notes": [str(x) for x in res.notes or []],
            "compose_error": None})
    if n < s.auto_retry_limit:
        log.info("auto_draft 초안 0건 (%s) → 재편성 %d/%d", err, n + 1, s.auto_retry_limit)
        # draft_store_shift 는 그대로 둔다 → 다음 compose 의 「직전 안」은 마지막으로 읽을 수 있었던 안
        return Command(goto="auto_draft", update={"auto_retry_count": n + 1, "result": "draft_empty",
                                                  "compose_error": err})
    # (가정 신규) 상한에서도 0건이면 유급 행만 있는 빈 안을 검사・저장해서 승인 화면으로 보낸다 → 점장이 직접 편집(수동 편성)
    #   하거나 재편성을 요청한다 (V9_ko 517 「아즈키 안: 0건이면 실패 → 수동 편성 + 점장에게 메시지」). ReviewWait 에는 늘 draft_id 가 있다.
    log.warning("auto_draft 초안 0건 (%s) — 자동 재편성 상한 %d 도달", err, s.auto_retry_limit)
    return Command(goto="validate", update={
        "draft_store_shift": with_paid_leave([], state["staff_availability"]),
        "shortages": _shortages(state, []), "compose_error": err,
        "notes": [f"자동 편성이 초안을 만들지 못했다 ({err}). 직접 편집하거나 재편성을 요청한다."]})


def validate(state: ShiftState, deps) -> Command:
    """코드 검증 (V9_ko 466-479). required 위반 → auto_draft (자동 상한 3, F-2). 상한 → 위반을 안고 승인 화면 (F-3)."""
    s = deps.settings
    problems = _validate(state, state["draft_store_shift"], deps)
    upd: dict = {"problems": problems, "blocked": [], "review_error": None}
    if state.get("compose_error"):  # 상한에서 초안 0건 (auto_draft 참조). 빈 안의 위반은 LLM 이력에 넣지 않는다
        upd["result"] = "draft_empty"
        return Command(goto="save_draft", update=upd)
    upd["violation_history"] = list(state.get("violation_history") or []) + [problems]
    n = state.get("auto_retry_count", 0)
    if not _required(problems):
        upd["result"] = "valid"
    elif n < s.auto_retry_limit:
        upd["result"] = "retry"
        upd["auto_retry_count"] = n + 1
        log.info("validate required 위반 %d건 → 재편성 %d/%d", sum(p["severity"] == "required" for p in problems),
                 n + 1, s.auto_retry_limit)
        return Command(goto="auto_draft", update=upd)
    else:
        upd["result"] = "limit_reached"
    log.info("validate result=%s 위반 %d건", upd["result"], len(problems))
    return Command(goto=_after_check(state, problems, s), update=upd)


def _after_check(state: ShiftState, problems: list[dict], s) -> str:
    if (s.enable_contact and state.get("contact_asks", 0) < CONTACT_ASK_LIMIT
            and any(p["check_code"] == "HEADCOUNT_SHORTAGE" for p in problems)):
        return "contact"
    return "save_draft"


# ---------------------------------------------------------------- 직원 연락 (M-6, G8 — ENABLE_CONTACT=true 일 때만)


def _shortage_windows(problems: list[dict]) -> list[tuple[str, time, time]]:
    """HEADCOUNT_SHORTAGE 칸 → 날짜별로 이어지는 30분 칸을 하나의 창으로 묶는다."""
    slots: dict[str, set[time]] = {}
    for p in problems:
        if p["check_code"] != "HEADCOUNT_SHORTAGE" or not p.get("work_date"):
            continue
        d = p.get("detail") or {}
        start = d.get("time") or d.get("start")
        if not start:
            continue
        st = time.fromisoformat(start)
        et = time.fromisoformat(d["end"]) if d.get("end") else None
        cur = datetime.combine(date.min, st)
        end = datetime.combine(date.min, et) if et else cur + timedelta(minutes=30)
        while cur < end:
            slots.setdefault(p["work_date"], set()).add(cur.time())
            cur += timedelta(minutes=30)
    out = []
    for wd in sorted(slots):
        ts = sorted(slots[wd])
        run_s = prev = ts[0]
        for t in ts[1:] + [None]:
            nxt = (datetime.combine(date.min, prev) + timedelta(minutes=30)).time()
            if t is not None and t == nxt:
                prev = t
                continue
            out.append((wd, run_s, nxt))
            if t is not None:
                run_s = prev = t
    return out


def _pick_contact(state: ShiftState) -> dict | None:
    """부족한 창 하나와 물어볼 후보 1명을 고른다 (10/26 「한 명씩 순서대로」, G8).

    (가정 신규) 후보는 파트・알바만. 사원은 출근일 수가 정해져 있어(V-5) 하루를 더하면 REGULAR_DAYS_MISMATCH 가 된다.
    순서: ① 그 날 희망을 안 낸 사람 (10/26 연락 대상 1단계) ② 그 창을 덮는 available 을 낸 사람. 같은 순위는 employee_id 순.
    빼는 사람: 그 날 이미 배정된 사람, 그 날 off・paid_leave・half_leave, 같은 칸을 이미 물어본 사람,
    폐점 시각까지인 창인데 closing_ok 가 아닌 사람 (V-4).
    """
    asked = {tuple(x) for x in state.get("contact_asked") or []}
    staff = {e["employee_id"]: e for e in state["llm_all_staff"]}
    av = {(a["employee_id"], a["work_date"]): a for a in state["staff_availability"]}
    busy = {(r["employee_id"], r["work_date"]) for r in state["draft_store_shift"]}
    for wd, st, et in _shortage_windows(state.get("problems") or []):
        wk = date.fromisoformat(wd).isoweekday()
        ends = [time.fromisoformat(r["end_time"]) for r in state["time_rules"] if r["weekday"] == wk]
        closing = max(ends) if ends else None
        first, second = [], []
        for eid, e in sorted(staff.items()):
            if e["is_regular"] or (eid, wd) in busy or (eid, wd, _hm(st)) in asked:
                continue
            if closing is not None and et >= closing and not e["closing_ok"]:
                continue
            a = av.get((eid, wd))
            if a is None:
                first.append(eid)
            elif (a["kind"] == "available" and time.fromisoformat(a["start_time"]) <= st
                  and et <= time.fromisoformat(a["end_time"])):
                second.append(eid)
        cands = first + second
        if cands:
            return {"employee_id": cands[0], "work_date": wd, "start_time": _hm(st), "end_time": _hm(et)}
    return None


def contact(state: ShiftState, deps) -> Command:
    """부족 칸에 후보 1명에게 LINE(스텁)으로 묻는다. 기다리기는 contact_wait 가 한다 (재개 때 다시 보내지 않게)."""
    if state.get("contact_asks", 0) >= CONTACT_ASK_LIMIT:
        return Command(goto="save_draft")
    pick = _pick_contact(state)
    if pick is None:
        log.info("contact 물어볼 후보 없음 → 부족을 안고 점장에게")
        return Command(goto="save_draft")
    with deps.connect() as conn:
        cid = repo.ask_contact(conn, period_id=state["period_id"], **pick)
        deps.send(conn, store_id=state["store_id"], employee_id=pick["employee_id"], kind="contact_ask",
                  dedupe_key=f"contact:{cid}",
                  body={"text": f"{pick['work_date']} {pick['start_time']}~{pick['end_time']} 근무 가능하신가요?",
                        "contact_id": cid, "buttons": ["yes", "no"], **pick})
    pending = {"type": "contact", "contact_id": cid, **pick}  # ContactWait (API_CONTRACT)
    log.info("contact ask contact_id=%s employee=%s %s %s~%s", cid, pick["employee_id"], pick["work_date"],
             pick["start_time"], pick["end_time"])
    return Command(goto="contact_wait", update={
        "contact_pending": pending, "contact_asks": state.get("contact_asks", 0) + 1,
        "contact_asked": list(state.get("contact_asked") or []) + [[pick["employee_id"], pick["work_date"],
                                                                    pick["start_time"]]]})


def contact_wait(state: ShiftState, deps) -> Command:
    """직원 답장 대기 (interrupt). resume 값 = {"contact_id", "answer": "yes"|"no"} (API_CONTRACT).
    다른 질문의 답・모양이 틀린 값은 버리고 같은 질문으로 다시 기다린다."""
    pending = state["contact_pending"]
    ans = interrupt(pending)
    a = ans if isinstance(ans, dict) else {}
    answer = a.get("answer")
    if _int(a.get("contact_id")) != pending["contact_id"] or answer not in ANSWER_STATUS:
        log.info("contact_wait 버린 답: %r", ans)
        return Command(goto="contact_wait")
    with deps.connect() as conn:
        repo.answer_contact(conn, contact_id=pending["contact_id"], status=ANSWER_STATUS[answer])
    upd: dict = {"contact_pending": None}
    if answer == "yes":
        row = {"employee_id": pending["employee_id"], "work_date": pending["work_date"], "kind": "work",
               "start_time": pending["start_time"], "end_time": pending["end_time"],
               "break_start": None, "break_end": None}
        rows = list(state["draft_store_shift"]) + [row]
        av = list(state["staff_availability"])
        if not any(x["employee_id"] == row["employee_id"] and x["work_date"] == row["work_date"] for x in av):
            # (가정 신규) 「가능」 답 = 그 창의 출근 가능 신청으로 본다. State 에만 더한다 (DB 희망 행은 고치지 않는다.
            #   답의 기록은 contact_log). 이것이 없으면 희망 미제출 알바는 V-3 OUTSIDE_AVAILABILITY 로 걸린다 (G19).
            av.append({"employee_id": row["employee_id"], "work_date": row["work_date"], "kind": "available",
                       "start_time": row["start_time"], "end_time": row["end_time"]})
        st2 = {**state, "staff_availability": av}
        problems = _validate(st2, rows, deps)
        # 연락으로 바뀐 안은 auto_draft 로 되돌리지 않는다 (받은 「가능」이 사라지므로). 결과 이름은 승인 화면용으로만 다시 붙인다.
        upd.update({"draft_store_shift": rows, "staff_availability": av, "problems": problems,
                    "result": "limit_reached" if _required(problems) else "valid"})
    return Command(goto="contact", update=upd)


# ---------------------------------------------------------------- 저장・점장 확인


def save_draft(state: ShiftState, deps) -> dict:
    """검사가 끝난 안을 DB 에 저장 (F-1, V9_ko 93). 위반도 같이 (G4). parent = 바로 전에 저장된 안."""
    with deps.connect() as conn:
        did = repo.save_draft(conn, period_id=state["period_id"], assignments=state["draft_store_shift"],
                              violations=state["problems"], parent_draft_id=state.get("draft_id"))
    log.info("save_draft draft_id=%s parent=%s result=%s", did, state.get("draft_id"), state.get("result"))
    return {"draft_id": did}


def manager_review(state: ShiftState, deps) -> Command:
    """점장 확인 대기 (interrupt, V9_ko 86). resume 값 = {"decision", "manager_id", "draft_id", ...} (API_CONTRACT).

    - draft_id 가 지금 안이 아니면(늦게 온 클릭・두 번 클릭) 아무것도 바꾸지 않고 다시 기다린다.
    - 이 노드는 State 만 바꾼다. DB 기록은 다음 노드가 한다 (재개 때 이 노드는 처음부터 다시 돌기 때문).
    """
    s = deps.settings
    payload = interrupt(review_wait(state, s))
    p = payload if isinstance(payload, dict) else {}
    if _int(p.get("draft_id")) != state.get("draft_id"):
        log.info("manager_review 지금 안이 아닌 draft_id=%r → 무시", p.get("draft_id"))
        return Command(goto="manager_review")
    decision, mid = p.get("decision"), _int(p.get("manager_id"))
    if mid is None:  # 승인자 ID 는 반드시 남긴다 (F-7, V9_ko 54)
        return Command(goto="manager_review", update={"review_error": "manager_id 가 없는 조작은 받지 않는다"})
    base = {"reviewer_id": mid, "review_error": None}
    if decision == "approved":
        waived = sorted({str(k) for k in p.get("waived_keys") or []})
        return Command(goto="check_approval", update={**base, "decision": "approved", "waived_violations": waived,
                                                       "review_input": None})
    if decision == "redraft_requested":
        n = state.get("manager_request_count", 0)
        if n >= s.manager_request_limit:  # F-2: 점장 요청 2회 (V9_ko 479)
            return Command(goto="manager_review", update={
                "review_error": f"재편성 요청은 {s.manager_request_limit}회까지다. 승인하거나 직접 편집한다."})
        instr = p.get("instruction")
        instr = instr.strip() if isinstance(instr, str) and instr.strip() else None
        return Command(goto="request_redraft", update={**base, "decision": "redraft_requested", "blocked": [],
                                                        "review_input": {"instruction": instr}})
    if decision == "edit_saved":
        try:
            rows = normalize_rows(p.get("assignments"), state)
        except ValueError as e:
            return Command(goto="manager_review", update={"review_error": f"편집 내용을 받지 않았다: {e}"})
        return Command(goto="apply_edit", update={**base, "decision": "edit_saved", "blocked": [],
                                                   "review_input": {"assignments": rows}})
    return Command(goto="manager_review", update={"review_error": f"알 수 없는 조작: {decision!r}"})


def check_approval(state: ShiftState, deps) -> Command:
    """승인 전 최종 검사 (F-8, V9_ko 87). 예외 불가 위반 + 허용 안 된 required 가 있으면 확정하지 않고 승인 화면으로."""
    blocked = deps.blocking([dict_to_viol(v) for v in state.get("problems") or []],
                            set(state.get("waived_violations") or []))
    if blocked:
        log.info("check_approval 막힘 %d건", len(blocked))
        return Command(goto="manager_review", update={"blocked": [viol_to_dict(v) for v in blocked]})
    return Command(goto="commit_notify", update={"blocked": []})


def request_redraft(state: ShiftState, deps) -> dict:
    """점장 재편성 요청을 기록하고 auto_draft 로 (F-2, F-7).

    (가정 신규) 자동 재편성 횟수(auto_retry_count)를 0으로 되돌린다 — 새 지시로 만드는 안도 자동 3회까지 고칠 수 있게.
      명세는 「자동 3회・점장 요청 2회를 따로 센다」(V9_ko 479)까지만 쓴다. 최악의 LLM 호출 = (1+3) × (1+2) = 12회.
    """
    instr = (state.get("review_input") or {}).get("instruction")
    with deps.connect() as conn:
        repo.record_action_once(conn, period_id=state["period_id"], draft_id=state["draft_id"],
                                manager_id=state["reviewer_id"], action="redraft_requested", instruction=instr)
    return {"manager_request_count": state.get("manager_request_count", 0) + 1, "instruction": instr,
            "auto_retry_count": 0, "waived_violations": [], "blocked": []}


def apply_edit(state: ShiftState, deps) -> dict:
    """편집 저장 (F-15, G17): 편집안을 다시 검증하고 새 안으로 저장한다 (parent = 지금 안). 점장 요청 횟수는 세지 않는다.

    결과 이름: required 위반이 없으면 valid, 있으면 limit_reached (위반을 안고 승인 화면 — ReviewWait.result 에 「편집안」 값이 없어서).
    """
    rows = with_paid_leave(state["review_input"]["assignments"], state["staff_availability"])
    problems = _validate(state, rows, deps)
    with deps.connect() as conn:
        repo.record_action_once(conn, period_id=state["period_id"], draft_id=state["draft_id"],
                                manager_id=state["reviewer_id"], action="edit_saved")
    return {"draft_store_shift": rows, "problems": problems, "compose_error": None,
            "violation_history": list(state.get("violation_history") or []) + [problems],
            "result": "limit_reached" if _required(problems) else "valid",
            "shortages": _shortages(state, []), "notes": [], "waived_violations": [], "blocked": []}


def commit_notify(state: ShiftState, deps) -> dict:
    """확정 저장 + 직원별 LINE 통지 1회 (F-9, V9_ko 88). 한 트랜잭션.

    다시 돌아도 같다: 확정은 「이미 확정이면 그대로」, 승인 기록은 부분 유일 인덱스 + ON CONFLICT,
    통지는 dedupe_key = confirm:{draft_id}:{employee_id} (line_stub).
    """
    did, mid = state["draft_id"], state["reviewer_id"]
    allowed = {p["key"] for p in state.get("problems") or [] if p["waivable"]} & set(state.get("waived_violations") or [])
    month = state["period"]["target_month"][:7]
    with deps.connect() as conn:
        repo.confirm_draft(conn, period_id=state["period_id"], draft_id=did, manager_id=mid,
                           waived_keys=sorted(allowed))
        rows = repo.load_assignments(conn, did)  # 통지는 DB 에 확정된 그대로 (State 가 아니라)
        sent = 0
        for e in state["llm_all_staff"]:
            eid = e["employee_id"]
            work = [{"work_date": r["work_date"], "start_time": r["start_time"], "end_time": r["end_time"]}
                    for r in rows if r["employee_id"] == eid and r["kind"] == "work"]
            leave = [r["work_date"] for r in rows if r["employee_id"] == eid and r["kind"] == "paid_leave"]
            sent += deps.send(conn, store_id=state["store_id"], employee_id=eid, kind="shift_confirmed",
                              dedupe_key=f"confirm:{did}:{eid}",
                              body={"text": f"{month} 시프트가 확정되었습니다. 출근 {len(work)}일.", "draft_id": did,
                                    "work_days": work, "paid_leave_days": leave})
    log.info("commit_notify draft_id=%s confirmed_by=%s 통지 %d건(새로 보낸 것)", did, mid, sent)
    return {"notified": True}
