<script setup lang="ts">
// 점장 로그인 (W-1, M-4, A-2). 성공하면 JWT 를 sessionStorage 에 두고 원래 가려던 곳으로.
import { computed, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import ErrorBox from '../components/ErrorBox.vue'
import { t } from '../i18n'
import { session } from '../lib/session'

const route = useRoute()
const router = useRouter()
const email = ref('')
const password = ref('')
const busy = ref(false)
const error = ref<unknown>(null)

const registered = computed(() => route.query.registered === '1')
const expired = computed(() => route.query.expired === '1')

function nextPath(): string {
  const n = route.query.next
  // 바깥 주소로는 보내지 않는다
  return typeof n === 'string' && n.startsWith('/') && !n.startsWith('//') ? n : '/'
}

async function submit() {
  busy.value = true
  error.value = null
  try {
    const r = await api.login({ email: email.value.trim(), password: password.value })
    session.set(r.access_token)
    password.value = ''
    await router.replace(nextPath())
  } catch (e) {
    error.value = e
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="page page--narrow">
    <h1>{{ t('login.title') }}</h1>
    <p v-if="registered" class="notice notice--ok" role="status">{{ t('login.registered') }}</p>
    <p v-if="expired" class="notice notice--warning" role="status">{{ t('login.expired') }}</p>
    <form class="stack" @submit.prevent="submit">
      <div class="field">
        <label for="login-email">{{ t('login.email') }}</label>
        <input id="login-email" v-model="email" class="input" type="email" autocomplete="username" required />
      </div>
      <div class="field">
        <label for="login-password">{{ t('login.password') }}</label>
        <input id="login-password" v-model="password" class="input" type="password" autocomplete="current-password" required />
      </div>
      <ErrorBox v-if="error" :error="error" />
      <button type="submit" class="btn btn--primary btn--lg" :disabled="busy">
        <span v-if="busy" class="spinner" aria-hidden="true"></span>{{ t('login.submit') }}
      </button>
    </form>
    <p class="small"><RouterLink to="/register">{{ t('login.toRegister') }}</RouterLink></p>
  </div>
</template>
