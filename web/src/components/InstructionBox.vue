<script setup lang="ts">
// 점장 지시 → 틀 규칙 (W-9, M-2). 미리보기(LLM 1번, 저장 안 함) → 점장이 고른 것만 확정.
// 틀에 안 맞는 말은 「参考・保証なし」 (V9_ko 478). 틀 검사에서 떨어진 것은 이유와 함께 보인다.
// 편성 시작 때 마지막 확정본이 고정된다 (F-17) → 시작 뒤에는 보기만.
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import { dateTimeLabel, t } from '../i18n'
import { describeRule, templateLabel, type NameOf } from '../lib/describe'
import { toast } from '../toast'
import type { ConfirmedInstruction, InstructionPreview } from '../types'
import ErrorBox from './ErrorBox.vue'

const props = defineProps<{ periodId: number; editable: boolean; nameOf: NameOf }>()

const current = ref<ConfirmedInstruction | null>(null)
const loadError = ref<unknown>(null)
const text = ref('')
const preview = ref<InstructionPreview | null>(null)
const previewText = ref('') // 미리보기를 만든 원문 (확정 때 같이 보낸다)
const useRule = ref<boolean[]>([])
const useFree = ref<boolean[]>([])
const busy = ref<'preview' | 'confirm' | null>(null)
const error = ref<unknown>(null)

async function loadCurrent() {
  try {
    current.value = await api.instruction(props.periodId)
    loadError.value = null
  } catch (e) {
    loadError.value = e
  }
}
onMounted(loadCurrent)

async function doPreview() {
  busy.value = 'preview'
  error.value = null
  try {
    const p = await api.previewInstruction(props.periodId, text.value)
    preview.value = p
    previewText.value = text.value
    useRule.value = p.rules.map(() => true)
    useFree.value = p.free_text.map(() => true)
  } catch (e) {
    error.value = e
  } finally {
    busy.value = null
  }
}

const chosenRules = computed(() => preview.value?.rules.filter((_, i) => useRule.value[i]) ?? [])
const chosenFree = computed(() => preview.value?.free_text.filter((_, i) => useFree.value[i]) ?? [])

async function doConfirm() {
  if (!preview.value) return
  busy.value = 'confirm'
  error.value = null
  try {
    await api.confirmInstruction(props.periodId, { text: previewText.value, rules: chosenRules.value, free_text: chosenFree.value })
    toast(t('instr.confirmed'))
    preview.value = null
    text.value = ''
    await loadCurrent()
  } catch (e) {
    error.value = e
  } finally {
    busy.value = null
  }
}

function rawText(raw: unknown): string {
  return typeof raw === 'string' ? raw : JSON.stringify(raw)
}
</script>

