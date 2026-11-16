# app/routers/drafts.py
# 005-初稿の取得・例外許可・承認・差し戻し・手修正 / 005-초안 조회・예외 허가・승인・반려・수동 수정
# 原則: AIは提案だけ。最終確定は管理者（承認API）。/ 원칙: AI는 제안만. 최종 확정은 관리자(승인 API).
from datetime import date, time

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator

from app.core.db import try_lock_period, tx
from app.core.deps import current_manager
from app.core.errors import ApiError
from app.services.graph_gateway import GraphGateway, get_gateway

router = APIRouter()

# 例外として許可できない検査（基準文書）。承認も止める。
# 예외로 허가할 수 없는 검사(기준 문서). 승인도 막는다.
NON_WAIVABLE = ("QUAL_MISSING", "DOUBLE_BOOKING")


def _draft(conn, draft_id: int, store_id: int) -> dict:
    """自分の店の案だけ取り出す。他店の案は「無い」扱い(404)。
    자기 점포의 안만 꺼낸다. 다른 점포의 안은 「없음」 취급(404)."""
    row = conn.execute(
        "SELECT d.draft_id, d.period_id, d.status, d.parent_draft_id, d.confirmed_at, d.confirmed_by, "
        "       p.thread_id, p.target_month "
        "FROM shift_drafts d JOIN shift_periods p ON p.period_id = d.period_id "
        "WHERE d.draft_id = %s AND p.store_id = %s", (draft_id, store_id)).fetchone()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "案が見つかりません / 안을 찾을 수 없습니다")
    return row


def _need_open(d: dict):
    if d["status"] == "confirmed":
        raise ApiError(409, "DRAFT_CONFIRMED", "確定済みの案は変更できません / 확정된 안은 변경할 수 없습니다")


def _need_thread(d: dict) -> str:
    if not d["thread_id"]:
        raise ApiError(409, "NOT_STARTED", "この月の編成がまだ始まっていません / 이 달의 편성이 아직 시작되지 않았습니다")
    return d["thread_id"]


_VCOLS = "violation_id, check_code, work_date, employee_id, detail, allowed_by, allowed_at"


@router.get("/admin/drafts/{draft_id}", summary="案の取得 / 안 조회")
def get_draft(draft_id: int, me: dict = Depends(current_manager)):
    with tx() as conn:
        d = _draft(conn, draft_id, me["store_id"])
        assigns = conn.execute(
            "SELECT employee_id, work_date, kind, start_time, end_time, break_start, break_end "
            "FROM shift_assignments WHERE draft_id = %s ORDER BY work_date, start_time, employee_id",
            (draft_id,)).fetchall()
        vs = conn.execute(f"SELECT {_VCOLS} FROM validation_violations WHERE draft_id = %s ORDER BY violation_id",
                          (draft_id,)).fetchall()
    open_v = [v for v in vs if v["allowed_by"] is None]
    return {
        "draft": {k: d[k] for k in ("draft_id", "period_id", "status", "parent_draft_id", "confirmed_at", "confirmed_by")},
        "assignments": assigns,
        "validation": {"ok": not open_v, "violations": open_v},      # 未解決のものだけ / 미해결인 것만
        "guideline_set": None,                                       # 指針は未実装（501の対象）/ 지침은 미구현
        "waivers": [v for v in vs if v["allowed_by"] is not None],   # 許可済み / 허가 완료
    }


class WaiveIn(BaseModel):
    violation_key: int | str          # = violation_id（提案: 6章はkey、DDLはid）/ (제안: 6장은 key, DDL은 id)
    reason: str = Field(min_length=1, max_length=500)


@router.post("/admin/drafts/{draft_id}/waive", status_code=201, summary="違反の例外許可 / 위반 예외 허가")
def waive(draft_id: int, body: WaiveIn, me: dict = Depends(current_manager)):
    try:
        vid = int(body.violation_key)
    except (TypeError, ValueError):
        raise ApiError(422, "VALIDATION_ERROR", "violation_key は違反ID / violation_key는 위반 ID")
    with tx() as conn:
        d = _draft(conn, draft_id, me["store_id"])
        _need_open(d)
        v = conn.execute(f"SELECT {_VCOLS} FROM validation_violations WHERE violation_id = %s AND draft_id = %s FOR UPDATE",
                         (vid, draft_id)).fetchone()
        if v is None:
            raise ApiError(404, "NOT_FOUND", "違反が見つかりません / 위반을 찾을 수 없습니다")
        if v["check_code"] in NON_WAIVABLE:
            raise ApiError(409, "NOT_WAIVABLE", "この違反は例外にできません / 이 위반은 예외로 할 수 없습니다",
                           {"check_code": v["check_code"]})
        if v["allowed_by"] is not None:
            raise ApiError(409, "ALREADY_WAIVED", "既に許可済みです / 이미 허가되었습니다")
        # 理由の専用列は無い → detail(JSON)に入れる（提案）/ 이유 전용 열이 없다 → detail(JSON)에 넣는다(제안)
        return conn.execute(
            "UPDATE validation_violations SET allowed_by = %s, allowed_at = now(), "
            "detail = COALESCE(detail, '{}'::jsonb) || jsonb_build_object('waive_reason', %s::text) "
            f"WHERE violation_id = %s RETURNING {_VCOLS}", (me["manager_id"], body.reason, vid)).fetchone()


