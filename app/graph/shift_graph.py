# ==============================================================================
# LangGraph/003-Handson/exercises/prototype_mcp.py　(自作LangGraph + MCP のプロトタイプ)
# 
# コードの内容
# ０) 初期設定: 店舗ごとの設定データ、最初に読み込むスタッフデータ、環境変数のロード
# １) 箱作成: Stateクラスの定義
# ２) グラフ作成: グラフのノード(コード・分岐・LLM・待機)を定義する
#                2-1) 【コード】希望・スキル・指示を読む
#                2-2) 【分岐】スキルチェックが必要かどうかを判断する
#                2-3) 【コード】未確認を「要指導」とみなして、店長に知らせる(止まらない)
#                2-4) 【コード】LLMで使う道具
#                2-5) 【LLM】AIが編成案を作る
#                2-6) 【コード】各ルールの検討
#                2-7) 【コード】管理者手動編成
#                2-8) 【コード】人手が足りない日のLINE連絡
#                2-9) 【待機】管理者承認
#               2-10) 【分岐】検査結果に応じて、スタッフに連絡するか、自動編成を再度行うかを判断する
#               2-11) 【コード】最新データで再チェックして、確定を保存
#               2-12) 【コード】最終結果を会社の規定に変換 (現在は、まだ未実装)
#               2-12) 【コード】最終結果を各従業員に通知
# 3) グラフ実行: ノード定義
# 4) グラフの連結: グラフをつなげる
# 5) グラフの実行: グラフを実行する
# ==============================================================================





import os, json, time, datetime, asyncio, re, sys, copy
from typing import TypedDict, Literal
from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command, interrupt
from pathlib import Path
# from mcp_call import call_mcp_tool





# 0) 初期設定
load_dotenv()                                                 # 環境変数をロードする
SERVER = str(Path(__file__).parent / "staff_mcp_server.py")   # MCPサーバーのファイル名

DB_URL = os.environ["DATABASE_URL"]                           # DBの接続URL
STAFF_FILE = os.environ.get("STAFF_FILE", "staff_data.json")  # スタッフデータのjsonファイル名
DRAFT_FILE = "month_draft.json"                               # LLM が返した案の保存先

PERIOD = {"start": "2026-11-01", "days": 30}                  # 対象期間の開始日と日数

UPPER_LIMIT = 3                                               # 作り直しの上限(LLM は1回で約6〜10分)
MODEL_NAME = "claude-sonnet-5-5"                              # LLMのモデル名
LLM = None                                                    # テストのときは、ここにニセの LLM を入れる


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


with open(STAFF_FILE, encoding="utf-8") as staff_file:                 # スタッフデータを読み込む
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





# ===============================================================================
# 2) グラフ作成
# ==============================================================================
# 2-1)【コード】希望・スキル・指示を読む
def collect(state):
    print("希望シフトを収集中...")
    # data = asyncio.run(call_mcp_tool(SERVER, "get_staff_availability"))                                 # MCPサーバーからデータを取得する
    # mcp_staff = data["staff"]                                                                           # MCPサーバーから取得したスタッフデータ
    # assert mcp_staff == llm_all_staff, "MCPサーバーから取得したスタッフデータと、初期データが一致しません。"  # debug用のアサーション

    print("スキルを評価中...")
    return {"llm_all_staff": llm_all_staff, "draft_store_shift": [], "result": "", "manager_request_count": 0, "instruction": "", "return_count": 0, "upper_limit": 3, "problems": [],
            "contact_queue": [], "contact_asked": 0, "contact_limit": 3, "contact_log": []}



# 2-2) 【分岐】スキルチェックが必要かどうかを判断する
def route_1(state) -> Literal["skill_check", "auto_draft"]:
    for staff in state["llm_all_staff"]:                         # 全員のデータを一人ずつ確認する
        if staff["staff_skill"] != staff["admin_rate"]:              # スキルと管理者評価が一致しない場合は、スキルチェックが必要
            return "skill_check"
    return "auto_draft"                                      # スキルと管理者評価が一致する場合は、自動編成に進む



# 2-3) 【コード】未確認を「要指導」とみなして、店長に知らせる(止まらない)
def skill_check(state):
    print("スキルチェック中...")
    for staff in state["llm_all_staff"]:                 
        if staff["staff_skill"] != staff["admin_rate"]:  
            print(f"{id_to_name[staff['staff_id']]}のスキルが違います。店長に知らせます。") 
    return {}



