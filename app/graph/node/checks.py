# 3-1) node/checks.py -- ルールの検査と、計算の道具 (LangGraph も State も使わない、ふつうの関数だけ)
#   check_* は「問題の文章のリスト」を返す。空のリストなら問題なし(validate がまとめて呼ぶ)
import datetime, re
from settings import PERIOD


# 道具(時間の変換・日付の一覧・希望の検索)
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


# 各ルールの検査(validate が呼ぶ)
def check_coverage(assignments, all_staff):           # どの30分にも社員が1人以上いるか(開店・閉店作業のため)
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


def check_qualification(assignments, all_staff, rules):       # 資格者が足りているか(承認を止める検査)
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
            if "start" in request:                          # 出たい日だけ(off の日は入れない)
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


def check_double_booking(assignments):  # 同じ人の勤務が、同じ日に重なっていないか(承認を止める検査)
    works_by_person_and_date = {}       # (スタッフID, 日付) -> その日の勤務の一覧
    for work in assignments:
        works_by_person_and_date.setdefault((work["staff_id"], work["date"]), []).append(work)

    problems = []
    for (staff_id, date), works in works_by_person_and_date.items():
        works = sorted(works, key=lambda work: to_min(work["start"]))   # 始まりが早い順
        for earlier, later in zip(works, works[1:]):                    # となりどうしを比べる
            if to_min(later["start"]) < to_min(earlier["end"]):         # 次が前の終わりより先に始まる = 重なり
                problems.append(f"{date} ID{staff_id}の勤務が重なっています({earlier['start']}〜{earlier['end']} と {later['start']}〜{later['end']})")
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
                dates.append(work["date"])                                             # 同じ日は1回だけ数える

        for request in staff["requests"]:
            if request.get("paid_leave") is True and request["date"] not in dates:     # 1日まるごと有給の日も、出勤日数に数える
                dates.append(request["date"])
        
        if len(dates) != work_days:
            problems.append(f"ID{staff['staff_id']}の出勤日数が{len(dates)}日です(決まりは{work_days}日)")
    return problems


# --- 人が足りない日の判定(希望から見て、そもそも足りない日を見つける) ---
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


# 連絡する候補の順番を作る(validate が、人が足りない日の連絡先を決めるときに使う)
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