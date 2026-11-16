# app/routers/employees.py
# 従業員・業務 / 직원・업무（仕様書 2-2・2-3）
import secrets

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field, model_validator

from app.core.config import settings
from app.core.tx import tx
from app.core.deps import current_manager
from app.core.errors import ApiError

router = APIRouter()

_COLS = ("employee_id, name, is_regular, has_qualification, closing_ok, weekly_days, fixed_weekdays, "
         "(line_user_id IS NOT NULL) AS line_linked")        # LINEのIDそのものは返さない / LINE ID 자체는 돌려주지 않는다


class EmployeeIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    is_regular: bool                                          # true=社員 / true=정직원
    has_qualification: bool = False                           # 登録販売者か / 등록판매자인지
    closing_ok: bool = False                                  # 22:30まで残ってよいか / 22:30까지 남아도 되는지
    weekly_days: int | None = Field(default=None, ge=1, le=7)
    fixed_weekdays: list[int] | None = None

    @model_validator(mode="after")
    def _days(self):
        # DBのCHECKと同じ規則を先に確認する / DB의 CHECK와 같은 규칙을 먼저 확인한다
        if self.fixed_weekdays is not None:
            if any(d < 1 or d > 7 for d in self.fixed_weekdays):
                raise ValueError("曜日は1〜7 / 요일은 1~7")
            if self.weekly_days is not None and self.weekly_days != len(self.fixed_weekdays):
                raise ValueError("weekly_days と fixed_weekdays の数が合いません / 개수가 맞지 않습니다")
        return self


class EmployeePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    is_regular: bool | None = None
    has_qualification: bool | None = None
    closing_ok: bool | None = None
    weekly_days: int | None = Field(default=None, ge=1, le=7)
    fixed_weekdays: list[int] | None = None


@router.get("/employees", summary="従業員の一覧 / 직원 목록")
def list_employees(me: dict = Depends(current_manager)):
    with tx() as conn:
        return conn.execute(f"SELECT {_COLS} FROM employees WHERE store_id = %s ORDER BY employee_id",
                            (me["store_id"],)).fetchall()


@router.post("/employees", status_code=201, summary="従業員の登録 / 직원 등록")
def create_employee(body: EmployeeIn, me: dict = Depends(current_manager)):
    with tx() as conn:
        return conn.execute(
            f"INSERT INTO employees (store_id, name, is_regular, has_qualification, closing_ok, weekly_days, fixed_weekdays) "
            f"VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING {_COLS}",
            (me["store_id"], body.name, body.is_regular, body.has_qualification, body.closing_ok,
             body.weekly_days, body.fixed_weekdays)).fetchone()


@router.get("/employees/{employee_id}", summary="従業員の取得 / 직원 조회")
def get_employee(employee_id: int, me: dict = Depends(current_manager)):
    with tx() as conn:
        row = conn.execute(f"SELECT {_COLS} FROM employees WHERE employee_id = %s AND store_id = %s",
                           (employee_id, me["store_id"])).fetchone()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "従業員が見つかりません / 직원을 찾을 수 없습니다")
    return row


@router.patch("/employees/{employee_id}", summary="従業員の更新 / 직원 수정")
def patch_employee(employee_id: int, body: EmployeePatch, me: dict = Depends(current_manager)):
    # 削除APIは無い（退職の扱いは未決）/ 삭제 API는 없다(퇴직 처리는 미결)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise ApiError(422, "VALIDATION_ERROR", "変更する項目がありません / 변경할 항목이 없습니다")
    sets = ", ".join(f"{k} = %s" for k in changes)
    with tx() as conn:
        row = conn.execute(
            f"UPDATE employees SET {sets} WHERE employee_id = %s AND store_id = %s RETURNING {_COLS}",
            [*changes.values(), employee_id, me["store_id"]]).fetchone()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "従業員が見つかりません / 직원을 찾을 수 없습니다")
    return row


@router.post("/employees/{employee_id}/link-token", status_code=201,
             summary="LINE連携リンクの発行 / LINE 연동 링크 발급")
def issue_link_token(employee_id: int, me: dict = Depends(current_manager)):
    """直前の合言葉は上書きされる。連携が済むと合言葉は空になる（POST /line/link）。
    직전의 암호는 덮어쓰인다. 연동이 끝나면 암호는 비워진다(POST /line/link)."""
    token = secrets.token_urlsafe(24)
    with tx() as conn:
        row = conn.execute(
            "UPDATE employees SET link_token = %s, link_token_expires_at = now() + make_interval(days => %s) "
            "WHERE employee_id = %s AND store_id = %s RETURNING link_token_expires_at",
            (token, settings.link_token_days, employee_id, me["store_id"])).fetchone()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "従業員が見つかりません / 직원을 찾을 수 없습니다")
    return {"token": token, "expires_at": row["link_token_expires_at"]}


# ---------- 業務 / 업무（tasks。残すが検査には使わない / 남기지만 검사에는 쓰지 않는다）----------
class TaskIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    sort_order: int = Field(default=0, ge=0, le=32767)


class TaskPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    sort_order: int | None = Field(default=None, ge=0, le=32767)


@router.get("/tasks", summary="業務の一覧 / 업무 목록")
def list_tasks(me: dict = Depends(current_manager)):
    with tx() as conn:
        return conn.execute("SELECT task_id, name, sort_order FROM tasks WHERE store_id = %s ORDER BY sort_order, task_id",
                            (me["store_id"],)).fetchall()


@router.post("/tasks", status_code=201, summary="業務の登録 / 업무 등록")
def create_task(body: TaskIn, me: dict = Depends(current_manager)):
    with tx() as conn:
        return conn.execute("INSERT INTO tasks (store_id, name, sort_order) VALUES (%s,%s,%s) "
                            "RETURNING task_id, name, sort_order",
                            (me["store_id"], body.name, body.sort_order)).fetchone()


@router.patch("/tasks/{task_id}", summary="業務の更新 / 업무 수정")
def patch_task(task_id: int, body: TaskPatch, me: dict = Depends(current_manager)):
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise ApiError(422, "VALIDATION_ERROR", "変更する項目がありません / 변경할 항목이 없습니다")
    sets = ", ".join(f"{k} = %s" for k in changes)
    with tx() as conn:
        row = conn.execute(f"UPDATE tasks SET {sets} WHERE task_id = %s AND store_id = %s "
                           f"RETURNING task_id, name, sort_order",
                           [*changes.values(), task_id, me["store_id"]]).fetchone()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "業務が見つかりません / 업무를 찾을 수 없습니다")
    return row


@router.delete("/tasks/{task_id}", status_code=204, summary="業務の削除 / 업무 삭제")
def delete_task(task_id: int, me: dict = Depends(current_manager)):
    with tx() as conn:
        n = conn.execute("DELETE FROM tasks WHERE task_id = %s AND store_id = %s", (task_id, me["store_id"])).rowcount
    if n == 0:
        raise ApiError(404, "NOT_FOUND", "業務が見つかりません / 업무를 찾을 수 없습니다")
    return Response(status_code=204)