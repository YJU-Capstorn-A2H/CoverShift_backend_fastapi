// 설정 화면 (W-2) 입력 검사. DB 제약을 미리 알려 주는 용도이고, 최종 판단은 서버(409 · 422)가 한다.

import type { EmployeeInput, TimeRule, TimeRuleInput } from '../types'
import { isHalfHour, toMinutes } from './time'

export type RuleFormError = 'WEEKDAY' | 'NOT_HALF_HOUR' | 'END_NOT_AFTER_START' | 'HEADCOUNT_MIN' | 'QUALIFIED_MIN' | 'OVERLAP'

/** store_time_rules 의 CHECK 와 같은 조건 (V9_ko 2-4: min_headcount >= 1, min_qualified >= 0, end > start)
 * + 같은 요일 시간대 겹침 (DB EXCLUDE → 409 TIME_RULE_OVERLAP). exceptRuleId = 수정 중인 자기 자신. */
export function validateTimeRule(input: TimeRuleInput, existing: readonly TimeRule[], exceptRuleId: number | null = null): RuleFormError | null {
  if (!Number.isInteger(input.weekday) || input.weekday < 1 || input.weekday > 7) return 'WEEKDAY'
  if (!isHalfHour(input.start_time) || !isHalfHour(input.end_time)) return 'NOT_HALF_HOUR'
  const s = toMinutes(input.start_time)
  const e = toMinutes(input.end_time)
  if (e <= s) return 'END_NOT_AFTER_START'
  if (!Number.isInteger(input.min_headcount) || input.min_headcount < 1) return 'HEADCOUNT_MIN'
  if (!Number.isInteger(input.min_qualified) || input.min_qualified < 0) return 'QUALIFIED_MIN'
  const overlap = existing.some(
    (r) => r.rule_id !== exceptRuleId && r.weekday === input.weekday && toMinutes(r.start_time) < e && s < toMinutes(r.end_time),
  )
  return overlap ? 'OVERLAP' : null
}

export type EmployeeFormError = 'NAME_REQUIRED' | 'WEEKLY_DAYS_RANGE' | 'FIXED_WEEKDAYS_RANGE' | 'WEEKLY_FIXED_MISMATCH'

/** employees 의 CHECK 와 같은 조건 (V9_ko 2-4):
 * weekly_days 1~7, fixed_weekdays ⊂ {1..7}, 둘 다 있으면 weekly_days = 고정 요일 수 (어기면 서버 422 CHECK_VIOLATION). */
export function validateEmployee(input: EmployeeInput): EmployeeFormError | null {
  if (!input.name.trim()) return 'NAME_REQUIRED'
  if (input.weekly_days !== null && (!Number.isInteger(input.weekly_days) || input.weekly_days < 1 || input.weekly_days > 7)) return 'WEEKLY_DAYS_RANGE'
  if (input.fixed_weekdays !== null && input.fixed_weekdays.some((w) => !Number.isInteger(w) || w < 1 || w > 7)) return 'FIXED_WEEKDAYS_RANGE'
  if (input.weekly_days !== null && input.fixed_weekdays !== null && input.weekly_days !== new Set(input.fixed_weekdays).size) return 'WEEKLY_FIXED_MISMATCH'
  return null
}

/** 고정 요일 체크 상자 → 보낼 값: 중복 제거 · 정렬, 하나도 없으면 null (= 고정 없음). */
export function normalizeFixedWeekdays(checked: readonly number[]): number[] | null {
  const uniq = [...new Set(checked)].sort((a, b) => a - b)
  return uniq.length ? uniq : null
}

/** 숫자 입력 칸 → 정수 또는 null (빈칸 = 제한 없음). */
export function optionalInt(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined) return null
  const s = String(value).trim()
  if (s === '') return null
  const n = Number(s)
  return Number.isInteger(n) ? n : null
}
