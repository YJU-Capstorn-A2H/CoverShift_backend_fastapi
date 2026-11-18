# app/routers/periods.py
# 月の設定・提出リンク・編成開始 / 월 설정・제출 링크・편성 시작
import secrets
from datetime import date

from fastapi import APIRouter, Depends
from pydantic import AwareDatetime, BaseModel, Field, field_validator

from app.core.tx import try_lock_period, tx
from app.core.deps import current_manager
from app.core.errors import ApiError
from app.services.graph_gateway import GraphGateway, get_gateway
from app.services.line_stub import send_line

router = APIRouter()

_COLS = ("period_id, target_month, submission_deadline, designated_off_days, off_request_limit, "
         "work_request_limit, thread_id")


class PeriodIn(BaseModel):
    target_month: date                                      # 月の1日（例 2026-12-01）/ 월의 1일
    submission_deadline: AwareDatetime                      # 希望の提出期限（タイムゾーン付き）/ 희망 제출 기한(시간대 포함)
    designated_off_days: int = Field(ge=0, le=31)           # 社員1人あたりの月の公休日数 / 정직원 1명당 월 공휴일수
    off_request_limit: int | None = Field(default=None, ge=0, le=31)    # 空=制限なし / 빈칸=제한 없음
    work_request_limit: int | None = Field(default=None, ge=0, le=31)

    @field_validator("target_month")
    @classmethod
    def _first_day(cls, v):
        if v.day != 1:
            raise ValueError("target_month は月の1日 / target_month는 월의 1일")
        return v


class PeriodPatch(BaseModel):
    submission_deadline: AwareDatetime | None = None
    designated_off_days: int | None = Field(default=None, ge=0, le=31)
    off_request_limit: int | None = Field(default=None, ge=0, le=31)
    work_request_limit: int | None = Field(default=None, ge=0, le=31)


# 仕様書V9 2-3 設定・提出のCRUD API｜期間の一覧・登録（MVP） / 명세서 V9 2-3 설정・제출 CRUD API｜기간 목록・등록(MVP)
@router.get("/shift-periods", summary="月の設定の一覧 / 월 설정 목록")
def list_periods(me: dict = Depends(current_manager)):
    with tx() as conn:
        return conn.execute(f"SELECT {_COLS} FROM shift_periods WHERE store_id = %s ORDER BY target_month DESC",
                            (me["store_id"],)).fetchall()


# 仕様書V9 2-3 設定・提出のCRUD API｜期間の一覧・登録（MVP） / 명세서 V9 2-3 설정・제출 CRUD API｜기간 목록・등록(MVP)
@router.post("/shift-periods", status_code=201, summary="月の設定の登録 / 월 설정 등록")
def create_period(body: PeriodIn, me: dict = Depends(current_manager)):
    with tx() as conn:        # 同じ月は1つだけ（UNIQUE → 409）/ 같은 달은 하나뿐(UNIQUE → 409)
        return conn.execute(
            f"INSERT INTO shift_periods (store_id, target_month, submission_deadline, designated_off_days, "
            f"off_request_limit, work_request_limit) VALUES (%s,%s,%s,%s,%s,%s) RETURNING {_COLS}",
            (me["store_id"], body.target_month, body.submission_deadline, body.designated_off_days,
             body.off_request_limit, body.work_request_limit)).fetchone()


# 仕様書V9 2-3 設定・提出のCRUD API｜期間の更新（MVP） / 명세서 V9 2-3 설정・제출 CRUD API｜기간 수정(MVP)
@router.patch("/shift-periods/{period_id}", summary="月の設定の更新 / 월 설정 수정")
def patch_period(period_id: int, body: PeriodPatch, me: dict = Depends(current_manager)):
    # 締切を変えると提出リンクの有効期限も変わる（リンクに別の期限は無い）
    # 마감을 바꾸면 제출 링크의 유효 기한도 바뀐다(링크에 별도 기한은 없다)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise ApiError(422, "VALIDATION_ERROR", "変更する項目がありません / 변경할 항목이 없습니다")
    sets = ", ".join(f"{k} = %s" for k in changes)
    with tx() as conn:
        row = conn.execute(f"UPDATE shift_periods SET {sets} WHERE period_id = %s AND store_id = %s RETURNING {_COLS}",
                           [*changes.values(), period_id, me["store_id"]]).fetchone()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "月の設定が見つかりません / 월 설정을 찾을 수 없습니다")
    return row


