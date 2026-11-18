<script setup lang="ts">
// 바깥 틀: 머리(앱 이름 · 관리자 메뉴 · 언어 · 테마) + 본문 + 알림.
// 옆 여백은 이 바깥 틀 하나에만 준다 (safe-area 포함).
import { computed, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import { api } from './api'
import ToastHost from './components/ToastHost.vue'
import { locale, setLocale, t, type Locale } from './i18n'
import { session } from './lib/session'
import { setTheme, theme, type ThemeChoice } from './theme'

const route = useRoute()
const router = useRouter()
const isAdmin = computed(() => !route.meta.public)
const storeName = ref<string | null>(null)

watch(
  isAdmin,
  async (admin) => {
    if (!admin || storeName.value) return
    try {
      storeName.value = (await api.me()).store_name
    } catch {
      storeName.value = null // 머리에 점포 이름이 없을 뿐, 화면은 계속 쓴다
    }
  },
  { immediate: true },
)

async function logout() {
  try {
    await api.logout()
  } catch {
    /* 서버 쪽 폐기는 미구현 (V9_ko 2-2). 실패해도 이 탭의 토큰은 지운다 */
  }
  session.clear()
  storeName.value = null
  await router.push('/login')
}

function onLocale(e: Event) {
  setLocale((e.target as HTMLSelectElement).value as Locale)
}
function onTheme(e: Event) {
  setTheme((e.target as HTMLSelectElement).value as ThemeChoice)
}

const periodsActive = computed(() => route.path.startsWith('/periods') || (route.path === '/settings' && route.query.tab === 'periods'))
const settingsActive = computed(() => route.path === '/settings' && route.query.tab !== 'periods')
</script>

<template>
  <div class="shell">
    <a class="skip" href="#main">{{ t('nav.skip') }}</a>
    <header class="topbar">
      <div class="brand">
        <RouterLink v-if="isAdmin" to="/" class="brand-name">{{ t('app.name') }}</RouterLink>
        <span v-else class="brand-name">{{ t('app.name') }}</span>
        <span v-if="isAdmin && storeName" class="brand-store muted small">{{ storeName }}</span>
      </div>

      <nav v-if="isAdmin" class="nav" :aria-label="t('nav.main')">
        <RouterLink :to="{ path: '/settings', query: { tab: 'periods' } }" class="nav-link" :aria-current="periodsActive ? 'page' : undefined">{{ t('nav.periods') }}</RouterLink>
        <RouterLink :to="{ path: '/settings', query: { tab: 'rules' } }" class="nav-link" :aria-current="settingsActive ? 'page' : undefined">{{ t('nav.settings') }}</RouterLink>
        <RouterLink to="/line-stub" class="nav-link" :aria-current="route.path === '/line-stub' ? 'page' : undefined">{{ t('nav.line') }}</RouterLink>
      </nav>

      <div class="prefs">
        <div class="pref">
          <label for="pref-lang" class="label-caps">{{ t('lang.label') }}</label>
          <select id="pref-lang" class="select select--sm" :value="locale" @change="onLocale">
            <option value="ja">日本語</option>
            <option value="ko">한국어</option>
          </select>
        </div>
        <div class="pref">
          <label for="pref-theme" class="label-caps">{{ t('theme.label') }}</label>
          <select id="pref-theme" class="select select--sm" :value="theme" @change="onTheme">
            <option value="system">{{ t('theme.system') }}</option>
            <option value="light">{{ t('theme.light') }}</option>
            <option value="dark">{{ t('theme.dark') }}</option>
          </select>
        </div>
        <button v-if="isAdmin" type="button" class="btn btn--sm" @click="logout">{{ t('nav.logout') }}</button>
      </div>
    </header>

    <main id="main" tabindex="-1">
      <RouterView :key="route.path" />
    </main>
    <ToastHost />
  </div>
</template>

<style scoped>
.shell {
  min-block-size: 100dvh;
  display: grid;
  grid-template-rows: auto 1fr;
  padding-left: max(16px, env(safe-area-inset-left, 0px));
  padding-right: max(16px, env(safe-area-inset-right, 0px));
  min-width: 0;
}
/* 틀 + 종이: 머리줄은 미색 틀 위, 일하는 면은 흰 종이 한 장 (선 1px · 모서리 16px · 그림자 없음) */
main {
  min-width: 0;
  margin-block-end: var(--sp-2);
  padding-inline: var(--sp-5);
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius-sheet);
}
@media (max-width: 40rem) {
  /* 휴대폰: 틀이 사라지고 종이가 곧 화면 */
  main {
    margin-block-end: 0;
    margin-inline: calc(-1 * max(16px, env(safe-area-inset-left, 0px)));
    padding-inline: max(16px, env(safe-area-inset-left, 0px));
    border: 0;
    border-radius: 0;
  }
}
main:focus {
  outline: none;
}
.skip {
  position: absolute;
  inset-inline-start: var(--sp-4);
  inset-block-start: -100px;
  padding: var(--sp-2) var(--sp-3);
  background: var(--panel);
  border: 1px solid var(--ai);
  border-radius: var(--radius);
  z-index: 10;
}
.skip:focus {
  inset-block-start: var(--sp-2);
}
.topbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-2) var(--sp-5);
  padding-block: var(--sp-3);
  min-width: 0;
}
.brand {
  display: flex;
  align-items: baseline;
  gap: var(--sp-3);
  min-width: 0;
}
.brand-name {
  font-family: var(--font-heading);
  font-weight: 700;
  font-size: var(--fs-lg);
  letter-spacing: 0.02em;
  color: var(--ink);
  text-decoration: none;
}
.brand-store {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.nav {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-1);
  min-width: 0;
}
.nav-link {
  display: inline-flex;
  align-items: center;
  min-block-size: 2.25rem;
  padding: 0 var(--sp-3);
  border-radius: var(--radius);
  color: var(--ink-soft);
  font-weight: 600;
  text-decoration: none;
}
.nav-link:hover {
  color: var(--ink);
  background: var(--panel-sunken);
}
/* 선택된 메뉴 = 종이와 같은 흰 조각 (색 강조 없이) */
.nav-link[aria-current='page'] {
  color: var(--ink);
  background: var(--panel);
  box-shadow: inset 0 0 0 1px var(--line);
}
.prefs {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-3);
  margin-inline-start: auto;
}
.pref {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}
.select--sm {
  min-block-size: 2rem;
  inline-size: auto;
  padding-block: 0;
  font-size: var(--fs-sm);
}
@media (max-width: 40rem) {
  .prefs {
    margin-inline-start: 0;
  }
  /* iOS 는 16px 미만 선택칸을 누르면 확대한다 */
  .select--sm {
    font-size: var(--fs-input);
  }
}
</style>
