# ADR-0003: PostgreSQL job foundation (Phase 1 design)

- Status: **accepted** (Phase 0 design 2026-11-24; Phase 1 implemented and
  verified the same day)
- Implements: `docs/DocMasker_Production_Readiness_Implementation_Brief.md`
  §2 (PostgreSQL migration/infrastructure), §3.1–3.4 (job record, transitions,
  atomic enqueue, `SKIP LOCKED` claim), §13 Phase 0/1
- Supersedes: ADR-0002's **SQLite** decision (the durable-job *concept* stays;
  the substrate changes). ADR-0002 remains the record of the 2026-11-23
  decisions and must be marked superseded in the same change as the migration.

## Context

The new implementation brief (baseline audit `dev`, 2026-11-24) makes
PostgreSQL the authoritative durable source of truth for jobs and adds Valkey
as a disposable result-cache index. The current stack (ADR-0002) uses a SQLite
WAL file on the private `jobs-data` volume, shared by `back` and `worker`.
Phase 1 replaces that foundation; Phase 2+ builds the full lifecycle on top.

Baseline facts verified on this host (2026-11-24):

- `dev` container `dm-back-dev` holds `/app/jobs/jobs.sqlite3` (20 KB, table
  `jobs`, **0 rows**). No live jobs/results to migrate; the volume also holds
  no spooled artifacts. A documented quiesce/reset procedure is therefore
  sufficient (§13 Phase 0 exit). Never delete the volume before that procedure
  is approved — it is a live mounted volume.
- ReportCollector **is accessible** at `D:\Work\ReportCollector`. Inspected
  (§0 requirement): `back/src/common/job_repository.py` (async SQLAlchemy
  `SKIP LOCKED` claim, `uuid4()` lease token, `_owns()` fencing guard,
  `clock_timestamp()` server time, bounded retry with exponential
  `available_at`), `migrations/versions/0026_durable_data_jobs.py` (Alembic
  `data_jobs` with `lease_token`/`lease_until`/`cache_key`/`artifact`),
  `common/durable_imports.py` (independent `asyncio` heartbeat task),
  `infra/compose.db.yaml` + `infra/compose.ci.yaml` (postgres:18-alpine,
  valkey/valkey, healthchecks, private CI ports).

### Patterns reused from ReportCollector (and deliberate changes)

| Pattern | Reuse | Change for DocMasker |
|---|---|---|
| Alembic versioned migrations, `data_jobs` shape | yes | New `jobs` table with the richer §3.1 record (`staging`, `cancelling`, queue/result TTL, artifact IDs, cache key); no `create_all()` |
| `SELECT … FOR UPDATE SKIP LOCKED` single-transaction claim | yes | DocMasker needs `staging` reconciliation and lease-expiry reclaim inside the same short transaction (RC retries stale rows in a separate step) |
| `uuid4()` lease token + `_owns(worker_id, token, lease_until > now)` | yes | Phase 2 will add worker_id + independent heartbeat; Phase 1 only needs the columns + guarded primitives |
| `clock_timestamp()` for DB-authoritative time | yes | Avoids app-clock skew across API/worker containers |
| Async SQLAlchemy session per operation | **no** | DocMasker's worker is a **thread pool** and the sync endpoints use `run_cpu`; choose sync SQLAlchemy 2 + psycopg3 behind one bounded DB executor, per brief §2.1's explicitly allowed alternative |
| RC's `postgres`/`valkey` compose services + healthchecks | yes | DocMasker keeps them **private** (no published host ports in prod), CI-only loopback ports; Valkey gets an ACL/password boundary |
| RC's JSONB `payload` with user data | no | §3.1 forbids PII/keys/filenames in job rows: artifact references only, in private staged files |

## Decision (Phase 1 scope)

### Stack

- **SQLAlchemy 2.x (sync Core/ORM) + psycopg 3 + Alembic** — one driver, one
  process-wide `QueuePool` (`DB_POOL_SIZE` + `DB_MAX_OVERFLOW`), a bounded
  dedicated `ThreadPoolExecutor` for all DB calls from async handlers
  (`run_db`, mirroring `run_cpu`). Worker threads use the same sync repository
  directly. No asyncpg, no SQLModel, no second driver. Recorded as the brief's
  allowed equivalent to the async baseline.
- **PostgreSQL 18** (`postgres:18-alpine`) and **Valkey 8**
  (`valkey/valkey:8-alpine`) as Compose services for prod, dev and CI.
- **Artifacts stay on a private shared volume** (filesystem spool). PostgreSQL
  coordinates workers; the volume is the storage boundary. Multi-host
  horizontal scaling needs an object-store adapter — explicitly out of scope
  (§1 single-host boundary). No PostgreSQL bytea blobs for DOCX/ZIP.

