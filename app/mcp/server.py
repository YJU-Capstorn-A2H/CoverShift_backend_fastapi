# app/mcp/server.py -- MCP サーバー(D5)。DB を読む道具を、MCP の後ろに置く
#   ・道具は1つだけ(基準文書 11/04「MCP 読み取り道具1つ(従業員情報)」)
#   ・読むだけ。書き込みの道具は置かない(書き込みはコードが検査してから行う。教授 10/16)
#   ・名前は返さない(employees.name は読まない。基準文書 11/07)
#   ・つなぎ方は stdio(グラフ側が、このファイルを子プロセスとして起動し、標準入出力でやりとりする)
#   ・DB の URL は、起動した側から環境変数 DATABASE_URL で受け取る(.env は読まない)
#
# 道具の一覧(「呼ぶ人」と「LLM に見せるか」は、グラフ側の node/mcp_tools.py の LLM_TOOLS で決める)
#   | 道具                    | 触るもの        | 呼ぶ人            | LLM に見せるか |
#   | get_staff_availability  | DB(読むだけ)   | コード(collect) | 見せない       |
#
# 単体で起動する: python app/mcp/server.py   (普段はグラフが起動するので、手で起動する必要はない)
import contextlib
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "graph"))   # app/graph の node/db.py と node/convert.py を使う

# import はここにまとめる(上の「app/graph を探す設定」のあとでないと動かない)
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from node.db import load_period_data
from node.convert import split_names

server = MCPServer("covershift-staff")


@server.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False))
def get_staff_availability(period_id: int) -> dict[str, Any]:
    """その期間(period_id)の店の従業員データを返す。名前は入らない。
    戻り値: {"store_id": 店の ID, "staff": [{staff_id, employment, qualification, staff_skill, admin_rate, closing_ok, requests}, ...]}
    requests は、その月の希望だけ(別の月・別の店の行は入らない)"""
    # stdio では標準出力が MCP の通信路なので、print(注意の表示など)は標準エラーへ回す(混ざると通信が壊れる)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            data = load_period_data(os.environ["DATABASE_URL"], period_id)
    except ValueError as error:        # 無い period_id など、呼ぶ側の間違いは理由を返す(DB の接続エラーなどは理由を返さない。接続先が漏れないように)
        raise ToolError(str(error)) from error
    _, staff = split_names(data["staff_list"])
    return {"store_id": data["store_id"], "staff": staff}


if __name__ == "__main__":
    server.run("stdio")
