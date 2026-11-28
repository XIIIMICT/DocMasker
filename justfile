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

# Sidecar резервных копий: ежедневный полный pg_dump + еженедельный drill.
# Не входит в db-stand (явный список сервисов) — поднимается отдельно.
backup-stand:
    docker compose up -d --wait pgbackup

# Внеочередной полный бекап (после правки конфигурации или при алерте).
backup-now:
    docker compose run --rm --no-deps pgbackup backup

# Репетиция восстановления последнего дампа в scratch-БД с проверкой данных.
backup-drill:
    docker compose run --rm --no-deps pgbackup drill

# Логи sidecar: причина провала pg_dump / pg_restore / off-host.
backup-logs:
    docker compose logs --no-color pgbackup

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

# Legacy host-side migration for developers (uv installs backend deps on the
# host; the working tree may differ from the deployed image). For deployments
# use `migrate-docker` below so schema and app come from the SAME pinned image.
migrate:
    uv run --directory back alembic upgrade head

# One-off migration from the exact deployed prod image (DM_BACK_IMAGE, never
# latest). Preflight prints host/port/schema and refuses an ambiguous local
# target unless ALLOW_LOCAL_DB=1; no password is printed. `-e DATABASE_URL`
# forwards an explicit URL only when set in the environment. `--no-deps`
# keeps worker/front out of the one-off container. See docs/OPERATIONS.md §2.
migrate-docker:
    uv run --no-sync --directory back python -m src.scripts.check_migration_target
    docker compose -f compose.yaml -f infra/compose.migrate.yaml --profile prod run --rm --no-deps -T -e DATABASE_URL back alembic upgrade head

# Read-only counterpart of `migrate-docker`: current revision before/after.
migrate-docker-current:
    uv run --no-sync --directory back python -m src.scripts.check_migration_target
    docker compose -f compose.yaml -f infra/compose.migrate.yaml --profile prod run --rm --no-deps -T -e DATABASE_URL back alembic current

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