### Schema (`jobs`, aligned with §3.1)

```
id                  UUID PK (uuid4, opaque >=128-bit)
kind                TEXT NOT NULL CHECK (mask_text|mask_file|unmask_text|unmask_file)
status              TEXT NOT NULL CHECK (staging|queued|running|cancelling|
                      succeeded|failed|cancelled|expired)
phase               TEXT NOT NULL
progress            INTEGER NOT NULL DEFAULT 0
attempt             INTEGER NOT NULL DEFAULT 0
max_attempts        INTEGER NOT NULL
worker_id           TEXT
lease_token         UUID
lease_until         TIMESTAMPTZ
heartbeat_at        TIMESTAMPTZ
started_at          TIMESTAMPTZ
finished_at         TIMESTAMPTZ
created_at          TIMESTAMPTZ NOT NULL
updated_at          TIMESTAMPTZ NOT NULL
cancel_requested_at TIMESTAMPTZ
queue_expires_at    TIMESTAMPTZ
result_expires_at   TIMESTAMPTZ
input_artifact      TEXT          -- opaque id, never a filename
keys_artifact       TEXT
result_artifact     TEXT
cache_key           TEXT
error_code          TEXT          -- safe code only
next_attempt_at     TIMESTAMPTZ
```

Indexes: `ix_jobs_queue (status, created_at)` for claims,
`ix_jobs_lease (status, lease_until)` for reclaim,
`ix_jobs_expiry (result_expires_at)` for GC. No PII, keys YAML, entity values,
raw exception text or user filenames in any column.

Transitions are enforced **centrally** in the repository (`ALLOWED` map,
§3.2); every mutation is `UPDATE … WHERE id=… AND <ownership guard>` and checks
the affected-row count. Stale workers cannot transition anything.

### Phase 1 primitives (contract suite against real PostgreSQL)

- `create_staging(kind, max_active)` — short transaction, counts
  staging+queued+running+cancelling as active (queue capacity), inserts a
  `staging` row with a fresh `cache_key`; raises `JobQueueFullError` → 429.
- `publish_staging(id, artifacts)` — `staging → queued` **only after** the
  input/keys artifacts are durable; attaches artifact IDs.
- `abort_staging(id)` — `staging → failed` + idempotent cleanup.
- `claim(worker_id, lease_seconds)` — one transaction: reclaim expired leases
  (new token, incremented attempt, `worker_lost`/`attempts_exhausted`),
  then `SELECT … FOR UPDATE SKIP LOCKED` the oldest eligible `queued` row
  (never `staging`), set `running`, fresh `lease_token`, `attempt += 1`.
- `heartbeat(id, worker_id, token)` and `succeed/fail/cancel` — ownership-
  guarded, affected-row checked.
- `get(id)` — safe projection only.

### Out of scope for Phase 1 (later phases)

Phase 2: independent heartbeat loop, cooperative cancellation checkpoints,
error classification/backoff, TTl/GC, immutable result publication, graceful
drain, frontend cancel/resume. Phase 3: Valkey cache. Phase 4+: config/DOCX/
API/perf/docs. **Phase 1 must not break the existing sync mask/unmask path or
the frontend** — the old SQLite repository is removed only after the new
contract suite passes against real PostgreSQL, in the same migration phase.

### Migration / backup notes

- Empty-database Alembic migration smoke test is required; CI runs it.
- This host's SQLite `jobs` table is **empty** (0 rows), so there is no data
  migration. Procedure: quiesce the dev stack (`docker compose --profile dev
  down`), keep `dm-jobs-data` as a backup until PostgreSQL is verified, then
  remove the obsolete volume with an explicit `down --volumes` on the *new*
  stack — never a blind `rm`. Production deployments with live rows need a
  documented data-migration step before this ADR is applied there.
- PostgreSQL is backed up with `pg_dump`; artifact volume is backed up
  consistently with the DB references (a DB backup without artifacts is not a
  recovery point). Valkey is not business data. Restore order: DB → artifacts.

## Consequences

Positive: transactional `SKIP LOCKED` claims and fencing work across multiple
worker processes; `staging` removes the enqueue/claim race; DB-authoritative
time removes clock skew; the same repository serves sync and job paths.

Accepted costs: one more Compose service pair and a DB executor; sync
SQLAlchemy needs a bounded executor on the ASGI side; the shared filesystem
remains a single-host constraint; ADR-0002's SQLite text becomes historical.

## Definition of done (Phase 1 exit, §13)

- [x] PostgreSQL + Valkey Compose services (prod/dev/CI), private in prod,
      healthcheck-gated startup, placeholder credentials in `.env.example`,
      startup validation of numeric settings and `DATABASE_URL`.
