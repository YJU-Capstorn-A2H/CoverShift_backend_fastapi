# graph.py -- ノード(1つ1つの処理)と、それをつなぐ builder
#   ・ここに処理の「中身」がある。shift_graph.py は、これを動かす入口だけ
#   ・分岐は routes.py / 検査と計算は node/checks.py / LLM は node/llm.py / プロンプトは node/prompts.py
#   ・止まる所(interrupt)は contact と approve の2つだけ(渡す値・返す値は README.md)
import copy, json, time
from typing import Literal
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command, interrupt
from settings import DB_URL, MANAGER_REQUEST_LIMIT, llm_all_staff, id_to_name, get_store
from state import State
from routes import route_2, route_3
from node.checks import (all_dates, check_coverage, check_qualification, check_requests, check_closing, check_double_booking,
                         check_shain_days, check_headcount, problem_key, make_shortage_report, impossible_cases, build_contact_queue)
from node.llm import get_llm, extract_json
from node.db import load_period_data
from node.convert import split_names
from node.line import send_line, make_final_text
from node.prompts import (rules, make_qualification_text, make_days_text,
                          make_headcount_text, make_problem_text, make_instruction_text, make_prev_draft_text)
# import asyncio                          # MCP を使うときに戻す
# from mcp_call import call_mcp_tool


def get_name(state, staff_id):    # 従業員の表示名。DB から読んだときは名前を読まないので「ID3」の形(名前は画面・API 側が staff_id から出す)。無ければ staff_data.json の表から
    if state.get("period_id"):
        return f"ID{staff_id}"
    return id_to_name.get(staff_id, f"ID{staff_id}")


# ===============================================================================
# 2) グラフ作成
# ==============================================================================

# 2-1) collect -- 最初に1回だけ。スタッフの希望を読んで、State を初期化する
#   出力: llm_all_staff(全員のデータ)と、各カウンターの初期値
#   いまは settings.py のデータをそのまま渡す(B1 で DB から、D5 で MCP から読む形に置き換える)
def collect(state):
    print("希望シフトを収集中...")
    # data = asyncio.run(call_mcp_tool(SERVER, "get_staff_availability"))                                 # MCPサーバーからデータを取得する
    # mcp_staff = data["staff"]                                                                           # MCPサーバーから取得したスタッフデータ
    # assert mcp_staff == llm_all_staff, "MCPサーバーから取得したスタッフデータと、初期データが一致しません。"  # debug用のアサーション

    db_update = {}                                          # DB から読んだときだけ入る(store_id・period・store_settings)
    staff_for_llm = llm_all_staff                           # period_id が無いときは、今までどおり staff_data.json のデータ
    if state.get("period_id"):                              # period_id があれば DB から読む(B1)
        data = load_period_data(DB_URL, state["period_id"])
        if state.get("store_id") not in (None, data["store_id"]):
            raise ValueError(f"store_id={state['store_id']} と period_id={state['period_id']} の店が違います(DB では store_id={data['store_id']})")
        _, staff_for_llm = split_names(data["staff_list"])  # 名前は State に入れない(DB からはそもそも読まない)
        db_update = {"store_id": data["store_id"], "period": data["period"], "store_settings": data["store_settings"]}
    elif not llm_all_staff:
        raise ValueError("period_id がなく、staff_data.json も見つかりません")
    return {**db_update, "llm_all_staff": staff_for_llm, "draft_store_shift": [], "result": "", "manager_request_count": 0, "instruction": "", "return_count": 0, "upper_limit": 3, "problems": [],
            "contact_queue": [], "contact_asked": 0, "contact_limit": 3, "contact_log": [], "blocked_reasons": []}


# 2-4) auto_draft -- LLM が、月間の編成案を作る(1回で約6〜10分)
#   入力: llm_all_staff / problems(前回の問題) / instruction(店長の指示) / draft_store_shift(前回の案)
#   出力: draft_store_shift(新しい案) / return_count(呼んだ回数)
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
    store = get_store(state)
    prompt = rules + make_qualification_text(store["qualification_rules"]) + make_days_text(store["shain_off_days"], len(all_dates(state.get("period")))) + make_headcount_text(store["min_headcount"]) + make_problem_text(state.get("problems", [])) + "\n【スタッフのデータ(JSON)】\n" + json.dumps(state["llm_all_staff"], ensure_ascii=False) + make_instruction_text(state.get("instruction", "")) + make_prev_draft_text(state.get("draft_store_shift", []))
    llm = get_llm()
    llm_start_time = time.time()
    res = llm.invoke(prompt)                                       # ← ここで時間がかかる
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

    with open("llm_raw_output.txt", "w", encoding="utf-8") as output_file:   # 返事の全文を保存(デバッグ用)
        output_file.write(text)

        print("返事の先頭:", repr(text[:100]))
    print("返事の末尾:", repr(text[-100:]))

    data = extract_json(text)                                                # 返事から、編成案のJSONだけを取り出す
    print(f"編成の件数: {len(data['assignments'])}")

    with open("month_draft.json", "w", encoding="utf-8") as output_file:     # 案をJSONで保存(デバッグ用)
        json.dump(data, output_file, ensure_ascii=False)

    return {"draft_store_shift": data["assignments"], "return_count": state["return_count"]}


