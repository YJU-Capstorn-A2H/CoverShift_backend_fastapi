<script setup lang="ts">
// 오류 표시: 무엇이 잘못됐고 어떻게 고치는지. API 의 error.message 를 그대로 보여 준다 (결정 사항).
// 계약상 message 는 한국어라서(API_CONTRACT.md 「공통」), 코드별 안내(i18n)를 위에 같이 둔다.
import { computed } from 'vue'
import { ApiError } from '../api'
import { hasKey, t, tk } from '../i18n'

const props = defineProps<{ error: unknown }>()

const info = computed(() => {
  const e = props.error
  if (e instanceof ApiError) {
    if (e.code === 'NETWORK') return { hint: t('err.NETWORK'), message: '', code: e.code }
    const hintKey = `err.hint.${e.code}`
    return { hint: hasKey(hintKey) ? tk(hintKey, '') : '', message: e.message, code: e.code }
  }
  if (e instanceof Error) return { hint: '', message: e.message, code: '' }
  return { hint: '', message: String(e), code: '' }
})
</script>

<template>
  <div class="notice notice--required" role="alert">
    <p class="notice-title">{{ info.hint || t('err.title') }}</p>
    <p v-if="info.message && info.message !== info.hint">{{ info.message }}</p>
    <p v-if="info.code" class="small muted">{{ t('err.code') }}: <span class="mono">{{ info.code }}</span></p>
  </div>
</template>
