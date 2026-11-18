// 표 ↔ 배정 목록 변환 (W-4 표시, W-6 편집 저장 = edit_saved 의 assignments 전체 목록).
import { describe, expect, it } from 'vitest'
import type { DraftAssignment } from '../types'
import { assignmentsToGrid, cellKey, changedKeys, clearCell, gridToAssignments, headcountByDate, setWork, workDaysByEmployee } from './grid'

const rows: DraftAssignment[] = [
  { employee_id: 2, employee_name: 'Bさん', work_date: '2026-11-02', kind: 'work', start_time: '13:00:00', end_time: '22:30:00', break_start: null, break_end: null },
  { employee_id: 1, employee_name: 'Aさん', work_date: '2026-11-01', kind: 'work', start_time: '08:00', end_time: '17:00', break_start: '12:00', break_end: '13:00' },
  { employee_id: 1, employee_name: 'Aさん', work_date: '2026-11-02', kind: 'paid_leave', start_time: null, end_time: null, break_start: null, break_end: null },
]

describe('표 ↔ 배정 req:W-6 req:M-3', () => {
  it('req:W-4 배정 목록 → 표: 근무 · 유급 칸이 생기고 초는 떨어진다', () => {
    const { grid, conflicts, invalid } = assignmentsToGrid(rows)
    expect(conflicts).toEqual([])
    expect(invalid).toEqual([])
    expect(grid.get(cellKey(2, '2026-11-02'))).toEqual({ kind: 'work', start: '13:00', end: '22:30', breakStart: null, breakEnd: null })
    expect(grid.get(cellKey(1, '2026-11-02'))).toEqual({ kind: 'paid_leave' })
  })

  it('req:W-6 바꾸지 않고 되돌리면 같은 목록 (날짜 → 직원 순, 유급은 시각 null)', () => {
    const out = gridToAssignments(assignmentsToGrid(rows).grid)
    expect(out).toEqual([
      { employee_id: 1, work_date: '2026-11-01', kind: 'work', start_time: '08:00', end_time: '17:00', break_start: '12:00', break_end: '13:00' },
      { employee_id: 1, work_date: '2026-11-02', kind: 'paid_leave', start_time: null, end_time: null, break_start: null, break_end: null },
      { employee_id: 2, work_date: '2026-11-02', kind: 'work', start_time: '13:00', end_time: '22:30', break_start: null, break_end: null },
    ])
  })

  it('req:W-6 칸 편집: 새 시각이 목록에 들어가고 원래 표는 그대로', () => {
    const { grid } = assignmentsToGrid(rows)
    const r = setWork(grid, cellKey(3, '2026-11-01'), '09:00', '15:30')
    if (!('grid' in r)) throw new Error('편집 실패')
    expect(grid.has(cellKey(3, '2026-11-01'))).toBe(false)
    const out = gridToAssignments(r.grid)
    expect(out).toContainEqual({ employee_id: 3, work_date: '2026-11-01', kind: 'work', start_time: '09:00', end_time: '15:30', break_start: null, break_end: null })
    expect(changedKeys(grid, r.grid)).toEqual(new Set([cellKey(3, '2026-11-01')]))
  })

  it('req:W-6 근무를 줄여 휴게가 밖으로 나가면 휴게를 지운다 (DB CHECK 를 어기는 값을 보내지 않음)', () => {
    const { grid } = assignmentsToGrid(rows)
    const r = setWork(grid, cellKey(1, '2026-11-01'), '08:00', '11:30')
    if (!('grid' in r)) throw new Error('편집 실패')
    const a = gridToAssignments(r.grid).find((x) => x.employee_id === 1 && x.work_date === '2026-11-01')
    expect(a).toMatchObject({ start_time: '08:00', end_time: '11:30', break_start: null, break_end: null })
  })

  it('req:W-6 (깨뜨리기) 30분이 아닌 시각 · 끝 ≤ 시작 · 유급 칸 편집은 거부', () => {
    const { grid } = assignmentsToGrid(rows)
    expect(setWork(grid, cellKey(3, '2026-11-01'), '09:15', '15:00')).toEqual({ error: 'NOT_HALF_HOUR' })
    expect(setWork(grid, cellKey(3, '2026-11-01'), '15:00', '15:00')).toEqual({ error: 'END_NOT_AFTER_START' })
    expect(setWork(grid, cellKey(1, '2026-11-02'), '09:00', '15:00')).toEqual({ error: 'PAID_LEAVE_LOCKED' })
    expect(clearCell(grid, cellKey(1, '2026-11-02'))).toEqual({ error: 'PAID_LEAVE_LOCKED' })
  })

  it('req:W-6 칸 비우기: 목록에서 그 행이 빠진다', () => {
    const { grid } = assignmentsToGrid(rows)
    const r = clearCell(grid, cellKey(2, '2026-11-02'))
    if (!('grid' in r)) throw new Error('비우기 실패')
    expect(gridToAssignments(r.grid).some((x) => x.employee_id === 2)).toBe(false)
  })

  it('req:W-4 같은 사람 · 같은 날 두 번째 행은 conflicts 로 따로 알린다 (조용히 버리지 않음)', () => {
    const dup = [...rows, { ...rows[0]!, start_time: '09:00', end_time: '12:00' }]
    const { grid, conflicts } = assignmentsToGrid(dup)
    expect(conflicts).toHaveLength(1)
    expect(grid.get(cellKey(2, '2026-11-02'))).toMatchObject({ start: '13:00' })
  })

  it('req:W-4 합계: 날짜별 인원 · 직원별 출근일에 유급은 세지 않는다', () => {
    const { grid } = assignmentsToGrid(rows)
    expect(headcountByDate(grid, ['2026-11-01', '2026-11-02'])).toEqual(new Map([['2026-11-01', 1], ['2026-11-02', 1]]))
    expect(workDaysByEmployee(grid)).toEqual(new Map([[1, 1], [2, 1]]))
  })
})
