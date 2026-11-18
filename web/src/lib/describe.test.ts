// 점장 지시 미리보기의 규칙 설명 (W-9, M-2) · 달력 (W-4 표 머리).
import { describe, expect, it } from 'vitest'
import type { Rule } from '../types'
import { dayType, isoWeekday, monthDates, monthInputToTargetMonth } from './calendar'
import { describeRule, detailText } from './describe'

const names = new Map([[3, 'Cさん']])
const nameOf = (id: number) => names.get(id)
const rule = (template_key: Rule['template_key'], params: Record<string, unknown>): Rule => ({ rule_key: 'r1', template_key, params, source_text: '原文' })

describe('지시 규칙 설명 req:W-9 req:M-2', () => {
  it('req:W-9 not_available: 요일 / 날짜, 이름은 ID 로 찾는다', () => {
    expect(describeRule(rule('not_available', { staff_id: 3, weekday: 7 }), nameOf)).toBe('Cさんは毎週日曜日に入れない')
    expect(describeRule(rule('not_available', { staff_id: 9, date: '2026-11-03' }), nameOf)).toBe('スタッフ#9は11/3（火）に入れない')
  })

  it('req:W-9 max_consecutive · min_headcount (staff_id 없음 = 全員, 요일 · 날짜 없음 = 毎日)', () => {
    expect(describeRule(rule('max_consecutive', { shift: 'late', limit: 1 }), nameOf)).toBe('全員の閉店までの勤務の連続は1回まで')
    expect(describeRule(rule('min_headcount', { count: 3, start: '17:00:00', end: '20:00' }), nameOf)).toBe('毎日 17:00–20:00は3人以上')
  })

  it('req:M-2 (깨뜨리기) params 가 계약과 다르면 숨기지 않고 JSON 그대로 보여 준다', () => {
    expect(describeRule(rule('min_headcount', { count: 'many' }), nameOf)).toBe('{"count":"many"}')
  })

  it('req:W-4 위반 detail: 아는 키는 번역, 모르는 키는 key=value', () => {
    // 칸 시각(time)은 목록이 「09:00–10:30」 범위로 따로 보여 주므로 여기서는 뺀다. source 는 말로 바꾼다
    expect(detailText({ time: '13:00', need: 2, have: 1, foo: 'bar' })).toBe('必要 2 · 実際 1 · foo=bar')
    expect(detailText({ need: 4, have: 3, source: 'setting' })).toBe('必要 4 · 実際 3 · 店舗設定の人数')
    expect(detailText({ start: '17:00:00', end: '20:00:00' })).toBe('17:00–20:00')
  })
})

describe('달력 req:W-4 req:W-7', () => {
  it('req:W-4 2026-11 은 30일, 11/1 은 일요일, 11/3 · 11/23 은 공휴일 색', () => {
    const ds = monthDates('2026-11-01')
    expect(ds).toHaveLength(30)
    expect(ds[0]).toBe('2026-11-01')
    expect(isoWeekday('2026-11-01')).toBe(7)
    expect(dayType('2026-11-07')).toBe('sat')
    expect(dayType('2026-11-08')).toBe('sun')
    expect(dayType('2026-11-03')).toBe('holiday')
    expect(dayType('2026-11-04')).toBe('weekday')
  })

  it('req:W-2 <input type=month> 값 → target_month, 틀리면 null', () => {
    expect(monthInputToTargetMonth('2026-12')).toBe('2026-12-01')
    expect(monthInputToTargetMonth('2026-13')).toBeNull()
  })
})
