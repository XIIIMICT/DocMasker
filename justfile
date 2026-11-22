set dotenv-load := true

[windows]
set shell := ["powershell.exe", "-Command"]

export UV_SYSTEM_CERTS := "1"
export NODE_OPTIONS := "--use-system-ca"

# Запуск бэкенда и фронтенда в prod-режиме (только сервисы приложения)
prod:
    docker compose --profile prod up -d back front

# То же самое, но с пересборкой
prod-build:
    docker compose --profile prod up --build -d back front

# Запуск бэкенда и фронтенда в dev-режиме (всегда с пересборкой)
dev:
    docker compose --profile dev up --build --watch back-dev front-dev

# Установка / обновление зависимостей (uv, bun)
deps:
    uv sync --directory back
    bun install --cwd front

# Линтинг проекта (ruff)
lint:
    uv run --directory back ruff check --fix
    uv run --directory back ruff format
    bun run --cwd front lint
    bun run --cwd front format

# Проверка типов (ty, bun check)
typecheck:
    uv run --directory back ty check --fix
    bun run --cwd front typecheck

# Тестирование (требует just db-stand, тесты используют TEST_DATABASE_URL)
test:
    uv run --directory back pytest
    bun run --cwd front test

# Интеграционное тестирование (требует just prod и заполненную БД).
e2e:
    bun run --cwd front test:e2e --project=smoke

# # Локальный запуск на хосте бэкенда и фронтенда
# # (пока не было необходимости)
# back:
#     uv run --directory back granian --interface asgi --reload --reload-ignore-dirs logs src.main:app --port {{ env_var("BACKEND_PORT") }}

# front:
#     bun run --cwd front dev --hostname 127.0.0.1 --port {{ env_var("FRONT_PORT") }}