# 2-4) 【コード】LLMで使う道具
def to_min(time):                 # 時間文字列を分に変換する
    hour, minute = time.split(":")
    return int(hour) * 60 + int(minute)


def hour_miniute(minutes):        # 分を時間文字列に変換する
    hour = minutes // 60
    minute = minutes % 60
    return f"{hour:02d}:{minute:02d}"


def all_dates():                  # 1か月分の日付のリストを作る
    start = datetime.date.fromisoformat(PERIOD["start"])
    return [(start + datetime.timedelta(days=i)).isoformat() for i in range(PERIOD["days"])]


def find_request(staff, date):    # staffのrequestsから、指定したdateの希望を返す。
    for req in staff.get("requests", []):
        if req["date"] == date:
            return req
    return None                   # 無ければ None を返す


def get_llm():                    # LLMのモデルを返す。テストのときは、ニセの LLM を返す
    global LLM
    if LLM is None:
        LLM = ChatAnthropic(model=MODEL_NAME, api_key=os.environ["ANTHROPIC_API_KEY"], max_tokens=64000, streaming=True, thinking={"type": "adaptive"}, effort="low")
    return LLM


def extract_json(text):           # LLMの返事から、完全なJSONを取り出す(後ろのブロックから探す)
    blocks = re.findall(r"```(?:json)?\s*(.*?)```", text, re.S)
    for block in reversed(blocks):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("assignments"):
            return data

    start, end = text.rfind('{"assignments"'), text.rfind("}")      # 囲みがないとき
    if start != -1 and end > start:
        try:
            data = json.loads(text[start:end + 1])
            if isinstance(data, dict) and data.get("assignments"):
                return data
        except json.JSONDecodeError:
            pass
    raise RuntimeError("返事の中に完全なJSONが見つかりません。llm_raw_output.txt を見てください")


# 2-5) 【LLM】AIが編成案を作る
# rules: LLMが編成案を作る際のプロンプト
rules = """あなたは日本のシフト形式を熟知したシフト編成の補助です。
提案だけを行い、最終確定は人(店長)が行います。

【requests の読み方】
- start と end がある: その時間で出たい(社員の場合は、その時間どおりに入れる)。
- off: 休みたい日。(社員の希望休は最大3回。リフレッシュ休暇の場合は5連休まで。)
- paid_leave: true: 1日まるごと有給。その日は店に出ない。
- paid_leave: half(start と end つき): 半休。その時間どおりに入れる。
- 何も書いていない日: パート・バイトは入れない。社員は、あなたが決めてよい。

【必ず守るルール】
1. 営業時間のあいだ、どの30分の区切りにも社員が1人以上いること。開店作業と閉店作業は、この社員が行う。
2. パート・バイトは、start のある申請の日だけ、申請の時間の中で入れること。申請のない日と off の日には入れない。
3. パート・バイトは22:00まで。22:30までは closing_ok が true の人だけ。
4. 時間は30分単位(例: 08:00, 13:30)で書くこと。
5. 出勤の時間は、営業時間のどこから始めてもよい(10時や11時の出勤も可)。

【守れないとき】
必ず守るルール同士が両立しない日は、次の順で、下のほうから諦めること。
  1. 資格者の配置(上ほど守る)
  2. 社員の出勤日数を守る
  3. 社員の希望休と有給・半休
  4. 時間帯ごとの最低人数
  5. パート・バイトの申請を、できるだけ多く入れる
  6. パート・バイトの出勤回数の差を小さくする
諦めた所は、必ず shortages に書くこと。

【できるだけ守る目標】
- パート・バイトは、店の人手が足りる範囲で、申請をできるだけ多く入れる。そのうえで、出勤回数の差は小さくする(目安は3日差まで)。
- 毎日の出勤人数、時間帯ごとの人数は、できるだけ均等にする。
- 社員の希望休(off)は、できるだけ通す。最大3回。通さなかった場合は notes に理由を書くこと。

【出力形式】
次のJSONだけを出力すること。説明文は書かない。
{"assignments": [{"staff_id": 1, "date": "2026-11-02", "start": "08:00", "end": "17:00"}],
 "shortages": [{"date": "2026-11-20", "start": "08:00", "end": "09:00", "reason": "出られる人が足りない"}],
 "notes": ["..."]}
"""


