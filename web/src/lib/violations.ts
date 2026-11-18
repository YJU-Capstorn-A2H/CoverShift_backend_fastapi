// 위반 목록 정리 (W-4): 종류별 묶기 · 예외 허용 가능 여부 · 승인을 막는 것 · 표의 칸 표시.
// 예외 불가: 자격 없음 · 중복 배치 (V9_ko 469, 475, 479). 서버가 waivable=true 로 보내도 화면은 허용 칸을 만들지 않는다.

import type { Severity, Violation } from '../types'
import { cellKey } from './grid'

export const NON_WAIVABLE_CODES: ReadonlySet<string> = new Set(['QUAL_MISSING', 'DOUBLE_BOOKING'])

export function isWaivable(v: Pick<Violation, 'check_code' | 'waivable'>): boolean {
  return v.waivable && !NON_WAIVABLE_CODES.has(v.check_code)
}

function byPlace(a: Violation, b: Violation): number {
  const da = a.work_date ?? ''
  const db = b.work_date ?? ''
  if (da !== db) return da < db ? -1 : 1
  if (a.check_code !== b.check_code) return a.check_code < b.check_code ? -1 : 1
  if ((a.employee_id ?? 0) !== (b.employee_id ?? 0)) return (a.employee_id ?? 0) - (b.employee_id ?? 0)
  const ta = String(a.detail['time'] ?? '')
  const tb = String(b.detail['time'] ?? '')
  return ta < tb ? -1 : ta > tb ? 1 : 0
}

export interface SeverityGroups {
  required: Violation[]
  warning: Violation[]
}

/** 필수(required) / 경고(warning) 로 나누고 날짜 → 코드 → 직원 순으로 정렬한다. */
export function groupBySeverity(vs: readonly Violation[]): SeverityGroups {
  const required = vs.filter((v) => v.severity === 'required').sort(byPlace)
  const warning = vs.filter((v) => v.severity !== 'required').sort(byPlace)
  return { required, warning }
}

/** 보낼 waived_keys: 지금 목록에 있고 예외 허용이 되는 키만 남긴다 (예전 안의 키 · 예외 불가 키는 버린다). */
export function sanitizeWaived(vs: readonly Violation[], waived: Iterable<string>): string[] {
  const allowed = new Set(vs.filter(isWaivable).map((v) => v.key))
  return [...new Set(waived)].filter((k) => allowed.has(k)).sort()
}

/** 승인해도 확정되지 않을 위반 (check_approval 과 같은 기준, F-8 · V9_ko 87):
 * 예외 불가 위반 전부 + 허용하지 않은 required. warning 은 막지 않는다. */
export function blockingViolations(vs: readonly Violation[], waived: ReadonlySet<string>): Violation[] {
  return vs.filter((v) => !isWaivable(v) || (v.severity === 'required' && !waived.has(v.key)))
}

export interface Marks {
  cells: Map<string, Severity> // 직원 + 날짜가 있는 위반
  dates: Map<string, Severity> // 날짜만 있는 위반 (예: 사원 부재 · 자격자 부족 · 인원 부족)
  rows: Map<number, Severity> // 직원만 있는 위반 (예: 사원 출근일수)
}

function bump<K>(m: Map<K, Severity>, k: K, s: Severity) {
  if (m.get(k) !== 'required') m.set(k, s)
}

/** 표에 그릴 표시. 같은 자리에 둘 다 있으면 required 가 이긴다. */
export function marksFor(vs: readonly Violation[]): Marks {
  const marks: Marks = { cells: new Map(), dates: new Map(), rows: new Map() }
  for (const v of vs) {
    if (v.employee_id != null && v.work_date) bump(marks.cells, cellKey(v.employee_id, v.work_date), v.severity)
    else if (v.work_date) bump(marks.dates, v.work_date, v.severity)
    else if (v.employee_id != null) bump(marks.rows, v.employee_id, v.severity)
  }
  return marks
}

/** 칸 · 열 · 줄에 걸린 위반 (칸 설명 · 클릭 시 목록 강조용). */
export function violationsAt(vs: readonly Violation[], employeeId: number | null, workDate: string | null): Violation[] {
  return vs.filter((v) => (employeeId === null || v.employee_id === employeeId) && (workDate === null || v.work_date === workDate))
}

export interface ViolationCounts {
  required: number
  warning: number
  nonWaivable: number
}

export function countViolations(vs: readonly Violation[]): ViolationCounts {
  return {
    required: vs.filter((v) => v.severity === 'required').length,
    warning: vs.filter((v) => v.severity !== 'required').length,
    nonWaivable: vs.filter((v) => !isWaivable(v)).length,
  }
}

/** 화면 한 줄 (W-4). 같은 날 · 같은 코드 · 같은 사람 · 같은 숫자의 30분 칸 위반이 이어지면 한 줄로 합친다.
 * 예외 허용은 묶인 key 전부에 같이 건다 (서버의 waive 단위는 여전히 Violation.key 하나씩). */
export interface ViolationRow {
  keys: string[]
  first: Violation
  start: string | null // 묶인 첫 칸의 시작 HH:MM (칸 위반이 아니면 null)
  end: string | null // 묶인 마지막 칸의 끝 HH:MM
}

const SLOT_KEYS = new Set(['time', 'start_time', 'end_time'])

function addMinutes(hhmm: string, m: number): string {
  const [h = 0, mm = 0] = hhmm.split(':').map(Number)
  const total = h * 60 + mm + m
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`
}

function sameExceptSlot(a: Violation, b: Violation): boolean {
  if (a.check_code !== b.check_code || a.severity !== b.severity || a.work_date !== b.work_date) return false
  if ((a.employee_id ?? null) !== (b.employee_id ?? null) || (a.allowed_at ?? null) !== (b.allowed_at ?? null)) return false
  const ka = Object.keys(a.detail).filter((k) => !SLOT_KEYS.has(k))
  const kb = Object.keys(b.detail).filter((k) => !SLOT_KEYS.has(k))
  return ka.length === kb.length && ka.every((k) => JSON.stringify(a.detail[k]) === JSON.stringify(b.detail[k]))
}

export function mergeSlotRows(vs: readonly Violation[]): ViolationRow[] {
  const rows: ViolationRow[] = []
  for (const v of vs) {
    const t = typeof v.detail['time'] === 'string' ? (v.detail['time'] as string).slice(0, 5) : null
    const prev = rows[rows.length - 1]
    if (t && prev && prev.end === t && sameExceptSlot(prev.first, v)) {
      prev.keys.push(v.key)
      prev.end = addMinutes(t, 30)
      continue
    }
    rows.push({ keys: [v.key], first: v, start: t, end: t ? addMinutes(t, 30) : null })
  }
  return rows
}
