# 서브에이전트 공통 규칙 (모든 구현 · 리뷰 에이전트가 먼저 읽는다)

작업 폴더: `/Users/leeseunghyuk/Dev/AIagent/shift-agent/mvp` (git 저장소 아님 — 되돌리기가 어렵다. 맡은 파일만 만진다)

## 근거 (역할이 아니라 이것으로 판단한다)
- 명세서: `spec/V9_ko.txt` (줄 번호로 인용). 다르면 2장(122~517줄)이 1장·6장보다 우선.
- 요구 번호: `REQUIREMENTS.md`. 추론으로 채운 것: `ASSUMPTIONS.md` (G 번호). 입구 API 모양: `API_CONTRACT.md`.
- 공유 형: `app/domain.py`. 설정 이름: `app/config.py`.
- 아즈키 시제품(11/07): `spec/azuki_prototype_mcp_1107.py` — 검사 함수 `check_*`(348~527줄) · 프롬프트 `make_*_text`(291~347줄) · 그래프(102~660줄).
- 측정 기록: `../experiments/2026-11-07_azuki_mix/RESULTS.md` (Sonnet 5.5 · 생각 설정 안 줌 · JSON 고정 → 1회차 필수 위반 0, 3.2분 · $0.36, n=1).

## 고치면 안 되는 파일 (메인 세션 소유)
`app/domain.py` `app/config.py` `app/db.py` `app/line_stub.py` `app/mcp_tools.py` `tests/conftest.py` `tests/test_harness.py`
`db/*.sql` `pyproject.toml` `uv.lock` `REQUIREMENTS.md` `ASSUMPTIONS.md` `API_CONTRACT.md` `harness/*` `tools/*`
바꿔야 하면 고치지 말고 보고서 「계약 변경 요청」에 쓴다. 새 패키지가 필요해도 같다 (pyproject 를 고치지 않는다).

## 금지
- `.env` 를 읽지 않는다 (어떤 명령으로도). 키 · 비밀번호 값을 출력하지 않는다.
- 진짜 LLM 을 부르지 않는다 (ANTHROPIC_API_KEY 를 쓰는 코드는 `@pytest.mark.llm` 테스트에만, 실행하지 않는다).
- 배포 · 외부 서버 · 클라우드 조작 금지. `git` 명령 불필요.
- Docker: 시험 DB `mvp-test` 를 내리거나 지우지 않는다. 5433 · 5441 포트의 기존 DB 에 접속하지 않는다. `down -v`, `volume rm` 금지.
- 맡지 않은 폴더의 파일을 만들거나 고치지 않는다. 다른 에이전트가 같은 시간에 옆 폴더를 쓰고 있다.

## 실행
- 셸에서 먼저 `unset VIRTUAL_ENV; cd /Users/leeseunghyuk/Dev/AIagent/shift-agent/mvp`
- 테스트: `uv run pytest tests/<내 폴더> -q` (시험 DB 가 안 떠 있으면: `docker compose -f compose.test.yaml -p mvp-test up -d --wait`)
- fixture `conn` / `clean_db` / `db_url` 은 `tests/conftest.py` 에 있다 (세션마다 새 DB, 테스트마다 표 비움).

## 만드는 방식
- 테스트마다 `@pytest.mark.req("<번호>")` 를 단다 (REQUIREMENTS.md 에 있는 번호만).
- 규칙 하나마다 **지키는 경우와 일부러 깨뜨리는 경우**를 둘 다 테스트한다. 깨뜨리는 쪽이 실제로 실패를 잡는지 본다.
- 명세에 없는 것을 정해야 하면: ASSUMPTIONS.md 에 있으면 그대로 따른다. 없으면 가장 보수적인 쪽으로 만들고
  코드 주석에 `(가정 신규)` + 이유를 쓰고 보고서 「새 가정 후보」에 올린다. 조용히 정하지 않는다.
- 주석은 한국어. 규칙 코드에는 근거 줄 번호(`V9_ko 468` 처럼)를 단다.
- Python 3.14 · 의존성은 pyproject 에 이미 있는 것만.

## 보고서 형식 (이 순서, 이 제목으로. 짧게)
1. 만든 · 고친 파일
2. 마지막 테스트 실행 명령과 출력 원문 (끝 15줄 그대로)
3. 요구 번호 → 테스트 이름 표
4. 계약 변경 요청 (없으면 「없음」)
5. 새 가정 후보 (없으면 「없음」) — 근거 줄 번호와 함께
6. 확인하지 못한 것 · 알려진 한계
「될 것이다」「문제없을 것이다」를 쓰지 않는다. 실행한 것과 안 한 것을 나눠 쓴다.
