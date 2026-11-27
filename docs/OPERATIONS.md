# Operations

This is the canonical home for startup order, migrations, backup/restore,
probes, TTL/GC, worker drain and failure runbooks. Compose/ports/volume
invariants stay in `infra/AGENTS.md`; backend and job hot-path rules in
`back/AGENTS.md` and `back/src/jobs/AGENTS.md`; HTTP probes in `docs/API.md`;
security boundary in `docs/SECURITY.md`; verification commands in
`docs/TESTING.md`. Deferred operational work is in `TODO.md`, "Old stage 6 (deferred)".

## 1. Startup order (mandatory, ReportCollector pattern)

PostgreSQL and Valkey are **not** part of the app profiles and are not pulled
in by `depends_on`. The application expects an already-provisioned data stand;
schema creation is an explicit step, never a startup race
(`infra/AGENTS.md`, `docs/WORKFLOW.md`, `justfile`).

From the repository root, in this order:

```sh
cp .env.example .env          # once; root is the single source of settings
just deps                     # uv sync (back) + bun install (front)
just db-stand                 # docker compose up -d --wait postgres valkey
just seed                     # Alembic 0001_jobs + verify the jobs table (idempotent)
just dev                      # or: just prod
```

`just seed` runs on the host using `POSTGRES_HOST`/`POSTGRES_PORT` from `.env`
(localhost:15433). Compose overrides `DATABASE_URL` with
`POSTGRES_HOST_DOCKER` (the compose service name) for `back`/`worker`, following
ReportCollector — there is no need to rewrite `.env` between host and container
runs. `POSTGRES_PASSWORD` is read from the same components in both places.

- `just dev` starts `back-dev`, `dm-worker-dev`, `dm-front-dev` with
  `develop.watch` hot-reload; `just prod` starts `dm-back`, `dm-worker`,
  `dm-front`. Sync a running dev stack by restarting with `just dev`
  (compose `--watch`), never `docker cp`.
- Ports come from `.env`: front **5001**, backend **8001**; 5000/8000 belong to
  ReportCollector and must not be reused. Prod publishes the backend on
  loopback only; the browser always goes through the Next proxy.
- Configuration changes (including `exceptions.yaml`) require a restart:
  there is no hot reload (`back/src/masking/AGENTS.md`).
- There are no host-run server recipes in the `justfile` (`just back` /
  `just front` are commented out). Host runs need Granian directly and the
  `justfile`-exported `UV_SYSTEM_CERTS` / `NODE_OPTIONS` for corporate CAs
  (`docs/WORKFLOW.md`).

## 2. Migrations and schema

- `just migrate` — `uv run --directory back alembic upgrade head`; applies
  migrations without seeding.
- `just revision "msg"` — `uv run --directory back alembic revision --autogenerate -m "msg"`.
- Migration chain: `back/migrations/versions/0001_jobs.py` creates the `jobs`
  table (statuses, lease/fencing columns, TTL deadlines, opaque artifact ids,
  safe `error_code`); the shared metadata in `back/src/jobs/schema.py` backs
  both the model and the migration. No `create_all()`, no SQLite fallback
  (ADR-0003, `back/src/jobs/AGENTS.md`).
- Migrations are an explicit deployment/CI step: CI migrates an empty database
  (`uv run --no-sync alembic upgrade head` in `.github/workflows/ci.yaml`) and
  the isolated prod-image stack (`docker compose -f infra/compose.ci.yaml exec
  -T back alembic upgrade head`, then `restart worker`).
- Empty-DB migration is verified in CI. Migrating retained live data is **not**
  exercised: the recorded baseline had zero rows in the old SQLite `jobs` table
  (`docs/IMPLEMENTATION_BASELINE.md`), so the approved path was a documented
  quiesce/reset, not a data migration (ADR-0003 §Migration notes).

## 3. Volumes, backup and restore

| Volume | Name | Contents | Backup? |
|---|---|---|---|
| PostgreSQL data | `dm-postgres-data` (declared in `infra/compose.db.yaml`) | Authoritative job state, leases, attempts, artifact references | **Yes** |
| Job artifacts | `dm-jobs-data` (declared in `compose.yaml`) | Id-keyed staged inputs/keys, results and the cache spool, shared by `back`/`worker` | **Yes, consistent with the DB** |
| Valkey | none (no persistence, `appendonly no`, `maxmemory 256mb`, `allkeys-lru`) | Disposable cache index only | **No** |

