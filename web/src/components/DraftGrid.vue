<script setup lang="ts">
// 편성안 표: 직원(줄) × 날짜(열). W-4 보기 · W-6 편집.
// - 첫 열(직원)과 날짜 머리 줄은 고정(sticky), 표는 자기 상자 안에서만 스크롤한다.
// - 土 = 파랑, 日・祝 = 빨강 (일본 달력 관습).
// - 위반이 가리키는 칸: 필수 = 실선 테두리 + 왼쪽 위 삼각 / 경고 = 점선 테두리 (색만으로 구분하지 않음).
// - 유급(有給)은 근무가 아니다 (F-16) → 빗금 칸.
import { computed, nextTick, ref, watch } from 'vue'
import { dateLabel, t, weekdayLabel } from '../i18n'
import { dayOfMonth, dayType, isoWeekday } from '../lib/calendar'
import { checkLabel } from '../lib/describe'
import { cellKey, headcountByDate, workDaysByEmployee, type Cell, type Grid, type GridRow, type WorkCell } from '../lib/grid'
import type { Marks } from '../lib/violations'
import type { Severity, Violation } from '../types'

const props = defineProps<{
  dates: readonly string[]
  rows: readonly GridRow[]
  grid: Grid
  marks: Marks
  violations: readonly Violation[]
  changed?: ReadonlySet<string>
  editable?: boolean
  focusKey?: string | null
}>()
const emit = defineEmits<{ edit: [key: string] }>()

const scroller = ref<HTMLElement | null>(null)

const headcount = computed(() => headcountByDate(props.grid, props.dates))
const workDays = computed(() => workDaysByEmployee(props.grid))

/** 칸 · 열 · 줄별 위반 이름 (설명 글자용) */
const labelsAt = computed(() => {
  const m = new Map<string, string[]>()
  const add = (k: string, v: Violation) => m.set(k, [...(m.get(k) ?? []), checkLabel(v.check_code)])
  for (const v of props.violations) {
    if (v.employee_id != null && v.work_date) add(cellKey(v.employee_id, v.work_date), v)
    else if (v.work_date) add(`date:${v.work_date}`, v)
    else if (v.employee_id != null) add(`row:${v.employee_id}`, v)
  }
  return m
})

function workAt(employeeId: number, d: string): WorkCell | null {
  const c = props.grid.get(cellKey(employeeId, d))
  return c?.kind === 'work' ? c : null
}

function sevClass(s: Severity | undefined): string {
  return s === 'required' ? 'has-required' : s === 'warning' ? 'has-warning' : ''
}

function cellText(c: Cell | undefined): string {
  if (!c) return t('grid.off')
  if (c.kind === 'paid_leave') return t('grid.paid')
  return `${c.start}–${c.end}`
}

function cellAria(row: GridRow, d: string): string {
  const key = cellKey(row.employee_id, d)
  const parts = [row.name, dateLabel(d), cellText(props.grid.get(key))]
  const v = labelsAt.value.get(key)
  if (v?.length) parts.push(v.join('、'))
  return parts.join(' ')
}

function headTitle(d: string): string | undefined {
  return labelsAt.value.get(`date:${d}`)?.join('、')
}

// 위반 목록의 「表で見る」 → 그 칸으로 스크롤하고 초점을 준다
watch(
  () => props.focusKey,
  async (key) => {
    if (!key) return
    await nextTick()
    const el = scroller.value?.querySelector<HTMLElement>(`[data-key="${CSS.escape(key)}"]`)
    if (!el) return
    el.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'smooth' })
    const target = el.querySelector<HTMLElement>('button') ?? el
    target.focus({ preventScroll: true })
  },
)
</script>

