<script setup lang="ts">
// 승인 화면 (W-4) + 재편성 요청 (W-5) + 편집 모드 (W-6).
// 읽는 순서: 요약 줄 → 표 → 위반 목록 → 승인 / 재편성 → 참고.
// 승인을 막는 판단은 서버(check_approval, F-8)가 한다. 화면은 같은 기준으로 미리 알려 줄 뿐 버튼을 막지 않는다
// (막힌 결과 = ReviewWait.blocked 가 돌아와 「確定できませんでした」로 보인다).
import { computed, ref, watch } from 'vue'
import { ApiError, api } from '../api'
import { dateLabel, t, tk } from '../i18n'
import { detailText, type NameOf } from '../lib/describe'
import { remainingRequests } from '../lib/flow'
import { assignmentsToGrid, changedKeys, clearCell, gridToAssignments, parseCellKey, setWork, type Grid, type GridRow } from '../lib/grid'
import { blockingViolations, countViolations, isWaivable, marksFor, sanitizeWaived } from '../lib/violations'
import { toast } from '../toast'
import type { Draft, ReviewWait, RunStatus } from '../types'
import CellEditor from './CellEditor.vue'
import DraftGrid from './DraftGrid.vue'
import ErrorBox from './ErrorBox.vue'
import ViolationList from './ViolationList.vue'

const props = defineProps<{
  periodId: number
  wait: ReviewWait
  draft: Draft | null
  rows: readonly GridRow[]
  dates: readonly string[]
  nameOf: NameOf
  /** 예외 허용 체크. 부모(PeriodView)가 안(draft_id)마다 가진다 — 승인이 막혀 돌아와도 고른 것이 남게 */
  waived: ReadonlySet<string>
}>()
const emit = defineEmits<{
  acted: [kind: 'approve' | 'redraft' | 'edit', status: RunStatus]
  refresh: []
  /** 체크 하나를 켜고 끔. 부모가 자기 최신 값에 적용한다 (같은 틱에 두 번 눌러도 하나를 잃지 않게) */
  waive: [key: string, on: boolean]
}>()

// ---------------------------------------------------------------- 표
const base = computed(() => assignmentsToGrid(props.draft?.assignments ?? []))
const editing = ref(false)
const editGrid = ref<Grid>(new Map())
const shownGrid = computed(() => (editing.value ? editGrid.value : base.value.grid))
const changed = computed(() => (editing.value ? changedKeys(base.value.grid, editGrid.value) : new Set<string>()))
const marks = computed(() => marksFor(props.wait.violations))
const focusKey = ref<string | null>(null)

// ---------------------------------------------------------------- 위반 · 예외 허용
watch(
  () => props.wait.draft_id,
  () => {
    editing.value = false
  },
)
const counts = computed(() => countViolations(props.wait.violations))
const waivedKeys = computed(() => sanitizeWaived(props.wait.violations, props.waived))
const blocking = computed(() => blockingViolations(props.wait.violations, new Set(waivedKeys.value)))
const nonWaivableLeft = computed(() => blocking.value.filter((v) => !isWaivable(v)).length)
const unwaivedRequired = computed(() => blocking.value.length - nonWaivableLeft.value)
const remaining = computed(() => remainingRequests(props.wait))

function toggle(key: string, on: boolean) {
  emit('waive', key, on)
}
function locate(key: string) {
  focusKey.value = null
  requestAnimationFrame(() => (focusKey.value = key))
}

const resultTone = computed(() => ({ valid: 'chip--ok', limit_reached: 'chip--warning', draft_empty: 'chip--required' })[props.wait.result] ?? '')

// ---------------------------------------------------------------- 조작
const busy = ref<'approve' | 'redraft' | 'edit' | null>(null)
// 이 오류가 오면 화면의 안이 낡은 것이므로 상태를 다시 읽는다 (계약 + 11/12 추가 코드)
const REFRESH_CODES = new Set(['STALE_DRAFT', 'NOT_WAITING', 'ALREADY_APPROVED', 'ALREADY_CONFIRMED'])
const actionError = ref<unknown>(null)
const redraftText = ref('')

