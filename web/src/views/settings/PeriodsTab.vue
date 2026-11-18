<script setup lang="ts">
// 근무 기간 (W-2, A-9, A-10): 목록 · 만들기 · 수정 · 제출 링크 보내기(LINE 스텁).
// 상한 빈칸 = 제한 없음 (V9_ko 2-3 「NULL=무제한」). 반휴는 상한에 세지 않는다.
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { api } from '../../api'
import ErrorBox from '../../components/ErrorBox.vue'
import StateChip from '../../components/StateChip.vue'
import { dateTimeLabel, monthLabel, t } from '../../i18n'
import { isoToLocalInput, localInputToIso, monthInputToTargetMonth } from '../../lib/calendar'
import { optionalInt } from '../../lib/forms'
import { toast } from '../../toast'
import type { Employee, ShiftPeriod, ShiftPeriodInput, SubmissionLinksResponse } from '../../types'

const periods = ref<ShiftPeriod[]>([])
const employees = ref<Employee[]>([])
const loadError = ref<unknown>(null)
const actionError = ref<unknown>(null)
const busy = ref(false)

async function load() {
  try {
    const [ps, es] = await Promise.all([api.periods(), api.employees().catch(() => [] as Employee[])])
    periods.value = [...ps].sort((a, b) => (a.target_month < b.target_month ? 1 : -1))
    employees.value = es
    loadError.value = null
  } catch (e) {
    loadError.value = e
  }
}
onMounted(load)

// ---------------------------------------------------------------- 폼
interface FormState {
  id: number | null
  month: string // "YYYY-MM"
  deadline: string // datetime-local
  offDays: string
  offLimit: string
  workLimit: string
}
const form = ref<FormState | null>(null)
const touched = ref(false)

function nextMonth(): string {
  const d = new Date()
  const n = new Date(d.getFullYear(), d.getMonth() + 1, 1)
  return `${n.getFullYear()}-${String(n.getMonth() + 1).padStart(2, '0')}`
}
function openNew() {
  form.value = { id: null, month: nextMonth(), deadline: '', offDays: '9', offLimit: '', workLimit: '' }
  touched.value = false
  actionError.value = null
}
function openEdit(p: ShiftPeriod) {
  form.value = {
    id: p.period_id,
    month: p.target_month.slice(0, 7),
    deadline: isoToLocalInput(p.submission_deadline),
    offDays: String(p.designated_off_days),
    offLimit: p.off_request_limit === null ? '' : String(p.off_request_limit),
    workLimit: p.work_request_limit === null ? '' : String(p.work_request_limit),
  }
  touched.value = false
  actionError.value = null
}

const parsed = computed<{ value: ShiftPeriodInput | null; error: string | null }>(() => {
  const f = form.value
  if (!f) return { value: null, error: null }
  const target = monthInputToTargetMonth(f.month)
  if (!target) return { value: null, error: t('periods.monthInvalid') }
  const deadline = localInputToIso(f.deadline)
  if (!deadline) return { value: null, error: t('periods.deadlineInvalid') }
  const off = optionalInt(f.offDays)
  if (off === null || off < 0 || off > 31) return { value: null, error: t('periods.offDaysInvalid') }
  return {
    value: { target_month: target, submission_deadline: deadline, designated_off_days: off, off_request_limit: optionalInt(f.offLimit), work_request_limit: optionalInt(f.workLimit) },
    error: null,
  }
})

async function save() {
  touched.value = true
  const f = form.value
  const v = parsed.value.value
  if (!f || !v) return
  busy.value = true
  actionError.value = null
  try {
    if (f.id === null) await api.createPeriod(v)
    else {
      // 대상 월은 바꾸지 않는다 (마감일 · 상한만 수정, V9_ko 2-3)
      const { target_month: _ignored, ...rest } = v
      void _ignored
      await api.updatePeriod(f.id, rest)
    }
    toast(t('common.saved'))
    form.value = null
    await load()
  } catch (e) {
    actionError.value = e
  } finally {
    busy.value = false
  }
}

// ---------------------------------------------------------------- 제출 링크
const links = ref<{ period: ShiftPeriod; res: SubmissionLinksResponse } | null>(null)
const nameOf = (id: number) => employees.value.find((e) => e.employee_id === id)?.name ?? `#${id}`
async function sendLinks(p: ShiftPeriod) {
  busy.value = true
  actionError.value = null
  try {
    const res = await api.sendSubmissionLinks(p.period_id)
    links.value = { period: p, res }
    toast(t('periods.linksSent', { n: res.sent }))
  } catch (e) {
    actionError.value = e
  } finally {
    busy.value = false
  }
}
function limitText(n: number | null): string {
  return n === null ? t('common.unlimited') : t('fmt.days', { n })
}
</script>

