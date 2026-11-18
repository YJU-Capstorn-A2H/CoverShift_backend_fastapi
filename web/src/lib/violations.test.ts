// 위반 묶기 · 예외 허용 걸러내기 (W-4). QUAL_MISSING · DOUBLE_BOOKING 은 어떤 경우에도 예외 불가 (V9_ko 479).
import { describe, expect, it } from 'vitest'
import type { Violation } from '../types'
import { cellKey } from './grid'
import { blockingViolations, countViolations, groupBySeverity, isWaivable, marksFor, sanitizeWaived, mergeSlotRows } from './violations'

function v(p: Partial<Violation> & Pick<Violation, 'check_code' | 'severity'>): Violation {
  return {
    key: `${p.check_code}|${p.work_date ?? ''}|${p.employee_id ?? ''}|${(p.detail?.['time'] as string) ?? ''}|`,
    waivable: true,
    work_date: null,
    employee_id: null,
    detail: {},
    ...p,
  }
}

const vs: Violation[] = [
  v({ check_code: 'HEADCOUNT_SHORTAGE', severity: 'warning', work_date: '2026-11-03', detail: { time: '13:00', need: 3, have: 2 } }),
  v({ check_code: 'QUAL_MISSING', severity: 'required', work_date: '2026-11-05', detail: { time: '09:00' }, waivable: false }),
  v({ check_code: 'OUTSIDE_AVAILABILITY', severity: 'required', work_date: '2026-11-02', employee_id: 4 }),
  v({ check_code: 'DOUBLE_BOOKING', severity: 'required', work_date: '2026-11-02', employee_id: 4, waivable: false }),
  v({ check_code: 'REGULAR_DAYS_MISMATCH', severity: 'required', employee_id: 1 }),
]

describe('위반 정리 req:W-4 req:M-3', () => {
  it('req:W-4 severity 로 나누고 날짜 → 코드 순으로 정렬', () => {
    const g = groupBySeverity(vs)
    expect(g.required.map((x) => x.check_code)).toEqual(['REGULAR_DAYS_MISMATCH', 'DOUBLE_BOOKING', 'OUTSIDE_AVAILABILITY', 'QUAL_MISSING'])
    expect(g.warning.map((x) => x.check_code)).toEqual(['HEADCOUNT_SHORTAGE'])
  })

  it('req:W-4 (깨뜨리기) 서버가 waivable=true 로 보내도 QUAL_MISSING · DOUBLE_BOOKING 은 예외 불가', () => {
    expect(isWaivable({ check_code: 'QUAL_MISSING', waivable: true })).toBe(false)
    expect(isWaivable({ check_code: 'DOUBLE_BOOKING', waivable: true })).toBe(false)
    expect(isWaivable({ check_code: 'OUTSIDE_AVAILABILITY', waivable: true })).toBe(true)
    // 서버가 막은 것은 화면도 막는다
    expect(isWaivable({ check_code: 'OUTSIDE_AVAILABILITY', waivable: false })).toBe(false)
  })

  it('req:W-4 보낼 waived_keys 에서 예외 불가 키 · 지금 목록에 없는 키는 빠진다', () => {
    const qual = vs[1]!.key
    const outside = vs[2]!.key
    const dbl = vs[3]!.key
    expect(sanitizeWaived(vs, [outside, qual, dbl, 'OLD|key', outside])).toEqual([outside])
  })

  it('req:W-4 승인을 막는 것: 예외 불가 전부 + 허용 안 한 required. warning 은 막지 않는다 (F-8 과 같은 기준)', () => {
    const none = blockingViolations(vs, new Set())
    expect(none.map((x) => x.check_code).sort()).toEqual(['DOUBLE_BOOKING', 'OUTSIDE_AVAILABILITY', 'QUAL_MISSING', 'REGULAR_DAYS_MISMATCH'])
    const waivedAll = blockingViolations(vs, new Set(vs.map((x) => x.key)))
    expect(waivedAll.map((x) => x.check_code).sort()).toEqual(['DOUBLE_BOOKING', 'QUAL_MISSING'])
  })

  it('req:W-4 표 표시: 칸 · 날짜 열 · 직원 줄로 나누고 같은 자리는 required 가 이긴다', () => {
    const m = marksFor(vs)
    expect(m.cells.get(cellKey(4, '2026-11-02'))).toBe('required')
    expect(m.dates.get('2026-11-03')).toBe('warning')
    expect(m.dates.get('2026-11-05')).toBe('required')
    expect(m.rows.get(1)).toBe('required')
    const mixed = marksFor([v({ check_code: 'A', severity: 'warning', work_date: '2026-11-09' }), v({ check_code: 'B', severity: 'required', work_date: '2026-11-09' })])
    expect(mixed.dates.get('2026-11-09')).toBe('required')
  })

  it('req:W-4 건수 요약', () => {
    expect(countViolations(vs)).toEqual({ required: 4, warning: 1, nonWaivable: 2 })
  })
})

describe('이어지는 30분 칸 묶기 (req:W-4)', () => {
  const slot = (time: string, extra: Record<string, unknown> = {}, over: Partial<Violation> = {}): Violation => ({
    key: `HEADCOUNT_SHORTAGE|2026-11-04||${time}|`, check_code: 'HEADCOUNT_SHORTAGE', severity: 'warning', waivable: true,
    work_date: '2026-11-04', employee_id: null, detail: { time, need: 4, have: 3, source: 'setting', ...extra }, ...over,
  })

  it('같은 날 이어지고 숫자가 같은 칸은 한 줄, 범위는 첫 칸 시작 ~ 마지막 칸 끝 (req:W-4)', () => {
    const rows = mergeSlotRows([slot('09:00'), slot('09:30'), slot('10:00')])
    expect(rows).toHaveLength(1)
    expect(rows[0]?.start).toBe('09:00')
    expect(rows[0]?.end).toBe('10:30')
    expect(rows[0]?.keys).toHaveLength(3)
  })

  it('(깨뜨리기) 숫자가 다르거나 끊기거나 날짜가 다르면 합치지 않는다 (req:W-4)', () => {
    const rows = mergeSlotRows([
      slot('09:00'),
      slot('09:30', { have: 2 }),
      slot('11:00', { have: 2 }),
      slot('11:30', { have: 2 }, { work_date: '2026-11-05', key: 'x' }),
    ])
    expect(rows.map((r) => [r.first.work_date, r.start, r.end])).toEqual([
      ['2026-11-04', '09:00', '09:30'],
      ['2026-11-04', '09:30', '10:00'],
      ['2026-11-04', '11:00', '11:30'],
      ['2026-11-05', '11:30', '12:00'],
    ])
  })

  it('칸이 아닌 위반(시각 없음)은 그대로 한 줄씩 (req:W-4)', () => {
    const v = slot('09:00')
    const a = { ...v, key: 'a', detail: { need: 21, have: 19 } }
    const b = { ...v, key: 'b', detail: { need: 21, have: 19 } }
    const rows = mergeSlotRows([a, b])
    expect(rows).toHaveLength(2)
    expect(rows[0]?.start).toBeNull()
  })
})
