# CoverShift MVP (11/20 시연용)

명세서 V9(아즈키, 11/12) + 빈 곳을 추론으로 채운 구현. **추론으로 정한 것은 전부 `ASSUMPTIONS.md` — 팀 채택 / 기각 대상.**
상태: 가짜 LLM 으로 끝에서 끝까지 도는 것까지 확인. 진짜 LLM · 화면과 진짜 API 연결 · Aegra 장애 경우는 아래 「미검증」.

## 폴더

| 경로 | 무엇 | 요구 번호 |
|---|---|---|
| `db/001_schema_v17.sql` | 명세 2-4 DDL 그대로 (15표) | — |
| `db/002_ext_assumed.sql` | (가정) manager_actions · line_outbox · contact_log · severity 열 · LLM 읽기 롤 | G2 G8 G14 G21 |
| `app/domain.py` | 모든 모듈이 주고받는 형 | — |
| `app/validate/` | 검증 규칙 (코드가 유일한 보증) | V-1~V-10, T-1~T-4 |
| `app/llm/` | 프롬프트 · JSON 출력 · 재시도 입력 · 가짜 LLM · 사용량 로그 | F-4~F-6, F-12~F-14, M-2 |
| `app/graph/` · `app/repo/drafts.py` | 편성 그래프 (LangGraph) | M-1, F-1~F-17 |
| `app/runner.py` · `app/mcp_*.py` | 로컬/Aegra 러너 · thread 잠금 · MCP 읽기 도구 | A-15, A-18, M-7, D-3 |
| `app/api/` · `app/repo/` · `ops/` | 입구 API · 인증 · CRUD · 운영 스크립트 | A-1~A-19 |
| `web/` | Vue 3 화면 (일본어 기본 / 한국어) | W-1~W-9 |
| `compose.yaml` · `Dockerfile` · `aegra.json` | 시연 스택 | D-1 |
| `harness/` · `tools/trace.py` · `tests/` | 하네스 (공통 규칙 · 리뷰 체크리스트 · 요구 대응표 · 시험) | — |

## 개발 (가짜 LLM)

```bash
cd mvp
docker compose -f compose.test.yaml -p mvp-test up -d --wait     # 시험 DB (메모리, 127.0.0.1:5443)
uv run pytest -q                                                  # 전체 (진짜 LLM 시험은 빠짐)
uv run python tools/trace.py                                      # 테스트 없는 요구 번호만 출력
(cd web && npm install && npm run typecheck && npm test && npm run build)
```

## 시연 스택 (Aegra + Postgres + API + 화면)

```bash
cp .env.example .env    # 값은 직접 채운다 (POSTGRES_PASSWORD · JWT_SECRET · ANTHROPIC_API_KEY …)
docker compose up -d --build --wait                         # 프로젝트 이름 mvp-demo, 127.0.0.1 에만 열림
docker compose exec api python -m scripts.demo_prepare --seed   # 승인 대기까지 미리 돌림 (D-2)
docker compose exec api python -m scripts.demo_resume_check     # 지금 상태 (재시작 뒤 복구 확인, D-3)
# 화면: http://127.0.0.1:5173
docker compose down          # 볼륨은 남는다. `down -v` 는 쓰지 않는다 (데이터 삭제)
```

진짜 LLM 1회 (비용 발생, 1주 분량): `uv run pytest tests/llm/test_real_llm.py -m llm -q -s`
→ `logs/llm_calls.jsonl` 에 시간 · 토큰 · 비용 추정이 1줄 남는다.

## 미검증 (실행 출력이 없는 것)

- 진짜 LLM (Sonnet 5.5) 호출 · fallbacks 파라미터를 실제 API 가 받는지
- 화면 ↔ 진짜 API 는 headless Chrome 으로 1회 확인 (11/13, 가짜 LLM): 로그인 → 지금 시작 → 승인 → 확정 · 콘솔 오류 0 · 400px 가로 스크롤 없음. 진짜 LLM 으로는 안 돌림
- Aegra 에서 run 도중 서버가 죽는 경우: SIGKILL 이면 thread 가 계속 Busy, SIGTERM 이면 error 로 남는다 (runtime 실측). 대책 미정
- 로그인 시도 횟수 제한 없음 · DB 연결 풀 없음 · API 오류 문구는 한국어
