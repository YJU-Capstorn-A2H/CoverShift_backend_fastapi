# CoverShift MVP (11/20 시연용)

명세서 V9(아즈키, 11/12) + 빈 곳을 추론으로 채운 구현. **추론으로 정한 것은 전부 `ASSUMPTIONS.md` — 팀 채택 / 기각 대상.**
상태: 가짜 LLM 으로 끝에서 끝까지 도는 것까지 확인. 진짜 LLM · 화면과 진짜 API 연결 · Aegra 장애 경우는 아래 「미검증」.

## 폴더

| 경로 | 무엇 | 요구 번호 |
|---|---|---|
| `db/001_schema_recommended.sql` | DB 추천안 (11/18, 15표 · 롤 3개 · 권한 · 확정안 잠금 트리거) | G2 G21 |
| `db/002_local_ext.sql` | 추천안에 없는 로컬 mvp 표: line_outbox(발송 상태 포함) · contact_requests (2표) | G8 G14 |
| `app/domain.py` | 모든 모듈이 주고받는 형 | — |
| `app/validate/` | 검증 규칙 (코드가 유일한 보증) | V-1~V-10, T-1~T-4 |
| `app/llm/` | 프롬프트 · JSON 출력 · 재시도 입력 · 가짜 LLM · 사용량 로그 | F-4~F-6, F-12~F-14, M-2 |
| `app/graph/` · `app/repo/drafts.py` | 편성 그래프 (LangGraph) | M-1, F-1~F-17 |
| `app/runner.py` · `app/mcp_*.py` | 로컬/Aegra 러너 · thread 잠금 · MCP 읽기 도구 | A-15, A-18, M-7, D-3 |
| `app/api/` · `app/repo/` · `ops/` | 입구 API · 인증 · CRUD · LINE 웹훅 · 운영 스크립트 | A-1~A-20, A-22 |
| `app/line_outbox.py` · `app/line_dispatcher.py` · `app/line_api.py` | LINE 발송: 발송함에 적기 / 발송기 / LINE 과의 HTTP · 메시지 모양 · 서명 | A-17, A-20~A-22 |
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

## 시연 스택 (Aegra + Postgres + API + LINE 발송기 + 화면)

```bash
cp .env.example .env    # 값은 직접 채운다 (POSTGRES_PASSWORD · JWT_SECRET · ANTHROPIC_API_KEY …)
docker compose up -d --build --wait                         # 프로젝트 이름 mvp-demo, 127.0.0.1 에만 열림
printf '%s' "$PW" | docker compose -p mvp-demo exec -T api python -m scripts.demo_prepare --seed --email m@example.com --password-stdin   # 빈 DB 에 더미 데이터 + 승인 대기까지 (D-2). 비밀번호는 stdin 으로만
docker compose exec api python -m scripts.demo_resume_check     # 지금 상태 (재시작 뒤 복구 확인, D-3)
# 화면: http://127.0.0.1:5173
docker compose down          # 볼륨은 남는다. `down -v` 는 쓰지 않는다 (데이터 삭제)
```

`db/002_local_ext.sql` 은 볼륨이 비어 있을 때만 적용된다. 11/19 에 `line_outbox` 정의가 바뀌었으므로, 그 전에 만든 볼륨이 있으면 이 표는 옛 모양 그대로다 (발송기 · 발송함 조회가 실패한다). 옮기는 SQL 은 만들지 않았다.

## LINE (기본 = 스텁)

