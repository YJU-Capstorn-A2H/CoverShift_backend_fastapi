# app/routers/not_implemented.py
# まだ作らないAPI・廃止されたAPIの「入口だけ」。仕様書6章の一覧と同じ道を用意し、
#   501 NOT_IMPLEMENTED = 保留・後回し・未決（作る予定 or 決まっていない）
#   410 GONE            = 廃止（V8で削除）
# を返す。Postmanで全APIを叩いても「404で分からない」にならないための表。
# 아직 만들지 않는 API・폐지된 API의 「입구만」. 사양서 6장 목록과 같은 경로를 준비하고,
#   501 NOT_IMPLEMENTED = 보류・후순위・미결(만들 예정 또는 정해지지 않음)
#   410 GONE            = 폐지(V8에서 삭제)
# 를 돌려준다. Postman으로 전체 API를 호출해도 「404라서 모른다」가 되지 않도록 하는 표.
from fastapi import APIRouter, Depends

from app.core.deps import current_manager
from app.core.errors import ApiError

router = APIRouter()

# (メソッド, パス, 理由 / 이유)
_NOT_IMPLEMENTED: list[tuple[list[str], str, str]] = [
    # --- 000 認証 / 인증 ---
    (["POST"], "/admin/invite-codes", "招待コードは運営スクリプトで発行（基準文書 11/08）/ 초대 코드는 운영 스크립트로 발급(기준 문서 11/08)"),
    (["POST"], "/staff/{staff_id}/magic-link", "旧方式。2章の submission-links / link-token が優先 / 구 방식. 2장의 submission-links / link-token이 우선"),
    (["GET"], "/pwa/session", "旧方式。2章の /submit/{token} が優先 / 구 방식. 2장의 /submit/{token}이 우선"),
    # --- 001 職員用（旧）/ 직원용(구) ---
    (["POST", "GET"], "/staff/{staff_id}/availability", "旧方式。2章の /submit/{token}/availability が優先 / 구 방식. 2장의 /submit/{token}/availability가 우선"),
    (["GET"], "/staff/{staff_id}/tasks", "不安フラグは後回し / 불안 플래그는 후순위"),
    (["PUT", "GET"], "/staff/{staff_id}/skill-concerns", "不安フラグは後回し / 불안 플래그는 후순위"),
    # --- 002 管理者・マスター（旧）/ 관리자・마스터(구) ---
    (["POST", "GET"], "/admin/tasks", "旧方式。2章の /tasks が優先 / 구 방식. 2장의 /tasks가 우선"),
    (["POST", "GET"], "/admin/qualifications", "2章では employees.has_qualification の1項目に変更 / 2장에서는 employees.has_qualification 1개 항목으로 변경"),
    (["POST", "GET"], "/admin/time-bands", "旧方式。2章の /store/time-rules が優先 / 구 방식. 2장의 /store/time-rules가 우선"),
    (["GET", "PUT"], "/admin/staff-skills", "スキルシートは後回し / 스킬시트는 후순위"),
    (["GET"], "/admin/staff-skills/unconfirmed-summary", "スキルシートは後回し / 스킬시트는 후순위"),
    (["POST", "GET"], "/admin/shift-periods", "旧方式。2章の /shift-periods が優先 / 구 방식. 2장의 /shift-periods가 우선"),
    (["PATCH"], "/admin/shift-periods/{period_id}", "旧方式。2章の /shift-periods/{id} が優先 / 구 방식. 2장의 /shift-periods/{id}가 우선"),
    (["GET", "PUT"], "/admin/settings", "保留 / 보류"),
    (["PUT"], "/admin/shift-periods/{period_id}/off-rules", "保留（公休日数は shift_periods に統合）/ 보류(공휴일수는 shift_periods에 통합)"),
    (["POST"], "/admin/paid-leave", "保留 / 보류"),
    (["GET"], "/admin/shift-periods/{period_id}/off-days", "保留 / 보류"),
    # --- 003 配置基準（12/4以降）/ 배치 기준(12/4 이후) ---
    (["GET", "PUT"], "/admin/staffing-requirements", "12/4以降 / 12/4 이후"),
    (["GET", "PUT"], "/admin/required-solo", "12/4以降 / 12/4 이후"),
    (["GET", "PUT"], "/admin/congestion-manual", "12/4以降 / 12/4 이후"),
    (["GET"], "/admin/staffing-requirements/suggestions", "保留 / 보류"),
    # --- 004 LINE（12/4以降）/ LINE(12/4 이후) ---
    (["POST"], "/webhooks/line", "12/4以降（MVPはLINEスタブ）/ 12/4 이후(MVP는 LINE 스텁)"),
    (["GET"], "/admin/line-registry", "2章では link-token 方式に変更 / 2장에서는 link-token 방식으로 변경"),
    (["POST"], "/admin/line-registry/match", "2章では link-token 方式に変更 / 2장에서는 link-token 방식으로 변경"),
    # --- 005 指針・相談・再編成 / 지침・상담・재편성 ---
    (["POST"], "/admin/drafts/rebalance", "再編成は reject（差し戻し）で行う / 재편성은 reject(반려)로 한다"),
    (["POST"], "/admin/consult", "相談グラフは別グラフ（後回し）/ 상담 그래프는 별도 그래프(후순위)"),
    (["GET", "POST"], "/admin/guidelines", "指針は後回し / 지침은 후순위"),
    (["PATCH"], "/admin/guidelines/items/{item_id}", "指針は後回し / 지침은 후순위"),
    (["POST"], "/admin/guidelines/items/{item_id}/feedback", "指針は後回し / 지침은 후순위"),
    # --- 006 調整ループ / 조정 루프 ---
    (["GET"], "/admin/contact-rounds/{draft_id}", "連絡は最後に付ける（遅ければ外す）/ 연락은 맨 마지막에 붙인다(늦으면 뺀다)"),
    (["PATCH"], "/admin/contact-rounds/max-contacts", "同上 / 동일"),
    (["POST"], "/admin/contact-rounds/{round_id}/cancel", "同上 / 동일"),
    (["POST"], "/admin/contact-rounds/{round_id}/skip-current", "同上 / 동일"),
    # --- 007 導入Excel（12/4以降）/ 도입 Excel(12/4 이후) ---
    (["POST"], "/admin/onboarding/imports", "12/4以降 / 12/4 이후"),
    (["POST"], "/admin/onboarding/imports/{import_id}/interpret", "12/4以降 / 12/4 이후"),
    (["GET", "DELETE"], "/admin/onboarding/imports/{import_id}", "12/4以降 / 12/4 이후"),
    (["PUT"], "/admin/onboarding/imports/{import_id}/confirmation", "12/4以降 / 12/4 이후"),
    (["POST"], "/admin/onboarding/imports/{import_id}/apply", "12/4以降 / 12/4 이후"),
    # --- 2章 保留 / 2장 보류 ---
    (["GET", "PUT"], "/employees/{employee_id}/skills", "保留 / 보류"),
    (["GET", "PUT"], "/store/time-rules/{rule_id}/task-needs", "保留 / 보류"),
    (["GET", "POST"], "/evaluations", "保留 / 보류"),
]

