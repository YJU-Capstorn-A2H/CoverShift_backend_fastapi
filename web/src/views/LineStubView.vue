<script setup lang="ts">
// LINE 스텁 (W-8, M-5, A-17): 보내지 않고 line_outbox 를 보여 준다. 3초마다 다시 읽는다.
// 출근 확인(contact_ask)에는 [可能][不可] → POST /api/line/reply (G16: contact_id + 그 직원의 line_user_id).
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ApiError, api } from '../api'
import ErrorBox from '../components/ErrorBox.vue'
import { dateTimeLabel, t, tk } from '../i18n'
import { isContactMessage, lineUserIdOf, messageText } from '../lib/flow'
import type { Employee, OutboxMessage } from '../types'

const LINE_POLL_MS = 3000

const messages = ref<OutboxMessage[]>([])
const employees = ref<Employee[]>([])
const filter = ref<string>('')
const loadError = ref<unknown>(null)
const answered = ref<Record<number, 'yes' | 'no'>>({})
const replyError = ref<Record<number, unknown>>({})
const replying = ref<number | null>(null)
const manualUserId = ref<Record<number, string>>({})

let timer: ReturnType<typeof setTimeout> | null = null
let alive = true

async function load() {
  try {
    messages.value = await api.outbox(filter.value ? Number(filter.value) : undefined)
    loadError.value = null
  } catch (e) {
    loadError.value = e
  }
}
function loop() {
  if (!alive) return
  timer = setTimeout(async () => {
    await load()
    loop()
  }, LINE_POLL_MS)
}
onMounted(async () => {
  try {
    employees.value = await api.employees()
  } catch {
    /* 거르기 목록이 없을 뿐 */
  }
  await load()
  loop()
})
onBeforeUnmount(() => {
  alive = false
  if (timer) clearTimeout(timer)
})
watch(filter, () => void load())

async function reply(m: OutboxMessage, answer: 'yes' | 'no') {
  const contactId = m.body['contact_id']
  const userId = lineUserIdOf(m) ?? manualUserId.value[m.message_id]?.trim() ?? ''
  if (typeof contactId !== 'number' || !userId) return
  replying.value = m.message_id
  replyError.value = { ...replyError.value, [m.message_id]: null }
  try {
    await api.lineReply({ contact_id: contactId, line_user_id: userId, answer })
    answered.value = { ...answered.value, [m.message_id]: answer }
  } catch (e) {
    if (e instanceof ApiError && e.code === 'ALREADY_ANSWERED') answered.value = { ...answered.value, [m.message_id]: answer }
    replyError.value = { ...replyError.value, [m.message_id]: e }
  } finally {
    replying.value = null
  }
}

/** 글 안의 주소를 눌러 열 수 있게 나눈다 (제출 링크 · 연결 링크 확인용) */
function parts(text: string): { text: string; url: boolean }[] {
  const out: { text: string; url: boolean }[] = []
  const re = /(https?:\/\/[^\s]+)/g
  let last = 0
  for (const m of text.matchAll(re)) {
    if (m.index > last) out.push({ text: text.slice(last, m.index), url: false })
    out.push({ text: m[0], url: true })
    last = m.index + m[0].length
  }
  if (last < text.length) out.push({ text: text.slice(last), url: false })
  return out
}

function extraUrl(m: OutboxMessage): string | null {
  for (const k of ['url', 'link_url']) {
    const v = m.body[k]
    if (typeof v === 'string' && !messageText(m).includes(v)) return v
  }
  return null
}

