<script setup lang="ts">
// 기간 하나의 편성 화면. 상태(RunStatus.state)에 따라 보이는 것이 바뀐다.
//   not_started     → 제출 현황 · 점장 지시(W-9) · 「今すぐ編成を開始する」(W-3)
//   running         → 작은 진행 표시, 2초마다 run-status (W-3)
//   waiting_review  → 승인 화면 (W-4 · W-5 · W-6)
//   waiting_contact → 직원 답장 대기 (M-6, 기본 꺼 둠)
//   confirmed       → 「確定済み」 + 확정 표
//   error           → 오류 글
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { ApiError, api } from '../api'
import ErrorBox from '../components/ErrorBox.vue'
import DraftGrid from '../components/DraftGrid.vue'
import InstructionBox from '../components/InstructionBox.vue'
import ReviewPanel from '../components/ReviewPanel.vue'
import StateChip from '../components/StateChip.vue'
import SubmissionsOverview from '../components/SubmissionsOverview.vue'
import ViolationList from '../components/ViolationList.vue'
import { dateLabel, dateTimeLabel, monthLabel, t } from '../i18n'
import { monthDates } from '../lib/calendar'
import { confirmedDraftId, contactWait, POLL_MS, reviewWait, shouldPoll } from '../lib/flow'
import { assignmentsToGrid, gridRows } from '../lib/grid'
import { countViolations, marksFor } from '../lib/violations'
import { toast } from '../toast'
import type { Draft, Employee, EmployeeSubmissions, RunStatus, ShiftPeriod } from '../types'

const route = useRoute()
const periodId = Number(route.params.id)

const period = ref<ShiftPeriod | null>(null)
const employees = ref<Employee[]>([])
const submissions = ref<EmployeeSubmissions[]>([])
const status = ref<RunStatus | null>(null)
const draft = ref<Draft | null>(null)
const loadError = ref<unknown>(null)
const pollError = ref<unknown>(null)
const draftError = ref<unknown>(null)
const startError = ref<unknown>(null)
const starting = ref(false)

// 방금 한 조작 — 결과가 돌아오면 알림을 고른다 (편집 저장 → 재검증 결과, 승인 → 確定しました)
const pending = ref<{ kind: 'approve' | 'redraft' | 'edit'; fromDraft: number } | null>(null)
const revalidated = ref<{ r: number; w: number } | null>(null)

const state = computed(() => status.value?.state ?? period.value?.run_state ?? null)
const wait = computed(() => reviewWait(status.value))
const contact = computed(() => contactWait(status.value))
const dates = computed(() => (period.value ? monthDates(period.value.target_month) : []))
const names = computed(() => {
  const m = new Map<number, string>()
  for (const a of draft.value?.assignments ?? []) m.set(a.employee_id, a.employee_name)
  for (const e of employees.value) m.set(e.employee_id, e.name)
  return m
})
const nameOf = (id: number) => names.value.get(id)
const rows = computed(() => gridRows(employees.value, draft.value?.assignments ?? []))

// 예외 허용 체크: 안(draft_id)이 바뀔 때만 비운다. 승인 → running → 막힘으로 돌아와도 남는다
const waived = ref(new Set<string>())
let waivedDraft: number | null = null
watch(
  () => wait.value?.draft_id,
  (id) => {
    if (id != null && id !== waivedDraft) {
      waived.value = new Set()
      waivedDraft = id
    }
  },
)

// ---------------------------------------------------------------- 마지막으로 본 안 (확정 뒤 표를 다시 열 때. 「계약 변경 요청」 참고)
const LAST_KEY = `covershift.lastDraft.${periodId}`
function readLast(): number | null {
  try {
    const v = sessionStorage.getItem(LAST_KEY)
    return v ? Number(v) : null
  } catch {
    return null
  }
}
function rememberDraft(id: number) {
  try {
    sessionStorage.setItem(LAST_KEY, String(id))
  } catch {
    /* 무시 */
  }
}

async function loadDraft(id: number) {
  try {
    draft.value = await api.draft(id)
    draftError.value = null
  } catch (e) {
    draftError.value = e
  }
}

// ---------------------------------------------------------------- 진행 상태 · 2초 폴링 (W-3)
let timer: ReturnType<typeof setTimeout> | null = null
let alive = true
let pendingPolls = 0
const PENDING_POLL_CAP = 450 // 15분. 조작 결과가 끝내 안 오면 멈춘다

function schedule() {
  if (!alive || timer) return
  const keep = shouldPoll(status.value?.state) || (pending.value !== null && pendingPolls < PENDING_POLL_CAP)
  if (!keep) return
  timer = setTimeout(async () => {
    timer = null
    if (pending.value) pendingPolls++
    try {
      applyStatus(await api.runStatus(periodId))
      pollError.value = null
    } catch (e) {
      pollError.value = e
      schedule()
    }
  }, POLL_MS)
}

