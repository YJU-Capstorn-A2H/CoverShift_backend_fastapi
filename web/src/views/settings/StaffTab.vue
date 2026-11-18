<script setup lang="ts">
// 직원 (W-2, A-7): 목록 · 추가 · 수정 (삭제 API 없음). LINE 연결 링크 발급 (A-13).
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api'
import ErrorBox from '../../components/ErrorBox.vue'
import { dateTimeLabel, t, tk, weekdayLabel } from '../../i18n'
import { normalizeFixedWeekdays, validateEmployee } from '../../lib/forms'
import { toast } from '../../toast'
import type { Employee, EmployeeInput } from '../../types'

const WEEKDAYS = [1, 2, 3, 4, 5, 6, 7] as const
const employees = ref<Employee[]>([])
const loadError = ref<unknown>(null)
const actionError = ref<unknown>(null)
const busy = ref(false)

async function load() {
  try {
    employees.value = await api.employees()
    loadError.value = null
  } catch (e) {
    loadError.value = e
  }
}
onMounted(load)

// ---------------------------------------------------------------- 입력 폼 (추가 · 수정 같이 씀)
interface FormState {
  id: number | null // null = 새 직원
  name: string
  is_regular: boolean
  has_qualification: boolean
  closing_ok: boolean
  weekly_days: string // '' = 決まりなし
  fixed: number[]
}
const form = ref<FormState | null>(null)
const touched = ref(false)

function openNew() {
  form.value = { id: null, name: '', is_regular: false, has_qualification: false, closing_ok: false, weekly_days: '', fixed: [] }
  touched.value = false
  actionError.value = null
}
function openEdit(e: Employee) {
  form.value = {
    id: e.employee_id,
    name: e.name,
    is_regular: e.is_regular,
    has_qualification: e.has_qualification,
    closing_ok: e.closing_ok,
    weekly_days: e.weekly_days === null ? '' : String(e.weekly_days),
    fixed: [...(e.fixed_weekdays ?? [])],
  }
  touched.value = false
  actionError.value = null
}

const payload = computed<EmployeeInput | null>(() => {
  const f = form.value
  if (!f) return null
  return {
    name: f.name.trim(),
    is_regular: f.is_regular,
    has_qualification: f.has_qualification,
    closing_ok: f.closing_ok,
    weekly_days: f.weekly_days === '' ? null : Number(f.weekly_days),
    fixed_weekdays: normalizeFixedWeekdays(f.fixed),
  }
})
const formError = computed(() => {
  const p = payload.value
  if (!p) return null
  const e = validateEmployee(p)
  return e ? tk(`empErr.${e}`, e) : null
})

async function save() {
  touched.value = true
  const p = payload.value
  const f = form.value
  if (!p || !f || formError.value) return
  busy.value = true
  actionError.value = null
  try {
    if (f.id === null) await api.createEmployee(p)
    else await api.updateEmployee(f.id, p)
    toast(t('common.saved'))
    form.value = null
    await load()
  } catch (e) {
    actionError.value = e
  } finally {
    busy.value = false
  }
}

// ---------------------------------------------------------------- LINE 연결 링크
const issued = ref<{ name: string; url: string; expires: string } | null>(null)
async function issueLink(e: Employee) {
  busy.value = true
  actionError.value = null
  try {
    const r = await api.linkToken(e.employee_id)
    issued.value = { name: e.name, url: r.link_url, expires: r.expires_at }
  } catch (err) {
    actionError.value = err
  } finally {
    busy.value = false
  }
}
async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    toast(t('common.copied'))
  } catch {
    /* 클립보드 권한이 없으면 링크를 직접 선택해 복사한다 */
  }
}

function fixedLabel(ws: number[] | null): string {
  return ws && ws.length ? ws.map(weekdayLabel).join('・') : t('staff.fixedNone')
}
</script>

