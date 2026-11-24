# TODO — DocMasker production readiness

Живой список оставшихся работ. Обновлять при каждом этапе: переносить пункты в
«Готово» и добавлять новые находки. Источник первичных наблюдений —
задание из `docs/` (было в untracked `DocMasker_production_readiness_prompt.md`,
удалено после переноса сюда), текущее состояние — `docs/PRODUCTION_READINESS.md`,
`docs/ARCHITECTURE.md`, ADR в `docs/adr/`.

Легенда статусов: `todo` · `in progress` · `done` · `deferred` · `blocked`
(внешняя настройка/решение). Ветки — task-ветки от `dev`, merge без squash.

---

## Готово (этапы 0–5)

- [x] **Этап 0** — синхронизация документации с реальным toolchain (Python 3.15,
  Granian, lock-версии), `front/AGENTS.md` + HeroUI reference.
- [x] **Этап 1** — baseline/бенчмарки, карта data-flow, живая матрица находок
  `docs/PRODUCTION_READINESS.md`, `back/benchmarks/`.
- [x] **Этап 2** — P0: подсчёт вхождений между абзацами, строгая валидация
  labels, агрегаты вместо raw PII, валидация OPC/ZIP, manifest вместо
  `X-Unused-Keys`, non-root front, loopback-порт бэка, span-overlap policy.
- [x] **Этап 3** — ADR-0001: профили/реестр детекторов, keys v2 + legacy v1,
  `GET /api/profiles`, `profile_id`, удаление `MASK_LABELS`.
- [x] **Этап 4** — ADR-0002: SQLite WAL store + spool, `/api/jobs` (202/status/
  result/cancel), threshold-redirect, worker-контейнер с тёплым пулом,
  `run_cpu` executor, bounded/streaming прокси, job UI polling.
- [x] **Этап 5** — все 6 вариантов header/footer + таблицы в них,
  `docs/DOCX_SUPPORT.md`, warn `X-Docx-Unsupported-Parts` + Tooltip,
  generation guard в `DocxViewer`, полная декомпозиция `masker-page.tsx`
  (`features/masking/`, `shared/ui/panes.tsx`, `shared/api/mask-helpers.ts`).
- [x] Побочные UI-правки: заголовок вкладки `ДокМаскер / …`, сохранение режима,
  адаптивный header (скрытие имени + Tooltip на кнопке), мобильная раскладка без
  наложений и без вертикального скроллбара.
- [x] Коммит `.mcp.json` / `opencode.json` / `.gitignore`.

---

## Этап 6 — Observability, deployment, operations `deferred`

> Отложено решением maintainer от 2026-11-23. Вернуться отдельной task-веткой.

- [x] Request ID / correlation ID в ответах и заголовке (`X-Request-ID`) —
      сделано в Phase 6. Структурированные JSON-логи без PII,
      корреляция по request_id в логах — остаётся здесь.
- [ ] Минимальные метрики: счётчики запросов/ошибок/latency, размер входа/выхода,
      сущности по label, глубина очереди, длительность jobs, concurrency воркера.
      Без Prometheus/OTel-стека, если деплой его не использует.
      *(Phase 6 добавил лишь coarse `GET /metrics`: active_jobs + cache counters;
      полный набор остаётся здесь.)*
- [x] Разделить liveness и readiness: `GET /health` (liveness) и `GET /ready`
      (проверка PostgreSQL); `/metrics` — coarse-агрегаты без PII.
      *(сделано в Phase 6; обновление compose/CI healthchecks и readiness
      фронта — всё ещё в этом отложенном этапе)*
- [ ] Грамотные startup/shutdown хуки, graceful drain воркера (SIGTERM уже есть —
      проверить на живом стеке после рестарта).
- [ ] Харднинг контейнеров: `read_only` + `tmpfs` (проверить temp-файлы
      Next/Python), `cap_drop: [ALL]`, `no-new-privileges`, `pids_limit`,
      mem/cpu limits; `back` и `worker` в internal-only сети (без host-порта
      бэка в проде). Не сломать temp-workflow.