- [x] Alembic migration `0001_jobs` creating the `jobs` table above; empty-DB
      smoke test (CI `alembic upgrade head` + isolated prod-image stack).
- [x] New sync SQLAlchemy repository with the primitives above; no
      `sqlite3`, `jobs.sqlite3`, `journal_mode`, `JOBS_DB_PATH` references
      remain in code/config.
- [x] Integration tests against real PostgreSQL: CRUD, queue capacity,
      concurrent `SKIP LOCKED` claim, restart, staging reconciliation, lease
      reclaim (`TEST_DATABASE_URL`; 180 tests, 38 skip offline).
- [x] Migration/backup notes in this ADR; ADR-0002 marked superseded;
      `AGENTS.md`/`docs/*` updated in the same change.
- [x] Existing sync endpoints + e2e smoke green on the live dev stack and on
      the isolated CI production-image stack (22/22 each, incl. the 202 job
      path through the PostgreSQL-backed worker).

**Implemented in Phase 6 (2026-11-24):** API contracts, timeouts, streaming
and event-loop safety. One stable error envelope (`{code, detail, request_id}`
+ `X-Request-ID`) with sanitized handlers; canonical `timeouts.ts` route map
(15 s JSON/job, 120 s upload, 300 s result); Next streams multipart unchanged,
backend stages uploads with a real byte counter + path validation and streams
results via `FileResponse`; `run_cpu`/`run_blocking` separate bounded pools keep
DB/ZIP/XML/filesystem work off the ASGI loop; `GET /ready` (PostgreSQL) +
`GET /metrics` and Pydantic cross-field validators. §8.2 OpenAPI→TS types
deferred by maintainer decision (`TODO.md`).

**Implemented in Phase 5 (2026-11-24):** full OPC-aware OOXML transformer
(`back/src/masking/ooxml/`), replacing the python-docx run-level traversal.
All text-bearing parts are located through `[Content_Types].xml` + relationship
URIs, parsed securely with `defusedxml.lxml`, rewritten at the text-node level,
and unchanged ZIP entries are copied byte-for-byte. Strict 422 rejection for
uninspectable embedded objects/unsafe XML; warning-only headers removed. One
versioned `manifest.json` (statistics, coverage, versions, cache) is produced
by sync and job file paths; the frontend requires and validates it. Text jobs
return the exact canonical DTO. `docs/DOCX_SUPPORT.md` documents coverage.

**Implemented in Phase 4 (2026-11-24):** one immutable masking configuration.
`back/config/masking/labels.yaml` is the canonical label catalogue (sequence
= output order, no `order` field), published as `GET /api/labels`; the frontend
consumes a generated shared TS file (`front/scripts/gen-labels.mjs`, CI checks
freshness). All curated lexicons and `always_mask`/`never_mask` exceptions live
in typed YAML loaded into one validated snapshot at startup
(`back/src/masking/config.py`); a non-reversible digest feeds the cache key,
so any exception change invalidates cached results. The profile abstraction,
`profile_id`, `GET /api/profiles` and `LabelMeta.order` were removed; the keys
`Профиль` marker is retained as ignored legacy metadata (§5.2).

**Implemented in Phase 3 (2026-11-24):** Valkey result cache. Keys are
`docmasker:result:v{CACHE_NAMESPACE_VERSION}:{operation}:{HMAC-SHA256}` over a
canonical fingerprint (exact input/keys bytes, normalized output filename,
`MASKING_RULESET_VERSION`, `MASKING_CONFIG_DIGEST`, `RESULT_MANIFEST_VERSION`,
`KEYS_FORMAT_VERSION`, `DOCX_TRANSFORMER_VERSION`, engine/entry versions) with a
stable `CACHE_KEY_SECRET` — no raw hashes/PII in key names or logs. Valkey holds
only compact JSON entries pointing to immutable, hash-verified artifacts in the
private volume; cache outages degrade to misses; writes happen only after the
fenced `succeeded` transition; identical active jobs dedupe in PostgreSQL.
Sync and job paths both use the cache. Bump the version settings (release
checklist in `back/src/jobs/cache.py`) whenever output semantics change.

**Implemented in Phase 2 (2026-11-24):** independent heartbeat loop
(`lease_seconds / 3`), cooperative cancellation checkpoints + `cancelling`
state, classified retry with bounded exponential backoff, active-job-safe
TTL/GC, immutable fenced result publication, graceful SIGTERM drain, frontend
Cancel (replaces the file button) + session-scoped resume. Failure-injection
tests cover stale fencing, cancellation without publication, long blocking
operations across lease intervals, queue-full staging cleanup and active-row
retention. Target-hardware crash/restart stress remains a Phase 8 item.
