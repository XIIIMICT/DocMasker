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
- **`GET /metrics` — coarse safe counters only**, e.g.
  `{"active_jobs":0,"cache":{"hits":3,"misses":1,"writes":1,"failures":0,"evictions":0}}`.
  A DB outage degrades to `active_jobs: null` and a cache outage to zeroed
  counters; neither returns 500 (`back/src/main.py`,
  `test_readiness.py::test_metrics_are_coarse_and_db_outage_degrades`).
  No ids, inputs, keys or key names are exposed (`docs/SECURITY.md`).
- Full metrics (latency, queue depth/age, lease losses, retries, job durations,
  DB pool saturation) and structured JSON logs remain deferred (`TODO.md`).

## 5. Job lifecycle operations

- **Queue limits:** `JOBS_MAX_ACTIVE` counts `staging|queued|running|cancelling`;
  over capacity returns 429 and leaves no staging row (`back/src/jobs/AGENTS.md`,
  `test_jobs_store.py::test_queue_full_leaves_no_staging_row`,
  `test_jobs_api.py::test_queue_full_returns_429`).
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
  thread was killed (`back/src/jobs/worker.py`, `WORKER_SHUTDOWN_GRACE_SECONDS=30`
  in `.env.example`). Forced termination is recovered by lease expiry.
  Compose currently has no explicit `stop_grace_period`; tuning it and a live
  restart drill are `TODO.md` items.

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

### Stuck, cancelled or expired job

- **Checks:** `GET /api/jobs/{id}` (`status`, `phase`, `attempt`,
  `cancel_requested`, safe `error_code`); `GET /metrics` active count.
- **Action:** `DELETE /api/jobs/{id}` requests cancellation — polling stops
  only locally, the server acknowledges at a checkpoint. A lease-expired job is
  reclaimed by another worker or fails at `JOBS_MAX_ATTEMPTS`. An expired result
  returns 404; resubmit. Do not touch DB rows or spool files by hand.

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
  a round-trip job after restart. Live crash/restart stress on target hardware
  is still TODO — do not claim it was exercised here.

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
healthchecks; full metrics and structured logs; log rotation; `stop_grace_period`
tuning and a live SIGTERM drill; clean-install/prod-image verification and job
lifecycle stress on target hardware (`TODO.md`, `docs/TESTING.md`). Branch
protection / the `CI required` check is a GitHub repository setting — the
repository only documents the checklist (`docs/WORKFLOW.md`).
