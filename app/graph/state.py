# state.py -- State の定義。グラフの全ノードが共有する「箱」(ノードは、この箱を読んで、変えたい所だけ返す)
from typing import TypedDict


# ===============================================================================
# 1) State箱作成
# ===============================================================================
class State(TypedDict):
    store_id: int              # 店舗ID
    period: dict               # 対象の月 {"start": "2026-11-01", "days": 30}。無ければ settings.PERIOD(B1 で DB の shift_periods から入れる)
    llm_all_staff: list        # 全員のLLMに渡すデータ
    draft_store_shift: list    # 店全体の月間シフト案
    result: str                # validate の結果: valid / retry / no_staff / limit_reached (routes.py の route_2 が見る)
    return_count: int          # 自動編成(LLM)を呼んだ回数
    upper_limit: int           # 自動編成の上限回数(UPPER_LIMIT)
    decision: str              # 店長の決定: approved / redraft_requested / edit_saved (route_3 が見る)
    instruction: str           # 店長の指示
    manager_request_count: int # 店長が「やり直し」を頼んだ回数(上限2回)
    problems: list             # 検査で見つかった問題の一覧(作り直しのとき LLM に返す)    
    final_message: str         # 最終シフトの送信メッセージ
    contact_queue: list        # 連絡する候補の順番 [{staff_id, date, start, end}]
    contact_asked: int         # もう連絡した人数 
    contact_limit: int         # 連絡できる人数の上限(初期値3人)
    contact_log: list          # 連絡の記録 [{staff_id, date, start, end, answer}]
    blocked_reasons: list      # 承認できない理由(承認画面に出す)