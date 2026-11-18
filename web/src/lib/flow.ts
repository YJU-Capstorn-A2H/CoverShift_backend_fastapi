// 편성 흐름 화면 (W-3 · W-5 · W-8) 의 판단을 순수 함수로 둔다.

import type { ContactWait, OutboxMessage, ReviewWait, RunState, RunStatus } from '../types'

/** 진행 상태를 2초마다 다시 읽을 상태 (W-3). running 은 계약대로,
 * (가정 신규) waiting_contact 도 읽는다 — 직원 답장은 다른 화면(LINE 스텁)에서 오므로 이 화면이 스스로 알 수 없다. */
export const POLL_STATES: ReadonlySet<RunState> = new Set<RunState>(['running', 'waiting_contact'])
export const POLL_MS = 2000

export function shouldPoll(state: RunState | null | undefined): boolean {
  return state != null && POLL_STATES.has(state)
}

export function reviewWait(status: RunStatus | null | undefined): ReviewWait | null {
  const w = status?.waiting
  return status?.state === 'waiting_review' && w && w.type === 'review' ? w : null
}

export function contactWait(status: RunStatus | null | undefined): ContactWait | null {
  const w = status?.waiting
  return status?.state === 'waiting_contact' && w && w.type === 'contact' ? w : null
}

/** 남은 점장 재편성 요청 횟수 (W-5): manager_request_limit − manager_request_count. 0 아래로는 내려가지 않는다 (F-2, V9_ko 479). */
export function remainingRequests(w: Pick<ReviewWait, 'manager_request_limit' | 'manager_request_count'>): number {
  return Math.max(0, w.manager_request_limit - w.manager_request_count)
}

/** LINE 스텁의 연락 메시지인가 (W-8): kind 가 contact_ask 이고 body 에 contact_id 가 있다. */
export function isContactMessage(m: Pick<OutboxMessage, 'kind' | 'body'>): boolean {
  return m.kind === 'contact_ask' && typeof m.body['contact_id'] === 'number'
}

/** 답장에 쓸 line_user_id. 계약에는 이 값을 화면에 주는 곳이 없어(「계약 변경 요청」)
 * 메시지 행 → body 순으로 찾고, 없으면 null (화면이 입력 칸을 보여 준다). */
export function lineUserIdOf(m: Pick<OutboxMessage, 'body' | 'line_user_id'>): string | null {
  if (typeof m.line_user_id === 'string' && m.line_user_id) return m.line_user_id
  const b = m.body['line_user_id']
  return typeof b === 'string' && b ? b : null
}

/** 메시지 본문 글자. body.text 가 없으면 빈 문자열. */
export function messageText(m: Pick<OutboxMessage, 'body'>): string {
  const t = m.body['text']
  return typeof t === 'string' ? t : ''
}

/** 확정안 ID 찾기: 계약에 자리가 없어서 (「계약 변경 요청」) 있는 값 → 이 화면에서 마지막으로 본 승인 대기 안 순으로 고른다. */
export function confirmedDraftId(status: RunStatus | null | undefined, periodConfirmed: number | null | undefined, lastSeen: number | null): number | null {
  return status?.confirmed_draft_id ?? periodConfirmed ?? lastSeen ?? null
}