async function run(kind: 'approve' | 'redraft' | 'edit', call: () => Promise<RunStatus>) {
  busy.value = kind
  actionError.value = null
  try {
    const s = await call()
    emit('acted', kind, s)
    return true
  } catch (e) {
    actionError.value = e
    if (e instanceof ApiError && REFRESH_CODES.has(e.code)) emit('refresh')
    return false
  } finally {
    busy.value = null
  }
}

function approve() {
  void run('approve', () => api.approve(props.periodId, props.wait.draft_id, waivedKeys.value))
}
async function redraft() {
  const ok = await run('redraft', () => api.requestRedraft(props.periodId, props.wait.draft_id, redraftText.value.trim()))
  if (ok) {
    toast(t('redraft.sent'))
    redraftText.value = ''
  }
}

// ---------------------------------------------------------------- 편집 모드 (W-6)
const editorKey = ref<string | null>(null)
const editorError = ref<string | null>(null)
const editorTitle = computed(() => {
  if (!editorKey.value) return ''
  const { employeeId, workDate } = parseCellKey(editorKey.value)
  return t('edit.dialogTitle', { name: props.nameOf(employeeId) ?? `#${employeeId}`, date: dateLabel(workDate) })
})

function startEdit() {
  editGrid.value = new Map(base.value.grid)
  editing.value = true
  actionError.value = null
}
function discardEdit() {
  if (changed.value.size && !window.confirm(t('edit.discardConfirm'))) return
  editing.value = false
  editorKey.value = null
}
function openEditor(key: string) {
  editorError.value = null
  editorKey.value = key
}
function applyEditor(start: string, end: string) {
  if (!editorKey.value) return
  const r = setWork(editGrid.value, editorKey.value, start, end)
  if ('error' in r) {
    editorError.value = r.error
    return
  }
  editGrid.value = r.grid
  editorKey.value = null
}
function clearEditor() {
  if (!editorKey.value) return
  const r = clearCell(editGrid.value, editorKey.value)
  if ('error' in r) {
    editorError.value = r.error
    return
  }
  editGrid.value = r.grid
  editorKey.value = null
}
async function saveEdit() {
  const ok = await run('edit', () => api.saveEdit(props.periodId, props.wait.draft_id, gridToAssignments(editGrid.value)))
  if (ok) {
    editing.value = false
    toast(t('edit.saved'), 'info')
  }
}
</script>

