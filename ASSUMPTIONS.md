# 추론으로 채운 것 (전부 (가정) — 팀 승인 대상)

기준 문서 규칙은 「기준 문서를 먼저 고친다 → 3명에게 알린다 → 코드」다 (V9_ko 441). 이 폴더는 그 순서를 거꾸로 밟았다.
아래 항목은 **코드가 먼저 정한 것**이다. 팀이 번호별로 채택 / 기각하면 기각된 것은 코드에서 고친다.
설정으로 끌 수 있는 것은 「끄는 법」 칸에 적었다.

| # | 빈 곳 (명세서 위치) | 이 폴더가 정한 것 (가정) | 끄는 법 / 바꾸는 곳 |
|---|---|---|---|
| G1 | ⑦~⑨ 관리자 API 미설계 (V9_ko 137-139) | API_CONTRACT.md 「편성 제어」: 지금 시작 / 진행 상태 / review(3값) / 초안 조회 | app/api |
| G2 | manager_actions 표 없음 (V9_ko 502, 505) | db/001: 조작 4종 + 지시문 + 틀 규칙 JSON. 승인 행만 draft_id 부분 UNIQUE | db/001_schema_recommended.sql |
| G3 | 余り의 희망 인원(want) 열 없음 (V9_ko 477) | want = 지시 틀 min_headcount, 없으면 store_time_rules.min_headcount. 기준 문서 미반영이라 **기본 꺼 둠** | ENABLE_SURPLUS |
| G4 | validation_violations 를 언제 쓰나 (V9_ko 116) | 편성안을 DB 에 저장할 때(검사 통과 시 · 상한 도달 시 · 편집 저장 시) 그 안의 위반을 같이 저장. 예외 허용은 확정 때 violation_waivers 에 1행 (allowed_by/at) | app/repo/drafts.py |
| G5 | 반휴 시각 검사 · 사원 available 검사 (V9_ko 956-964 「제안」) | 코드는 넣되 **기본 꺼 둠** | ENABLE_HALF_LEAVE_CHECK · ENABLE_REGULAR_AVAILABILITY_WARNING |
| G6 | 영업시간 08:00~22:30 을 어디서 읽나 | 그 요일 store_time_rules 의 가장 이른 시작 ~ 가장 늦은 종료 (V9_ko 228-229 주석). 폐점 시각 = 가장 늦은 종료 | app/validate |
| G7 | MCP 서버 위치 · 서빙 방식 미정 (V9_ko 1123) | 같은 저장소의 stdio MCP 서버 1개(도구 1개). 그래프 collect 가 부른다 (10/25 승혁 결정 「메인은 collect가 MCP 부르고」) | app/mcp_server.py |
| G8 | 직원 연락 contact_log 없음 (V9_ko 503) | db/002 contact_requests (001 의 contact_log 는 쓰지 않는다). 부족 칸마다 후보 1명씩 순서대로 질문(10/26 순차). LINE 미연결 직원은 후보에서 뺀다(11/19). **기본 꺼 둠** | ENABLE_CONTACT |
| G9 | MVP 규칙 중 5-1 코드표에 없는 것의 코드 | REGULAR_ABSENT · CLOSING_NOT_ALLOWED · REGULAR_DAYS_MISMATCH 신설. 余り는 기존 HEADCOUNT_EXCESS 사용 | REQUIREMENTS.md V 표 |
| G10 | min_qualified=0 인 칸도 「자격자 1명 이상」인가 (V9_ko 469: 「1명 이상」과 「필요 수는 min_qualified」가 같이 있음) | **min_qualified 를 그대로 쓴다** (0 이면 검사 안 함). 시연 데이터는 1 이상으로 넣는다 | app/validate |
| G11 | max_consecutive 의 "late" 정의 없음 (V9_ko 967) | late = 그 날 폐점 시각에 끝나는 근무. shift="any" = 출근일 연속 | app/domain.py MaxConsecutiveParams |
| G12 | min_headcount 틀의 params 없음 (5-2 표에 없음) | {count, start, end, weekday? / date?} | app/domain.py MinHeadcountParams |
| G13 | 확정 후 결원 · 확정표 수정 (V9_ko 64 「범위 밖」) | 만들지 않음 | — |
| G14 | LINE 스텁의 「로그・화면」 저장 위치 | db/002 line_outbox (dedupe_key 로 중복 발송 방지). 11/19: 발송 상태 열을 더해 「대기 → 발송기」 구조로 (G25) | db/002 |
| G15 | 무효 계정 JWT (V9_ko 56 「제안」) | 요청마다 manager_accounts.is_active 확인 (JWT 기한 60분도 같이) | app/api |
| G16 | 직원 답장 버튼의 인증 (실제 LINE 은 서명, MVP 는 스텁) | 스텁 화면에서는 contact_id + 그 직원의 line_user_id 를 같이 보낸다. `LINE_MODE=stub` 일 때만 열린다 — line 모드는 서명을 검증하는 웹훅만 (A-20) | app/api · LINE_MODE |
| G17 | 편집 저장 후 흐름 | 편집안을 새 draft 로 저장(parent = 이전 안) → 재검증 → 다시 승인 대기. 점장 요청 횟수는 세지 않음 | app/graph |
| G18 | 점장 지시 → 틀 규칙 변환을 어디서 하나 (V9_ko 446 「그래프 밖, 1회」) | 입구 API 가 LLM 을 1번 부르고(미리보기), 점장이 확인하면 manager_actions(rules_confirmed) 로 저장. 편성 시작 때 마지막 확인본을 State 에 고정 | app/api · app/llm |
| G19 | 미제출 직원 (V9_ko 955 V8 설계) | 행 없음 그대로 = 사원: 배치 가능 / 파트·알바: 불가 (v17 주석 V9_ko 308-309) | — |
| G20 | 시연 데이터 | 아즈키 staff_data.json(11/04, 2026-11 한 달) → seed. 이름은 원본의 더미 이름(Aさん 등) | app/seed.py |
| G21 | validation_violations 에 severity 열 없음 | db/001 에 열이 있다. 그래프가 판정한 값을 저장 → 화면의 필수/경고가 규칙 표 추정에 기대지 않음 | db/001, app/repo/drafts.py |
| G22 | thread_id 모양 | `period-{id}-<임의>`. DB 를 다시 만들어 id 가 겹쳐도 옛 체크포인트와 부딪히지 않게. 잠금 이름은 기간 고정 | app/api/routes_control.py |
| G23 | 스텁 답장 버튼의 line_user_id | 발송함 응답에 직원의 line_user_id 를 같이 준다 (관리자 JWT 필요한 dev 화면만) | app/repo/line.py |
| G24 | 진짜 LINE 에서 직원 ↔ LINE 계정을 어떻게 잇나 (V9_ko 157-159 는 link_token + 만료만) | **(가정 신규)** 직원이 공식 계정에 연결 토큰을 메시지로 보내면, 서명이 검증된 웹훅의 `source.userId` 를 그 직원에 연결한다. 링크는 `https://line.me/R/oaMessage/{LINE ID}/?{token}` (토큰이 미리 채워진 대화창). LINE 공식 「account linking」(linkToken · nonce)은 쓰지 않는다 — 이유: 서버가 link token 발급 · 로그인 · nonce 저장 · 리다이렉트 · 해제까지 5단계를 구현해야 하고, 우리 토큰이 이미 1회용 · 만료(7일) 있음. line 모드에서는 공식 계정 ID 가 필수 | LINE_OFFICIAL_ACCOUNT_ID · app/api/routes_line_webhook.py |
| G25 | 발송 실패 때 언제까지 · 얼마 뒤에 다시 보내나 (명세 · LINE 문서에 숫자 없음) | **(가정 신규)** 횟수가 아니라 **기한**: 대기에 들어간 뒤 6시간까지 재시도한다 (재시도 키 유효 24시간 안쪽). 간격은 30초부터 두 배, 상한 15분. 재시도하는 것 = 5xx · 429 · 시간 초과 · 연결 실패. 그 밖의 4xx(409 는 성공) · 보낼 수 없는 본문은 바로 `failed`. `failed` 는 ERROR 로그를 남기고, 되돌릴 때는 `ops/requeue_line.py` (행이 18시간보다 오래됐으면 재시도 키를 새로 만든다 — 중복 발송 가능). push 의 단계별 시간 한도 8초 | app/line_dispatcher.py · app/line_api.py · ops/requeue_line.py 의 상수 |
| G26 | 웹훅 이벤트를 받을 수 없을 때 무엇을 응답하나 | **(가정 신규)** 영구 거절(남의 연락 · 모르는 연락 · 이미 답함 · 모르는 토큰 · 이미 연결된 LINE 계정 · 읽을 수 없는 이벤트)은 로그만 남기고 200. **일시 장애**(DB 접속 실패 · 러너 오류 · 그래프가 도는 중 = BUSY)가 하나라도 있으면 503 → LINE 이 요청을 다시 보내게 한다. 같은 요청의 다른 이벤트는 그대로 처리하고, 재전송 때 이미 반영된 것은 다시 반영되지 않는다. LINE 콘솔에서 Webhook redelivery 를 켜야 재전송된다 (꺼져 있으면 그 답은 사라지고 직원이 버튼을 다시 눌러야 한다). 본문 상한 1MiB (넘으면 413) | app/api/routes_line_webhook.py |

