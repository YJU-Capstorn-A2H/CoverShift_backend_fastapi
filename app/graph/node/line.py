# line.py -- LINE で送る部品(スタブ)
#   ・いまは、本物は送らない。送った内容を SENT に控えるだけ(テスト・画面表示用)
#   ・本物にするときは send_line の中身だけを差し替える(呼ぶ側は変えない)
#   ・本物では、staff_id から DB で line_user_id を引いて送る。LINE の ID は State に入れない
#   ・名前は読まない決まり(基準文書 11/07)なので、メッセージにも名前を入れない
import datetime

SENT = []                                        # 送った内容の控え [{"staff_id", "text"}]
WEEKDAYS = "月火水木金土日"


# 3-6) send_line -- 1人に1通送る(スタブ)
def send_line(staff_id, text):
    SENT.append({"staff_id": staff_id, "text": text})


# 3-7) make_final_text -- 確定シフトのお知らせの本文を作る(その人の勤務だけ。日付の順)
def make_final_text(works):
    works = sorted(works, key=lambda work: (work["date"], work["start"]))
    first = datetime.date.fromisoformat(works[0]["date"])
    lines = [f"【確定シフトのお知らせ】{first.year}年{first.month}月分の勤務が確定しました。"]
    for work in works:
        day = datetime.date.fromisoformat(work["date"])
        lines.append(f"{day.month}/{day.day}({WEEKDAYS[day.weekday()]}) {work['start']}〜{work['end']}")
    return "\n".join(lines)