- API · 그래프는 `line_outbox` 에 「대기」로 적기만 한다. 보내는 것은 발송기다: compose 의 `line-dispatcher`, 로컬은 `uv run python -m app.line_dispatcher`. 발송기가 안 떠 있으면 발송함의 행은 `pending` 으로 남는다.
- `LINE_MODE=stub` (기본): 발송기가 LINE 을 부르지 않고 로그에 찍은 뒤 `sent` 로 표시한다. 시크릿이 필요 없다. 스텁 화면의 연결 · 답장 버튼이 동작한다.
- `LINE_MODE=line`: 진짜 push. `LINE_CHANNEL_SECRET` · `LINE_CHANNEL_ACCESS_TOKEN` · `LINE_OFFICIAL_ACCOUNT_ID` 가 하나라도 비면 api · 발송기가 시작하지 않는다 (aegra 는 그래프 파일을 읽다가 같은 오류를 낸다). 본인 확인은 웹훅 `POST /api/line/webhook` (서명 검증)만 하고, 스텁 화면의 연결 · 답장 버튼(`/api/line/link` · `/api/line/reply`)은 404 가 된다.
- `LINE_OFFICIAL_ACCOUNT_ID` (공식 계정의 LINE ID, `@…`): 연결 링크가 `https://line.me/R/oaMessage/…` 모양이 된다 (G24). line 모드에서는 필수.
- 발송 실패: 5xx · 429 · 시간 초과는 6시간까지 다시 보내고(간격 30초 ~ 15분), 그래도 안 되면 `failed` + ERROR 로그로 끝난다 (G25). 되돌리기:
  `uv run python -m ops.requeue_line` (failed 목록만 보여 준다) → `uv run python -m ops.requeue_line --apply` (대기로 돌린다. `--message-id N` 으로 고를 수 있다).
  나중에 LINE 을 연결한 직원 앞 메시지(`no_recipient`)는 `--status no_recipient --apply`. 18시간보다 오래된 행은 재시도 키가 새로 만들어져 중복으로 나갈 수 있다(경고가 찍힌다).
- 웹훅을 외부에 열 때는 프록시에서 **`/api/line/webhook` 경로만** 통과시킨다. stub 모드의 `/api/line/link` · `/api/line/reply` (본문의 line_user_id 를 그대로 믿는다)나 관리자 API 가 같이 열리면 안 된다.
- 웹훅은 일시 장애(DB · 러너 · 그래프가 도는 중)에 503 을 돌려준다. LINE 콘솔에서 Webhook redelivery 를 켜야 LINE 이 다시 보낸다 (G26).
- 직원 연락(`ENABLE_CONTACT`)은 LINE 이 연결된 직원에게만 묻는다. 보낸 질문이 `failed` 로 끝나거나 직원이 답하지 않으면 그래프는 계속 기다린다 (기한 처리 없음).
- 진짜 LINE 서버로는 아직 한 번도 보내 보지 않았다 (아래 「미검증」).

진짜 LLM 1회 (비용 발생, 1주 분량): `uv run pytest tests/llm/test_real_llm.py -m llm -q -s`
→ `logs/llm_calls.jsonl` 에 시간 · 토큰 · 비용 추정이 1줄 남는다.

## 미검증 (실행 출력이 없는 것)

- 진짜 LLM (Sonnet 5.5) 호출 · fallbacks 파라미터를 실제 API 가 받는지
- 화면 ↔ 진짜 API 는 headless Chrome 으로 1회 확인 (11/13, 가짜 LLM): 로그인 → 지금 시작 → 승인 → 확정 · 콘솔 오류 0 · 400px 가로 스크롤 없음. 진짜 LLM 으로는 안 돌림
- Aegra 에서 run 도중 서버가 죽는 경우: SIGKILL 이면 thread 가 계속 Busy, SIGTERM 이면 error 로 남는다 (runtime 실측). 대책 미정
- `LINE_MODE=line`: 진짜 LINE 채널이 없어 push · 웹훅을 실제 LINE 과 주고받아 본 적이 없다 (가짜 서버 · 가짜 서명 시험만). 버튼 메시지(postback action)의 필드 · 길이 제한은 LINE 문서와 대조하지 못했다 (`app/line_api.py` 의 `(미확인: …)`)
- compose 의 `line-dispatcher` 서비스는 띄워 보지 않았다 (`docker compose config` 로 정의만 확인)
- Aegra 가 `app/graph/aegra_entry.py` 를 읽을 때 설정 오류(ConfigError)가 나면 서버가 어떻게 되는지(시작 실패인지 그래프만 빠지는지)는 실제 Aegra 로 확인하지 않았다 (파일을 다시 import 하는 시험만)
- 로그인 시도 횟수 제한 없음 · DB 연결 풀 없음 · API 오류 문구는 한국어
