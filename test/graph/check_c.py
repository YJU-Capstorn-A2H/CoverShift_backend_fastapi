import json, sys, pathlib
from settings import llm_all_staff
from node.checks import find_request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "app" / "graph"))
draft = json.load(open("month_draft.json", encoding="utf-8"))["assignments"]
d = "2026-11-11"

for s in llm_all_staff:
    req = find_request(s, d)
    mine = [(a["start"], a["end"]) for a in draft if a["staff_id"] == s["staff_id"] and a["date"] == d]
    days = len({a["date"] for a in draft if a["staff_id"] == s["staff_id"]})
    print("ID", s["staff_id"], s["employment"], s.get("qualification"), "希望:", req, "入っている時間:", mine, "月の出勤日数:", days)