# 廃止（V8で削除）/ 폐지(V8에서 삭제)
_GONE: list[tuple[list[str], str, str]] = [
    (["POST"], "/staff/{staff_id}/skills/self-draft", "自己評価は廃止 / 자기평가는 폐지"),
    (["POST"], "/admin/drafts/{draft_id}/weight-dialog", "T2（重みの対話）は廃止 / T2(가중치 대화)는 폐지"),
    (["GET"], "/admin/weight-presets", "重みプリセットは廃止 / 가중치 프리셋은 폐지"),
]


def _make(status: int, code: str, reason: str, needs_auth: bool):
    # 管理者API(/admin, /employees, /store ...)は認証を先に確認。公開の入口(/webhooks, /pwa, /staff)は不要。
    # 관리자 API는 인증을 먼저 확인. 공개 입구(/webhooks, /pwa, /staff)는 불필요.
    if needs_auth:
        def handler(_: dict = Depends(current_manager)):
            raise ApiError(status, code, reason)
    else:
        def handler():
            raise ApiError(status, code, reason)
    return handler


_PUBLIC_PREFIXES = ("/webhooks", "/pwa", "/staff")


def _register_all():
    for rows, status, code in ((_NOT_IMPLEMENTED, 501, "NOT_IMPLEMENTED"), (_GONE, 410, "GONE")):
        for methods, path, reason in rows:
            auth = not path.startswith(_PUBLIC_PREFIXES)
            for m in methods:       # 1メソッド=1ルート（OpenAPIのIDを重ならせない）/ 1메서드=1라우트(OpenAPI ID가 겹치지 않게)
                router.add_api_route(path, _make(status, code, reason, auth), methods=[m],
                                     summary=f"[{status}] {reason}", operation_id=f"stub_{m}_{path}")


_register_all()
