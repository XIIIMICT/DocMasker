# Remediation Phase 0 — research (read-only)

Scope: facts only, no code/data/remote changes. Source coordinates `file:line`
are evidence, not measurements. Every factual number is tagged **MEASURED**
(observed in source or a local run), **ESTIMATE** (documented/derived, not
verified here) or **UNKNOWN** (no evidence obtainable).

## Repo state

| Fact | Value | Evidence |
|---|---|---|
| Working tree | `dev`, clean (`## dev...origin/dev`) — MEASURED | `git status --short --branch` |
| `dev` head | `2fe2edebdd285e2c4a6f8a6fa2e009b418fd0585` — MEASURED | `git rev-parse dev` |
| Remote | `origin  git@github.com:XIIIMICT/DocMasker.git` (fetch+push) — MEASURED | `git remote -v` |
| Default branch | UNKNOWN — no local `refs/remotes/origin/HEAD`; `git ls-remote --symref origin HEAD` timed out on SSH. Local `main` and `dev` exist; CI targets both (MEASURED) | `git branch -a`, `.github/workflows/ci.yaml:4-7` |

## 1. Declared contracts

Docs/agents read: root `AGENTS.md`; `infra/AGENTS.md`, `front/AGENTS.md`,
`back/AGENTS.md`, `back/src/jobs/AGENTS.md`, `back/src/masking/AGENTS.md`;
ADRs `0001-masking-profiles.md` (superseded), `0002-durable-job-execution.md`
(superseded), `0003-postgres-job-foundation.md` (accepted),
`0004-ooxml-transformer.md` (accepted), `0005-single-config-and-cache.md`
(accepted). All locations under `docs/adr/`.

**Ports** (all MEASURED): front `5001`, back `8001` (`.env.example:12-13`,
`AGENTS.md:27-29`); CI back `18001`, CI front `15001`
(`infra/compose.ci.yaml:58,96`, `docs/WORKFLOW.md:145`); worker scrape `8002`
(`.env.example:133`, `back/src/config.py:75`); PG host `15433`
(`.env.example:57`, `infra/compose.db.yaml:25`); CI PG `15432`, CI Valkey
`16379` (`.github/workflows/ci.yaml:58,64`); Alloy UI `12345`, Loki `3100`
(`docs/OPERATIONS.md:354`, `infra/compose.monitoring.yaml:35,53`).

**Config env vars** (MEASURED; `.env.example` unless noted): ports/hosts
`BACKEND_PORT`, `FRONT_PORT`, `BACKEND_HOST`, `BACKEND_HOST_DEV`,
`AUTH_APP_ORIGIN` (`:12-23`); limits `UPLOAD_MAX_MB`, `MAX_TEXT_CHARS`,
`MAX_KEYS_CHARS` (`:38-40`); PG `POSTGRES_USER/PASSWORD/DB/HOST/HOST_DOCKER/PORT`,
`DATABASE_URL`, `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_CONNECT_TIMEOUT_SECONDS`
(`:47-63`); Valkey/cache `VALKEY_URL`, `VALKEY_PASSWORD`, `CACHE_ENABLED`,
`CACHE_TTL_SECONDS`, `CACHE_NAMESPACE_VERSION`, `CACHE_KEY_SECRET`,
`CACHE_CONNECT_TIMEOUT_SECONDS`, `CACHE_SOCKET_TIMEOUT_SECONDS`,
`CACHE_MAX_ENTRIES`, `CACHE_MAX_MEMORY_MB` (`:64-74`); versions/digests
`MASKING_RULESET_VERSION`, `RESULT_MANIFEST_VERSION`, `MASKING_CONFIG_DIGEST`,
`MASKING_CONFIG_DIR`, `KEYS_FORMAT_VERSION`, `DOCX_TRANSFORMER_VERSION`
(`:76-81`); staging/jobs `STAGING_LEASE_SECONDS`, `JOBS_DIR`,
`SYNC_MAX_TEXT_CHARS`, `SYNC_MAX_DOCX_BYTES`, `SYNC_MAX_DOCX_PARAGRAPHS`,
`JOBS_MAX_ACTIVE`, `JOBS_CONCURRENCY`, `JOBS_MAX_ATTEMPTS`, `JOBS_LEASE_SECONDS`,
`JOBS_POLL_SECONDS`, `JOBS_TTL_SECONDS` (`:82-93`); observability `LOKI_URL`,
`LOG_ENV`, `GF_SECURITY_ADMIN_USER/PASSWORD`, `PROMETHEUS_RETENTION`,
`LOKI_RETENTION`, `BACKEND_METRICS_HOST`, `WORKER_METRICS_HOST`,
`WORKER_METRICS_PORT`, `METRICS_PUSHGATEWAY_URL` (`:112-136`); alert/SMTP
`SUPPORT_MAIL`, `ALERT_SMTP_*`, `SMTP_RELAY_*` (`:144-159`). Additional
worker-only knobs declared in `back/AGENTS.md`/jobs guide but not in
`.env.example`: `CACHE_GC_INTERVAL_SECONDS` (default `900` s — MEASURED,
`infra/AGENTS.md:68-69`), `DETECTION_MIN_CHARS` (`8000`),
`DETECTION_CHUNK_CHARS` (`4096`), `DETECTION_OVERLAP_CHARS` (`512`),
`DETECTION_MAX_WINDOW_CHARS` (`16384`) — MEASURED, `back/src/config.py:85-91`.

