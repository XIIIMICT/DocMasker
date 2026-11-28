# back/ — backend agent guide (level 2, global)

The root `AGENTS.md` is the entry point. This file owns **backend-wide** facts
only. Area detail lives in:
- `back/src/masking/AGENTS.md` — detectors, config/exceptions, keys, OOXML,
  windowing, result manifest.
- `back/src/jobs/AGENTS.md` — PostgreSQL schema/state machine, fencing,
  heartbeat, cancellation, TTL, artifact spool, Valkey cache.
- `docs/ARCHITECTURE.md`, `docs/API.md`, `docs/OPERATIONS.md`,
  `docs/TESTING.md`, `docs/adr/` — durable specifications.

## Stack & commands

FastAPI on Granian, Python **3.15** (**uv**); natasha + phonenumbers + regex,
`py-yaml12` keys; SQLAlchemy 2 sync + psycopg 3 + Alembic; Valkey cache.
Alpine image, runs as `appuser`; dev CMD uses `--reload-ignore-dirs jobs`.

```sh
uv run --directory back ruff check --fix
uv run --directory back ruff format
uv run --directory back ty check --python-platform linux
uv run --directory back pytest -q        # offline, filterwarnings=error, ≥80% branch
uv run --directory back python -m src.masking.validate_config
```

Module map: `src/main.py` (app + `/health` `/ready` `/metrics` Prometheus-only scrape + error handlers),
`src/errors.py`, `src/config.py`, `src/api.py`, `src/uploads.py`, then the two
area trees above. Tests: `back/tests/` (see `docs/TESTING.md`).

## Backend-wide landmines

1. **`setuptools<81` is a hard runtime pin.** `natasha`→`pymorphy2` imports
   `pkg_resources` at runtime; setuptools ≥81 removed it on Python 3.15. The
   pinned version also emits a `UserWarning`, so `filterwarnings=error` needs
   the scoped ignore in `pyproject.toml`. Never "clean up" either.
2. **Deprecated APIs fail the suite.** Use
   `status.HTTP_422_UNPROCESSABLE_CONTENT`, not `..._UNPROCESSABLE_ENTITY`.
   Any new endpoint path tests hit must use non-deprecated symbols.
3. **CPU *and* IO work must leave the event loop** (`masking/executor.py`):
   `run_cpu` (bounded CPU pool, `JOBS_CONCURRENCY * 2` slots, overflow → 429)
   and `run_blocking` (separate bounded IO pool, `DB_POOL_SIZE +
   DB_MAX_OVERFLOW` slots). One semaphore must not stand in for all resource
   limits. Never call maskers, `job_repository()` DB methods, ZIP/XML parsing
   or large hashing directly in `async def`.
4. **One error envelope everywhere** (`src/errors.py`): `{code, detail,
   request_id}` + `X-Request-ID`. `code` stable/machine-readable, `detail`
   Russian. No traceback, path, DB message, PII, keys or submitted values ever
   leak; 500 logs only the exception *type*. Statuses: 413/415/422/429/409/404;
   full contract in `docs/API.md`.
5. **Streaming bounds are real limits.** `uploads.py::stage_upload` counts
   streamed bytes (Content-Length is not trusted), validates from the staged
   path, and cleans up on error/abort; results use `FileResponse`. The sync
   file endpoints still hold one small package in memory — documented, not
   claimed as streaming. See `docs/API.md` §Streaming.
6. **Settings are cross-validated** (`config.py`): positive limits/TTLs,
   PostgreSQL-only URL, lease/heartbeat/DB-connect relation, worker
   concurrency ≤ pool capacity, `JOBS_MAX_ACTIVE > JOBS_CONCURRENCY`, sync
   thresholds ≤ hard limits, cache secret when enabled. Defaults
   `JOBS_CONCURRENCY=4`, `JOBS_MAX_ACTIVE=32`, `JOBS_RETRY_AFTER_SECONDS=45`
   are measurement-derived (`docs/PERFORMANCE.md`); `test_settings_validation.py`
   pins them. Never relax them.
7. **`MASK_LABELS` fails startup**; profile APIs/fields were removed.
   `GET /api/labels` is the single canonical catalogue. `/ready` checks
   PostgreSQL; `/metrics` exposes only the `dm_*` Prometheus exposition (the
   legacy JSON answer was removed without a compatibility window).
8. **§8.2 (OpenAPI → TS types) is implemented.** `app.openapi()` is exported
   in-process by `python -m src.openapi_export`; the pinned dev-only generator
   writes `front/src/shared/api/generated/openapi.d.ts` (`bun run --cwd front
   gen:openapi`, CI gate `gen:openapi --check`). Generated DTOs are compile-time
   only — no runtime validation, no client SDK. See `docs/API.md`, `TODO.md`.
9. **Windows/PowerShell mangles UTF-8 request bodies.** Put JSON in a UTF-8
   file and POST with `curl.exe --data-binary @file`, or run inside a container.
   Corrupted Cyrillic in the response is a harness artifact, not a server bug.
10. **Never commit credentials or private `exceptions.yaml`** (gitignored;
    mounted read-only). CI uses `exceptions.example.yaml`. No PII in rows,
    logs, metrics, cache keys or error details — see `docs/SECURITY.md`.
11. **All `/api/*` responses are uncacheable.** `cache_headers.py` sets
    `Cache-Control: no-store` + `Pragma: no-cache` (PII JSON, ZIPs, errors);
    per-route `Content-Disposition`/`Cache-Control` must survive. Probes stay
    out of scope. Do not add cache headers only to ZIP routes again.
12. **Artifact storage is a typed precondition, not a 500.** `jobs/readiness.py`
    checks `JOBS_MIN_FREE_BYTES` (default 1 GiB) + a write probe at worker
    startup and job admission, before any staging row/artifact; failure is
    `{code:"artifact_storage_unavailable", detail:<Russian>, request_id}` with
    HTTP 503 and `X-Request-ID`. No new metric/log field; the alert/dashboard
    threshold must be updated together with the setting.

When behavior changes, update the matching area guide and `docs/` file in the
same change; keep each fact in exactly one home.