A PostgreSQL dump without its referenced artifacts is not a recovery point, and
an artifact copy without the matching DB rows is not one either
(ADR-0003 §Migration/backup notes, Brief §2.4).

**Consistent backup procedure:**

1. Stop writers: `docker compose --profile prod stop worker back front`
   (use `--profile dev` with the dev service names for a dev stand). Wait until
   no active jobs remain — `GET /metrics` `active_jobs` should be `0`, or query
   the `jobs` table. Do not back up mid-publication.
2. Dump PostgreSQL (credentials come from the root `.env`; example uses the
   template values):
   `docker compose exec -T postgres pg_dump -U dm -d docmasker -Fc > dm-postgres.dump`
3. Copy the artifact spool:
   `docker compose cp back:/app/jobs ./dm-jobs-data-backup`
   (for the dev stack use `back-dev`).
4. Back up the private configuration securely:
   `back/config/masking/exceptions.yaml` and the lexicons under
   `back/config/masking/lexicons/` (the latter are committed, the former is not).
5. Resume the stack: `just prod` or `just dev`.

**Restore:** restore configuration, then the **database first, artifacts
second**:

```sh
docker compose exec -T postgres pg_restore -U dm -d docmasker --clean --if-exists < dm-postgres.dump
docker compose cp ./dm-jobs-data-backup/. back:/app/jobs
just prod
```

After restore, check that every `input_artifact` / `keys_artifact` /
`result_artifact` reference resolves to a file in `/app/jobs` and that
`GET /ready` returns `200`. Valkey needs no restore: restart/flush it and let
the cache rebuild (`docs/SECURITY.md` §3).

**Drill evidence (2026-11-27, local disposable only).** The harness in
`back/tests/drill_support.py` executed the dump → artifact copy → truncate →
`pg_restore` → artifact copy-back sequence against a dedicated `dm-drill-*`
container and a temporary spool: 2/2 rows restored identically, 2/2 artifact
SHA-256 checksums matched, and the digest of a *committed synthetic* config
snapshot (`exceptions.example.yaml`) was unchanged. No private
`exceptions.yaml` was read or printed (`private_exception_values_touched:
false`). This is **not** a target-host rehearsal: restoring the real
read-only private config and the named volumes on the deployment host stays an
operator-only **OPEN** gate (`docs/PRODUCTION_READINESS.md`).

**Historic quiesce/reset note:** before the PostgreSQL migration, the recorded
dev volume held a 20 KB SQLite file with **0 rows** and no staged artifacts.
The approved procedure was to quiesce with `docker compose --profile dev down`,
keep `dm-jobs-data` as a backup until PostgreSQL was verified, and only then
remove the obsolete volume with an explicit `down --volumes` on the new stack —
never a blind `rm` (`docs/IMPLEMENTATION_BASELINE.md`, ADR-0003). Today
`dm-jobs-data` is the **live artifact spool** and must be backed up, not deleted.

## 4. Probes and metrics

- **`GET /health` — liveness.** Always `200 {"status":"ok"}`; there is no model
  to load or warm up. This is what the Compose healthchecks actually call
  (`compose.yaml`: `dm-back` and `dm-front`; `infra/compose.db.yaml`: PostgreSQL
  and Valkey). Front liveness is the Next route `/api/health`
  (`front/e2e/health.spec.ts`).
- **`GET /ready` — readiness.** Checks PostgreSQL via the repository `ping`
  and that the immutable masking config is loaded: `200 {"status":"ready",
  "labels":6}` or `503 {"status":"not_ready", "reason":"Хранилище заданий
  недоступно."}` (`back/src/main.py`, `docs/API.md` §`GET /ready`). A DB failure
  logs only the exception type. **Do not claim Compose/CI healthchecks use
  `/ready`** — wiring it (and a front readiness route) is deferred stage 6
  (`infra/AGENTS.md`, `TODO.md`).
- **`GET /metrics`** keeps the legacy coarse JSON answer for a plain request,
  e.g. `{"active_jobs":0,"cache":{"hits":3,"misses":1,"writes":1,"failures":0,"evictions":0,"gc_candidates":0,"gc_deleted":0,"gc_reclaimed_bytes":0,"gc_failures":0}}`;
  a DB outage degrades to `active_jobs: null` and a cache outage to zeroed
  counters; neither returns 500. With `Accept: text/plain` (what Prometheus
  sends) the same route returns the full `dm_*` exposition instead. No ids,
  inputs, keys or key names are exposed (`docs/SECURITY.md`, §8).
