# 입구 API 계약 (화면 ↔ API ↔ 러너가 같이 보는 한 장)

메인 세션 소유. api · web · runtime 에이전트는 이 문서대로 만든다. 바꿔야 하면 보고서에 「계약 변경 요청」.

공통
- 접두사 `/api`. JSON. 날짜 `YYYY-MM-DD`, 시각 `HH:MM` (30분 단위), 시점 ISO 8601 (timestamptz).
- 오류는 전부 `{"error": {"code": "<대문자_코드>", "message": "<사람이 읽는 한국어>"}}` (A-19).
- 관리자 API: `Authorization: Bearer <JWT>`. JWT 는 `{sub: manager_id, store_id, exp}` (HS256). 요청마다 is_active 확인 (G15).
  `DEV_SKIP_AUTH=true` 면 헤더 없이 manager_id=1 로 본다 (A-14, 시연 메인 흐름에선 끔).
- 점포는 JWT 에서 정한다. 요청에 store_id 를 받지 않는다 (A-5). 남의 점포 자원 = 404.
- 화면 개발 서버: `http://127.0.0.1:5173` (Vite 가 `/api` 를 `http://127.0.0.1:8010` 으로 넘김). API 는 8010.

## 인증 (A-1 ~ A-4)

| 메서드 · 경로 | 요청 | 응답 |
|---|---|---|
| POST /api/auth/register | `{invite_code, email, password}` | 201 `{manager_id}` / 400 `INVALID_CODE` (없음·만료·사용됨 같은 응답) / 409 `EMAIL_EXISTS` / 422 |
| POST /api/auth/manager/login | `{email, password}` | 200 `{access_token, manager_id, store_id}` / 401 `INVALID_CREDENTIALS` |
| POST /api/auth/logout | — | 204 |
| GET /api/auth/me | — | 200 `{manager_id, email, store_id, store_name}` |

운영 스크립트 (API 아님): `uv run python -m ops.create_store --store "<점포명>" --email <e> --password-stdin`, `uv run python -m ops.issue_invite --store-id <id>` → 코드 1번 출력.

## 설정 CRUD (A-5 ~ A-9, M-8)

| 메서드 · 경로 | 요청 / 응답 |
|---|---|
| GET · PATCH /api/store | `{store_id, name}` |
| GET /api/store/time-rules | `[{rule_id, weekday, start_time, end_time, min_headcount, min_qualified}]` (weekday 1=월…7=일) |
| POST /api/store/time-rules | `{weekday, start_time, end_time, min_headcount, min_qualified}` → 201 / 409 `TIME_RULE_OVERLAP` / 422 |
| PATCH · DELETE /api/store/time-rules/{rule_id} | 부분 수정 / 204 |
| POST /api/store/time-rules/copy | `{from_weekday, to_weekdays: [int]}` → 200 `{created}` (대상 요일의 기존 규칙은 지우고 복사) |
| GET · POST /api/employees | `{employee_id, name, is_regular, has_qualification, closing_ok, weekly_days, fixed_weekdays, line_linked}` / 422 `CHECK_VIOLATION` |
| GET · PATCH /api/employees/{id} | 같은 모양 |
| GET · POST /api/tasks, PATCH · DELETE /api/tasks/{id} | `{task_id, name, sort_order}` |
| GET · POST /api/shift-periods | `{period_id, target_month, submission_deadline, designated_off_days, off_request_limit, work_request_limit, thread_id, run_state}` / 409 `PERIOD_EXISTS` |
| GET · PATCH /api/shift-periods/{id} | 같은 모양 |
| GET /api/shift-periods/{id}/submissions | `[{employee_id, name, is_regular, items: [{work_date, kind, start_time, end_time}]}]` |

## LINE 연결 · 제출 링크 · 제출 (A-10 ~ A-13, W-7)