<template>
  <div class="draft-grid">
    <ul class="legend small" :aria-label="t('grid.caption')">
      <li><span class="mk mk--required" aria-hidden="true"></span>{{ t('grid.legendRequired') }}</li>
      <li><span class="mk mk--warning" aria-hidden="true"></span>{{ t('grid.legendWarning') }}</li>
      <li v-if="editable"><span class="mk mk--changed" aria-hidden="true"></span>{{ t('grid.legendChanged') }}</li>
      <li><span class="mk mk--paid" aria-hidden="true"></span>{{ t('grid.paid') }}</li>
      <li class="scroll-hint muted">{{ t('grid.scrollHint') }}</li>
    </ul>

    <div ref="scroller" class="grid-scroll" tabindex="0" role="region" :aria-label="t('grid.caption')">
      <table class="grid" :class="{ 'is-editable': editable }">
        <caption class="sr-only">{{ t('grid.caption') }}</caption>
        <thead>
          <tr>
            <th scope="col" class="rh corner">{{ t('grid.staff') }}</th>
            <th
              v-for="d in dates"
              :key="d"
              scope="col"
              class="dh"
              :class="[`dt-${dayType(d)}`, sevClass(marks.dates.get(d)), { 'is-focus': focusKey === `date:${d}` }]"
              :data-key="`date:${d}`"
              :title="headTitle(d)"
            >
              <span class="dh-d num">{{ dayOfMonth(d) }}</span>
              <span class="dh-w">{{ weekdayLabel(isoWeekday(d)) }}</span>
              <span v-if="marks.dates.get(d)" class="mk" :class="`mk--${marks.dates.get(d)}`">
                <span class="sr-only">{{ headTitle(d) }}</span>
              </span>
            </th>
            <th scope="col" class="sum">{{ t('grid.days') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.employee_id">
            <th
              scope="row"
              class="rh"
              :class="[sevClass(marks.rows.get(row.employee_id)), { 'is-focus': focusKey === `row:${row.employee_id}` }]"
              :data-key="`row:${row.employee_id}`"
              :title="labelsAt.get(`row:${row.employee_id}`)?.join('、')"
            >
              <span class="rh-line">
                <span class="rh-name">{{ row.name }}</span>
                <span class="rh-id num muted">#{{ row.employee_id }}</span>
                <span v-if="marks.rows.get(row.employee_id)" class="mk" :class="`mk--${marks.rows.get(row.employee_id)}`">
                  <span class="sr-only">{{ labelsAt.get(`row:${row.employee_id}`)?.join('、') }}</span>
                </span>
              </span>
              <span class="rh-chips">
                <span v-if="row.is_regular !== null" class="chip" :class="{ 'chip--solid': row.is_regular }">{{ row.is_regular ? t('staff.regular') : t('staff.partShort') }}</span>
                <span v-if="row.has_qualification" class="chip chip--accent">{{ t('staff.qualShort') }}</span>
                <span v-if="row.closing_ok" class="chip">{{ t('staff.closingShort') }}</span>
              </span>
            </th>
            <td
              v-for="d in dates"
              :key="d"
              :class="[
                `dt-${dayType(d)}`,
                sevClass(marks.cells.get(cellKey(row.employee_id, d))),
                {
                  'is-changed': changed?.has(cellKey(row.employee_id, d)),
                  'is-focus': focusKey === cellKey(row.employee_id, d),
                  'is-paid': grid.get(cellKey(row.employee_id, d))?.kind === 'paid_leave',
                },
              ]"
              :data-key="cellKey(row.employee_id, d)"
              :title="labelsAt.get(cellKey(row.employee_id, d))?.join('、')"
            >
              <button
                v-if="editable && grid.get(cellKey(row.employee_id, d))?.kind !== 'paid_leave'"
                type="button"
                class="cell cell--btn"
                :aria-label="cellAria(row, d)"
                @click="emit('edit', cellKey(row.employee_id, d))"
              >
                <template v-if="workAt(row.employee_id, d)">
                  <span class="t">{{ workAt(row.employee_id, d)?.start }}</span>
                  <span class="t t--end">{{ workAt(row.employee_id, d)?.end }}</span>
                </template>
                <span v-else class="plus" aria-hidden="true">+</span>
              </button>
              <span v-else class="cell" :aria-label="cellAria(row, d)" role="img">
                <template v-if="workAt(row.employee_id, d)">
                  <span class="t">{{ workAt(row.employee_id, d)?.start }}</span>
                  <span class="t t--end">{{ workAt(row.employee_id, d)?.end }}</span>
                </template>
                <span v-else-if="grid.get(cellKey(row.employee_id, d))?.kind === 'paid_leave'" class="paid">{{ t('grid.paid') }}</span>
              </span>
            </td>
            <td class="sum num">{{ workDays.get(row.employee_id) ?? 0 }}</td>
          </tr>
        </tbody>
        <tfoot>
          <tr>
            <th scope="row" class="rh">{{ t('grid.total') }}</th>
            <td v-for="d in dates" :key="d" class="num total" :class="`dt-${dayType(d)}`">{{ headcount.get(d) ?? 0 }}</td>
            <td class="sum"></td>
          </tr>
        </tfoot>
      </table>
    </div>
  </div>
</template>

<style scoped>
.draft-grid {
  display: grid;
  gap: var(--sp-2);
  min-width: 0;
}
.legend {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-1) var(--sp-4);
  list-style: none;
  color: var(--ink-soft);
}
.legend li {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
}
.scroll-hint {
  margin-inline-start: auto;
}
@media (min-width: 90rem) {
  .scroll-hint {
    display: none !important;
  }
}

