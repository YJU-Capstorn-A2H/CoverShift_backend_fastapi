<script setup lang="ts">
// 초대 코드로 점장 등록 (W-1, M-4, A-1). 코드 오류는 이유 구분 없이 같은 400 INVALID_CODE.
import { ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { api } from '../api'
import ErrorBox from '../components/ErrorBox.vue'
import { t } from '../i18n'

const router = useRouter()
const code = ref('')
const email = ref('')
const password = ref('')
const busy = ref(false)
const error = ref<unknown>(null)

async function submit() {
  busy.value = true
  error.value = null
  try {
    await api.register({ invite_code: code.value.trim(), email: email.value.trim(), password: password.value })
    password.value = ''
    await router.replace({ path: '/login', query: { registered: '1' } })
  } catch (e) {
    error.value = e
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="page page--narrow">
    <h1>{{ t('register.title') }}</h1>
    <p class="muted small">{{ t('register.lead') }}</p>
    <form class="stack" @submit.prevent="submit">
      <div class="field">
        <label for="reg-code">{{ t('register.code') }}</label>
        <input id="reg-code" v-model="code" class="input mono" type="text" autocomplete="one-time-code" required />
      </div>
      <div class="field">
        <label for="reg-email">{{ t('login.email') }}</label>
        <input id="reg-email" v-model="email" class="input" type="email" autocomplete="username" required />
      </div>
      <div class="field">
        <label for="reg-password">{{ t('login.password') }}</label>
        <input id="reg-password" v-model="password" class="input" type="password" autocomplete="new-password" required />
      </div>
      <ErrorBox v-if="error" :error="error" />
      <button type="submit" class="btn btn--primary btn--lg" :disabled="busy">
        <span v-if="busy" class="spinner" aria-hidden="true"></span>{{ t('register.submit') }}
      </button>
    </form>
    <p class="small"><RouterLink to="/login">{{ t('register.toLogin') }}</RouterLink></p>
  </div>
</template>
