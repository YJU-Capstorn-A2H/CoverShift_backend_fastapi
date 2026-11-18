"""점장 확인: 재편성 요청(상한 2) · 편집 저장 · 예외 허용 · 두 번 승인 · 틀 규칙 고정."""
from __future__ import annotations

from datetime import time

import psycopg
import pytest
from langgraph.types import Command

from app.graph_seung import nodes
from tests.graph.fakes import (BASE, MARK, ScriptedComposer, cfg, draft, make_deps, values, waiting)

START = {"store_id": 1, "period_id": 1}


def review(decision, draft_id, manager_id=1, **kw):
    return Command(resume={"decision": decision, "manager_id": manager_id, "draft_id": draft_id, **kw})


def rows_of(*asg):
    return [{"employee_id": a.employee_id, "work_date": a.work_date.isoformat(), "kind": a.kind,
             "start_time": a.start_time.strftime("%H:%M"), "end_time": a.end_time.strftime("%H:%M")} for a in asg]


@pytest.mark.req("F-2", "F-7")
def test_redraft_requests_capped_at_2(world, mem_graph):
    comp = ScriptedComposer([draft()])
    g = mem_graph(make_deps(comp))
    g.invoke(START, cfg("t"))
    g.invoke(review("redraft_requested", 1, instruction="A"), cfg("t"))
    w = waiting(g, "t")
    assert (w["draft_id"], w["manager_request_count"]) == (2, 1)
    g.invoke(review("redraft_requested", 2, instruction="B"), cfg("t"))
    w = waiting(g, "t")
    assert (w["draft_id"], w["manager_request_count"]) == (3, 2)
    assert [c["instruction"] for c in comp.calls] == [None, "A", "B"]
    assert comp.calls[2]["previous"] == BASE  # 재편성 요청의 「직전 안」 = 점장이 본 안

    # 깨뜨리는 쪽: 3번째 요청 → 받지 않고 같은 안으로 다시 기다린다 (화면에 이유)
    g.invoke(review("redraft_requested", 3, instruction="C"), cfg("t"))
    w = waiting(g, "t")
    assert (w["draft_id"], w["manager_request_count"]) == (3, 2) and len(comp.calls) == 3
    assert any("2회까지" in n for n in w["notes"])
    acts = world.q("SELECT draft_id, instruction FROM manager_actions WHERE action = 'redraft_requested' ORDER BY 1")
    assert [(a["draft_id"], a["instruction"]) for a in acts] == [(1, "A"), (2, "B")]
    assert [r["parent_draft_id"] for r in world.q("SELECT parent_draft_id FROM shift_drafts ORDER BY draft_id")] == [
        None, 1, 2]
    # 상한 뒤에도 승인은 된다
    g.invoke(review("approved", 3, waived_keys=[]), cfg("t"))
    assert world.one("SELECT status FROM shift_drafts WHERE draft_id = 3")["status"] == "confirmed"


@pytest.mark.req("F-2")
def test_redraft_resets_auto_retry_count(world, mem_graph):
    comp = ScriptedComposer([draft("absent")] * 5 + [draft()])
    g = mem_graph(make_deps(comp))
    g.invoke(START, cfg("t"))
    assert (waiting(g, "t")["result"], len(comp.calls)) == ("limit_reached", 4)
    g.invoke(review("redraft_requested", 1, instruction="고쳐"), cfg("t"))
    w = waiting(g, "t")
    # (가정 신규) 점장 요청 뒤에는 자동 횟수를 0 부터 다시 센다 → bad 1번 + good 1번
    assert (w["result"], w["auto_retry_count"], w["manager_request_count"], len(comp.calls)) == ("valid", 1, 1, 6)


@pytest.mark.req("F-15", "F-7")
def test_edit_saved_creates_child_draft_and_revalidates(world, mem_graph):
    comp = ScriptedComposer([draft()])
    g = mem_graph(make_deps(comp))
    g.invoke(START, cfg("t"))
    # 화면은 근무 행만 보냈다 (유급 행 없음) + 10:00 시작 행(가짜 검증: REGULAR_ABSENT)
    g.invoke(review("edit_saved", 1, assignments=rows_of(*BASE, MARK["absent"])), cfg("t"))
    w = waiting(g, "t")
    assert (w["draft_id"], w["result"], w["manager_request_count"]) == (2, "limit_reached", 0)
    assert [v["check_code"] for v in w["violations"]] == ["REGULAR_ABSENT"]  # 검증이 다시 돌았다
    assert len(comp.calls) == 1  # LLM 은 부르지 않는다
    d2 = world.one("SELECT parent_draft_id, status FROM shift_drafts WHERE draft_id = 2")
    assert (d2["parent_draft_id"], d2["status"]) == (1, "draft")
    kinds = world.q("SELECT employee_id, kind FROM shift_assignments WHERE draft_id = 2 ORDER BY employee_id, kind")
    assert [(k["employee_id"], k["kind"]) for k in kinds] == [(1, "work"), (2, "paid_leave"), (2, "work")]
    assert world.count("validation_violations", "draft_id = 2") == 1
    assert world.count("manager_actions", "action = 'edit_saved' AND draft_id = 1 AND manager_id = 1") == 1

    # 고친 편집 → 위반 없음 → valid
    g.invoke(review("edit_saved", 2, assignments=rows_of(*BASE)), cfg("t"))
    w = waiting(g, "t")
    assert (w["draft_id"], w["result"], w["violations"]) == (3, "valid", [])


