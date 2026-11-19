"""편성 제어 (G1, M-1, M-3, M-5, A-14, A-15, F-7, F-10) — API 는 러너에 넘기기만 한다. 그래프 상태는 러너가 갖는다.

러너 계약 (API_CONTRACT 「러너 계약」): start / status / resume, Busy → 409 BUSY, NotWaiting → 409 NOT_WAITING.
겹치는 요청은 입구 잠금(deps.entrance_lock)이 먼저 막고, 러너의 Busy 가 한 번 더 막는다 (V9_ko 505, 509).
"""
from __future__ import annotations

import secrets

from app.privacy import mask_names, store_employees

from fastapi import APIRouter

from app.repo import draft_view
from app.repo import employees as emp_repo
from app.repo import line as line_repo
from app.repo import periods as period_repo

from .deps import AlreadyStarted, Busy, Conn, Manager, NotWaiting, Runner, StaleDraft, entrance_lock
from .errors import BUSY_MESSAGE, ApiError, not_found
from .routes_settings import load_period
from .runstate import status_of
from .schemas import EditIn, RedraftIn, ReplyIn, ReviewBody
from .serialize import out

router = APIRouter(prefix="/api", tags=["control"])


def lock_name(period_id: int) -> str:
    """입구 잠금 이름. 기간마다 고정 (A-15, V9_ko 509). 지금 시작 · review · 답장이 같은 이름으로 줄을 선다."""
    return f"period-{period_id}"


def new_thread_id(period_id: int) -> str:
    """F-10: 기간 1개 = thread 1개 (V9_ko 98, 275). 임의 꼬리를 붙인다 (G22):
    앱 표를 비우거나 DB 를 다시 만들어 period_id 가 1부터 다시 나와도, 남아 있는 옛 체크포인트 thread 와 부딪히지 않게."""
    return f"period-{period_id}-{secrets.token_hex(4)}"


def _busy() -> ApiError:
    return ApiError(409, "BUSY", BUSY_MESSAGE)


def _already_started() -> ApiError:
    return ApiError(409, "ALREADY_STARTED", "この期間は、すでに編成を開始しています")


def _stale() -> ApiError:
    return ApiError(409, "STALE_DRAFT", "画面の案が最新ではありません。再読み込みしてから、もう一度確認してください")


def _not_waiting(msg: str = "いまは承認待ちの状態ではありません") -> ApiError:
    return ApiError(409, "NOT_WAITING", msg)


@router.post("/shift-periods/{period_id}/start-now", status_code=202)
def start_now(period_id: int, m: Manager, conn: Conn, runner: Runner):
    """지금 시작 (V9_ko 54 · 80, A-14). JWT 확인 뒤에만 (Manager 의존성).

    순서: 잠금 → (잠근 뒤) 기간 다시 읽기 → thread_id 저장 · commit → runner.start.
    thread_id 를 먼저 commit 하는 이유: 러너가 돌기 시작한 뒤 이 요청이 실패해도 DB 에서 thread 를 잃지 않게.
    러너가 not_started 라고 하면(전에 저장만 되고 시작이 실패한 경우) 같은 thread 로 다시 시작할 수 있다.
    """
    load_period(conn, m, period_id)
    with entrance_lock(conn, lock_name(period_id)):
        p = load_period(conn, m, period_id)  # 잠금 뒤에 다시 읽는다: 앞 요청이 방금 thread_id 를 넣었을 수 있다
        if p["thread_id"] is not None:
            if runner.status(period_id, p["thread_id"]).get("state") != "not_started":
                raise _already_started()
            thread_id = p["thread_id"]
        else:
            thread_id = new_thread_id(period_id)
            period_repo.set_thread_id(conn, period_id, thread_id)
            conn.commit()
        try:
            st = runner.start(period_id, thread_id)
        except Busy:
            raise _busy()
        except AlreadyStarted:  # 러너가 체크포인트를 보고 막은 경우 (위 확인과 러너 확인 사이의 틈)
            raise _already_started()
    return out(st)


@router.get("/shift-periods/{period_id}/run-status")
def run_status(period_id: int, m: Manager, conn: Conn, runner: Runner):
    st = dict(status_of(runner, load_period(conn, m, period_id)))
    st["confirmed_draft_id"] = period_repo.confirmed_draft_id(conn, period_id)  # 확정 뒤에도 확정표를 열 수 있게
    return out(st)


