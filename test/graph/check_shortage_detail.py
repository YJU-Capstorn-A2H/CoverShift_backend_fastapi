# check_shortage_detail.py -- 本物の DB を「読むだけ」で、人が足りない時間帯を、時間帯ごとに数える(試験用。あとで消す)
#   ・書き込みはしない。名前は表示しない
# 実行: test\graph フォルダで  python check_shortage_detail.py
import os, sys, pathlib, collections
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "app" / "graph"))
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
import psycopg
import settings
import node.checks as checks
import node.db as db

with psycopg.connect(settings.DB_URL) as conn:
    conn.read_only = True
    period_ids = [row[0] for row in conn.execute("SELECT period_id FROM shift_periods ORDER BY period_id")]
    rules = conn.execute("SELECT weekday, start_time, end_time, min_headcount, min_qualified FROM store_time_rules ORDER BY weekday, start_time").fetchall()
print("store_time_rules(曜日 開始 終了 人数 資格者):")
for rule in rules:
    print("  ", rule[0], str(rule[1])[:5], str(rule[2])[:5], rule[3], rule[4])

for period_id in period_ids:
    data = db.load_period_data(settings.DB_URL, period_id)
    staff_list = data["staff_list"]
    lines = checks.make_shortage_report(data["store_settings"]["min_headcount"], staff_list, checks.all_dates(data["period"]))
    lines = [line for line in lines if "：" in line]
    count = collections.Counter(line.split(" ", 1)[1].split("：")[0] for line in lines)
    print(f"\nperiod_id={period_id}: 人が足りない(理論上) {len(lines)}件")
    for slot, days in sorted(count.items()):
        print(f"  {slot} … {days}日")
    for line in lines[:3]:
        print("   例:", line)
print("\n読み込み完了(何も書き込んでいません)")