def auto_draft(state: State):
    """
    希望のシフト・スキル・管理者評価をもとに、LLMが自動で編成案を作る。
    1) アルバイト・パートの希望シフト(llm_all_staff: list)を確認する
    2) 社員の希望休暇(llm_all_staff: list)を確認する
    3) スキル・管理者評価をもとに、シフトの組み合わせを考える
    4) 可能な限り希望シフトを尊重しつつ、必要なスキルを確保する
    5) 編成案(draft_store_shift: list)を作成する
    """
    print("自動編成中...")
    state["return_count"] += 1
    store = STORE_SETTINGS[state["store_id"]]
    prompt = rules + make_qualification_text(store["qualification_rules"]) + make_days_text(store["shain_off_days"]) + make_headcount_text(store["min_headcount"]) + make_problem_text(state.get("problems", [])) + "\n【スタッフのデータ(JSON)】\n" + json.dumps(state["llm_all_staff"], ensure_ascii=False) + make_instruction_text(state.get("instruction", "")) + make_prev_draft_text(state.get("draft_store_shift", []))
    llm = get_llm()
    llm_start_time = time.time()
    res = llm.invoke(prompt)
    print(f"LLM呼び出し: {time.time() - llm_start_time:.0f}秒")
    print(res.usage_metadata)
    print(res.response_metadata.get("stop_reason"))
    if res.response_metadata.get("stop_reason") == "max_tokens":
        raise RuntimeError("出力の上限に達しました。max_tokens を上げてください")
    text = ""
    if isinstance(res.content, str):
        text = res.content
    else:
        for block in res.content:
            if isinstance(block, str):
                text += block
            elif isinstance(block, dict) and block.get("type") == "text":
                text += block["text"]

    with open("llm_raw_output.txt", "w", encoding="utf-8") as output_file:
        output_file.write(text)

        print("返事の先頭:", repr(text[:100]))
    print("返事の末尾:", repr(text[-100:]))

    data = extract_json(text)
    print(f"編成の件数: {len(data['assignments'])}")

    with open("month_draft.json", "w", encoding="utf-8") as output_file:
        json.dump(data, output_file, ensure_ascii=False)

    return {"draft_store_shift": data["assignments"], "return_count": state["return_count"]}


# 2-6) 【コード】各ルールの検討
def make_qualification_text(qual_rules):     # 資格ルールの文章を作る
    if not qual_rules:                       # [] のときは、文を作らない(スイッチOFF)
        return ""
    
    text = "\n【資格のルール(必ず守る)】\n"
    for rule in qual_rules:
        names = "、".join(rule["qualification"])
        text += f"- {rule['start']}〜{rule['end']}は、qualification が「{names}」のどれかの人が、どの30分の区切りにも{rule['min']}人以上いること。\n"
    
    return text


def make_days_text(off_days):                # 社員の出勤日数の文章を作る
    if off_days is None:                     # None のときは、文を作らない(スイッチOFF)
        return ""
    
    work_days = 30 - off_days
    return (f"\n【社員の出勤日数(必ず守る)】\n"
            f"- 社員(employment が 社員 の人)は、1か月の公休(休み)が{off_days}日です。出勤は、ちょうど{work_days}日にすること。\n"
            f"- requests に off と書かれた日は、公休にあててよい。\n"
            f"- requests に paid_leave: true と書かれた日は、1日まるごと有給で、その日は店に出ない。ただし、出勤日数には1日として数える(例: 有給が2日なら、実際に出る日は{work_days - 2}日)。\n"
            f"- requests に paid_leave: half と書かれた日は、半休。start と end の時間どおりに入れる。この日も、出勤日数に1日として数える。\n"
            f"- 有給と半休の日は、できるだけ守る。守れなかったときは、notes に理由を書くこと。\n"
            f"- 出力する前に、社員1人ずつ、出勤した日と有給の日を合わせて数えて、{work_days}日であることを確かめること。\n")


def make_problem_text(problems):                      # 問題の一覧を、LLM に渡す文にする関数
    if not problems:                                  # 問題がないとき(1回目)は、文を作らない
        return ""
    
    text = "\n【前回の案の問題(今回は必ず直すこと)】\n"
    for problem in problems[:20]:                           # 長くなりすぎないよう、20件まで
        text += f"- {problem}\n"

    if len(problems) > 20:
        text += f"- (ほか{len(problems) - 20}件)\n"
    return text