const runningSince = ref<number | null>(null)
const now = ref(Date.now())
const clock = setInterval(() => (now.value = Date.now()), 1000)
const elapsed = computed(() => {
  if (runningSince.value === null) return '0:00'
  const s = Math.max(0, Math.floor((now.value - runningSince.value) / 1000))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
})

function applyStatus(s: RunStatus) {
  status.value = s
  if (s.state === 'running') runningSince.value ??= Date.now()
  else runningSince.value = null

  const w = reviewWait(s)
  if (w) {
    rememberDraft(w.draft_id)
    if (draft.value?.draft_id !== w.draft_id) void loadDraft(w.draft_id)
    const p = pending.value
    if (p) {
      const newDraft = w.draft_id !== p.fromDraft
      if (p.kind === 'edit' && newDraft) {
        const c = countViolations(w.violations)
        revalidated.value = { r: c.required, w: c.warning }
        pending.value = null
      } else if (p.kind === 'redraft' && newDraft) {
        pending.value = null
      } else if (p.kind === 'approve' && w.blocked.length) {
        toast(t('blocked.title'), 'error', 6000)
        pending.value = null
      }
    }
  }
  if (s.state === 'confirmed') {
    if (pending.value?.kind === 'approve') toast(t('approve.done'))
    pending.value = null
    const cid = confirmedDraftId(s, period.value?.confirmed_draft_id, readLast())
    if (cid !== null && (draft.value?.draft_id !== cid || draft.value.status !== 'confirmed')) void loadDraft(cid)
  }
  if (s.state === 'error') pending.value = null
  if (!pending.value) pendingPolls = 0
  schedule()
}

async function refreshStatus() {
  try {
    applyStatus(await api.runStatus(periodId))
    pollError.value = null
  } catch (e) {
    pollError.value = e
  }
}

async function load() {
  const [p, emps, subs] = await Promise.allSettled([api.period(periodId), api.employees(), api.submissions(periodId)])
  if (p.status === 'fulfilled') period.value = p.value
  else loadError.value = p.reason
  if (emps.status === 'fulfilled') employees.value = emps.value
  if (subs.status === 'fulfilled') submissions.value = subs.value
  await refreshStatus()
}
onMounted(load)
onBeforeUnmount(() => {
  alive = false
  if (timer) clearTimeout(timer)
  clearInterval(clock)
})

// ---------------------------------------------------------------- 지금 시작 (W-3)
async function startNow() {
  starting.value = true
  startError.value = null
  try {
    const s = await api.startNow(periodId)
    toast(t('start.started'))
    applyStatus(s)
  } catch (e) {
    startError.value = e
    if (e instanceof ApiError && e.code === 'ALREADY_STARTED') await refreshStatus()
  } finally {
    starting.value = false
  }
}

function onWaive(key: string, on: boolean) {
  const next = new Set(waived.value)
  if (on) next.add(key)
  else next.delete(key)
  waived.value = next
}

function onActed(kind: 'approve' | 'redraft' | 'edit', s: RunStatus) {
  revalidated.value = null
  pending.value = { kind, fromDraft: wait.value?.draft_id ?? 0 }
  applyStatus(s)
}

// ---------------------------------------------------------------- 확정 표
const confirmedGrid = computed(() => assignmentsToGrid(draft.value?.assignments ?? []).grid)
const confirmedMarks = computed(() => marksFor(draft.value?.violations ?? []))
const confirmedReady = computed(() => state.value === 'confirmed' && draft.value?.status === 'confirmed')
</script>