- Full metrics (latency, queue depth/age, lease losses, retries, job durations,
  DB pool saturation) and structured JSON logs are now emitted by the backend
  and worker and shipped by the monitoring stack — see §8. Compose/CI
  healthchecks still call `/health`, not `/ready`.

## 5. Job lifecycle operations

- **Queue limits and backpressure:** `JOBS_MAX_ACTIVE` (default **32**) counts
  `staging|queued|running|cancelling`; over capacity returns the typed 429
  `queue_full` envelope and leaves no staging row. The 429 also carries
  `Retry-After: <seconds>` (integer, from `JOBS_RETRY_AFTER_SECONDS`, default
  **45**); no other error gets this header. There is no server-side auto-retry —
  the client resubmits after the hint, and the front proxies forward the header.
  Cross-field validation requires `JOBS_MAX_ACTIVE > JOBS_CONCURRENCY`.
  Tests: `test_jobs_store.py::test_queue_full_leaves_no_staging_row`,
  `test_jobs_api.py::test_queue_full_returns_typed_429_with_retry_after`,
  `::test_admission_boundary_rejects_then_admits_after_release`,
  `test_jobs_worker.py::test_saturation_bounds_claims_and_retry_is_bounded`.
- **Leases/fencing:** every claim gets a fresh `uuid4()` token and `worker_id`;
  all mutations are ownership-guarded. A heartbeat thread renews the lease at
  `JOBS_LEASE_SECONDS / 3` independently of the blocking masking work. A stale
  worker cannot publish a result; expired leases are reclaimed with a new token
  and incremented attempt until `JOBS_MAX_ATTEMPTS`
  (`back/src/jobs/AGENTS.md`, `back/src/jobs/worker.py`).
- **TTL/GC:** active jobs are never swept. The TTL clock starts on a terminal
  transition (`JOBS_TTL_SECONDS`, default 3600); `expired_jobs` selects only
  terminal rows, deletes artifacts first and then removes rows. Queued jobs
  past their deadline become `expired` with `error_code=queue_timeout`.
  Failed/cancelled rows have bounded retention
  (`test_jobs_store.py::test_active_jobs_are_never_swept`,
  `::test_result_ttl_starts_on_completion`).
- **Cancellation:** `DELETE /api/jobs/{id}` makes a queued job terminal
  immediately; for a running job it sets `cancelling` and the worker
  acknowledges at cooperative checkpoints. A cancelled job publishes neither
  result nor cache entry. Client-side abort only stops polling
  (`docs/API.md`, `front/AGENTS.md`).
- **Worker drain:** on SIGTERM the worker stops claiming new jobs but keeps
  heartbeating in-flight jobs while its pool drains with
  `shutdown(wait=True, cancel_futures=False)` — it never pretends a Python
   thread was killed (`back/src/jobs/worker.py`). Compose uses its default
   stop grace; if the process is killed before the pool drains, lease expiry
   recovers the job with bounded attempts. No configurable shutdown grace is
   claimed: Python cannot safely force-stop worker threads. A local controlled
   drill (2026-11-27) invoked the same handler path with an in-flight DOCX job:
   one claim, then no further claims; the in-flight job published a valid ZIP
   (`succeeded`) and a second queued job stayed `queued` with no result.
   Delivery of a real POSIX SIGTERM to the container on target hardware is
   still an operator gate.
- **Crash recovery (P0.2):** `staging` rows carry a renewable
   `STAGING_LEASE_SECONDS` (default 60) lease, refreshed every 4 MiB while a
   large upload is copied. Worker maintenance/claim cycles atomically claim
   only expired staging rows (`failed`, `staging_abandoned`) and expired
   `cancelling` rows (`cancelled`), then clean their artifacts idempotently.
   Live publishers cannot publish after their staging row was recovered; a
   `cancelling` job is never selected as ordinary work. The local crash-window
   drill (2026-11-27) verified all five windows against a disposable database:
   abandoned staging → `failed/staging_abandoned` with no artifact bytes;
   committed metadata → restart completes; lease-lost claim → stale publish,
   fail and heartbeat all rejected while the new owner succeeds at attempt 2;
   ENOSPC at ZIP creation → queued with no partial result then clean retry;
   result bytes written before the fenced commit → stale key rejected and
   removed, only the current attempt key published. Details:
   `back/benchmarks/operational-drill-results.json`.

