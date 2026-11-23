# ADR-0002: Durable job execution for large documents

- Status: accepted (2026-11-23)
- Stage: production-readiness stage 4 (findings F-01, F-02, F-07, F-08 / request §6)
- Related: ADR-0001 (profiles), `docs/PRODUCTION_READINESS.md` baseline

## Context

Stage-1 measurements on the actual stack:

| Signal | Measured |
|---|---|
| Event loop blocked by a 36 KB `/api/mask` | `/health` latency 2–3 ms → **5,353 ms** (live Granian) |
| `detect()` scaling | 36 KB ≈ 5.4 s; 360 KB ≈ **92.8 s and 7.3 GB RSS** (killed) |
| Hotspot | 91% of detect = natasha/yargy `AddrExtractor` chart parsing, no windowing |
| DOCX 5,000 paragraphs | **115 s** in one HTTP request, event loop blocked |
| Buffering | 8 full copies of the payload along browser → Next → FastAPI → docx → ZIP → browser (F-07) |

Current transport assumptions that make long work fragile:

- `front/src/instrumentation.ts` globally disables undici `headersTimeout`/`bodyTimeout` (`0`) for **every** server-side fetch.
- All four proxy routes declare `maxDuration = 3600`.
- Backend endpoints are `async def` but call synchronous `natasha`/`phonenumbers`/`python-docx`/ZIP work directly in the event loop (F-01).
- One HTTP connection must survive the whole operation; a browser tab close, proxy restart or network blip loses everything.

Deployment shape: single-host Docker Compose (`compose.yaml`), no database, no auth boundary, one backend instance, one frontend. The product is single-tenant/private (ADR-0001).

Reference implementation (maintainer pointed at `../ReportCollector`): durable jobs on PostgreSQL + SQLAlchemy, a lease/heartbeat `JobRepository`, bounded queue with `MAX_ACTIVE_JOBS`, `202 Accepted` + status endpoint, retry with exponential backoff, TTL cleanup, and **process isolation** (`asyncio.create_subprocess_exec(sys.executable, -m src.scripts.import_worker, job_id, lease_token)`) with stdout JSON progress and a lease-ownership watchdog. Its 24 h proxy timeouts serve hour-long exports. That setup is proven but drags in Postgres + Redis for a deployment that has neither.

Constraints from the request:

- Do NOT use FastAPI `BackgroundTasks` as a queue; do NOT add Kafka/K8s/a DB just in case; prefer a modular monolith.
- CPU work must leave the event loop; bound concurrency; do not ship PII/keys in queue payloads, logs, metrics or headers.
- Job state must survive a web-process restart; results have TTL and cleanup; backpressure via 429/503.
- Keep the synchronous path for small jobs.
- Replace global unbounded timeouts with job-specific bounded ones.

## Options considered

### A. Durable metadata substrate

1. **SQLite (WAL) file on a private volume + a single worker process.**
   Restart-safe, zero new services, stdlib `sqlite3`, transactional claim with
   `BEGIN IMMEDIATE`, leases as timestamps, TTL sweeps in the worker loop.
   Fits the single-host deployment and the request's "no DB without ADR" rule.
2. **PostgreSQL + Redis (ReportCollector-style).** Proven at scale, supports
   multi-instance workers; requires new services, migrations, secrets, ops.
   Over-engineered for one host without a measured need.
3. **File-based JSON job records + flock.** Simpler than SQLite but races under
   concurrent claims and has no transactional atomicity — rejected.

### B. Worker form

1. **One long-lived worker process (separate container) with a bounded
   internal thread pool, warmed `MorphVocab` per worker, polling SQLite.**
   Avoids the seconds-long cold start and the ~170 MB dictionary per job;
   natasha extractors are not proven thread-safe, so each thread owns its own
   masker instance (bounded by `JOB_CONCURRENCY`, memory budget accounted).
2. **Process-per-job (ReportCollector-style subprocess).** Strong isolation
   and clean cancellation, but pays interpreter + MorphVocab startup per job
   and multiplies RSS under concurrency; acceptable for hour-long exports,
   wasteful for ~2-minute DOCX jobs.
3. **In-process thread pool inside the web process.** No restart durability
   (jobs die with the web worker) — rejected for the job path; still useful for
   the synchronous path's bounded executor.

### C. Sync threshold and CPU isolation (both paths)

- Synchronous endpoints: `asyncio.to_thread`-style offload through a bounded
  executor (or `run_in_executor`) with a semaphore; exceed the bound → 429/503.
- Threshold by measured size/complexity, configurable: text chars / DOCX bytes
  / estimated paragraph count. Baseline suggests small texts (≤ a few KB) and
  small DOCX (≤ a few hundred paragraphs) stay synchronous; larger ones become
  jobs. The exact numbers are a decision with the maintainer (benchmark-backed).

### D. Streaming and timeouts

