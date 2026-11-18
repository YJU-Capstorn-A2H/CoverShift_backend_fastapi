// 제출 항목 검사 (W-7). available · half_leave 만 시간 필수 · 30분 단위 (V9_ko 165, A-12).
import { describe, expect, it } from 'vitest'
import { changeKind, checkLimits, diffSubmission, entriesFromItems, toItemInput, validateEntry, type DayEntry } from './submission'

const e = (kind: DayEntry['kind'], start: string | null = null, end: string | null = null, d = '2026-11-01'): DayEntry => ({
  work_date: d,
  kind,
  start_time: start,
  end_time: end,
})

describe('제출 항목 검사 req:W-7', () => {
  it('req:W-7 available · half_leave 는 시간이 있으면 통과', () => {
    expect(validateEntry(e('available', '09:00', '17:30'))).toBeNull()
    expect(validateEntry(e('half_leave', '13:00', '17:00'))).toBeNull()
  })

  it('req:W-7 (깨뜨리기) available · half_leave 에 시간이 없으면 TIME_REQUIRED', () => {
    expect(validateEntry(e('available', '09:00', null))).toBe('TIME_REQUIRED')
    expect(validateEntry(e('half_leave'))).toBe('TIME_REQUIRED')
  })

  it('req:W-7 (깨뜨리기) 30분이 아닌 시각 · 끝 ≤ 시작은 거부', () => {
    expect(validateEntry(e('available', '09:15', '17:00'))).toBe('NOT_HALF_HOUR')
    expect(validateEntry(e('available', '17:00', '09:00'))).toBe('END_NOT_AFTER_START')
    expect(validateEntry(e('half_leave', '13:00', '13:00'))).toBe('END_NOT_AFTER_START')
  })

  it('req:W-7 off · paid_leave 는 시간 없이 통과, 시간이 붙으면 TIME_NOT_ALLOWED', () => {
    expect(validateEntry(e('off'))).toBeNull()
    expect(validateEntry(e('paid_leave'))).toBeNull()
    expect(validateEntry(e('off', '09:00', '10:00'))).toBe('TIME_NOT_ALLOWED')
    expect(validateEntry(e('none'))).toBeNull()
  })

  it('req:W-7 종류를 off 로 바꾸면 시간을 지우고, available 로 바꾸면 기본 시각을 채운다', () => {
    const d = { start: '09:00', end: '18:00' }
    expect(changeKind(e('available', '10:00', '15:00'), 'off', d)).toEqual(e('off'))
    expect(changeKind(e('none'), 'available', d)).toEqual(e('available', '09:00', '18:00'))
  })

  it('req:W-7 보낼 모양: off 에는 시간 키가 아예 없다', () => {
    expect(toItemInput(e('off'))).toEqual({ work_date: '2026-11-01', kind: 'off' })
    expect(toItemInput(e('available', '09:00', '12:00'))).toEqual({ work_date: '2026-11-01', kind: 'available', start_time: '09:00', end_time: '12:00' })
  })

  it('req:W-7 상한: 사원만 세고 half_leave 는 세지 않는다 · null 은 제한 없음', () => {
    const entries = [e('off', null, null, 'a'), e('off', null, null, 'b'), e('available', '09:00', '10:00', 'c'), e('half_leave', '09:00', '12:00', 'd')]
    expect(checkLimits(entries, { is_regular: true, off_request_limit: 1, work_request_limit: 1 })).toEqual({ offCount: 2, workCount: 1, offOver: true, workOver: false })
    expect(checkLimits(entries, { is_regular: true, off_request_limit: null, work_request_limit: null }).offOver).toBe(false)
    expect(checkLimits(entries, { is_regular: false, off_request_limit: 1, work_request_limit: 0 })).toMatchObject({ offOver: false, workOver: false })
  })

  it('req:W-7 저장 차이: 바뀐 날만 PUT, 「선택 없음」으로 되돌린 날은 DELETE', () => {
    const dates = ['2026-11-01', '2026-11-02', '2026-11-03']
    const original = entriesFromItems(dates, [
      { work_date: '2026-11-01', kind: 'available', start_time: '09:00:00', end_time: '17:00:00' },
      { work_date: '2026-11-02', kind: 'off', start_time: null, end_time: null },
    ])
    expect(original.map((x) => x.kind)).toEqual(['available', 'off', 'none'])
    const edited: DayEntry[] = [original[0]!, e('none', null, null, '2026-11-02'), e('paid_leave', null, null, '2026-11-03')]
    expect(diffSubmission(original, edited)).toEqual({ upserts: [{ work_date: '2026-11-03', kind: 'paid_leave' }], deletes: ['2026-11-02'] })
    expect(diffSubmission(original, original)).toEqual({ upserts: [], deletes: [] })
  })
})