def make_headcount_text(rules):              # 時間帯ごとの最低人数の文章を作る
    if not rules:                            # [] のときは、文を作らない(スイッチOFF)
        return ""
    
    text = "\n【時間帯ごとの最低人数(必ず守る)】\n"
    for rule in rules:
        text += f"- {rule['start']}〜{rule['end']}は、同時にいる人数を{rule['min']}人以上にすること。\n"
        
        if rule.get("exclude"):
            text += f"  (qualification が{'・'.join(rule['exclude'])}の人は、この時間帯の人数に数えない)\n"
        
        if rule.get("want"):
            text += f"  できれば{rule['want']}人にする。\n"
    
    text += "- 出られる人が足りなくて、どうしても守れない日は、守れなくてよい。その日は notes に書くこと。\n"
    return text


def check_coverage(assignments, all_staff):           # 社員が毎回1人以上いるか
    id_to_emp = {}
    for staff in all_staff:
        id_to_emp[staff["staff_id"]] = staff["employment"]

    problems = []
    for day in range(1, 31):
        date = f"2026-11-{day:02d}"
        
        for slot_minute in range(8 * 60, 22 * 60 + 30, 30):
            count = 0
            for work in assignments:
                if work["date"] == date and id_to_emp[work["staff_id"]] == "社員" and to_min(work["start"]) <= slot_minute < to_min(work["end"]):
                    count += 1
            
            if count == 0:
                problems.append(f"{date} {slot_minute // 60:02d}:{slot_minute % 60:02d} 社員がいません")
    return problems


def check_qualification(assignments, all_staff, rules):       # 資格者が足りているか
    id_to_qual = {}
    for staff in all_staff:
        id_to_qual[staff["staff_id"]] = staff.get("qualification")    # 資格がない人は None

    problems = []
    for rule in rules:                                        # ルールが [] なら何もしない(スイッチOFF)
        for day in range(1, 31):
            date = f"2026-11-{day:02d}"
            for slot_minute in range(to_min(rule["start"]), to_min(rule["end"]), 30):
                count = 0
                for work in assignments:
                    if work["date"] == date and id_to_qual.get(work["staff_id"]) in rule["qualification"] and to_min(work["start"]) <= slot_minute < to_min(work["end"]):
                        count += 1
                if count < rule["min"]:
                    problems.append(f"{date} {slot_minute // 60:02d}:{slot_minute % 60:02d} 資格者が{rule['min']}人未満です({count}人)")
    return problems


def check_requests(assignments, all_staff):                 # パート・バイトが、申請のない日に入っていないか
    id_to_staff = {}
    for staff in all_staff:
        id_to_staff[staff["staff_id"]] = staff

    problems = []
    for work in assignments:
        staff = id_to_staff[work["staff_id"]]
        if staff["employment"] not in ("パート", "バイト"):
            continue                                        # 社員はここでは見ない
        
        ok_dates = []
        for request in staff["requests"]:
            if "start" in request:                                # 出たい日だけ(off の日は入れない)
                ok_dates.append(request["date"])

        if work["date"] not in ok_dates:
            problems.append(f"{work['date']} ID{work['staff_id']}は申請のない日です")

    return problems


def check_closing(assignments, all_staff):                   # 22:30まで残るのは closing_ok の人だけ
    id_to_staff = {}
    for staff in all_staff:
        id_to_staff[staff["staff_id"]] = staff

    problems = []
    for work in assignments:
        staff = id_to_staff[work["staff_id"]]
        if staff["employment"] not in ("パート", "バイト"):
            continue

        if work["end"] > "22:00" and not staff.get("closing_ok"):
            problems.append(f"{work['date']} ID{work['staff_id']}は22:30まで残れません")
    return problems


def check_shain_days(assignments, all_staff, off_days):      # 社員の出勤日数が、決まりどおりか
    if off_days is None:                                     # None のときは何も見ない(スイッチOFF)
        return []
    
    work_days = 30 - off_days
    
    problems = []
    for staff in all_staff:
        if staff["employment"] != "社員":
            continue
        dates = []
        for work in assignments:
            if work["staff_id"] == staff["staff_id"] and work["date"] not in dates:
                dates.append(work["date"])                                    # 同じ日は1回だけ数える
        for request in staff["requests"]:
            if request.get("paid_leave") is True and request["date"] not in dates:     # 1日まるごと有給の日も、出勤日数に数える
                dates.append(request["date"])
        if len(dates) != work_days:
            problems.append(f"ID{staff['staff_id']}の出勤日数が{len(dates)}日です(決まりは{work_days}日)")
    return problems