## 모듈 에이전트가 올린 새 가정 후보 (코드에 `(가정 신규)` 주석. 팀 채택 / 기각 대상)

번호는 「모듈-번호」. 근거 줄은 각 코드 주석에 있다.

- **validate**: N1 폐점(V-4)을 사원에게도 적용(아즈키 시제품은 파트만) · N2 시드에 13:00~22:30 시간대 추가 · N3 파트 반휴 시각도 신청 범위로 봄 · N4 겹치는 min_headcount 틀은 가장 큰 수 · N5 영업시간 밖 틀 칸도 검사 · N6 틀이 깨져 있으면 검증이 오류로 멈춤 · N7 희망에 없는 유급 행은 출근일로 안 셈 · N8 배정 1건 검사는 key 에 시각을 안 넣음(시각만 옮겨도 같은 위반으로 묶임) · N9 연속 상한 위반은 구간당 1건 · N10 want 없는 칸은 余り 안 봄 · N11 RULE_VIOLATION 은 required
- **llm**: L1 형식이 틀린 행은 버리고 notes 에 남김 · L2 재시도에는 required 만 넘김(경고 제외, mix1 과 같음) · L3 재시도 위반을 「이번에 고칠 것 / 되돌리지 말 것」 두 칸으로 · L4 점장 지시가 있으면 「지시에 필요한 변경은 허용」 · L5 점장 원문(source_text)은 프롬프트에 안 실음 · L6 다른 기간 staff_id · 날짜의 틀은 거부 · L7 지시 변환이 실패하면 원문 전체를 free_text 로
- **graph**: R-a 직원 연락은 thread 당 3번, 후보는 파트만 · R-b 점장 재편성 요청 때 자동 재편성 횟수를 0으로(최악 LLM 12회) · R-c 상한에서도 0건이면 유급 행만 있는 빈 안으로 승인 화면 · R-d 저장할 수 없는 LLM 행은 「0건」으로 셈 · R-e 근무 행이 있는 날에도 유급 행을 더해 DOUBLE_BOOKING 으로 잡음 · R-f 같은 사람 같은 날 2행은 저장 때 첫 행만 · R-g 재편성·편집 기록은 안마다 1행 · R-h 확정 통지는 출근 0일 직원에게도 · R-i 위반 이력은 회차별 원본
- **api**: P1 이메일 소문자화 · P2 비밀번호 8자~72바이트 · P3 초대 코드 sha256 저장 · P4 LINE 연결 토큰 7일 · P5 연결된 직원에게 연결 토큰 재발급 금지 · P6 제출 링크 LINE 발송은 연결된 직원만 · P7 파트 유급 허용 · P8 마감은 `now() > deadline` · P10 요일 복사 실수 방지 422 · P11 러너 실패 시 run_state=error · P12 틀의 staff_id/날짜가 이 점포·이 달인지 검사 · P15 남의 연락과 없는 연락은 같은 403
- **runtime**: X-a 체크포인트는 별도 스키마 `lg_checkpoint` · X-d 체크포인트가 있는 thread 에 start 하면 AlreadyStarted · X-e 오류 상태에서는 resume 거절 · X-f 중간에 멈춘 thread 는 error 로 표시(자동 재개 안 함) · X-g Aegra wait_idle 3초 · X-h Aegra 와 앱이 같은 DB
- **web**: W-a 일본어 기본 + 한국어 전환 · W-b 시간 선택지 끝 23:30 · W-c 편집에서 유급 칸은 못 바꿈 · W-d 공휴일 색은 손으로 넣은 6일(내각부 목록 미대조) · W-e 승인 버튼은 막지 않고 경고만(판단은 서버)
