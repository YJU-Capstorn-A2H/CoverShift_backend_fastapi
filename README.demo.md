# CoverShift 데모 안내 (11/20 중간 발표용)

[한국어](README.demo.md) | [日本語](README.demo.ja.md)

드럭스토어의 월간 시프트를 AI가 「제안」하고, 코드가 「검사」하고, 점장이 「확정」하는 데모입니다.
**AI는 제안만 합니다. 최종 확정은 사람(점장)이 화면에서 합니다.**

> 이 브랜치(`feature/add-seunghyeok-cde`)는 아즈키의 그래프・DB・API와 승혁의 서버・화면・검사를 하나로 합친 것입니다. 아직 `develop`에는 들어가지 않았습니다.

## 1. 무엇이 움직이는가

```mermaid
flowchart LR
  W[화면 Vue<br/>127.0.0.1:5173] -->|/api| A[입구 API FastAPI<br/>127.0.0.1:8010]
  A --> R[실행 담당 LocalRunner]
  R -->|GRAPH_IMPL=seung 기본| GS[승혁의 그래프<br/>app/graph_seung]
  R -->|GRAPH_IMPL=azuki| B[다리 app/graph_azuki_bridge.py]
  B --> GA[아즈키의 그래프<br/>app/graph]
  GS --> D[(PostgreSQL)]
  GA --> D
  GS -.->|LLM_MODE=fake / anthropic| L[AI]
  GA -.->|Claude| L
```

흐름: 희망 제출 → AI가 안을 만듦 → 코드가 검사(안 고쳐지면 다시 만듦, 최대 3회) → 사람이 부족하면 담당자에게 문의 → 점장이 확인・승인 → 확정 → 직원에게 알림(LINE은 **스텁** = 실제로는 보내지 않고 화면에 표시).

## 2. 전환 스위치

| 환경 변수 | 값 | 뜻 |
|---|---|---|
| `GRAPH_IMPL` | `seung`(기본) | 승혁의 그래프를 사용 |
| | `azuki` | 아즈키의 그래프를 사용(다리가 화면・DB와 모양을 맞춤) |
| `LLM_MODE` | `fake` | 가짜 AI(빠름. 돈이 들지 않음). 승혁의 그래프용 |
| `ENABLE_CONTACT` | `1` | 인원 부족이 남으면 담당자에게 문의(승혁의 그래프용. 아즈키의 그래프는 항상 켜짐) |

## 3. 필요한 것

