// 라이트 / 다크 / 자동(시스템) 전환. 고른 값은 localStorage 에 둔다 (막혀 있어도 화면은 동작).
// 자동 = data-theme 속성을 지워 prefers-color-scheme 을 따른다 (tokens.css 참고).

import { ref } from 'vue'

export type ThemeChoice = 'light' | 'dark' | 'system'
const THEME_KEY = 'covershift.theme'

function read(): ThemeChoice {
  try {
    const v = localStorage.getItem(THEME_KEY)
    if (v === 'light' || v === 'dark') return v
  } catch {
    /* 무시 */
  }
  return 'system'
}

export const theme = ref<ThemeChoice>(read())

export function applyTheme(choice: ThemeChoice) {
  if (typeof document === 'undefined') return
  const root = document.documentElement
  if (choice === 'system') delete root.dataset.theme
  else root.dataset.theme = choice
}

export function setTheme(choice: ThemeChoice) {
  theme.value = choice
  applyTheme(choice)
  try {
    if (choice === 'system') localStorage.removeItem(THEME_KEY)
    else localStorage.setItem(THEME_KEY, choice)
  } catch {
    /* 무시 */
  }
}
