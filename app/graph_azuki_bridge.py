# app/graph_azuki_bridge.py -- アズキのグラフ(app/graph/)を、승혁 の runner・API・画面につなぐ「橋」
#   ・app/graph/ のファイルは、1文字も変えない。このファイルが、間に立って、値の形を変える(変換の関数は app/graph_azuki_convert.py)
#   ・build_graph(checkpointer, deps) は、runner が期待する形で AzukiGraph(包み)を返す。環境変数 GRAPH_IMPL=azuki で、runner がこれを使う
#   ・包みがやること:
#       1) 承認待ち(approve)で止まったら、승혁 の検査(app.validate)で、コードつきの違反を作り、下書きを DB(shift_drafts など)に保存する
#       2) 止まる所の値を、승혁 の形(ReviewWait / ContactWait)にして runner に見せる
#       3) 再開の値を、アズキのグラフの形に変えてから渡す。やり直し・編集の記録(manager_actions)と、確定の保存(confirm_draft)も、ここで書く
#   ・승혁 の部品(app.graph_seung の nodes.collect など)を、検査の準備に借りる
from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from langgraph.types import Command

from app import graph_azuki_convert as cv

log = logging.getLogger("graph.azuki")

_GRAPH_DIR = str(Path(__file__).resolve().parent / "graph")
_NON_WAIVABLE = frozenset({"QUAL_MISSING", "DOUBLE_BOOKING"})  # 例外を許せない違反 (app.domain.NON_WAIVABLE_CODES と同じ)
_CONTACT_STATUS = {"yes": "accepted", "no": "declined", "expired": "expired"}  # db/002 contact_log.status


def _hm(v: Any) -> str | None:
    return None if v is None else str(v)[:5]


def _split_resume(r: Any) -> tuple[str | None, Any]:
    """runner の Command(resume=...) の中身 → (interrupt の id か None, payload)。"""
    if isinstance(r, dict) and ("decision" in r or "contact_id" in r):
        return None, r
    if isinstance(r, dict) and len(r) == 1:
        ((iid, p),) = r.items()
        if isinstance(p, dict):
            return str(iid), p
    return None, r


def _key(r: dict) -> tuple:
    return (int(r["employee_id"]), str(r["work_date"]), r.get("kind") or "work", _hm(r.get("start_time")), _hm(r.get("end_time")))