@router.post("/admin/drafts/{draft_id}/approve", summary="案の承認(確定) / 안 승인(확정)")
def approve(draft_id: int, me: dict = Depends(current_manager), gw: GraphGateway = Depends(get_gateway)):
    """未解決の違反が残っていれば 409（一覧つき）。通れば 記録 → 確定 → グラフ再開 を1トランザクションで。
    グラフの再開に失敗したら全部取り消す（確定だけ残らない）。
    미해결 위반이 남아 있으면 409(목록 포함). 통과하면 기록 → 확정 → 그래프 재개를 1트랜잭션으로.
    그래프 재개에 실패하면 전부 취소한다(확정만 남지 않는다)."""
    with tx() as conn:
        d = _draft(conn, draft_id, me["store_id"])
        if not try_lock_period(conn, d["period_id"]):
            raise ApiError(409, "BUSY", "今、処理中です / 지금 처리 중입니다")
        _need_open(d)
        thread_id = _need_thread(d)
        open_v = conn.execute(f"SELECT {_VCOLS} FROM validation_violations "
                              "WHERE draft_id = %s AND allowed_by IS NULL ORDER BY violation_id", (draft_id,)).fetchall()
        if open_v:
            raise ApiError(409, "APPROVAL_BLOCKED", "未解決の違反があるため承認できません / 미해결 위반이 있어 승인할 수 없습니다",
                           {"violations": open_v})
        conn.execute("INSERT INTO manager_actions (draft_id, manager_id, action_type) VALUES (%s,%s,'approve')",
                     (draft_id, me["manager_id"]))      # 二重承認はDBのUNIQUEで409 / 이중 승인은 DB UNIQUE로 409
        conn.execute("UPDATE shift_drafts SET status = 'confirmed', confirmed_at = now(), confirmed_by = %s "
                     "WHERE draft_id = %s", (me["manager_id"], draft_id))
        graph = gw.resume(thread_id, "approved")
    return {"draft_id": draft_id, "status": "confirmed", "graph": graph}


class RejectIn(BaseModel):
    instruction: str = Field(default="", max_length=1000)


@router.post("/admin/drafts/{draft_id}/reject", summary="案の差し戻し(再編成の依頼) / 안 반려(재편성 요청)")
def reject(draft_id: int, body: RejectIn, me: dict = Depends(current_manager),
           gw: GraphGateway = Depends(get_gateway)):
    """店長の指示文つきで再編成を頼む。回数の上限はグラフ側で数える（API側では数えない）。
    점장의 지시문과 함께 재편성을 요청한다. 횟수 상한은 그래프 쪽에서 센다(API에서는 세지 않는다)."""
    instruction = body.instruction.strip()
    with tx() as conn:
        d = _draft(conn, draft_id, me["store_id"])
        if not try_lock_period(conn, d["period_id"]):
            raise ApiError(409, "BUSY", "今、処理中です / 지금 처리 중입니다")
        _need_open(d)
        thread_id = _need_thread(d)
        conn.execute("INSERT INTO manager_actions (draft_id, manager_id, action_type, instruction_text) "
                     "VALUES (%s,%s,'redraft_request',%s)", (draft_id, me["manager_id"], instruction or None))
        graph = gw.resume(thread_id, {"decision": "redraft_requested", "instruction": instruction})
    return {"next": "recompose", "graph": graph}


