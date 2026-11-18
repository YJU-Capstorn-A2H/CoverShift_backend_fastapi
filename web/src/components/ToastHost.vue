<script setup lang="ts">
// 짧은 알림 자리. 화면 읽기 프로그램이 읽도록 aria-live.
import { t } from '../i18n'
import { dismiss, toasts } from '../toast'
</script>

<template>
  <div class="toasts" role="status" aria-live="polite">
    <div v-for="x in toasts" :key="x.id" class="toast" :class="`toast--${x.tone}`">
      <span class="toast-text">{{ x.text }}</span>
      <button type="button" class="btn btn--quiet btn--sm" @click="dismiss(x.id)">{{ t('common.close') }}</button>
    </div>
  </div>
</template>

<style scoped>
.toasts {
  position: fixed;
  inset-inline-end: max(16px, env(safe-area-inset-right, 0px));
  inset-block-end: max(16px, env(safe-area-inset-bottom, 0px));
  display: grid;
  gap: var(--sp-2);
  z-index: 50;
  max-inline-size: min(24rem, calc(100vw - 32px));
  pointer-events: none;
}
.toast {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  padding: var(--sp-2) var(--sp-2) var(--sp-2) var(--sp-4);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel);
  box-shadow: var(--shadow-pop);
  pointer-events: auto;
  min-width: 0;
}
.toast-text {
  flex: 1;
  min-width: 0;
  font-weight: 600;
}
.toast--ok {
  border-color: var(--ok);
}
.toast--ok .toast-text {
  color: var(--ok);
}
.toast--error {
  border-color: var(--required);
}
.toast--error .toast-text {
  color: var(--required);
}
</style>