**Metric names** (`dm_*`, MEASURED, `back/src/metrics.py:77-235`):
`dm_http_requests_total`, `dm_http_request_duration_seconds`,
`dm_http_last_failure_timestamp_seconds`, `dm_http_requests_in_progress`,
`dm_dependency_check_duration_seconds`, `dm_db_connections_in_use`,
`dm_db_pool_capacity`, `dm_runtime_event_loop_lag_seconds`,
`dm_runtime_disk_free_bytes`, `dm_cache_operations_total`,
`dm_cache_gc_candidates`, `dm_cache_gc_deleted_total`,
`dm_cache_gc_reclaimed_bytes_total`, `dm_cache_gc_failures_total`,
`dm_uploads_total`, `dm_jobs_created_total`, `dm_jobs_succeeded_total`,
`dm_jobs_failed_total`, `dm_jobs_retried_total`, `dm_jobs_active`,
`dm_jobs_running`, `dm_job_duration_seconds`, `dm_job_queue_age_seconds`,
`dm_lease_renew_failures_total`, `dm_lease_expired_total`,
`dm_artifact_disk_free_bytes`, `dm_worker_heartbeat_timestamp_seconds`.
Legacy JSON keys: `active_jobs`, `cache.{hits,misses,writes,evictions,gc_candidates,gc_deleted,gc_reclaimed_bytes,gc_failures}` (MEASURED, `back/src/main.py:162-174`).

**CI gates / check names** (MEASURED, `.github/workflows/ci.yaml`): jobs
`config` (`:19`, actionlint `:26`, compose config `:30-31`,
`python3 infra/check-monitoring-binds.py` `:33`), `back` (`:35`, ruff/ty
`:76-78`, alembic `:80`, pytest with `--cov-fail-under=80` `:82`),
`front` (`:112`, `gen:labels` freshness `:131-133`, typecheck/lint/format
`:134-136`, `check-coverage.mjs 65` `:139`), `openapi-contract` (`:147`,
`gen:openapi --check` `:169`), `secrets` (`:93`, gitleaks full history
`:98-110`), `production` (`:172`, smoke discovery `:219-224`, Playwright
`smoke` `:226`), aggregate `required` / `CI required` (`:253-263`). Branch
protection for `CI required` is a GitHub-side setting — status UNKNOWN
(`infra/AGENTS.md:135-137`).