# 2-5) validate -- 編成案がルールを守っているか検査する(コードだけ。LLM は使わない)
#   入力: draft_store_shift(案) / llm_all_staff(希望)
#   出力: result = valid(OK) / retry(作り直し) / no_staff(人が足りない日あり) / limit_reached(承認画面へ)
#         problems = 見つかった問題の一覧
#   次の行き先: route_2 (2-6) が result を見て決める
#   判定の順番: ⓪検査回数が上限超え→limit_reached ①案が空→retry ②店長が直した案に問題→limit_reached(LLMは呼ばない)
#               ③前回と同じ問題→limit_reached ④作り直しの上限→limit_reached ⑤問題あり→retry ⑥問題なし→valid
#               (⑥で人が足りない日が残り、連絡できる人がいれば no_staff)
#   注意: 店長が直した案(edit_saved)は、LLM に作り直させない(店長の編集が消えるため)
def validate(state: State):
    print("編成案を検査中...")

    # 【分岐】検査回数が上限に達しているかどうかを判断する
    print(f"検査回数: {state['return_count']}/{state['upper_limit']}")
    if state["return_count"] > state["upper_limit"] :
        print("編成案の検査回数が上限に達しました。承認画面で、店長に直してもらいます。")
        state["result"] = "limit_reached"
        return {"result": state['result']}

    else:
        # 検査ロジック実装
        store = get_store(state)
        draft = state["draft_store_shift"]
        staff = state["llm_all_staff"]
        dates = all_dates(state.get("period"))                  # この月の日付(state["period"] が無ければ settings.PERIOD)

        problems = []
        problems += check_coverage(draft, staff, dates)                                   # どの30分にも社員がいるか
        problems += check_qualification(draft, staff, store["qualification_rules"], dates)    # 資格者が足りているか
        problems += check_requests(draft, staff)                                   # パート・バイトが申請のない日に入っていないか
        problems += check_closing(draft, staff)                                    # 22:30まで残るのは closing_ok の人だけ
        problems += check_shain_days(draft, staff, store["shain_off_days"], dates)            # 社員の出勤日数
        problems += check_headcount(draft, staff, store["min_headcount"], dates)              # 時間帯ごとの最低人数
        problems += check_double_booking(draft)                                    # 同じ人の勤務の重なり

        if state["draft_store_shift"] == []:                    # 案が空 -> 作り直し
            state["result"] = "retry"
        elif problems and state.get("decision") == "edit_saved":   # 店長が直した案は、LLMに作り直させない(編集が消えるため)
            print("店長が直した案に、まだ問題があります。店長の画面に戻します。")
            return {"result": "limit_reached", "problems": problems}
        elif problems and {problem_key(problem) for problem in problems} == {problem_key(problem) for problem in state.get("problems", [])}:   # 前回と同じ問題 -> もう直らない
            print("前回と同じ問題が残っています。これ以上は直りません。")
            return {"result": "limit_reached", "problems": problems}
        elif problems and state["return_count"] >= state["upper_limit"]:   # 作り直しの上限
            print("編成案の検査回数が上限に達しました。承認画面で、店長に直してもらいます。")
            return {"result": "limit_reached", "problems": problems}
        elif problems:                                         # 問題あり -> 作り直し
            print(problems)
            state["result"] = "retry"
            return {"result": state['result'], "problems": problems}
        else:
            state["result"] = "valid"

        # 【分岐】検査結果
        if state["result"] == "valid":
            print("編成案は有効です。")
            shortage = make_shortage_report(store["min_headcount"], state["llm_all_staff"], dates)
            if shortage:
                print("【人が足りない日(管理者に相談してください)】")
                print("\n".join(shortage))

            # 人が足りない日があれば、スタッフに連絡する(上限まで)
            asked_count = state.get("contact_asked", 0)
            queue = state.get("contact_queue", [])

            if shortage and asked_count == 0 and not queue:  # 最初の1回だけ、候補の順番を作る
                already_asked = {(log_item["staff_id"], log_item["date"]) for log_item in state.get("contact_log", [])}
                queue = build_contact_queue(store["min_headcount"], state["llm_all_staff"], draft, already_asked, dates)

            still_short = {(date, rule["start"]) for date, rule, max_people
                           in impossible_cases(store["min_headcount"], state["llm_all_staff"], dates)}
            queue = [item for item in queue if (item["date"], item["start"]) in still_short]   # もう足りた日は聞かない
            
            if queue and asked_count < state.get("contact_limit", 3):
                print("人が足りない日があるので、スタッフに連絡します。")
                return {"result": "no_staff", "problems": [], "contact_queue": queue}
            return {"result": state['result'], "problems": [], "contact_queue": queue}

        elif state["result"] == "no_staff":
            print("編成案に必要なスタッフが不足しています。再度編成を行います。")
            return {"result": state['result']}
        elif state["result"] == "limit_reached":
            print("編成案の検査回数が上限に達しました。承認画面で、店長に直してもらいます。")
            return {"result": state['result']}
        elif state["result"] == "retry":
            print("案が空です。作り直します。")
            return {"result": state['result']}
        else:
            print("編成案に問題があります。再度編成を行います。")
            return {"result": state['result']}


