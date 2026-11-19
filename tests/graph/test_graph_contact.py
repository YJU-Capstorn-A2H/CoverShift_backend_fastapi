"""직원 연락 (M-6, G8 — ENABLE_CONTACT=true 일 때만. 기본 꺼 둠)."""
from __future__ import annotations

from datetime import date, time

import pytest
from langgraph.types import Command

from app.domain import Assignment, ComposeResult, Violation
from tests.graph.fakes import ScriptedComposer, cfg, make_deps, waiting

START = {"store_id": 1, "period_id": 1}
D3 = date(2026, 11, 3)


def headcount_validate(ctx, assignments):
    """시험용 검증: 11/03 13:00 칸은 2명 필요(warning) + 알바는 희망 행이 그 시간을 덮어야 함(required)."""
    out = []
    have = sum(1 for a in assignments
               if a.kind == "work" and a.work_date == D3 and a.start_time <= time(13) < a.end_time)
    if have < 2:
        out.append(Violation("HEADCOUNT_SHORTAGE", "warning", D3, None, {"time": "13:00", "need": 2, "have": have}))
    regular = {e.employee_id: e.is_regular for e in ctx.employees}
    for a in assignments:
        if a.kind != "work" or regular[a.employee_id]:
            continue
        ok = any(v.employee_id == a.employee_id and v.work_date == a.work_date and v.kind == "available"
                 and v.start_time <= a.start_time and a.end_time <= v.end_time for v in ctx.availability)
        if not ok:
            out.append(Violation("OUTSIDE_AVAILABILITY", "required", a.work_date, a.employee_id, {}))
    return out


ONE_ON_D3 = ComposeResult(ok=True, assignments=[Assignment(1, D3, "work", time(9), time(18))])


@pytest.fixture
def contact_on(monkeypatch, world):
    """직원 연락을 켜고, 지금 있는 직원 전원을 LINE 에 연결해 둔다 (미연결 직원에게는 묻지 않는다)."""
    monkeypatch.setenv("ENABLE_CONTACT", "true")
    world.q("UPDATE employees SET line_user_id = 'U-' || employee_id")


def answer(cid, ans):
    return Command(resume={"contact_id": cid, "answer": ans})


@pytest.mark.req("M-6")
def test_contact_yes_fills_the_shortage(world, mem_graph, contact_on):
    g = mem_graph(make_deps(ScriptedComposer([ONE_ON_D3]), validate=headcount_validate))
    g.invoke(START, cfg("t"))
    w = waiting(g, "t")
    # 직원 3 = 그 날 희망 미제출 알바 (1순위). 직원 4 는 15:00~ 만 가능 → 후보 아님. 사원은 후보 아님 (가정 신규)
    assert w == {"type": "contact", "contact_id": 1, "employee_id": 3, "work_date": "2026-11-03",
                 "start_time": "13:00", "end_time": "13:30"}
    assert world.count("shift_drafts") == 0  # 연락 중인 안도 아직 저장 전 (F-1)
    msg = world.one("SELECT employee_id, kind, dedupe_key, body FROM line_outbox")
    assert (msg["employee_id"], msg["kind"], msg["dedupe_key"]) == (3, "contact_ask", "contact:1")
    assert msg["body"]["buttons"] == ["yes", "no"] and msg["body"]["contact_id"] == 1

    g.invoke(answer(99, "yes"), cfg("t"))  # 다른 질문의 답 → 버리고 같은 질문으로 기다린다
    assert waiting(g, "t") == w and world.count("line_outbox") == 1
    g.invoke(Command(resume={"decision": "approved", "manager_id": 1, "draft_id": None, "waived_keys": []}),
             cfg("t"))  # 연락 대기 중에 온 승인 → 버린다 (prototype_aegra E4 「지금 대기가 아니면 버린다」)
    assert waiting(g, "t") == w and world.count("contact_requests", "status = 'asked'") == 1

    g.invoke(answer(1, "yes"), cfg("t"))
    r = waiting(g, "t")
    assert r["type"] == "review" and r["result"] == "valid"
    assert [v["check_code"] for v in r["violations"]] == []  # 부족이 채워졌고, 「가능」 답이 희망으로 들어가 V-3 도 없음
    got = world.q("SELECT employee_id, start_time, end_time FROM shift_assignments WHERE draft_id = 1 AND employee_id = 3")
    assert [(x["start_time"], x["end_time"]) for x in got] == [(time(13), time(13, 30))]
    assert world.one("SELECT status FROM contact_requests WHERE contact_id = 1")["status"] == "accepted"
    assert world.count("availability_submissions", "employee_id = 3 AND work_date = '2026-11-03'") == 0  # DB 희망은 그대로