### Backpressure and capacity budget (2026-11-27)

Defaults are measurements, not guesses (raw numbers and method in
`docs/PERFORMANCE.md` §Concurrency and queue budget):

- **Worker concurrency.** Peak process RSS on a benign 16-document synthetic
  DOCX load was flat at `431 / 431 / 433 / 433 MB` at `JOBS_CONCURRENCY`
  `2 / 4 / 6 / 8`; the conservative per-worker share (peak minus the 109 MB
  baseline, divided by workers) was `159 / 80 / 53 / 40 MB`. We take the largest
  share (**159 MB**), a documented **1 GiB** worker-container budget (no
  `mem_limit` in Compose — UNMEASURED) and a **1.5** safety factor:
  `floor(1024 / (159 × 1.5)) = 4`. Higher concurrency is not enabled without
  target-host RSS evidence even though the proxy run stayed flat through 8.
- **Queue capacity.** A measured burst of **16** distinct submissions was
  accepted over ~5.9 s (~2.7 jobs/s) before the first claim; p95 per-document
  latency at concurrency 4 was **6.2 s**. Capacity is `2 × 16 = 32` (100%
  headroom over the observed burst, and `> concurrency`), bounded — never an
  unbounded queue.
- **Retry hint.** A full queue drains its `32 − 4 = 28` backlog slots in
  `28 × 6.2 / 4 ≈ 43.4 s`, rounded up to **45 s** (`JOBS_RETRY_AFTER_SECONDS`).
  Operators changing capacity, concurrency or latency must update this setting,
  the two alert thresholds and the dashboard lines together.

- **Sustained load (2026-11-27):** a 1800.5 s soak at concurrency 4 on the
  disposable stand completed 7,824 synthetic DOCX jobs with 0 failures; the
  Windows working-set proxy showed no unbounded growth, but its ~15 GB plateau
  is a *harness-process* figure on this host, not worker-container RSS. The
  1 GiB budget assumption behind the default remains **unvalidated on target
  hardware**; raw samples and the honest caveats are in
  `docs/PERFORMANCE.md` §Concurrency and queue budget.

## 6. Runbooks

Each entry lists symptoms, cheap checks and the safe action. Escalate anything
involving real PII or credentials to the maintainer; never paste input text,
keys or exception messages into tickets or chat.

### PostgreSQL unavailable

- **Symptoms:** `/ready` returns 503 with the fixed Russian reason; `/metrics`
  shows `active_jobs: null`; job submissions/status return a safe bounded error,
  not a hung request.
- **Checks:** `docker compose ps postgres`, compose healthcheck,
  `GET /ready`; inspect only the exception *type* in logs.
- **Action:** restore the stand with `just db-stand` (waits for health), verify
  `just seed` / `just migrate`, then restart the app stack (`just prod` or
  `just dev`). Jobs left `running` are reclaimed by lease expiry with bounded
  attempts; do not manually edit rows. A PostgreSQL restart no longer kills the worker: poll/heartbeat DB errors are caught and retried (Worker._claim_safe / Heartbeat._run, Phase 8 stress finding), so the worker stays up and lease fencing recovers in-flight jobs. If data is missing, follow §3 restore.

### Valkey unavailable / corrupt cache

- **Symptoms:** `/metrics` cache counters are zeroed or failures rise, but
  mask/unmask still work; no 500s.
- **Checks:** `docker compose ps valkey`, its healthcheck; confirm
  `CACHE_ENABLED` / `CACHE_KEY_SECRET` unchanged.
- **Action:** `docker compose restart valkey`. Flushing the cache is safe
  (`docker compose exec valkey valkey-cli -a "$VALKEY_PASSWORD" FLUSHDB`) — it
  is disposable and will rebuild. A missing/corrupt artifact is treated as a
  cache miss and evicted (`back/src/jobs/cache.py`). Keep the version settings
  unchanged unless output semantics actually changed.

### Cache artifact GC

