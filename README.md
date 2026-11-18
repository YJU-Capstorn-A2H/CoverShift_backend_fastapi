# CoverShift Backend (FastAPI)

[한국어](README.md) | [日本語](README.ja.md)

## 필요한 것

- Python 3.13 (아즈키 환경에서 확인함)
- Docker Desktop
- Git

## 처음 세팅

1. 가상환경 만들기
   - Windows: `python -m venv .venv` → `.venv\Scripts\activate`
   - Mac: `python3 -m venv .venv` → `source .venv/bin/activate`
2. 패키지 설치: `pip install -r requirements.txt`
3. 환경 변수 파일 만들기 (`.env`는 Git에 올리지 않는다)
   - Windows: `copy .env.example .env`
   - Mac: `cp .env.example .env`
   - `ANTHROPIC_API_KEY`는 각자 채운다
4. DB 실행: `docker compose up -d`
5. 스키마 적용

```
   docker compose cp db/schema_v5.sql db:/tmp/schema_v5.sql
   docker compose exec db psql -U covershift -d covershift -v ON_ERROR_STOP=1 -f /tmp/schema_v5.sql
```

6. 서버 실행: `uvicorn app.main:app --reload`
7. 확인
   - http://127.0.0.1:8000/health → `{"status":"ok"}`
   - http://127.0.0.1:8000/health/db → `{"db":"ok","result":1}`
   - http://127.0.0.1:8000/docs → API 목록

## MCP (D5): 그래프가 직원 데이터를 읽는 길

- 직원 데이터(희망 근무 등)는 그래프의 `collect`가 MCP 서버의 도구를 호출해서 읽는다. DB를 읽는 것은 MCP 서버 안에서만이다.
- 파일
  - `app/mcp/mcp_server.py` … MCP 서버. 도구는 `get_staff_availability` 하나뿐이다. 읽기만 하고, 쓰기 도구는 없다. 이름(`employees.name`)은 돌려주지 않는다.
  - `app/mcp/mcp_client.py` … 그래프에서 도구를 호출하는 부품. LLM에게 보여 주는 도구는 `LLM_TOOLS`에 넣은 것뿐이다(지금은 비어 있음).
- 도구 목록

| 도구 | 건드리는 것 | 호출하는 쪽 | LLM에게 보여 주나 |
|---|---|---|---|
| `get_staff_availability` | DB(읽기만) | 코드(`collect`) | 보여 주지 않는다 |

- 환경 변수 `STAFF_SOURCE` (`period_id`가 있을 때, 직원 데이터를 어디서 읽을지)
  - `mcp`(기본) … MCP 서버를 거친다
  - `db` … DB를 직접 읽는다 (결과는 `mcp`와 같다. `test_mcp.py`의 M5a에서 확인)
- MCP 서버에는 `DATABASE_URL`만 넘긴다. `ANTHROPIC_API_KEY` 등은 넘기지 않는다.
- `mcp` 패키지가 필요하다 (2.3.0에서 확인).
- 테스트: `test\graph` 폴더에서 `python test_mcp.py` (환경 변수 `TEST_DATABASE_URL`이 필요하다. 없으면 SKIP하고 끝난다)

## 주의

- DB 포트는 **5433**이다. (아즈키의 Windows에 PostgreSQL이 이미 5432를 쓰고 있었음)
  5432가 비어 있는 PC에서는 `docker-compose.yml`의 `ports`와 `.env`의 `DATABASE_URL`을 함께 바꿔도 된다.
- 모든 명령은 `docker-compose.yml`이 있는 폴더에서 실행한다.

## DB를 처음부터 다시 만들기 (데이터가 모두 지워진다)

```
docker compose exec db psql -U covershift -d covershift -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
```

그 뒤 5번(스키마 적용)을 다시 실행한다.