def max_headcount(date, slot_minute, all_staff, exclude):              # その日その時刻に、理論上いちばん多く何人出られるか
    n = 0
    for staff in all_staff:
        if staff.get("qualification") in exclude:
            continue

        req = find_request(staff, date)
        if staff["employment"] == "社員":
            if req and (req.get("off") or req.get("paid_leave") is True):
                continue

            if req and "start" in req and not (to_min(req["start"]) <= slot_minute < to_min(req["end"])):
                continue
            n += 1

        else:                                                # パート・バイトは、申請した時間だけ
            if req and "start" in req and to_min(req["start"]) <= slot_minute < to_min(req["end"]):
                n += 1
    return n


def impossible_cases(rules, all_staff):                     # 理論上、最低人数に届かない (日付, ルール, 最大人数) の一覧
    out = []
    for day in range(1, 31):
        date = f"2026-11-{day:02d}"

        for rule in rules:
            slots = range(to_min(rule["start"]), to_min(rule["end"]), 30)
            best = min(max_headcount(date, slot_minute, all_staff, rule.get("exclude", [])) for slot_minute in slots)

            if best < rule["min"]:
                out.append((date, rule, best))
    return out


def make_shortage_report(rules, all_staff):                  #管理者に見せる『人が足りない日』と、相談の候補
    lines = []
    for date, rule, best in impossible_cases(rules, all_staff):
        lines.append(f"{date} {rule['start']}〜{rule['end']}：最大でも{best}人(最低{rule['min']}人)")
        part, shain = [], []
        for staff in all_staff:
            if staff.get("qualification") in rule.get("exclude", []):
                continue

            req = find_request(staff, date)
            if staff["employment"] == "社員":
                if req and req.get("off"):
                    shain.append(str(staff["staff_id"]))

            elif not req:   # 申請も休みの連絡もない人
                part.append(str(staff["staff_id"]))

        if part:
            lines.append(f"  → パート・バイトに出られるか聞く候補(ID): {', '.join(part)}")

        if shain:
            lines.append(f"  → 希望休を変えられるか相談する社員(ID): {', '.join(shain)}")
    return lines


def check_headcount(assignments, all_staff, rules):                             # 時間帯ごとの最低人数が守られているか
    skip = {(data, rule["start"]) for data, rule, _ in impossible_cases(rules, all_staff)}
    qual = {staff["staff_id"]: staff.get("qualification") for staff in all_staff}
    problems = []
    for day in range(1, 31):
        date = f"2026-11-{day:02d}"
        for rule in rules:
            if (date, rule["start"]) in skip:
                continue
            low = None
            for slot_minute in range(to_min(rule["start"]), to_min(rule["end"]), 30):
                n = sum(1 for work in assignments
                        if work["date"] == date
                        and qual.get(work["staff_id"]) not in rule.get("exclude", [])
                        and to_min(work["start"]) <= slot_minute < to_min(work["end"]))
                low = n if low is None else min(low, n)
            if low is not None and low < rule["min"]:
                problems.append(f"{date} {rule['start']}〜{rule['end']}の人数が{low}人です。{rule['min']}人以上にしてください")
    return problems


def problem_key(problem):                              # 人数の数字を消して、同じ問題か比べる
    return re.sub(r"\d+人", "", problem)


def make_instruction_text(instruction):          # 店長の指示を、LLMに渡す文にする
    if not instruction:
        return ""
    return f"\n【店長からの指示(今回は必ず反映すること)】\n- {instruction}\n"


def make_prev_draft_text(draft):                 # 前回の案を、土台としてLLMに渡す
    if not draft:                                # 1回目は空なので、何も足さない
        return ""
    return ("\n【前回の案(これを土台にして、問題の日と店長の指示だけを直すこと。ほかの日は変えない)】\n"
            + json.dumps(draft, ensure_ascii=False) + "\n")