@pytest.mark.req("F-15")
@pytest.mark.parametrize("bad_row", [
    {"employee_id": 1, "work_date": "2026-11-02", "kind": "work", "start_time": "09:15", "end_time": "18:00"},
    {"employee_id": 1, "work_date": "2026-11-02", "kind": "work", "start_time": "18:00", "end_time": "09:00"},
    {"employee_id": 77, "work_date": "2026-11-02", "kind": "work", "start_time": "09:00", "end_time": "18:00"},
    {"employee_id": 1, "work_date": "2026-11-02", "kind": "paid_leave", "start_time": "09:00", "end_time": "18:00"},
], ids=["not_30min", "end_before_start", "unknown_employee", "paid_leave_with_time"])
def test_edit_saved_with_unstorable_row_is_refused(world, mem_graph, bad_row):
    g = mem_graph(make_deps(ScriptedComposer([draft()])))
    g.invoke(START, cfg("t"))
    g.invoke(review("edit_saved", 1, assignments=[bad_row]), cfg("t"))
    w = waiting(g, "t")
    assert w["draft_id"] == 1 and any("편집 내용을 받지 않았다" in n for n in w["notes"])
    assert world.count("shift_drafts") == 1 and world.count("manager_actions") == 0


@pytest.mark.req("F-3", "F-8")
def test_qual_missing_cannot_be_waived_but_regular_absent_can(world, mem_graph):
    g = mem_graph(make_deps(ScriptedComposer([draft("absent", "qual")])))
    g.invoke(START, cfg("t"))
    w = waiting(g, "t")
    assert w["result"] == "limit_reached"
    keys = {v["check_code"]: v["key"] for v in w["violations"]}
    assert {v["check_code"]: v["waivable"] for v in w["violations"]} == {"REGULAR_ABSENT": True, "QUAL_MISSING": False}

    # 깨뜨리는 쪽 1: 아무것도 허용하지 않고 승인 → required 둘 다 막힌다
    g.invoke(review("approved", 1, waived_keys=[]), cfg("t"))
    assert sorted(v["check_code"] for v in waiting(g, "t")["blocked"]) == ["QUAL_MISSING", "REGULAR_ABSENT"]
    # 깨뜨리는 쪽 2: 둘 다 허용해도 QUAL_MISSING 은 막힌다 (예외 불가, V9_ko 469・479)
    g.invoke(review("approved", 1, waived_keys=list(keys.values())), cfg("t"))
    w = waiting(g, "t")
    assert [v["check_code"] for v in w["blocked"]] == ["QUAL_MISSING"] and w["draft_id"] == 1
    assert world.count("shift_drafts", "status = 'confirmed'") == 0 and world.count("line_outbox") == 0
    assert world.count("manager_actions", "action = 'approved'") == 0

    # 지키는 쪽: 자격자 칸을 고친 편집 → REGULAR_ABSENT 만 남음 → 허용하고 승인 → 확정, allowed_by 기록
    g.invoke(review("edit_saved", 1, assignments=rows_of(*BASE, MARK["absent"])), cfg("t"))
    w = waiting(g, "t")
    assert w["blocked"] == [] and [v["check_code"] for v in w["violations"]] == ["REGULAR_ABSENT"]
    g.invoke(review("approved", 2, waived_keys=[w["violations"][0]["key"]]), cfg("t"))
    assert waiting(g, "t") is None
    v = world.one("SELECT allowed_by, allowed_at FROM validation_violations WHERE draft_id = 2")
    assert v["allowed_by"] == 1 and v["allowed_at"] is not None
    # 막혔던 1번 안의 위반에는 허용이 붙지 않는다
    assert world.count("validation_violations", "draft_id = 1 AND allowed_by IS NOT NULL") == 0


