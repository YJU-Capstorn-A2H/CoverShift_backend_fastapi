"""설정 CRUD (A-5 ~ A-9, M-8, V9_ko 168-194). 점포는 JWT 의 관리자에서만 정한다 — 경로 · 본문에서 store_id 를 받지 않는다 (A-5)."""
from __future__ import annotations

from fastapi import APIRouter, Response

from app.repo import employees as emp_repo
from app.repo import periods as period_repo
from app.repo import store as store_repo

from .deps import Conn, Manager, Runner
from .errors import ApiError, not_found
from .runstate import run_state
from .schemas import (CopyIn, EmployeeIn, EmployeePatch, PeriodIn, PeriodPatch, StorePatch, TaskIn, TaskPatch,
                      TimeRuleIn, TimeRulePatch)
from .serialize import out

router = APIRouter(prefix="/api", tags=["settings"])


# ---------------------------------------------------------------- 점포

@router.get("/store")
def get_store(m: Manager, conn: Conn):
    return out(store_repo.get_store(conn, m.store_id))


@router.patch("/store")
def patch_store(body: StorePatch, m: Manager, conn: Conn):
    return out(store_repo.update_store(conn, m.store_id, body.name))


# ---------------------------------------------------------------- 시간대 규칙 (A-6)

@router.get("/store/time-rules")
def list_time_rules(m: Manager, conn: Conn):
    return out(store_repo.list_time_rules(conn, m.store_id))


@router.post("/store/time-rules", status_code=201)
def create_time_rule(body: TimeRuleIn, m: Manager, conn: Conn):
    return out(store_repo.create_time_rule(conn, m.store_id, body.model_dump()))


@router.post("/store/time-rules/copy")
def copy_time_rules(body: CopyIn, m: Manager, conn: Conn):
    targets = sorted(set(body.to_weekdays))
    if body.from_weekday in targets:
        # 원본 요일을 대상에 넣으면 「지우고 복사」가 원본을 지운다
        raise ApiError(422, "INVALID_COPY", "コピー元とコピー先の曜日が同じです")
    if store_repo.count_rules(conn, m.store_id, body.from_weekday) == 0:
        # (가정 신규) 원본 요일에 규칙이 없으면 대상 요일 규칙만 지워지고 끝난다 → 실수로 보고 거부한다
        raise ApiError(422, "NO_SOURCE_RULES", "コピー元の曜日に時間帯ルールがありません")
    return {"created": store_repo.copy_time_rules(conn, m.store_id, body.from_weekday, targets)}


@router.patch("/store/time-rules/{rule_id}")
def patch_time_rule(rule_id: int, body: TimeRulePatch, m: Manager, conn: Conn):
    row = store_repo.update_time_rule(conn, m.store_id, rule_id, body.model_dump(exclude_unset=True))
    if row is None:
        raise not_found("時間帯ルール")
    return out(row)


@router.delete("/store/time-rules/{rule_id}", status_code=204)
def delete_time_rule(rule_id: int, m: Manager, conn: Conn):
    if not store_repo.delete_time_rule(conn, m.store_id, rule_id):
        raise not_found("時間帯ルール")
    return Response(status_code=204)


# ---------------------------------------------------------------- 직원 (A-7). 삭제 API 없음 (V9_ko 180)

@router.get("/employees")
def list_employees(m: Manager, conn: Conn):
    return out(emp_repo.list_employees(conn, m.store_id))


@router.post("/employees", status_code=201)
def create_employee(body: EmployeeIn, m: Manager, conn: Conn):
    return out(emp_repo.create_employee(conn, m.store_id, body.model_dump()))


@router.get("/employees/{employee_id}")
def get_employee(employee_id: int, m: Manager, conn: Conn):
    row = emp_repo.get_employee(conn, m.store_id, employee_id)
    if row is None:
        raise not_found("従業員")
    return out(row)


@router.patch("/employees/{employee_id}")
def patch_employee(employee_id: int, body: EmployeePatch, m: Manager, conn: Conn):
    fields = body.model_dump(exclude_unset=True)
    for k in ("name", "is_regular", "has_qualification", "closing_ok"):
        if k in fields and fields[k] is None:
            raise ApiError(422, "VALIDATION_ERROR", f"{k} はnullに変更できません")
    row = emp_repo.update_employee(conn, m.store_id, employee_id, fields)
    if row is None:
        raise not_found("従業員")
    return out(row)


# ---------------------------------------------------------------- 업무 (A-8)

@router.get("/tasks")
def list_tasks(m: Manager, conn: Conn):
    return out(emp_repo.list_tasks(conn, m.store_id))


@router.post("/tasks", status_code=201)
def create_task(body: TaskIn, m: Manager, conn: Conn):
    return out(emp_repo.create_task(conn, m.store_id, body.name, body.sort_order))


@router.patch("/tasks/{task_id}")
def patch_task(task_id: int, body: TaskPatch, m: Manager, conn: Conn):
    fields = body.model_dump(exclude_unset=True)
    if any(v is None for v in fields.values()):
        raise ApiError(422, "VALIDATION_ERROR", "name · sort_order はnullに変更できません")
    row = emp_repo.update_task(conn, m.store_id, task_id, fields)
    if row is None:
        raise not_found("業務")
    return out(row)


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: int, m: Manager, conn: Conn):
    if not emp_repo.delete_task(conn, m.store_id, task_id):
        raise not_found("業務")
    return Response(status_code=204)


# ---------------------------------------------------------------- 편성 기간 (A-9)

def period_out(conn, runner, p: dict) -> dict:
    d = {k: v for k, v in p.items() if k != "store_id"}
    d["run_state"] = run_state(conn, runner, p)
    d["confirmed_draft_id"] = period_repo.confirmed_draft_id(conn, p["period_id"])
    return out(d)


def load_period(conn, m, period_id: int, *, for_update: bool = False) -> dict:
    p = period_repo.get_period(conn, m.store_id, period_id, for_update=for_update)
    if p is None:
        raise not_found("シフト期間")
    return p


@router.get("/shift-periods")
def list_periods(m: Manager, conn: Conn, runner: Runner):
    return [period_out(conn, runner, p) for p in period_repo.list_periods(conn, m.store_id)]


@router.post("/shift-periods", status_code=201)
def create_period(body: PeriodIn, m: Manager, conn: Conn, runner: Runner):
    return period_out(conn, runner, period_repo.create_period(conn, m.store_id, body.model_dump()))


@router.get("/shift-periods/{period_id}")
def get_period(period_id: int, m: Manager, conn: Conn, runner: Runner):
    return period_out(conn, runner, load_period(conn, m, period_id))


@router.patch("/shift-periods/{period_id}")
def patch_period(period_id: int, body: PeriodPatch, m: Manager, conn: Conn, runner: Runner):
    fields = body.model_dump(exclude_unset=True)
    for k in ("submission_deadline", "designated_off_days"):
        if k in fields and fields[k] is None:
            raise ApiError(422, "VALIDATION_ERROR", f"{k} はnullに変更できません")
    row = period_repo.update_period(conn, m.store_id, period_id, fields)
    if row is None:
        raise not_found("シフト期間")
    return period_out(conn, runner, row)


@router.get("/shift-periods/{period_id}/submissions")
def period_submissions(period_id: int, m: Manager, conn: Conn):
    p = load_period(conn, m, period_id)
    return out(period_repo.submissions(conn, m.store_id, p))