- [ ] Reverse-proxy / TLS / auth boundary: пример конфигурации, документация
      upload limits / timeouts / rate limits; явное описание single-tenant
      private-режима ( auth пока отсутствует — не выдавать за защищённое).
- [ ] Retention/backup/cleanup, runbooks: queue outage, storage outage, stuck
      job, disk full, worker OOM, битый DOCX, восстановление после deploy.
- [ ] `Cache-Control: no-store` для всех ответов с документами/ключами
      (есть на `/api/jobs/{id}/result` — проверить остальные).
- [ ] Ротация/retention логов.

## Этап 7 — Финальная документация, CI, production verification `in progress`

> Делается этап 7 без зависимости от этапа 6 (по решению maintainer). Этап 6
> остаётся `deferred` и придёт позже.

- [x] Корневой `AGENTS.md` — короткий индекс; `back/AGENTS.md` и
      `infra/AGENTS.md` заведены; все landmines перенесены по темам
      (`front/AGENTS.md` обновлён: 3.2.8, `registered_titles`, 21 тест),
      без дублирования.
- [x] `docs/API.md` (полный HTTP-контракт), `docs/TEMPLATES.md` (формат ключей,
      профили, рецепты расширения), `docs/PERFORMANCE.md` (бенчмарки, лимиты,
      незакрытые хотспоты); поправлены устаревшие факты в `ARCHITECTURE.md`,
      `WORKFLOW.md`, `PRODUCTION_READINESS.md`, `DOCX_SUPPORT.md`.
- [x] CI: front coverage gate (65% строк, измерено 71%), `production` зависит от
      `config`+`back`+`front`, поднимает `back`+`worker`+`front`, явно
      перечисляет smoke-спеки, `--wait-timeout 180`.
- [ ] Корневой `README.md` (создаст maintainer — не трогать без запроса):
      продукт, форматы, архитектура, запуск, production requirements, безопасность,
      Known DOCX limitations.
- [ ] `docs/SECURITY.md`, `docs/OPERATIONS.md` — **зависят от этапа 6**
      (reverse-proxy/TLS/auth boundary, runbooks, retention), делать вместе с ним.
- [ ] Branch protection / ruleset + required check `CI required` — **внешняя
      настройка GitHub**, не утверждать, что включено; вести checklist
      (origin пока только `feat/demasking`, `main`/`dev` локально).
- [x] Clean install → unit → integration → e2e → Docker production smoke:
      prod images `dm-back:ci`/`dm-front:ci` built, isolated `infra/compose.ci.yaml`
      stack (`back`+`worker`+`front`, `--wait --wait-timeout 180`) booted all three
      healthy, `/health`+`/api/health` 200, **21/21 e2e** on 15001 (включая job
      202→воркер и DOCX-warning); dev-стек — 21/21 на 5001; back 174 теста /
      90%+ branch, front 53 unit / 65% gate, ruff/ty/oxlint/oxfmt/actionlint,
      оба compose config зелёные. Стек разобран (`down --volumes`), dev не тронут.
      Осталось: повторить на целевом железе + стресс job lifecycle
      (crash/restart, queue full, disk full, expired result, cancel).
- [ ] Повторно пройти исходный список замечаний раздела 3 и обновить матрицу:
      `done`, `not done`, `blocked`, `deferred` с доказательствами.

## Отдельные незакрытые технические долги (изbaseline и ТЗ)

- [ ] **F-02** `detect()` сверхлинеен на больших текстах: 360 КБ → 92.8 с /
      7.3 ГБ RSS, 91% времени — natasha `AddrExtractor` (yargy). Нужны
      окна/батчинг детекции и повторные замеры `back/benchmarks/`; порог
      sync→job сейчас уводит большие входы в воркер, но сам алгоритм не ускорен.
