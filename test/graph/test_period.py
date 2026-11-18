# test_period.py -- B1-0 のテスト: 月の日数(28〜31日)と、曜日つきルールで、検査が正しく動くか
# DB・LLM は使わない。検査の関数を直接呼ぶだけ
# 実行: test\graph フォルダで  python test_period.py
import os, json
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "app" / "graph"))

staff_list = [
    {"staff_id": 1, "staff_name": "社員A", "employment": "社員", "qualification": "登録販売者(管理者)",
     "staff_skill": "a", "admin_rate": "a", "closing_ok": True, "requests": []},
    {"staff_id": 2, "staff_name": "バイトB", "employment": "バイト", "qualification": None,
     "staff_skill": "a", "admin_rate": "a", "closing_ok": False, "requests": []},
]
with open("staff_test.json", "w", encoding="utf-8") as staff_file:
    json.dump(staff_list, staff_file, ensure_ascii=False)
os.environ.update(DATABASE_URL="postgresql://dummy", ANTHROPIC_API_KEY="dummy", STAFF_FILE="staff_test.json")

# import はここにまとめる(上の「app/graph を探す設定」と「環境変数」のあとでないと動かない)
import settings
import graph as graph_module
import node.checks as checks
import node.prompts as prompts

failed = []
def check(name, ok):
    print(("OK  " if ok else "NG  ") + name)
    if not ok:
        failed.append(name)

DEC = {"start": "2026-12-01", "days": 31}      # 2026-12-01 は火曜日。土曜日は 5・12・19・26 日
FEB = {"start": "2027-02-01", "days": 28}
all_staff = staff_list

# --- 1) 日付の一覧 ---------------------------------------------------------------------------
dec_dates = checks.all_dates(DEC)
check("P1a: 12月は31日あって、最後は 12/31", len(dec_dates) == 31 and dec_dates[-1] == "2026-12-31")
check("P1b: 2月は28日あって、最後は 2/28", len(checks.all_dates(FEB)) == 28 and checks.all_dates(FEB)[-1] == "2027-02-28")
check("P1c: period を渡さなければ、今までどおり11月の30日", len(checks.all_dates()) == 30 and checks.all_dates()[0] == "2026-11-01")

# --- 2) ルールが使える日(曜日) ---------------------------------------------------------------
sat_rule = {"start": "09:00", "end": "13:00", "min": 2, "weekdays": [6]}
check("P2a: 土曜(6)のルールは、12/5(土)で使える", checks.rule_applies(sat_rule, "2026-12-05"))
check("P2b: 土曜のルールは、12/4(金)では使えない", not checks.rule_applies(sat_rule, "2026-12-04"))
check("P2c: weekdays が無いルールは、毎日使える", checks.rule_applies({"start": "09:00", "end": "13:00", "min": 2}, "2026-12-04"))
check("P2d: 日曜は 7", checks.rule_applies({"weekdays": [7]}, "2026-12-06") and not checks.rule_applies({"weekdays": [7]}, "2026-12-05"))

# --- 3) 社員の出勤日数が、月の日数に合う ----------------------------------------------------
def shain_works(days):
    return [{"staff_id": 1, "date": date, "start": "08:00", "end": "17:00"} for date in days]

check("P3a: 12月(31日)で公休9日 → 22日出勤なら問題なし",
      checks.check_shain_days(shain_works(dec_dates[:22]), all_staff, 9, dec_dates) == [])
check("P3b: 12月で公休9日 → 21日だと問題(決まりは22日)",
      len(checks.check_shain_days(shain_works(dec_dates[:21]), all_staff, 9, dec_dates)) == 1)
check("P3c: 11月(dates なし)は、今までどおり 30-9=21日",
      checks.check_shain_days(shain_works(checks.all_dates()[:21]), all_staff, 9) == [])
check("P3d: 2月(28日)で公休9日 → 19日出勤で問題なし",
      checks.check_shain_days(shain_works(checks.all_dates(FEB)[:19]), all_staff, 9, checks.all_dates(FEB)) == [])

# --- 4) 人数ルール(曜日つき) -----------------------------------------------------------------
def everyday_one_person(dates):
    return [{"staff_id": 1, "date": date, "start": "08:00", "end": "22:30"} for date in dates]

# 土曜だけ「9〜13時に2人以上」。バイトBは毎日 9-13 に出られる(申請あり)が、案では土曜に入れていない → 土曜(4日)だけ問題になるはず
# (そもそも出られる人が足りない日は、check_headcount は見ない決まりなので、バイトBに申請を付けておく)
staff_b_available = [staff_list[0], dict(staff_list[1], requests=[{"date": date, "start": "09:00", "end": "13:00"} for date in dec_dates])]
problems = checks.check_headcount(everyday_one_person(dec_dates) + [{"staff_id": 2, "date": date, "start": "09:00", "end": "13:00"} for date in dec_dates if date not in ("2026-12-05", "2026-12-12", "2026-12-19", "2026-12-26")],
                                  staff_b_available, [sat_rule], dec_dates)
