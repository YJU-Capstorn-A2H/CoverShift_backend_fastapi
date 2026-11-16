# app/routers/store.py
# 店舗・時間帯ルール / 점포・시간대 규칙（仕様書 2-3）
from datetime import time

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field, model_validator

from app.core.db import tx
from app.core.deps import current_manager
from app.core.errors import ApiError

router = APIRouter()


# ---------- 店舗 / 점포 ----------
class StorePatch(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.get("/store", summary="店舗の取得 / 점포 조회")
def get_store(me: dict = Depends(current_manager)):
    with tx() as conn:
        return conn.execute("SELECT store_id, name FROM stores WHERE store_id = %s", (me["store_id"],)).fetchone()


@router.patch("/store", summary="店舗の更新 / 점포 수정")
def patch_store(body: StorePatch, me: dict = Depends(current_manager)):
    with tx() as conn:
        return conn.execute("UPDATE stores SET name = %s WHERE store_id = %s RETURNING store_id, name",
                            (body.name, me["store_id"])).fetchone()


# ---------- 時間帯ルール / 시간대 규칙 ----------
# 要日×時間帯。時間帯の重なりはDB(EXCLUDE)が拒否 → 409
# 요일×시간대. 시간대 겹침은 DB(EXCLUDE)가 거부 → 409
class RuleIn(BaseModel):
    weekday: int = Field(ge=1, le=7)                    # 1=月 ... 7=日 / 1=월 ... 7=일
    start_time: time
    end_time: time
    min_headcount: int = Field(ge=1)                    # 休憩込みの必要人数 / 휴식 포함 필요 인원
    min_qualified: int = Field(default=0, ge=0)         # 必要な資格者数（基準文書 11/07）/ 필요한 자격자 수

    @model_validator(mode="after")
    def _order(self):
        if self.end_time <= self.start_time:
            raise ValueError("終了は開始より後 / 종료는 시작보다 뒤")
        return self


class RulePatch(BaseModel):
    weekday: int | None = Field(default=None, ge=1, le=7)
    start_time: time | None = None
    end_time: time | None = None
    min_headcount: int | None = Field(default=None, ge=1)
    min_qualified: int | None = Field(default=None, ge=0)


class CopyIn(BaseModel):
    from_weekday: int = Field(ge=1, le=7)
    to_weekdays: list[int] = Field(min_length=1)


_COLS = "rule_id, weekday, start_time, end_time, min_headcount, min_qualified"


@router.get("/store/time-rules", summary="時間帯ルールの一覧 / 시간대 규칙 목록")
def list_rules(weekday: int | None = None, me: dict = Depends(current_manager)):
    sql = f"SELECT {_COLS} FROM store_time_rules WHERE store_id = %s"
    params: list = [me["store_id"]]
    if weekday is not None:
        sql += " AND weekday = %s"
        params.append(weekday)
    with tx() as conn:
        return conn.execute(sql + " ORDER BY weekday, start_time", params).fetchall()


@router.post("/store/time-rules", status_code=201, summary="時間帯ルールの登録 / 시간대 규칙 등록")
def create_rule(body: RuleIn, me: dict = Depends(current_manager)):
    with tx() as conn:
        return conn.execute(
            f"INSERT INTO store_time_rules (store_id, weekday, start_time, end_time, min_headcount, min_qualified) "
            f"VALUES (%s,%s,%s,%s,%s,%s) RETURNING {_COLS}",
            (me["store_id"], body.weekday, body.start_time, body.end_time, body.min_headcount, body.min_qualified),
        ).fetchone()


@router.post("/store/time-rules/copy", summary="曜日ルールの複製 / 요일 규칙 복사")
def copy_rules(body: CopyIn, me: dict = Depends(current_manager)):
    """ある曜日の規則を別の曜日にコピーする。重なる曜日があれば全体を取り消して 409。
    어떤 요일의 규칙을 다른 요일에 복사한다. 겹치는 요일이 있으면 전체를 취소하고 409."""
    targets = [w for w in dict.fromkeys(body.to_weekdays) if w != body.from_weekday]
    if any(w < 1 or w > 7 for w in targets):
        raise ApiError(422, "VALIDATION_ERROR", "曜日は1〜7 / 요일은 1~7")
    with tx() as conn:
        src = conn.execute(
            "SELECT start_time, end_time, min_headcount, min_qualified FROM store_time_rules "
            "WHERE store_id = %s AND weekday = %s", (me["store_id"], body.from_weekday)).fetchall()
        if not src:
            raise ApiError(404, "NOT_FOUND", "コピー元の規則がありません / 복사 원본 규칙이 없습니다")
        n = 0
        for w in targets:
            for r in src:
                conn.execute(
                    "INSERT INTO store_time_rules (store_id, weekday, start_time, end_time, min_headcount, min_qualified) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (me["store_id"], w, r["start_time"], r["end_time"], r["min_headcount"], r["min_qualified"]))
                n += 1
    return {"copied": n}


@router.patch("/store/time-rules/{rule_id}", summary="時間帯ルールの更新 / 시간대 규칙 수정")
def patch_rule(rule_id: int, body: RulePatch, me: dict = Depends(current_manager)):
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise ApiError(422, "VALIDATION_ERROR", "変更する項目がありません / 변경할 항목이 없습니다")
    sets = ", ".join(f"{k} = %s" for k in changes)        # キーはモデルで固定済み / 키는 모델에서 고정됨
    with tx() as conn:
        row = conn.execute(
            f"UPDATE store_time_rules SET {sets} WHERE rule_id = %s AND store_id = %s RETURNING {_COLS}",
            [*changes.values(), rule_id, me["store_id"]]).fetchone()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "規則が見つかりません / 규칙을 찾을 수 없습니다")
    return row


@router.delete("/store/time-rules/{rule_id}", status_code=204, summary="時間帯ルールの削除 / 시간대 규칙 삭제")
def delete_rule(rule_id: int, me: dict = Depends(current_manager)):
    # store_task_needs は CASCADE で一緒に消える / store_task_needs는 CASCADE로 함께 삭제된다
    with tx() as conn:
        n = conn.execute("DELETE FROM store_time_rules WHERE rule_id = %s AND store_id = %s",
                         (rule_id, me["store_id"])).rowcount
    if n == 0:
        raise ApiError(404, "NOT_FOUND", "規則が見つかりません / 규칙을 찾을 수 없습니다")
    return Response(status_code=204)
