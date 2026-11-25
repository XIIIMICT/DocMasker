set dotenv-load := true

[windows]
set shell := ["powershell.exe", "-Command"]

export UV_SYSTEM_CERTS := "1"
export NODE_OPTIONS := "--use-system-ca"

# Запуск prod-стека: бэкенд, воркер заданий и фронтенд
prod:
    docker compose --profile prod up -d back worker front

# То же самое, но с пересборкой
prod-build:
    docker compose --profile prod up --build -d back worker front

# dev-стек (всегда с пересборкой, compose --watch): + воркер заданий
dev:
    docker compose --profile dev up --build --watch back-dev worker-dev front-dev

# Поднять отдельный стенд данных (PostgreSQL + Valkey) — как в ReportCollector.
# Приложение НЕ поднимает их само: just dev / just prod работают с уже
# существующим PostgreSQL/Valkey. Перед per-test БД применяем just seed.
db-stand:
    docker compose up -d --wait postgres valkey

# Только Alloy — логи идут во внешний/центральный Loki (LOKI_URL из .env)
monitoring:
    docker compose up -d alloy

# Полный локальный стек: Loki, Grafana, Alloy, Prometheus, Alertmanager, SMTP-релей
monitoring-stand:
    docker compose up -d loki grafana alloy prometheus alertmanager smtp-relay

# Локальная проверка дрейфа общих конфигов между тремя стеками и DWH
drift-check:
    python ../infra/check-drift.py

# Создать/проверить схему PostgreSQL (Alembic) и засидить минимальные данные.
# --host нужен при запуске с хоста, когда DATABASE_URL указывает на сервис
# compose `postgres`; внутри контейнера хост подставляется compose.
seed:
    uv run --directory back -m src.scripts.seed_database

# Применить миграции Alembic (явный шаг деплоя/CI, не гонка при старте).
migrate:
    uv run --directory back alembic upgrade head

# Новая ревизия Alembic: just revision "описание"
revision message:
    uv run --directory back alembic revision --autogenerate -m "{{message}}"

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

# Тестирование (детерминированное, офлайн — без сети и моделей).
# Интеграционные тесты PostgreSQL требуют beforehand: just db-stand && just seed,
# затем TEST_DATABASE_URL (см. docs/WORKFLOW.md).
test:
    uv run --directory back pytest
    bun run --cwd front test

# Интеграционное e2e-тестирование (требует запущенного стека: just prod или just dev,
# затем E2E_BASE_URL=http://127.0.0.1:5001 — см. docs/WORKFLOW.md).
e2e:
    bun run --cwd front test:e2e --project=smoke

# # Локальный запуск на хосте бэкенда и фронтенда
# # (пока не было необходимости)
# back:
#     uv run --directory back granian --interface asgi --reload --reload-ignore-dirs logs src.main:app --port {{ env_var("BACKEND_PORT") }}

# front:
#     bun run --cwd front dev --hostname 127.0.0.1 --port {{ env_var("FRONT_PORT") }}
