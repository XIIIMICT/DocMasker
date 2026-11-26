# Follow-up remediation matrix

Source: `DocMasker_Follow_Up_Fixes_Agent_Prompt.md` (untracked maintainer file).
Baseline: `dev` at `c376ecd`, 287 back tests, 1 skipped, 87% branch coverage.
Status legend: `todo` · `in progress` · `done` (tested) · `verified-existing`.

| # | Finding | Change | Tests | Docs | Status |
|---|---|---|---|---|---|
| P0.1 | Worker ownership/fencing on every mutation; shared `<job_id>.result` can be overwritten by a stale worker; `fail()`/acknowledge lack liveness checks | `jobs/repository.py`, `jobs/worker.py`, `jobs/storage.py`, `jobs/api.py` | `test_jobs_store.py` A/B lease race, explicit `OWNERSHIP_LOST`, ack idempotency; worker reads DB-referenced key | `back/src/jobs/AGENTS.md`, `docs/ARCHITECTURE.md`, `docs/adr/0003` | done |
| P0.2 | Stale staging recovery not invoked; cancelling stuck after worker death; retry off-by-one; shutdown grace unused | `repository.py` (`staging_until` + recovery methods, off-by-one), migration `0002_staging_recovery`, `worker.py` (sweep recovery), `routing.py`/`storage.py` (staging lease refresh), `config.py` (`STAGING_LEASE_SECONDS`, removed unused shutdown grace) | `test_jobs_store.py` abandoned-staging and expired-cancelling recovery tests; `test_settings_validation.py` | `docs/OPERATIONS.md`, `infra/AGENTS.md`, matrix | done |
| P0.3 | OOXML coverage truthfulness, additional metadata fields | `ooxml/package.py` (VT tags, author/initials, disposition registry, per-part rel resolution, honest coverage), `manifest.py`, front manifest type | `test_ooxml.py` custom props/author attributes, coverage counts, chart rejection, root/nested rel resolution | `docs/DOCX_SUPPORT.md`, `docs/API.md`, masking AGENTS | done |
| P1.1 | Unified input limits, hostile ZIP names, unsafe result stems | `masking/router.py` shared staged admission, `docx_validation.py` entry-name checks, `archives.py::safe_stem`, `jobs/api.py` keys limit, `jobs/dependencies.py` size threshold, `jobs/routing.py` path paragraph count, deprecated 413 symbol | `test_docx_validation.py`, `test_archives.py`, `test_mask.py`, `test_jobs_api.py`, existing file tests | `docs/API.md`, `docs/SECURITY.md`, masking AGENTS | done |
| P1.2 | Config digest misses output-affecting values; exception matching semantics | `masking/config.py`, `config.py` | `test_config.py`, `test_cache.py` | `docs/MASKING_CONFIGURATION.md` | todo |
| P1.3 | Cache-artifact GC with live references | `jobs/cache.py`, `worker.py` | `test_cache.py` | `docs/OPERATIONS.md` | todo |
| P1.4 | Cancellation checkpoints in DOCX pipeline; address prefilter false negative | `ooxml/package.py`, `rule_based.py` | `test_ooxml.py`, `test_windowed.py` | `docs/DOCX_SUPPORT.md` | todo |
| P1/P2 | Atomic queue admission/dedupe; monotonic progress | `repository.py` | `test_jobs_store.py` | `back/src/jobs/AGENTS.md` | todo |
| P2 | Large-file memory copies; frontend cancellation honesty; monitoring exposure; shutdown settings | `jobs/operations.py`, `jobs/api.py`, front hook, monitoring compose | backend + front tests, compose config | `docs/PERFORMANCE.md`, `docs/OPERATIONS.md` | todo |
| Final | Documentation claims, stress tests, release gate | docs | full matrix | all docs | todo |
