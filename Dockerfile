# syntax=docker/dockerfile:1.7
# CoverShift MVP 앱 이미지 1개 — compose.yaml 의 aegra(그래프 서버) · api(입구 API) 가 같이 쓴다 (D-1, V9_ko 507).
# 버전 고정: 베이스 이미지 · uv 는 태그 고정, 파이썬 패키지는 uv.lock 그대로(--frozen). aegra-cli 0.10.8 은 pyproject 에 고정.
# 빌드 맥락에서 빼는 것(.env · .venv 등)은 Dockerfile.dockerignore.

FROM python:3.14.5-slim-trixie AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Tokyo
WORKDIR /app

# ---------------------------------------------------------------- 의존성만 먼저 (pyproject · uv.lock 이 안 바뀌면 캐시)
FROM base AS deps
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /bin/uv
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
# --frozen: uv.lock 을 고치지 않고 그대로 깐다 (lock 과 pyproject 가 어긋나면 여기서 실패한다)
RUN uv sync --frozen --no-dev --no-install-project

# ---------------------------------------------------------------- 실행 이미지
FROM base AS final
RUN groupadd --system app && useradd --system --gid app --home-dir /app app \
    && mkdir -p /app/logs && chown app:app /app/logs   # LLM 측정 기록(logs/llm_calls.jsonl) — 리뷰 A D1
COPY --from=deps /app/.venv /app/.venv
COPY aegra.json pyproject.toml ./
COPY app ./app
COPY scripts ./scripts
COPY ops ./ops
COPY seed ./seed
ENV PATH="/app/.venv/bin:$PATH" \
    AEGRA_CONFIG=/app/aegra.json
USER app
EXPOSE 2026 8010
# 기본 = 입구 API. aegra 서비스는 compose.yaml 에서 command 를 바꾼다
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8010"]