check("P4a: 土曜だけ2人必要。土曜にだけ1人の案 → 問題は土曜の4件だけ", len(problems) == 4 and all(any(sat in problem for sat in ("12-05", "12-12", "12-19", "12-26")) for problem in problems))
check("P4b: 平日のルール(weekdays=[1..5])は、土曜の問題にならない",
      checks.check_headcount(everyday_one_person(dec_dates), all_staff, [{"start": "09:00", "end": "13:00", "min": 1, "weekdays": [1, 2, 3, 4, 5]}], dec_dates) == [])

# --- 5) 資格者ルール(曜日つき) ---------------------------------------------------------------
qual_rule = {"qualification": ["登録販売者(管理者)"], "start": "09:00", "end": "13:00", "min": 1, "weekdays": [7]}   # 日曜だけ
draft_without_qualified_on_sunday = [work for work in everyday_one_person(dec_dates) if work["date"] != "2026-12-06"]
problems = checks.check_qualification(draft_without_qualified_on_sunday, all_staff, [qual_rule], dec_dates)
check("P5a: 日曜だけ資格者が必要。12/6(日)に資格者がいない案 → その日の問題が出る", len(problems) > 0 and all("2026-12-06" in problem for problem in problems))
draft_without_qualified_on_monday = [work for work in everyday_one_person(dec_dates) if work["date"] != "2026-12-07"]
check("P5b: 月曜に資格者がいなくても、日曜ルールだから問題なし",
      checks.check_qualification(draft_without_qualified_on_monday, all_staff, [qual_rule], dec_dates) == [])

# --- 6) 「人が足りない日」(曜日つき) ----------------------------------------------------------
# バイトB は 12/5(土) に 9-13 で出られるが、社員Aは休み希望。土曜に3人必要 → 足りない。日曜ルールなら、土曜は数えない
staff_sat = [dict(staff_list[0], requests=[{"date": "2026-12-05", "off": True}]), staff_list[1]]
cases = checks.impossible_cases([dict(sat_rule, min=3)], staff_sat, dec_dates)
check("P6a: 土曜に3人必要で、最大2人未満 → 足りない日は 12/5 を含む", any(case[0] == "2026-12-05" for case in cases))
check("P6b: 土曜のルールで、土曜以外の日は『足りない日』にならない", all(case[0] in ("2026-12-05", "2026-12-12", "2026-12-19", "2026-12-26") for case in cases))

# --- 7) プロンプトの文章 ---------------------------------------------------------------------
check("P7a: 出勤日数の文章が、12月は 22日 になる", "ちょうど22日" in prompts.make_days_text(9, 31))
check("P7b: 出勤日数の文章が、days なしなら今までどおり 21日", "ちょうど21日" in prompts.make_days_text(9))
check("P7c: 曜日つきの人数ルールは、「土のみ」と書かれる", "土のみ: 09:00〜13:00" in prompts.make_headcount_text([sat_rule]))
check("P7d: 曜日なしのルールは、今までと同じ文章(「のみ」が付かない)", "のみ" not in prompts.make_headcount_text([{"start": "09:00", "end": "13:00", "min": 2}]))
check("P7e: 曜日つきの資格者ルールも、「日のみ」と書かれる", "日のみ: 09:00〜13:00" in prompts.make_qualification_text([qual_rule]))

# --- 8) グラフの validate が、State の period(12月・31日)を使う --------------------------------
settings.STORE_SETTINGS[101].update(
    shain_off_days=None, min_headcount=[],
    qualification_rules=[{"qualification": ["登録販売者(管理者)"], "start": "08:00", "end": "22:30", "min": 1}])

def validate_state(draft, period):
    state = {"store_id": 101, "llm_all_staff": all_staff, "draft_store_shift": draft, "result": "", "return_count": 1,
             "upper_limit": 3, "problems": [], "contact_asked": 0, "contact_queue": [], "contact_log": [], "contact_limit": 3}
    if period:
        state["period"] = period
    return graph_module.validate(state)

full_december = everyday_one_person(dec_dates)
check("P8a: 12月の全日に社員がいる案 → period を渡せば valid", validate_state(full_december, DEC)["result"] == "valid")
no_31st = [work for work in full_december if work["date"] != "2026-12-31"]
res = validate_state(no_31st, DEC)
check("P8b: 12/31(31日目)だけ社員がいない案 → 検査で見つかる(30日で止まらない)", res["result"] == "retry" and any("2026-12-31" in problem for problem in res["problems"]))
check("P8c: period を渡さなければ、今までどおり11月(30日)で検査する",
      validate_state(everyday_one_person(checks.all_dates()), None)["result"] == "valid")

print("ALL PASS" if not failed else f"FAILED: {failed}")
sys.exit(1 if failed else 0)