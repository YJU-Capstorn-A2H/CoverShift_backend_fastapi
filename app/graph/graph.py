# builder とつなぎ。ここから graph を出す
import copy, json, time
from typing import Literal
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command, interrupt
from settings import STORE_SETTINGS, MANAGER_REQUEST_LIMIT, llm_all_staff, id_to_name
from state import State
from routes import route_1, route_2, route_3
from node.checks import (check_coverage, check_qualification, check_requents, check_closing, check_double_booking,
                         check_shain_days, check_headcount, problem_key, make_short_report, impossible_cases, build_contact_queue)
from node.llm import get_llm, extract_json
from node.prompts import (rules, make_qualification_text, make_days_text, make_instruction_text, make_prev_draft_text)

# mcp用 import / mcp용 import
# import asyncio
# from mcp_call import call_mcp_tool





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
            "contact_queue": [], "contact_asked": 0, "contact_limit": 3, "contact_log": [], "blocked_reasons": []}



# 2-3) 【コード】未確認を「要指導」とみなして、店長に知らせる(止まらない)
def skill_check(state):
    print("スキルチェック中...")
    for staff in state["llm_all_staff"]:                 
        if staff["staff_skill"] != staff["admin_rate"]:  
            print(f"{id_to_name[staff['staff_id']]}のスキルが違います。店長に知らせます。") 
    return {}



# 2-5) 【LLM】AIが編成案を作る
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



# 2-6c) 【コード】編成案の検査
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
        problems += check_double_booking(draft)

        if state["draft_store_shift"] == []:
            state["result"] = "retry"
        elif problems and state.get("decision") == "edit_saved":   # 店長が直した案は、LLMに作り直させない(編集が消えるため) / 점장이 고친 안은 LLM이 다시 만들지 않게 한다
            print("店長が直した案に、まだ問題があります。店長の画面に戻します。")
            return {"result": "limit_reached", "problems": problems}
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
        "ask": "approved | redraft_requested | edit_saved",
        "problems": state.get("problems", []),                                    # 直っていない問題(参考) 
        "blocked_reasons": state.get("blocked_reasons", []),                      # 承認できない理由
        "redraft_left": MANAGER_REQUEST_LIMIT - state["manager_request_count"]})  # あと何回やり直せるか

    if isinstance(resp, str):                                                     # 文字列だけで再開しても動くように
        resp = {"decision": resp}

    update = {"decision": resp["decision"], "blocked_reasons": []}                # 理由は読んだら消す / 이유는 읽었으면 지운다

    if resp["decision"] == "redraft_requested":
        update["instruction"] = resp.get("instruction", "")

    if resp["decision"] == "edit_saved":
        update["draft_store_shift"] = resp["assignments"]

    return update



# 2-11)【コード】最新データで再チェックして、確定を保存
def finalize(state):
    print("編成案を確定しました。")
    return {"final_message": "〇月の編成案を確定しました。"}



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