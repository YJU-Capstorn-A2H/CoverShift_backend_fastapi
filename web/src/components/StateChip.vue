<script setup lang="ts">
// 편성 진행 상태 칩. running 은 작은 회전 표시 (화면 전체를 덮지 않는다).
import { computed } from 'vue'
import { tk } from '../i18n'
import type { RunState } from '../types'

const props = defineProps<{ state: RunState | null | undefined; large?: boolean }>()

const tone = computed(() => {
  switch (props.state) {
    case 'confirmed':
      return 'chip--ok'
    case 'waiting_review':
      return 'chip--accent'
    case 'waiting_contact':
      return 'chip--warning'
    case 'error':
      return 'chip--required'
    default:
      return ''
  }
})
</script>

<template>
  <span class="chip" :class="[tone, { 'chip--lg': large }]">
    <span v-if="state === 'running'" class="spinner" aria-hidden="true"></span>
    {{ state ? tk(`state.${state}`, state) : '—' }}
  </span>
</template>