- Python 3.13, Node 24 이상, Docker Desktop, Git
- Windows에서는 명령 앞에 `$env:PYTHONUTF8="1"` (글자 깨짐・읽기 오류 방지)
- 아즈키의 그래프로 진짜 Claude를 쓸 때만 `.env`에 `ANTHROPIC_API_KEY` (**Git에 올리지 않기・채팅에 붙이지 않기**)

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
cd web
npm install
```

## 4. 실행 (터미널 3개)

### ① DB 준비 (처음 1번. 다시 하고 싶을 때도 이것)

```
docker compose -f compose.test.yaml -p mvp-test up -d --wait
$env:PYTHONUTF8="1"; python -m scripts.screen_dev_setup
```

- 평소 DB(5433)는 건드리지 않습니다. 시험용 Postgres(5443)에 `demo`라는 DB를 새로 만듭니다.
- **이 DB는 메모리 위에 있습니다. Docker를 끄면 사라집니다** (데모용 더미 데이터라 괜찮습니다).
- 확정된 기간은 다시 시작할 수 없습니다. 다시 시험하려면 이 순서를 다시 하세요.

### ② API (터미널 2)

**A. 가짜 AI + 승혁의 그래프 (빠름. 화면 조작 확인용)**

```
$env:PYTHONUTF8="1"; $env:DATABASE_URL="postgresql://mvp:mvp@127.0.0.1:5443/demo"; $env:JWT_SECRET="screen-only-secret-0123456789abcdefghij"; $env:DEV_SKIP_AUTH="false"; $env:RUNNER="local"; $env:MCP_MODE="inprocess"; $env:LLM_MODE="fake"; $env:FAKE_LLM_SCENARIO="greedy"; $env:GRAPH_IMPL="seung"; $env:ENABLE_CONTACT="1"
python -m uvicorn app.api_seung.main:app --port 8010
```

**B. 아즈키의 그래프 + 진짜 Claude (약 7분 걸림)**

```
$env:PYTHONUTF8="1"; $env:DATABASE_URL="postgresql://mvp:mvp@127.0.0.1:5443/demo"; $env:JWT_SECRET="screen-only-secret-0123456789abcdefghij"; $env:DEV_SKIP_AUTH="false"; $env:RUNNER="local"; $env:MCP_MODE="inprocess"; $env:LLM_MODE="fake"; $env:GRAPH_IMPL="azuki"
python -m uvicorn app.api_seung.main:app --port 8010
```

`JWT_SECRET`은 이 데모 전용의 일회용 값입니다(실서비스에서는 쓰지 않습니다).

> `LLM_MODE`는 **승혁의 그래프용** 스위치입니다. 아즈키의 그래프(`GRAPH_IMPL=azuki`)는 이 값과 상관없이 **항상 진짜 Claude**를 호출합니다(`app/graph/node/llm.py`).

### ③ 화면 (터미널 3)

```
cd web
npm run dev
```

브라우저에서 http://127.0.0.1:5173 을 열고 로그인합니다.

- 이메일: `demo@example.com`
- 비밀번호: `demo-password-1` (데모용 더미)

화면 오른쪽 위의 「言語」에서 표시 언어를 바꿀 수 있습니다.

## 5. 화면 조작 (A의 경우)

| 순서 | 조작 | 볼 것 |
|---|---|---|
| 1 | 「シフト期間」(시프트 기간)에서 2026년 11월을 열고 「今すぐ開始」(지금 시작) | 몇 초 뒤 문의 대기가 된다 |
| 2 | 「LINE送信箱（スタブ）」(LINE 발신함) 열기 | 「勤務できますか?」(근무할 수 있나요?)와 답장 버튼이 나온다 |
| 3 | 「不可」(불가)를 누른다 | 다음 사람에게 문의(최대 3명) |
| 4 | 다음 문의에서 「可能」(가능)을 누른다 | 승인 대기로 넘어간다 |
| 5 | 「AIへの指示」(AI에게 지시)를 쓰고 「再編成を依頼する」(재편성 요청) | 안이 새로워지고 남은 횟수가 줄어든다 |
| 6 | 「編集モードにする」(편집 모드)로 1칸을 고치고 저장 | 다시 검사된다 |
| 7 | 「承認して確定する」(승인하고 확정) | 확정됨. LINE 발신함에 「シフトが確定しました」(시프트가 확정되었습니다)가 늘어선다 |

경고(인원 부족 등)는 **확인만 하며 확정을 막지 않습니다**. 「필수」 위반(자격자 없음・중복 배치)이 남아 있으면 승인할 수 없습니다.

## 6. 테스트

시험용 DB를 띄운 상태에서 실행합니다.

```
$env:PYTHONUTF8="1"; python -m pytest tests/api tests/graph tests/test_e2e.py tests/test_e2e_azuki.py tests/test_review_fixes.py -q -p no:warnings
```

- 11/19 기준 결과: **104건 성공, 1건 실패**. 실패 1건(`test_aegra_entry_graph_runs_async_with_injected_checkpointer`)은 Windows의 비동기 방식(ProactorEventLoop) 때문입니다. 코드 결함이 아닙니다.
- `tests/test_e2e_azuki.py`: 아즈키의 그래프를 API 경유로 통과시키는 테스트(승인~확정, 문의 답장). 가짜 AI를 씁니다.
- `tests/test_real_llm_azuki.py`: 진짜 Claude로 1번 통과시키는 테스트. **돈이 드니** `RUN_REAL_LLM=1`일 때만 움직입니다.

```
$env:RUN_REAL_LLM="1"; python -m pytest tests/test_real_llm_azuki.py -q -s -p no:warnings
```

## 7. 실측과 알려진 제약

**진짜 Claude 실측 (아즈키의 그래프, 11/19, 1회분)**

| 회 | 초 | 출력 토큰 | 검사 결과 |
|---|---|---|---|
| 1회째 | 206 | 33,572 | 인원 문제 5건 → 다시 만듦 |
| 2회째 | 166 | 27,089 | 문제 1건 → 다시 만듦 |
| 3회째 | 56 | 11,521 | 유효 → 문의로 |

- 합계 약 7분. 시간은 AI가 출력하는 양에 거의 비례했습니다(약 160토큰/초). 출력의 약 절반은 AI가 「생각하는」 부분이었습니다.
- 진짜 Claude는 실행할 때마다 결과가 달라집니다.

**화면 통과 확인 (아즈키의 그래프 + 진짜 Claude, 11/19)**

- 「今すぐ開始」(지금 시작)부터 승인 대기까지 약 6분 반. 승인하여 확정하고, LINE 발신함(스텁)에 일본어 확정 알림이 나오는 것을 확인했습니다.
- 이때의 안은 자동 재편성 3회로 상한에 도달했고, 필수 위반 0건・경고 2건이었습니다(실행할 때마다 결과는 달라집니다).

**알려진 제약**

- 7분의 대기 시간은 데모 중에 기다릴 수 없습니다. 발표 전에 1번 돌려 두기를 권합니다.
- 아즈키의 그래프(B)에서는 화면의 「AIのメモ」(AI 메모)와 「人数が足りない時間帯（AIの報告）」(인원이 부족한 시간대(AI 보고))가 비어 있습니다. AI 답변의 부기를 그래프가 State에 남기지 않기 때문입니다.
- DB는 승혁의 `db/001`・`db/002`를 전제로 합니다(`line_outbox`, `contact_log`의 모양 등). 리더의 DB안(2026-11-18)과의 정합은 아직 맞추지 못했습니다.
- LINE은 **스텁**입니다. 실제 LINE에는 보내지 않습니다.
- 화면에 나오는 문장은 일본어로 바꿨습니다. 로그(개발자용)에는 한국어가 남아 있습니다.

## 8. 주요 파일

| 위치 | 내용 |
|---|---|
| `app/graph/` | 아즈키의 그래프(LangGraph). **이 안은 바꾸지 않았습니다** |
| `app/graph_seung/` | 승혁의 그래프(안전망으로 남김) |
| `app/graph_azuki_bridge.py` | 다리. 아즈키의 그래프를 화면・DB・실행 담당의 모양에 맞춤 |
| `app/graph_azuki_convert.py` | 다리의 값 변환(행의 모양, 답장의 모양) |
| `app/api_seung/` | 승혁의 입구 API(화면이 연결되는 곳) |
| `app/validate/` | 승혁의 검사(화면에 내는 위반 목록을 만듦) |
| `scripts/screen_dev_setup.py` | 화면용 DB를 다시 만듦 |
| `web/` | 화면(Vue) |
| `tests/` | 테스트 일체 |
