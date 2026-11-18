<script setup lang="ts">
// 직원 제출 페이지 (W-7, A-11 · A-12). 로그인 없음 — 링크 토큰으로 연다.
// 휴대폰 우선: 날마다 한 줄씩 쌓이고, 30분 선택칸은 손가락으로 누르기 좋은 크기. 400px 에서 가로 스크롤 없음.
// 마감 뒤 410 → 안내만 보이고 입력은 막는다. 상한 초과 422 LIMIT_EXCEEDED 는 API 의 message 를 보여 준다.
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ApiError, api } from '../api'
import ErrorBox from '../components/ErrorBox.vue'
import TimeSelect from '../components/TimeSelect.vue'
import { dateLabel, dateTimeLabel, monthLabel, t, tk } from '../i18n'
import { dayType, monthDates } from '../lib/calendar'
import { changeKind, checkLimits, diffSubmission, entriesFromItems, needsTime, validateEntry, type DayChoice, type DayEntry } from '../lib/submission'
import { endOptions, startOptions } from '../lib/time'
import { toast } from '../toast'
import type { SubmitPage } from '../types'

const route = useRoute()
const token = String(route.params.token)

const page = ref<SubmitPage | null>(null)
const original = ref<DayEntry[]>([])
const entries = ref<DayEntry[]>([])
const status = ref<'loading' | 'ready' | 'closed' | 'notfound' | 'error'>('loading')
const closedMessage = ref('')
const loadError = ref<unknown>(null)
const saveError = ref<unknown>(null)
const saving = ref(false)
const showErrors = ref(false)
// (가정 신규) 「出られる」를 고르면 직전에 고른 시각을 채운다. 처음에는 09:00–17:00.
const lastTimes = ref({ start: '09:00', end: '17:00' })

const starts = startOptions()

async function load() {
  try {
    const p = await api.submitPage(token)
    page.value = p
    const dates = monthDates(p.target_month)
    original.value = entriesFromItems(dates, p.items)
    entries.value = original.value.map((e) => ({ ...e }))
    status.value = 'ready'
  } catch (e) {
    if (e instanceof ApiError && e.status === 410) {
      status.value = 'closed'
      closedMessage.value = e.message
    } else if (e instanceof ApiError && e.status === 404) status.value = 'notfound'
    else {
      status.value = 'error'
      loadError.value = e
    }
  }
}
onMounted(load)

const regular = computed(() => page.value?.is_regular ?? false)
const kinds = computed<{ value: DayChoice; label: string }[]>(() => [
  { value: 'none', label: regular.value ? t('submit.kind.none.regular') : t('submit.kind.none.part') },
  { value: 'available', label: regular.value ? t('submit.kind.available.regular') : t('submit.kind.available.part') },
  { value: 'off', label: t('submit.kind.off') },
  { value: 'paid_leave', label: t('submit.kind.paid_leave') },
  { value: 'half_leave', label: t('submit.kind.half_leave') },
])

const errors = computed(() => entries.value.map(validateEntry))
const hasErrors = computed(() => errors.value.some((e) => e !== null))
const diff = computed(() => diffSubmission(original.value, entries.value))
const changeCount = computed(() => diff.value.upserts.length + diff.value.deletes.length)
const limits = computed(() =>
  page.value
    ? checkLimits(entries.value, { is_regular: page.value.is_regular, off_request_limit: page.value.off_request_limit, work_request_limit: page.value.work_request_limit })
    : null,
)
const limitText = (n: number | null) => (n === null ? '—' : String(n))

function setKind(i: number, kind: DayChoice) {
  const cur = entries.value[i]
  if (!cur) return
  entries.value[i] = changeKind(cur, kind, lastTimes.value)
}
function setTime(i: number, which: 'start_time' | 'end_time', v: string) {
  const cur = entries.value[i]
  if (!cur) return
  const next = { ...cur, [which]: v }
  // 시작을 끝 뒤로 옮기면 끝을 첫 선택지로
  if (which === 'start_time' && next.end_time && next.end_time <= v) next.end_time = endOptions(v)[0] ?? null
  entries.value[i] = next
  if (next.start_time && next.end_time) lastTimes.value = { start: next.start_time, end: next.end_time }
}