@pytest.mark.req("M-6")
def test_contact_gives_up_after_3_asks(world, mem_graph, contact_on):
    for i in range(5, 9):  # 희망 미제출 알바 4명 더 (5~8)
        world.q("""INSERT INTO employees (store_id, name, is_regular, closing_ok, line_user_id)
                   VALUES (1, %s, false, true, %s)""", (f"x{i}", f"U-x{i}"))
    g = mem_graph(make_deps(ScriptedComposer([ONE_ON_D3]), validate=headcount_validate))
    g.invoke(START, cfg("t"))
    asked = []
    while (w := waiting(g, "t"))["type"] == "contact":
        asked.append(w["employee_id"])
        g.invoke(answer(w["contact_id"], "no"), cfg("t"))
    assert asked == [3, 5, 6]  # 3번 묻고 그만둔다 (가정 신규). 같은 사람에게 두 번 묻지 않는다
    assert w["type"] == "review" and [v["check_code"] for v in w["violations"]] == ["HEADCOUNT_SHORTAGE"]
    assert [r["status"] for r in world.q("SELECT status FROM contact_requests ORDER BY contact_id")] == ["declined"] * 3
    assert world.count("line_outbox", "kind = 'contact_ask'") == 3


@pytest.mark.req("M-6")
def test_contact_is_off_by_default(world, mem_graph, monkeypatch):
    """깨뜨리는 쪽: 설정을 켜지 않으면 같은 부족이 있어도 묻지 않고 바로 승인 대기."""
    monkeypatch.delenv("ENABLE_CONTACT", raising=False)
    g = mem_graph(make_deps(ScriptedComposer([ONE_ON_D3]), validate=headcount_validate))
    g.invoke(START, cfg("t"))
    w = waiting(g, "t")
    assert w["type"] == "review" and [v["check_code"] for v in w["violations"]] == ["HEADCOUNT_SHORTAGE"]
    assert world.count("contact_requests") == 0 and world.count("line_outbox") == 0


@pytest.mark.req("M-6", "A-21")
def test_contact_skips_employees_without_line(world, mem_graph, contact_on):
    """LINE 미연결 직원에게는 묻지 않는다 — 메시지가 닿지 않아(no_recipient) 그래프가 오지 않을 답을 기다리게 된다."""
    world.q("""INSERT INTO employees (store_id, name, is_regular, closing_ok, line_user_id)
               VALUES (1, 'x5', false, true, 'U-x5')""")  # 직원 5: 연결됨
    world.q("UPDATE employees SET line_user_id = NULL WHERE employee_id = 3")  # 1순위였던 직원 3 의 연결을 끊는다
    g = mem_graph(make_deps(ScriptedComposer([ONE_ON_D3]), validate=headcount_validate))
    g.invoke(START, cfg("t"))
    assert waiting(g, "t")["employee_id"] == 5  # 3 을 건너뛰고 다음 후보에게
    assert [r["employee_id"] for r in world.q("SELECT employee_id FROM line_outbox")] == [5]


@pytest.mark.req("M-6", "A-21")
def test_contact_asks_nobody_when_no_candidate_has_line(world, mem_graph, contact_on):
    world.q("UPDATE employees SET line_user_id = NULL")
    g = mem_graph(make_deps(ScriptedComposer([ONE_ON_D3]), validate=headcount_validate))
    g.invoke(START, cfg("t"))
    assert waiting(g, "t")["type"] == "review"  # 기다리지 않고 부족을 안은 채 점장에게
    assert world.count("contact_requests") == 0 and world.count("line_outbox") == 0
