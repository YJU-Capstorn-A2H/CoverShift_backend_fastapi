<script setup lang="ts">
// 시간대 규칙 (W-2, A-6, M-8): 요일 × 시간대. 추가 · 수정 · 삭제 · 요일 복사.
// min_headcount = 「休憩込みの必要人数」 (M-8, V9_ko 452), min_qualified = 그 시간대에 필요한 등판 수 (V9_ko 469).
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api'
import ErrorBox from '../../components/ErrorBox.vue'
import TimeSelect from '../../components/TimeSelect.vue'
import { t, tk, weekdayLabel } from '../../i18n'
import { validateTimeRule } from '../../lib/forms'
import { endOptions, normTime, startOptions, toMinutes } from '../../lib/time'
import { toast } from '../../toast'
import type { TimeRule, TimeRuleInput } from '../../types'

const WEEKDAYS = [1, 2, 3, 4, 5, 6, 7] as const
const rules = ref<TimeRule[]>([])
const loadError = ref<unknown>(null)
const actionError = ref<unknown>(null)
const busy = ref(false)
const weekday = ref<number>(1)

const starts = startOptions()

async function load() {
  try {
    rules.value = (await api.timeRules()).map((r) => ({ ...r, start_time: normTime(r.start_time) ?? r.start_time, end_time: normTime(r.end_time) ?? r.end_time }))
    loadError.value = null
  } catch (e) {
    loadError.value = e
  }
}
onMounted(load)

const dayRules = computed(() => rules.value.filter((r) => r.weekday === weekday.value).sort((a, b) => toMinutes(a.start_time) - toMinutes(b.start_time)))

function hours(w: number): string {
  const rs = rules.value.filter((r) => r.weekday === w)
  if (!rs.length) return t('rules.noHours')
  const s = rs.reduce((m, r) => (toMinutes(r.start_time) < toMinutes(m) ? r.start_time : m), rs[0]!.start_time)
  const e = rs.reduce((m, r) => (toMinutes(r.end_time) > toMinutes(m) ? r.end_time : m), rs[0]!.end_time)
  return `${s}–${e}`
}

// ---------------------------------------------------------------- 추가
const draftNew = ref<Omit<TimeRuleInput, 'weekday'>>({ start_time: '08:00', end_time: '13:00', min_headcount: 2, min_qualified: 1 })
const newError = computed(() => {
  const e = validateTimeRule({ ...draftNew.value, weekday: weekday.value }, rules.value)
  return e ? tk(`formErr.${e}`, e) : null
})
const newTouched = ref(false)

async function add() {
  newTouched.value = true
  if (newError.value) return
  busy.value = true
  actionError.value = null
  try {
    await api.createTimeRule({ ...draftNew.value, weekday: weekday.value })
    toast(t('common.saved'))
    newTouched.value = false
    await load()
  } catch (e) {
    actionError.value = e
  } finally {
    busy.value = false
  }
}

// ---------------------------------------------------------------- 수정 · 삭제
const editingId = ref<number | null>(null)
const editValue = ref<TimeRuleInput | null>(null)
const editError = computed(() => {
  if (!editValue.value) return null
  const e = validateTimeRule(editValue.value, rules.value, editingId.value)
  return e ? tk(`formErr.${e}`, e) : null
})

function startEdit(r: TimeRule) {
  editingId.value = r.rule_id
  editValue.value = { weekday: r.weekday, start_time: r.start_time, end_time: r.end_time, min_headcount: r.min_headcount, min_qualified: r.min_qualified }
}
async function saveEdit() {
  if (!editValue.value || editingId.value === null || editError.value) return
  busy.value = true
  actionError.value = null
  try {
    await api.updateTimeRule(editingId.value, editValue.value)
    toast(t('common.saved'))
    editingId.value = null
    await load()
  } catch (e) {
    actionError.value = e
  } finally {
    busy.value = false
  }
}
async function remove(r: TimeRule) {
  if (!window.confirm(t('rules.deleteConfirm'))) return
  busy.value = true
  actionError.value = null
  try {
    await api.deleteTimeRule(r.rule_id)
    toast(t('common.deleted'))
    await load()
  } catch (e) {
    actionError.value = e
  } finally {
    busy.value = false
  }
}

