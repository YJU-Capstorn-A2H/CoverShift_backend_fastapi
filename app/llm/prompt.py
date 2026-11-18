"""프롬프트 만들기 (순수 함수 — 테스트로 문장을 확인한다).

재료와 근거
- LLM 입력 약속 (V9_ko 483): 기간・영업시간 + 고정 규칙 문 + 설정에서 만드는 문(자격・사원 일수・최소 인원)
  + 확인된 틀 규칙 + 직원 데이터(실명 없이 ID만, F-6).
- 문장 내용은 아즈키 시제품(spec/azuki_prototype_mcp_1107.py 236-347 rules · make_*_text)을 그대로 쓰고,
  11/08 mix1_55(유일하게 통과한 측정 구성, experiments/2026-11-07_azuki_mix/run.py base_prompt_parts)의 변경만 더했다:
  ① 최소 인원은 「できるだけ守る目標。検査では警告だけ」 ② 「出力する前に…数えて確かめること」 삭제
  ③ 「길 주기」(【進め方】: 먼저 내라 · 검사는 코드 · 못 채우면 shortages) ④ 출력 형식 문단은 구조화 출력이 맡는다.
- 재시도 입력 (F-5, V9_ko 486): 직전 안 + 이력 전부(같은 위반 묶음 + 회차) + 「違反のセル以外は変えないこと」.
- 위반 문장은 check_code + detail 에서 코드로 만든다 — 같은 위반은 같은 문장 (V9_ko 412-415).

캐시: system 블록(규칙 · 설정 · 틀 규칙 — 한 편성 안에서 바뀌지 않음, F-17)과 직원 데이터 블록에 cache_control.
직전 안 · 위반 · 지시문(매번 바뀜)은 그 뒤에 둔다.
"""
from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence

from app.domain import (
    Assignment,
    MaxConsecutiveParams,
    MinHeadcountParams,
    NotAvailableParams,
    PeriodContext,
    TemplateRule,
    Violation,
    ViolationGroup,
)
from app.llm.hours import WEEKDAY_JA, business_hours, fmt_weekdays, hhmm

CACHE = {"type": "ephemeral"}

# 재시도 프롬프트의 고정 문장 (F-5). 테스트가 이 문자열 그대로를 찾는다.
KEEP_OTHER_CELLS = "違反のセル以外は変えないこと。"

# 재시도 이력에 경고(warning)도 넣을지. (가정 신규) mix1 측정처럼 「반드시」위반만 넘긴다 — 경고까지 넘기면
# 필수 위반을 고치는 횟수를 잡아먹을 우려(V9_ko 116 주의 「余り」 메모와 같은 이유). True 로 바꾸면 경고도 넘긴다.
INCLUDE_WARNINGS_IN_RETRY = False