**Deprecation policy** (MEASURED): no timed compatibility window is declared;
breaking removals are loud. `MASK_LABELS` fails startup with no window
(`docs/adr/0001-masking-profiles.md:68-70,145-146`, `back/src/config.py:116-120`);
`profile_id`/`GET /api/profiles` removed (`docs/adr/0005-single-config-and-cache.md:28-30`);
deprecated FastAPI status symbol `HTTP_422_UNPROCESSABLE_ENTITY` must not be
used (`back/AGENTS.md:36-38`). Deliberate legacy carry-overs: keys `Профиль`
marker ignored on read (`back/src/masking/AGENTS.md:45`), legacy result
suffix `result` still accepted (`back/src/jobs/api.py:225-227`), and the
legacy JSON `/metrics` answer retained (`back/src/main.py:150-151`).

## 2. Metrics endpoint, artifact responses, disk failure, CI bind guard

### (a) Legacy JSON `/metrics` and consumers

- Endpoint: `back/src/main.py:145` (`@app.get("/metrics", include_in_schema=False)`);
  content negotiation at `:153-155` (`Accept: text/plain` → Prometheus);
  legacy JSON body built at `:156-174`, returned at `:174`. No `Cache-Control`
  header is set on either branch (MEASURED).
- Consumers (MEASURED, exact locations):
  - Tests: `back/tests/test_readiness.py:57-63` (asserts `active_jobs is None`
    and `cache.hits`); `back/tests/test_metrics.py:175-182` (plain request
    asserts the legacy JSON object).
  - Docs: `docs/API.md:118-129` (contract + example); `docs/OPERATIONS.md:82,142-147,214,250,265,274,299`;
    `docs/ARCHITECTURE.md:164-166`; `docs/SECURITY.md:56`;
    `infra/AGENTS.md:92-95`; `TODO.md:125`;
    `docs/PRODUCTION_READINESS.md:137,150`;
    `docs/adr/0003-postgres-job-foundation.md:205`.
  - Grafana/Alertmanager: **no** legacy-JSON consumer found. Dashboards use
    `dm_*` series (`infra/grafana/provisioning/dashboards-json/{worker,cache,api-errors,artifact-disk}.json`,
    e.g. `worker.json:17-18`, `cache.json:19`);
    alert rules use `dm_*` (`infra/prometheus/alert-rules.yml:9,47,62,77,90,104,118,132-133,148,163-164,179-182,197`);
    Prometheus scrapes `/metrics` with its default probe
    (`infra/prometheus/prometheus.yml.tpl:42,55,63`). Frontend: no `/metrics`
    fetch found (MEASURED by `rg`). Worker listener serves Prometheus text
    only (`back/src/jobs/metrics.py:22,30-47`).

### (b) Endpoints serving artifacts / PII-derived responses and headers

All values MEASURED from source. "None" = no explicit `Cache-Control` in app
code (no middleware adds one; `back/src/errors.py:131-177` adds only
`X-Request-ID`).

| Endpoint | Location | Body | Current headers |
|---|---|---|---|
| `POST /api/mask` | `back/src/masking/router.py:324-364` | masked text + keys YAML + stats (PII-derived) | default JSON `Content-Type`; `X-Request-ID`. No `Cache-Control` |
| `POST /api/unmask` | `back/src/masking/router.py:426-478` | restored text (PII) | same; no `Cache-Control` |
| `POST /api/mask-file` | `back/src/masking/router.py:367-423`; headers helper `:156-162` | ZIP: masked docx + keys + manifest | `Content-Disposition`, `Content-Type: application/zip`, `Cache-Control: no-store` |
| `POST /api/unmask-file` | `back/src/masking/router.py:481-547`; helper `:156-162` | ZIP: restored docx + manifest | same three; `no-store` |
| `POST /api/jobs` | `back/src/jobs/api.py:85-167` | `202` opaque ids, no PII | JSON; `X-Request-ID`; no `Cache-Control` |
| `GET /api/jobs/{id}` | `back/src/jobs/api.py:170-200` | safe status projection | JSON; no `Cache-Control` |
| `GET /api/jobs/{id}/result` | `back/src/jobs/api.py:203-249` | streamed result ZIP | `FileResponse` with `Content-Disposition`, `Cache-Control: no-store` (`:244-249`) |
| `GET /metrics` (legacy) | `back/src/main.py:145-174` | coarse counters | JSON or Prometheus text; no `Cache-Control` |
| Front `/api/mask`, `/api/unmask` proxies | `front/src/app/api/mask/route.ts:25`, `unmask/route.ts`; `front/src/shared/api/mask.ts:19-25,86-104,112-124` | streamed JSON | `Content-Type`, `X-Request-ID` only; no `Cache-Control` |
| Front `/api/mask-file`, `/api/unmask-file`, `/api/jobs/[...path]` | `front/src/app/api/mask-file/route.ts:40`, `unmask-file/route.ts:38`, `jobs/[...path]/route.ts:21-26`; `front/src/shared/api/proxy.ts:5-17` | streamed ZIP/JSON | forwards `Content-Type`, `Content-Disposition`, `Cache-Control`, `X-Request-ID`; result ZIPs inherit `no-store` from backend |

