// API_CONTRACT.md 를 그대로 따르는 형 붙은 클라이언트. 경로 · 모양이 바뀌면 이 파일과 types.ts 만 고친다.
// - 접두사 /api (개발 중에는 Vite 가 127.0.0.1:8010 으로 넘긴다)
// - 관리자 요청은 Authorization: Bearer <JWT>. 401 이면 토큰을 지우고 /login 으로 (결정 사항)
// - 오류 본문 {"error":{"code","message"}} (A-19) → ApiError

import { parseErrorBody, session } from './lib/session'
import type {
  AssignmentOut,
  ConfirmedInstruction,
  Draft,
  Employee,
  EmployeeInput,
  EmployeeSubmissions,
  InstructionPreview,
  LineReplyBody,
  LinkTokenResponse,
  LoginResponse,
  Me,
  OutboxMessage,
  ReviewBody,
  Rule,
  RunStatus,
  ShiftPeriod,
  ShiftPeriodInput,
  Store,
  SubmissionItem,
  SubmissionLinksResponse,
  SubmitItemInput,
  SubmitPage,
  TimeRule,
  TimeRuleInput,
} from './types'

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

let unauthorizedHandler: (() => void) | null = null
/** main.ts 에서 라우터를 넘겨 401 처리 방법을 정한다 (api.ts 가 라우터를 직접 import 하지 않게). */
export function onUnauthorized(handler: () => void) {
  unauthorizedHandler = handler
}

type Method = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'

