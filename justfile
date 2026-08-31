set windows-shell := ["powershell.exe", "-Command"]
# Порты берём из корневого .env (BACKEND_PORT / FRONT_PORT) — те же, что
# уходят в compose.yaml; без них рецепты падают, а не молча используют дефолт.
set dotenv-load := true

export UV_SYSTEM_CERTS := "1"

# ── Docker (профили prod/dev) ────────────────────────────────────────────────
prod:
    docker compose --profile prod up -d

prod-build:
    docker compose --profile prod up --build -d

dev:
    docker compose --profile dev up --build --watch

down:
    docker compose down

clean:
    docker compose down --rmi all --remove-orphans

# ── Локальный запуск бэкенда на хосте (для быстрых тестов API) ────────────────
back:
    uv run --directory back -m uvicorn src.main:app --reload --port {{ env_var("BACKEND_PORT") }}

front:
    bun run --cwd front dev --hostname 127.0.0.1 --port {{ env_var("FRONT_PORT") }}

# ── Зависимости ──────────────────────────────────────────────────────────────
deps:
    uv sync --directory back
    bun install --cwd front

# ── Качество ─────────────────────────────────────────────────────────────────
lint:
    uv run --directory back ruff check --fix
    uv run --directory back ruff format
    bun run --cwd front lint
    bun run --cwd front format

typecheck:
    uv run --directory back ty check
    bun run --cwd front typecheck

test:
    uv run --directory back pytest

# Пример запуска маскирования через curl (см. docs/ARCHITECTURE.md).
mask:
    curl -s -X POST http://127.0.0.1:{{ env_var("BACKEND_PORT") }}/api/mask -H "Content-Type: application/json" -d '{\"text\":\"Иван Петров, тел +7 900 123-45-67\"}'