### (c) Artifact write path and full/read-only disk behavior

MEASURED from code unless tagged otherwise. Enqueue: `back/src/jobs/routing.py:61-116`
creates a `staging` row (`repository.py:154-208`), then writes `input`/`name`/
`kind`/`keys` via `ArtifactStore.write` / `write_result_from_path` /
`copy_from` (`back/src/jobs/storage.py:103-127,153-197`) and only then
`publish_staging`. Any write exception aborts staging and deletes partial
artifacts (`routing.py:101-115`). Worker result path:
`back/src/jobs/worker.py:259-289` builds a temp result ZIP, writes an
attempt-scoped result, performs the fenced `succeed`, then caches.

- **Full disk at submit/staging:** `OSError` is re-raised uncaught
  (`routing.py:101-104`) → generic handler `back/src/errors.py:220-228` →
  HTTP `500` with `{code:"internal_error", detail:"Внутренняя ошибка сервера."}`
  (`back/src/errors.py:225-228`; Russian detail, `X-Request-ID`); no client
  retry hint. Drill evidence: injected
  ENOSPC at staging produced `failed` / `staging_failed` with `0` leftover
  input files (`docs/PRODUCTION_READINESS.md:61`, `back/tests/drill_support.py:471-481`).
- **Full disk during worker ZIP/result write:** the `OSError` is not converted
  to `JobExecutionError` (`worker.py:274-277`, `storage.py:110-127`), so the
  generic branch maps it to `internal_error`, retryable (`worker.py:331-342,
  101-110`); `_fail` applies immediate first retry then capped exponential
  backoff (`worker.py:356-369`, cap `30` s — MEASURED) up to `JOBS_MAX_ATTEMPTS`
  (`3` — MEASURED). Drill: injected ENOSPC at ZIP creation left the job
  `queued` with no partial result; retry succeeded
  (`docs/PRODUCTION_READINESS.md:58`, `back/tests/drill_support.py:316-354`).
  Client polling sees `status`/`error_code` only (`back/src/jobs/api.py:189-200`);
  a terminal failure exposes `error_code` and no `result_url`.
- **Read-only artifact volume:** no explicit EROFS test found — UNKNOWN. Code
  inference (ESTIMATE): `ArtifactStore.__init__` `mkdir(exist_ok=True)`
  (`storage.py:36-38`) succeeds when the directory exists; writes then raise
  `OSError`, following the same `500` (submit) / `internal_error` + retry
  (worker) paths as ENOSPC.

### (d) `infra/check-monitoring-binds.py`

MEASURED (`infra/check-monitoring-binds.py:1-85`, invoked at
`.github/workflows/ci.yaml:32-33`): scans `compose.yaml`,
`infra/compose.db.yaml`, `infra/compose.monitoring.yaml`,
`infra/compose.ci.yaml` (`:18-23`). For every `ports:` entry not belonging to
service `front` (`:24`), any host:container mapping whose host part is not
`127.0.0.1` or `::1` is a failure (`:30-35,38-69`); container-only port
declarations (no `:`) are ignored (`:32-33`). Exit `1` with an itemised list
or `0` on success (`:72-81`).

