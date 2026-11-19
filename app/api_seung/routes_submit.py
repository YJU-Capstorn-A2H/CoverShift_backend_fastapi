"""LINE 연결 · 제출 링크 · 직원 제출 · 발송함 (A-10 ~ A-13, A-17, W-7, W-8, V9_ko 155-165)."""
from __future__ import annotations

import hashlib
from datetime import date

from fastapi import APIRouter, Response

from app import line_stub
from app.config import get_settings
from app.repo import employees as emp_repo
from app.repo import line as line_repo
from app.repo import periods as period_repo
from app.repo import submissions as sub_repo

from .deps import Conn, Manager
from .errors import ApiError, not_found
from .routes_settings import load_period
from .schemas import AvailabilityItemIn, AvailabilityPut, LinkIn, check_slot
from .serialize import out

router = APIRouter(prefix="/api", tags=["submit"])

KINDS = ("available", "off", "paid_leave", "half_leave")  # V9_ko 165, db/001 CHECK
TIMED = ("available", "half_leave")  # 시각이 필요한 종류 (V9_ko 165)


def submit_url(token: str) -> str:
    # (가정 신규) 화면 경로 모양. 계약에는 API 경로(/api/submit/{token})만 있다 → 화면(W-7)과 맞춰야 한다
    return f"{get_settings().public_base_url.rstrip('/')}/submit/{token}"


def link_url(token: str) -> str:
    # (가정 신규) 같은 이유. 화면이 이 경로에서 POST /api/line/link 를 부른다
    return f"{get_settings().public_base_url.rstrip('/')}/line/link/{token}"


# ---------------------------------------------------------------- LINE 연결 (A-13)

@router.post("/employees/{employee_id}/link-token")
def issue_link_token(employee_id: int, m: Manager, conn: Conn):
    e = emp_repo.get_employee(conn, m.store_id, employee_id)
    if e is None:
        raise not_found("従業員")
    if e["line_linked"]:
        # (가정 신규) 이미 연결된 직원에게 새 토큰을 주면 다른 LINE 계정으로 덮어쓸 길이 생긴다 → 거부
        raise ApiError(409, "LINE_ALREADY_LINKED", "この従業員はすでにLINEに連携されています")
    token, expires_at = line_repo.issue_link_token(conn, m.store_id, employee_id)
    url = link_url(token)
    # 발급할 때마다 새 메시지 1건 (토큰이 바뀌므로 dedupe_key 도 바뀐다). 토큰 원문은 키에 넣지 않는다
    line_stub.send(conn, store_id=m.store_id, employee_id=employee_id, kind="link_token",
                   dedupe_key=f"link_token:{employee_id}:{hashlib.sha256(token.encode()).hexdigest()[:16]}",
                   body={"text": "下のリンクを開いて、LINEを連携してください", "url": url,
                         "expires_at": expires_at.isoformat()})
    return {"link_url": url, "expires_at": expires_at.isoformat()}


@router.post("/line/link")
def line_link(body: LinkIn, conn: Conn):
    """토큰 인증 (V9_ko 159). 토큰 없음 · 만료 · 이미 쓴 토큰은 같은 400. 다른 직원이 쓰는 line_user_id 는 409."""
    e = line_repo.find_by_link_token(conn, body.link_token)
    if e is None:
        raise ApiError(400, "INVALID_TOKEN", "連携リンクが正しくないか、期限が切れています")
    line_repo.link(conn, e["employee_id"], body.line_user_id)  # UNIQUE 위반 → 409 LINE_ALREADY_LINKED (errors.py)
    return {"employee_id": e["employee_id"]}


# ---------------------------------------------------------------- 제출 링크 일괄 발급 (A-10)

@router.post("/shift-periods/{period_id}/submission-links")
def submission_links(period_id: int, m: Manager, conn: Conn):
    """직원마다 링크 1개 (있으면 같은 링크). LINE 스텁 발송은 (직원, 기간)당 1번 (dedupe_key).

    (가정 신규) LINE 이 연결된 직원에게만 스텁 발송을 기록한다 — 연결 안 된 직원은 실제 LINE 이면 보낼 곳이 없다.
    연결 뒤 다시 부르면 그 직원에게만 새로 기록된다. 링크 자체는 연결 여부와 관계없이 전원에게 만든다(점장이 직접 전달 가능).
    """
    p = load_period(conn, m, period_id)
    links, sent = [], 0
    for r in sub_repo.ensure_links(conn, m.store_id, period_id):
        url = submit_url(r["token"])
        links.append({"employee_id": r["employee_id"], "url": url})
        if r["line_linked"]:
            sent += line_stub.send(
                conn, store_id=m.store_id, employee_id=r["employee_id"], kind="submission_link",
                dedupe_key=f"submission_link:{period_id}:{r['employee_id']}",
                body={"text": f"{p['target_month']:%Y-%m} 勤務希望を提出してください", "url": url,
                      "submission_deadline": p["submission_deadline"].isoformat()})
    return {"links": links, "sent": sent}