- [ ] **F-07** `python-docx` строит DOM в памяти (стриминг ответа и spool уже
      есть); streaming upload / OOXML streaming transformer — отдельное решение.
- [ ] **Точность (7.3)**: версионируемый regression corpus, precision/recall/F1
      по label, false positives/negatives; `score=1.0` — константа, не confidence.
- [ ] **Горячие точки (7.2)**: кэш prefix-лексикона `_suspicious_positions`,
      стоимость `SequenceMatcher`/повторной морфологии на больших текстах.
- [ ] **Стресс-тесты (12.2)**: crash/restart воркера, queue full, disk full,
      request cancellation, «почти без PII» документы, 1/2/4/N конкурентных jobs.
- [ ] **Профили (этап 3, остатки)**: frontend profile selection UI (решение —
      «без изменений UI»), пользовательские словари/исключения как данные
      профиля, пользовательские regex с лимитами/timeout при появлении спроса.
- [ ] **API-контракт (§9)**: сгенерированные TypeScript типы из OpenAPI
      (единый source-of-truth) — см. «Отложено по решению maintainer» ниже.
      Стабильный формат ошибок и единый каталог labels уже сделаны в Phase 4/6.
- [ ] **Масштабирование (6.1)**: для multi-instance понадобятся Postgres/Redis
      и приватное object storage (PostgreSQL уже координация, но spool на
      одном хосте — ADR-0003). ADR перед добавлением.
- [ ] **Auth (8.1)**: приложение без аутентификации; для публичного/межпользовательского
      деплоя — OIDC/SSO или проверенный reverse-proxy auth + защита job/result
      от IDOR, CSRF/origin для cookie-based auth.
- [ ] **TLS при сборке (8.3)**: `--allow-insecure-host` / `NODE_TLS_REJECT_UNAUTHORIZED=0`
      оставлены осознанно (как в ReportCollector); замена на доверенный CA —
      когда появится корпоративный сертификат.

---

## Implementation Brief (`docs/DocMasker_Production_Readiness_Implementation_Brief.md`, 2026-11-24)

> Новая редакция ТЗ. Фазы по §13, по одной task-ветке на фазу.

- [x] **Phase 0** — baseline/дизайн: `docs/IMPLEMENTATION_BASELINE.md`
      (карта data-flow, зависимости, 0 живых строк в старой SQLite),
      `docs/adr/0003-postgres-job-foundation.md` (выбор SQLAlchemy 2 sync +
      psycopg 3 + Alembic, PostgreSQL 18 + Valkey 8, схема `jobs`,
      SKIP LOCKED, перенос паттернов ReportCollector, план backup/migration).
- [x] **Phase 1** — PostgreSQL foundation + job schema: compose prod/dev/CI
      (postgres/valkey приватные, healthcheck-gated), Alembic `0001_jobs`,
      новый репозиторий (staging/publish, SKIP LOCKED claim + fencing token,
      lease reclaim, transitions), Settings-валидация, интеграционные тесты
      на реальном PostgreSQL (`TEST_DATABASE_URL`), SQLite удалён полностью.
- [x] **Phase 2** — durable-job correctness: staged atomic enqueue,
      `SKIP LOCKED` claim с fencing token + worker_id, независимый heartbeat
      (lease/3), cooperative cancellation (`cancelling` + checkpoints),
      классификация ошибок с bounded backoff, TTL/GC (active не expire),
      immutable fenced result publication, graceful drain; UI Cancel вместо
      кнопки файла + sessionStorage resume. Failure-injection тесты.
- [x] **Phase 3** — Valkey result cache: versioned HMAC-SHA256 cache key
      (input/keys/filename fingerprints + ruleset/config/manifest/engine
      versions), compact artifact-reference entries (large ZIP/DOCX stay in
      private storage), hash-verified hits, TTL/GC, metrics, cache-outage
      fallback, PostgreSQL-side dedupe of identical active jobs; sync + job
      paths, failed/cancelled never cached. 206 back tests incl. real-Valkey
      integration.