# 2-6b) 【コード】人が足りない日に連絡する候補の順番を作る
# 順番 = 直近の勤務日数が少ない人が先 (基準文書§20。スキルの一致は、今回は未実装)
def build_contact_queue(rules, all_staff, draft, asked):
    work_days = {}                                           # 案の中で、1人が何日入っているか
    for assignment in draft:
        work_days.setdefault(assignment["staff_id"], set()).add(assignment["date"])

    queue, seen = [], set()
    for date, rule, best in impossible_cases(rules, all_staff):
        cands = []
        for staff in all_staff:
            if staff.get("qualification") in rule.get("exclude", []):
                continue
            if staff["employment"] == "社員":                    # 社員は「希望休の相談」なので、ここでは連絡しない
                continue
            if find_request(staff, date):                        # 申請か休みの連絡がある人には聞かない
                continue
            if (staff["staff_id"], date) in asked:               # もう聞いた人には聞かない 
                continue
            cands.append(staff)
        cands.sort(key=lambda staff: (len(work_days.get(staff["staff_id"], ())), staff["staff_id"]))
        for staff in cands:
            if (staff["staff_id"], date) in seen:                # 同じ人に同じ日を2回聞かない
                continue
            seen.add((staff["staff_id"], date))
            queue.append({"staff_id": staff["staff_id"], "date": date, "start": rule["start"], "end": rule["end"]})
    return queue


def validate(state: State):   # ルールを守っているか検査する
    print("編成案を検査中...")

    # 【分岐】検査回数が上限に達しているかどうかを判断する
    print(f"検査回数: {state['return_count']}/{state['upper_limit']}")
    if state["return_count"] > state["upper_limit"] :
        print("編成案の検査回数が上限に達しました。手動編成を行います。")
        state["result"] = "limit_reached"
        return {"result": state['result']}

    else:
        # 検査ロジック実装
        store = STORE_SETTINGS[state["store_id"]]
        draft = state["draft_store_shift"]
        staff = state["llm_all_staff"]

        problems = []
        problems += check_coverage(draft, staff)
        problems += check_qualification(draft, staff, store["qualification_rules"])
        problems += check_requests(draft, staff)
        problems += check_closing(draft, staff)
        problems += check_shain_days(draft, staff, store["shain_off_days"])
        problems += check_headcount(draft, staff, store["min_headcount"])

        if state["draft_store_shift"] == []:
            state["result"] = "retry"
        elif problems and {problem_key(problem) for problem in problems} == {problem_key(problem) for problem in state.get("problems", [])}:
            print("前回と同じ問題が残っています。これ以上は直りません。")
            return {"result": "limit_reached", "problems": problems}
        elif problems and state["return_count"] >= state["upper_limit"]:
            print("編成案の検査回数が上限に達しました。手動編成を行います。")
            return {"result": "limit_reached", "problems": problems}
        elif problems:
            print(problems)
            state["result"] = "retry"
            return {"result": state['result'], "problems": problems}
        else:
            state["result"] = "valid"

        # 【分岐】検査結果
        if state["result"] == "valid":
            print("編成案は有効です。")
            shortage = make_shortage_report(store["min_headcount"], state["llm_all_staff"])
            if shortage:
                print("【人が足りない日(管理者に相談してください)】")
                print("\n".join(shortage))

            # 人が足りない日があれば、スタッフに連絡する(上限まで)
            asked_count = state.get("contact_asked", 0)
            queue = state.get("contact_queue", [])

            if shortage and asked_count == 0 and not queue:  # 最初の1回だけ、候補の順番を作る / 처음 한 번만 후보 순서를 만든다
                already_asked = {(log_item["staff_id"], log_item["date"]) for log_item in state.get("contact_log", [])}
                queue = build_contact_queue(store["min_headcount"], state["llm_all_staff"], draft, already_asked)

            still_short = {(date, rule["start"]) for date, rule, max_people
                           in impossible_cases(store["min_headcount"], state["llm_all_staff"])}
            queue = [item for item in queue if (item["date"], item["start"]) in still_short]   # もう足りた日は聞かない / 이미 충족된 날은 묻지 않는다
            
            if queue and asked_count < state.get("contact_limit", 3):
                print("人が足りない日があるので、スタッフに連絡します。")
                return {"result": "no_staff", "problems": [], "contact_queue": queue}
            return {"result": state['result'], "problems": [], "contact_queue": queue}

        elif state["result"] == "no_staff":
            print("編成案に必要なスタッフが不足しています。再度編成を行います。")
            return {"result": state['result']}
        elif state["result"] == "limit_reached":
            print("編成案の検査回数が上限に達しました。手動編成を行います。")
            return {"result": state['result']}
        elif state["result"] == "retry":
            print("案が空です。作り直します。")
            return {"result": state['result']}
        else:
            print("編成案に問題があります。再度編成を行います。")
            return {"result": state['result']}