# ---------------------------------------------------------------- 직원 제출 (A-11, A-12)

def _open_link(conn, token: str, *, for_update: bool = False) -> dict:
    link = sub_repo.by_token(conn, token, for_update=for_update)
    if link is None:
        raise ApiError(404, "NOT_FOUND", "提出リンクが見つかりません")
    if link["closed"]:
        raise ApiError(410, "SUBMISSION_CLOSED", "提出期限が過ぎています")  # V9_ko 163
    return link


def _page(conn, link: dict) -> dict:
    return out({
        "employee_name": link["employee_name"], "is_regular": link["is_regular"],
        "target_month": link["target_month"], "submission_deadline": link["submission_deadline"],
        "off_request_limit": link["off_request_limit"], "work_request_limit": link["work_request_limit"],
        "items": sub_repo.items(conn, link["employee_id"], link["target_month"]),
    })


@router.get("/submit/{token}")
def submit_page(token: str, conn: Conn):
    """몇 번 열어도 같은 데이터 (V9_ko 163) — 읽기만 하고 아무것도 바꾸지 않는다."""
    return _page(conn, _open_link(conn, token))


def _invalid(msg: str) -> ApiError:
    return ApiError(422, "INVALID_ITEM", msg)


def _check_item(it: AvailabilityItemIn, start: date, end: date) -> dict:
    """V9_ko 165: available · half_leave 만 시각 필수, off · paid_leave 는 시각 없음, 30분 단위, 그 달 안."""
    if it.kind not in KINDS:
        raise _invalid(f"不明な種類です: {it.kind}")
    if not (start <= it.work_date < end):
        raise _invalid(f"{it.work_date} はこの月の日付ではありません")
    if it.kind in TIMED:
        if it.start_time is None or it.end_time is None:
            raise _invalid(f"{it.work_date}: {it.kind} には開始・終了時刻が必要です")
        try:
            check_slot(it.start_time)
            check_slot(it.end_time)
        except ValueError as e:
            raise _invalid(f"{it.work_date}: {e}")
        if it.end_time <= it.start_time:
            raise _invalid(f"{it.work_date}: 終了時刻は開始時刻より後にしてください")
    elif it.start_time is not None or it.end_time is not None:
        # 조용히 버리지 않고 거부한다 — 화면이 잘못 보낸 것을 알 수 있게
        raise _invalid(f"{it.work_date}: {it.kind} には時刻を入れません")
    # (가정 신규) 파트 · 알바의 paid_leave 도 받는다. 허용 여부 미결(V9_ko 437) — API 로 막지 않고 검증 · 점장 판단에 맡긴다
    return it.model_dump()


@router.put("/submit/{token}/availability")
def put_availability(token: str, body: AvailabilityPut, conn: Conn):
    """보낸 날짜만 UPSERT (V9_ko 165). 상한은 저장 뒤 그 달 전체를 세어 넘으면 422 → 트랜잭션 통째로 취소(부분 저장 없음)."""
    link = _open_link(conn, token, for_update=True)
    start, end = period_repo.month_range(link["target_month"])
    seen: set[date] = set()
    items = []
    for it in body.items:
        if it.work_date in seen:
            raise _invalid(f"{it.work_date} が2回入っています")
        seen.add(it.work_date)
        items.append(_check_item(it, start, end))
    for it in items:
        sub_repo.upsert(conn, link["employee_id"], it)
    if link["is_regular"]:
        # 사원만 센다. half_leave · paid_leave 는 세지 않는다 (V9_ko 165 「half_leave 는 세지 않는다」)
        n = sub_repo.count_kinds(conn, link["employee_id"], link["target_month"])
        if link["off_request_limit"] is not None and n.get("off", 0) > link["off_request_limit"]:
            raise ApiError(422, "LIMIT_EXCEEDED",
                           f"希望休は{link['off_request_limit']}日までです(いま{n.get('off', 0)}日)")
        if link["work_request_limit"] is not None and n.get("available", 0) > link["work_request_limit"]:
            raise ApiError(422, "LIMIT_EXCEEDED",
                           f"出勤希望は{link['work_request_limit']}日までです(いま{n.get('available', 0)}日)")
    return {"items": out(sub_repo.items(conn, link["employee_id"], link["target_month"]))}


@router.delete("/submit/{token}/availability/{work_date}", status_code=204)
def delete_availability(token: str, work_date: date, conn: Conn):
    link = _open_link(conn, token, for_update=True)
    start, end = period_repo.month_range(link["target_month"])
    if not (start <= work_date < end):
        raise _invalid(f"{work_date} はこの月の日付ではありません")  # 다른 달 희망은 이 링크로 지우지 못한다
    sub_repo.delete(conn, link["employee_id"], work_date)
    return Response(status_code=204)


# ---------------------------------------------------------------- 발송함 (A-17, W-8)

@router.get("/dev/line-outbox")
def line_outbox(m: Manager, conn: Conn, employee_id: int | None = None):
    return out(line_repo.outbox(conn, m.store_id, employee_id))