# 仕様書V9 2-2 認証API｜提出リンクの一括発行・送信 / 명세서 V9 2-2 인증 API｜제출 링크 일괄 발급・발송
@router.post("/shift-periods/{period_id}/submission-links", status_code=201,
             summary="提出リンクの一括発行・発送 / 제출 링크 일괄 발급・발송")
def issue_submission_links(period_id: int, me: dict = Depends(current_manager)):
    """全従業員の提出リンクを作り、LINE連携済みの人にはLINE(スタブ)で送る。既にあれば同じリンクを使う。
    전 직원의 제출 링크를 만들고, LINE 연동 완료자에게는 LINE(스텁)으로 보낸다. 이미 있으면 같은 링크를 쓴다."""
    with tx() as conn:
        period = conn.execute("SELECT period_id, target_month FROM shift_periods WHERE period_id = %s AND store_id = %s",
                              (period_id, me["store_id"])).fetchone()
        if period is None:
            raise ApiError(404, "NOT_FOUND", "月の設定が見つかりません / 월 설정을 찾을 수 없습니다")
        emps = conn.execute("SELECT employee_id, line_user_id FROM employees WHERE store_id = %s ORDER BY employee_id",
                            (me["store_id"],)).fetchall()
        created = 0
        for e in emps:
            n = conn.execute(
                "INSERT INTO submission_links (token, employee_id, period_id) VALUES (%s,%s,%s) "
                "ON CONFLICT (employee_id, period_id) DO NOTHING",
                (secrets.token_urlsafe(24), e["employee_id"], period_id)).rowcount
            created += n
        links = conn.execute(
            "SELECT employee_id, token FROM submission_links WHERE period_id = %s ORDER BY employee_id",
            (period_id,)).fetchall()
        by_emp = {e["employee_id"]: e["line_user_id"] for e in emps}
        sent, skipped = 0, []
        for link in links:
            lu = by_emp.get(link["employee_id"])
            if lu:
                send_line(lu, f"{period['target_month']:%Y年%m月}分の希望を提出してください / 희망을 제출해 주세요: /submit/{link['token']}")
                sent += 1
            else:
                skipped.append(link["employee_id"])        # LINE未連携 / LINE 미연동
    return {"created": created, "reused": len(links) - created, "sent": sent,
            "skipped_no_line": skipped, "links": links}


# 仕様書V9 6章 002-管理者用｜編成を今すぐ開始 / 명세서 V9 6장 002-관리자용｜편성 즉시 시작
@router.post("/admin/shift-periods/{period_id}/start-now", status_code=202,
             summary="編成を今すぐ開始 / 편성을 지금 바로 시작")
def start_now(period_id: int, me: dict = Depends(current_manager), gw: GraphGateway = Depends(get_gateway)):
    """編成期間1つ = thread1つ（shift_periods.thread_id）。2回目は 409。
    グラフの開始に失敗したら thread_id も元に戻す（同じトランザクション）。
    편성 기간 1개 = thread 1개(shift_periods.thread_id). 두 번째는 409.
    그래프 시작에 실패하면 thread_id도 원래대로 되돌린다(같은 트랜잭션)."""
    thread_id = f"period-{period_id}"
    with tx() as conn:
        if not try_lock_period(conn, period_id):
            raise ApiError(409, "BUSY", "今、編成を処理しています / 지금 편성을 처리하고 있습니다")
        row = conn.execute("UPDATE shift_periods SET thread_id = %s WHERE period_id = %s AND store_id = %s "
                           "AND thread_id IS NULL RETURNING period_id", (thread_id, period_id, me["store_id"])).fetchone()
        if row is None:
            exists = conn.execute("SELECT 1 FROM shift_periods WHERE period_id = %s AND store_id = %s",
                                  (period_id, me["store_id"])).fetchone()
            if exists:
                raise ApiError(409, "ALREADY_STARTED", "既に開始されています / 이미 시작되었습니다")
            raise ApiError(404, "NOT_FOUND", "月の設定が見つかりません / 월 설정을 찾을 수 없습니다")
        gw.start(thread_id, me["store_id"], period_id)       # 失敗(例外)ならここで全部ロールバック / 실패(예외)면 여기서 전부 롤백
    return {"thread_id": thread_id}
