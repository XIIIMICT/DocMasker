# Backend remediation — final report: DocMasker (2026-11-27)

Scope: only the confirmed findings in `backend-remediation-brief`. Base `2fe2ede`;
final local `dev` = `5d80056`. Task branches merged `--no-ff`; nothing pushed; no
production data, live migration or remote settings other than branch protection (below).

## Phase 0 — research (`ef22250`)
`docs/REMEDIATION-PHASE0-RESEARCH.md`: AGENTS/ADR inventory; legacy JSON `/metrics` had
only test/doc consumers; artifact ZIPs already set `Cache-Control: no-store` but PII JSON
did not; disk-full produced `internal_error`; queue-full was a typed 429 without
`Retry-After`; local RSS deltas at JOBS_CONCURRENCY 2/4/6/8 were 84.4/120.1/166.0/199.2 MB
(audit's ~110 MB/thread remained an unverified estimate); `RuleBasedMasker.mask()` had zero
production call sites; no automated backup.

## P0-1 — branch protection (applied remotely)
Research contradicted the brief: DM has **no `main`** (default `dev`). Per owner decision,
`main` was created from the then-current remote `dev` (`2fe2ede`) and protected; the same
was done for RC and EL. Verified via `gh api`: sole required check `CI required`, strict, 1
approval, dismiss stale, admins included, force pushes/deletions disabled. Residual:
`dev` remains default and unprotected — PRs to `dev` bypass this gate. Live push rejection
was not tested (SSH unavailable); settings verified via API.

## P0-3 — no-store + artifact storage readiness (`1246799`)
`ApiNoStoreMiddleware` adds `Cache-Control: no-store` and `Pragma: no-cache` to every
`/api/*` response (JSON, ZIP, typed errors, generic 500); `/health`, `/ready`, `/metrics`,
`/openapi.json` stay out of scope; the Next proxies forward/default both headers.
Readiness (`check_artifact_storage`) verifies `JOBS_DIR` writability, free space ≥ **1 GiB**
(chosen over a volume percentage for determinism) and one mkstemp/fsync probe. Called at
job admission before any staging row/file and at worker startup. Failure: **503**
`{code:"artifact_storage_unavailable", detail:<Russian>, request_id}` + `X-Request-ID` +
no-store; no partial artifact, no ambiguous 500. Negative proofs: removing the middleware
or the admission gate made the corresponding tests fail. Monitoring: reused existing
`dm_*` series, added low-disk alert + dashboard panel + contract docs in the same change.
Full backend pytest on a fresh disposable DB: 527 passed, 4 skipped, 87.00% coverage.
Real ENOSPC/EROFS on the target volume was **not** exercised locally (simulated only).

## P1-4 — worker concurrency and backpressure (`90bc40c`)
Measured at 1/2/4/6/8 threads; chose default `JOBS_CONCURRENCY=4` via
`floor(1024 / (159 × 1.5)) = 4`. `JOBS_MAX_ACTIVE` 16→32 from a measured 16-submission
burst; `JOBS_RETRY_AFTER_SECONDS=45` adds `Retry-After` to typed `queue_full` 429s (also
forwarded by the proxies), with the Russian `{code,detail,request_id}` envelope unchanged.
Queue-depth/saturation alerts and worker-dashboard panels landed with docs. A full
**1800.5 s** soak at concurrency 4 processed 7,824 synthetic DOCX jobs with 0 failures;
RSS proxy (Windows working-set of the harness, not container RSS) rose 4.2% over the last
10 minutes without unbounded growth. The 1 GiB budget arithmetic and a target-host Linux
container soak remain OPEN. Backend 533 passed/4 skipped, 87.01%; frontend 248 pass,
coverage 86.44%; `promtool check rules` 20 rules.

## P1-5 — guard the production mask path (`a02c296`)
Re-verified zero production call sites for the quadratic `RuleBasedMasker.mask()`. The repo
deprecation policy has **no timed compatibility window** and removal would break
legitimate reference tests/benchmarks, so the method is deprecated in place with a
`DeprecationWarning`; the linear `service.mask_text` path is untouched. A new AST guard
(`check_legacy_mask_path.py`, wired into CI) rejects `.mask` and dynamic variants under
`back/src`; negative proof: a temporary probe produced exit 1, restoring gave exit 0.
Full backend 543 passed/2 skipped, 87.28%.

## P1-6 — sunset legacy JSON `/metrics` (`3dfc57f`)
Only tests/docs consumed the JSON representation; dashboards/alerts use `dm_*`. Per the
loud-removal policy (no timed shim window), the JSON contract was removed: every request
returns `text/plain` Prometheus exposition regardless of `Accept`, so a stale client fails
loudly on content type. The **route itself stays** because Prometheus scrapes it — a 404
would break monitoring; this nuance is documented. A monitoring-contract test asserts no
dashboard/alert expression uses legacy keys. 543 passed/4 skipped, 87.19%.

## P1-8 — PostgreSQL backups with verified restore (`ab04637`)
Chose `pg_dump -Fc` sidecar over WAL-G/pgBackRest (no PITR requirement, no WAL archive
access). Assumed RPO ≤24 h (not agreed), daily 02:00Z dump, retention 7, weekly Sunday
03:00Z restore drill. Real disposable drill applied the actual Alembic chain, inserted 2
synthetic rows, verified a SHA-256 fingerprint after backup and restore, dropped the
scratch DB; a tampered fingerprint produced exit 1 and no leftover DB. 10 `dm_pg_backup_*`
series, 5 alerts, a 6-panel dashboard and `docs/RUNBOOKS.md` landed together;
`promtool check rules` 25 rules. **OPEN:** `BACKUP_OFFHOST_TARGET` is empty → off-host copy
requirement NOT satisfied (`dm_pg_backup_offhost_configured 0`); no target-host rehearsal;
external role needs `CREATEDB`; SMTP delivery not re-verified.

## P1-9 — nightly heavy suite (`5d80056`)
`04:13 UTC` daily + `workflow_dispatch`, not part of `CI required`; jobs prefixed `Nightly`.
Seven jobs: compose/actionlint, backend coverage on disposable PG+Valkey, frontend coverage
(65%), OpenAPI freshness, report-only pip-audit/bun audit + freshness, blocking gitleaks,
production-image + Playwright smoke. Artifacts 7 days. `pip-audit` found 3 advisories on the
deliberate `setuptools<81` pin (fix would break `pkg_resources` on Python 3.15) — concrete
reason to keep it report-only; gitleaks stays blocking. No CI→Alertmanager wiring exists;
gap documented without inventing a secret. Scheduled night not observed (no push).

## P2 triggers — NOT TRIGGERED
| Item | Trigger | Status |
|---|---|---|
| P2-1 S3/MinIO artifacts + multi-host workers | single-host disk pressure, CPU ceiling, HA need | not triggered; one host still fits measured concurrency 4 |
| P2-3 OpenTelemetry | cross-service latency debugging need | not triggered |
| P2-4 SLOs / error budgets | baseline + weeks of monitoring | not triggered |
| Broker T1 (section 7) | multi-host workers AND S3 done AND measured PG contention | not fired; no broker introduced |

## Remaining OPEN / owner gates
- `dev` (default branch) protection; off-host backup destination; real full/RO disk drill
  on the target host; Linux container RSS soak at the 1 GiB budget; 300/500-client presence
  measurements (RC); Keycloak OIDC browser CI; alert-mail delivery; scheduled night.
- Artifact spool (`dm-jobs-data`) still needs a consistent manual copy alongside PG dumps.

## Runbooks / docs updated
`docs/RUNBOOKS.md`, `docs/PERFORMANCE.md`, `docs/OPERATIONS.md`, `docs/ARCHITECTURE.md`,
`docs/API.md`, `docs/WORKFLOW.md`, `docs/TODO.md`, `docs/adr/*`, AGENTS files. No weakened
gates, no new runtime dependency, no pushes.
