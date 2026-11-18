// 편성안 표(직원 × 날짜) ↔ 배정 목록 변환 (W-4 표시, W-6 편집 저장).
// 편집 저장은 계약대로 「배정 전체 목록」을 보낸다 (API_CONTRACT.md ReviewBody edit_saved).
// 한 사람 · 한 날은 한 칸이다: shift_assignments PK(draft_id, employee_id, work_date) (V9_ko 2-4 DDL).

import type { AssignmentOut, DraftAssignment } from '../types'
import { isHalfHour, normTime, toMinutes } from './time'

export interface WorkCell {
  kind: 'work'
  start: string
  end: string
  breakStart: string | null // 기록만. 검사는 안 본다 (V9_ko 351)
  breakEnd: string | null
}
export interface PaidLeaveCell {
  kind: 'paid_leave' // 시프트에는 실리지만 출근이 아니다 (V9_ko 300, F-16)
}
export type Cell = WorkCell | PaidLeaveCell

/** 표의 한 줄 (직원). 표시용 표식(사원 · 등판 · 마감)은 직원 API 에서 오고, 못 읽으면 null. */
export interface GridRow {
  employee_id: number
  name: string
  is_regular: boolean | null
  has_qualification: boolean | null
  closing_ok: boolean | null
}
export type Grid = Map<string, Cell>

/** 표의 줄: 직원 목록 + 배정에만 있는 직원(이름은 배정의 employee_name). 사원 먼저, 그 안에서 ID 순. */
export function gridRows(
  employees: readonly { employee_id: number; name: string; is_regular: boolean; has_qualification: boolean; closing_ok: boolean }[],
  assignments: readonly DraftAssignment[],
): GridRow[] {
  const rows = new Map<number, GridRow>()
  for (const e of employees) rows.set(e.employee_id, { employee_id: e.employee_id, name: e.name, is_regular: e.is_regular, has_qualification: e.has_qualification, closing_ok: e.closing_ok })
  for (const a of assignments) {
    if (!rows.has(a.employee_id)) rows.set(a.employee_id, { employee_id: a.employee_id, name: a.employee_name, is_regular: null, has_qualification: null, closing_ok: null })
  }
  const rank = (r: GridRow) => (r.is_regular === true ? 0 : r.is_regular === false ? 1 : 2)
  return [...rows.values()].sort((a, b) => rank(a) - rank(b) || a.employee_id - b.employee_id)
}

export function cellKey(employeeId: number, workDate: string): string {
  return `${employeeId}|${workDate}`
}

export function parseCellKey(key: string): { employeeId: number; workDate: string } {
  const [id, date] = key.split('|') as [string, string]
  return { employeeId: Number(id), workDate: date }
}

export interface GridBuild {
  grid: Grid
  /** 같은 사람 · 같은 날의 두 번째 이후 행. 표에는 한 칸만 보이므로 화면이 따로 알린다. */
  conflicts: AssignmentOut[]
  /** 시각이 비었거나 형식이 틀려 칸으로 만들지 못한 행 */
  invalid: AssignmentOut[]
}

/** API 배정 목록 → 표. 먼저 나온 행이 칸을 차지한다. */
export function assignmentsToGrid(rows: readonly (AssignmentOut | DraftAssignment)[]): GridBuild {
  const grid: Grid = new Map()
  const conflicts: AssignmentOut[] = []
  const invalid: AssignmentOut[] = []
  for (const row of rows) {
    const key = cellKey(row.employee_id, row.work_date)
    if (grid.has(key)) {
      conflicts.push(row)
      continue
    }
    if (row.kind === 'paid_leave') {
      grid.set(key, { kind: 'paid_leave' })
      continue
    }
    const start = normTime(row.start_time)
    const end = normTime(row.end_time)
    if (start === null || end === null) {
      invalid.push(row)
      continue
    }
    grid.set(key, {
      kind: 'work',
      start,
      end,
      breakStart: normTime(row.break_start ?? null),
      breakEnd: normTime(row.break_end ?? null),
    })
  }
  return { grid, conflicts, invalid }
}

/** 휴게가 근무 안에 들어가는가 (DB CHECK: break_start >= start_time AND break_end <= end_time). */
function breakFits(cell: WorkCell): boolean {
  if (cell.breakStart === null || cell.breakEnd === null) return false
  const bs = toMinutes(cell.breakStart)
  const be = toMinutes(cell.breakEnd)
  return bs < be && bs >= toMinutes(cell.start) && be <= toMinutes(cell.end)
}

