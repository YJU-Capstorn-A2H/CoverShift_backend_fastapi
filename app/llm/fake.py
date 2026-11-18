"""가짜 LLM (하네스 C5). 네트워크 없음 · 결정적 · 아즈키 30일 데이터에서 1초 안.

시나리오 (생성자 인자 > 환경 변수 FAKE_LLM_SCENARIO > "greedy"):
- greedy        : ctx 로 그럴듯한 안을 만든다. 파트・알바는 신청 시간 그대로만, 사원은 출근일수를 맞추려 한다. 완벽하지 않아도 된다.
- bad_then_good : 첫 호출(previous 없음)은 일부러 위반 1개(같은 사람 같은 날 2행 = DOUBLE_BOOKING)를 넣은 안,
                  직전 안이 있는 호출부터는 greedy. 인스턴스 상태가 아니라 previous 로 가른다 —
                  그래프가 get_composer() 를 노드마다 새로 불러도 같은 동작이 되게.
- always_bad    : 매번 위반 1개를 넣은 안 (상한 도달 · 예외 불가 위반 경로 시험용).
- empty         : ok=False, error="empty" (F-4: 초안 0건).
- script        : 생성자에 준 ComposeResult 목록을 순서대로. 다 쓰면 RuntimeError (시험 작성 실수를 숨기지 않기 위해).

LLM 과 같은 길을 밟게, 안은 LLM 출력 모양(dict)으로 만든 뒤 schema.parse_draft 로 다시 검사한다.
프롬프트도 매번 만들어 last_request 에 둔다 (프롬프트 코드가 어떤 ctx 에서도 깨지지 않는지 그래프 시험에서도 보이게).
"""
from __future__ import annotations

import os
import re
import time
from collections import defaultdict
from collections.abc import Sequence
from datetime import date

from app.domain import (
    Assignment,
    ComposeResult,
    InstructionResult,
    NotAvailableParams,
    PeriodContext,
    ViolationGroup,
)
from app.llm.hours import business_hours, hhmm, to_min
from app.llm.prompt import build_compose_request
from app.llm.schema import finalize_rules, parse_draft

SCENARIOS = ("greedy", "bad_then_good", "always_bad", "empty", "script")
SHIFT_MINUTES = 9 * 60  # 아즈키 데이터의 사원 근무 모양(08:00-17:00 / 13:30-22:30)과 같은 길이


class FakeComposer:
    def __init__(self, scenario: str | None = None, script: Sequence[ComposeResult] | None = None) -> None:
        sc = scenario or os.environ.get("FAKE_LLM_SCENARIO") or ("script" if script is not None else "greedy")
        if sc not in SCENARIOS:
            raise ValueError(f"unknown fake scenario: {sc} (choose from {SCENARIOS})")
        if sc == "script" and script is None:
            raise ValueError("scenario 'script' needs script=[ComposeResult, ...]")
        self.scenario = sc
        self._script = list(script or [])
        self.calls: list[dict] = []  # 시험에서 「무엇을 넘겼나」를 보려고 남긴다 (F-5 확인용)
        self.last_request: tuple[list[dict], list[dict]] | None = None

    # ------------------------------------------------------------ Composer

    def compose(self, ctx: PeriodContext, *, previous: Sequence[Assignment] | None,
                history: Sequence[ViolationGroup], instruction: str | None) -> ComposeResult:
        t0 = time.perf_counter()
        self.calls.append({"previous": None if previous is None else list(previous), "history": list(history),
                           "instruction": instruction})
        self.last_request = build_compose_request(ctx, previous, history, instruction)
        n = len(self.calls)

        if self.scenario == "script":
            if n > len(self._script):
                raise RuntimeError(f"FakeComposer script exhausted (call {n}, script has {len(self._script)})")
            return self._script[n - 1]
        if self.scenario == "empty":
            res = ComposeResult(ok=False, error="empty")
        else:
            bad = self.scenario == "always_bad" or (self.scenario == "bad_then_good" and previous is None)
            res = parse_draft(ctx, greedy_draft(ctx, bad=bad))
        res.usage = {**res.usage, "model": "fake", "scenario": self.scenario, "call": n,
                     "seconds": round(time.perf_counter() - t0, 4), "input_tokens": 0, "output_tokens": 0}
        return res

    def instruction_to_rules(self, ctx: PeriodContext, text: str) -> InstructionResult:
        candidates, free = keyword_candidates(text)
        return finalize_rules(ctx, candidates, free)


# ---------------------------------------------------------------- greedy 안

