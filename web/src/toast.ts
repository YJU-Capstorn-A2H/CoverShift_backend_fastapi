// 짧은 알림 (예: 「確定しました」). 화면 오른쪽 아래, aria-live 로 읽힌다.

import { ref } from 'vue'

export interface Toast {
  id: number
  text: string
  tone: 'ok' | 'info' | 'error'
}

export const toasts = ref<Toast[]>([])
let seq = 0

export function toast(text: string, tone: Toast['tone'] = 'ok', ms = 4000) {
  const id = ++seq
  toasts.value = [...toasts.value, { id, text, tone }]
  setTimeout(() => dismiss(id), ms)
}

export function dismiss(id: number) {
  toasts.value = toasts.value.filter((x) => x.id !== id)
}