## 3. Worker RSS / `JOBS_CONCURRENCY`

MEASURED: worker `ThreadPoolExecutor(max_workers=JOBS_CONCURRENCY,
thread_name_prefix="dm-job")` (`back/src/jobs/worker.py:180-189`); sync CPU
pool uses `JOBS_CONCURRENCY * 2` (`back/src/masking/executor.py:47-65`);
one masker per thread via `threading.local()` (`back/src/masking/rule_based.py:705-719`,
`back/src/jobs/operations.py:115`). `JOBS_CONCURRENCY` default `2` and must not
exceed `DB_POOL_SIZE + DB_MAX_OVERFLOW` (`back/src/config.py:92-94,214-215`).
Existing memory measurements: `docs/PERFORMANCE.md:302-342` records drill
peak working-set RSS of `224.8 / 260.2 / 295.3 / 367.7` MB at concurrency
`1 / 2 / 2 / 4` (MEASURED previously, not re-run here); the `~110–170 MB per
MorphVocab instance` figure at `:304-308` is an **ESTIMATE**, not a measured
per-thread RSS delta. No existing run at concurrency `6`/`8` was found
(MEASURED).

Local proxy measurement (MEASURED here): fresh process per concurrency value,
`N` threads warm one thread-local masker each through
`mask_document(MaskRequest(text=...) )` on synthetic disposable text (`3820`
chars, fabricated PII-shaped values), no DB, no repo files changed; peak
Windows working set via `GetProcessMemoryInfo` (same method as
`back/benchmarks/util.py:18-52`). Script:
`C:\Users\Scorpi-ON\AppData\Local\Temp\opencode\dm_rss_probe.py` (outside repo).
Command: `foreach ($n in 2,4,6,8) { uv run --no-sync --directory back python ...\dm_rss_probe.py $n }`.

| Threads | Baseline peak (MB) | Peak after (MB) | Delta (MB) | Delta/thread (MB) | Wall (s) |
|---|---:|---:|---:|---:|---:|
| 2 (MEASURED) | 81.7 | 166.1 | 84.4 | 42.2 | 0.300 |
| 4 (MEASURED) | 81.8 | 201.9 | 120.1 | 30.0 | 0.539 |
| 6 (MEASURED) | 82.0 | 248.0 | 166.0 | 27.7 | 0.785 |
| 8 (MEASURED) | 81.8 | 281.0 | 199.2 | 24.9 | 1.074 |

Caveat (ESTIMATE): this is a masker-warm proxy, not full worker RSS; shared
natasha pages and short synthetic input lower the marginal cost. The audit's
`~110 MB/thread` is an UNVERIFIED ESTIMATE as a per-thread RSS delta. Full
worker RSS at `6`/`8` would require the drill harness
(`back/src/scripts/operational_drills.py:52-99`) with a disposable
`TEST_DATABASE_URL` and its fixed `1/2/2/4` cases
(`docs/PERFORMANCE.md:328-333`) — extending it to `6`/`8` needs a code change,
which is out of scope here.

## 4. Queue-full behavior

