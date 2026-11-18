"""그래프 흐름: 시작 → 초안 → 검증(재편성) → 저장 → 승인 대기 → 확정・통지 (가짜 LLM・가짜 검증, 진짜 DB)."""
from __future__ import annotations

from datetime import date, time

import pytest
from langgraph.types import Command

from app.domain import Assignment, ComposeResult
from tests.graph.fakes import BASE, D2, MARK, ScriptedComposer, cfg, draft, empty, make_deps, values, waiting

REVIEW_KEYS = {"type", "draft_id", "result", "violations", "blocked", "shortages", "notes", "free_text_guidelines",
               "auto_retry_count", "manager_request_count", "manager_request_limit"}
V_KEYS = {"key", "check_code", "severity", "waivable", "work_date", "employee_id", "detail"}
START = {"store_id": 1, "period_id": 1}


def approve(draft_id, waived=(), manager_id=1):
    return Command(resume={"decision": "approved", "manager_id": manager_id, "draft_id": draft_id,
                           "waived_keys": list(waived)})


@pytest.mark.req("M-1", "F-9", "F-14", "F-16")
def test_happy_path_review_then_approve(world, mem_graph):
    sh = [{"work_date": "2026-11-02", "start_time": "18:00", "end_time": "22:30", "need": 2, "have": 1}]
    comp = ScriptedComposer([draft(shortages=sh, notes=["메모"])])
    g = mem_graph(make_deps(comp))
    g.invoke(START, cfg("t"))

    w = waiting(g, "t")
    assert set(w) == REVIEW_KEYS  # API_CONTRACT ReviewWait 그대로
    assert (w["type"], w["result"], w["draft_id"], w["auto_retry_count"], w["manager_request_count"],
            w["manager_request_limit"]) == ("review", "valid", 1, 0, 0, 2)
    assert w["shortages"] == sh and w["notes"] == ["메모"]  # F-14: 참고용으로 그대로 나온다. 부족이 있어도 valid
    assert w["violations"] == [] and w["blocked"] == []
    assert world.one("SELECT status FROM shift_drafts WHERE draft_id = 1")["status"] == "draft"
    rows = world.q("SELECT employee_id, work_date, kind, start_time FROM shift_assignments ORDER BY employee_id")
    # F-16: 유급 신청(직원 2, 11/05)은 코드가 시프트에 싣는다 — 시각 없음, 출근 아님
    assert [(r["employee_id"], r["work_date"].isoformat(), r["kind"]) for r in rows] == [
        (1, "2026-11-02", "work"), (2, "2026-11-05", "paid_leave")]
    assert rows[1]["start_time"] is None
    assert world.count("line_outbox") == 0  # 승인 전에는 아무것도 보내지 않는다

    g.invoke(approve(1), cfg("t"))
    assert waiting(g, "t") is None and g.get_state(cfg("t")).next == ()
    assert values(g, "t")["notified"] is True
    d = world.one("SELECT status, confirmed_by, confirmed_at FROM shift_drafts WHERE draft_id = 1")
    assert d["status"] == "confirmed" and d["confirmed_by"] == 1 and d["confirmed_at"] is not None  # F-7 승인자 ID
    assert world.count("manager_actions", "action = 'approved' AND draft_id = 1 AND manager_id = 1") == 1
    msgs = world.q("SELECT employee_id, kind, dedupe_key, body FROM line_outbox ORDER BY employee_id")
    assert [(m["employee_id"], m["kind"]) for m in msgs] == [(e, "shift_confirmed") for e in (1, 2, 3, 4)]  # F-9 1명 1통
    assert msgs[0]["dedupe_key"] == "confirm:1:1"
    assert msgs[0]["body"]["work_days"] == [{"work_date": "2026-11-02", "start_time": "09:00", "end_time": "18:00"}]
    assert msgs[1]["body"]["work_days"] == [] and msgs[1]["body"]["paid_leave_days"] == ["2026-11-05"]  # F-16


@pytest.mark.req("F-16")
def test_paid_leave_day_with_work_row_is_kept_for_validator(world, mem_graph):
    """깨뜨리는 쪽: LLM 이 유급 날(직원 2, 11/05)에 근무를 넣어도 유급 행이 사라지지 않고 같이 검증으로 간다
    → 같은 사람・같은 날 두 행 = DOUBLE_BOOKING 은 검증 몫 (V-8)."""
    seen = []

    def spy(ctx, assignments):
        seen.append([(a.employee_id, a.work_date.isoformat(), a.kind) for a in assignments])
        return []
    on_leave = ComposeResult(ok=True, assignments=BASE + [Assignment(2, date(2026, 11, 5), "work", time(9), time(15))])
    g = mem_graph(make_deps(ScriptedComposer([on_leave]), validate=spy))
    g.invoke(START, cfg("t"))
    assert sorted(seen[0]) == [(1, "2026-11-02", "work"), (2, "2026-11-05", "paid_leave"), (2, "2026-11-05", "work")]


