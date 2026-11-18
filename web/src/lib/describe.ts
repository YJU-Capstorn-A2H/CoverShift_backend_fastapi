// 틀 규칙 · 위반을 사람이 읽는 글로 바꾼다 (W-9 확인 표, W-4 위반 목록).
// params 모양은 app/domain.py (NotAvailableParams · MaxConsecutiveParams · MinHeadcountParams) 를 따른다.

import { dateLabel, t, tk, weekdayLabel } from '../i18n'
import type { Rule, Violation } from '../types'
import { normTime } from './time'

export type NameOf = (employeeId: number) => string | undefined

function staffName(id: unknown, nameOf: NameOf): string {
  if (typeof id !== 'number') return t('ruleDesc.everyone')
  return nameOf(id) ?? t('ruleDesc.staffId', { id })
}

/** 틀 이름 (모르는 틀이면 그대로) */
export function templateLabel(key: string): string {
  return tk(`tpl.${key}`, key)
}

/** 틀 규칙 한 줄 설명. 모양이 계약과 다르면 params 를 JSON 그대로 보여 준다 (숨기지 않는다). */
export function describeRule(rule: Rule, nameOf: NameOf): string {
  const p = rule.params
  switch (rule.template_key) {
    case 'not_available': {
      const staff = staffName(p['staff_id'], nameOf)
      if (typeof p['weekday'] === 'number') return t('ruleDesc.not_available.weekday', { staff, wd: weekdayLabel(p['weekday']) })
      if (typeof p['date'] === 'string') return t('ruleDesc.not_available.date', { staff, date: dateLabel(p['date']) })
      break
    }
    case 'max_consecutive': {
      const staff = staffName(p['staff_id'], nameOf)
      if (typeof p['limit'] !== 'number') break
      return p['shift'] === 'late'
        ? t('ruleDesc.max_consecutive.late', { staff, limit: p['limit'] })
        : t('ruleDesc.max_consecutive.any', { staff, limit: p['limit'] })
    }
    case 'min_headcount': {
      const start = normTime(typeof p['start'] === 'string' ? p['start'] : null)
      const end = normTime(typeof p['end'] === 'string' ? p['end'] : null)
      if (typeof p['count'] !== 'number' || !start || !end) break
      const when =
        typeof p['date'] === 'string'
          ? dateLabel(p['date'])
          : typeof p['weekday'] === 'number'
            ? t('ruleDesc.weekday', { wd: weekdayLabel(p['weekday']) })
            : t('ruleDesc.everyday')
      return t('ruleDesc.min_headcount', { when, start, end, count: p['count'] })
    }
  }
  return JSON.stringify(p)
}

/** 위반 코드 이름 */
export function checkLabel(code: string): string {
  return tk(`code.${code}`, code)
}

/** 위반 detail 을 짧은 글로. 아는 키는 번역하고, 모르는 키는 key=value 로 그대로 보여 준다. */
export function detailText(detail: Record<string, unknown>): string {
  const parts: string[] = []
  const start = detail['start']
  const end = detail['end']
  if (typeof start === 'string' && typeof end === 'string') parts.push(`${normTime(start) ?? start}–${normTime(end) ?? end}`)
  for (const [k, v] of Object.entries(detail)) {
    if (k === 'start' || k === 'end') {
      if (typeof start === 'string' && typeof end === 'string') continue
    }
    if (v === null || v === undefined) continue
    if (k === 'time' || k === 'start_time' || k === 'end_time' || k === 'severity') continue // 칸은 목록이 범위로 보여 준다
    if (k === 'source') {
      parts.push(tk(`source.${String(v)}`, String(v)))
      continue
    }
    const value = typeof v === 'string' ? (normTime(v) ?? v) : typeof v === 'object' ? JSON.stringify(v) : String(v)
    parts.push(tk(`detail.${k}`, `${k}=${value}`, { v: value }))
  }
  return parts.join(' · ')
}

export function violationWhere(v: Violation, nameOf: NameOf): string {
  const who = v.employee_id != null ? (nameOf(v.employee_id) ?? t('ruleDesc.staffId', { id: v.employee_id })) : ''
  const when = v.work_date ? dateLabel(v.work_date) : ''
  if (who && when) return `${when} · ${who}`
  if (when) return `${when} · ${t('viol.wholeDay')}`
  if (who) return `${who} · ${t('viol.wholeMonth')}`
  return ''
}