/* 표시 모양 (범례 · 머리 · 줄 머리에서 같이 씀) */
.mk {
  display: inline-block;
  inline-size: 9px;
  block-size: 9px;
  flex: none;
}
.mk--required {
  background: var(--required); /* 꽉 찬 네모 */
}
.mk--warning {
  border: 2px solid var(--warning-line); /* 속이 빈 동그라미 (필수 = 꽉 찬 네모) */
  border-radius: 50%;
}
.mk--changed {
  background: var(--ai);
  border-radius: 50%;
}
.mk--paid {
  border: 1px solid var(--line-strong);
  background: repeating-linear-gradient(135deg, transparent 0 2px, var(--line-strong) 2px 3px);
}

.grid-scroll {
  overflow: auto;
  max-block-size: min(72vh, 48rem);
  min-width: 0;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel);
  overscroll-behavior: contain;
}
.grid {
  border-collapse: separate;
  border-spacing: 0;
  font-size: var(--fs-sm);
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
}
.grid th,
.grid td {
  padding: 0;
  border-right: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
  background-clip: padding-box;
}

/* 날짜 머리 (위 고정) */
.grid thead th {
  position: sticky;
  top: 0;
  z-index: 2;
  background: var(--panel-sunken);
  font-weight: 600;
}
.dh {
  inline-size: var(--cell-w);
  min-inline-size: var(--cell-w);
  padding: var(--sp-1) 0 !important;
  text-align: center;
  vertical-align: top;
}
.dh-d {
  display: block;
  font-size: var(--fs-md);
  color: var(--ink);
}
.dh-w {
  display: block;
  font-size: var(--fs-xs);
  color: var(--ink-soft);
}
.dh .mk {
  display: block;
  margin: 3px auto 0;
}
.grid thead th.dt-sat {
  background: var(--sat-soft);
}
.grid thead th.dt-sun,
.grid thead th.dt-holiday {
  background: var(--sun-soft);
}
.dt-sat .dh-d,
.dt-sat .dh-w {
  color: var(--sat);
}
.dt-sun .dh-d,
.dt-sun .dh-w,
.dt-holiday .dh-d,
.dt-holiday .dh-w {
  color: var(--sun);
}
.grid thead th.has-required {
  box-shadow: inset 0 -3px 0 var(--required);
}
.grid thead th.has-warning {
  box-shadow: inset 0 -3px 0 var(--warning-line);
}

/* 직원 머리 (왼쪽 고정) */
.rh {
  position: sticky;
  left: 0;
  z-index: 1;
  inline-size: var(--rowhead-w);
  min-inline-size: var(--rowhead-w);
  max-inline-size: var(--rowhead-w);
  padding: var(--sp-1) var(--sp-3) !important;
  background: var(--panel);
  border-right: 1px solid var(--line-strong) !important;
  text-align: start;
  font-weight: 400;
}
.grid thead th.corner {
  left: 0;
  z-index: 4;
  text-align: start;
  vertical-align: bottom;
  font-size: var(--fs-xs);
  color: var(--ink-soft);
  letter-spacing: 0.04em;
}
.rh-line {
  display: flex;
  align-items: baseline;
  gap: var(--sp-1);
  min-width: 0;
}
.rh-name {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}
.rh-id {
  font-size: var(--fs-xs);
  flex: none;
}
.rh .mk {
  align-self: center;
}
.rh-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 3px;
  margin-top: 2px;
}
.rh-chips .chip {
  min-block-size: 1.125rem;
  padding: 0 5px;
  font-size: 0.6875rem;
}
.rh.has-required {
  box-shadow: inset 3px 0 0 var(--required);
}
.rh.has-warning {
  box-shadow: inset 3px 0 0 var(--warning-line);
}