@router.post("/shift-periods/{period_id}/review", status_code=202)
def review(period_id: int, body: ReviewBody, m: Manager, conn: Conn, runner: Runner):
    """승인 · 재편성 요청 · 편집 저장 (F-7, M-3). manager_id 는 본문이 아니라 JWT 에서 (V9_ko 54 「승인자 기록」).

    본 안(draft_id)이 지금 기다리는 안과 다르면 409 STALE_DRAFT — 점장이 옛 화면을 보고 승인하는 것을 막는다.
    """
    p = load_period(conn, m, period_id)
    if p["thread_id"] is None:
        raise _not_waiting("まだ編成を開始していません")
    if isinstance(body, EditIn):
        _check_edit(conn, m.store_id, p, body)
    with entrance_lock(conn, lock_name(period_id)):
        st = runner.status(period_id, p["thread_id"])
        if st.get("state") == "running":
            raise _busy()
        waiting = st.get("waiting") or {}
        if st.get("state") != "waiting_review" or waiting.get("type") != "review":
            raise _not_waiting()
        if waiting.get("draft_id") != body.draft_id:
            raise _stale()
        payload = {"decision": body.decision, "manager_id": m.manager_id, "draft_id": body.draft_id}
        if isinstance(body, RedraftIn):
            payload["instruction"] = mask_names(body.instruction, store_employees(conn, m.store_id))  # 리뷰 A #5
        elif isinstance(body, EditIn):
            payload["assignments"] = out([a.model_dump() for a in body.assignments])
        else:
            payload["waived_keys"] = list(body.waived_keys)
        try:
            st = runner.resume(period_id, p["thread_id"], payload)
        except Busy:
            raise _busy()
        except StaleDraft:  # NotWaiting 의 하위 클래스라 먼저 잡는다
            raise _stale()
        except NotWaiting:
            raise _not_waiting()
    return out(st)


def _check_edit(conn, store_id: int, period: dict, body: EditIn) -> None:
    """편집안의 직원 · 날짜가 이 점포 · 이 달인지 (A-5). 규칙 검증은 그래프가 다시 돌린다 (F-15)."""
    ids = emp_repo.employee_ids(conn, store_id)
    start, end = period_repo.month_range(period["target_month"])
    seen = set()
    for i, a in enumerate(body.assignments):
        if a.employee_id not in ids:
            raise ApiError(422, "INVALID_ITEM", f"assignments[{i}]: employee_id {a.employee_id} はこの店舗の従業員ではありません")
        if not (start <= a.work_date < end):
            raise ApiError(422, "INVALID_ITEM", f"assignments[{i}]: {a.work_date} はこの期間の日付ではありません")
        if (a.employee_id, a.work_date) in seen:
            # shift_assignments PK(draft, employee, date) — 1명 1일 1행 (V9_ko 336)
            raise ApiError(422, "INVALID_ITEM", f"assignments[{i}]: 同じ従業員・同じ日が2回あります")
        seen.add((a.employee_id, a.work_date))


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: int, m: Manager, conn: Conn):
    d = draft_view.get_draft(conn, m.store_id, draft_id)
    if d is None:
        raise not_found("案")
    return out(d)


# ---------------------------------------------------------------- 직원 답장 (M-5, G16)

@router.post("/line/reply", status_code=202)
def line_reply(body: ReplyIn, conn: Conn, runner: Runner):
    """답장 버튼 [가능][불가]. 관리자 JWT 가 아니라 contact_id + 그 직원의 line_user_id 로 확인한다 (G16).

    연락이 없거나 다른 사람 것이면 같은 403 — contact_id 를 바꿔 가며 남의 연락이 있는지 알아낼 수 없게.
    """
    c = line_repo.contact_for_reply(conn, body.contact_id)
    if c is None or c["line_user_id"] is None or c["line_user_id"] != body.line_user_id:
        raise ApiError(403, "NOT_YOUR_CONTACT", "この連絡には答えられません")
    if c["status"] != "asked":
        raise ApiError(409, "ALREADY_ANSWERED", "すでに答えた連絡です")
    if c["thread_id"] is None:
        raise _not_waiting("編成は進行中ではありません")
    with entrance_lock(conn, lock_name(c["period_id"])):
        st = runner.status(c["period_id"], c["thread_id"])
        if st.get("state") == "running":
            raise _busy()
        waiting = st.get("waiting") or {}
        if st.get("state") != "waiting_contact" or waiting.get("contact_id") != body.contact_id:
            raise _not_waiting("いまはこの連絡への返事を待っていません")
        try:
            st = runner.resume(c["period_id"], c["thread_id"], {"contact_id": body.contact_id, "answer": body.answer})
        except Busy:
            raise _busy()
        except NotWaiting:
            raise _not_waiting("いまはこの連絡への返事を待っていません")
    return out(st)
