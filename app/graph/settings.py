# settings.py -- 設定・定数・店舗ごとの設定・スタッフデータの読み込み
#   他のファイルに依存しない(一番下の土台)。B1 で DB から、D5 で MCP から読む形に置き換える場所
import os, json
from pathlib import Path
from dotenv import load_dotenv


# 0) 初期設定
load_dotenv()                                                 # 環境変数をロードする
SERVER = str(Path(__file__).parents[1] / "mcp" / "server.py")   # MCPサーバーのファイル(app/mcp/server.py)
STAFF_SOURCE = os.environ.get("STAFF_SOURCE", "mcp")          # period_id があるとき、従業員データをどこから読むか: mcp(MCPサーバー経由) / db(DB を直接読む)
if STAFF_SOURCE not in ("mcp", "db"):
    raise ValueError(f"STAFF_SOURCE は mcp か db です: {STAFF_SOURCE}")

DB_URL = os.environ["DATABASE_URL"]                           # DBの接続URL
STAFF_FILE = os.environ.get("STAFF_FILE", str(Path(__file__).parent / "staff_data.json"))   # スタッフデータのjsonファイル名(既定は、このファイルと同じフォルダ)
DRAFT_FILE = "month_draft.json"                               # LLM が返した案の保存先

PERIOD = {"start": "2026-11-01", "days": 30}                  # 対象期間の開始日と日数

UPPER_LIMIT = 3                                               # 作り直しの上限(LLM は1回で約6〜10分)
MANAGER_REQUEST_LIMIT = 2                                     # 店長が「やり直し」を頼める回数
MODEL_NAME = "claude-sonnet-5-5"                              # LLMのモデル名


STORE_SETTINGS = {
    101: {                                          # 店舗ID
        "open_hours":  ("09:00", "22:00"),          # お客さんの開店〜閉店
        "open_by_weekday": {},                      # 曜日で違う場合 (0=月 ... 6=日)
        "open_by_date": {"2027-01-01": None},       # 日付で違う場合。休業日は None
        "prep_before": 60,                          # 開店の何分前から働くか
        "close_after": 30,                          # 閉店の何分後まで働くか
        "min_headcount": [                          # 時間帯ごとの最低人数
            {"start": "08:00", "end": "09:00", "min": 3, "exclude": ["薬剤師"]},
    {"start": "09:00", "end": "13:00", "min": 4, "want": 5, "exclude": []},
        ],                     
        "qualification_rules": [                    # 資格者のルール
            {"qualification": ["登録販売者(管理者)", "薬剤師"], "start": "08:00", "end": "22:30", "min": 1},
        ],               
        "shain_off_days": 9,                        # 社員の公休日数
    },
}


STAFF = []                                                    # staff_data.json が無くてもよい(DB から読むときは使わない)
if os.path.exists(STAFF_FILE):
    with open(STAFF_FILE, encoding="utf-8") as staff_file:    # スタッフデータを読み込む
        STAFF = json.load(staff_file)

id_to_name = {}                                               # 名前とIDの対応表の辞書                            
for staff in STAFF:
    id_to_name[staff["staff_id"]] = staff["staff_name"]       # IDから名前を取得する


llm_all_staff = []                                            # 名前からIDを削除するリスト             
for staff in STAFF:                                  
    item = {}                                                 # 名前を削除したデータを作成する
    for key, value in staff.items():
        if key != "staff_name":                               # 名前がある場合
            item[key] = value                                 # 名前を削除して、IDだけを残す
    llm_all_staff.append(item)                                # 名前を削除したデータをリストに追加する


def get_store(state):    # その State の店の設定。DB から読んだ設定(store_settings)があればそれ、無ければ STORE_SETTINGS から
    return state.get("store_settings") or STORE_SETTINGS[state["store_id"]]
