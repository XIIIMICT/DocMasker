# Implementation baseline and dependency map (Phase 0)

Source: `docs/DocMasker_Production_Readiness_Implementation_Brief.md` §13
Phase 0. Recorded 2026-11-24 before any Phase 1 code change. The detailed
target design and the ReportCollector comparison live in
`docs/adr/0003-postgres-job-foundation.md`.

## Current data flow (`dev`, pre-migration)

```
Browser ──same-origin──► Next BFF (front, :5001)
                          │  /api/mask, /api/mask-file, /api/unmask,
                          │  /api/unmask-file, /api/jobs/*
                          │  bounded undici timeouts (60s/600s), streaming
                          ▼
                        FastAPI (back, :8001, Granian)
                          │  small input → run_cpu bounded executor → response
                          │  large input → 202 + JobAccepted
                          ▼
                        SQLite WAL (/app/jobs/jobs.sqlite3)  ◄── jobs-data volume
                          │  claims with leases, heartbeat at phase boundaries
                          ▼
                        Worker (dm-worker-dev, warm thread pool)
                          │  mask_docx_in_memory / mask_document
                          ▼
                        private spool: input/keys/result files by job id
```

Front proxies to `BACKEND_INTERNAL_URL`; the browser never reaches the
backend directly. Artifacts and DB share the private `jobs-data` volume.
Keys YAML carries `Профиль: russian-default` + Russian sections (no versions).

## Dependency inventory (Phase 1 will add)

| Layer | Current | After Phase 1 |
|---|---|---|
| DB driver | stdlib `sqlite3` (sync, one connection per op) | SQLAlchemy 2 sync + psycopg 3, one `QueuePool`, bounded DB executor for ASGI |
| Migrations | none (schema created ad hoc in `repository.py`) | Alembic, empty-DB smoke in CI |
| Cache | none | Valkey service present; cache logic is Phase 3 |
| Compose | `back`, `worker`, `front` (+ dev variants), `jobs-data` | adds `postgres`, `valkey`, `postgres-data` (prod/dev/CI) |
| Worker | thread pool, phase-boundary heartbeat | unchanged in Phase 1; full lifecycle is Phase 2 |
| API types | hand-written TS in `front/src/shared/api/` | unchanged until Phase 6 |

## Verified baseline facts

- `back/src/jobs/repository.py` = SQLite WAL, `PRAGMA journal_mode=WAL`,
  claims via `BEGIN IMMEDIATE` + lease timestamps; `storage.py` = id-keyed
  spool; `config.py` has `jobs_db_path=/app/jobs/jobs.sqlite3`.
- Live dev volume `dm-jobs-data`: `jobs.sqlite3` exists (20 KB), table `jobs`
  has **0 rows**, no staged artifacts. No data migration needed; quiesce/reset
  procedure in ADR-0003 §Migration.
- ReportCollector available at `D:\Work\ReportCollector`: reusable PostgreSQL
  `SKIP LOCKED`/lease-token/Alembic/Compose patterns (see ADR-0003 table).
  Differences: DocMasker is PII-reversible, sync-driver + thread-pool worker,
  no JSONB payload with user data, no published prod DB ports.
- Unrelated pre-existing work preserved: `.github/workflows/ci.yaml`
  `setup-uv` bump committed separately (`2778ca6`); the new brief file is
  untracked user work and is kept.

## Exit

Design is written down (this map + ADR-0003). No code behavior changed in
Phase 0. Phase 1 may now start from this baseline.