- Cache artifacts are immutable, content-addressed files in the private `cache/`
  spool; Valkey stores only compact references. Valkey TTL/LRU eviction never
  triggers a delete by itself.
- The worker runs a bounded collection pass every `CACHE_GC_INTERVAL_SECONDS`
  (default 900 s, must be positive). Retention is `max(CACHE_TTL_SECONDS, 24 h)
  + 1 h`; an artifact is deleted only when its mtime is older than that. Reuse
  (hit or re-publish of the same hash) refreshes mtime before the Valkey key is
  published, and the publisher verifies the file after `SET` — a live key never
  points at a deleted artifact. Selection uses `stat` only (no file contents in
  memory), re-checks mtime atomically before unlink, and is idempotent.
- A corrupt/missing artifact or hostile `artifact_id` is a cache miss plus
  eviction, never an operation failure. Valkey/disk errors are logged by type
  and the pass is skipped; masking and jobs continue.
- Safe aggregates: `/metrics` JSON `cache.gc_candidates`, `gc_deleted`,
  `gc_reclaimed_bytes`, `gc_failures`, plus the worker Prometheus counters
  `dm_cache_gc_*` / `dm_cache_operations_total{operation="gc"}`. No artifact
  ids, keys or payloads are exposed.

### Disk / artifact volume full

- **Symptoms:** failed artifact publication, job failures with a safe
  `artifact_io`/internal code, worker errors by type only, container write
  errors.
- **Checks:** host filesystem usage, `docker system df -v`, volume usage for
  `dm-jobs-data`; confirm TTL sweeps are running.
- **Action:** stop claims first (`docker compose stop worker`), let/free disk
  space by archiving old **terminal** artifacts through the normal TTL/GC or a
  consistent backup (§3) — never delete `<job_id>.*` files for active jobs.
  Restart with `just prod`/`just dev` and verify `GET /metrics` and a test job.
  Local simulated ENOSPC (2026-11-27) failed staging with `staging_failed` and
  zero leftover input files, and failed ZIP creation with no published result;
  retries succeeded after the injected error was removed. A real filesystem
  full event on target hardware is still an operator gate.

### Stuck, cancelled or expired job

- **Checks:** `GET /api/jobs/{id}` (`status`, `phase`, `attempt`,
  `cancel_requested`, safe `error_code`); `GET /metrics` active count.
- **Action:** `DELETE /api/jobs/{id}` requests cancellation — polling stops
  only locally, the server acknowledges at a checkpoint. A lease-expired job is
  reclaimed by another worker or fails at `JOBS_MAX_ATTEMPTS`. An expired result
  returns 404; resubmit. Do not touch DB rows or spool files by hand. The local
  expiry drill (2026-11-27) swept 2 terminal rows and their result bytes while
  leaving the active row `queued`; it counted 1 referenced-but-missing artifact
  before the sweep and 1 unreferenced orphan file after it. GC is row-driven:
  orphan bytes are **reported, not deleted** — investigate them separately.

### Corrupt/hostile DOCX

- **Symptoms:** 413/415 from upload validation or 422 from the OPC/XML
  transformer, with a fixed Russian `detail` and a stable error code
  (`invalid_package`, `zip_bomb`, `encrypted_package`, `unsafe_xml`,
  `unsupported_package`, …).
- **Action:** this is the intended fail-closed behavior; ask the submitter for a
  valid file. There is no partial-result mode and no cache entry to clean.
  Response bodies must never be widened with filenames or exception text
  (`docs/SECURITY.md` §2).

### Worker crash / restart

- **Symptoms:** worker container restarted or exited; jobs stay `running`
  briefly until lease expiry; no duplicate result publication.
- **Checks:** `docker compose ps worker`, `GET /metrics`, job statuses;
  `back/src/jobs/worker.py` drain and heartbeat logs.
- **Action:** `docker compose restart worker` (or `just prod`/`just dev` to
  rebuild). Lease recovery reassigns work with a fresh fencing token. Verify
  a round-trip job after restart. A controlled local restart/fencing drill
  (2026-11-27) covered the five crash windows and the handler-equivalent SIGTERM
  drain; it proved no stale/partial publication and no cross-job artifact
  deletion. Real container SIGTERM and crash stress on target hardware are
  operator gates — do not claim they were exercised here.

### Migration failure

