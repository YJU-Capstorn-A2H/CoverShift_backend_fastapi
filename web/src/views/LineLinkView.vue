<script setup lang="ts">
// LINE 연결 스텁 (A-13, V9_ko 157-159). 링크 = {PUBLIC_BASE_URL}/line/link/{token} (계약 추가 11/12).
// 진짜로는 LINE 로그인(LIFF 등)이 line_user_id 를 준다 — 방법은 미확인 (V9_ko 2-5). MVP 는 손으로 넣는다.
import { ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import ErrorBox from '../components/ErrorBox.vue'
import { t } from '../i18n'

const route = useRoute()
const token = String(route.params.token)
const userId = ref('')
const busy = ref(false)
const error = ref<unknown>(null)
const done = ref(false)

async function submit() {
  busy.value = true
  error.value = null
  try {
    await api.lineLink({ link_token: token, line_user_id: userId.value.trim() })
    done.value = true
  } catch (e) {
    error.value = e
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="page page--narrow">
    <h1>{{ t('lineLink.title') }}</h1>
    <div v-if="done" class="notice notice--ok" role="status">
      <p class="notice-title">{{ t('lineLink.done') }}</p>
      <p class="small">{{ t('lineLink.doneLead') }}</p>
    </div>
    <template v-else>
      <p class="muted small">{{ t('lineLink.lead') }}</p>
      <form class="stack" @submit.prevent="submit">
        <div class="field">
          <label for="line-link-uid">{{ t('lineLink.userId') }}</label>
          <input id="line-link-uid" v-model="userId" class="input mono" type="text" autocomplete="off" required />
          <p class="field-hint">{{ t('lineLink.stubHint') }}</p>
        </div>
        <ErrorBox v-if="error" :error="error" />
        <button type="submit" class="btn btn--primary btn--lg" :disabled="busy || !userId.trim()">
          <span v-if="busy" class="spinner" aria-hidden="true"></span>{{ t('lineLink.submit') }}
        </button>
      </form>
    </template>
  </div>
</template>