def _blocked(ctx: PeriodContext) -> set[tuple[int, date]]:
    """not_available 틀 규칙 → (staff_id, 날짜) 집합. 가짜도 확인된 틀 규칙은 지킨다."""
    out: set[tuple[int, date]] = set()
    for r in ctx.template_rules:
        p = r.params
        if isinstance(p, NotAvailableParams):
            for d in ctx.period.dates():
                if (p.date is not None and d == p.date) or (p.weekday is not None and d.isoweekday() == p.weekday):
                    out.add((p.staff_id, d))
    return out


def _qualified_need(ctx: PeriodContext):
    """(날짜, 분) → 그 칸의 min_qualified (V-2). 규칙이 없으면 0."""
    by_wd: dict[int, list] = defaultdict(list)
    for r in ctx.time_rules:
        by_wd[r.weekday].append((to_min(r.start_time), to_min(r.end_time), r.min_qualified))

    def need(d: date, t: int) -> int:
        return max((q for s, e, q in by_wd.get(d.isoweekday(), ()) if s <= t < e), default=0)

    return need


def greedy_draft(ctx: PeriodContext, *, bad: bool = False) -> dict:
    """LLM 출력 모양의 dict. bad=True 면 맨 앞 행을 한 번 더 넣는다 (같은 사람 같은 날 2행 → DOUBLE_BOOKING, V-8).

    순서: ① 파트・알바 = 신청 그대로 (V-3) ② 사원 = 신청 시각이 있는 날은 그대로, 빈 날 중에서 출근일수(V-5)를 맞춰 고름
    ③ 날마다 사원 0명 칸(V-1) · 자격자 부족 칸(V-2)을 9시간 창으로 메움 ④ 그래도 빈 날은, 다른 날에 넣은 사원 1명을
    옮겨 와서 메움(옮긴 날이 비지 않을 때만 — 출근일수는 그대로). 완벽하지 않아도 된다 (가짜).
    """
    bh = business_hours(ctx)
    dates = [d for d in ctx.period.dates() if d.isoweekday() in bh]
    avail = {(a.employee_id, a.work_date): a for a in ctx.availability}
    blocked = _blocked(ctx)
    by_id = {e.employee_id: e for e in ctx.employees}
    qual_need = _qualified_need(ctx)

    def clip(d: date, s: int, e: int, closing_ok: bool) -> tuple[int, int]:
        o, c = bh[d.isoweekday()]
        s, e = max(s, o), min(e, c)
        if e >= c and not closing_ok:  # V-4: 폐점까지는 closing_ok 만
            e = c - 30
        return s, e

    base: dict[date, list[tuple[int, int, int]]] = defaultdict(list)  # 옮기지 않는 행 (파트 · 사원 신청 시각)
    picks: dict[date, dict[int, tuple[int, int]]] = defaultdict(dict)  # 코드가 고른 사원 행 (옮길 수 있음)

    # ① 파트・알바: 신청한 날 · 신청 시간 그대로 (V-3)
    for emp in (x for x in ctx.employees if not x.is_regular):
        for d in dates:
            a = avail.get((emp.employee_id, d))
            if a is None or a.kind not in ("available", "half_leave") or a.start_time is None or a.end_time is None:
                continue
            if (emp.employee_id, d) in blocked:
                continue
            s, e = clip(d, to_min(a.start_time), to_min(a.end_time), emp.closing_ok)
            if e > s:
                base[d].append((emp.employee_id, s, e))

    # ② 사원: 신청 시각이 있는 날 = 그대로 / 빈 날(free) = 코드가 고른다
    regs = sorted((x for x in ctx.employees if x.is_regular), key=lambda x: x.employee_id)
    target = len(ctx.period.dates()) - ctx.period.designated_off_days
    free: dict[int, set[date]] = {}
    remaining: dict[int, int] = {}
    for emp in regs:
        eid = emp.employee_id
        free[eid] = set()
        n_paid = n_fixed = 0
        for d in ctx.period.dates():
            a = avail.get((eid, d))
            if a is not None and a.kind == "paid_leave":
                n_paid += 1
            elif d.isoweekday() not in bh or (eid, d) in blocked or (a is not None and a.kind == "off"):
                continue  # 휴업 요일 · 틀 규칙 불가 · 희망휴
            elif a is not None and a.start_time is not None and a.end_time is not None:
                s, e = clip(d, to_min(a.start_time), to_min(a.end_time), emp.closing_ok)
                if e > s:
                    base[d].append((eid, s, e))
                    n_fixed += 1
            else:
                free[eid].add(d)
        remaining[eid] = max(0, target - n_paid - n_fixed)

    def intervals(d: date, *, without: int | None = None) -> list[tuple[int, int, int]]:
        return base[d] + [(x, s, e) for x, (s, e) in picks[d].items() if x != without]

    def gaps(d: date, iv: list[tuple[int, int, int]]) -> list[tuple[bool, bool, int]]:
        """빈 칸 목록 (까다로운 순): 폐점 칸 자격자 부족 > 자격자 부족 > 사원 없음, 같으면 이른 칸."""
        o, c = bh[d.isoweekday()]
        out = []
        for t in range(o, c, 30):
            here = [x for x, s, e in iv if s <= t < e]
            need_q = sum(by_id[x].has_qualification for x in here) < qual_need(d, t)
            if need_q or not any(by_id[x].is_regular for x in here):
                out.append((not (need_q and t >= c - 30), not need_q, t))
        return sorted(out)

    def window(d: date, t: int) -> tuple[int, int]:
        o, c = bh[d.isoweekday()]
        s = max(o, min(t, c - SHIFT_MINUTES))
        return s, min(c, s + SHIFT_MINUTES)

    def fits(x: int, d: date, t: int, need_q: bool) -> bool:
        o, c = bh[d.isoweekday()]
        return (by_id[x].has_qualification or not need_q) and (by_id[x].closing_ok or t < c - 30)

    free_left = {eid: len(v) for eid, v in free.items()}
    for i, d in enumerate(dates):
        o, c = bh[d.isoweekday()]
        cands = [e.employee_id for e in regs if d in free[e.employee_id] and remaining[e.employee_id] > 0]
        urgency = {x: (-remaining[x] / max(free_left[x], 1), x) for x in cands}
        chosen = [x for x in cands if remaining[x] >= free_left[x]]  # 오늘 안 넣으면 일수가 모자라는 사람
        for _ in range(8):  # ③ 빈 칸 메우기 (한 번에 9시간 창 하나)
            g = gaps(d, intervals(d))
            if not g:
                break
            _, nq, t = g[0]
            need_q = not nq
            # 자격자가 필요 없는 칸에는 무자격 사원을 먼저 — 자격자 사원의 남은 일수를 아껴 둔다
            pref = lambda x, nq=need_q, u=urgency: (by_id[x].has_qualification != nq, u[x])  # noqa: E731
            pool = sorted((x for x in chosen if x not in picks[d]), key=pref) + sorted(
                (x for x in cands if x not in chosen), key=pref)
            ok = [x for x in pool if fits(x, d, t, need_q)] or [x for x in pool if fits(x, d, t, False)]
            if not ok:
                break
            x = ok[0]
            if x not in chosen:
                chosen.append(x)
            picks[d][x] = clip(d, *window(d, t), by_id[x].closing_ok)
        rest_days = len(dates) - i
        want = sum(remaining.values()) // rest_days if rest_days else 0  # 내림: 남은 날은 must 가 뒤에서 채운다
        spare = sorted((x for x in cands if x not in chosen), key=lambda x: (by_id[x].has_qualification, urgency[x]))
        chosen += spare[:max(0, want - len(chosen))]
        for k, x in enumerate(x for x in chosen if x not in picks[d]):  # 남은 사람은 이른 근무 / 늦은 근무를 번갈아
            late = k % 2 == 1 and by_id[x].closing_ok
            picks[d][x] = clip(d, *((c - SHIFT_MINUTES, c) if late else (o, o + SHIFT_MINUTES)), by_id[x].closing_ok)
        for x in chosen:
            remaining[x] -= 1
        for x in (e.employee_id for e in regs if d in free[e.employee_id]):
            free_left[x] -= 1

    # ④ 옮겨서 메우기: 빈 날 d 에, 다른 날 d2 에 넣었던 사원 x 를 옮긴다 (d2 가 비지 않을 때만). 출근일수는 그대로
    for _ in range(4 * len(dates)):
        moved = False
        for d in dates:
            g = gaps(d, intervals(d))
            if not g:
                continue
            _, nq, t = g[0]
            for x in sorted(free, key=lambda x: (not by_id[x].has_qualification, x)):
                if d not in free[x] or x in picks[d] or not fits(x, d, t, not nq):
                    continue
                xw = clip(d, *window(d, t), by_id[x].closing_ok)
                d2 = next((d2 for d2 in dates if x in picks[d2] and not gaps(d2, intervals(d2, without=x))), None)
                if d2 is not None:
                    del picks[d2][x]
                    picks[d][x] = xw
                    moved = True
                    break
                # 맞바꾸기: d2 의 x ↔ d 의 u (x 는 d 로, u 는 x 가 있던 d2 의 자리로). 두 날 모두 나빠지지 않을 때만
                for d2 in (d2 for d2 in dates if x in picks[d2]):
                    for u in [u for u in picks[d] if u != x and d2 in free[u] and u not in picks[d2]]:
                        uw = clip(d2, *picks[d2][x], by_id[u].closing_ok)
                        new_d = [iv for iv in intervals(d, without=u)] + [(x, *xw)]
                        new_d2 = [iv for iv in intervals(d2, without=x)] + [(u, *uw)]
                        if not gaps(d2, new_d2) and len(gaps(d, new_d)) < len(g):
                            del picks[d2][x], picks[d][u]
                            picks[d][x], picks[d2][u] = xw, uw
                            moved = True
                            break
                    if moved:
                        break
                if moved:
                    break
            if moved:
                break
        if not moved:
            break

    rows = [{"staff_id": x, "date": d.isoformat(), "start": hhmm(s), "end": hhmm(e)}
            for d in dates for x, s, e in intervals(d) if e > s]
    rows.sort(key=lambda r: (r["date"], r["staff_id"], r["start"]))
    notes = ["[fake] greedy"]
    if bad and rows:
        rows.append(dict(rows[0]))  # 같은 사람 · 같은 날 · 같은 시간 2행 (V-8 DOUBLE_BOOKING, 예외 불가)
        notes = ["[fake] わざと同じ行を2回入れた(DOUBLE_BOOKING)"]
    return {"assignments": rows, "shortages": [], "notes": notes}