| 메서드 · 경로 | 요청 / 응답 |
|---|---|
| POST /api/employees/{id}/link-token | → 200 `{link_url, expires_at}` (line_outbox 에 `link_token` 기록) / 409 `LINE_ALREADY_LINKED` |
| POST /api/line/link | `{link_token, line_user_id}` → 200 `{employee_id}` / 400 `INVALID_TOKEN` / 409 `LINE_ALREADY_LINKED` |
| POST /api/shift-periods/{id}/submission-links | → 200 `{links: [{employee_id, url}], sent}` (있으면 같은 링크, line_outbox `submission_link`) |
| GET /api/submit/{token} | → 200 `{employee_name, is_regular, target_month, submission_deadline, off_request_limit, work_request_limit, items: [...]}` / 404 `NOT_FOUND` / 410 `SUBMISSION_CLOSED` |
| PUT /api/submit/{token}/availability | `{items: [{work_date, kind, start_time?, end_time?}]}` → 200 `{items}` (그 날짜만 UPSERT) / 410 / 422 `LIMIT_EXCEEDED` · `INVALID_ITEM` |
| DELETE /api/submit/{token}/availability/{work_date} | → 204 |

## 점장 지시 → 틀 규칙 (M-2, W-9, G18)

| 메서드 · 경로 | 요청 / 응답 |
|---|---|
| POST /api/shift-periods/{id}/instructions/preview | `{text}` → 200 `{rules: [Rule], free_text: [str], rejected: [{raw, reason}]}` (LLM 1번. 저장 안 함) |
| POST /api/shift-periods/{id}/instructions/confirm | `{text, rules: [Rule], free_text: [str]}` → 201 `{action_id}` (코드가 Rule 을 다시 검사 → 틀리면 422 `INVALID_RULE`) |
| GET /api/shift-periods/{id}/instructions | → 200 `{action_id, text, rules, free_text, created_at}` 또는 `null` |

`Rule = {rule_key, template_key: "not_available"|"max_consecutive"|"min_headcount", params: {...}, source_text}` — params 모양은 app/domain.py.

## 편성 제어 (M-1, M-3, G1, A-14, A-15)

| 메서드 · 경로 | 요청 / 응답 |
|---|---|
| POST /api/shift-periods/{id}/start-now | → 202 `RunStatus` / 409 `ALREADY_STARTED` / 409 `BUSY` |
| GET /api/shift-periods/{id}/run-status | → 200 `RunStatus` |
| POST /api/shift-periods/{id}/review | `ReviewBody` → 202 `RunStatus` / 409 `BUSY` 「지금 편성을 처리하고 있습니다」 / 409 `STALE_DRAFT` (본 안이 최신이 아님) / 409 `NOT_WAITING` |
| GET /api/drafts/{draft_id} | → 200 `Draft` |

```
RunStatus = {
  period_id, thread_id,
  state: "not_started" | "running" | "waiting_review" | "waiting_contact" | "confirmed" | "error",
  waiting: ReviewWait | ContactWait | null,
  error: str | null
}
ReviewWait = {
  type: "review", draft_id, result: "valid"|"limit_reached"|"draft_empty",
  violations: [V], blocked: [V],            // blocked = check_approval 이 막은 것 (승인 시도 뒤에만 채워짐)
  shortages: [{work_date, start_time, end_time, need, have, reason?}], notes: [str],
  free_text_guidelines: [str],              // 「참고・보장 없음」으로 보여 준다
  auto_retry_count, manager_request_count, manager_request_limit
}
ContactWait = {type: "contact", contact_id, employee_id, work_date, start_time, end_time}
V = {key, check_code, severity: "required"|"warning", waivable, work_date, employee_id, detail}

ReviewBody =
    {decision: "approved", draft_id, waived_keys: [str]}
  | {decision: "redraft_requested", draft_id, instruction: str}
  | {decision: "edit_saved", draft_id, assignments: [{employee_id, work_date, kind, start_time, end_time, break_start?, break_end?}]}

Draft = {
  draft_id, period_id, status: "draft"|"confirmed", parent_draft_id, confirmed_at, confirmed_by,
  assignments: [{employee_id, employee_name, work_date, kind, start_time, end_time, break_start, break_end}],
  violations: [V + {allowed_by, allowed_at}]
}
```

## LINE 스텁 · 직원 답장 (M-5, M-6, A-17, W-8, G16)

