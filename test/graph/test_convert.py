# test_convert.py -- B1-1 のテスト: DB の行(v18) → グラフのデータ に正しく変わるか
# DB・LLM は使わない。ニセの行を作って、node/convert.py の関数に渡すだけ
# 実行: test\graph フォルダで  python test_convert.py
import os, json
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "app" / "graph"))

with open("staff_test.json", "w", encoding="utf-8") as staff_file:      # settings を import するために必要(中身は使わない)
    json.dump([{"staff_id": 1, "staff_name": "X", "employment": "社員", "qualification": None,
                "staff_skill": "a", "admin_rate": "a", "closing_ok": True, "requests": []}], staff_file)
os.environ.update(DATABASE_URL="postgresql://dummy", ANTHROPIC_API_KEY="dummy", STAFF_FILE="staff_test.json")

# import はここにまとめる(上の「app/graph を探す設定」と「環境変数」のあとでないと動かない)
import datetime
import node.checks as checks
import node.convert as convert
import node.prompts as prompts

failed = []
def check(name, ok):
    print(("OK  " if ok else "NG  ") + name)
    if not ok:
        failed.append(name)

TIME = datetime.time
DATE = datetime.date

# --- ニセの DB 行 ------------------------------------------------------------------------
employee_rows = [
    {"employee_id": 4, "name": "バイトD", "is_regular": False, "has_qualification": False, "closing_ok": False},
    {"employee_id": 1, "name": "社員A", "is_regular": True, "has_qualification": True, "closing_ok": True},
    {"employee_id": 2, "name": "社員B", "is_regular": True, "has_qualification": False, "closing_ok": False},
    {"employee_id": 3, "name": "バイトC", "is_regular": False, "has_qualification": False, "closing_ok": True},
]
submission_rows = [
    {"employee_id": 3, "work_date": DATE(2026, 12, 8), "kind": "available", "start_time": TIME(9, 0), "end_time": TIME(13, 0)},
    {"employee_id": 3, "work_date": DATE(2026, 12, 2), "kind": "available", "start_time": "13:30:00", "end_time": "22:00:00"},   # 文字列の時刻
    {"employee_id": 2, "work_date": DATE(2026, 12, 5), "kind": "off", "start_time": None, "end_time": None},
    {"employee_id": 2, "work_date": DATE(2026, 12, 6), "kind": "paid_leave", "start_time": None, "end_time": None},
    {"employee_id": 2, "work_date": DATE(2026, 12, 7), "kind": "half_leave", "start_time": TIME(9, 0), "end_time": TIME(13, 0)},
]
# 平日(月〜金=1〜5)と土曜(6)は 09-13 に2人、日曜(7)は3人。資格者は 09-13 に毎日1人。13-22:30 は毎日4人
time_rule_rows = (
    [{"weekday": day, "start_time": TIME(9, 0), "end_time": TIME(13, 0), "min_headcount": 2, "min_qualified": 1} for day in range(1, 7)]
    + [{"weekday": 7, "start_time": TIME(9, 0), "end_time": TIME(13, 0), "min_headcount": 3, "min_qualified": 1}]
    + [{"weekday": day, "start_time": TIME(13, 0), "end_time": TIME(22, 30), "min_headcount": 4, "min_qualified": 0} for day in range(1, 8)]
)
period_row = {"target_month": DATE(2026, 12, 1), "designated_off_days": 9}

# --- 1) 日付・時刻・期間 ------------------------------------------------------------------
check("C1a: 時刻 datetime.time → '09:00'", convert.to_hhmm(TIME(9, 0)) == "09:00")
check("C1b: 時刻の文字列 '13:30:00' → '13:30'", convert.to_hhmm("13:30:00") == "13:30")
check("C1c: 12月分 → 31日", convert.make_period(DATE(2026, 12, 1)) == {"start": "2026-12-01", "days": 31})
check("C1d: 2月分(うるう年でない) → 28日", convert.make_period("2027-02-01")["days"] == 28)
check("C1e: 2月分(うるう年) → 29日", convert.make_period("2028-02-01")["days"] == 29)
try:
    convert.make_period(DATE(2026, 12, 2))
    check("C1f: 1日でない target_month はエラー", False)