# ---------------------------------------------------------------- 지시문 키워드 파서 (가짜 instruction_to_rules)

_WD = {"月": 1, "火": 2, "水": 3, "木": 4, "金": 5, "土": 6, "日": 7,
       "월": 1, "화": 2, "수": 3, "목": 4, "금": 5, "토": 6, "일": 7}
_STAFF = r"(?:ID\s*(\d+)|(\d+)\s*(?:番|번))"
_WDAY = r"([月火水木金土日월화수목금토일])\s*(?:曜日|曜|요일)"
_NG = r"(?:不可|ダメ|だめ|入れない|NG|불가|안\s*됨|안돼)"
_TIME = r"(\d{1,2}:\d{2})\s*[-~〜～]\s*(\d{1,2}:\d{2})"

_RE_NOT_AVAILABLE = re.compile(_STAFF + r".*?" + _WDAY + r".*?" + _NG)
_RE_HEADCOUNT = re.compile(r"(?:" + _WDAY + r"\s*)?" + _TIME + r"\D*?(\d+)\s*(?:人|명)")
_RE_CONSEC = re.compile(r"(?:連勤|連続出勤|연속\s*(?:근무|출근)?|연근)\D*?(\d+)\s*(?:日|일|回|회)\s*(?:まで|까지)")
_RE_CONSEC2 = re.compile(r"(\d+)\s*連勤まで")
_RE_LATE = re.compile(r"遅番|늦은\s*근무|마감")


