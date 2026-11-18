"""MCP 읽기 도구 (M-7, A-18): 진짜 stdio 서버를 자식 프로세스로 띄워 시험 DB 를 읽는다."""
from __future__ import annotations

import asyncio
import json

import pytest
from mcp.client.client import Client

from app.mcp_client import McpToolError, call_staff_info, server_params
from app.mcp_tools import staff_info_to_domain

REAL_NAMES = ("실명가나다", "山田花子")


def _seed(conn) -> None:
    conn.execute("INSERT INTO manager_accounts(email, password_hash) VALUES ('m@x', 'h')")
    conn.execute("INSERT INTO stores(manager_id, name) VALUES (1, '점포')")
    conn.execute("INSERT INTO employees(store_id, name, is_regular, has_qualification, closing_ok) "
                 "VALUES (1, %s, true, true, true), (1, %s, false, false, true)", REAL_NAMES)
    conn.execute("INSERT INTO shift_periods(store_id, target_month, submission_deadline, designated_off_days) "
                 "VALUES (1, '2026-11-01', '2026-10-20T00:00+09', 9)")
    conn.execute("INSERT INTO availability_submissions(employee_id, work_date, kind, start_time, end_time) "
                 "VALUES (2, '2026-11-03', 'available', '09:00', '13:00'), (1, '2026-11-04', 'off', NULL, NULL)")
    conn.commit()


@pytest.mark.req("M-7")
def test_server_exposes_exactly_one_tool(clean_db):
    async def tools():
        async with Client(server_params(clean_db)) as c:
            return (await c.list_tools()).tools

    ts = asyncio.run(tools())
    assert [t.name for t in ts] == ["get_staff_info"]
    assert "period_id" in json.dumps(ts[0].input_schema)


@pytest.mark.req("A-18")
@pytest.mark.req("M-7")
def test_tool_over_stdio_returns_staff_without_names(conn, clean_db):
    _seed(conn)
    # 깨뜨리는 쪽의 전제: 이름은 DB 에 실제로 있다 (없어서 안 보이는 것이 아님)
    assert [r[0] for r in conn.execute("SELECT name FROM employees ORDER BY employee_id")] == list(REAL_NAMES)
    info = call_staff_info(1, clean_db)
    dump = json.dumps(info, ensure_ascii=False)
    for n in REAL_NAMES:
        assert n not in dump
    assert all("name" not in e for e in info["employees"])
    assert [e["employee_id"] for e in info["employees"]] == [1, 2]
    assert {(a["employee_id"], a["work_date"], a["kind"]) for a in info["availability"]} == {
        (2, "2026-11-03", "available"), (1, "2026-11-04", "off")}
    emps, av = staff_info_to_domain(info)  # 그래프가 받는 형으로 바뀐다
    assert emps[0].is_regular is True and av[0].employee_id in (1, 2)


@pytest.mark.req("A-18")
def test_name_check_catches_a_leak():
    """깨뜨리는 쪽: 위 시험의 검사 방식이 이름이 섞인 결과를 실제로 잡는지 (가짜 결과로)."""
    leaked = {"employees": [{"employee_id": 1, "name": REAL_NAMES[0]}], "availability": []}
    dump = json.dumps(leaked, ensure_ascii=False)
    assert REAL_NAMES[0] in dump and any("name" in e for e in leaked["employees"])


@pytest.mark.req("M-7")
def test_unknown_period_is_tool_error(clean_db):
    with pytest.raises(McpToolError, match="not found"):
        call_staff_info(999, clean_db)


@pytest.mark.req("M-7")
def test_call_from_inside_running_event_loop(conn, clean_db):
    """Aegra 처럼 이벤트 루프가 도는 곳에서 불려도 된다 (따로 스레드에서 돈다)."""
    _seed(conn)

    async def inside():
        return call_staff_info(1, clean_db)

    assert len(asyncio.run(inside())["employees"]) == 2
