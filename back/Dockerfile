# DocMasker backend. GLiNER pulls `torch`, which ships no musl (Alpine) wheels
# for cp314, so the build uses a glibc (Debian) base image rather than the
# project's Alpine convention.
FROM ghcr.io/astral-sh/uv:python3.14-bookworm AS base
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=0 \
    UV_LINK_MODE=copy \
    UV_SYSTEM_CERTS=1

FROM base AS deps
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project \
    --allow-insecure-host pypi.org \
    --allow-insecure-host files.pythonhosted.org

FROM python:3.14-slim AS pre-prod
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    # Local HuggingFace cache inside the container; persist via a compose volume.
    HF_HOME=/app/.cache/huggingface \
    # Corporate proxies may need SSL verification disabled when downloading deps.
    UV_INSECURE_HOST=pypi.org

# Порт, на котором слушает uvicorn. Единственная точка в Dockerfile: значение
# задаёт compose.yaml из BACKEND_PORT в .env.
ARG BACKEND_PORT=8000
ENV BACKEND_PORT=${BACKEND_PORT}

RUN groupadd --system appgroup && useradd --system --gid appgroup --create-home appuser && \
    mkdir -p /app/logs /app/.cache/huggingface && \
    chown -R appuser:appgroup /app

COPY --chown=appuser:appgroup src/ src/
COPY --chown=appuser:appgroup --from=deps /app/.venv/ ./.venv/
USER appuser

FROM pre-prod AS dev
ENTRYPOINT ["sh", "-c", "exec uvicorn src.main:app --reload --host 0.0.0.0 --port \"$BACKEND_PORT\""]

FROM pre-prod AS prod
ENTRYPOINT ["sh", "-c", "exec uvicorn src.main:app --host 0.0.0.0 --port \"$BACKEND_PORT\""]