def _hm(s: str) -> str:
    h, m = s.split(":")
    return f"{int(h):02d}:{m}"


def keyword_candidates(text: str) -> tuple[list[dict], list[str]]:
    """文 하나씩 본다. 맞는 틀이 있으면 후보 dict, 없으면 free_text. 틀 검사는 finalize_rules 가 한다."""
    cands: list[dict] = []
    free: list[str] = []
    for seg in (s.strip() for s in re.split(r"[。\n]|(?<=[.!?])\s", text or "")):
        if not seg:
            continue
        m = _RE_NOT_AVAILABLE.search(seg)
        if m:
            sid = int(m.group(1) or m.group(2))
            cands.append({"template_key": "not_available", "params": {"staff_id": sid, "weekday": _WD[m.group(3)]},
                          "source_text": seg})
            continue
        m = _RE_HEADCOUNT.search(seg)
        if m:
            params: dict = {"count": int(m.group(4)), "start": _hm(m.group(2)), "end": _hm(m.group(3))}
            if m.group(1):
                params["weekday"] = _WD[m.group(1)]
            cands.append({"template_key": "min_headcount", "params": params, "source_text": seg})
            continue
        m = _RE_CONSEC.search(seg) or _RE_CONSEC2.search(seg)
        if m:
            params = {"limit": int(m.group(1)), "shift": "late" if _RE_LATE.search(seg) else "any"}
            ms = re.search(_STAFF, seg)
            if ms:
                params["staff_id"] = int(ms.group(1) or ms.group(2))
            cands.append({"template_key": "max_consecutive", "params": params, "source_text": seg})
            continue
        free.append(seg)
    return cands, free
