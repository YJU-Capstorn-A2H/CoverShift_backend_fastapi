// 화면 경로. 관리자 화면은 JWT 가 있어야 들어온다 (W-1). 직원 제출 페이지 · 로그인 · 등록은 로그인 없음.

import { createRouter, createWebHistory, type RouteLocationNormalized } from 'vue-router'
import { t, type MessageKey } from './i18n'
import { session } from './lib/session'

declare module 'vue-router' {
  interface RouteMeta {
    public?: boolean // 로그인 없이 열림
    staff?: boolean // 직원용 화면 (관리자 메뉴를 숨긴다)
    titleKey?: MessageKey
  }
}

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: { path: '/settings', query: { tab: 'periods' } } },
    { path: '/login', component: () => import('./views/LoginView.vue'), meta: { public: true, titleKey: 'title.login' } },
    { path: '/register', component: () => import('./views/RegisterView.vue'), meta: { public: true, titleKey: 'title.register' } },
    { path: '/settings', component: () => import('./views/SettingsView.vue'), meta: { titleKey: 'title.settings' } },
    { path: '/periods/:id(\\d+)', component: () => import('./views/PeriodView.vue'), meta: { titleKey: 'title.period' } },
    { path: '/line-stub', component: () => import('./views/LineStubView.vue'), meta: { titleKey: 'title.line' } },
    { path: '/line/link/:token', component: () => import('./views/LineLinkView.vue'), meta: { public: true, staff: true, titleKey: 'title.lineLink' } },
    { path: '/submit/:token', component: () => import('./views/SubmitView.vue'), meta: { public: true, staff: true, titleKey: 'title.submit' } },
    { path: '/:pathMatch(.*)*', component: () => import('./views/NotFoundView.vue'), meta: { public: true, titleKey: 'title.notFound' } },
  ],
})

router.beforeEach((to) => {
  if (!to.meta.public && !session.get()) {
    return { path: '/login', query: { next: to.fullPath } }
  }
  return true
})

export function pageTitle(route: RouteLocationNormalized): string {
  const key = route.meta.titleKey
  return key ? `CoverShift — ${t(key)}` : 'CoverShift'
}

router.afterEach((to) => {
  document.title = pageTitle(to)
})
