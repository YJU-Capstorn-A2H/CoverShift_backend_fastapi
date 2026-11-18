// 편성 흐름 판단 (W-3 진행 상태 · W-5 남은 요청 · W-8 LINE 스텁) · 로그인 토큰 · 오류 본문 (W-1).
import { describe, expect, it } from 'vitest'
import type { RunStatus } from '../types'
import { confirmedDraftId, contactWait, isContactMessage, lineUserIdOf, messageText, remainingRequests, reviewWait, shouldPoll } from './flow'
import { createSession, parseErrorBody, TOKEN_KEY } from './session'

const review: RunStatus = {
  period_id: 1,
  thread_id: 't1',
  state: 'waiting_review',
  error: null,
  waiting: {
    type: 'review', draft_id: 7, result: 'valid', violations: [], blocked: [], shortages: [], notes: [], free_text_guidelines: [],
    auto_retry_count: 1, manager_request_count: 1, manager_request_limit: 2,
  },
}

describe('편성 흐름 req:W-3 req:W-5', () => {
  it('req:W-3 running 동안만(그리고 직원 답장 대기) 다시 읽는다', () => {
    expect(shouldPoll('running')).toBe(true)
    expect(shouldPoll('waiting_contact')).toBe(true)
    expect(shouldPoll('waiting_review')).toBe(false)
    expect(shouldPoll('confirmed')).toBe(false)
    expect(shouldPoll('not_started')).toBe(false)
    expect(shouldPoll(null)).toBe(false)
  })

  it('req:W-4 승인 대기 꺼내기: 상태와 waiting.type 이 둘 다 맞아야 한다', () => {
    expect(reviewWait(review)?.draft_id).toBe(7)
    expect(reviewWait({ ...review, state: 'running' })).toBeNull()
    expect(contactWait(review)).toBeNull()
  })

  it('req:W-5 남은 재편성 요청 = limit − count, 0 밑으로 안 내려감', () => {
    expect(remainingRequests({ manager_request_limit: 2, manager_request_count: 0 })).toBe(2)
    expect(remainingRequests({ manager_request_limit: 2, manager_request_count: 2 })).toBe(0)
    expect(remainingRequests({ manager_request_limit: 2, manager_request_count: 5 })).toBe(0)
  })

  it('req:W-4 확정안 ID: 서버 값 → 기간 값 → 마지막으로 본 안', () => {
    expect(confirmedDraftId({ ...review, state: 'confirmed', confirmed_draft_id: 9 }, 8, 7)).toBe(9)
    expect(confirmedDraftId({ ...review, state: 'confirmed' }, null, 7)).toBe(7)
    expect(confirmedDraftId(null, undefined, null)).toBeNull()
  })
})

describe('LINE 스텁 req:W-8 req:M-5', () => {
  const contact = { kind: 'contact_ask', body: { text: '11/3 13:00-17:00 出られますか', contact_id: 3, buttons: ['yes', 'no'] } }

  it('req:W-8 contact_ask + contact_id 가 있으면 답장 버튼을 단다', () => {
    expect(isContactMessage(contact)).toBe(true)
    expect(messageText(contact)).toContain('出られますか')
  })

  it('req:W-8 (깨뜨리기) 확정 통지 · contact_id 없는 메시지에는 버튼을 달지 않는다', () => {
    expect(isContactMessage({ kind: 'shift_confirmed', body: { text: '確定', work_days: [] } })).toBe(false)
    expect(isContactMessage({ kind: 'contact_ask', body: { text: 'x' } })).toBe(false)
  })

  it('req:M-5 line_user_id: 행 → body 순으로 찾고 없으면 null (화면이 입력 칸을 낸다)', () => {
    expect(lineUserIdOf({ line_user_id: 'U1', body: { line_user_id: 'U2' } })).toBe('U1')
    expect(lineUserIdOf({ body: { line_user_id: 'U2' } })).toBe('U2')
    expect(lineUserIdOf({ body: {} })).toBeNull()
  })
})

describe('로그인 토큰 · 오류 req:W-1 req:M-4', () => {
  function memStore() {
    const m = new Map<string, string>()
    return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k), m }
  }

  it('req:W-1 JWT 를 sessionStorage 키에 두고, clear 하면 지운다 (401 → /login 때 쓰는 동작)', () => {
    const store = memStore()
    const s = createSession(store)
    expect(s.get()).toBeNull()
    s.set('jwt-abc')
    expect(store.m.get(TOKEN_KEY)).toBe('jwt-abc')
    expect(s.get()).toBe('jwt-abc')
    s.clear()
    expect(s.get()).toBeNull()
  })

  it('req:W-1 (깨뜨리기) 저장소가 예외를 던져도 이 탭 안에서는 토큰을 유지한다', () => {
    const broken = { getItem: () => { throw new Error('blocked') }, setItem: () => { throw new Error('blocked') }, removeItem: () => { throw new Error('blocked') } }
    const s = createSession(broken)
    s.set('jwt-x')
    expect(s.get()).toBe('jwt-x')
  })

  it('req:M-4 오류 본문: 계약 모양에서 code · message 를 꺼내고, 다른 모양이어도 무언가는 보여 준다', () => {
    expect(parseErrorBody(400, { error: { code: 'INVALID_CODE', message: '초대 코드가 올바르지 않습니다' } })).toEqual({ code: 'INVALID_CODE', message: '초대 코드가 올바르지 않습니다' })
    expect(parseErrorBody(422, { detail: [{ msg: 'field required' }] })).toEqual({ code: 'HTTP_422', message: 'field required' })
    expect(parseErrorBody(500, null)).toEqual({ code: 'HTTP_500', message: 'HTTP 500' })
  })
})
