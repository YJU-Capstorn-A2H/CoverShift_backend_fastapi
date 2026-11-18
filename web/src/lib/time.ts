// 30분 단위 시각 도우미 (V9_ko 165 「30분 단위는 화면이 제시」, 447 「편집 모드 30분 단위」).
// 시각은 계약대로 "HH:MM" 문자열로 다룬다 (API_CONTRACT.md 「공통」).

export const SLOT_MINUTES = 30

/** 편집 · 제출 화면의 선택지 범위.
 * (가정 신규) 끝을 24:00 이 아니라 23:30 으로 둔다. API 는 Python `datetime.time` 으로 받는데
 * 그 형은 24:00 을 표현하지 못한다(hour 0~23). 시연 점포의 영업시간은 08:00~22:30 (V9_ko 228-229, G6) 이라
 * 06:00~23:30 이면 충분하다. 자정까지 일하는 배치가 필요해지면 계약부터 정해야 한다. */
export const DAY_FIRST = '06:00'
export const DAY_LAST = '23:30'

const HHMM = /^(\d{2}):(\d{2})(?::\d{2})?$/

/** "HH:MM" 또는 "HH:MM:SS" → "HH:MM". 형식이 틀리면 null. API 가 초를 붙여 보내도 화면이 깨지지 않게 한다. */
export function normTime(value: string | null | undefined): string | null {
  if (value == null) return null
  const m = HHMM.exec(value.trim())
  if (!m) return null
  const h = Number(m[1])
  const min = Number(m[2])
  if (h > 24 || min > 59 || (h === 24 && min !== 0)) return null
  return `${m[1]}:${m[2]}`
}

/** "HH:MM" → 0시부터의 분. 형식이 틀리면 NaN. */
export function toMinutes(value: string): number {
  const t = normTime(value)
  if (t === null) return Number.NaN
  const [h, m] = t.split(':').map(Number) as [number, number]
  return h * 60 + m
}

export function fromMinutes(total: number): string {
  const h = Math.floor(total / 60)
  const m = total % 60
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
}

/** 분이 00 또는 30 인가 (DB CHECK 와 같은 조건, V9_ko 317 근처 availability_submissions). */
export function isHalfHour(value: string | null | undefined): boolean {
  const t = normTime(value)
  if (t === null) return false
  return toMinutes(t) % SLOT_MINUTES === 0
}

/** from 부터 to 까지(둘 다 포함) 30분 간격 목록. from/to 가 30분 단위가 아니면 안쪽으로 맞춘다. */
export function halfHourOptions(from: string = DAY_FIRST, to: string = DAY_LAST): string[] {
  const a = toMinutes(from)
  const b = toMinutes(to)
  if (Number.isNaN(a) || Number.isNaN(b) || b < a) return []
  const start = Math.ceil(a / SLOT_MINUTES) * SLOT_MINUTES
  const out: string[] = []
  for (let x = start; x <= b; x += SLOT_MINUTES) out.push(fromMinutes(x))
  return out
}

/** 시작 선택지: 마지막 칸은 뺀다 (끝이 시작보다 뒤여야 하므로). */
export function startOptions(from: string = DAY_FIRST, to: string = DAY_LAST): string[] {
  return halfHourOptions(from, to).slice(0, -1)
}

/** 끝 선택지: start 보다 뒤인 것만. start 가 없으면 첫 칸을 뺀 전부. */
export function endOptions(start: string | null, from: string = DAY_FIRST, to: string = DAY_LAST): string[] {
  const all = halfHourOptions(from, to)
  if (start === null || Number.isNaN(toMinutes(start))) return all.slice(1)
  const s = toMinutes(start)
  return all.filter((t) => toMinutes(t) > s)
}

/** 길이(분). 표시용. */
export function spanMinutes(start: string, end: string): number {
  return toMinutes(end) - toMinutes(start)
}
