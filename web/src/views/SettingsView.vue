<script setup lang="ts">
// 초기 설정 (W-2): 시간대 규칙 · 직원 · 근무 기간. 탭은 주소(?tab=)에 남겨 바로 열 수 있게 한다.
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { t, type MessageKey } from '../i18n'
import PeriodsTab from './settings/PeriodsTab.vue'
import StaffTab from './settings/StaffTab.vue'
import TimeRulesTab from './settings/TimeRulesTab.vue'

type Tab = 'rules' | 'staff' | 'periods'
const TABS: { id: Tab; label: MessageKey }[] = [
  { id: 'periods', label: 'settings.tab.periods' },
  { id: 'rules', label: 'settings.tab.rules' },
  { id: 'staff', label: 'settings.tab.staff' },
]

const route = useRoute()
const router = useRouter()
const tab = computed<Tab>(() => {
  const q = route.query.tab
  return q === 'rules' || q === 'staff' || q === 'periods' ? q : 'periods'
})

function select(id: Tab) {
  void router.replace({ query: { ...route.query, tab: id } })
}

function onKey(e: KeyboardEvent) {
  // 좌우 화살표로 탭 이동 (WAI-ARIA tabs 관례)
  if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return
  const i = TABS.findIndex((x) => x.id === tab.value)
  const next = TABS[(i + (e.key === 'ArrowRight' ? 1 : TABS.length - 1)) % TABS.length]!
  select(next.id)
  requestAnimationFrame(() => document.getElementById(`tab-${next.id}`)?.focus())
}
</script>

<template>
  <div class="page">
    <h1>{{ t('settings.title') }}</h1>
    <div class="tabs" role="tablist" :aria-label="t('settings.tabs')" @keydown="onKey">
      <button
        v-for="x in TABS"
        :id="`tab-${x.id}`"
        :key="x.id"
        type="button"
        role="tab"
        class="tab"
        :aria-selected="tab === x.id"
        :aria-controls="`panel-${x.id}`"
        :tabindex="tab === x.id ? 0 : -1"
        @click="select(x.id)"
      >
        {{ t(x.label) }}
      </button>
    </div>
    <div :id="`panel-${tab}`" role="tabpanel" :aria-labelledby="`tab-${tab}`" class="tabpanel">
      <PeriodsTab v-if="tab === 'periods'" />
      <TimeRulesTab v-else-if="tab === 'rules'" />
      <StaffTab v-else />
    </div>
  </div>
</template>

<style scoped>
.tabpanel {
  min-width: 0;
}
</style>
