<script setup lang="ts">
// 위반 목록 (W-4): 필수 / 경고로 나눠 보여 주고, 예외 허용이 되는 것에만 체크 상자를 단다.
// QUAL_MISSING · DOUBLE_BOOKING 은 「例外不可」 자물쇠 칩 (V9_ko 479). 체크 상자 없음.
import { computed } from 'vue'
import { t } from '../i18n'
import { checkLabel, detailText, violationWhere, type NameOf } from '../lib/describe'
import { cellKey } from '../lib/grid'
import { groupBySeverity, isWaivable, mergeSlotRows, type ViolationRow } from '../lib/violations'
import type { Violation } from '../types'
import LockIcon from './LockIcon.vue'

const props = defineProps<{
  violations: readonly Violation[]
  nameOf: NameOf
  waived?: ReadonlySet<string>
  selectable?: boolean // 승인 대기 중에만 체크 상자를 쓸 수 있다
  idPrefix: string
}>()
const emit = defineEmits<{ toggle: [key: string, on: boolean]; locate: [focusKey: string] }>()

const groups = computed(() => groupBySeverity(props.violations))
const rows = computed(() => ({ required: mergeSlotRows(groups.value.required), warning: mergeSlotRows(groups.value.warning) }))

function allWaived(r: ViolationRow): boolean {
  return r.keys.every((k) => props.waived?.has(k))
}
function toggleRow(r: ViolationRow, on: boolean) {
  for (const k of r.keys) emit('toggle', k, on)
}

function focusKeyOf(v: Violation): string | null {
  if (v.employee_id != null && v.work_date) return cellKey(v.employee_id, v.work_date)
  if (v.work_date) return `date:${v.work_date}`
  if (v.employee_id != null) return `row:${v.employee_id}`
  return null
}

function safeId(v: Violation, i: number): string { // 묶인 줄은 첫 위반의 key 로
  return `${props.idPrefix}-${i}-${v.key.replace(/[^A-Za-z0-9_-]/g, '_')}`
}
</script>

<template>
  <div class="vlist">
    <p v-if="violations.length === 0" class="muted">{{ t('viol.none') }}</p>
    <template v-for="sev in (['required', 'warning'] as const)" :key="sev">
      <section v-if="groups[sev].length" class="vgroup" :aria-labelledby="`${idPrefix}-${sev}`">
        <h4 :id="`${idPrefix}-${sev}`" class="vgroup-title">
          <span class="chip" :class="sev === 'required' ? 'chip--required' : 'chip--warning'">{{ sev === 'required' ? t('review.required', { n: groups[sev].length }) : t('review.warning', { n: groups[sev].length }) }}</span>
          <span class="small muted">{{ sev === 'required' ? t('viol.required') : t('viol.warning') }}</span>
        </h4>
        <ul class="items">
          <li v-for="(r, i) in rows[sev]" :key="`${r.keys[0] ?? ''}-${i}`" class="item" :class="`item--${sev}`" :title="r.first.check_code">
            <div class="item-main">
              <span class="item-code">{{ checkLabel(r.first.check_code) }}</span>
              <span class="item-where small">{{ violationWhere(r.first, nameOf) }}</span>
              <span v-if="r.start" class="small num">{{ r.start }}–{{ r.end }}</span>
              <span v-if="detailText(r.first.detail)" class="item-detail small muted num">{{ detailText(r.first.detail) }}</span>
              <span v-if="r.keys.length > 1" class="small muted num">{{ t('viol.slots', { n: r.keys.length }) }}</span>
            </div>
            <div class="item-side">
              <span v-if="r.first.allowed_at" class="chip chip--ok">{{ t('viol.allowed') }}</span>
              <span v-else-if="!isWaivable(r.first)" class="chip chip--lock"><LockIcon />{{ t('viol.locked') }}</span>
              <label v-else-if="selectable" class="check small" :for="safeId(r.first, i)">
                <input :id="safeId(r.first, i)" type="checkbox" :checked="allWaived(r)" @change="toggleRow(r, ($event.target as HTMLInputElement).checked)" />
                {{ t('viol.waive') }}
              </label>
              <button v-if="focusKeyOf(r.first)" type="button" class="btn btn--quiet btn--sm" @click="emit('locate', focusKeyOf(r.first)!)">{{ t('viol.showInGrid') }}</button>
            </div>
          </li>
        </ul>
      </section>
    </template>
  </div>
</template>

<style scoped>
.vlist {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}
.vgroup {
  display: grid;
  gap: var(--sp-2);
  min-width: 0;
}
.vgroup-title {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-2);
  margin: 0;
  font-family: var(--font-body);
  font-size: var(--fs-md);
}
.items {
  list-style: none;
  display: grid;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel);
  min-width: 0;
}
.item {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-2) var(--sp-4);
  padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid var(--line);
  min-width: 0;
}
.item:last-child {
  border-bottom: 0;
}
/* 필수 = 꽉 찬 네모, 경고 = 속이 빈 동그라미 (표의 머리 표시와 같은 모양) */
.item-main {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--sp-1) var(--sp-3);
  min-width: 0;
  flex: 1 1 18rem;
}
.item-main::before {
  content: '';
  inline-size: 9px;
  block-size: 9px;
  align-self: center;
  flex: none;
}
.item--required .item-main::before {
  background: var(--required);
}
.item--warning .item-main::before {
  border: 2px solid var(--warning-line);
  border-radius: 50%;
}
.item-code {
  font-weight: 700;
}
.item-where,
.item-detail {
  min-width: 0;
  overflow-wrap: anywhere;
}
.item-side {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-2);
}
</style>