- Next proxy: keep `request.formData()` buffering for the synchronous path
  (ReportCollector found re-streaming loses multipart parts in Next dev), but
  forward the backend response as a Web Stream (`new Response(response.body)`)
  instead of `arrayBuffer()`; propagate status, content-type, cancellation.
- Replace the global `headersTimeout: 0`/`bodyTimeout: 0` dispatcher with
  per-route bounded values (short for JSON job endpoints, longer but finite
  for the synchronous file path); remove `maxDuration = 3600` from short
  routes; jobs use polling/SSE, not a held-open upload connection.
- Backend: enforce request-size limits before reading (already has 413 after
  `await file.read()` — move to a streaming byte counter in a later step).

## Decision (proposed; open points below)

1. **SQLite WAL job store** (`back/src/jobs/`) + **one dedicated worker
   container** (`dm-worker`, same image, different CMD) polling it. Job record:
   `id` (opaque uuid4), `kind`, `status` (`queued|running|succeeded|failed|
   cancelled|expired`), `phase`, `progress`, `attempt`, `lease_until`,
   `created_at`, `expires_at`, `error_code`, `result_key`. No PII/keys in the
   record or logs; input/output live as files on a private volume keyed by
   `job_id`, never in the DB rows.
2. **Job API** (prefix `/api/jobs`):
   - `POST /api/jobs` → `202` + `job_id` (input as multipart or JSON text,
     plus `profile_id`); payload written to a spooled private file first.
   - `GET /api/jobs/{id}` → status/progress/phase/safe error.
   - `GET /api/jobs/{id}/result` → ZIP (masked docx + keys / restored docx +
     manifest), `Cache-Control: no-store`, 404 when expired.
   - `DELETE /api/jobs/{id}` → cancellation request when still queued/running.
   - Bounded queue → 429; unknown `job_id` → 404; opaque ids prevent IDOR in
     the single-tenant/private deployment (documented, no auth added).
3. **Worker execution**: long-lived process, bounded concurrency via a
   thread pool of warm per-thread maskers, lease/heartbeat, retry ≤ 3 with
   backoff, TTL sweep on startup and periodically, atomic result writes,
   SIGTERM drain. Reuse `mask_docx_in_memory` / `unmask_docx_in_memory` /
   `mask_document` unchanged.
4. **Synchronous path**: CPU work offloaded to a bounded executor with a
   semaphore so `/health` stays responsive; limit `MAX_TEXT_CHARS` /
   `UPLOAD_MAX_MB` already in place; threshold-driven redirect to jobs for
   large inputs.
5. **Front**: for large files, POST the job, poll `GET /api/jobs/{id}` with
   backoff, show progress, fetch the result ZIP on success, unpack as today;
   keep the synchronous flow for small inputs. Global timeout disable removed.

## Consequences

Positive: event loop stays responsive; long work survives disconnect/restart
(metadata + files on disk); bounded resources; no PII in logs/headers/DB;
small-job UX unchanged; reproducible kill/expiry/backpressure behavior.

Negative / accepted costs: one more container; SQLite is single-host only
(multi-instance deployment would need Postgres/Redis — a future ADR); two
execution paths to maintain; the 8-buffer problem is only partly addressed
(streaming responses, spooled inputs) because python-docx is not a streaming
transformer; job throttle values are estimates until re-benchmarked.

## Decisions (maintainer, 2026-11-23)

1. **Substrate: SQLite WAL + one dedicated worker container.** No Postgres/Redis.
2. **Worker form: long-lived warm thread pool**, one masker per thread (bounded
   by `JOB_CONCURRENCY`), no cold interpreter/dictionary start per job.
3. **Sync/job threshold:** text ≤ 20 KB, DOCX ≤ 1 MB or ≤ 200 paragraphs stay
   synchronous; larger inputs become jobs. All three numbers configurable
   (`.env`), to be re-anchored by benchmarks later.
4. **Frontend scope:** no custom percent-progress widget in this stage
   (maintainer decision, 2026-11-23). Implement the job flow as originally
   planned in the request (§6/§13): the API + worker, polling with backoff,
   status/progress/error display, retry/cancel/resume where they naturally
   belong, and the existing result panes/download UX unchanged. Progress is
   carried as job phases in the status payload; a dedicated percentage
   indicator for upload/parse/mask steps is explicitly out of scope.

## Verification plan

- Job store unit tests: claim/lease/heartbeat/retry/expiry/cancel/concurrency
  caps, no PII columns, restart simulation (new repository instance over the
  same DB file).
- Worker integration: mask DOCX through a job end-to-end, result ZIP unpacks,
  keys v2 restore; crash mid-job → lease reclaim; TTL sweep removes artifacts.
- Event-loop probe re-run: `/health` during a job must stay in single-digit ms.
- Synchronous bounded-executor test: over-limit concurrent masks → 429/503.
- Proxy streaming tests + bounded timeout assertions; e2e smoke on a live
  CI-compose stack including the worker service.
- `back/benchmarks/` re-run: record after-numbers in `PRODUCTION_READINESS.md`.