- **Action:** stop writes to the app stack, inspect `alembic current` and the
  migration in `back/migrations/versions/`; do not delete volumes or downgrade
  blindly. If a restore is needed, follow §3 and re-run `just migrate`. Never
  let multiple API replicas race to migrate at startup (ADR-0003, Brief §2.2).

### Log handling

- Container logs are plain text today (`back/src/main.py` basicConfig); capture
  with `docker compose logs --no-color` (CI uploads them as an artifact). Do
  not paste them into public channels — they can contain opaque ids and safe
  codes, and must never contain PII. Rotation/retention and structured logs
  with correlation ids are `TODO.md`, "Old stage 6 (deferred)" (`docs/SECURITY.md` §2).

## 7. Not yet operationalised (TODO, stage 6)

Reverse-proxy/TLS/auth example and rate limits; container hardening and
internal-only backend network; `/ready` and front readiness wired into
healthchecks; log rotation and long-term metric retention policy;
`stop_grace_period` tuning and a live POSIX SIGTERM drill; clean-install/prod-image
verification and job lifecycle stress on target hardware (`TODO.md`,
`docs/TESTING.md`). Local disposable drills (2026-11-27) now cover the crash
matrix, handler-equivalent SIGTERM drain, simulated ENOSPC, 1-slot DB pool
exhaustion, result expiry/byte audit and 1/2/N synthetic DOCX batches; a real
full filesystem, real SIGTERM, target hardware and restoring the real private
config/volumes remain **OPEN** gates (`docs/PRODUCTION_READINESS.md`). Branch
protection / the `CI required` check is a GitHub repository setting — the
repository only documents the checklist (`docs/WORKFLOW.md`).

## 8. Monitoring stack (startup, modes, runbooks)