# 2-8) contact -- 人手が足りない日に、1人ずつ「出られますか?」と聞く (基準文書§20)
#   止まる所(interrupt)。LINE送信は未実装で、いまは返事(可能/断り/不確か)を受け取る所だけ
# 1人ずつ聞く。返事は3つ: 可能 / 断り / 不確か
#   可能   -> その人の希望と案に足して、validate へ戻る
#   断り   -> 次の人へ (上限3人。尽きたら承認画面へ)
#   不確か -> 今回は「断り」と同じ扱い。先回りの連絡(prefetch)は未実装
CONTACT_ANSWERS = ("可能", "断り", "不確か")   # 連絡の返事の3種類(ここだけで定義する)


def contact(state) -> Command[Literal["validate", "contact", "approve"]]:
    queue = state["contact_queue"]
    target = queue[0]                                        # いま聞く人 
    staff_id = target["staff_id"]
    name = get_name(state, staff_id)
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
    print("連絡できる人がいなくなりました。承認画面に進みます。")
    return Command(goto="approve", update=update)


# 2-9) approve -- 管理者の承認待ち。店長の画面に案を出して止まる(interrupt)
#   返事: approved / redraft_requested / edit_saved(渡す値・返す値は README.md)
def approve(state):
    print("店長の承認を待っています...")
    resp = interrupt({
        "draft_store_shift": state["draft_store_shift"],
        "ask": "approved | redraft_requested | edit_saved",
        "problems": state.get("problems", []),                                    # 直っていない問題(参考) 
        "blocked_reasons": state.get("blocked_reasons", []),                      # 承認できない理由
        "redraft_left": MANAGER_REQUEST_LIMIT - state["manager_request_count"]})  # あと何回やり直せるか

    if isinstance(resp, str):                                                     # 文字列だけで再開しても動くように
        resp = {"decision": resp}

    update = {"decision": resp["decision"], "blocked_reasons": []}                # 理由は読んだら消す

    if resp["decision"] == "redraft_requested":
        update["instruction"] = resp.get("instruction", "")

    if resp["decision"] == "edit_saved":
        update["draft_store_shift"] = resp["assignments"]

    return update


# 2-11) finalize -- 最新データで再チェックして、確定を保存
#   (仮実装) いまはメッセージを返すだけ。DBへの保存は B8 で作る
def finalize(state):
    print("編成案を確定しました。")
    return {"final_message": "〇月の編成案を確定しました。"}



# 2-12) send_final_shift -- 最終結果を各従業員に通知
#   確定した案で、勤務のある人全員に、1人1通ずつ送る(送る部品は node/line.py。いまはスタブ=本物は送らない)
#   ⚠ 再開すると、この関数は最初から走り直す。「送り済み」の印は、まだ無い(B9 の宿題。README §10)
def send_final_shift(state):
    draft = state["draft_store_shift"]
    staff_ids = sorted({work["staff_id"] for work in draft})
    for staff_id in staff_ids:
        send_line(staff_id, make_final_text([work for work in draft if work["staff_id"] == staff_id]))
    print(f"最終シフトを{len(staff_ids)}人に送信しました。")
    return {"final_message": f"最終シフトを{len(staff_ids)}人に送信しました。"}




               


# ===============================================================================
# 4) グラフの設計: ノード(処理)を登録する。route_3 だけはノードとして登録する(Command で行き先を返すため)。route_2 は下の5)で分かれ道として登録する
# ===============================================================================
builder = StateGraph(State)
builder.add_node("collect", collect)
builder.add_node("auto_draft", auto_draft)
builder.add_node("validate", validate)
builder.add_node("contact", contact)
builder.add_node("approve", approve)
builder.add_node("route_3", route_3)
builder.add_node("finalize", finalize)
builder.add_node("send_final_shift", send_final_shift)





# ===============================================================================
# 5) グラフ連結: 矢印をつなぐ。分かれ道の route_2 は routes.py (route_3 は Command で行き先を返す)
# ===============================================================================
builder.add_edge(START, "collect")
builder.add_edge("collect", "auto_draft")
builder.add_edge("auto_draft", "validate")
builder.add_conditional_edges("validate", route_2, {"approve": "approve", "contact": "contact", "auto_draft": "auto_draft"})
builder.add_edge("approve", "route_3")
builder.add_edge("finalize", "send_final_shift")
builder.add_edge("send_final_shift", END)