/** 표 → API 배정 목록 (edit_saved 의 assignments). 날짜 → 직원 순으로 정렬한다.
 * 휴게가 바뀐 근무 시간 밖으로 나가면 휴게를 지운다 (DB CHECK 위반을 보내지 않기 위해). */
export function gridToAssignments(grid: Grid): AssignmentOut[] {
  const out: AssignmentOut[] = []
  for (const [key, cell] of grid) {
    const { employeeId, workDate } = parseCellKey(key)
    if (cell.kind === 'paid_leave') {
      out.push({ employee_id: employeeId, work_date: workDate, kind: 'paid_leave', start_time: null, end_time: null, break_start: null, break_end: null })
      continue
    }
    const keepBreak = breakFits(cell)
    out.push({
      employee_id: employeeId,
      work_date: workDate,
      kind: 'work',
      start_time: cell.start,
      end_time: cell.end,
      break_start: keepBreak ? cell.breakStart : null,
      break_end: keepBreak ? cell.breakEnd : null,
    })
  }
  out.sort((a, b) => (a.work_date === b.work_date ? a.employee_id - b.employee_id : a.work_date < b.work_date ? -1 : 1))
  return out
}

export type EditError = 'NOT_HALF_HOUR' | 'END_NOT_AFTER_START' | 'PAID_LEAVE_LOCKED'

/** 칸에 근무 시각을 넣는다. 원래 표는 건드리지 않고 새 표를 돌려준다.
 * (가정 신규) 유급 칸은 편집 모드에서 바꾸지 않는다 — 유급은 코드가 희망에서 채우므로 (F-16, V9_ko 300). */
export function setWork(grid: Grid, key: string, start: string, end: string): { grid: Grid } | { error: EditError } {
  const current = grid.get(key)
  if (current?.kind === 'paid_leave') return { error: 'PAID_LEAVE_LOCKED' }
  if (!isHalfHour(start) || !isHalfHour(end)) return { error: 'NOT_HALF_HOUR' }
  const s = normTime(start) as string
  const e = normTime(end) as string
  if (toMinutes(e) <= toMinutes(s)) return { error: 'END_NOT_AFTER_START' }
  const next = new Map(grid)
  next.set(key, {
    kind: 'work',
    start: s,
    end: e,
    breakStart: current?.kind === 'work' ? current.breakStart : null,
    breakEnd: current?.kind === 'work' ? current.breakEnd : null,
  })
  return { grid: next }
}

/** 칸을 비운다 (그 날 배치 없음). */
export function clearCell(grid: Grid, key: string): { grid: Grid } | { error: EditError } {
  if (grid.get(key)?.kind === 'paid_leave') return { error: 'PAID_LEAVE_LOCKED' }
  const next = new Map(grid)
  next.delete(key)
  return { grid: next }
}

function sameCell(a: Cell | undefined, b: Cell | undefined): boolean {
  if (a === undefined || b === undefined) return a === b
  if (a.kind !== b.kind) return false
  if (a.kind === 'paid_leave') return true
  const bw = b as WorkCell
  return a.start === bw.start && a.end === bw.end
}

/** 원래 표와 달라진 칸의 키 (편집 표시 · 「변경 n칸」). */
export function changedKeys(original: Grid, edited: Grid): Set<string> {
  const keys = new Set<string>([...original.keys(), ...edited.keys()])
  const out = new Set<string>()
  for (const k of keys) if (!sameCell(original.get(k), edited.get(k))) out.add(k)
  return out
}

/** 날짜별 근무 인원 (표 아래 합계 줄). 유급은 세지 않는다. */
export function headcountByDate(grid: Grid, dates: readonly string[]): Map<string, number> {
  const out = new Map<string, number>(dates.map((d) => [d, 0]))
  for (const [key, cell] of grid) {
    if (cell.kind !== 'work') continue
    const { workDate } = parseCellKey(key)
    if (out.has(workDate)) out.set(workDate, (out.get(workDate) ?? 0) + 1)
  }
  return out
}

/** 직원별 출근 일수 (표 오른쪽 합계). 유급은 출근이 아니다 (F-16). */
export function workDaysByEmployee(grid: Grid): Map<number, number> {
  const out = new Map<number, number>()
  for (const [key, cell] of grid) {
    if (cell.kind !== 'work') continue
    const { employeeId } = parseCellKey(key)
    out.set(employeeId, (out.get(employeeId) ?? 0) + 1)
  }
  return out
}