| 메서드 · 경로 | 요청 / 응답 |
|---|---|
| GET /api/dev/line-outbox?employee_id= | 관리자 JWT → 200 `[{message_id, employee_id, employee_name, kind, body, created_at}]` (최신 순) |
| POST /api/line/reply | `{contact_id, line_user_id, answer: "yes"|"no"}` → 202 `RunStatus` / 403 `NOT_YOUR_CONTACT` / 409 `BUSY` / 409 `ALREADY_ANSWERED` |

`body` 예: 확정 통지 `{"text": "...", "work_days": [...]}`, 연락 `{"text": "...", "contact_id": 3, "buttons": ["yes","no"]}`.

## 러너 계약 (api ↔ runtime, Python)

```python
# app/runner.py (runtime 에이전트 소유)
class Busy(Exception): ...          # 같은 thread 에 도는 run 이 있음 → API 가 409 BUSY
class NotWaiting(Exception): ...    # resume 할 interrupt 가 없음 → 409 NOT_WAITING
class GraphRunner(Protocol):
    def start(self, period_id: int, thread_id: str) -> dict: ...         # 백그라운드로 돌리고 RunStatus(dict) 를 바로 돌려줌
    def status(self, period_id: int, thread_id: str | None) -> dict: ...
    def resume(self, period_id: int, thread_id: str, payload: dict) -> dict: ...
def get_runner() -> GraphRunner: ...  # RUNNER=local → 같은 프로세스 / aegra → Aegra HTTP (langgraph_sdk)
```

resume payload: review → `{"decision", "manager_id", "draft_id", "waived_keys"?, "instruction"?, "assignments"?}`, contact → `{"contact_id", "answer"}`.

## 11/12 통합 때 바뀐 것 (모듈 에이전트의 「계약 변경 요청」 반영)

- JWT `sub` 는 **문자열** `str(manager_id)` (PyJWT 2.10 이 숫자 sub 를 거부).
- 러너 예외 추가: `AlreadyStarted` → 409 `ALREADY_STARTED`, `StaleDraft(NotWaiting)` → 409 `STALE_DRAFT`. start 입력은 `{"period_id"}` 만 (나머지는 그래프가 DB 에서 읽음).
- thread_id = `period-{period_id}-<임의 8자>` (G22). 입구 잠금 이름은 `period-{period_id}` 로 기간마다 고정.
- `GET /api/shift-periods/{id}/run-status` 와 기간 응답에 `confirmed_draft_id` (확정 전 null).
- `GET /api/dev/line-outbox` 행에 `line_user_id` (스텁 화면의 답장 버튼이 씀, G23).
- 화면 경로: 제출 링크 `{PUBLIC_BASE_URL}/submit/{token}`, LINE 연결 `{PUBLIC_BASE_URL}/line/link/{token}`.
- `submission-links.sent` = 정수. `PUT /api/submit/{token}/availability` 응답 items = 그 달 전체. Rule params 의 시각 = `HH:MM`.
- shortages 항목 = `{work_date, start_time, end_time, need, have, reason?}`.
- contact 답 `answer` 는 `yes | no` (그래프는 `expired` 도 받는다 — D7 타이머용, 화면에는 없음).
- 추가 오류 코드: 401 `UNAUTHORIZED` · 409 `ALREADY_STARTED` `TASK_EXISTS` `IN_USE` `CONFLICT` `ALREADY_APPROVED` `ALREADY_CONFIRMED` `NOT_WAITING` `LINE_ALREADY_LINKED` · 422 `INVALID_COPY` `NO_SOURCE_RULES` `VALIDATION_ERROR` `INVALID_ITEM` · 502 `LLM_ERROR` · 500 `SERVER_MISCONFIGURED` `INTERNAL_ERROR`.
- 열린 채로 둔 것 (결정 필요): ReviewWait 에 거절 이유 칸(`error`)이 없어 그래프가 `notes` 끝에 붙인다 · 편집/연락으로 바뀐 안의 `result` 값(`edited` 신설 여부) · API 오류 message 언어(지금 한국어, 화면은 일본어 안내를 위에 따로 보여 줌).