MEASURED: `JOBS_MAX_ACTIVE` default `16` (`.env.example:88`,
`back/src/config.py:93`); admission counts `staging|queued|running|cancelling`
under advisory lock `0x444D5F41444D494E` (`back/src/jobs/repository.py:46-49,154-189`).
At capacity the repository raises `JobQueueFullError` with Russian detail
`"Очередь заданий переполнена. Повторите позже."` (`repository.py:187-189`).
Translations: `POST /api/jobs` → `ApiError("queue_full", ..., 429)`
(`back/src/jobs/api.py:55-71,163-164`); sync overflow/file routes →
`HTTPException(429)` mapped to code `queue_full` by detail phrase
(`back/src/masking/router.py:109-121,227-245`, `back/src/errors.py:85-99,188-199`).
Envelope: HTTP `429`, `{code:"queue_full", detail:"Очередь заданий переполнена. Повторите позже.", request_id:"..."}` plus `X-Request-ID` (`back/src/errors.py:36-41,124-125,179-186`).
No staging row/artifacts remain (`docs/OPERATIONS.md:155-158`,
`back/tests/test_jobs_store.py:364`, `back/tests/test_jobs_api.py:155-160`).
Retry: server-side automatic retry is **not** performed for an admission
rejection; the client must resubmit. Frontend shows the Russian `detail`
verbatim (`front/src/features/masking/mask-requests.ts:26-30`,
`front/src/features/masking/use-masker-workflow.ts:206-220,235-256`) and does
not auto-retry `429` (MEASURED). No `Retry-After` header was found (MEASURED).

## 5. Legacy `RuleBasedMasker.mask()`

MEASURED (`rg "\.mask\(|def mask\(" back/`): definition at
`back/src/masking/rule_based.py:648-654` (quadratic rewrite: one string slice
per entity). **No production request/worker call site.** All `.mask(...)`
calls are in tests/benchmarks: `back/tests/test_rule_based.py:26-271`,
`test_config.py:118-273`, `test_address_prefilter.py:183-208`,
`test_unmask.py:24-40`, `test_mask.py:27`, plus `test_transform_cleanup.py`,
`test_ooxml*.py`, `test_docx.py`, `test_reversible_demasking.py` and
`back/benchmarks/docx_mask.py:84`. Production text path uses
`detect()` + `mask_text()` one-pass join (`back/src/masking/service.py:162-171`,
`back/src/masking/AGENTS.md:44`); DOCX path calls `detect()` inside the
transform (`back/src/masking/docx_service.py`, `back/src/jobs/operations.py:115-122`).

## 6. PostgreSQL backup state

MEASURED: `infra/compose.db.yaml:13-31` defines one PostgreSQL service
`postgres:18-alpine`, named volume `postgres-data` / `dm-postgres-data`
(`:57-59`), loopback-only publish `127.0.0.1:${POSTGRES_PORT:-15433}:5432`
(`:24-25`); Valkey has no volume and `appendonly no` (`:33-55`). CI compose
(`infra/compose.ci.yaml:20-30,112-113`) uses `postgres:18-alpine` with **no
named PG volume** (ephemeral; torn down with `down --volumes --remove-orphans`
at `.github/workflows/ci.yaml:249-251`). No automated backup job/schedule,
retention policy or backup cron exists in the repo (MEASURED by `rg` for
`pg_dump|backup|retention`): backup is a documented **manual operator
procedure** (`docs/OPERATIONS.md:66-107`: quiesce, `pg_dump -Fc`, copy
`dm-jobs-data`, backup config, restore DB → artifacts). A disposable local
rehearsal passed (`docs/OPERATIONS.md:109-118`,
`docs/PRODUCTION_READINESS.md:67-68`); target-host backup/restore is an
**OPEN** gate (`docs/PRODUCTION_READINESS.md:172`). Artifact volume
`dm-jobs-data` is declared in `compose.yaml` and must be backed up with the DB
(`docs/OPERATIONS.md:68-76`). Retention values: `PROMETHEUS_RETENTION=15d`,
`LOKI_RETENTION=168h` (`.env.example:122-123`) are metrics/log retention,
**not** database backup retention.

## Commands used (all read-only except the temporary RSS script outside the repo)

```
git status --short --branch
git remote -v
git branch -a
git rev-parse dev
git symbolic-ref refs/remotes/origin/HEAD
git ls-remote --symref origin HEAD
rg "<patterns above>" D:\Work\DocMasker
uv run --no-sync --directory back python C:\Users\Scorpi-ON\AppData\Local\Temp\opencode\dm_rss_probe.py <2|4|6|8>
```