def _j(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _t(t) -> str:
    return t.strftime("%H:%M")


# ---------------------------------------------------------------- system: 기간 · 영업시간

def make_period_text(ctx: PeriodContext) -> str:
    dates = ctx.period.dates()
    bh = business_hours(ctx)
    groups: dict[tuple[int, int], set[int]] = defaultdict(set)
    for wd, oc in bh.items():
        groups[oc].add(wd)
    lines = [f"\n【対象期間】\n- {dates[0].isoformat()}〜{dates[-1].isoformat()}({len(dates)}日)\n", "\n【営業時間】\n"]
    for (o, c), wds in sorted(groups.items(), key=lambda kv: min(kv[1])):
        lines.append(f"- {fmt_weekdays(wds)}: {hhmm(o)}〜{hhmm(c)}(閉店時刻は{hhmm(c)})\n")
    closed = [w for w in range(1, 8) if w not in bh]
    if closed:
        lines.append(f"- {fmt_weekdays(set(closed))}: 休業(誰も入れない)\n")
    lines.append("- 勤務は営業時間の中だけに入れる。\n")
    lines.append("\n【カレンダー】\n" + ", ".join(f"{d.isoformat()}({WEEKDAY_JA[d.isoweekday()]})" for d in dates) + "\n")
    return "".join(lines)


# ---------------------------------------------------------------- system: 고정 규칙 (아즈키 rules 를 MVP 데이터 모양에 맞춤)

INTRO = (
    "あなたは日本のシフト形式を熟知したシフト編成の補助です。\n"
    "提案だけを行い、最終確定は人(店長)が行います。\n"
    "スタッフは ID(staff_id)だけで書かれています。\n"
)

REQUESTS_TEXT = (
    "\n【requests の読み方】\n"
    "- start と end がある: その時間で出たい(社員の場合は、その時間どおりに入れる)。\n"
    "- off: 休みたい日。\n"
    "- paid_leave: true: 1日まるごと有給。その日は店に出ない。\n"
    "- paid_leave: half(start と end つき): 半休。その時間どおりに入れる。\n"
    "- 何も書いていない日: パート・バイトは入れない。社員は、あなたが決めてよい。\n"
    "- employment は「社員」か「パート・バイト」。qualified が true の人は資格者。\n"
    "- weekly_days・fixed_weekdays があれば、週の出勤日数・曜日の目安(参考。検査はしない)。\n"
)

FIXED_RULES_TEXT = (
    "\n【必ず守るルール】\n"
    "1. 営業時間のあいだ、どの30分の区切りにも社員が1人以上いること。開店作業と閉店作業は、この社員が行う。\n"
    "2. パート・バイトは、start のある申請の日だけ、申請の時間の中で入れること。申請のない日と off の日には入れない。\n"
    "3. その日の閉店時刻まで残れる(end が閉店時刻になる)のは closing_ok が true の人だけ。"
    "closing_ok が false の人は、閉店時刻の30分前までに終えること。\n"
    "4. 時間は30分単位(例: 08:00, 13:30)で書くこと。\n"
    "5. 出勤の時間は、営業時間のどこから始めてもよい(10時や11時の出勤も可)。\n"
    "6. 同じ人は1日に1つの勤務(1行)だけ。同じ人の時間を重ねない。\n"
)

# F-12 포기 순서 (V9_ko 485, 아즈키 rules 【守れないとき】 그대로)
GIVE_UP_ORDER = (
    "資格者の配置(上ほど守る)",
    "社員の出勤日数を守る",
    "社員の希望休と有給・半休",
    "時間帯ごとの最低人数",
    "パート・バイトの申請を、できるだけ多く入れる",
    "パート・バイトの出勤回数の差を小さくする",
)


def make_give_up_text() -> str:
    lines = "".join(f"  {i}. {s}\n" for i, s in enumerate(GIVE_UP_ORDER, 1))
    return ("\n【守れないとき】\n"
            "必ず守るルール同士が両立しない日は、次の順で、下のほうから諦めること。\n"
            + lines + "諦めた所は、必ず shortages に書くこと。\n")


GOALS_TEXT = (
    "\n【できるだけ守る目標】\n"
    "- パート・バイトは、店の人手が足りる範囲で、申請をできるだけ多く入れる。そのうえで、出勤回数の差は小さくする(目安は3日差まで)。\n"
    "- 毎日の出勤人数、時間帯ごとの人数は、できるだけ均等にする。\n"
    "- 社員の希望休(off)は、できるだけ通す。通さなかった場合は notes に理由を書くこと。\n"
)

OUTPUT_TEXT = (
    "\n【出力の中身】\n"
    "- assignments: 出勤の行だけ。1人1日1行(staff_id・date・start・end)。"
    "有給(paid_leave: true)の日は書かない(コードが申請から入れる)。半休の日は申請の時間で書く。\n"
    "- shortages: 埋められなかった時間帯(date・start・end・need=必要な人数・have=入れられた人数・reason)。店長に参考として見せる。\n"
    "- notes: 店長への短いメモ(通さなかった希望休の理由など)。\n"
)

# mix1 の「길 주기」 (experiments/2026-11-07_azuki_mix/run.py base_prompt_parts の way 그대로)
WAY_TEXT = (
    "\n【進め方】\n"
    "- まず案を出すこと。完璧に合わせようと長く考えない。検査はコードが行い、見つかった問題は次の回に伝える。\n"
    "- 埋められない所(人数・資格者が足りない時間帯)は、無理に埋めず shortages に書くこと。\n"
    "- 出力は指定の JSON の形だけ。説明文・下書きは書かない。\n"
)


# ---------------------------------------------------------------- system: 설정에서 만드는 문

def _group_rules(ctx: PeriodContext, value) -> list[tuple[str, str, int, set[int]]]:
    """(start, end, 값) 이 같은 시간대 규칙을 요일끼리 묶는다. 값 0 은 뺀다.
    같은 요일에서 이어진 시간대(앞 end == 뒤 start)의 값이 같으면 한 줄로 합친다 (문장을 짧게 — 뜻은 같음)."""
    spans: list[tuple[int, int, int, int]] = []  # (weekday, start, end, value)
    by_wd: dict[int, list] = defaultdict(list)
    for r in ctx.time_rules:
        by_wd[r.weekday].append(r)
    for wd, rs in by_wd.items():
        for r in sorted(rs, key=lambda x: x.start_time):
            v = value(r) or 0
            s, e = r.start_time.hour * 60 + r.start_time.minute, r.end_time.hour * 60 + r.end_time.minute
            if spans and spans[-1][0] == wd and spans[-1][2] == s and spans[-1][3] == v:
                spans[-1] = (wd, spans[-1][1], e, v)
            else:
                spans.append((wd, s, e, v))
    g: dict[tuple[str, str, int], set[int]] = defaultdict(set)
    for wd, s, e, v in spans:
        if v > 0:
            g[(hhmm(s), hhmm(e), v)].add(wd)
    return sorted(((s, e, v, w) for (s, e, v), w in g.items()), key=lambda x: (x[0], x[1], min(x[3]), x[2]))


def make_qualification_text(ctx: PeriodContext) -> str:
    """V-2 (V9_ko 469). min_qualified 그대로, 0 이면 문장 없음 (G10)."""
    rows = _group_rules(ctx, lambda r: r.min_qualified)
    if not rows:
        return ""
    text = "\n【資格のルール(必ず守る)】\n"
    for s, e, n, wds in rows:
        text += f"- {fmt_weekdays(wds)} {s}〜{e}は、qualified が true の人が、どの30分の区切りにも{n}人以上いること。\n"
    return text


def make_days_text(ctx: PeriodContext) -> str:
    """V-5 (V9_ko 472). 아즈키 make_days_text 에서 「出力する前に…」 줄만 뺀 것 (mix1)."""
    n_days = len(ctx.period.dates())
    off_days = ctx.period.designated_off_days
    work_days = n_days - off_days
    if not any(e.is_regular for e in ctx.employees):
        return ""
    return (f"\n【社員の出勤日数(必ず守る)】\n"
            f"- 社員(employment が 社員 の人)は、期間({n_days}日)の公休(休み)が{off_days}日です。出勤は、ちょうど{work_days}日にすること。\n"
            f"- requests に off と書かれた日は、公休にあててよい。\n"
            f"- requests に paid_leave: true と書かれた日は、1日まるごと有給で、その日は店に出ない。ただし、出勤日数には1日として数える"
            f"(例: 有給が2日なら、実際に出る日は{work_days - 2}日)。\n"
            f"- requests に paid_leave: half と書かれた日は、半休。start と end の時間どおりに入れる。この日も、出勤日数に1日として数える。\n"
            f"- 有給と半休の日は、できるだけ守る。守れなかったときは、notes に理由を書くこと。\n")


def make_headcount_text(ctx: PeriodContext) -> str:
    """V-9 (V9_ko 476, 514). 설정값 = 경고. 지시 틀(min_headcount)이 있는 칸은 그 숫자가 우선이고 반드시 (G12)."""
    rows = _group_rules(ctx, lambda r: r.min_headcount)
    if not rows:
        return ""
    text = "\n【時間帯ごとの最低人数(できるだけ守る目標。検査では警告だけ)】\n"
    for s, e, n, wds in rows:
        text += f"- {fmt_weekdays(wds)} {s}〜{e}は、同時にいる人数を{n}人以上にすること。\n"
    text += "- 出られる人が足りない時間帯は、無理に合わせず shortages に書くこと。\n"
    if any(r.template_key == "min_headcount" for r in ctx.template_rules):
        text += "- ただし【店長の指示】に人数がある時間帯は、指示の人数が優先で、必ず守る。\n"
    if ctx.options.enable_surplus:  # G3 — 기본 꺼 둠. 켜면 余り 가 「반드시」 위반이 된다 (V9_ko 477)
        text += "- 各30分の区切りの人数は、最低人数(店長の指示があればその人数)を超えないこと(余りは検査で問題になる)。\n"
    return text


# ---------------------------------------------------------------- system: 틀 규칙 · 자유문

def template_rule_text(rule: TemplateRule) -> str:
    """틀 규칙 1개 → 문장. 점장 원문(source_text)은 넣지 않는다 — 원문에 실명이 있을 수 있어서 (F-6, 가정 신규)."""
    p = rule.params
    if isinstance(p, NotAvailableParams):
        when = f"{WEEKDAY_JA[p.weekday]}曜日" if p.weekday is not None else p.date.isoformat()
        return f"ID{p.staff_id}は{when}に入れない"
    if isinstance(p, MaxConsecutiveParams):
        who = f"ID{p.staff_id}" if p.staff_id is not None else "全員"
        if p.shift == "late":
            return f"{who}は、遅番(その日の閉店時刻に終わる勤務)の連続を{p.limit}回までにする"
        return f"{who}は、連続出勤を{p.limit}日までにする"
    if isinstance(p, MinHeadcountParams):
        if p.date is not None:
            when = p.date.isoformat()
        elif p.weekday is not None:
            when = f"{WEEKDAY_JA[p.weekday]}曜日"
        else:
            when = "毎日"
        return f"{when} {_t(p.start)}〜{_t(p.end)}は、同時にいる人数を{p.count}人以上にする(設定の最低人数より優先)"
    return f"{rule.template_key} {p.model_dump_json()}"


def make_template_rules_text(ctx: PeriodContext) -> str:
    """확인된 틀 규칙 = 코드가 검사하고 보장 (V9_ko 478)."""
    if not ctx.template_rules:
        return ""
    text = "\n【店長の指示(必ず守る。コードが検査する)】\n"
    for r in ctx.template_rules:
        text += f"- [{r.rule_key}] {template_rule_text(r)}。\n"
    return text


def make_free_text(ctx: PeriodContext) -> str:
    """틀 없는 말 = 문장 그대로 + 「참고・보장 없음」 (V9_ko 478, T-4)."""
    items = [t for t in ctx.free_text_guidelines if t and t.strip()]
    if not items:
        return ""
    text = "\n【店長のメモ(参考・保証なし。コードは検査しない。できる範囲で考える)】\n"
    for t in items:
        text += f"- {t.strip()}\n"
    return text


def build_system_text(ctx: PeriodContext) -> str:
    return (INTRO + make_period_text(ctx) + REQUESTS_TEXT + FIXED_RULES_TEXT + make_qualification_text(ctx)
            + make_days_text(ctx) + make_headcount_text(ctx) + make_template_rules_text(ctx) + make_free_text(ctx)
            + make_give_up_text() + GOALS_TEXT + OUTPUT_TEXT + WAY_TEXT)


# ---------------------------------------------------------------- user: 직원 데이터 (ID 만)

def staff_payload(ctx: PeriodContext) -> list[dict]:
    """직원 데이터 (F-6: Employee 에는 이름이 없다). requests 는 아즈키 staff_data 모양으로 바꾼다 (측정한 프롬프트와 같게)."""
    by_emp: dict[int, list] = defaultdict(list)
    for a in ctx.availability:
        by_emp[a.employee_id].append(a)
    out = []
    for e in sorted(ctx.employees, key=lambda x: x.employee_id):
        reqs = []
        for a in sorted(by_emp.get(e.employee_id, []), key=lambda x: x.work_date):
            r: dict = {"date": a.work_date.isoformat()}
            if a.kind == "available":
                if a.start_time is None or a.end_time is None:
                    continue  # 시각 없는 available 은 A-12 에서 막힘. 들어오면 「희망 없음」과 같게 둔다
                r.update(start=_t(a.start_time), end=_t(a.end_time))
            elif a.kind == "off":
                r["off"] = True
            elif a.kind == "paid_leave":
                r["paid_leave"] = True
            elif a.kind == "half_leave":
                r["paid_leave"] = "half"
                if a.start_time is not None and a.end_time is not None:
                    r.update(start=_t(a.start_time), end=_t(a.end_time))
            reqs.append(r)
        item: dict = {"staff_id": e.employee_id, "employment": "社員" if e.is_regular else "パート・バイト",
                      "qualified": e.has_qualification, "closing_ok": e.closing_ok}
        if e.weekly_days is not None:
            item["weekly_days"] = e.weekly_days
        if e.fixed_weekdays:
            item["fixed_weekdays"] = [WEEKDAY_JA[w] for w in e.fixed_weekdays]
        item["requests"] = reqs
        out.append(item)
    return out


def make_staff_text(ctx: PeriodContext) -> str:
    return "【スタッフのデータ(JSON)】\n" + _j(staff_payload(ctx))


# ---------------------------------------------------------------- user: 재시도 (F-5)

def _detail_value(v: object) -> str:
    return str(v) if isinstance(v, (int, float, str)) else json.dumps(v, ensure_ascii=False, sort_keys=True)


_CODE_TEXT = {
    "REGULAR_ABSENT": "社員が1人もいない",
    "QUAL_MISSING": "資格者(qualified)が足りない",
    "OUTSIDE_AVAILABILITY": "申請の日・申請の時間の外に入っている",
    "CLOSING_NOT_ALLOWED": "closing_ok が false なのに閉店時刻まで入っている",
    "REGULAR_DAYS_MISMATCH": "社員の出勤日数が決まりの日数と違う",
    "HALF_LEAVE_MISMATCH": "半休の日が申請の時間どおりになっていない",
    "DOUBLE_BOOKING": "同じ人が同じ日に2つ以上入っている(時間の重なり)",
    "HEADCOUNT_SHORTAGE": "同時にいる人数が足りない",
    "HEADCOUNT_EXCESS": "人数が多すぎる(余り)",
    "RULE_VIOLATION": "店長の指示に反している",
}
# detail 키 → 사람이 읽는 말. app/validate(rules.py · templates.py, 11/12 시점)가 넣는 키에 맞췄다.
# 모르는 키는 「key=value」로 이름 순서대로 붙인다 (같은 값이면 같은 문장).
_KIND_JA = {"available": "出勤希望", "off": "休み", "paid_leave": "有給", "half_leave": "半休", None: "申請なし"}
_REASON_JA = {"no_submission": "申請なし", "off": "休みの日", "paid_leave": "有給の日", "outside_time": "申請の時間の外",
              "missing": "勤務が入っていない", "mismatch": "時間が違う"}
_CONSUMED = {"time", "start", "end", "need", "have", "rule_key", "kind", "assigned_start", "assigned_end", "avail_kind",
             "avail_start", "avail_end", "reason", "closing", "work_days", "paid_leave_days", "count", "overlap", "want",
             "excess", "run_start", "run_end", "run_length", "limit", "shift", "source", "template_key"}


def _detail_extras(d: dict) -> list[str]:
    ex: list[str] = []
    if "need" in d and "have" in d:
        ex.append(f"必要{_detail_value(d['need'])}・実際{_detail_value(d['have'])}")
    if d.get("assigned_start") and d.get("assigned_end"):
        ex.append(f"入れた時間 {d['assigned_start']}〜{d['assigned_end']}")
    if d.get("avail_start") and d.get("avail_end"):
        kind = _KIND_JA.get(d.get("avail_kind"), str(d.get("avail_kind"))) + " " if "avail_kind" in d else ""
        ex.append(f"申請 {kind}{d['avail_start']}〜{d['avail_end']}")
    elif "avail_kind" in d:
        ex.append(f"申請 {_KIND_JA.get(d['avail_kind'], str(d['avail_kind']))}")
    if d.get("reason"):
        ex.append(_REASON_JA.get(d["reason"], str(d["reason"])))
    if d.get("closing"):
        ex.append(f"閉店 {d['closing']}")
    if "work_days" in d or "paid_leave_days" in d:
        ex.append(f"出勤{_detail_value(d.get('work_days', 0))}日・有給{_detail_value(d.get('paid_leave_days', 0))}日")
    if "count" in d:
        ex.append(f"{_detail_value(d['count'])}件")
    if d.get("overlap"):
        ex.append("時間が重なる")
    if "want" in d or "excess" in d:
        ex.append(f"希望{_detail_value(d.get('want'))}・余り{_detail_value(d.get('excess'))}")
    if "run_length" in d:
        what = "遅番" if d.get("shift") == "late" else "出勤"
        ex.append(f"{d.get('run_start')}〜{d.get('run_end')} {what}{_detail_value(d['run_length'])}日連続・上限{_detail_value(d.get('limit'))}")
    elif "limit" in d:
        ex.append(f"上限{_detail_value(d['limit'])}")
    ex += [f"{k}={_detail_value(d[k])}" for k in sorted(d) if k not in _CONSUMED]
    return ex


def violation_text(v: Violation, ctx: PeriodContext | None = None) -> str:
    """check_code + detail → 문장. 같은 위반(같은 값)은 항상 같은 문장 (V9_ko 412-415). detail 키 순서와 무관."""
    d = v.detail or {}
    parts: list[str] = []
    if v.work_date is not None:
        parts.append(v.work_date.isoformat())
    if d.get("time"):
        parts.append(str(d["time"]))
    elif d.get("start") and d.get("end"):
        parts.append(f"{d['start']}〜{d['end']}")
    elif d.get("start"):
        parts.append(str(d["start"]))
    if v.employee_id is not None:
        parts.append(f"ID{v.employee_id}")
    base = _CODE_TEXT.get(v.check_code, v.check_code)
    if v.check_code == "OUTSIDE_AVAILABILITY" and d.get("kind") == "regular":
        base = "社員の希望の時間の外に入っている"
    rk = d.get("rule_key")
    if rk:
        rule = next((r for r in (ctx.template_rules if ctx else ()) if r.rule_key == rk), None)
        base += f"(店長の指示 {rk}" + (f": {template_rule_text(rule)}" if rule else "") + ")"
    extra = _detail_extras(d)
    s = " ".join(parts + [base])
    return s + (f"({', '.join(extra)})" if extra else "")


def _rounds(g: ViolationGroup) -> str:
    return "・".join(str(r) for r in sorted(g.rounds)) + "回目"


def _assignment_rows(previous: Sequence[Assignment]) -> list[dict]:
    rows = [a for a in previous if a.kind == "work" and a.start_time is not None and a.end_time is not None]
    rows.sort(key=lambda a: (a.work_date, a.employee_id, a.start_time))
    return [{"staff_id": a.employee_id, "date": a.work_date.isoformat(), "start": _t(a.start_time), "end": _t(a.end_time)}
            for a in rows]


def make_retry_text(ctx: PeriodContext, previous: Sequence[Assignment] | None, history: Sequence[ViolationGroup],
                    instruction: str | None) -> str:
    """재시도 · 재편성 요청 때만 붙는 부분. 첫 호출(직전 안 · 이력 · 지시 모두 없음)이면 빈 문자열."""
    parts: list[str] = []
    if previous is not None:
        parts.append("【前回の案(assignments)】\n" + _j(_assignment_rows(previous)))
    shown = [g for g in history if INCLUDE_WARNINGS_IN_RETRY or g.severity == "required"]
    if shown:
        latest = max(r for g in history for r in g.rounds)
        key = lambda g: (g.check_code, g.example.work_date.isoformat() if g.example.work_date else "", g.key)  # noqa: E731
        cur = sorted((g for g in shown if latest in g.rounds), key=key)
        old = sorted((g for g in shown if latest not in g.rounds), key=key)
        if cur:
            parts.append(f"【{latest}回目の検査で出た問題(全{len(cur)}件。今回は必ず直すこと。同じ問題は1行にまとめ、出た回を書いた)】\n"
                         + "\n".join(f"- {violation_text(g.example, ctx)}【{_rounds(g)}】" for g in cur))
        if old:
            parts.append(f"【それより前の回だけで出た問題(全{len(old)}件。今は直っている。元に戻さないこと)】\n"
                         + "\n".join(f"- {violation_text(g.example, ctx)}【{_rounds(g)}】" for g in old))
    if instruction and instruction.strip():
        parts.append("【店長からの作り直しの指示(できるだけ反映する。コードは検査しない)】\n" + instruction.strip())
    if previous is not None:
        tail = KEEP_OTHER_CELLS
        if instruction and instruction.strip():
            tail += "ただし、店長の指示に必要な変更はしてよい。"
        parts.append(tail + "問題のある所だけを直し、案全体(assignments をすべて)をもう一度出すこと。")
    return "\n\n".join(parts)


# ---------------------------------------------------------------- 요청 모양

def build_compose_request(ctx: PeriodContext, previous: Sequence[Assignment] | None,
                          history: Sequence[ViolationGroup], instruction: str | None) -> tuple[list[dict], list[dict]]:
    """→ (system 블록, messages). Anthropic Messages API 의 모양 그대로."""
    system = [{"type": "text", "text": build_system_text(ctx), "cache_control": CACHE}]
    content = [{"type": "text", "text": make_staff_text(ctx), "cache_control": CACHE}]
    retry = make_retry_text(ctx, previous, history, instruction)
    if retry:
        content.append({"type": "text", "text": retry})
    return system, [{"role": "user", "content": content}]


INSTRUCTION_SYSTEM = (
    "あなたは店長の指示文を、決まった「枠」に当てはめる係です。\n"
    "枠に当てはまる部分だけを rules に入れ、当てはまらない部分は free_text に原文のまま入れます。推測で枠に入れないこと。\n"
    "\n【枠】\n"
    "1. not_available: ある人が、ある曜日(またはある日)に入れない。params: staff_id と、weekday(1=月 … 7=日)か date(YYYY-MM-DD)のどちらか1つ。\n"
    "2. max_consecutive: 連続の上限。params: limit(日数・回数)、shift(\"any\"=出勤日の連続 / \"late\"=閉店時刻に終わる勤務の連続)、"
    "staff_id(特定の人だけなら。全員なら null)。\n"
    "3. min_headcount: ある時間帯の最低人数。params: count、start と end(HH:MM、30分単位)、weekday か date(どちらも null なら毎日)。\n"
    "\n【決まり】\n"
    "- 人は ID(staff_id)で書く。「3番」「ID3」は staff_id 3。名前しか書かれていない人は ID がわからないので、その部分は free_text に入れる。\n"
    "- その枠で使わない params は null にする。\n"
    "- source_text には、その枠のもとになった店長の原文の部分をそのまま入れる。\n"
    "- 1つの文に枠が2つあれば、rules を2つにする。\n"
    "- 出力は指定の JSON の形だけ。\n"
)


def build_instruction_request(ctx: PeriodContext, text: str) -> tuple[list[dict], list[dict]]:
    dates = ctx.period.dates()
    regs = [e.employee_id for e in sorted(ctx.employees, key=lambda x: x.employee_id) if e.is_regular]
    parts = [e.employee_id for e in sorted(ctx.employees, key=lambda x: x.employee_id) if not e.is_regular]
    user = (f"【対象期間】{dates[0].isoformat()}〜{dates[-1].isoformat()}\n"
            f"【スタッフの ID】社員: {', '.join(map(str, regs)) or 'なし'} / パート・バイト: {', '.join(map(str, parts)) or 'なし'}\n"
            f"\n【店長の指示文】\n{text.strip()}")
    system = [{"type": "text", "text": INSTRUCTION_SYSTEM, "cache_control": CACHE}]
    return system, [{"role": "user", "content": user}]


def request_text(system: list[dict], messages: list[dict]) -> str:
    """요청에 실리는 글자 전부 (테스트 · 감사용: 실명이 섞였는지 본다)."""
    out = [b["text"] for b in system]
    for m in messages:
        c = m["content"]
        out += [c] if isinstance(c, str) else [b["text"] for b in c if b.get("type") == "text"]
    return "\n".join(out)


__all__ = [
    "GIVE_UP_ORDER", "KEEP_OTHER_CELLS", "build_compose_request", "build_instruction_request", "build_system_text",
    "make_retry_text", "make_staff_text", "request_text", "staff_payload", "template_rule_text", "violation_text",
]