The stack lives in `infra/compose.monitoring.yaml` (included from the root
`compose.yaml`) and is independent of the app profiles. Architecture and the
full variable contract are in `docs/ARCHITECTURE.md` ("Observability /
monitoring stack"); this section is the operational half.

### Startup and ports

```sh
just monitoring        # Alloy only — logs to an external/central Loki
just monitoring-stand  # Loki + Grafana + Alloy + Prometheus + Alertmanager + relay
```

`LOKI_URL`, `GF_SECURITY_ADMIN_USER` and `GF_SECURITY_ADMIN_PASSWORD` are
mandatory (`:?` in compose). Host ports: Alloy UI **12345**, Loki **3100**,
Grafana **3000**, Prometheus **9090**, Alertmanager **9093** — all published on
`127.0.0.1` only. The SMTP relay is internal only (port 25 inside `dm-net`).
`infra/check-monitoring-binds.py` runs in CI and rejects host-wide bindings for
internal/admin services; to expose a dashboard, put an authenticated reverse
proxy in front of the loopback port rather than widening the Compose binding.
These are the same ports the other two projects use — run one project's full
stand at a time, or remap host ports in a local override (the container ports
stay fixed). External/central Loki mode (`just monitoring`) is unchanged.

Prometheus targets are rendered at container start from `.env`; for the app in
this same compose set `BACKEND_METRICS_HOST=back` and
`WORKER_METRICS_HOST=worker` (prod `back` is published on loopback only, so
`host.docker.internal` cannot see it). For an external app/server set both to
`host.docker.internal` or the central host; `WORKER_METRICS_PORT=8002` by
default. There is no Pushgateway — the worker is scraped.

### Retention and volumes

Prometheus keeps 15 days of raw samples (`PROMETHEUS_RETENTION`); Loki keeps
168h (`LOKI_RETENTION`, expanded with `-config.expand-env=true`). Volumes:
`dm-loki-data`, `dm-prometheus-data`, `dm-grafana-data`,
`dm-alertmanager-data` — all private. Monitoring data is disposable relative
to `dm-postgres-data`/`dm-jobs-data`: losing it loses history, not job state.

### Self-monitoring and checks

Prometheus scrapes `prometheus`, `loki`, `alertmanager` and `grafana` in
addition to `docmasker-back`/`docmasker-worker`; rules for all five live in
`docmasker-stack` / the `up` alerts of `infra/prometheus/alert-rules.yml`.
Health checks after a change:

```sh
docker compose --profile prod --profile dev config --quiet
docker compose -f infra/compose.ci.yaml config --quiet
docker compose exec prometheus promtool check config /etc/prometheus/alert-rules.yml
curl -s http://localhost:9090/api/v1/targets   # all jobs up
curl -s http://localhost:3000/api/health
```

Grafana dashboards (folder `DocMasker`): «Рабочая очередь», «Ошибки API»,
«Кэш», «Диск артефактов». The worker dashboard has two additional panels —
queue depth against the `JOBS_MAX_ACTIVE` line (24 = 75% warning threshold, 32 =
capacity) and worker saturation against `JOBS_CONCURRENCY` (4) with the backlog
series; both reuse `dm_jobs_active` / `dm_jobs_running` and constant lines, with
no new metric. The alerts `DocMaskerWorkerQueueNearCapacity` (24 active, 2m) and
`DocMaskerWorkerSaturated` (all 4 slots busy with backlog, 5m) are PROVISIONAL
and must be re-derived when those settings change. The disk dashboard includes a `JOBS_MIN_FREE_BYTES`
threshold line next to `dm_artifact_disk_free_bytes`; the
`DocMaskerArtifactStorageBelowReadinessFloor` alert uses the same 1 GiB default
and the backend's `dm_runtime_disk_free_bytes{storage="jobs"}` as a second
signal. Admission and worker startup reject a full/read-only spool with HTTP
503 `artifact_storage_unavailable` (Russian `detail`); no new metric or log
field is introduced — the failure is visible as a 503 in
`dm_http_requests_total` and as a structured `error_code` event. If
`JOBS_MIN_FREE_BYTES` is overridden, update both the alert expression and the
dashboard threshold in the same change. Alert annotations name the relevant
dashboard and say where the `request_id`/`job_id` is: in Loki structured
metadata, never in a metric label. Thresholds marked PROVISIONAL need
calibration before they are treated as SLOs.

### Verification drill (2026-11-27, local disposable only)

A Granian backend was started on a disposable database and spool
(`JOBS_MAX_ACTIVE=5`, `JOBS_CONCURRENCY=4`) on loopback port 18101. Five distinct
text submissions returned `202`; the sixth returned the unchanged typed
envelope with HTTP `429`, Russian detail, matching `X-Request-ID` and the new
`Retry-After: 45` header; `Cache-Control: no-store` / `Pragma: no-cache` were
preserved. No server-side retry or silent drop occurred. Rule syntax was checked
with `promtool check rules` (20 rules, SUCCESS). **Not executed:** the staging
Alertmanager delivery drill for `DocMaskerWorkerQueueNearCapacity` and
`DocMaskerWorkerSaturated` (firing both alerts on the staging Prometheus stand
and confirming the Russian e-mail via the SMTP relay) — no staging stand or
credentials were available in this environment. The alerts remain PROVISIONAL
thresholds validated only by `promtool` and the live expression review.

### Alert mail and the SMTP deviation

`infra/alertmanager/entrypoint.sh` renders recipient/sender/smarthost from
`SUPPORT_MAIL`/`ALERT_SMTP_*` at container start, so `.env` edits require
`just monitoring-stand` again. **Deviation:** Alertmanager → our own
`dm-smtp-relay` is plain SMTP on port 25 (`smtp_require_tls: false`); the relay
→ corporate SMTP uses STARTTLS on 587 with `SMTP_RELAY_*` credentials. Port
465 (implicit TLS) cannot be used with `smtp_require_tls`. When the relay has
no credentials, Alertmanager sends unauthenticated to it and the relay owns
upstream auth; empty `ALERT_SMTP_USER`/`PASSWORD` remove the auth lines from
the rendered config.

If mail does not arrive: check `localhost:9093` (alerts are still visible
there), then `docker compose logs dm-alertmanager` for `Notify success` and
`docker compose logs dm-smtp-relay` for the upstream SMTP error — never put
credentials or PII into a ticket. Stopping Grafana/Prometheus/Loki must not
stop `just prod`/`just dev`: the workload writes files and local metrics and
degrades on emission failures by exception type only.

### External/central mode verification

Set `LOKI_URL` to the central endpoint (e.g.
`http://host.docker.internal:3100`), `LOG_ENV=prod`, run `just monitoring`,
exercise a masking request and a job, and confirm the entries appear in the
central Grafana with `project="docmasker"` and the expected `env` label plus
`request_id`/`job_id` metadata. Metrics/alerting in that mode belong to the
central Prometheus/Alertmanager; the local stand is not started.
