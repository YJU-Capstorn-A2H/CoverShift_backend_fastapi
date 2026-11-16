# State
from typing import TypedDict

# ===============================================================================
# 1) State箱作成
# ===============================================================================
class State(TypedDict):
    store_id: int              # 店舗ID
    llm_all_staff: list        # 全員のLLMに渡すデータ
    draft_store_shift: list    # 店全体の月間シフト案
    result: str                # 検査の結果
    return_count: int          # 自動編成の作り直し回数
    upper_limit: int           # 上限値
    decision: str              # 店長の承認結果
    instruction: str           # 店長の指示
    manager_request_count: int # 管理者リクエスト回数
    problems: list             # 検査で見つかった問題の一覧(作り直しのとき LLM に返す)    
    final_message: str         # 最終シフトの送信メッセージ
    contact_queue: list        # 連絡する候補の順番 [{staff_id, date, start, end}]
    contact_asked: int         # もう連絡した人数 
    contact_limit: int         # 連絡できる人数の上限(初期値3人)
    contact_log: list          # 連絡の記録 [{staff_id, date, start, end, answer}]
    blocked_reasons: list      # 承認できない理由(承認画面に出す)