<template>
  <section class="review" aria-labelledby="review-title">
    <div class="review-head">
      <h2 id="review-title">{{ t('review.title') }}</h2>
      <!-- 요약 먼저 -->
      <div class="statusbar" role="group" :aria-label="t('review.summary')">
        <span class="chip chip--lg" :class="resultTone">{{ tk(`review.result.${wait.result}`, wait.result) }}</span>
        <span class="chip chip--lg chip--required">{{ t('review.required', { n: counts.required }) }}</span>
        <span class="chip chip--lg chip--warning">{{ t('review.warning', { n: counts.warning }) }}</span>
        <span class="stat num">{{ t('review.autoRetry', { n: wait.auto_retry_count }) }}</span>
        <span class="stat num">{{ t('review.remaining', { n: remaining }) }}</span>
        <span class="stat num muted">{{ t('review.draft', { id: wait.draft_id }) }}</span>
      </div>
      <p class="muted">{{ tk(`review.resultHint.${wait.result}`, '') }}</p>
    </div>

    <div v-if="wait.blocked.length" class="notice notice--required blocked" role="alert">
      <p class="notice-title">{{ t('blocked.title') }}</p>
      <p>{{ t('blocked.lead') }}</p>
      <ViolationList :violations="wait.blocked" :name-of="nameOf" id-prefix="blocked" @locate="locate" />
    </div>

    <ErrorBox v-if="actionError" :error="actionError" />

    <!-- 편집 도구 줄 -->
    <div class="toolbar">
      <template v-if="!editing">
        <button type="button" class="btn" :disabled="!draft || busy !== null" @click="startEdit">{{ t('edit.enter') }}</button>
      </template>
      <template v-else>
        <span class="chip chip--accent chip--lg">{{ t('edit.title') }}</span>
        <span class="small muted edit-lead">{{ t('edit.lead') }}</span>
        <span class="stat num">{{ t('edit.changed', { n: changed.size }) }}</span>
        <div class="row toolbar-actions">
          <button type="button" class="btn" :disabled="busy !== null" @click="discardEdit">{{ t('edit.discard') }}</button>
          <button type="button" class="btn btn--primary" :disabled="busy !== null || changed.size === 0" @click="saveEdit">
            <span v-if="busy === 'edit'" class="spinner" aria-hidden="true"></span>{{ t('edit.save') }}
          </button>
        </div>
      </template>
    </div>

    <p v-if="base.conflicts.length" class="notice notice--warning small">{{ t('grid.conflicts', { n: base.conflicts.length }) }}</p>

    <p v-if="!draft" class="muted"><span class="spinner" aria-hidden="true"></span> {{ t('common.loading') }}</p>
    <DraftGrid
      v-else
      :dates="dates"
      :rows="rows"
      :grid="shownGrid"
      :marks="marks"
      :violations="wait.violations"
      :changed="changed"
      :editable="editing"
      :focus-key="focusKey"
      @edit="openEditor"
    />

    <div class="cols">
      <section class="section" aria-labelledby="viol-title">
        <h3 id="viol-title">{{ t('viol.title') }}</h3>
        <ViolationList
          :violations="wait.violations"
          :name-of="nameOf"
          :waived="props.waived"
          :selectable="!editing"
          id-prefix="viol"
          @toggle="toggle"
          @locate="locate"
        />
      </section>

      <aside class="actions">
        <section v-if="!editing" class="panel" aria-labelledby="approve-title">
          <h3 id="approve-title">{{ t('approve.title') }}</h3>
          <p class="small num">{{ t('approve.waivedCount', { n: waivedKeys.length }) }}</p>
          <p v-if="blocking.length" class="notice notice--warning small">
            {{ t('approve.blockHint', { a: nonWaivableLeft, b: unwaivedRequired }) }}
          </p>
          <button type="button" class="btn btn--primary btn--lg" :disabled="busy !== null || !draft" @click="approve">
            <span v-if="busy === 'approve'" class="spinner" aria-hidden="true"></span>{{ t('approve.button') }}
          </button>
        </section>

        <section v-if="!editing" class="panel" aria-labelledby="redraft-title">
          <h3 id="redraft-title">{{ t('redraft.title') }}</h3>
          <template v-if="remaining > 0">
            <div class="field">
              <label for="redraft-text">{{ t('redraft.label') }}</label>
              <textarea id="redraft-text" v-model="redraftText" class="textarea" :placeholder="t('redraft.placeholder')" rows="3"></textarea>
            </div>
            <button type="button" class="btn" :disabled="busy !== null" @click="redraft">
              <span v-if="busy === 'redraft'" class="spinner" aria-hidden="true"></span>{{ t('redraft.button', { n: remaining }) }}
            </button>
          </template>
          <p v-else class="small muted">{{ t('redraft.none', { limit: wait.manager_request_limit }) }}</p>
        </section>
      </aside>
    </div>

    <!-- 참고: 코드 검사 대상이 아님 (F-14, V9_ko 484) -->
    <section class="section reference" aria-labelledby="ref-title">
      <h3 id="ref-title">{{ t('ref.title') }}</h3>
      <div class="ref-cols">
        <div class="stack">
          <h4 class="ref-h">{{ t('ref.shortages') }}</h4>
          <div v-if="wait.shortages.length" class="table-scroll">
            <table class="table">
              <thead>
                <tr>
                  <th scope="col">{{ t('ref.date') }}</th>
                  <th scope="col">{{ t('ref.time') }}</th>
                  <th scope="col" class="num">{{ t('ref.need') }}</th>
                  <th scope="col" class="num">{{ t('ref.have') }}</th>
                  <th scope="col">{{ t('ref.reason') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(s, i) in wait.shortages" :key="i">
                  <td class="tnum nowrap">{{ dateLabel(s.work_date) }}</td>
                  <td class="tnum nowrap">{{ detailText({ start: s.start_time, end: s.end_time }) }}</td>
                  <td class="num">{{ s.need }}</td>
                  <td class="num">{{ s.have }}</td>
                  <td>{{ s.reason === 'impossible' ? t('ref.reasonImpossible') : (s.reason ?? '') }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="small muted">{{ t('ref.empty') }}</p>
        </div>
        <div class="stack">
          <h4 class="ref-h">{{ t('ref.notes') }}</h4>
          <ul v-if="wait.notes.length" class="plain-list">
            <li v-for="(n, i) in wait.notes" :key="i">{{ n }}</li>
          </ul>
          <p v-else class="small muted">{{ t('ref.empty') }}</p>
          <h4 class="ref-h">
            <span class="chip chip--warning">{{ t('instr.freeTitle') }}</span>
            <span class="small muted">{{ t('ref.freeText') }}</span>
          </h4>
          <ul v-if="wait.free_text_guidelines.length" class="plain-list">
            <li v-for="(n, i) in wait.free_text_guidelines" :key="i">{{ n }}</li>
          </ul>
          <p v-else class="small muted">{{ t('ref.empty') }}</p>
        </div>
      </div>
    </section>

    <CellEditor
      :open="editorKey !== null"
      :title="editorTitle"
      :cell="editorKey ? editGrid.get(editorKey) : undefined"
      :error="editorError"
      @apply="applyEditor"
      @clear="clearEditor"
      @close="editorKey = null"
    />
  </section>
</template>

<style scoped>
.review {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}
.review-head {
  display: grid;
  gap: var(--sp-2);
}
.statusbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-2) var(--sp-3);
  padding: var(--sp-2) var(--sp-3);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel);
}
.stat {
  font-size: var(--fs-sm);
  font-weight: 600;
  white-space: nowrap;
}
.stat + .stat::before {
  content: '';
  display: inline-block;
  inline-size: 1px;
  block-size: 0.9em;
  margin-inline-end: var(--sp-3);
  background: var(--line-strong);
  vertical-align: -0.1em;
}
.toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-2) var(--sp-3);
  min-width: 0;
}
.edit-lead {
  flex: 1 1 16rem;
  min-width: 0;
}
.toolbar-actions {
  margin-inline-start: auto;
}
.cols {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(16rem, 22rem);
  gap: var(--sp-5);
  align-items: start;
}
.actions {
  display: grid;
  gap: var(--sp-3);
  position: sticky;
  top: var(--sp-4);
}
.actions .btn--lg {
  inline-size: 100%;
}
.reference {
  border-top: 1px solid var(--line);
  padding-top: var(--sp-5);
}
.ref-cols {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 20rem), 1fr));
  gap: var(--sp-5);
}
.ref-h {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-2);
  margin: 0;
  font-size: var(--fs-md);
}
.plain-list {
  display: grid;
  gap: var(--sp-1);
  padding-inline-start: 1.2em;
}
.blocked {
  gap: var(--sp-3);
}
@media (max-width: 60rem) {
  .cols {
    grid-template-columns: minmax(0, 1fr);
  }
  .actions {
    position: static;
  }
}
</style>
