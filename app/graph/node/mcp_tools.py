# node/mcp_tools.py -- グラフから MCP サーバー(app/mcp/server.py)の道具を呼ぶ部品(D5)
#   ・いまは「コード(collect)が呼ぶ」だけ。LLM には道具を渡していない(auto_draft は llm.invoke で1回呼ぶだけ)
#   ・LLM に道具を渡すときは、LLM_TOOLS に名前を入れたものだけを渡す(書き込み・送信の道具は入れない)
#   ・サーバーは呼ぶたびに子プロセスで起動し、終わったら止める(stdio)
import asyncio
import os
import sys
from mcp import Client, StdioServerParameters

LLM_TOOLS = set()              # LLM に見せてよい道具の名前。いまは無し(全部コードが呼ぶ)
SERVER_ENV_KEYS = ["DATABASE_URL"]   # サーバーに渡す環境変数。これ以外(API キーなど)は渡さない


async def call_mcp_tool_async(server, name, arguments):    # 非同期版。すでに動いているイベントループの中(FastAPI など)からはこちらを await する
    env = {key: os.environ[key] for key in SERVER_ENV_KEYS if key in os.environ}   # 渡さないと子プロセスに届かない(MCP は最低限の環境変数しか引き継がない)
    params = StdioServerParameters(command=sys.executable, args=[server], env=env)
    async with Client(params) as client:
        result = await client.call_tool(name, arguments)
    if result.is_error:                                    # 道具の中のエラー(無い period_id など)は、ここで例外にする
        message = " ".join(getattr(block, "text", "") for block in result.content)
        raise RuntimeError(f"MCP の道具 {name} がエラーを返しました: {message}")
    return result.structured_content


def call_mcp_tool(server, name, arguments):    # MCP サーバーの道具を1回呼んで、結果(dict)を返す。同期のノード(collect)から使う
    return asyncio.run(call_mcp_tool_async(server, name, arguments))