@pytest.mark.req("A-16", "F-9")
def test_approving_twice_and_stale_draft_id(world, mem_graph):
    comp = ScriptedComposer([draft()])
    deps = make_deps(comp)
    g = mem_graph(deps)
    g.invoke(START, cfg("t"))
    g.invoke(review("edit_saved", 1, assignments=rows_of(*BASE)), cfg("t"))  # 지금 안 = 2
    before = waiting(g, "t")
    assert before["draft_id"] == 2

    # 늦게 온 클릭 (예전 안 1을 승인) → 아무것도 바꾸지 않고 같은 값으로 다시 기다린다
    g.invoke(review("approved", 1, waived_keys=[]), cfg("t"))
    assert waiting(g, "t") == before
    g.invoke(Command(resume={"contact_id": 1, "answer": "yes"}), cfg("t"))  # 승인 대기 중에 온 직원 답장 → 버린다
    assert waiting(g, "t") == before
    assert world.count("shift_drafts", "status = 'confirmed'") == 0

    g.invoke(review("approved", 2, waived_keys=[]), cfg("t"))
    g.invoke(review("approved", 2, waived_keys=[]), cfg("t"))  # 두 번째 클릭 (thread 는 이미 끝남)
    assert waiting(g, "t") is None and len(comp.calls) == 1  # 처음부터 다시 돌지도 않는다
    assert world.count("shift_drafts") == 2
    assert world.count("shift_drafts", "status = 'confirmed'") == 1
    assert world.one("SELECT draft_id FROM shift_drafts WHERE status = 'confirmed'")["draft_id"] == 2
    assert world.count("manager_actions", "action = 'approved'") == 1
    assert world.count("line_outbox", "kind = 'shift_confirmed'") == 4

    # 중단 뒤 commit_notify 가 다시 도는 경우 (노드를 직접 한 번 더) → 그대로
    nodes.commit_notify(values(g, "t"), deps)
    assert world.count("shift_drafts", "status = 'confirmed'") == 1
    assert world.count("manager_actions", "action = 'approved'") == 1
    assert world.count("line_outbox") == 4

    # 깨뜨리는 쪽: 그래프를 거치지 않고 DB 에 직접 두 번째 승인・두 번째 확정을 넣으면 DB 가 거부한다 (V9_ko 505)
    with pytest.raises(psycopg.errors.UniqueViolation):
        world.q("INSERT INTO manager_actions (period_id, draft_id, manager_id, action) VALUES (1, 2, 1, 'approved')")
    with pytest.raises(psycopg.errors.UniqueViolation):
        world.q("UPDATE shift_drafts SET status = 'confirmed', confirmed_at = now(), confirmed_by = 1 WHERE draft_id = 1")


@pytest.mark.req("F-7")
def test_review_without_manager_id_is_refused(world, mem_graph):
    g = mem_graph(make_deps(ScriptedComposer([draft()])))
    g.invoke(START, cfg("t"))
    g.invoke(Command(resume={"decision": "approved", "draft_id": 1, "waived_keys": []}), cfg("t"))
    w = waiting(g, "t")
    assert w is not None and any("manager_id" in n for n in w["notes"])
    assert world.count("shift_drafts", "status = 'confirmed'") == 0
    g.invoke(Command(resume={"decision": "nonsense", "manager_id": 1, "draft_id": 1}), cfg("t"))
    assert waiting(g, "t")["draft_id"] == 1 and world.count("manager_actions") == 0


@pytest.mark.req("F-17", "T-4")
def test_template_rules_frozen_at_start(world, mem_graph):
    r1 = {"rule_key": "r1", "template_key": "not_available", "params": {"staff_id": 3, "weekday": 7},
          "source_text": "C 는 일요일 불가"}
    world.add_rules([r1], ["가능하면 주말은 베테랑"])
    comp = ScriptedComposer([draft()])
    g = mem_graph(make_deps(comp))
    g.invoke(START, cfg("t"))
    assert waiting(g, "t")["free_text_guidelines"] == ["가능하면 주말은 베테랑"]

    r2 = {"rule_key": "r2", "template_key": "min_headcount",
          "params": {"count": 3, "start": "17:00", "end": "20:00", "weekday": 5}, "source_text": "금요일 저녁 3명"}
    world.add_rules([r2], [])  # 시작 뒤에 점장이 새로 확인
    g.invoke(review("redraft_requested", 1, instruction="다시"), cfg("t"))
    assert [[r.rule_key for r in c["ctx"].template_rules] for c in comp.calls] == [["r1"], ["r1"]]  # 고정
    assert comp.calls[0]["ctx"].free_text_guidelines == ("가능하면 주말은 베테랑",)

    # 깨뜨리는 쪽: 새로 시작한 thread 는 마지막 확인본(r2)을 읽는다 → 고정이 「처음 읽은 값」 때문이지 우연이 아니다
    g.invoke(START, cfg("t2"))
    assert [r.rule_key for r in comp.calls[-1]["ctx"].template_rules] == ["r2"]
    assert comp.calls[-1]["ctx"].template_rules[0].params.start == time(17)


@pytest.mark.req("T-4")
def test_broken_template_rule_is_not_frozen(world, mem_graph):
    world.add_rules([{"rule_key": "r1", "template_key": "no_such_template", "params": {}, "source_text": "?"}], [])
    g = mem_graph(make_deps(ScriptedComposer([draft()])))
    with pytest.raises(ValueError, match="unknown template_key"):
        g.invoke(START, cfg("t"))
    assert world.count("shift_drafts") == 0
