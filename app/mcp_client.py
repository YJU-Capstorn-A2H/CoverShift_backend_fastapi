"""MCP 클라이언트 (M-7, G7). 그래프 collect 노드가 부른다: call_staff_info(period_id) -> dict.

stdio 로 `sys.executable -m app.mcp_server` 를 자식 프로세스로 띄우고 도구 get_staff_info 1번 부른 뒤 닫는다.
자식에게 넘기는 환경 변수는 mcp 기본값(HOME · PATH 등) + DATABASE_URL + PYTHONPATH 뿐이다
(ANTHROPIC_API_KEY · JWT_SECRET 같은 키는 넘기지 않는다 — 읽기 도구에 필요 없다).
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path

import anyio
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters

from app.config import get_settings

ROOT = Path(__file__).resolve().parents[1]
TOOL = "get_staff_info"


class McpToolError(RuntimeError):
    """도구가 is_error 로 답함 (예: 없는 period_id)."""


def server_params(database_url: str | None = None) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp_server"],
        env={"DATABASE_URL": database_url or get_settings().database_url, "PYTHONPATH": str(ROOT)},
        cwd=str(ROOT),
    )


def _result_to_dict(result) -> dict:
    """CallToolResult → dict. structured_content 를 먼저 보고, 없으면 text 를 JSON 으로 읽는다."""
    sc = getattr(result, "structured_content", None)
    if isinstance(sc, dict):
        # 반환형이 dict 가 아니면 SDK 가 {"result": ...} 로 감싼다
        if set(sc) == {"result"} and isinstance(sc["result"], dict):
            return sc["result"]
        return sc
    for block in result.content or []:
        text = getattr(block, "text", None)
        if text:
            return json.loads(text)
    raise McpToolError("MCP 도구 결과가 비어 있습니다")


async def acall_staff_info(period_id: int, database_url: str | None = None, timeout: float = 60.0) -> dict:
    with anyio.fail_after(timeout):
        async with Client(server_params(database_url)) as client:
            result = await client.call_tool(TOOL, {"period_id": period_id}, read_timeout_seconds=timeout)
    if result.is_error:
        texts = " ".join(getattr(b, "text", "") for b in result.content or [])
        raise McpToolError(f"{TOOL} 실패: {texts}")
    return _result_to_dict(result)


def call_staff_info(period_id: int, database_url: str | None = None, timeout: float = 60.0) -> dict:
    """동기 입구. 이미 이벤트 루프가 도는 스레드에서 불려도 되게, 그 경우는 따로 스레드를 띄워 돌린다."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(acall_staff_info(period_id, database_url, timeout))
    box: dict = {}

    def run() -> None:
        try:
            box["v"] = asyncio.run(acall_staff_info(period_id, database_url, timeout))
        except BaseException as e:  # 부른 쪽 스레드에서 다시 던진다
            box["e"] = e

    t = threading.Thread(target=run, name="mcp-call", daemon=True)
    t.start()
    t.join(timeout + 10)
    if "e" in box:
        raise box["e"]
    if "v" not in box:
        raise TimeoutError(f"{TOOL} 가 {timeout}초 안에 끝나지 않았습니다")
    return box["v"]