@pytest.mark.req("F-2", "F-5")
def test_bad_then_good_retry(world, mem_graph):
    comp = ScriptedComposer([draft("absent"), draft()])
    g = mem_graph(make_deps(comp))
    g.invoke(START, cfg("t"))

    w = waiting(g, "t")
    assert (w["result"], w["auto_retry_count"], len(comp.calls)) == ("valid", 1, 2)
    first, second = comp.calls
    assert first["previous"] is None and first["history"] == [] and first["instruction"] is None
    # F-5: 재시도 입력 = 직전 안(근무 행만 — 유급 행은 코드 몫) + 이력(같은 위반 묶음 + 회차)
    assert second["previous"] == BASE + [MARK["absent"]]
    assert [(h.check_code, h.rounds) for h in second["history"]] == [("REGULAR_ABSENT", (1,))]
    assert world.count("shift_drafts") == 1  # 실패한 안은 저장하지 않는다 (F-1)


@pytest.mark.req("F-2", "F-3")
def test_always_bad_reaches_limit_after_exactly_3_auto_retries(world, mem_graph):
    comp = ScriptedComposer([draft("absent")])
    g = mem_graph(make_deps(comp))
    g.invoke(START, cfg("t"))

    w = waiting(g, "t")
    assert len(comp.calls) == 4  # 처음 1 + 자동 재편성 3
    assert (w["result"], w["auto_retry_count"]) == ("limit_reached", 3)
    assert [v["check_code"] for v in w["violations"]] == ["REGULAR_ABSENT"]
    v = w["violations"][0]
    assert set(v) == V_KEYS and v["waivable"] is True and v["severity"] == "required"
    # 상한에 닿은 안은 위반을 안은 채 저장된다 (G4)
    assert world.count("shift_drafts") == 1
    assert world.count("validation_violations", "draft_id = 1 AND check_code = 'REGULAR_ABSENT'") == 1
    assert [h.rounds for h in comp.calls[3]["history"]] == [(1, 2, 3)]  # C3: 같은 위반을 묶고 회차 표시


@pytest.mark.req("F-2")
def test_auto_retry_limit_comes_from_settings(world, mem_graph, monkeypatch):
    """깨뜨리는 쪽: 상한을 1로 바꾸면 compose 는 2번만 불린다 → 상한이 코드에 박혀 있지 않다."""
    monkeypatch.setenv("AUTO_RETRY_LIMIT", "1")
    comp = ScriptedComposer([draft("absent")])
    g = mem_graph(make_deps(comp))
    g.invoke(START, cfg("t"))
    assert len(comp.calls) == 2 and waiting(g, "t")["result"] == "limit_reached"


@pytest.mark.req("F-4", "F-13")
@pytest.mark.parametrize("bad", [
    empty("timeout"),  # F-13: 시간 초과로 끊긴 호출 = 실패
    empty("parse_error"),
    ComposeResult(ok=True, assignments=[]),  # 0행
    ComposeResult(ok=True, assignments=[Assignment(1, D2, "work", time(9, 15), time(18))]),  # 30분 단위 아님 → 저장 불가
    ComposeResult(ok=True, assignments=[Assignment(99, D2, "work", time(9), time(18))]),  # 없는 직원
    ComposeResult(ok=True, assignments=[Assignment(1, date(2026, 12, 1), "work", time(9), time(18))]),  # 기간 밖
], ids=["timeout", "parse_error", "zero_rows", "not_30min", "unknown_employee", "outside_period"])
def test_empty_draft_counts_as_one_auto_retry(world, mem_graph, bad):
    comp = ScriptedComposer([bad, draft()])
    g = mem_graph(make_deps(comp))
    g.invoke(START, cfg("t"))
    w = waiting(g, "t")
    assert (len(comp.calls), w["result"], w["auto_retry_count"]) == (2, "valid", 1)
    assert world.count("shift_drafts") == 1


@pytest.mark.req("F-4")
def test_empty_every_time_goes_to_review_with_draft_empty(world, mem_graph):
    comp = ScriptedComposer([empty("timeout")])
    g = mem_graph(make_deps(comp))
    g.invoke(START, cfg("t"))
    w = waiting(g, "t")
    assert len(comp.calls) == 4 and (w["result"], w["auto_retry_count"]) == ("draft_empty", 3)
    assert w["draft_id"] == 1 and any("timeout" in n for n in w["notes"])
    # (가정 신규) 유급 행만 있는 빈 안을 저장 → 점장이 직접 편집하거나 재편성을 요청한다
    assert [r["kind"] for r in world.q("SELECT kind FROM shift_assignments WHERE draft_id = 1")] == ["paid_leave"]


