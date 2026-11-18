// 시작점. 글꼴은 @fontsource 로 동봉한다 (시연 PC 가 오프라인이어도 제목 글꼴이 나오게, CDN 안 씀).
import '@fontsource/zen-kaku-gothic-new/700.css'
import './styles/tokens.css'
import './styles/base.css'

import { createApp, watch } from 'vue'
import App from './App.vue'
import { onUnauthorized } from './api'
import { locale } from './i18n'
import { pageTitle, router } from './router'
import { applyTheme, theme } from './theme'

applyTheme(theme.value)
document.documentElement.lang = locale.value

// 401 → 토큰은 api.ts 가 지웠다. 로그인 화면으로 보내고, 돌아올 곳을 남긴다.
onUnauthorized(() => {
  const current = router.currentRoute.value
  if (current.path === '/login' || current.meta.public) return
  void router.push({ path: '/login', query: { next: current.fullPath, expired: '1' } })
})

// 언어를 바꾸면 탭 제목도 바꾼다
watch(locale, () => {
  document.title = pageTitle(router.currentRoute.value)
})

createApp(App).use(router).mount('#app')
