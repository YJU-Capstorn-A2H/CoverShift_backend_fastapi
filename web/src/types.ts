// API_CONTRACT.md 의 모양을 그대로 옮긴 형. 계약이 바뀌면 여기부터 고친다.
// 시각은 "HH:MM", 날짜는 "YYYY-MM-DD", 시점은 ISO 8601 문자열.

export type RunState = 'not_started' | 'running' | 'waiting_review' | 'waiting_contact' | 'confirmed' | 'error'
export type Severity = 'required' | 'warning'
export type AvailabilityKind = 'available' | 'off' | 'paid_leave' | 'half_leave'
export type AssignmentKind = 'work' | 'paid_leave'
export type DraftResult = 'valid' | 'limit_reached' | 'draft_empty'
export type TemplateKey = 'not_available' | 'max_consecutive' | 'min_headcount'

export interface ApiErrorBody {
  error: { code: string; message: string }
}

// ---------------------------------------------------------------- 인증 (A-1 ~ A-4)
export interface LoginResponse {
  access_token: string
  manager_id: number
  store_id: number
}
export interface Me {
  manager_id: number
  email: string
  store_id: number
  store_name: string
}

// ---------------------------------------------------------------- 설정 (A-5 ~ A-9)
export interface Store {
  store_id: number
  name: string
}
export interface TimeRule {
  rule_id: number
  weekday: number // 1=월 … 7=일
  start_time: string
  end_time: string
  min_headcount: number // 休憩込みの必要人数 (M-8)
  min_qualified: number
}
export type TimeRuleInput = Omit<TimeRule, 'rule_id'>

export interface Employee {
  employee_id: number
  name: string
  is_regular: boolean
  has_qualification: boolean
  closing_ok: boolean
  weekly_days: number | null
  fixed_weekdays: number[] | null
  line_linked: boolean
}
export type EmployeeInput = Omit<Employee, 'employee_id' | 'line_linked'>

export interface ShiftPeriod {
  period_id: number
  target_month: string
  submission_deadline: string
  designated_off_days: number
  off_request_limit: number | null
  work_request_limit: number | null
  thread_id: string | null
  run_state: RunState | null
  /** 계약에 없음 — 「계약 변경 요청」 (확정 표를 다시 열 때 필요). 있으면 쓴다. */
  confirmed_draft_id?: number | null
}
export interface ShiftPeriodInput {
  target_month: string
  submission_deadline: string
  designated_off_days: number
  off_request_limit: number | null
  work_request_limit: number | null
}

export interface SubmissionItem {
  work_date: string
  kind: AvailabilityKind
  start_time: string | null
  end_time: string | null
}
export interface EmployeeSubmissions {
  employee_id: number
  name: string
  is_regular: boolean
  items: SubmissionItem[]
}

// ---------------------------------------------------------------- LINE · 제출 (A-10 ~ A-13, W-7)
export interface LinkTokenResponse {
  link_url: string
  expires_at: string
}
export interface SubmissionLinksResponse {
  links: { employee_id: number; url: string }[]
  sent: number
}
export interface SubmitPage {
  employee_name: string
  is_regular: boolean
  target_month: string
  submission_deadline: string
  off_request_limit: number | null
  work_request_limit: number | null
  items: SubmissionItem[]
}
export interface SubmitItemInput {
  work_date: string
  kind: AvailabilityKind
  start_time?: string
  end_time?: string
}

// ---------------------------------------------------------------- 점장 지시 (M-2, W-9)
export interface Rule {
  rule_key: string
  template_key: TemplateKey
  params: Record<string, unknown>
  source_text: string
}
export interface InstructionPreview {
  rules: Rule[]
  free_text: string[]
  rejected: { raw: unknown; reason: string }[]
}
export interface ConfirmedInstruction {
  action_id: number
  text: string
  rules: Rule[]
  free_text: string[]
  created_at: string
}

// ---------------------------------------------------------------- 편성 제어 (M-1, M-3)
export interface Violation {
  key: string
  check_code: string
  severity: Severity
  waivable: boolean
  work_date: string | null
  employee_id: number | null
  detail: Record<string, unknown>
  allowed_by?: number | null
  allowed_at?: string | null
}
export interface Shortage {
  work_date: string
  start_time: string
  end_time: string
  need: number
  have: number
  reason?: string
}
export interface ReviewWait {
  type: 'review'
  draft_id: number
  result: DraftResult
  violations: Violation[]
  blocked: Violation[]
  shortages: Shortage[]
  notes: string[]
  free_text_guidelines: string[]
  auto_retry_count: number
  manager_request_count: number
  manager_request_limit: number
}
export interface ContactWait {
  type: 'contact'
  contact_id: number
  employee_id: number
  work_date: string
  start_time: string
  end_time: string
}
export interface RunStatus {
  period_id: number
  thread_id: string | null
  state: RunState
  waiting: ReviewWait | ContactWait | null
  error: string | null
  /** 계약에 없음 — 「계약 변경 요청」. 확정 뒤 waiting 이 null 이 되면 확정안 ID 를 알 길이 없다. */
  confirmed_draft_id?: number | null
}

export interface AssignmentOut {
  employee_id: number
  work_date: string
  kind: AssignmentKind
  start_time: string | null
  end_time: string | null
  break_start?: string | null
  break_end?: string | null
}
export interface DraftAssignment extends AssignmentOut {
  employee_name: string
}
export interface Draft {
  draft_id: number
  period_id: number
  status: 'draft' | 'confirmed'
  parent_draft_id: number | null
  confirmed_at: string | null
  confirmed_by: number | null
  assignments: DraftAssignment[]
  violations: Violation[]
}

export type ReviewBody =
  | { decision: 'approved'; draft_id: number; waived_keys: string[] }
  | { decision: 'redraft_requested'; draft_id: number; instruction: string }
  | { decision: 'edit_saved'; draft_id: number; assignments: AssignmentOut[] }

// ---------------------------------------------------------------- LINE 스텁 (M-5, W-8)
export interface OutboxMessage {
  message_id: number
  employee_id: number
  employee_name: string
  kind: string
  body: Record<string, unknown>
  created_at: string
  /** 계약에 없음 — 「계약 변경 요청」. 답장(POST /api/line/reply)에 line_user_id 가 필요하다 (G16). */
  line_user_id?: string | null
}
export interface LineReplyBody {
  contact_id: number
  line_user_id: string
  answer: 'yes' | 'no'
}
