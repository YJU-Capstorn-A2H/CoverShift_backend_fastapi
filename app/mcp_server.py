"""MCP 읽기 도구 서버 (M-7, A-18, G7). stdio 로 뜨고 도구는 정확히 1개: get_staff_info.

- 본체는 app.mcp_tools.get_staff_info (메인 세션 소유). 이 파일은 감싸기만 한다.
- 이름(employees.name)은 돌려주지 않는다: 본체가 이름 열을 읽지 못하는 DB 롤(covershift_llm_read)로 읽는다 (V9_ko 451, 495, 1119).
- mcp 2.3.0 에서 FastMCP 는 MCPServer 로 이름이 바뀌었다 (mcp/server/fastmcp.py 가 그렇게 안내한다).
- stdout 은 MCP 통신 통로다. 여기서 print 하지 않는다 (로그는 stderr).

실행: `python -m app.mcp_server` (DATABASE_URL 환경 변수 필요). 보통은 app.mcp_client 가 띄운다.
"""
from __future__ import annotations

import logging
import sys

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from app import mcp_tools

server = MCPServer("covershift-staff")


@server.tool()
def get_staff_info(period_id: int) -> dict:
    """편성 기간의 직원 정보(ID · 정직원 여부 · 자격 · 폐점 가능 · 주 출근일수 · 고정 요일)와 그 달 희망. 이름은 없다."""
    try:
        return mcp_tools.get_staff_info(period_id)
    except LookupError as e:
        # 없는 기간은 알려 준다. 그 밖의 예외(DB 오류 등)는 mcp 2.x 가 문구를 숨긴 채 is_error 로 돌려준다
        raise ToolError(str(e)) from e


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    server.run("stdio")


if __name__ == "__main__":
    main()