class AzukiGraph:
    """コンパイル済みのアズキのグラフを包む。runner が使う invoke / get_state だけを、승혁 の形に合わせる。"""

    def __init__(self, compiled, deps):
        self._g = compiled
        self._deps = deps
        self._viol_cache: dict[tuple[int, int], list[dict]] = {}

    def __getattr__(self, name: str):  # nodes / checkpointer など、他の属性は、中のグラフにそのまま聞く
        return getattr(self._g, name)

    # ------------------------------------------------------------ 読む(runner の status が呼ぶ)
    def get_state(self, config):
        snap = self._g.get_state(config)
        intr = list(snap.interrupts) or [i for t in snap.tasks for i in t.interrupts]
        values = snap.values or {}
        shown = tuple(SimpleNamespace(id=getattr(i, "id", None), value=self._wait(i.value, values)) for i in intr)
        tasks = [SimpleNamespace(interrupts=(), error=getattr(t, "error", None)) for t in snap.tasks]
        return SimpleNamespace(interrupts=shown, tasks=tasks, created_at=snap.created_at, next=snap.next, values=values)

    def _wait(self, value: Any, values: dict) -> Any:
        if not isinstance(value, dict):
            return value
        ask = str(value.get("ask", ""))
        if ask.startswith("approved"):
            return self._review(value, values)
        if ask.startswith("可能"):
            return self._contact(value, values)
        return value  # 知らない形は、そのまま渡す(runner が「不明な type」の error にする)

    def _review(self, value: dict, values: dict) -> dict:
        from app.repo import drafts as repo

        period_id = values.get("period_id")
        with self._deps.connect() as conn:
            did = self._latest_draft_id(conn, period_id)
            rows = repo.load_assignments(conn, did) if did else []
        violations = self._violations(period_id, did, rows)
        blocked = [v for v in violations if not v.get("waivable", True)]
        return cv.review_wait(value, values=values, draft_id=did, violations=violations, blocked=blocked,
                              limit=self._deps.settings.manager_request_limit)

    def _contact(self, value: dict, values: dict) -> dict:
        from app.repo import drafts as repo

        pick = {"employee_id": int(value["staff_id"]), "work_date": value["date"],
                "start_time": _hm(value["start"]), "end_time": _hm(value["end"])}
        with self._deps.connect() as conn:
            cid = repo.ask_contact(conn, period_id=values["period_id"], **pick)  # 同じ質問なら、同じ contact_id が返る
            self._deps.send(conn, store_id=values.get("store_id"), employee_id=pick["employee_id"], kind="contact_ask",
                            dedupe_key=f"contact:{cid}",
                            body={"text": f"{pick['work_date']} {pick['start_time']}~{pick['end_time']} 勤務できますか?",
                                  "contact_id": cid, "buttons": ["yes", "no"], **pick})
        return {"type": "contact", "contact_id": cid, **pick}

    # ------------------------------------------------------------ 動かす(runner の start / resume が呼ぶ)
    def invoke(self, inp, config, **kw):
        payload, graph_inp = None, inp
        if isinstance(inp, Command) and inp.resume is not None:
            iid, payload = _split_resume(inp.resume)
            if isinstance(payload, dict):
                self._before(config, payload)
                translated = cv.resume_to_azuki(payload)
                graph_inp = Command(resume={iid: translated} if iid else translated)
        out = self._g.invoke(graph_inp, config, **kw)
        self._after(config, payload)
        return out

    def _before(self, config, payload: dict) -> None:
        """グラフに渡す前に、DB に残す記録(再開で、この関数が何度走っても、増えないように、repo 側が一度だけにする)。"""
        from app.repo import drafts as repo

        values = self._g.get_state(config).values or {}
        decision = payload.get("decision")
        with self._deps.connect() as conn:
            if "contact_id" in payload and payload.get("answer") in _CONTACT_STATUS:
                repo.answer_contact(conn, contact_id=int(payload["contact_id"]), status=_CONTACT_STATUS[payload["answer"]])
            elif decision in ("redraft_requested", "edit_saved") and payload.get("draft_id") and payload.get("manager_id"):
                instr = (payload.get("instruction") or "").strip() or None if decision == "redraft_requested" else None
                repo.record_action_once(conn, period_id=values["period_id"], draft_id=int(payload["draft_id"]),
                                        manager_id=int(payload["manager_id"]), action=decision, instruction=instr)

    def _after(self, config, payload: dict | None) -> None:
        snap = self._g.get_state(config)
        values = snap.values or {}
        intr = list(snap.interrupts) or [i for t in snap.tasks for i in t.interrupts]
        asks = [str(i.value.get("ask", "")) for i in intr if isinstance(i.value, dict)]
        if any(a.startswith("approved") for a in asks):
            self._save_if_new(values)  # 店長の画面に出す案を、DB に残す
        elif not snap.next and isinstance(payload, dict) and payload.get("decision") == "approved":
            self._confirm(values, payload)  # グラフが最後まで行った = 承認が通った

    # ------------------------------------------------------------ DB(下書き・違反・確定)
    @staticmethod
    def _latest_draft_id(conn, period_id) -> int | None:
        row = conn.execute("SELECT max(draft_id) AS d FROM shift_drafts WHERE period_id = %s", (period_id,)).fetchone()
        return row["d"] if row else None

    def _state_for(self, period_id: int) -> dict:
        """승혁 の collect で、検査の準備(職員・希望・時間帯ルール)を、DB から読む。"""
        from app.graph_seung import nodes as N

        st: dict = {"period_id": period_id}
        st.update(N.collect(st, self._deps))
        return st

    def _violations(self, period_id: int, did: int | None, rows: list[dict]) -> list[dict]:
        if did is None:
            return []
        if (period_id, did) not in self._viol_cache:
            from app.graph_seung import nodes as N

            self._viol_cache[(period_id, did)] = N._validate(self._state_for(period_id), rows, self._deps)
        return self._viol_cache[(period_id, did)]

    def _save_if_new(self, values: dict) -> int:
        from app.graph_seung import nodes as N
        from app.repo import drafts as repo

        period_id = values["period_id"]
        st = self._state_for(period_id)
        rows = N.normalize_rows(cv.rows_to_seung(values.get("draft_store_shift") or []), st)
        rows = N.with_paid_leave(rows, st["staff_availability"])
        with self._deps.connect() as conn:
            last = self._latest_draft_id(conn, period_id)
            if last and {_key(r) for r in repo.load_assignments(conn, last)} == {_key(r) for r in rows}:
                return last  # 同じ案(承認が止められて、同じ画面に戻っただけ)は、増やさない
            viols = N._validate(st, rows, self._deps)
            did = repo.save_draft(conn, period_id=period_id, assignments=rows, violations=viols, parent_draft_id=last)
        self._viol_cache[(period_id, did)] = viols
        log.info("azuki save_draft period=%s draft_id=%s parent=%s violations=%d", period_id, did, last, len(viols))
        return did

    def _confirm(self, values: dict, payload: dict) -> None:
        """確定の保存(confirm_draft)と、従業員ごとの「確定しました」の仮LINE(승혁 の commit_notify と同じ。dedupe_key で、二重送信しない)。"""
        from app.repo import drafts as repo

        period_id, did = values["period_id"], int(payload["draft_id"])
        waived = sorted({str(k) for k in payload.get("waived_keys") or [] if str(k).split("|")[0] not in _NON_WAIVABLE})
        st = self._state_for(period_id)
        month = st["period"]["target_month"][:7]
        with self._deps.connect() as conn:
            repo.confirm_draft(conn, period_id=period_id, draft_id=did, manager_id=int(payload["manager_id"]), waived_keys=waived)
            rows = repo.load_assignments(conn, did)  # 通知は、DB に確定した、そのままの内容で
            for e in st["llm_all_staff"]:
                eid = e["employee_id"]
                work = [{"work_date": r["work_date"], "start_time": r["start_time"], "end_time": r["end_time"]}
                        for r in rows if r["employee_id"] == eid and r["kind"] == "work"]
                leave = [r["work_date"] for r in rows if r["employee_id"] == eid and r["kind"] == "paid_leave"]
                self._deps.send(conn, store_id=st["store_id"], employee_id=eid, kind="shift_confirmed",
                                dedupe_key=f"confirm:{did}:{eid}",
                                body={"text": f"{month} シフトが確定しました。出勤 {len(work)}日。", "draft_id": did,
                                      "work_days": work, "paid_leave_days": leave})
        log.info("azuki confirm period=%s draft_id=%s", period_id, did)


def build_graph(checkpointer=None, deps=None):
    """runner が呼ぶ入口。アズキのグラフをコンパイルして、包み(AzukiGraph)にして返す。"""
    if _GRAPH_DIR not in sys.path:
        sys.path.append(_GRAPH_DIR)  # 末尾に足す: settings / state / routes / node などの名前が、他の物を隠さないように
    from graph import builder  # app/graph/graph.py の builder (StateGraph)

    from app.graph_seung.deps import default_deps

    return AzukiGraph(builder.compile(checkpointer=checkpointer), deps or default_deps())