except ValueError:
    check("C1f: 1日でない target_month はエラー", True)

# --- 2) スタッフ -------------------------------------------------------------------------
staff_list = convert.make_staff_list(employee_rows, submission_rows)
by_id = {staff["staff_id"]: staff for staff in staff_list}
check("C2a: 4人とも出る。ID順に並ぶ", [staff["staff_id"] for staff in staff_list] == [1, 2, 3, 4])
check("C2b: is_regular → 社員 / バイト", [by_id[i]["employment"] for i in (1, 2, 3, 4)] == ["社員", "社員", "バイト", "バイト"])
check("C2c: 資格者は登録販売者(管理者)、そうでなければ None", by_id[1]["qualification"] == "登録販売者(管理者)" and by_id[2]["qualification"] is None)
check("C2d: closing_ok がそのまま入る", by_id[3]["closing_ok"] is True and by_id[4]["closing_ok"] is False)
check("C2e: staff_skill と admin_rate は同じ値(スキルチェックが起きない)", all(staff["staff_skill"] == staff["admin_rate"] for staff in staff_list))
check("C2f: キーは今の staff_data.json と同じ", set(by_id[1]) == {"staff_id", "staff_name", "employment", "qualification", "staff_skill", "admin_rate", "closing_ok", "requests"})
check("C2g: 出られる日(時間つき)は日付順で、時刻は 'HH:MM'",
      by_id[3]["requests"] == [{"date": "2026-12-02", "start": "13:30", "end": "22:00"}, {"date": "2026-12-08", "start": "09:00", "end": "13:00"}])
check("C2h: 希望休 → off: True", {"date": "2026-12-05", "off": True} in by_id[2]["requests"])
check("C2i: 有給 → paid_leave: True", {"date": "2026-12-06", "paid_leave": True} in by_id[2]["requests"])
check("C2j: 半休 → paid_leave: 'half' と時間", {"date": "2026-12-07", "paid_leave": "half", "start": "09:00", "end": "13:00"} in by_id[2]["requests"])
check("C2k: 希望のない人は requests が空", by_id[1]["requests"] == [] and by_id[4]["requests"] == [])
check("C2l: 名前は staff_name に入る", by_id[1]["staff_name"] == "社員A")
try:
    convert.make_staff_list(employee_rows, [{"employee_id": 99, "work_date": DATE(2026, 12, 1), "kind": "off", "start_time": None, "end_time": None}])
    check("C2m: 知らない employee_id の希望はエラー", False)
except ValueError:
    check("C2m: 知らない employee_id の希望はエラー", True)
try:
    convert.make_request({"work_date": DATE(2026, 12, 1), "kind": "holiday", "start_time": None, "end_time": None})
    check("C2n: 知らない kind はエラー", False)
except ValueError:
    check("C2n: 知らない kind はエラー", True)

# --- 3) 名前を分ける ----------------------------------------------------------------------
id_to_name, llm_all_staff = convert.split_names(staff_list)
check("C3a: ID → 名前の辞書", id_to_name == {1: "社員A", 2: "社員B", 3: "バイトC", 4: "バイトD"})
check("C3b: LLM 用のリストに、名前が入っていない", all("staff_name" not in staff for staff in llm_all_staff) and "社員A" not in json.dumps(llm_all_staff, ensure_ascii=False))
check("C3c: 元のリストは変わらない(名前が残っている)", "staff_name" in staff_list[0])

# --- 4) 店の設定 -------------------------------------------------------------------------
store = convert.make_store_settings(time_rule_rows, period_row)
check("C4a: 人数ルール: 月〜土の 09-13 は2人(曜日つき)", {"start": "09:00", "end": "13:00", "min": 2, "weekdays": [1, 2, 3, 4, 5, 6]} in store["min_headcount"])
check("C4b: 人数ルール: 日曜だけ 09-13 は3人", {"start": "09:00", "end": "13:00", "min": 3, "weekdays": [7]} in store["min_headcount"])
check("C4c: 人数ルール: 毎日同じなら weekdays を付けない", {"start": "13:00", "end": "22:30", "min": 4} in store["min_headcount"])
check("C4d: 人数ルールは3件だけ(まとめられている)", len(store["min_headcount"]) == 3)
check("C4e: 資格者ルール: 毎日 09-13 に1人(weekdays なし)",
      store["qualification_rules"] == [{"qualification": ["登録販売者(管理者)"], "start": "09:00", "end": "13:00", "min": 1}])
