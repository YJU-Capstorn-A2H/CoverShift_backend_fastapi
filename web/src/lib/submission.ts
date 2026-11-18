// 직원 제출 페이지 (W-7) 의 입력 검사 · 저장 차이 계산.
// 규칙: available · half_leave 만 시간 필수, off · paid_leave 는 시간 없음, 30분 단위, 끝 > 시작 (V9_ko 165, A-12).
// 상한: 사원의 off 수 ≤ off_request_limit, available 수 ≤ work_request_limit. half_leave 는 세지 않는다 (V9_ko 165).
// 서버가 최종 판단한다 (422 LIMIT_EXCEEDED · INVALID_ITEM). 화면 검사는 보내기 전에 알려 주는 용도.

import type { AvailabilityKind, SubmissionItem, SubmitItemInput } from '../types'
import { isHalfHour, normTime, toMinutes } from './time'

/** none = 행 없음 (사원: 희망 없음 → 배치 가능 / 파트 · 알바: 나올 수 없음, V9_ko 308-309 · G19) */
export type DayChoice = AvailabilityKind | 'none'

export interface DayEntry {
  work_date: string
  kind: DayChoice
  start_time: string | null
  end_time: string | null
}

export type ItemError = 'TIME_REQUIRED' | 'NOT_HALF_HOUR' | 'END_NOT_AFTER_START' | 'TIME_NOT_ALLOWED'

export function needsTime(kind: DayChoice): boolean {
  return kind === 'available' || kind === 'half_leave'
}

/** 한 날의 입력 검사. 문제가 없으면 null. */
export function validateEntry(e: DayEntry): ItemError | null {
  if (e.kind === 'none') return null
  if (!needsTime(e.kind)) {
    return e.start_time || e.end_time ? 'TIME_NOT_ALLOWED' : null
  }
  if (!e.start_time || !e.end_time) return 'TIME_REQUIRED'
  if (!isHalfHour(e.start_time) || !isHalfHour(e.end_time)) return 'NOT_HALF_HOUR'
  if (toMinutes(e.end_time) <= toMinutes(e.start_time)) return 'END_NOT_AFTER_START'
  return null
}

/** 종류를 바꿀 때: 시간이 필요 없는 종류면 시간을 지운다 (화면이 TIME_NOT_ALLOWED 를 만들지 않게). */
export function changeKind(e: DayEntry, kind: DayChoice, defaults: { start: string; end: string }): DayEntry {
  if (!needsTime(kind)) return { ...e, kind, start_time: null, end_time: null }
  return { ...e, kind, start_time: e.start_time ?? defaults.start, end_time: e.end_time ?? defaults.end }
}

/** 서버 행 → 달의 날마다 한 줄 (없는 날은 none). */
export function entriesFromItems(dates: readonly string[], items: readonly SubmissionItem[]): DayEntry[] {
  const byDate = new Map(items.map((i) => [i.work_date, i]))
  return dates.map((d) => {
    const it = byDate.get(d)
    if (!it) return { work_date: d, kind: 'none', start_time: null, end_time: null }
    return { work_date: d, kind: it.kind, start_time: normTime(it.start_time), end_time: normTime(it.end_time) }
  })
}

/** 보낼 모양: 시간이 필요 없는 종류는 시간 키를 아예 빼고 보낸다. */
export function toItemInput(e: DayEntry): SubmitItemInput {
  if (e.kind === 'none') throw new Error('none 은 DELETE 로 보낸다')
  if (needsTime(e.kind)) return { work_date: e.work_date, kind: e.kind, start_time: e.start_time ?? undefined, end_time: e.end_time ?? undefined }
  return { work_date: e.work_date, kind: e.kind }
}

export interface LimitCheck {
  offCount: number
  workCount: number
  offOver: boolean
  workOver: boolean
}

/** 상한 확인. 사원만 센다 (V9_ko 165 「사원의 off 개수・available 개수」). 상한 null = 제한 없음. */
export function checkLimits(
  entries: readonly DayEntry[],
  opts: { is_regular: boolean; off_request_limit: number | null; work_request_limit: number | null },
): LimitCheck {
  const offCount = entries.filter((e) => e.kind === 'off').length
  const workCount = entries.filter((e) => e.kind === 'available').length
  if (!opts.is_regular) return { offCount, workCount, offOver: false, workOver: false }
  return {
    offCount,
    workCount,
    offOver: opts.off_request_limit !== null && offCount > opts.off_request_limit,
    workOver: opts.work_request_limit !== null && workCount > opts.work_request_limit,
  }
}

function sameEntry(a: DayEntry, b: DayEntry): boolean {
  if (a.kind !== b.kind) return false
  if (!needsTime(a.kind)) return true
  return a.start_time === b.start_time && a.end_time === b.end_time
}

/** 저장할 것: 바뀐 날은 PUT(그 날짜만 UPSERT), none 으로 바꾼 날은 DELETE (API_CONTRACT.md 제출 표). */
export function diffSubmission(original: readonly DayEntry[], edited: readonly DayEntry[]): { upserts: SubmitItemInput[]; deletes: string[] } {
  const before = new Map(original.map((e) => [e.work_date, e]))
  const upserts: SubmitItemInput[] = []
  const deletes: string[] = []
  for (const e of edited) {
    const o = before.get(e.work_date) ?? { work_date: e.work_date, kind: 'none' as const, start_time: null, end_time: null }
    if (sameEntry(o, e)) continue
    if (e.kind === 'none') {
      if (o.kind !== 'none') deletes.push(e.work_date)
    } else upserts.push(toItemInput(e))
  }
  return { upserts, deletes }
}