# 【分岐】検査結果に応じて、スタッフに連絡するか、自動編成を再度行うかを判断する
def route_2(state) -> Literal["approve", "contact", "auto_draft", "manual_draft"]:
    if state["result"] == "valid":
        return "approve"
    elif state["result"] == "no_staff":
        return "contact"
    elif state["result"] == "limit_reached":
        return "manual_draft"
    elif state["result"] == "retry":
        return "auto_draft"
    else:
        return "manual_draft"


# 2-7) 【コード】管理者手動編成
def manual_draft(state):
    print("手動編成中...")
    # 手動編成のロジックを実装する
    for problem in state.get("problems", [])[:5]:
        print("未解決: ", problem)
    return {}


# 2-8) 【コード】人手が足りない日のLINE連絡 (基準文書§20)
# 1人ずつ聞く。返事は3つ: 可能 / 断り / 不確か
#   可能   -> その人の希望と案に足して、validate へ戻る
#   断り   -> 次の人へ (上限3人。尽きたら管理者の手動編成へ)
#   不確か -> 今回は「断り」と同じ扱い。先回りの連絡(prefetch)は未実装
CONTACT_ANSWERS = ("可能", "断り", "不確か")


def contact(state) -> Command[Literal["validate", "contact", "manual_draft"]]:
    queue = state["contact_queue"]
    target = queue[0]                                        # いま聞く人 
    staff_id = target["staff_id"]
    name = id_to_name.get(staff_id, f"ID{staff_id}")
    print(f"スタッフに連絡中... {name}さん {target['date']} {target['start']}〜{target['end']}")

    # ここで止まる。返事(LINEのボタン)で再開する
    # ⚠ 再開すると、この関数は最初から、もう一度走る。LINE送信は、この行より前に書かない(2回送られる)
    answer = interrupt({"ask": "可能 | 断り | 不確か", "staff_id": staff_id, "staff_name": name,
                        "date": target["date"], "start": target["start"], "end": target["end"]})

    if isinstance(answer, dict):                             # {"answer": "可能"} でも受ける
        answer = answer.get("answer")
    if answer not in CONTACT_ANSWERS:
        raise ValueError(f"不正な返事: {answer}")

    asked_count = state.get("contact_asked", 0) + 1
    contact_log = list(state.get("contact_log", [])) + [{**target, "answer": answer}]
    remaining_queue = queue[1:]                              # まだ聞いていない人

    if answer == "可能":
        print(f"{name}さんが出られます。案に足します。")
        new_staff_list = copy.deepcopy(state["llm_all_staff"])   # その人の希望に、この日の勤務を足す 

        for staff in new_staff_list:
            if staff["staff_id"] == staff_id:
                staff.setdefault("requests", []).append(
                    {"date": target["date"], "start": target["start"], "end": target["end"]})
                
        new_draft = list(state["draft_store_shift"]) + [
            {"staff_id": staff_id, "date": target["date"], "start": target["start"], "end": target["end"]}]
        remaining_queue = [item for item in remaining_queue
                           if not (item["staff_id"] == staff_id and item["date"] == target["date"])]
        
        return Command(goto="validate", update={
            "llm_all_staff": new_staff_list, "draft_store_shift": new_draft,
            "contact_queue": remaining_queue, "contact_asked": asked_count, "contact_log": contact_log})

    print(f"{name}さんは{answer}でした。")                  # 断り・不確か
    update = {"contact_queue": remaining_queue, "contact_asked": asked_count, "contact_log": contact_log}

    if remaining_queue and asked_count < state.get("contact_limit", 3):
        return Command(goto="contact", update=update)        # 次の人へ 
    print("連絡できる人がいなくなりました。管理者の手動編成に進みます。")
    return Command(goto="manual_draft", update=update)