async function save() {
  showErrors.value = true
  if (hasErrors.value || !changeCount.value) return
  saving.value = true
  saveError.value = null
  const { upserts, deletes } = diff.value
  try {
    // DELETE 를 먼저: 희망휴를 다른 날로 옮길 때 PUT 시점에 상한을 넘겨 422 가 나지 않게 (리뷰 B M1)
    for (const d of deletes) await api.deleteAvailability(token, d)
    if (upserts.length) await api.putAvailability(token, upserts)
    toast(t('submit.saved'))
    showErrors.value = false
  } catch (e) {
    if (e instanceof ApiError && e.status === 410) {
      status.value = 'closed'
      closedMessage.value = e.message
      return
    }
    saveError.value = e
  } finally {
    saving.value = false
  }
  // 서버에 실제로 남은 것을 다시 읽어 보여 준다 (일부만 저장된 경우도 그대로 보이게)
  if (status.value === 'ready') {
    try {
      const p = await api.submitPage(token)
      page.value = p
      original.value = entriesFromItems(monthDates(p.target_month), p.items)
      if (!saveError.value) entries.value = original.value.map((e) => ({ ...e }))
    } catch {
      /* 다시 읽기 실패 — 입력은 그대로 둔다 */
    }
  }
}
</script>

<template>
  <div class="page submit">
    <p v-if="status === 'loading'" class="muted"><span class="spinner" aria-hidden="true"></span> {{ t('common.loading') }}</p>

    <div v-else-if="status === 'closed'" class="notice notice--warning" role="alert">
      <p class="notice-title">{{ t('submit.closed') }}</p>
      <p>{{ t('err.hint.SUBMISSION_CLOSED') }}</p>
      <p v-if="closedMessage" class="small muted">{{ closedMessage }}</p>
    </div>

    <div v-else-if="status === 'notfound'" class="notice notice--required" role="alert">
      <p class="notice-title">{{ t('submit.notFound') }}</p>
    </div>

    <ErrorBox v-else-if="status === 'error'" :error="loadError" />

    <template v-else-if="page">
      <header class="submit-head">
        <p class="label-caps">{{ t('submit.month', { month: monthLabel(page.target_month) }) }}</p>
        <h1>{{ /(さん|様|くん|ちゃん|님)$/.test(page.employee_name) ? page.employee_name : t('submit.hello', { name: page.employee_name }) }}</h1>
        <p class="small num">{{ t('submit.deadline', { at: dateTimeLabel(page.submission_deadline) }) }}</p>
        <p class="muted small">{{ regular ? t('submit.lead.regular') : t('submit.lead.part') }}</p>
        <p v-if="regular && limits" class="row small num">
          <span class="chip" :class="{ 'chip--required': limits.offOver }">{{ t('submit.kind.off') }} {{ limits.offCount }}/{{ limitText(page.off_request_limit) }}</span>
          <span class="chip" :class="{ 'chip--required': limits.workOver }">{{ t('submit.kind.available.regular') }} {{ limits.workCount }}/{{ limitText(page.work_request_limit) }}</span>
        </p>
        <p v-if="limits?.offOver" class="field-error">{{ t('submit.limitOff', { n: page.off_request_limit ?? 0 }) }}</p>
        <p v-if="limits?.workOver" class="field-error">{{ t('submit.limitWork', { n: page.work_request_limit ?? 0 }) }}</p>
      </header>

      <ol class="days">
        <li
          v-for="(e, i) in entries"
          :key="e.work_date"
          class="day"
          :class="[`dt-${dayType(e.work_date)}`, { 'is-set': e.kind !== 'none', 'is-invalid': showErrors && errors[i] }]"
        >
          <div class="day-top">
            <label :for="`day-${e.work_date}-kind`" class="day-date num">{{ dateLabel(e.work_date) }}<span class="sr-only">{{ t('submit.dayLabel', { date: '' }) }}</span></label>
            <select :id="`day-${e.work_date}-kind`" class="select kind" :value="e.kind" @change="setKind(i, ($event.target as HTMLSelectElement).value as DayChoice)">
              <option v-for="k in kinds" :key="k.value" :value="k.value">{{ k.label }}</option>
            </select>
          </div>
          <div v-if="needsTime(e.kind)" class="times">
            <TimeSelect
              :id="`day-${e.work_date}-start`"
              :label="t('submit.start')"
              :options="starts"
              :model-value="e.start_time"
              :invalid="showErrors && !!errors[i]"
              @update:model-value="setTime(i, 'start_time', $event)"
            />
            <TimeSelect
              :id="`day-${e.work_date}-end`"
              :label="t('submit.end')"
              :options="endOptions(e.start_time)"
              :model-value="e.end_time"
              :invalid="showErrors && !!errors[i]"
              @update:model-value="setTime(i, 'end_time', $event)"
            />
          </div>
          <p v-if="showErrors && errors[i]" class="field-error">{{ tk(`submit.itemErr.${errors[i]}`, String(errors[i])) }}</p>
        </li>
      </ol>

      <div class="savebar">
        <ErrorBox v-if="saveError" :error="saveError" />
        <p v-if="showErrors && hasErrors" class="field-error" role="alert">{{ t('submit.fixErrors') }}</p>
        <div class="savebar-row">
          <span class="small num" :class="{ muted: !changeCount }">{{ changeCount ? t('submit.unsaved', { n: changeCount }) : t('submit.noChanges') }}</span>
          <button type="button" class="btn btn--primary btn--lg" :disabled="saving || !changeCount" @click="save">
            <span v-if="saving" class="spinner" aria-hidden="true"></span>{{ saving ? t('submit.saving') : t('submit.save') }}
          </button>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.submit {
  max-inline-size: 40rem;
  padding-block-end: 0;
}
.submit-head {
  display: grid;
  gap: var(--sp-2);
}
.days {
  list-style: none;
  display: grid;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel);
  min-width: 0;
}
.day {
  display: grid;
  gap: var(--sp-2);
  padding: var(--sp-3);
  border-bottom: 1px solid var(--line);
  min-width: 0;
}
.day:last-child {
  border-bottom: 0;
}
.day.dt-sat {
  background: var(--sat-soft);
}
.day.dt-sun,
.day.dt-holiday {
  background: var(--sun-soft);
}
.day.is-invalid {
  box-shadow: inset 0 0 0 2px var(--required);
}
.day-top {
  display: grid;
  grid-template-columns: 6.5rem minmax(0, 1fr);
  align-items: center;
  gap: var(--sp-3);
}
.day-date {
  font-weight: 700;
  font-size: var(--fs-input);
  white-space: nowrap;
}
.dt-sat .day-date {
  color: var(--sat);
}
.dt-sun .day-date,
.dt-holiday .day-date {
  color: var(--sun);
}
.kind,
.times :deep(.select) {
  min-block-size: var(--touch);
}
.is-set .kind {
  border-color: var(--ai);
  font-weight: 600;
}
.times {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--sp-3);
  padding-inline-start: calc(6.5rem + var(--sp-3));
}
.savebar {
  position: sticky;
  bottom: 0;
  display: grid;
  gap: var(--sp-2);
  padding: var(--sp-3) 0 max(var(--sp-3), env(safe-area-inset-bottom, 0px));
  background: var(--paper);
  border-top: 1px solid var(--line);
}
.savebar-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-3);
}
@media (max-width: 30rem) {
  .day-top {
    grid-template-columns: 6rem minmax(0, 1fr);
  }
  .day-date {
    font-size: var(--fs-md);
  }
  .times {
    padding-inline-start: 0;
  }
}
</style>