<template>
  <div class="stack stack--lg">
    <ErrorBox v-if="loadError" :error="loadError" />
    <div class="row row--between">
      <span class="muted small num">{{ t('fmt.people', { n: employees.length }) }}</span>
      <button type="button" class="btn btn--primary" @click="openNew">{{ t('staff.add') }}</button>
    </div>

    <ErrorBox v-if="actionError" :error="actionError" />

    <div v-if="issued" class="notice notice--info" role="status">
      <p class="notice-title">{{ t('staff.linkIssued', { name: issued.name, at: dateTimeLabel(issued.expires) }) }}</p>
      <div class="row">
        <a :href="issued.url" class="mono small link-url" target="_blank" rel="noopener">{{ issued.url }}</a>
        <button type="button" class="btn btn--sm" @click="copyText(issued.url)">{{ t('common.copy') }}</button>
        <button type="button" class="btn btn--quiet btn--sm" @click="issued = null">{{ t('common.close') }}</button>
      </div>
    </div>

    <!-- 입력 폼: 따로 떨어진 편집 물건 -->
    <form v-if="form" class="panel" aria-labelledby="staff-form-title" @submit.prevent="save">
      <h3 id="staff-form-title">{{ form.id === null ? t('staff.addTitle') : t('staff.editTitle', { name: form.name }) }}</h3>
      <div class="form-grid">
        <div class="field">
          <label for="staff-name">{{ t('staff.name') }}</label>
          <input id="staff-name" v-model="form.name" class="input" type="text" autocomplete="off" />
        </div>
        <fieldset class="field">
          <legend class="field-label">{{ t('staff.type') }}</legend>
          <div class="row">
            <label class="check" for="staff-type-regular"><input id="staff-type-regular" v-model="form.is_regular" type="radio" :value="true" name="staff-type" />{{ t('staff.regular') }}</label>
            <label class="check" for="staff-type-part"><input id="staff-type-part" v-model="form.is_regular" type="radio" :value="false" name="staff-type" />{{ t('staff.part') }}</label>
          </div>
        </fieldset>
        <div class="field">
          <label for="staff-weekly">{{ t('staff.weeklyDays') }}</label>
          <select id="staff-weekly" v-model="form.weekly_days" class="select">
            <option value="">{{ t('staff.weeklyNone') }}</option>
            <option v-for="n in 7" :key="n" :value="String(n)">{{ t('fmt.days', { n }) }}</option>
          </select>
        </div>
      </div>
      <div class="row flags">
        <label class="check" for="staff-qual"><input id="staff-qual" v-model="form.has_qualification" type="checkbox" />{{ t('staff.qual') }}</label>
        <label class="check" for="staff-closing"><input id="staff-closing" v-model="form.closing_ok" type="checkbox" />{{ t('staff.closing') }}</label>
      </div>
      <fieldset class="stack">
        <legend class="field-label small muted">{{ t('staff.fixedWeekdays') }}</legend>
        <div class="row">
          <label v-for="w in WEEKDAYS" :key="w" class="check" :for="`staff-fixed-${w}`">
            <input :id="`staff-fixed-${w}`" v-model="form.fixed" type="checkbox" :value="w" />{{ weekdayLabel(w) }}
          </label>
        </div>
        <p class="field-hint">{{ t('staff.fixedHint') }}</p>
      </fieldset>
      <p v-if="touched && formError" class="field-error" role="alert">{{ formError }}</p>
      <div class="row">
        <button type="submit" class="btn btn--primary" :disabled="busy">{{ t('common.save') }}</button>
        <button type="button" class="btn" @click="form = null">{{ t('common.cancel') }}</button>
      </div>
    </form>

    <div class="table-scroll">
      <table class="table">
        <thead>
          <tr>
            <th scope="col">{{ t('staff.name') }}</th>
            <th scope="col">{{ t('staff.type') }}</th>
            <th scope="col">{{ t('staff.qualShort') }}</th>
            <th scope="col">{{ t('staff.closing') }}</th>
            <th scope="col" class="num">{{ t('staff.weeklyDays') }}</th>
            <th scope="col">{{ t('staff.fixedWeekdays') }}</th>
            <th scope="col">{{ t('staff.line') }}</th>
            <th scope="col">{{ t('common.actions') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!employees.length">
            <td colspan="8" class="muted">{{ t('staff.empty') }}</td>
          </tr>
          <tr v-for="e in employees" :key="e.employee_id">
            <th scope="row">
              <span class="name">{{ e.name }}</span> <span class="muted num small">#{{ e.employee_id }}</span>
            </th>
            <td><span class="chip" :class="{ 'chip--solid': e.is_regular }">{{ e.is_regular ? t('staff.regular') : t('staff.partShort') }}</span></td>
            <td><span v-if="e.has_qualification" class="chip chip--accent">{{ t('staff.qualShort') }}</span></td>
            <td><span v-if="e.closing_ok" class="chip">{{ t('staff.closingShort') }}</span></td>
            <td class="num">{{ e.weekly_days === null ? t('staff.weeklyNone') : t('fmt.days', { n: e.weekly_days }) }}</td>
            <td>{{ fixedLabel(e.fixed_weekdays) }}</td>
            <td>
              <span class="chip" :class="e.line_linked ? 'chip--ok' : ''">{{ e.line_linked ? t('staff.linked') : t('staff.notLinked') }}</span>
            </td>
            <td>
              <div class="row">
                <button type="button" class="btn btn--sm" @click="openEdit(e)">{{ t('common.edit') }}</button>
                <button type="button" class="btn btn--sm" :disabled="busy || e.line_linked" @click="issueLink(e)">{{ t('staff.linkButton') }}</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.name {
  font-weight: 600;
}
.flags {
  gap: var(--sp-2) var(--sp-5);
}
.link-url {
  overflow-wrap: anywhere;
  min-width: 0;
}
fieldset.field .row {
  min-block-size: 2.5rem;
}
</style>
