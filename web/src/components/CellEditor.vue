<script setup lang="ts">
// 편집 모드 칸 편집 (W-6): 시작 · 종료를 30분 선택지에서 고르거나, 그 날 근무를 지운다.
// 네이티브 <dialog> 의 showModal 을 써서 초점 가두기 · Esc 닫기를 브라우저에 맡긴다.
import { computed, nextTick, ref, watch } from 'vue'
import { t, tk } from '../i18n'
import type { Cell } from '../lib/grid'
import { endOptions, startOptions } from '../lib/time'
import TimeSelect from './TimeSelect.vue'

const props = defineProps<{
  open: boolean
  title: string
  cell: Cell | undefined
  error: string | null
}>()
const emit = defineEmits<{ apply: [start: string, end: string]; clear: []; close: [] }>()

const dlg = ref<HTMLDialogElement | null>(null)
const start = ref<string | null>(null)
const end = ref<string | null>(null)

const starts = startOptions()
const ends = computed(() => endOptions(start.value))

watch(
  () => props.open,
  async (open) => {
    await nextTick()
    const d = dlg.value
    if (!d) return
    if (open) {
      const c = props.cell
      start.value = c?.kind === 'work' ? c.start : '09:00'
      end.value = c?.kind === 'work' ? c.end : '17:00'
      if (!d.open) d.showModal()
    } else if (d.open) d.close()
  },
)

// 시작을 끝보다 뒤로 옮기면 끝을 첫 선택지로 맞춘다
watch(start, (s) => {
  if (s && end.value && !ends.value.includes(end.value)) end.value = ends.value[0] ?? null
})

function apply() {
  if (start.value && end.value) emit('apply', start.value, end.value)
}
</script>

<template>
  <dialog ref="dlg" class="dialog" aria-labelledby="cell-editor-title" @close="emit('close')">
    <form class="dialog-body" method="dialog" @submit.prevent="apply">
      <h3 id="cell-editor-title">{{ title }}</h3>
      <div class="pair">
        <TimeSelect id="cell-edit-start" :label="t('edit.start')" :options="starts" v-model="start" />
        <TimeSelect id="cell-edit-end" :label="t('edit.end')" :options="ends" v-model="end" />
      </div>
      <p v-if="error" class="field-error" role="alert">{{ tk(`edit.err.${error}`, error) }}</p>
      <div class="row row--between">
        <button type="button" class="btn btn--danger" :disabled="cell?.kind !== 'work'" @click="emit('clear')">{{ t('edit.clear') }}</button>
        <div class="row">
          <button type="button" class="btn" @click="emit('close')">{{ t('common.cancel') }}</button>
          <button type="submit" class="btn btn--primary" :disabled="!start || !end">{{ t('edit.apply') }}</button>
        </div>
      </div>
    </form>
  </dialog>
</template>

<style scoped>
.pair {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--sp-3);
}
</style>