class EditItem(BaseModel):
    staff_id: int
    date: date
    start_time: time | None = None      # 両方 null = その日の配置を削除 / 둘 다 null = 그날 배치를 삭제
    end_time: time | None = None

    @model_validator(mode="after")
    def _check(self):
        if (self.start_time is None) != (self.end_time is None):
            raise ValueError("開始と終了は両方入れるか両方空 / 시작과 종료는 둘 다 넣거나 둘 다 비운다")
        if self.start_time is not None:
            if self.end_time <= self.start_time:
                raise ValueError("終了は開始より後 / 종료는 시작보다 뒤")
            for t in (self.start_time, self.end_time):
                if t.minute not in (0, 30) or t.second:
                    raise ValueError("時刻は30分単位 / 시각은 30분 단위")
        return self


class EditIn(BaseModel):
    assignments: list[EditItem] = Field(min_length=1)
    reason: str | None = Field(default=None, max_length=500)


@router.put("/admin/drafts/{draft_id}/assignments", summary="配置の手修正 / 배치 수동 수정")
def edit_assignments(draft_id: int, body: EditIn | list[EditItem], me: dict = Depends(current_manager),
                     gw: GraphGateway = Depends(get_gateway)):
    """修正だけを受け取り、DBの現在の配置に重ねて「全体のリスト」を作り、グラフに edit_saved で渡す。
    再検証・DB保存はグラフ側（validate → 保存）。確定後は 409。
    수정분만 받아 DB의 현재 배치에 겹쳐 「전체 리스트」를 만들고, 그래프에 edit_saved로 넘긴다.
    재검증・DB 저장은 그래프 쪽(validate → 저장). 확정 후는 409."""
    edits = body.assignments if isinstance(body, EditIn) else body
    if not edits:
        raise ApiError(422, "VALIDATION_ERROR", "修正が空です / 수정이 비어 있습니다")
    with tx() as conn:
        d = _draft(conn, draft_id, me["store_id"])
        if not try_lock_period(conn, d["period_id"]):
            raise ApiError(409, "BUSY", "今、処理中です / 지금 처리 중입니다")
        _need_open(d)
        thread_id = _need_thread(d)

        # 従業員は自分の店の人だけ / 직원은 자기 점포 사람만
        ids = sorted({e.staff_id for e in edits})
        found = {r["employee_id"] for r in conn.execute(
            "SELECT employee_id FROM employees WHERE store_id = %s AND employee_id = ANY(%s)",
            (me["store_id"], ids)).fetchall()}
        if set(ids) - found:
            raise ApiError(422, "UNKNOWN_STAFF", "存在しない従業員が含まれています / 존재하지 않는 직원이 포함되어 있습니다")
        # 日付は対象月だけ / 날짜는 대상 월만
        m = d["target_month"]
        if any((e.date.year, e.date.month) != (m.year, m.month) for e in edits):
            raise ApiError(422, "DATE_OUT_OF_PERIOD", "対象月の日付だけ修正できます / 대상 월의 날짜만 수정할 수 있습니다")

        cur = conn.execute(
            "SELECT employee_id, work_date, start_time, end_time FROM shift_assignments "
            "WHERE draft_id = %s AND kind = 'work'", (draft_id,)).fetchall()
        merged = {(r["employee_id"], r["work_date"]): (r["start_time"], r["end_time"]) for r in cur}
        for e in edits:
            if e.start_time is None:
                merged.pop((e.staff_id, e.date), None)
            else:
                merged[(e.staff_id, e.date)] = (e.start_time, e.end_time)
        full = [{"staff_id": k[0], "date": k[1].isoformat(), "start": v[0].strftime("%H:%M"), "end": v[1].strftime("%H:%M")}
                for k, v in sorted(merged.items(), key=lambda kv: (kv[0][1], kv[0][0]))]

        conn.execute("INSERT INTO manager_actions (draft_id, manager_id, action_type) VALUES (%s,%s,'edit_save')",
                     (draft_id, me["manager_id"]))
        graph = gw.resume(thread_id, {"decision": "edit_saved", "assignments": full})
    return {"saved": len(edits), "total_assignments": len(full), "graph": graph}


@router.get("/admin/drafts/{draft_id}/edits", summary="修正履歴 / 수정 이력")
def list_edits(draft_id: int, me: dict = Depends(current_manager)):
    """専用の修正ログ表は無いので manager_actions の記録を返す（誰が・いつ・何をしたか）。
    전용 수정 로그 테이블이 없으므로 manager_actions의 기록을 돌려준다(누가・언제・무엇을 했는가)."""
    with tx() as conn:
        _draft(conn, draft_id, me["store_id"])
        return conn.execute(
            "SELECT action_id, manager_id, action_type, instruction_text, created_at FROM manager_actions "
            "WHERE draft_id = %s ORDER BY created_at, action_id", (draft_id,)).fetchall()
