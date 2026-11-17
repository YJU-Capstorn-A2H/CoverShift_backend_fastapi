# node/convert.py -- DB の行(v18) → グラフが使うデータ に変える関数だけを書くファイル
#   DB にはつながない・settings も読まない(渡された行を変えるだけ)。DB を読むのは B1-2 で別に作る
#   行は「列名 → 値」の辞書(psycopg の dict_row)。時刻は datetime.time でも "HH:MM:SS" の文字列でもよい
import calendar
import datetime


QUALIFICATION_NAME = "登録販売者(管理者)"      # has_qualification が true の人に付ける資格名(qualification_rules の名前と同じにする)
ALL_WEEKDAYS = [1, 2, 3, 4, 5, 6, 7]           # 1=月 ... 7=日 (DB の weekday と同じ)


def to_hhmm(value):               # 時刻を "09:00" の形にする
    if isinstance(value, str):
        value = datetime.time.fromisoformat(value)
    return value.strftime("%H:%M")


def to_date(value):               # 日付を datetime.date にする("2026-12-01" の文字列でもよい)
    if isinstance(value, str):
        return datetime.date.fromisoformat(value)
    if isinstance(value, datetime.datetime):
        return value.date()
    return value


def make_period(target_month):    # shift_periods.target_month → State の period {"start": "2026-12-01", "days": 31}
    month = to_date(target_month)
    if month.day != 1:
        raise ValueError(f"target_month は1日のはずです: {month}")
    return {"start": month.isoformat(), "days": calendar.monthrange(month.year, month.month)[1]}


def make_request(row):            # availability_submissions の1行 → requests の1件
    date = to_date(row["work_date"]).isoformat()
    kind = row["kind"]
    if kind == "available":       # 出られる(時間つき)
        return {"date": date, "start": to_hhmm(row["start_time"]), "end": to_hhmm(row["end_time"])}
    if kind == "off":             # 希望休
        return {"date": date, "off": True}
    if kind == "paid_leave":      # 1日まるごと有給
        return {"date": date, "paid_leave": True}
    if kind == "half_leave":      # 半休(時間つき)
        return {"date": date, "paid_leave": "half", "start": to_hhmm(row["start_time"]), "end": to_hhmm(row["end_time"])}
    raise ValueError(f"知らない kind です: {kind}")


def make_staff_list(employee_rows, submission_rows):
    """employees と availability_submissions の行 → スタッフのリスト(名前つき。staff_data.json と同じ形)
    - employment: is_regular が true なら「社員」、false なら「バイト」(DB にパートとバイトの区別は無い。コードは同じ扱い)
    - qualification: has_qualification が true なら QUALIFICATION_NAME、false なら None
    - staff_skill / admin_rate: DB に無いので、どちらも None(同じ値 = スキルチェックは起きない)
    - 希望(requests)は日付順。知らない employee_id の行があればエラー(店を間違えた可能性)"""
    requests_by_id = {row["employee_id"]: [] for row in employee_rows}
    for row in sorted(submission_rows, key=lambda row: to_date(row["work_date"])):
        if row["employee_id"] not in requests_by_id:
            raise ValueError(f"従業員一覧にない employee_id の希望があります: {row['employee_id']}")
        requests_by_id[row["employee_id"]].append(make_request(row))

    staff_list = []
    for row in sorted(employee_rows, key=lambda row: row["employee_id"]):
        staff_list.append({
            "staff_id": row["employee_id"],
            "staff_name": row["name"],
            "employment": "社員" if row["is_regular"] else "バイト",
            "qualification": QUALIFICATION_NAME if row["has_qualification"] else None,
            "staff_skill": None,
            "admin_rate": None,
            "closing_ok": bool(row["closing_ok"]),
            "requests": requests_by_id[row["employee_id"]],
        })
    return staff_list


def split_names(staff_list):      # 名前つきのリスト → (ID→名前の辞書, 名前を消した LLM 用リスト)
    id_to_name = {}
    llm_all_staff = []
    for staff in staff_list:
        id_to_name[staff["staff_id"]] = staff["staff_name"]
        llm_all_staff.append({key: value for key, value in staff.items() if key != "staff_name"})
    return id_to_name, llm_all_staff


def group_rules(time_rule_rows, count_column):
    """同じ(開始・終了・人数)の曜日をまとめる。7日ぜんぶなら "weekdays" は付けない(毎日)。人数が0の行は捨てる"""
    groups = {}
    for row in time_rule_rows:
        count = row[count_column]
        if count <= 0:
            continue
        key = (to_hhmm(row["start_time"]), to_hhmm(row["end_time"]), count)
        groups.setdefault(key, set()).add(row["weekday"])
    rules = []
    for (start, end, count), weekdays in sorted(groups.items(), key=lambda item: (item[0], sorted(item[1]))):
        rule = {"start": start, "end": end, "min": count}
        if sorted(weekdays) != ALL_WEEKDAYS:
            rule["weekdays"] = sorted(weekdays)
        rules.append(rule)
    return rules


def make_store_settings(time_rule_rows, period_row):
    """store_time_rules と shift_periods の行 → STORE_SETTINGS の1店舗分(使う3項目だけ)
    - min_headcount: min_headcount 列から(休憩込みの必要人数)
    - qualification_rules: min_qualified 列から(0の行は無し)
    - shain_off_days: designated_off_days 列から"""
    qualification_rules = []
    for rule in group_rules(time_rule_rows, "min_qualified"):
        qualification_rules.append({"qualification": [QUALIFICATION_NAME], **rule})
    return {
        "min_headcount": group_rules(time_rule_rows, "min_headcount"),
        "qualification_rules": qualification_rules,
        "shain_off_days": period_row["designated_off_days"],
    }