// 설정 화면 입력 검사 (W-2). DB 제약(V9_ko 2-4 DDL)을 보내기 전에 알려 준다.
import { describe, expect, it } from 'vitest'
import type { TimeRule } from '../types'
import { normalizeFixedWeekdays, optionalInt, validateEmployee, validateTimeRule } from './forms'

const existing: TimeRule[] = [{ rule_id: 1, weekday: 1, start_time: '08:00', end_time: '13:00', min_headcount: 2, min_qualified: 1 }]
const base = { weekday: 1, start_time: '13:00', end_time: '22:30', min_headcount: 3, min_qualified: 1 }

describe('시간대 규칙 · 직원 입력 req:W-2', () => {
  it('req:W-2 맞닿는 시간대(13:00 끝 / 13:00 시작)는 겹침이 아니다', () => {
    expect(validateTimeRule(base, existing)).toBeNull()
  })

  it('req:W-2 (깨뜨리기) 겹침 · 30분 아님 · 끝 ≤ 시작 · 휴게 포함 인원 0 · 자격자 음수', () => {
    expect(validateTimeRule({ ...base, start_time: '12:30' }, existing)).toBe('OVERLAP')
    expect(validateTimeRule({ ...base, start_time: '13:15' }, existing)).toBe('NOT_HALF_HOUR')
    expect(validateTimeRule({ ...base, end_time: '13:00' }, existing)).toBe('END_NOT_AFTER_START')
    expect(validateTimeRule({ ...base, min_headcount: 0 }, existing)).toBe('HEADCOUNT_MIN')
    expect(validateTimeRule({ ...base, min_qualified: -1 }, existing)).toBe('QUALIFIED_MIN')
    expect(validateTimeRule({ ...base, weekday: 8 }, existing)).toBe('WEEKDAY')
  })

  it('req:W-2 자기 자신을 고칠 때는 겹침에서 뺀다 · 다른 요일은 겹침이 아니다', () => {
    expect(validateTimeRule({ ...base, start_time: '08:00', end_time: '12:00' }, existing, 1)).toBeNull()
    expect(validateTimeRule({ ...base, weekday: 2, start_time: '08:00' }, existing)).toBeNull()
  })

  it('req:W-2 직원: 주 출근일수와 고정 요일 수가 같으면 통과, 다르면 WEEKLY_FIXED_MISMATCH (DB CHECK 와 같음)', () => {
    const emp = { name: 'Aさん', is_regular: false, has_qualification: false, closing_ok: true, weekly_days: 3, fixed_weekdays: [1, 3, 5] }
    expect(validateEmployee(emp)).toBeNull()
    expect(validateEmployee({ ...emp, fixed_weekdays: [1, 3] })).toBe('WEEKLY_FIXED_MISMATCH')
    expect(validateEmployee({ ...emp, weekly_days: null })).toBeNull()
    expect(validateEmployee({ ...emp, weekly_days: 0 })).toBe('WEEKLY_DAYS_RANGE')
    expect(validateEmployee({ ...emp, name: '  ' })).toBe('NAME_REQUIRED')
  })

  it('req:W-2 고정 요일은 정렬 · 중복 제거, 비면 null / 빈 숫자칸은 null (= 제한 없음)', () => {
    expect(normalizeFixedWeekdays([5, 1, 5])).toEqual([1, 5])
    expect(normalizeFixedWeekdays([])).toBeNull()
    expect(optionalInt('')).toBeNull()
    expect(optionalInt('4')).toBe(4)
    expect(optionalInt('2.5')).toBeNull()
  })
})