/* 칸 */
.grid td {
  position: relative;
  text-align: center;
  vertical-align: middle;
}
.grid tbody td.dt-sat {
  background: var(--sat-soft);
}
.grid tbody td.dt-sun,
.grid tbody td.dt-holiday {
  background: var(--sun-soft);
}
.cell {
  display: grid;
  place-content: center;
  inline-size: var(--cell-w);
  min-block-size: 2.75rem;
  padding: 2px;
  color: var(--ink);
}
.t {
  display: block;
  font-size: var(--fs-xs);
  letter-spacing: -0.01em;
}
.t--end {
  color: var(--ink-soft);
}
.paid {
  font-size: var(--fs-xs);
  font-weight: 600;
  color: var(--ink-soft);
}
.grid td.is-paid {
  background: repeating-linear-gradient(135deg, transparent 0 5px, var(--line) 5px 6px);
}

/* 위반 표시: 필수 = 실선 + 왼쪽 위 삼각, 경고 = 점선 */
.grid td.has-required {
  box-shadow: inset 0 0 0 2px var(--required);
}
.grid td.has-required::after {
  content: '';
  position: absolute;
  inset-block-start: 0;
  inset-inline-start: 0;
  border-style: solid;
  border-width: 9px 9px 0 0;
  border-color: var(--required) transparent transparent transparent;
  pointer-events: none;
}
.grid td.has-warning {
  outline: 2px dashed var(--warning-line);
  outline-offset: -3px;
}

/* 편집한 칸 */
.grid td.is-changed .cell {
  background: var(--ai-soft);
}
.grid td.is-changed::before {
  content: '';
  position: absolute;
  inset-block-start: 4px;
  inset-inline-end: 4px;
  inline-size: 6px;
  block-size: 6px;
  border-radius: 50%;
  background: var(--ai);
  pointer-events: none;
}

/* 편집 모드: 누를 수 있는 칸 */
.cell--btn {
  border: 0;
  margin: 0;
  background: transparent;
  font: inherit;
  cursor: pointer;
  inline-size: 100%;
}
.cell--btn:hover {
  background: var(--ai-soft);
  box-shadow: inset 0 0 0 1px var(--ai);
}
.cell--btn:focus-visible {
  outline: 2px solid var(--focus);
  outline-offset: -2px;
}
.plus {
  color: var(--ink-faint);
  font-size: var(--fs-md);
  opacity: 0;
}
.cell--btn:hover .plus,
.cell--btn:focus-visible .plus {
  opacity: 1;
}

/* 위반 목록에서 고른 자리 */
.grid .is-focus {
  outline: 3px solid var(--focus) !important;
  outline-offset: -3px;
}

/* 합계 */
.sum {
  min-inline-size: 3rem;
  padding: 0 var(--sp-2) !important;
  text-align: center;
  font-weight: 600;
  color: var(--ink-soft);
}
.grid tfoot th,
.grid tfoot td {
  position: sticky;
  bottom: 0;
  z-index: 1;
  background: var(--panel-sunken);
  border-top: 1px solid var(--line-strong);
  font-weight: 600;
  color: var(--ink-soft);
}
.grid tfoot th.rh {
  z-index: 3;
  background: var(--panel-sunken);
  font-size: var(--fs-xs);
  letter-spacing: 0.04em;
}
.grid tfoot td.total {
  padding: var(--sp-1) 0;
  font-size: var(--fs-sm);
}

@media (max-width: 40rem) {
  .draft-grid {
    --rowhead-w: 8.5rem;
  }
}
</style>