// ---------------------------------------------------------------- 요일 복사
const copyTargets = ref<number[]>([])
async function copy() {
  if (!copyTargets.value.length) return
  if (!window.confirm(t('rules.copyConfirm', { day: weekdayLabel(weekday.value) }))) return
  busy.value = true
  actionError.value = null
  try {
    const r = await api.copyTimeRules({ from_weekday: weekday.value, to_weekdays: [...copyTargets.value].sort() })
    toast(t('rules.copied', { n: r.created }))
    copyTargets.value = []
    await load()
  } catch (e) {
    actionError.value = e
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="stack stack--lg">
    <p class="muted">{{ t('rules.lead') }}</p>
    <ErrorBox v-if="loadError" :error="loadError" />

    <!-- 요일 고르기: 요일마다 규칙 수와 영업시간을 보여 준다 -->
    <fieldset class="weekdays">
      <legend class="sr-only">{{ t('rules.pickWeekday') }}</legend>
      <label v-for="w in WEEKDAYS" :key="w" class="wd" :class="{ 'wd--sat': w === 6, 'wd--sun': w === 7, 'is-on': weekday === w }" :for="`wd-${w}`">
        <input :id="`wd-${w}`" v-model="weekday" type="radio" name="rules-weekday" :value="w" class="sr-only" @change="editingId = null" />
        <span class="wd-name">{{ weekdayLabel(w) }}</span>
        <span class="wd-hours num small">{{ hours(w) }}</span>
      </label>
    </fieldset>

    <ErrorBox v-if="actionError" :error="actionError" />

    <div class="table-scroll">
      <table class="table">
        <thead>
          <tr>
            <th scope="col">{{ t('rules.start') }}–{{ t('rules.end') }}</th>
            <th scope="col" class="num">{{ t('rules.headcount') }}</th>
            <th scope="col" class="num">{{ t('rules.qualified') }}</th>
            <th scope="col">{{ t('common.actions') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!dayRules.length">
            <td colspan="4" class="muted">{{ t('rules.empty') }}</td>
          </tr>
          <tr v-for="r in dayRules" :key="r.rule_id">
            <template v-if="editingId === r.rule_id && editValue">
              <td>
                <div class="pair">
                  <TimeSelect :id="`rule-${r.rule_id}-start`" :label="t('rules.start')" hide-label :options="starts" v-model="editValue.start_time" />
                  <TimeSelect :id="`rule-${r.rule_id}-end`" :label="t('rules.end')" hide-label :options="endOptions(editValue.start_time)" v-model="editValue.end_time" />
                </div>
                <p v-if="editError" class="field-error">{{ editError }}</p>
              </td>
              <td class="num">
                <label class="sr-only" :for="`rule-${r.rule_id}-hc`">{{ t('rules.headcount') }}</label>
                <input :id="`rule-${r.rule_id}-hc`" v-model.number="editValue.min_headcount" class="input input--num" type="number" min="1" max="99" />
              </td>
              <td class="num">
                <label class="sr-only" :for="`rule-${r.rule_id}-q`">{{ t('rules.qualified') }}</label>
                <input :id="`rule-${r.rule_id}-q`" v-model.number="editValue.min_qualified" class="input input--num" type="number" min="0" max="99" />
              </td>
              <td>
                <div class="row">
                  <button type="button" class="btn btn--primary btn--sm" :disabled="busy || !!editError" @click="saveEdit">{{ t('common.save') }}</button>
                  <button type="button" class="btn btn--sm" @click="editingId = null">{{ t('common.cancel') }}</button>
                </div>
              </td>
            </template>
            <template v-else>
              <td class="tnum nowrap">{{ r.start_time }}–{{ r.end_time }}</td>
              <td class="num">{{ t('fmt.people', { n: r.min_headcount }) }}</td>
              <td class="num">{{ t('fmt.people', { n: r.min_qualified }) }}</td>
              <td>
                <div class="row">
                  <button type="button" class="btn btn--sm" :disabled="busy" @click="startEdit(r)">{{ t('common.edit') }}</button>
                  <button type="button" class="btn btn--sm btn--danger" :disabled="busy" @click="remove(r)">{{ t('common.delete') }}</button>
                </div>
              </td>
            </template>
          </tr>
        </tbody>
      </table>
    </div>

    <form class="section" @submit.prevent="add">
      <h3>{{ t('rules.addTitle', { day: weekdayLabel(weekday) }) }}</h3>
      <div class="form-grid">
        <TimeSelect id="rule-new-start" :label="t('rules.start')" :options="starts" v-model="draftNew.start_time" />
        <TimeSelect id="rule-new-end" :label="t('rules.end')" :options="endOptions(draftNew.start_time)" v-model="draftNew.end_time" />
        <div class="field">
          <label for="rule-new-hc">{{ t('rules.headcount') }}</label>
          <input id="rule-new-hc" v-model.number="draftNew.min_headcount" class="input" type="number" min="1" max="99" />
        </div>
        <div class="field">
          <label for="rule-new-q">{{ t('rules.qualified') }}</label>
          <input id="rule-new-q" v-model.number="draftNew.min_qualified" class="input" type="number" min="0" max="99" />
        </div>
        <button type="submit" class="btn btn--primary" :disabled="busy">{{ t('rules.add') }}</button>
      </div>
      <p v-if="newTouched && newError" class="field-error" role="alert">{{ newError }}</p>
    </form>

    <form class="section" @submit.prevent="copy">
      <h3>{{ t('rules.copyTitle', { day: weekdayLabel(weekday) }) }}</h3>
      <fieldset class="row">
        <legend class="field-label small muted">{{ t('rules.copyTargets') }}</legend>
        <label v-for="w in WEEKDAYS.filter((x) => x !== weekday)" :key="w" class="check" :for="`copy-to-${w}`">
          <input :id="`copy-to-${w}`" v-model="copyTargets" type="checkbox" :value="w" />
          {{ weekdayLabel(w) }}
        </label>
      </fieldset>
      <div class="row">
        <button type="submit" class="btn" :disabled="busy || !copyTargets.length || !dayRules.length">{{ t('rules.copySubmit') }}</button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.weekdays {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: var(--sp-2);
}
.wd {
  display: grid;
  gap: 2px;
  justify-items: center;
  padding: var(--sp-2) var(--sp-1);
  min-block-size: var(--touch);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel);
  cursor: pointer;
  text-align: center;
  min-width: 0;
}
.wd:hover {
  border-color: var(--ai);
}
.wd:focus-within {
  outline: 2px solid var(--focus);
  outline-offset: 2px;
}
.wd.is-on {
  border-color: var(--ai);
  background: var(--ai-soft);
  box-shadow: inset 0 0 0 1px var(--ai);
}
.wd-name {
  font-weight: 700;
  font-size: var(--fs-lg);
}
.wd--sat .wd-name {
  color: var(--sat);
}
.wd--sun .wd-name {
  color: var(--sun);
}
.wd-hours {
  color: var(--ink-soft);
  overflow-wrap: anywhere;
}
.pair {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--sp-2);
  min-inline-size: 13rem;
}
.input--num {
  inline-size: 5rem;
  text-align: end;
}
fieldset.row {
  gap: var(--sp-2) var(--sp-4);
}
fieldset.row > legend {
  inline-size: 100%;
  margin-block-end: var(--sp-1);
}
</style>
