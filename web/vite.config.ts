// CoverShift 화면 개발 서버 · 빌드 · 단위 테스트 설정.
// 계약(API_CONTRACT.md 「공통」): 화면 127.0.0.1:5173 → /api 를 127.0.0.1:8010 으로 넘긴다.
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

// 기본은 계약대로 8010. 화면만 따로 확인할 때(가짜 API 등)는 COVERSHIFT_API_URL 로 바꾼다.
const apiTarget = process.env['COVERSHIFT_API_URL'] ?? 'http://127.0.0.1:8010'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: apiTarget, changeOrigin: false },
    },
  },
  preview: {
    host: '127.0.0.1',
    port: 4173,
    proxy: {
      '/api': { target: apiTarget, changeOrigin: false },
    },
  },
  test: {
    // 순수 함수만 시험한다 (DOM 필요 없음)
    include: ['src/**/*.test.ts'],
    environment: 'node',
  },
})