function workDays(m: OutboxMessage): string[] {
  const w = m.body['work_days']
  if (!Array.isArray(w)) return []
  return w.map((x) => {
    if (typeof x === 'string') return x
    if (x && typeof x === 'object') {
      const o = x as Record<string, unknown>
      const d = typeof o['work_date'] === 'string' ? o['work_date'] : ''
      const s = typeof o['start_time'] === 'string' ? o['start_time'].slice(0, 5) : ''
      const e = typeof o['end_time'] === 'string' ? o['end_time'].slice(0, 5) : ''
      return [d, s && e ? `${s}–${e}` : String(o['kind'] ?? '')].filter(Boolean).join(' ')
    }
    return String(x)
  })
}
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1>{{ t('line.title') }}</h1>
      <span class="small muted row"><span class="spinner" aria-hidden="true"></span>{{ t('line.autoRefresh') }}</span>
    </div>
    <p class="muted">{{ t('line.lead') }}</p>

    <div class="field filter">
      <label for="line-filter">{{ t('line.filter') }}</label>
      <select id="line-filter" v-model="filter" class="select">
        <option value="">{{ t('line.all') }}</option>
        <option v-for="e in employees" :key="e.employee_id" :value="String(e.employee_id)">{{ e.name }}</option>
      </select>
    </div>

    <ErrorBox v-if="loadError" :error="loadError" />
    <p v-if="!messages.length && !loadError" class="muted">{{ t('line.empty') }}</p>

    <ol class="messages">
      <li v-for="m in messages" :key="m.message_id" class="msg">
        <header class="msg-head">
          <span class="msg-name">{{ m.employee_name }}</span>
          <span class="chip" :class="{ 'chip--accent': isContactMessage(m) }">{{ tk(`line.kind.${m.kind}`, m.kind) }}</span>
          <time class="small muted num" :datetime="m.created_at">{{ dateTimeLabel(m.created_at) }}</time>
        </header>
        <p class="msg-text">
          <template v-for="(p, i) in parts(messageText(m))" :key="i">
            <a v-if="p.url" :href="p.text" target="_blank" rel="noopener" class="mono">{{ p.text }}</a>
            <template v-else>{{ p.text }}</template>
          </template>
        </p>
        <p v-if="extraUrl(m)" class="small"><a :href="extraUrl(m)!" target="_blank" rel="noopener" class="mono">{{ extraUrl(m) }}</a></p>
        <div v-if="workDays(m).length" class="stack">
          <p class="label-caps">{{ t('line.workDays') }}</p>
          <ul class="workdays num small">
            <li v-for="(w, i) in workDays(m)" :key="i">{{ w }}</li>
          </ul>
        </div>

        <div v-if="isContactMessage(m)" class="reply">
          <template v-if="!lineUserIdOf(m)">
            <p class="small muted">{{ t('line.userIdMissing') }}</p>
            <div class="field">
              <label :for="`line-uid-${m.message_id}`">{{ t('line.userId') }}</label>
              <input :id="`line-uid-${m.message_id}`" v-model="manualUserId[m.message_id]" class="input mono" type="text" autocomplete="off" />
            </div>
          </template>
          <p v-if="answered[m.message_id]" class="chip chip--ok chip--lg">{{ t('line.answered', { answer: answered[m.message_id] === 'yes' ? t('line.yes') : t('line.no') }) }}</p>
          <div v-else class="row">
            <button
              type="button"
              class="btn btn--primary btn--lg"
              :disabled="replying !== null || !(lineUserIdOf(m) || manualUserId[m.message_id]?.trim())"
              @click="reply(m, 'yes')"
            >
              {{ t('line.yes') }}
            </button>
            <button
              type="button"
              class="btn btn--lg"
              :disabled="replying !== null || !(lineUserIdOf(m) || manualUserId[m.message_id]?.trim())"
              @click="reply(m, 'no')"
            >
              {{ t('line.no') }}
            </button>
          </div>
          <ErrorBox v-if="replyError[m.message_id]" :error="replyError[m.message_id]" />
        </div>
      </li>
    </ol>
  </div>
</template>

<style scoped>
.filter {
  max-inline-size: 20rem;
}
.messages {
  list-style: none;
  display: grid;
  gap: var(--sp-3);
  max-inline-size: 44rem;
}
/* 메시지 하나 = 따로 떨어진 물건 → 카드 */
.msg {
  display: grid;
  gap: var(--sp-2);
  padding: var(--sp-3) var(--sp-4);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel);
  min-width: 0;
}
.msg-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-2) var(--sp-3);
}
.msg-name {
  font-weight: 700;
}
.msg-head time {
  margin-inline-start: auto;
}
.msg-text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.workdays {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-1) var(--sp-3);
  list-style: none;
}
.reply {
  display: grid;
  gap: var(--sp-2);
  padding-top: var(--sp-2);
  border-top: 1px solid var(--line);
}
.reply .chip {
  justify-self: start;
}
</style>