async function call<T>(method: Method, path: string, body?: unknown, opts: { auth?: boolean } = {}): Promise<T> {
  const auth = opts.auth ?? true
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const token = auth ? session.get() : null
  if (token) headers['Authorization'] = `Bearer ${token}`

  let res: Response
  try {
    res = await fetch(`/api${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
  } catch {
    // 서버가 안 떠 있거나 네트워크가 끊김. 메시지는 화면이 i18n 으로 바꾼다 (code=NETWORK).
    throw new ApiError(0, 'NETWORK', 'network error')
  }

  if (res.status === 204) return undefined as T
  const text = await res.text()
  let data: unknown = null
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = null
    }
  }
  if (!res.ok) {
    const { code, message } = parseErrorBody(res.status, data)
    if (res.status === 401 && auth) {
      session.clear()
      unauthorizedHandler?.()
    }
    throw new ApiError(res.status, code, message)
  }
  return data as T
}

const enc = encodeURIComponent

export const api = {
  // ---------------------------------------------------------------- 인증 (A-1 ~ A-4)
  register: (b: { invite_code: string; email: string; password: string }) =>
    call<{ manager_id: number }>('POST', '/auth/register', b, { auth: false }),
  login: (b: { email: string; password: string }) => call<LoginResponse>('POST', '/auth/manager/login', b, { auth: false }),
  logout: () => call<void>('POST', '/auth/logout'),
  me: () => call<Me>('GET', '/auth/me'),

  // ---------------------------------------------------------------- 설정 (A-5 ~ A-9)
  store: () => call<Store>('GET', '/store'),
  timeRules: () => call<TimeRule[]>('GET', '/store/time-rules'),
  createTimeRule: (b: TimeRuleInput) => call<TimeRule>('POST', '/store/time-rules', b),
  updateTimeRule: (id: number, b: Partial<TimeRuleInput>) => call<TimeRule>('PATCH', `/store/time-rules/${id}`, b),
  deleteTimeRule: (id: number) => call<void>('DELETE', `/store/time-rules/${id}`),
  copyTimeRules: (b: { from_weekday: number; to_weekdays: number[] }) => call<{ created: number }>('POST', '/store/time-rules/copy', b),

  employees: () => call<Employee[]>('GET', '/employees'),
  createEmployee: (b: EmployeeInput) => call<Employee>('POST', '/employees', b),
  updateEmployee: (id: number, b: Partial<EmployeeInput>) => call<Employee>('PATCH', `/employees/${id}`, b),
  linkToken: (id: number) => call<LinkTokenResponse>('POST', `/employees/${id}/link-token`),
  // 직원 쪽 LINE 연결 (토큰, 로그인 없음). 링크 = {PUBLIC_BASE_URL}/line/link/{token}
  lineLink: (b: { link_token: string; line_user_id: string }) => call<{ employee_id: number }>('POST', '/line/link', b, { auth: false }),

  periods: () => call<ShiftPeriod[]>('GET', '/shift-periods'),
  period: (id: number) => call<ShiftPeriod>('GET', `/shift-periods/${id}`),
  createPeriod: (b: ShiftPeriodInput) => call<ShiftPeriod>('POST', '/shift-periods', b),
  updatePeriod: (id: number, b: Partial<ShiftPeriodInput>) => call<ShiftPeriod>('PATCH', `/shift-periods/${id}`, b),
  submissions: (id: number) => call<EmployeeSubmissions[]>('GET', `/shift-periods/${id}/submissions`),
  sendSubmissionLinks: (id: number) => call<SubmissionLinksResponse>('POST', `/shift-periods/${id}/submission-links`),

  // ---------------------------------------------------------------- 점장 지시 (M-2, W-9)
  previewInstruction: (id: number, text: string) => call<InstructionPreview>('POST', `/shift-periods/${id}/instructions/preview`, { text }),
  confirmInstruction: (id: number, b: { text: string; rules: Rule[]; free_text: string[] }) =>
    call<{ action_id: number }>('POST', `/shift-periods/${id}/instructions/confirm`, b),
  instruction: (id: number) => call<ConfirmedInstruction | null>('GET', `/shift-periods/${id}/instructions`),

  // ---------------------------------------------------------------- 편성 제어 (M-1, M-3)
  startNow: (id: number) => call<RunStatus>('POST', `/shift-periods/${id}/start-now`),
  runStatus: (id: number) => call<RunStatus>('GET', `/shift-periods/${id}/run-status`),
  review: (id: number, b: ReviewBody) => call<RunStatus>('POST', `/shift-periods/${id}/review`, b),
  approve: (id: number, draftId: number, waivedKeys: string[]) =>
    api.review(id, { decision: 'approved', draft_id: draftId, waived_keys: waivedKeys }),
  requestRedraft: (id: number, draftId: number, instruction: string) =>
    api.review(id, { decision: 'redraft_requested', draft_id: draftId, instruction }),
  saveEdit: (id: number, draftId: number, assignments: AssignmentOut[]) =>
    api.review(id, { decision: 'edit_saved', draft_id: draftId, assignments }),
  draft: (draftId: number) => call<Draft>('GET', `/drafts/${draftId}`),

  // ---------------------------------------------------------------- 직원 제출 (토큰, 로그인 없음)
  submitPage: (token: string) => call<SubmitPage>('GET', `/submit/${enc(token)}`, undefined, { auth: false }),
  putAvailability: (token: string, items: SubmitItemInput[]) =>
    call<{ items: SubmissionItem[] }>('PUT', `/submit/${enc(token)}/availability`, { items }, { auth: false }),
  deleteAvailability: (token: string, workDate: string) =>
    call<void>('DELETE', `/submit/${enc(token)}/availability/${enc(workDate)}`, undefined, { auth: false }),

  // ---------------------------------------------------------------- LINE 스텁 (M-5, W-8)
  outbox: (employeeId?: number) =>
    call<OutboxMessage[]>('GET', `/dev/line-outbox${employeeId ? `?employee_id=${employeeId}` : ''}`),
  // 직원 쪽 요청이라 관리자 JWT 를 붙이지 않는다 (G16: contact_id + line_user_id 로 확인)
  lineReply: (b: LineReplyBody) => call<RunStatus>('POST', '/line/reply', b, { auth: false }),
}