- [x] **Phase 4** — one masking configuration + exception files: profiles
      (`MaskingProfile/PROFILES/get_profile`, `GET /api/profiles`, `profile_id`
      everywhere) удалены; `labels.yaml` — единый каталог (без `order`) +
      `GET /api/labels` + сгенерированный TS-файл (`gen:labels`, CI freshness);
      все лексиконы (org/person/address/common) и `always_mask`/`never_mask`
      вынесены в типизированные YAML с жёсткой валидацией старта, digest
      инвалидирует кэш; CLI `validate_config`; приватный `exceptions.yaml`
      в .gitignore; legacy `Профиль` marker читается/пишется как metadata.
- [x] **Phase 5** — namespace-aware OOXML coverage + общий manifest:
      полноценный OPC-трансформер (`back/src/masking/ooxml/`) обрабатывает body/
      tables/все header-footer/footnotes/endnotes/comments/tracked deletions/
      textboxes/sdt/core+app props/alt-text/external rel targets, сохраняя
      неизменённые ZIP-записи байт-в-байт; strict 422 для embedded objects/
      unsafe XML; warning-плаumbing и X-Docx-* удалены. Единый versioned
      `manifest.json` для sync и job (`statistics`/`coverage`/`versions`),
      frontend требует и валидирует его, text-job DTO = канонический ответ.
      215 back tests incl. `test_ooxml.py` synthetic fixtures.
- [x] **Phase 6** — API contracts/timeouts/streaming/event-loop safety:
      единый формат ошибок `{code, detail, request_id}` + `X-Request-ID`
      (обработчики validation/domain/not-found/queue/storage/500, без утечки
      PII/путей/traceback), каноническая карта route→timeout
      (`front/src/shared/api/timeouts.ts`: 15 s JSON/job, 120 s upload, 300 s
      result; 20 min client job-wait), streaming multipart через Next без
      `formData()`-rebuild + `duplex:"half"`, bounded staging upload с реальным
      счётчиком байт, валидация по пути, `FileResponse` для результата,
      отдельные CPU/IO пулы (`run_cpu`/`run_blocking`), потоковый подсчёт
      абзацев, `/ready` (PostgreSQL) + `/metrics` (без PII), Pydantic
      cross-field валидация лимитов. 260 back tests / 88% branch, 63 front unit.
      **§8.2 (OpenAPI → TS-типы) перенесено в отдельный пункт ниже по решению
      maintainer.**

## Отложено по решению maintainer (2026-11-24)

- [ ] **§8.2 — генерация TypeScript API-типов из текущего OpenAPI**:
      `openapi-typescript` (или аналог), экспорт схемы из живой FastAPI-аппы в
      CI, коммит `front/src/shared/api/generated/schema.ts`, проверка свежести
      в CI, замена рукописных DTO. Решение: «поместим в TODO» — не делается в
      Phase 6, чтобы не смешивать с error-contract/streaming работой.
- [ ] **Phase 7** — performance/capacity (long-text windowing, DOCX prefilters,
      re-run benchmarks).
- [ ] **Phase 8** — final agent-doc refresh + release readiness
      (clean install, migrations, stress, CI protection checklist).

---

## Процесс

- Каждая смысловая задача — отдельная task-ветка от актуальной `dev`, merge
  `--no-ff` без squash; `dev` → `main` только squash после release approval.
- Перед merge: `just lint`, `just typecheck`, `just test`, e2e на `just dev`,
  diff-review, отсутствие секретов и debug-артефактов.
- Живые проверки — на запущенном `just dev` (BrowserMCP / встроенный браузер
  Orca / локальный Chrome через `local.playwright.config.ts`); см.
  `docs/WORKFLOW.md`.
- Не коммитить без явной команды; не удалять/не сбрасывать чужие изменения;
  спорные продуктовые решения — спрашивать у maintainer.