<template>
  <section class="section" aria-labelledby="instr-title">
    <h2 id="instr-title">{{ t('instr.title') }}</h2>
    <ErrorBox v-if="loadError" :error="loadError" />

    <!-- 확정된 지시 -->
    <div v-if="current" class="stack">
      <p class="label-caps">{{ t('instr.current', { at: dateTimeLabel(current.created_at) }) }}</p>
      <blockquote class="quote">{{ current.text }}</blockquote>
      <ul v-if="current.rules.length" class="rule-list">
        <li v-for="r in current.rules" :key="r.rule_key">
          <span class="chip chip--accent">{{ templateLabel(r.template_key) }}</span>
          <span>{{ describeRule(r, nameOf) }}</span>
        </li>
      </ul>
      <div v-if="current.free_text.length" class="stack">
        <p><span class="chip chip--warning">{{ t('instr.freeTitle') }}</span></p>
        <ul class="plain-list">
          <li v-for="(f, i) in current.free_text" :key="i">{{ f }}</li>
        </ul>
      </div>
    </div>
    <p v-else-if="!loadError" class="small muted">{{ t('instr.none') }}</p>

    <template v-if="editable">
      <p class="muted small">{{ t('instr.lead') }}</p>
      <div class="field">
        <label for="instr-text">{{ t('instr.textLabel') }}</label>
        <textarea id="instr-text" v-model="text" class="textarea" rows="4"></textarea>
      </div>
      <div class="row">
        <button type="button" class="btn" :disabled="!text.trim() || busy !== null" @click="doPreview">
          <span v-if="busy === 'preview'" class="spinner" aria-hidden="true"></span>
          {{ busy === 'preview' ? t('instr.previewing') : t('instr.preview') }}
        </button>
      </div>
      <ErrorBox v-if="error" :error="error" />

      <!-- 미리보기: 따로 떨어진 확인 물건이라 카드로 -->
      <div v-if="preview" class="panel preview" aria-labelledby="instr-preview-title">
        <h3 id="instr-preview-title">{{ t('instr.previewTitle') }}</h3>

        <div class="stack">
          <h4 class="sub-h">{{ t('instr.rulesTitle') }}</h4>
          <div v-if="preview.rules.length" class="table-scroll">
            <table class="table">
              <thead>
                <tr>
                  <th scope="col">{{ t('instr.use') }}</th>
                  <th scope="col">{{ t('instr.template') }}</th>
                  <th scope="col">{{ t('instr.content') }}</th>
                  <th scope="col">{{ t('instr.source') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(r, i) in preview.rules" :key="r.rule_key">
                  <td>
                    <label class="check" :for="`instr-rule-${i}`">
                      <input :id="`instr-rule-${i}`" v-model="useRule[i]" type="checkbox" />
                      <span class="sr-only">{{ t('instr.use') }} {{ r.rule_key }}</span>
                    </label>
                  </td>
                  <td><span class="chip chip--accent">{{ templateLabel(r.template_key) }}</span></td>
                  <td>{{ describeRule(r, nameOf) }}</td>
                  <td class="muted">{{ r.source_text }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="small muted">{{ t('instr.noRules') }}</p>
        </div>

        <div v-if="preview.free_text.length" class="stack">
          <h4 class="sub-h"><span class="chip chip--warning">{{ t('instr.freeTitle') }}</span></h4>
          <p class="small muted">{{ t('instr.freeLead') }}</p>
          <ul class="free-list">
            <li v-for="(f, i) in preview.free_text" :key="i">
              <label class="check" :for="`instr-free-${i}`">
                <input :id="`instr-free-${i}`" v-model="useFree[i]" type="checkbox" />
                <span>{{ f }}</span>
              </label>
            </li>
          </ul>
        </div>

        <div v-if="preview.rejected.length" class="stack">
          <h4 class="sub-h"><span class="chip chip--required">{{ t('instr.rejectedTitle') }}</span></h4>
          <ul class="plain-list">
            <li v-for="(r, i) in preview.rejected" :key="i">
              <span class="mono small">{{ rawText(r.raw) }}</span>
              <span class="small"> — {{ t('instr.reason') }}: {{ r.reason }}</span>
            </li>
          </ul>
        </div>

        <div class="row">
          <button type="button" class="btn btn--primary" :disabled="busy !== null" @click="doConfirm">
            <span v-if="busy === 'confirm'" class="spinner" aria-hidden="true"></span>{{ t('instr.confirm') }}
          </button>
          <button type="button" class="btn btn--quiet" :disabled="busy !== null" @click="preview = null">{{ t('common.cancel') }}</button>
        </div>
      </div>
    </template>
    <p v-else class="small muted">{{ t('instr.fixedAtStart') }}</p>
  </section>
</template>

<style scoped>
.quote {
  margin: 0;
  padding: var(--sp-2) var(--sp-3);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel-sunken);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.rule-list,
.free-list {
  list-style: none;
  display: grid;
  gap: var(--sp-2);
}
.rule-list li {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-2);
  min-width: 0;
}
.plain-list {
  display: grid;
  gap: var(--sp-1);
  padding-inline-start: 1.2em;
  overflow-wrap: anywhere;
}
.preview {
  box-shadow: var(--shadow-pop);
  gap: var(--sp-4);
}
.sub-h {
  margin: 0;
  font-size: var(--fs-md);
}
.free-list .check {
  align-items: flex-start;
}
.free-list .check input {
  margin-top: 0.3em;
}
</style>
