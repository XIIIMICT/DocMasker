# Follow-up remediation matrix

Source: `DocMasker_Follow_Up_Fixes_Agent_Prompt.md` (untracked maintainer file).
Baseline: `dev` at `c376ecd`, 287 back tests, 1 skipped, 87% branch coverage.
Status legend: `todo` · `in progress` · `done` (tested) · `verified-existing`.

| # | Finding | Change | Tests | Docs | Status |
|---|---|---|---|---|---|
| P0.1 | Worker ownership/fencing on every mutation; shared `<job_id>.result` can be overwritten by a stale worker; `fail()`/acknowledge lack liveness checks | `jobs/repository.py`, `jobs/worker.py`, `jobs/storage.py`, `jobs/api.py` | `test_jobs_store.py` A/B lease race, explicit `OWNERSHIP_LOST`, ack idempotency; worker reads DB-referenced key | `back/src/jobs/AGENTS.md`, `docs/ARCHITECTURE.md`, `docs/adr/0003` | done |
| P0.2 | Stale staging recovery not invoked; cancelling stuck after worker death; retry off-by-one; shutdown grace unused | `repository.py`, `worker.py`, `routing.py`, config | `test_jobs_store.py`, `test_jobs_worker.py` | `docs/OPERATIONS.md` | todo |
| P0.3 | OOXML coverage truthfulness, additional metadata fields | `masking/ooxml/package.py`, `manifest.py` | `test_ooxml.py` fixtures | `docs/DOCX_SUPPORT.md` | todo |
| P1.1 | Unified upload/input limits, hostile ZIP entry names, output filename sanitisation | `masking/router.py`, `docx_validation.py`, `uploads.py`, `ooxml/package.py` | `test_mask_file.py`, `test_uploads.py`, `test_docx_validation.py` | `docs/API.md`, `docs/SECURITY.md` | todo |
| P1.2 | Config digest misses output-affecting values; exception matching semantics | `masking/config.py`, `config.py` | `test_config.py`, `test_cache.py` | `docs/MASKING_CONFIGURATION.md` | todo |
| P1.3 | Cache-artifact GC with live references | `jobs/cache.py`, `worker.py` | `test_cache.py` | `docs/OPERATIONS.md` | todo |
| P1.4 | Cancellation checkpoints in DOCX pipeline; address prefilter false negative | `ooxml/package.py`, `rule_based.py` | `test_ooxml.py`, `test_windowed.py` | `docs/DOCX_SUPPORT.md` | todo |
| P1/P2 | Atomic queue admission/dedupe; monotonic progress | `repository.py` | `test_jobs_store.py` | `back/src/jobs/AGENTS.md` | todo |
| P2 | Large-file memory copies; frontend cancellation honesty; monitoring exposure; shutdown settings | `jobs/operations.py`, `jobs/api.py`, front hook, monitoring compose | backend + front tests, compose config | `docs/PERFORMANCE.md`, `docs/OPERATIONS.md` | todo |
| Final | Documentation claims, stress tests, release gate | docs | full matrix | all docs | todo |
