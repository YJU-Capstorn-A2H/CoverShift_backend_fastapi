// 30분 선택지 생성 (W-6 편집 모드, W-7 제출 페이지). 근거 V9_ko 165 · 447.
import { describe, expect, it } from 'vitest'
import { DAY_FIRST, DAY_LAST, endOptions, halfHourOptions, isHalfHour, normTime, startOptions, toMinutes } from './time'

describe('30분 선택지 req:W-6 req:M-3', () => {
  it('req:W-6 06:00~23:30 은 30분 간격 36개, 전부 분이 00 또는 30', () => {
    const opts = halfHourOptions(DAY_FIRST, DAY_LAST)
    expect(opts).toHaveLength(36)
    expect(opts[0]).toBe('06:00')
    expect(opts.at(-1)).toBe('23:30')
    expect(opts.every((o) => /^\d{2}:(00|30)$/.test(o))).toBe(true)
    // 간격이 정확히 30분
    for (let i = 1; i < opts.length; i++) expect(toMinutes(opts[i]!) - toMinutes(opts[i - 1]!)).toBe(30)
  })

  it('req:W-6 30분 단위가 아닌 시작은 안쪽(다음 30분)으로 맞춘다', () => {
    expect(halfHourOptions('08:10', '09:30')).toEqual(['08:30', '09:00', '09:30'])
  })

  it('req:W-6 (깨뜨리기) 끝이 시작보다 앞이거나 형식이 틀리면 빈 목록', () => {
    expect(halfHourOptions('10:00', '09:00')).toEqual([])
    expect(halfHourOptions('ab:cd', '09:00')).toEqual([])
  })

  it('req:W-6 시작 선택지는 마지막 칸을 빼고, 끝 선택지는 시작보다 뒤만', () => {
    expect(startOptions('08:00', '09:30')).toEqual(['08:00', '08:30', '09:00'])
    expect(endOptions('08:30', '08:00', '09:30')).toEqual(['09:00', '09:30'])
    expect(endOptions(null, '08:00', '09:30')).toEqual(['08:30', '09:00', '09:30'])
  })

  it('req:W-7 isHalfHour: 00 · 30 만 통과, 15 · 45 · 형식 오류는 실패', () => {
    expect(isHalfHour('09:00')).toBe(true)
    expect(isHalfHour('09:30')).toBe(true)
    expect(isHalfHour('09:15')).toBe(false)
    expect(isHalfHour('09:45')).toBe(false)
    expect(isHalfHour('9:00')).toBe(false)
    expect(isHalfHour(null)).toBe(false)
  })

  it('req:W-4 API 가 초를 붙여 보내도 HH:MM 으로 읽는다', () => {
    expect(normTime('09:30:00')).toBe('09:30')
    expect(normTime('25:00')).toBeNull()
  })
})
