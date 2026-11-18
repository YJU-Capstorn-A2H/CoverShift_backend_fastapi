// 날짜 도우미. 시간대(타임존) 영향을 받지 않도록 UTC 로만 계산한다.
// 요일 번호는 DB 와 같다: 1=월 ... 7=일 (V9_ko store_time_rules.weekday, date.isoweekday()).

export type DayType = 'weekday' | 'sat' | 'sun' | 'holiday'

/** (가정 신규) 표 머리의 「日・祝」 색칠용 일본 공휴일. 표시에만 쓰고 검사·편성에는 쓰지 않는다.
 * 명세에 공휴일 자료가 없어서 시연 달(2026-11) 앞뒤만 손으로 넣었다. 내각부 공표 목록과 대조하지 않았다. */
const JP_HOLIDAYS = new Set<string>([
  '2026-11-03', // 文化の日
  '2026-11-23', // 勤労感謝の日
  '2027-01-01', // 元日
  '2027-01-11', // 成人の日
  '2027-02-11', // 建国記念の日
  '2027-02-23', // 天皇誕生日
])

const YMD = /^(\d{4})-(\d{2})-(\d{2})$/

function parseYmd(value: string): Date | null {
  const m = YMD.exec(value)
  if (!m) return null
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])))
  return Number.isNaN(d.getTime()) ? null : d
}

function fmt(d: Date): string {
  return d.toISOString().slice(0, 10)
}

/** target_month("YYYY-MM-01" 또는 "YYYY-MM") 의 날짜 전부. */
export function monthDates(targetMonth: string): string[] {
  const first = parseYmd(targetMonth.length === 7 ? `${targetMonth}-01` : targetMonth)
  if (!first) return []
  const out: string[] = []
  const d = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth(), 1))
  const month = d.getUTCMonth()
  while (d.getUTCMonth() === month) {
    out.push(fmt(d))
    d.setUTCDate(d.getUTCDate() + 1)
  }
  return out
}

/** 1=월 ... 7=일 */
export function isoWeekday(date: string): number {
  const d = parseYmd(date)
  if (!d) return 0
  const js = d.getUTCDay() // 0=일
  return js === 0 ? 7 : js
}

export function dayType(date: string): DayType {
  if (JP_HOLIDAYS.has(date)) return 'holiday'
  const w = isoWeekday(date)
  if (w === 6) return 'sat'
  if (w === 7) return 'sun'
  return 'weekday'
}

export function dayOfMonth(date: string): number {
  return Number(date.slice(8, 10))
}

export function monthParts(targetMonth: string): { year: number; month: number } {
  return { year: Number(targetMonth.slice(0, 4)), month: Number(targetMonth.slice(5, 7)) }
}

/** <input type="month"> 값 "2026-12" → target_month "2026-12-01". 틀리면 null. */
export function monthInputToTargetMonth(value: string): string | null {
  return /^\d{4}-(0[1-9]|1[0-2])$/.test(value) ? `${value}-01` : null
}

/** <input type="datetime-local"> 값(현지 시각) → ISO 8601 (UTC). 틀리면 null. */
export function localInputToIso(value: string): string | null {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) return null
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? null : d.toISOString()
}

/** ISO 시점 → <input type="datetime-local"> 값(현지 시각). */
export function isoToLocalInput(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}