@pytest.mark.req("F-1")
def test_nothing_saved_before_validation_finishes(world, mem_graph):
    seen_compose, seen_validate = [], []
    comp = ScriptedComposer([draft("absent"), draft("absent"), draft()],
                            on_compose=lambda n: seen_compose.append(world.count("shift_drafts")))

    def checking_validate(ctx, assignments):
        from tests.graph.fakes import marker_validate
        seen_validate.append(world.count("shift_drafts"))
        return marker_validate(ctx, assignments)
    g = mem_graph(make_deps(comp, validate=checking_validate))
    g.invoke(START, cfg("t"))
    assert seen_compose == [0, 0, 0] and seen_validate == [0, 0, 0]
    assert world.count("shift_drafts") == 1
    # 깨뜨리는 쪽: 같은 검사가 저장된 안을 실제로 본다 — 재편성 요청 뒤의 compose 에서는 앞의 안 1개가 보인다
    g.invoke(Command(resume={"decision": "redraft_requested", "manager_id": 1, "draft_id": 1, "instruction": "x"}),
             cfg("t"))
    assert seen_compose[-1] == 1 and world.count("shift_drafts") == 2


@pytest.mark.req("F-6")
def test_llm_input_and_state_have_no_names(world, mem_graph):
    comp = ScriptedComposer([draft()])
    g = mem_graph(make_deps(comp))
    g.invoke(START, cfg("t"))
    names = [r["name"] for r in world.q("SELECT name FROM employees")]
    assert len(names) == 4  # 깨뜨리는 쪽의 전제: DB 에는 실명이 있다
    blob = repr(comp.calls[0]["ctx"]) + repr(values(g, "t")) + repr(waiting(g, "t"))
    assert not [n for n in names if n in blob]


@pytest.mark.req("F-10")
def test_one_period_one_thread(world, mem_graph):
    world.q("""INSERT INTO shift_periods (store_id, target_month, submission_deadline, designated_off_days)
               VALUES (1, '2026-12-01', '2026-11-20T00:00+09', 9)""")

    def month_draft(ctx):  # 기간의 첫날에 1행 — 기간이 다르면 다른 안
        return ComposeResult(ok=True, assignments=[Assignment(1, ctx.period.target_month, "work", time(9), time(18))])
    g = mem_graph(make_deps(ScriptedComposer([month_draft])))
    g.invoke({"store_id": 1, "period_id": 1}, cfg("period-1"))
    g.invoke({"store_id": 1, "period_id": 2}, cfg("period-2"))
    w1, w2 = waiting(g, "period-1"), waiting(g, "period-2")
    assert {w1["draft_id"], w2["draft_id"]} == {1, 2}
    by_period = {r["period_id"]: r["draft_id"] for r in world.q("SELECT period_id, draft_id FROM shift_drafts")}
    assert by_period == {1: w1["draft_id"], 2: w2["draft_id"]}
    g.invoke(approve(w2["draft_id"]), cfg("period-2"))
    assert waiting(g, "period-1") == w1  # 다른 기간의 thread 는 그대로 기다린다
    assert world.count("shift_drafts", "status = 'confirmed'") == 1


@pytest.mark.req("F-10")
def test_period_of_another_store_is_rejected(world, mem_graph):
    g = mem_graph(make_deps(ScriptedComposer([draft()])))
    with pytest.raises(ValueError, match="store"):
        g.invoke({"store_id": 2, "period_id": 1}, cfg("t"))


@pytest.mark.req("F-10")
def test_start_with_period_id_only(world, mem_graph):
    """app/runner.py 의 start_input 은 {"period_id"} 만 넘긴다 → 점포는 기간에서 정한다."""
    g = mem_graph(make_deps(ScriptedComposer([draft()])))
    g.invoke({"period_id": 1}, cfg("t"))
    assert waiting(g, "t")["draft_id"] == 1 and values(g, "t")["store_id"] == 1


@pytest.mark.req("M-7", "A-18")
def test_collect_reads_staff_through_mcp_stdio(world, mem_graph, monkeypatch):
    """MCP_MODE=stdio 면 collect 가 MCP 서버(자식 프로세스)로 직원 정보를 읽는다 (G7). 이름은 오지 않는다."""
    import app.mcp_client as mc
    calls = []
    real = mc.call_staff_info

    def spy(period_id, *a, **kw):
        calls.append(period_id)
        return real(period_id, *a, **kw)
    monkeypatch.setattr(mc, "call_staff_info", spy)
    monkeypatch.setenv("MCP_MODE", "stdio")
    comp = ScriptedComposer([draft()])
    g = mem_graph(make_deps(comp))
    g.invoke(START, cfg("t"))
    assert calls == [1]
    staff = values(g, "t")["llm_all_staff"]
    assert [e["employee_id"] for e in staff] == [1, 2, 3, 4] and all("name" not in e for e in staff)
    assert len(comp.calls[0]["ctx"].availability) == 3

    # 깨뜨리는 쪽: MCP_MODE 를 빼면 MCP 를 거치지 않는다 (같은 함수를 직접 부름)
    monkeypatch.delenv("MCP_MODE")
    g.invoke(START, cfg("t2"))
    assert calls == [1] and waiting(g, "t2")["type"] == "review"
