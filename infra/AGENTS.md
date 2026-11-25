# infra/ — deployment agent guide

Compose, ports, volumes and CI invariants — a level-2 guide. Root `AGENTS.md`
is the entry point; durable detail lives in `docs/ARCHITECTURE.md`,
`docs/WORKFLOW.md`, `docs/OPERATIONS.md`, `docs/SECURITY.md`, `docs/TESTING.md`.

## Compose layout

- `compose.yaml` (name `docmasker`) has profiles `prod` and `dev` for the
  **application only**: `back`, `worker`, `front` / `back-dev`, `worker-dev`,
  `front-dev` (dev built with `develop.watch` syncing `back/src` and `front/src`).
- PostgreSQL and Valkey are **not** part of those profiles and are not pulled
  in by `depends_on`. They live in `infra/compose.db.yaml` (included from
  `compose.yaml`) and are started explicitly with `just db-stand` — the same
  pattern as ReportCollector. Backend `DATABASE_URL`/`VALKEY_URL` point to the
  compose service names on the private networks; seed/migrate from the host
  reach PostgreSQL via loopback `${POSTGRES_PORT:-15433}`.
- `just dev` / `just prod` therefore require a running stand:
  `just db-stand` then `just seed` (applies Alembic `0001_jobs` and verifies
  the `jobs` table) before starting the app. `just migrate` applies migrations
  without seeding; `just revision "msg"` creates an Alembic revision.
- `infra/compose.monitoring.yaml` is included with **no profiles**: alloy is
  always started (`just monitoring`, external Loki), the rest is selected by
  `just monitoring-stand`. See `docs/ARCHITECTURE.md` (Observability) and
  `docs/OPERATIONS.md` §8. `just drift-check` runs the workspace-level
  `../infra/check-drift.py` to verify byte-identical shared Loki configs and
  Alertmanager entrypoints across all three projects and the DWH stack.
- `infra/compose.ci.yaml` is the isolated CI stack: back **18001**, front
  **15001** (loopback-only); postgres/valkey are **private** (no published
  ports), never load the developer's `.env`, and the `jobs-data` volume is
  mounted into `back` and `worker`. CI boots `back worker front` with
  `--wait --wait-timeout 180` after the infra healthchecks pass.
- Two private volumes: `postgres-data` (`dm-postgres-data`) holds the
  authoritative job state and **must be backed up**; `jobs-data`
  (`dm-jobs-data`) holds id-keyed input/result artifacts (`JOBS_DIR=/app/jobs`),
  shared by `back` and `worker` only. Neither is published. PostgreSQL does
  **not** make local files distributed — multi-host workers need a shared or
  object-storage adapter (out of scope). `back/Dockerfile` creates `/app/jobs`
  owned by `appuser`.

## Ports & env

- Ports come from the root `.env`, single source: `FRONT_PORT=5001`,
  `BACKEND_PORT=8001` — mandatory (`:?`), spread into Dockerfile ARGs, port
  publishing and healthchecks. 5000/8000 are occupied by ReportCollector —
  do not reuse.
- Prod `back` publishes `127.0.0.1:${BACKEND_PORT}:${BACKEND_PORT}`
  (loopback-only; the browser must go through the front proxy).
  `BACKEND_INTERNAL_URL` is derived from the compose service name +
  `BACKEND_PORT`; the browser calls only its own origin (no CORS).
- PostgreSQL host/port are split exactly like ReportCollector: `.env` keeps
  `POSTGRES_HOST=localhost` + `POSTGRES_PORT=15433` for **host-side** scripts
  (`just seed`, migrations, local backend), while compose overrides
  `DATABASE_URL` with `POSTGRES_HOST_DOCKER` (default `postgres`, the compose
  service name) via the shared `x-db-env` anchor — also mounted into both
  worker variants. `DATABASE_URL` is optional (CI sets it). Never rewrite
  docker host values in `.env` just to seed. `POSTGRES_USER/PASSWORD/DB`,
  `DB_POOL_SIZE`,
  `DB_MAX_OVERFLOW`, `DB_CONNECT_TIMEOUT_SECONDS`, `VALKEY_URL`,
  `VALKEY_PASSWORD` (Valkey runs with `--requirepass`; prod `back`/`worker`
  get the password via `VALKEY_URL`), `CACHE_ENABLED`, `CACHE_KEY_SECRET`
  (stable HMAC secret ≥16 chars; startup fails without it when caching is
  on), `CACHE_TTL_SECONDS`, `CACHE_NAMESPACE_VERSION`, `CACHE_MAX_ENTRIES`,
  `CACHE_MAX_MEMORY_MB`, `MASKING_RULESET_VERSION`,
  `RESULT_MANIFEST_VERSION`, `MASKING_CONFIG_DIGEST`, `KEYS_FORMAT_VERSION`,
  `DOCX_TRANSFORMER_VERSION`, `WORKER_SHUTDOWN_GRACE_SECONDS`.
  Production startup fails clearly when credentials are missing (`:?` in
  compose). Valkey stays private to the internal network.
