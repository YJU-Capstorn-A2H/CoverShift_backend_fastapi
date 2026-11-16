# app/routers/public.py
# リンク（トークン）で使うAPI。ログイン不要。/ 링크(토큰)로 쓰는 API. 로그인 불필요.
#   GET  /submit/{token}               提出ページ用データ / 제출 페이지용 데이터
#   PUT  /submit/{token}/availability  希望の提出・修正(UPSERT) / 희망 제출・수정(UPSERT)
#   POST /line/link                    LINEアカウントの連携 / LINE 계정 연동
from datetime import date, time

from fastapi import APIRouter
from pydantic import BaseModel, model_validator

from app.core.tx import tx
from app.core.errors import ApiError

router = APIRouter()

KINDS = ("available", "off", "paid_leave", "half_leave")
NEEDS_TIME = ("available", "half_leave")     # 時刻が要る種類 / 시각이 필요한 종류


def _load_link(conn, token: str) -> dict:
    """トークンから 従業員＋月 を引く。無ければ404、締切後なら410。
    토큰으로 직원＋월을 찾는다. 없으면 404, 마감 후면 410."""
    row = conn.execute(
        "SELECT l.employee_id, l.period_id, e.name, e.is_regular, e.store_id, "
        "       p.target_month, p.submission_deadline, p.off_request_limit, p.work_request_limit, "
        "       (p.submission_deadline <= now()) AS closed "
        "FROM submission_links l "
        "JOIN employees e ON e.employee_id = l.employee_id "
        "JOIN shift_periods p ON p.period_id = l.period_id "
        "WHERE l.token = %s", (token,)).fetchone()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "リンクが正しくありません / 링크가 올바르지 않습니다")
    if row["closed"]:
        raise ApiError(410, "SUBMISSION_CLOSED", "提出期限が過ぎました / 제출 기한이 지났습니다")
    return row


@router.get("/submit/{token}", summary="提出ページ用データ / 제출 페이지 데이터")
def get_submit(token: str):
    with tx() as conn:
        link = _load_link(conn, token)
        rows = conn.execute(
            "SELECT work_date, kind, start_time, end_time FROM availability_submissions "
            "WHERE employee_id = %s AND work_date >= %s AND work_date < (%s::date + interval '1 month') "
            "ORDER BY work_date", (link["employee_id"], link["target_month"], link["target_month"])).fetchall()
    return {
        "employee": {"name": link["name"], "is_regular": link["is_regular"]},
        "period": {"target_month": link["target_month"], "submission_deadline": link["submission_deadline"],
                   "off_request_limit": link["off_request_limit"], "work_request_limit": link["work_request_limit"]},
        "submissions": rows,
    }


class SubmitIn(BaseModel):
    work_date: date
    kind: str
    start_time: time | None = None
    end_time: time | None = None

    @model_validator(mode="after")
    def _check(self):
        if self.kind not in KINDS:
            raise ValueError("kind は available/off/paid_leave/half_leave / kind는 이 4가지 중 하나")
        if self.kind in NEEDS_TIME:
            if self.start_time is None or self.end_time is None:
                raise ValueError("この種類は時刻が必要 / 이 종류는 시각이 필요합니다")
            if self.end_time <= self.start_time:
                raise ValueError("終了は開始より後 / 종료는 시작보다 뒤")
            for t in (self.start_time, self.end_time):
                if t.minute not in (0, 30) or t.second or t.microsecond:
                    raise ValueError("時刻は30分単位 / 시각은 30분 단위")
        elif self.start_time is not None or self.end_time is not None:
            raise ValueError("この種類に時刻は付けられない / 이 종류에는 시각을 붙일 수 없습니다")
        return self


@router.put("/submit/{token}/availability", summary="希望の提出・修正 / 희망 제출・수정")
def put_availability(token: str, body: SubmitIn):
    """同じ日の再入力は上書き。社員だけ希望休・出勤希望の上限を数える（半休は数えない）。
    같은 날 재입력은 덮어쓴다. 정직원만 희망휴・출근 희망 상한을 센다(반휴는 세지 않는다)."""
    with tx() as conn:
        link = _load_link(conn, token)
        first = link["target_month"]
        # 対象月の外の日付は受け付けない（提案）/ 대상 월 밖의 날짜는 받지 않는다(제안)
        if (body.work_date.year, body.work_date.month) != (first.year, first.month):
            raise ApiError(422, "DATE_OUT_OF_PERIOD", "対象月の日付だけ提出できます / 대상 월의 날짜만 제출할 수 있습니다")

        # 同じ従業員の同時提出で上限をすり抜けないよう、従業員の行を押さえる
        # 같은 직원의 동시 제출로 상한을 빠져나가지 못하도록 직원 행을 잠근다
        conn.execute("SELECT 1 FROM employees WHERE employee_id = %s FOR UPDATE", (link["employee_id"],))

        limit = {"off": link["off_request_limit"], "available": link["work_request_limit"]}.get(body.kind)
        if link["is_regular"] and limit is not None:
            cnt = conn.execute(
                "SELECT count(*) AS n FROM availability_submissions "
                "WHERE employee_id = %s AND kind = %s AND work_date >= %s "
                "AND work_date < (%s::date + interval '1 month') AND work_date <> %s",
                (link["employee_id"], body.kind, first, first, body.work_date)).fetchone()["n"]
            if cnt + 1 > limit:
                raise ApiError(422, "LIMIT_EXCEEDED",
                               f"上限({limit}日)を超えます / 상한({limit}일)을 넘습니다",
                               {"kind": body.kind, "limit": limit, "current": cnt})

        row = conn.execute(
            "INSERT INTO availability_submissions (employee_id, work_date, kind, start_time, end_time) "
            "VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT (employee_id, work_date) DO UPDATE "
            "SET kind = EXCLUDED.kind, start_time = EXCLUDED.start_time, end_time = EXCLUDED.end_time "
            "RETURNING work_date, kind, start_time, end_time",
            (link["employee_id"], body.work_date, body.kind, body.start_time, body.end_time)).fetchone()
    return row


class LineLinkIn(BaseModel):
    link_token: str
    line_user_id: str


@router.post("/line/link", summary="LINEアカウント連携 / LINE 계정 연동")
def line_link(body: LineLinkIn):
    """合言葉が有効なら line_user_id を保存し、合言葉を空にする（1回きり）。
    合言葉が違う・期限切れは同じ 400。すでに別の人に使われている LINE は 409。
    암호가 유효하면 line_user_id를 저장하고 암호를 비운다(1회용).
    암호가 틀리거나 만료됐으면 같은 400. 이미 다른 사람에게 쓰인 LINE은 409."""
    bad = ApiError(400, "INVALID_TOKEN", "連携リンクが正しくありません / 연동 링크가 올바르지 않습니다")
    with tx() as conn:
        row = conn.execute(
            "SELECT employee_id FROM employees WHERE link_token = %s AND link_token_expires_at > now() FOR UPDATE",
            (body.link_token,)).fetchone()
        if row is None:
            raise bad
        # UNIQUE違反は共通ハンドラで 409 になる / UNIQUE 위반은 공통 핸들러에서 409가 된다
        conn.execute(
            "UPDATE employees SET line_user_id = %s, link_token = NULL, link_token_expires_at = NULL "
            "WHERE employee_id = %s", (body.line_user_id, row["employee_id"]))
    return {"employee_id": row["employee_id"], "linked": True}
