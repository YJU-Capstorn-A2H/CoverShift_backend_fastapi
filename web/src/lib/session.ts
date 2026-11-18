// JWT 보관 (W-1, M-4). 탭을 닫으면 사라지도록 sessionStorage 에 둔다 (결정 사항).
// 저장소가 막힌 브라우저(사생활 모드 등)에서도 화면이 죽지 않게 모든 접근을 try/catch 로 감싼다.

export const TOKEN_KEY = 'covershift.jwt'

export interface KeyValueStore {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
  removeItem(key: string): void
}

function defaultStore(): KeyValueStore | null {
  try {
    return typeof sessionStorage === 'undefined' ? null : sessionStorage
  } catch {
    return null
  }
}

export function createSession(store: KeyValueStore | null = defaultStore()) {
  let memory: string | null = null // 저장소가 없을 때도 이 탭 동안은 로그인 상태를 유지
  return {
    get(): string | null {
      try {
        return store?.getItem(TOKEN_KEY) ?? memory
      } catch {
        return memory
      }
    },
    set(token: string) {
      memory = token
      try {
        store?.setItem(TOKEN_KEY, token)
      } catch {
        /* 저장 실패 — memory 로 버틴다 */
      }
    },
    clear() {
      memory = null
      try {
        store?.removeItem(TOKEN_KEY)
      } catch {
        /* 무시 */
      }
    },
  }
}

export const session = createSession()

/** 오류 응답 본문 → {code, message}. 계약 모양(A-19)이 아니면 FastAPI 기본 모양(detail)도 읽어 본다. */
export function parseErrorBody(status: number, body: unknown): { code: string; message: string } {
  if (body && typeof body === 'object') {
    const err = (body as { error?: unknown }).error
    if (err && typeof err === 'object') {
      const code = (err as { code?: unknown }).code
      const message = (err as { message?: unknown }).message
      if (typeof code === 'string') return { code, message: typeof message === 'string' ? message : code }
    }
    const detail = (body as { detail?: unknown }).detail
    if (typeof detail === 'string') return { code: `HTTP_${status}`, message: detail }
    if (Array.isArray(detail)) {
      const msg = detail
        .map((d) => (d && typeof d === 'object' && typeof (d as { msg?: unknown }).msg === 'string' ? (d as { msg: string }).msg : JSON.stringify(d)))
        .join(' / ')
      return { code: `HTTP_${status}`, message: msg }
    }
  }
  return { code: `HTTP_${status}`, message: `HTTP ${status}` }
}