# 2-9) 【待機】管理者承認
def approve(state):
    print("店長の承認を待っています...")
    resp = interrupt({
        "draft_store_shift": state["draft_store_shift"],
        "ask": "approved | redraft_requested | edit_saved"
    })
    
    if isinstance(resp, str):                                     # 文字列だけで再開しても動くように
        resp = {"decision": resp}

    update = {"decision": resp["decision"]}

    if resp["decision"] == "redraft_requested":
        update["instruction"] = resp.get("instruction", "")

    if resp["decision"] == "edit_saved":
        update["draft_store_shift"] = resp["assignments"]

    return update



# 2-10) 【分岐】店長の決定に応じて、スタッフに連絡するか、自動編成を再度行うかを判断する
def route_3(state) -> Command[Literal["finalize", "auto_draft", "validate"]]:
    decision = state["decision"]
    if decision == "approved":
        return Command(goto="finalize")
    if decision == "redraft_requested":
        return Command(goto="auto_draft", update={
            "manager_request_count": state["manager_request_count"] + 1,
            "return_count": 0, "contact_asked": 0, "contact_queue": []})
    if decision == "edit_saved":
        return Command(goto="validate")
    raise ValueError(f"不正な決定: {decision}")


# 2-11)【コード】最新データで再チェックして、確定を保存
def finalize(state):
    print("編成案を確定しました。")
    return {"final_message": "〇月の編成案を確定しました。"}



# 2-12) 【コード】最終結果を会社の規定に変換
# -> 削除


# 2-13) 【コード】最終結果を各従業員に通知
def send_final_shift(state):
    print("最終シフトを送信しました。")
    return {"final_message": "〇月の最終シフトを送信しました。"}


               


# ===============================================================================
# 3) グラフの設計
# ===============================================================================
builder = StateGraph(State)
builder.add_node("collect", collect)
builder.add_node("skill_check", skill_check)
builder.add_node("auto_draft", auto_draft)
builder.add_node("validate", validate)
builder.add_node("manual_draft", manual_draft)
builder.add_node("contact", contact)
builder.add_node("approve", approve)
builder.add_node("route_3", route_3)
builder.add_node("finalize", finalize)
builder.add_node("send_final_shift", send_final_shift)





# ===============================================================================
# 4) グラフ連結
# ===============================================================================
builder.add_edge(START, "collect")
builder.add_conditional_edges("collect", route_1, {"skill_check": "skill_check", "auto_draft": "auto_draft"})
builder.add_edge("skill_check", "auto_draft")
builder.add_edge("auto_draft", "validate")
builder.add_conditional_edges("validate", route_2, {"approve": "approve", "contact": "contact", "auto_draft": "auto_draft", "manual_draft": "manual_draft"})
builder.add_edge("manual_draft", "approve")
builder.add_edge("approve", "route_3")
builder.add_edge("finalize", "send_final_shift")
builder.add_edge("send_final_shift", END)





# ===============================================================================
# 5) グラフ実行
# ===============================================================================
if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "start"            # 実行モードを取得する（デフォルトは "start"）
    thread = sys.argv[2] if len(sys.argv) > 2 else "test_resume_1"  # スレッドIDを取得する（デフォルトは "test_resume_1"）

    with PostgresSaver.from_conn_string(DB_URL) as saver:    # データベースに接続する
        saver.setup()                                        # データベースのテーブルを作成する
        graph = builder.compile(checkpointer=saver)          # グラフをコンパイルする
        config = {"configurable": {"thread_id": thread}}     # スレッドIDを設定する
        start = time.time()                                  # 開始時間を記録する

        if mode == "start":
            inp = {"store_id": 101}
        elif mode == "approve":
            inp = Command(resume="approved")
        elif mode == "contact":                              # 連絡の返事で再開する (例: contact 可能)
            inp = Command(resume=sys.argv[3] if len(sys.argv) > 3 else "可能")
        elif mode == "redraft":
            inp = Command(resume={"decision": "redraft_requested", "instruction": "11/4の9時〜13時を4人以上にしてください"})
        else:
            raise SystemExit(f"不明なモード: {mode} (start / approve / contact / redraft)")

        for chunk in graph.stream(inp, config, stream_mode="updates"):                  # グラフをストリームモードで実行する
            for node_name, update in chunk.items():                                     # ノード名と更新内容を表示する
                print(f"--- {node_name} が終わりました（{time.time() - start:.1f}秒）")   # 経過時間を表示する

        print("ストリーム終了")
        print("次のノード:", graph.get_state(config).next)