check("C4f: 資格者が0人の時間帯(13-22:30)は、資格者ルールにならない", all(rule["start"] != "13:00" for rule in store["qualification_rules"]))
check("C4g: 公休日数が入る", store["shain_off_days"] == 9)
check("C4h: 曜日の順番が逆の行でも、同じ結果", convert.make_store_settings(list(reversed(time_rule_rows)), period_row) == store)
check("C4i: ルールが1件も無ければ、空のリスト", convert.make_store_settings([], period_row)["min_headcount"] == [])

# --- 5) 変換した結果を、今の検査・プロンプトに渡して動く --------------------------------------
dec_dates = checks.all_dates(convert.make_period(DATE(2026, 12, 1)))
sunday = "2026-12-06"
saturday = "2026-12-05"
draft = [{"staff_id": 1, "date": saturday, "start": "09:00", "end": "13:00"},
         {"staff_id": 2, "date": saturday, "start": "09:00", "end": "13:00"}]
saturday_problems = [p for p in checks.check_headcount(draft, staff_list, store["min_headcount"], [saturday]) if "09:00" <= p.split()[1] < "13:00"]
check("C5a: 土曜の 09-13 は2人いるので、人数の問題が出ない", saturday_problems == [])
draft_sunday = [{"staff_id": 1, "date": sunday, "start": "09:00", "end": "13:00"},
                {"staff_id": 3, "date": sunday, "start": "09:00", "end": "13:00"}]
sunday_13 = "2026-12-13"      # 12/6 は社員Bが有給で「理論上3人に届かない日」になり人数の検査が飛ばされるため、別の日曜で試す
staff_sunday_13 = [dict(staff, requests=staff["requests"] + [{"date": sunday_13, "start": "09:00", "end": "13:00"}]) if staff["staff_id"] == 3 else staff
                   for staff in staff_list]
draft_sunday_13 = [{"staff_id": 1, "date": sunday_13, "start": "09:00", "end": "13:00"},
                   {"staff_id": 3, "date": sunday_13, "start": "09:00", "end": "13:00"}]
check("C5b: 日曜の 09-13 は3人必要なので、2人だと人数の問題が出る",
      any("2026-12-13 09:00" in problem for problem in checks.check_headcount(draft_sunday_13, staff_sunday_13, store["min_headcount"], [sunday_13])))
check("C5c: 資格者(社員A)がいる日は、資格者の問題が出ない",
      checks.check_qualification(draft_sunday, staff_list, store["qualification_rules"], [sunday]) == [])
draft_no_qual = [{"staff_id": 2, "date": sunday, "start": "09:00", "end": "13:00"}]
check("C5d: 資格者がいない日は、資格者の問題が出る", checks.check_qualification(draft_no_qual, staff_list, store["qualification_rules"], [sunday]) != [])
check("C5e: 12月は31日あるので、公休9日 → 出勤22日が決まり", "22日" in prompts.make_days_text(store["shain_off_days"], len(dec_dates)))
check("C5f: 人数ルールの文章に「月・火・水・木・金・土のみ」が出る", "月・火・水・木・金・土のみ" in prompts.make_headcount_text(store["min_headcount"]))
check("C5g: 要求の check_requests: バイトCが申請のない日(12/3)に入ると問題", checks.check_requests([{"staff_id": 3, "date": "2026-12-03", "start": "13:30", "end": "20:00"}], staff_list) != [])
check("C5h: バイトCが申請のある日(12/2)なら問題なし", checks.check_requests([{"staff_id": 3, "date": "2026-12-02", "start": "13:30", "end": "20:00"}], staff_list) == [])

if failed:
    print("失敗:", failed)
    sys.exit(1)
print("ALL PASS")