<template>
  <div class="page page--wide">
    <nav class="small">
      <RouterLink :to="{ path: '/settings', query: { tab: 'periods' } }">← {{ t('period.back') }}</RouterLink>
    </nav>

    <ErrorBox v-if="loadError" :error="loadError" />

    <header v-if="period" class="page-head">
      <h1>{{ t('period.title', { month: monthLabel(period.target_month) }) }}</h1>
      <StateChip :state="state" large />
      <p class="muted small num">{{ t('period.deadline', { at: dateTimeLabel(period.submission_deadline) }) }}</p>
    </header>

    <ErrorBox v-if="pollError" :error="pollError" />

    <!-- 시작 전 -->
    <div v-if="state === 'not_started'" class="before">
      <SubmissionsOverview :submissions="submissions" />
      <div class="stack stack--lg">
        <InstructionBox :period-id="periodId" :editable="true" :name-of="nameOf" />
        <section class="section start" aria-labelledby="start-title">
          <h2 id="start-title">{{ t('start.title') }}</h2>
          <p class="muted small">{{ t('start.lead') }}</p>
          <p class="muted small">{{ t('instr.fixedAtStart') }}</p>
          <div class="row">
            <button type="button" class="btn btn--primary btn--lg" :disabled="starting" @click="startNow">
              <span v-if="starting" class="spinner" aria-hidden="true"></span>{{ t('start.button') }}
            </button>
          </div>
          <ErrorBox v-if="startError" :error="startError" />
        </section>
      </div>
    </div>

    <!-- 편성 중 -->
    <div v-else-if="state === 'running'" class="notice notice--info running" role="status">
      <p class="notice-title row"><span class="spinner" aria-hidden="true"></span>{{ t('run.running') }}</p>
      <p class="small num muted">{{ t('run.elapsed', { t: elapsed }) }}</p>
    </div>

    <!-- 직원 답장 대기 -->
    <div v-else-if="state === 'waiting_contact'" class="notice notice--warning" role="status">
      <p class="notice-title row"><span class="spinner" aria-hidden="true"></span>{{ t('run.contact') }}</p>
      <p v-if="contact" class="num">
        {{ t('run.contactDetail', { name: nameOf(contact.employee_id) ?? `#${contact.employee_id}`, date: dateLabel(contact.work_date), start: contact.start_time, end: contact.end_time }) }}
      </p>
      <p><RouterLink to="/line-stub">{{ t('run.contactLink') }}</RouterLink></p>
    </div>

    <!-- 승인 대기 -->
    <template v-else-if="state === 'waiting_review' && wait">
      <p v-if="revalidated" class="notice notice--info" role="status">{{ t('edit.revalidated', revalidated) }}</p>
      <ErrorBox v-if="draftError" :error="draftError" />
      <ReviewPanel
        :period-id="periodId"
        :wait="wait"
        :draft="draft && draft.draft_id === wait.draft_id ? draft : null"
        :rows="rows"
        :dates="dates"
        :name-of="nameOf"
        :waived="waived"
        @waive="onWaive"
        @acted="onActed"
        @refresh="refreshStatus"
      />
    </template>

    <!-- 확정 -->
    <template v-else-if="state === 'confirmed'">
      <section class="section" aria-labelledby="confirmed-title">
        <h2 id="confirmed-title">{{ t('confirmed.title') }}</h2>
        <ErrorBox v-if="draftError" :error="draftError" />
        <template v-if="confirmedReady && draft">
          <p class="muted">{{ t('confirmed.lead', { at: dateTimeLabel(draft.confirmed_at) }) }}</p>
          <DraftGrid :dates="dates" :rows="rows" :grid="confirmedGrid" :marks="confirmedMarks" :violations="draft.violations" />
          <section v-if="draft.violations.length" class="section" aria-labelledby="confirmed-viol">
            <h3 id="confirmed-viol">{{ t('viol.title') }}</h3>
            <ViolationList :violations="draft.violations" :name-of="nameOf" id-prefix="confirmed" />
          </section>
        </template>
        <p v-else-if="!draftError" class="notice">{{ t('confirmed.missing') }}</p>
      </section>
    </template>

    <!-- 오류 -->
    <div v-else-if="state === 'error'" class="notice notice--required" role="alert">
      <p class="notice-title">{{ t('run.error') }}</p>
      <p v-if="status?.error" class="mono small">{{ status.error }}</p>
      <div class="row">
        <button type="button" class="btn" @click="refreshStatus">{{ t('run.reload') }}</button>
      </div>
    </div>

    <p v-else-if="!loadError" class="muted"><span class="spinner" aria-hidden="true"></span> {{ t('common.loading') }}</p>

    <!-- 시작 뒤에도 제출 현황 · 확정된 지시는 접어서 볼 수 있다 -->
    <details v-if="state && state !== 'not_started'" class="later">
      <summary>{{ t('sub.title') }} · {{ t('instr.title') }}</summary>
      <div class="before">
        <SubmissionsOverview :submissions="submissions" />
        <InstructionBox :period-id="periodId" :editable="false" :name-of="nameOf" />
      </div>
    </details>
  </div>
</template>

<style scoped>
.before {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 26rem), 1fr));
  gap: var(--sp-6);
  align-items: start;
  min-width: 0;
}
.running .row {
  gap: var(--sp-2);
}
.start {
  border-top: 1px solid var(--line);
  padding-top: var(--sp-5);
}
.later {
  border-top: 1px solid var(--line);
  padding-top: var(--sp-4);
}
.later[open] > .before {
  margin-top: var(--sp-4);
}
</style>