- Phase 4 config: `MASKING_CONFIG_DIR=/app/config/masking` (default in the
  image), `MASKING_CONFIG_DIGEST=auto` (computed from the loaded snapshot).
  `back/config/masking/` is copied into the image **except** the private
  `exceptions.yaml` (`back/.dockerignore`); compose mounts
  `./back/config/masking/exceptions.yaml` read-only into `back`/`worker` in
  prod and dev. CI uses the synthetic `exceptions.example.yaml` fallback.
  No hot reload: restart to pick up config changes.
- Job thresholds/limits in `.env.example`: `SYNC_MAX_TEXT_CHARS`,
  `SYNC_MAX_DOCX_BYTES`, `SYNC_MAX_DOCX_PARAGRAPHS`, `JOBS_MAX_ACTIVE`,
  `JOBS_CONCURRENCY`, `JOBS_MAX_ATTEMPTS`, `JOBS_LEASE_SECONDS`,
  `JOBS_POLL_SECONDS`, `JOBS_TTL_SECONDS`, `UPLOAD_MAX_MB`,
  `MAX_TEXT_CHARS`, `MAX_KEYS_CHARS`.

## Health & lifecycle

- Backend `GET /health` is a simple liveness probe (`{"status":"ok"}`, no
  model to load); front `/api/health` is a Next route. Phase 6 added
  `GET /ready` (PostgreSQL check, 503 when down) and `GET /metrics` (coarse
  counters without PII) on the backend — but **wiring them into compose/CI
  healthchecks and a front readiness route is still part of the deferred
  observability stage 6**; do not claim the healthchecks use `/ready`.
  Worker drains its pool on SIGTERM (see `jobs/worker.py`).
- `--reload-ignore-dirs logs,jobs` in the dev Dockerfile is required: Granian
  watches `/app/jobs` and the artifact spool changes on every job write.
  Restart the dev stack with `just dev` (compose `--watch`), never `docker cp`.
- `back`/`worker` do **not** depend on the database containers; they expect an
  already-provisioned PostgreSQL/Valkey stand (`just db-stand`). Alembic
  migrations are an explicit step (`just seed` / `just migrate`), never a
  startup race. CI uses its own isolated stack with its own healthchecks and
  applies migrations explicitly.

## Containers & TLS (current state, stage 6 deferred)

- Back image is Alpine, runs as `appuser` (non-root). Front prod image runs as
  the image's `node` user with `--chown=node:node` copies.
- TLS verification is **bypassed at build time on purpose** (maintainer
  decision, mirrors ReportCollector): `back/Dockerfile` uses
  `uv sync --allow-insecure-host pypi.org --allow-insecure-host
  files.pythonhosted.org`; `front/Dockerfile` base stage sets
  `NODE_TLS_REJECT_UNAUTHORIZED=0` (build/dev only — the Node prod runtime
  does not inherit it). Replacing with a trusted corporate CA
  (`NODE_EXTRA_CA_CERTS` / system trust store) belongs to stage 6.
- Container hardening (`read_only`, `tmpfs`, `cap_drop`, `no-new-privileges`,
  `pids_limit`, resource limits) and reverse-proxy/TLS/auth guidance are
  **deferred** — see `TODO.md`. Observability is no longer deferred: see the
  monitoring bullet above and `docs/OPERATIONS.md` §8.

## CI

- `.github/workflows/ci.yaml` triggers on push/PR to `main`/`dev`. Jobs:
  `config` (actionlint + compose config), `back` (ruff/ty/pytest ≥80%
  branch coverage), `front` (typecheck/lint/format/unit + 65% line-coverage
  gate via `front/scripts/check-coverage.mjs`), `secrets` (gitleaks over the
  full history), `production` (needs config+back+front, builds prod images,
  boots back+worker+front, asserts the smoke test list, runs Playwright
  `smoke`), `required` (aggregate gate).
- `dependency-review.yaml` (PR dependency review) and `docker-release.yaml`
  (tag-triggered publication of the exact images CI tested) mirror the other
  two repositories. Backend CI sets `DATABASE_URL` for Alembic in addition to
  `TEST_DATABASE_URL` for pytest — migrations read the former.
- Branch protection / required check `CI required` is configured in **GitHub
  repository settings** — the repo cannot enable it; keep the checklist in
  `TODO.md`, never claim it is active.
- `docker compose --profile prod --profile dev config --quiet` and
  `docker compose -f infra/compose.ci.yaml config --quiet` must stay green;
  actionlint via `rhysd/actionlint:1.7.7` must pass after workflow edits.
