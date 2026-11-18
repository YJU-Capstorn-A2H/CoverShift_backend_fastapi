# check_real_db.py -- 本物の DB(.env の DATABASE_URL)を「読むだけ」で、B1 の読み込みが動くか確かめる
#   ・書き込みはしない。名前は表示しない(人数と件数だけ)
#   ・全部の期間(shift_periods)について、読み込みの結果をまとめて出す
# 実行: test\graph フォルダで  python check_real_db.py
import os, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "app" / "graph"))
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")      # settings が読むだけ(ここでは LLM を呼ばない)

# import はここにまとめる(上の「app/graph を探す設定」のあとでないと動かない)
import collections
import psycopg
import settings
import node.checks as checks
import node.db as db

with psycopg.connect(settings.DB_URL) as conn:
    conn.read_only = True
    period_ids = [row[0] for row in conn.execute("SELECT period_id FROM shift_periods ORDER BY period_id")]
print(f"shift_periods: {len(period_ids)} 件 {period_ids}")

for period_id in period_ids:
    data = db.load_period_data(settings.DB_URL, period_id)
    staff_list = data["staff_list"]
    kinds = collections.Counter()
    for staff in staff_list:
        for request in staff["requests"]:
            kinds["off" if request.get("off") else "paid_leave" if "paid_leave" in request else "available"] += 1
    print(f"\n--- period_id={period_id}: store_id={data['store_id']} / {data['period']['start']} から {data['period']['days']}日")
    print(f"  社員 {sum(s['employment'] == '社員' for s in staff_list)}人 / バイト {sum(s['employment'] == 'バイト' for s in staff_list)}人 / 資格者 {sum(s['qualification'] is not None for s in staff_list)}人 / closing_ok {sum(s['closing_ok'] for s in staff_list)}人")
    print(f"  希望の件数: {dict(kinds)}(出られる=available / 希望休=off / 有給・半休=paid_leave)")
    print(f"  公休日数: {data['store_settings']['shain_off_days']}")
    print(f"  人数ルール {len(data['store_settings']['min_headcount'])}件 / 資格者ルール {len(data['store_settings']['qualification_rules'])}件")
    for rule in data["store_settings"]["min_headcount"]:
        print(f"    人数: {rule.get('weekdays', '毎日')} {rule['start']}〜{rule['end']} {rule['min']}人以上")
    for rule in data["store_settings"]["qualification_rules"]:
        print(f"    資格者: {rule.get('weekdays', '毎日')} {rule['start']}〜{rule['end']} {rule['min']}人以上")
    shortage = checks.make_shortage_report(data["store_settings"]["min_headcount"], [dict(s, requests=s["requests"]) for s in staff_list], checks.all_dates(data["period"]))
    print(f"  理論上、人数が足りない時間帯: {len([line for line in shortage if '：' in line])}件")
print("\n読み込み完了(何も書き込んでいません)")