<template>
  <div class="stack stack--lg">
    <ErrorBox v-if="loadError" :error="loadError" />
    <div class="row row--between">
      <span class="muted small num">{{ t('fmt.count', { n: periods.length }) }}</span>
      <button type="button" class="btn btn--primary" @click="openNew">{{ t('periods.add') }}</button>
    </div>
    <ErrorBox v-if="actionError" :error="actionError" />

    <form v-if="form" class="panel" aria-labelledby="period-form-title" @submit.prevent="save">
      <h3 id="period-form-title">{{ form.id === null ? t('periods.addTitle') : t('periods.editTitle', { month: monthLabel(`${form.month}-01`) }) }}</h3>
      <div class="form-grid">
        <div class="field">
          <label for="period-month">{{ t('periods.month') }}</label>
          <input id="period-month" v-model="form.month" class="input num" type="month" :disabled="form.id !== null" />
        </div>
        <div class="field">
          <label for="period-deadline">{{ t('periods.deadline') }}</label>
          <input id="period-deadline" v-model="form.deadline" class="input num" type="datetime-local" step="1800" />
        </div>
        <div class="field">
          <label for="period-offdays">{{ t('periods.offDays') }}</label>
          <input id="period-offdays" v-model="form.offDays" class="input num" type="number" min="0" max="31" />
        </div>
        <div class="field">
          <label for="period-offlimit">{{ t('periods.offLimit') }}</label>
          <input id="period-offlimit" v-model="form.offLimit" class="input num" type="number" min="0" max="31" :placeholder="t('common.unlimited')" />
        </div>
        <div class="field">
          <label for="period-worklimit">{{ t('periods.workLimit') }}</label>
          <input id="period-worklimit" v-model="form.workLimit" class="input num" type="number" min="0" max="31" :placeholder="t('common.unlimited')" />
        </div>
      </div>
      <p class="field-hint">{{ t('periods.limitHint') }}</p>
      <p v-if="touched && parsed.error" class="field-error" role="alert">{{ parsed.error }}</p>
      <div class="row">
        <button type="submit" class="btn btn--primary" :disabled="busy">{{ t('common.save') }}</button>
        <button type="button" class="btn" @click="form = null">{{ t('common.cancel') }}</button>
      </div>
    </form>

    <div v-if="links" class="notice notice--info" role="status">
      <p class="notice-title">{{ t('periods.linksTitle') }} — {{ monthLabel(links.period.target_month) }} · {{ t('periods.linksSent', { n: links.res.sent }) }}</p>
      <ul class="links">
        <li v-for="l in links.res.links" :key="l.employee_id">
          <span class="link-name">{{ nameOf(l.employee_id) }}</span>
          <a :href="l.url" class="mono small link-url" target="_blank" rel="noopener">{{ l.url }}</a>
        </li>
      </ul>
      <div class="row"><button type="button" class="btn btn--quiet btn--sm" @click="links = null">{{ t('common.close') }}</button></div>
    </div>

    <div class="table-scroll">
      <table class="table">
        <thead>
          <tr>
            <th scope="col">{{ t('periods.month') }}</th>
            <th scope="col">{{ t('periods.deadline') }}</th>
            <th scope="col" class="num">{{ t('periods.offDays') }}</th>
            <th scope="col" class="num">{{ t('periods.offLimit') }}</th>
            <th scope="col" class="num">{{ t('periods.workLimit') }}</th>
            <th scope="col">{{ t('periods.state') }}</th>
            <th scope="col">{{ t('common.actions') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!periods.length">
            <td colspan="7" class="muted">{{ t('periods.empty') }}</td>
          </tr>
          <tr v-for="p in periods" :key="p.period_id">
            <th scope="row">
              <RouterLink :to="`/periods/${p.period_id}`" class="month-link">{{ monthLabel(p.target_month) }}</RouterLink>
            </th>
            <td class="tnum nowrap">{{ dateTimeLabel(p.submission_deadline) }}</td>
            <td class="num">{{ t('fmt.days', { n: p.designated_off_days }) }}</td>
            <td class="num">{{ limitText(p.off_request_limit) }}</td>
            <td class="num">{{ limitText(p.work_request_limit) }}</td>
            <td><StateChip :state="p.run_state ?? 'not_started'" /></td>
            <td>
              <div class="row">
                <RouterLink :to="`/periods/${p.period_id}`" class="btn btn--sm btn--primary">{{ t('periods.open') }}</RouterLink>
                <button type="button" class="btn btn--sm" @click="openEdit(p)">{{ t('common.edit') }}</button>
                <button type="button" class="btn btn--sm" :disabled="busy" @click="sendLinks(p)">{{ t('periods.sendLinks') }}</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.month-link {
  font-weight: 700;
  white-space: nowrap;
}
a.btn {
  text-decoration: none;
}
.links {
  list-style: none;
  display: grid;
  gap: var(--sp-1);
}
.links li {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-1) var(--sp-3);
  min-width: 0;
}
.link-name {
  font-weight: 600;
  min-inline-size: 6rem;
}
.link-url {
  overflow-wrap: anywhere;
  min-width: 0;
}
</style>
