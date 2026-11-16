# route_1, route_2, route_3
from typing import Literal
from langgraph.types import Command
from settings import STORE_SETTINGS, MANAGER_REQUEST_LIMIT
from node.checks import check_qualification, check_double_booking

# ===============================================================================
# 2) グラフ作成(分岐)
# ===============================================================================
# 2-2) 【分岐】スキルチェックが必要かどうかを判断する
def route_1(state) -> Literal["skill_check", "auto_draft"]:
    for staff in state["llm_all_staff"]:                         # 全員のデータを一人ずつ確認する
        if staff["staff_skill"] != staff["admin_rate"]:          # スキルと管理者評価が一致しない場合は、スキルチェックが必要
            return "skill_check"
        
    return "auto_draft"                                          # スキルと管理者評価が一致する場合は、自動編成に進む


# 2-10) 【分岐】検査結果に応じて、スタッフに連絡するか、自動編成を再度行うかを判断する
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


# 2-10) 【分岐】店長の決定に応じて、スタッフに連絡するか、自動編成を再度行うかを判断する
def route_3(state) -> Command[Literal["finalize", "auto_draft", "validate", "approve"]]:
    decision = state["decision"]

    if decision == "approved":
        store = STORE_SETTINGS[state["store_id"]]
        draft = state["draft_store_shift"]

        # 資格者なし・重複配置が残っていたら承認できない(管理者でも外せない)
        blocking = check_qualification(draft, state["llm_all_staff"], store["qualification_rules"]) + check_double_booking(draft)
        if blocking:
            print("承認できません。案を直してから、もう一度承認してください。")
            shown = blocking[:20]                                             # 長くなりすぎないよう20件まで / 20건까지
            if len(blocking) > 20:
                shown.append(f"(ほか{len(blocking) - 20}件)")
            return Command(goto="approve", update={"blocked_reasons": shown})
        
        return Command(goto="finalize")

    if decision == "redraft_requested":
        if state["manager_request_count"] >= MANAGER_REQUEST_LIMIT:           # 店長のやり直しは2回まで / 2번까지
            print("やり直しの依頼の回数が上限に達しました。")
            return Command(goto="approve", update={"blocked_reasons": [
                f"やり直しの依頼は{MANAGER_REQUEST_LIMIT}回までです。案を直して保存するか、承認してください。"]})
        
        return Command(goto="auto_draft", update={
            "manager_request_count": state["manager_request_count"] + 1,
            "return_count": 0, "contact_asked": 0, "contact_queue": []})

    if decision == "edit_saved":
        return Command(goto="validate")

    raise ValueError(f"不正な決定: {decision}")