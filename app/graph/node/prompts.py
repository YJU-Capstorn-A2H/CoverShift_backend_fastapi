# 3-3) node/prompts.py -- LLM に渡す文章(プロンプト)だけを書くファイル。ルールの検査は node/checks.py
import json


# 【LLM】AIが編成案を作る(auto_draft が使う)
# rules: LLMが編成案を作る際のプロンプト
rules = """あなたは日本のシフト形式を熟知したシフト編成の補助です。
提案だけを行い、最終確定は人(店長)が行います。

【requests の読み方】
- start と end がある: その時間で出たい(社員の場合は、その時間どおりに入れる)。
- off: 休みたい日。(社員の希望休は最大3回。リフレッシュ休暇の場合は5連休まで。)
- paid_leave: true: 1日まるごと有給。その日は店に出ない。
- paid_leave: half(start と end つき): 半休。その時間どおりに入れる。
- 何も書いていない日: パート・バイトは入れない。社員は、あなたが決めてよい。

【必ず守るルール】
1. 営業時間のあいだ、どの30分の区切りにも社員が1人以上いること。開店作業と閉店作業は、この社員が行う。
2. パート・バイトは、start のある申請の日だけ、申請の時間の中で入れること。申請のない日と off の日には入れない。
3. パート・バイトは22:00まで。22:30までは closing_ok が true の人だけ。
4. 時間は30分単位(例: 08:00, 13:30)で書くこと。
5. 出勤の時間は、営業時間のどこから始めてもよい(10時や11時の出勤も可)。

【守れないとき】
必ず守るルール同士が両立しない日は、次の順で、下のほうから諦めること。
  1. 資格者の配置(上ほど守る)
  2. 社員の出勤日数を守る
  3. 社員の希望休と有給・半休
  4. 時間帯ごとの最低人数
  5. パート・バイトの申請を、できるだけ多く入れる
  6. パート・バイトの出勤回数の差を小さくする
諦めた所は、必ず shortages に書くこと。

【できるだけ守る目標】
- パート・バイトは、店の人手が足りる範囲で、申請をできるだけ多く入れる。そのうえで、出勤回数の差は小さくする(目安は3日差まで)。
- 毎日の出勤人数、時間帯ごとの人数は、できるだけ均等にする。
- 社員の希望休(off)は、できるだけ通す。最大3回。通さなかった場合は notes に理由を書くこと。

【出力形式】
次のJSONだけを出力すること。説明文は書かない。
{"assignments": [{"staff_id": 1, "date": "2026-11-02", "start": "08:00", "end": "17:00"}],
 "shortages": [{"date": "2026-11-20", "start": "08:00", "end": "09:00", "reason": "出られる人が足りない"}],
 "notes": ["..."]}
"""


# 【コード】各ルールを、LLM に渡す文章にする (make_*_text)
#   ルールが空(None や [])のときは、何も足さない = そのルールはスイッチOFF
def make_qualification_text(qual_rules):     # 資格ルールの文章を作る
    if not qual_rules:                       # [] のときは、文を作らない(スイッチOFF)
        return ""
    
    text = "\n【資格のルール(必ず守る)】\n"
    for rule in qual_rules:
        names = "、".join(rule["qualification"])
        text += f"- {rule['start']}〜{rule['end']}は、qualification が「{names}」のどれかの人が、どの30分の区切りにも{rule['min']}人以上いること。\n"
    
    return text


def make_days_text(off_days):                # 社員の出勤日数の文章を作る
    if off_days is None:                     # None のときは、文を作らない(スイッチOFF)
        return ""
    
    work_days = 30 - off_days
    return (f"\n【社員の出勤日数(必ず守る)】\n"
            f"- 社員(employment が 社員 の人)は、1か月の公休(休み)が{off_days}日です。出勤は、ちょうど{work_days}日にすること。\n"
            f"- requests に off と書かれた日は、公休にあててよい。\n"
            f"- requests に paid_leave: true と書かれた日は、1日まるごと有給で、その日は店に出ない。ただし、出勤日数には1日として数える(例: 有給が2日なら、実際に出る日は{work_days - 2}日)。\n"
            f"- requests に paid_leave: half と書かれた日は、半休。start と end の時間どおりに入れる。この日も、出勤日数に1日として数える。\n"
            f"- 有給と半休の日は、できるだけ守る。守れなかったときは、notes に理由を書くこと。\n"
            f"- 出力する前に、社員1人ずつ、出勤した日と有給の日を合わせて数えて、{work_days}日であることを確かめること。\n")


def make_problem_text(problems):                      # 問題の一覧を、LLM に渡す文にする関数
    if not problems:                                  # 問題がないとき(1回目)は、文を作らない
        return ""
    
    text = "\n【前回の案の問題(今回は必ず直すこと)】\n"
    for problem in problems[:20]:                           # 長くなりすぎないよう、20件まで
        text += f"- {problem}\n"

    if len(problems) > 20:
        text += f"- (ほか{len(problems) - 20}件)\n"
    return text


def make_headcount_text(rules):              # 時間帯ごとの最低人数の文章を作る
    if not rules:                            # [] のときは、文を作らない(スイッチOFF)
        return ""
    
    text = "\n【時間帯ごとの最低人数(必ず守る)】\n"
    for rule in rules:
        text += f"- {rule['start']}〜{rule['end']}は、同時にいる人数を{rule['min']}人以上にすること。\n"
        
        if rule.get("exclude"):
            text += f"  (qualification が{'・'.join(rule['exclude'])}の人は、この時間帯の人数に数えない)\n"
        
        if rule.get("want"):
            text += f"  できれば{rule['want']}人にする。\n"
    
    text += "- 出られる人が足りなくて、どうしても守れない日は、守れなくてよい。その日は notes に書くこと。\n"
    return text


def make_instruction_text(instruction):          # 店長の指示を、LLMに渡す文にする
    if not instruction:
        return ""
    return f"\n【店長からの指示(今回は必ず反映すること)】\n- {instruction}\n"


def make_prev_draft_text(draft):                 # 前回の案を、土台としてLLMに渡す
    if not draft:                                # 1回目は空なので、何も足さない
        return ""
    return ("\n【前回の案(これを土台にして、問題の日と店長の指示だけを直すこと。ほかの日は変えない)】\n"
            + json.dumps(draft, ensure_ascii=False) + "\n")