# Testing

This is the canonical home for the component-to-test map, the required
failure-scenario matrix and the verification commands. Toolchain conventions,
CI job descriptions and the quality table live in `docs/WORKFLOW.md`; backend
hot-path test notes in `back/AGENTS.md`, `back/src/jobs/AGENTS.md` and
`back/src/masking/AGENTS.md`; frontend notes in `front/AGENTS.md`. The scenario
source is Implementation Brief §14 (the implementation brief (removed after Phase 8; remaining items in `TODO.md`)).

Status labels below mean: **Covered** = a named test exists in the repository;
**Recorded** = a historical run is documented, not re-run here; **Drilled
(local)** = a controlled drill actually ran on disposable local resources, not
on target hardware; **TODO** = genuinely deferred and must not be claimed as
passed. A named test proves the code path at test time; it is not a substitute
for a live stress drill.

## 1. Component-to-test map

### Backend (`back/tests/`, pytest, deterministic/offline unless noted)

| Component | Tests |
|---|---|
| Detectors, span policy, labels | `test_rule_based.py`, `test_labels_api.py` |
| Placeholders and keys YAML | `test_mapping.py`, `test_keys_v2.py`, `test_unmask.py` |
| One masking config / exceptions | `test_config.py`, `test_keys_v2.py` |
| Long-text windowing & boundaries | `test_windowed.py` |
| Text endpoints | `test_mask.py`, `test_unmask.py` |
| DOCX package validation | `test_docx_validation.py` |
| OOXML/OPC transform + manifest parity | `test_ooxml.py`, `test_docx.py` |
| File endpoints / flat ZIP / manifest | `test_mask_file.py` |
| Streaming upload staging, paragraph count | `test_uploads.py` |
| Bounded CPU/IO pools | `test_executor.py` |
| Job repository / schema / TTL / GC | `test_jobs_store.py` (needs `TEST_DATABASE_URL`) |
| Worker lifecycle & failure injection | `test_jobs_worker.py` (needs `TEST_DATABASE_URL`) |
| Job API (202/status/result/cancel) | `test_jobs_api.py`, `test_jobs_routing.py` |
| Valkey cache | `test_cache.py` (fake client offline; real Valkey via `TEST_VALKEY_URL`) |
| Stable error contract / no PII leakage | `test_errors.py` |
| OpenAPI contract (schema-only, no server/DB) | `test_openapi_contract.py` (also pins deterministic `app.openapi()` export) |
| Readiness and coarse metrics | `test_readiness.py` |
| Cross-field settings validation | `test_settings_validation.py` |
| Isolated operational drills | `test_operational_drills.py`, `drill_support.py`, `src/scripts/operational_drills.py` (needs `TEST_DATABASE_URL`; optional `DOCMASKER_DRILL_PG_CONTAINER` for the disposable backup rehearsal) |

`test_jobs_*.py`, `test_cache.py`, `test_operational_drills.py` and parts of
`test_jobs_api.py` skip without a real PostgreSQL/Valkey stand (`just db-stand`
+ `just seed`, then `TEST_DATABASE_URL` / `TEST_VALKEY_URL`); see
`docs/WORKFLOW.md` and `back/src/jobs/AGENTS.md`. The drill harness refuses any
database whose name does not contain `drill`, start with `test` or end in `_ci`,
and all its artifacts live in a caller-supplied temporary spool.

### Frontend unit (`bun test` + happy-dom, `__tests__/` next to the code)

29 test files, grouped by the code they exercise. Setup: `front/bunfig.toml` →
`front/src/test-setup.ts`; DOM tests use Testing Library `fireEvent` (no user-event
or axe dependency). See `front/AGENTS.md` for stack/command details.

| Area | Files | What they pin |
|---|---|---|
| Config / stats | `shared/__tests__/config.test.ts`, `shared/__tests__/mask-stats.test.ts` | runtime config, stats breakdown and counts |
| API adapters | `shared/api/__tests__/mask.test.ts`, `mask-archive.test.ts`, `jobs.test.ts`, `timeouts.test.ts`, `placeholder-count.test.ts`, `lazy-zip.test.ts` | requests/responses, ZIP + manifest parsing, canonical timeouts, job polling, single-flight JSZip loader |
| Hooks | `shared/hooks/__tests__/use-synced-scroll.test.ts` | ratio-based pane scrolling |
| Pane boundaries | `shared/ui/__tests__/action-boundaries.test.tsx`, `pane-boundaries.test.ts` | Enter/Space and synthetic pointer clicks on visually disabled actions are blocked by the workflow guard (not by CSS); stable `disabledProps` and one lazy-import guard — no export-name, import-order or retired-module string checks |
| Accessibility | `shared/ui/__tests__/pane-accessibility.test.tsx`, `brand-accessibility.test.tsx` | accessible names for text areas, file actions, tabs and downloads; `aria-describedby` for invalid keys and result errors; tab/menu keyboard operation; focus return after Escape |
| Dropdown | `shared/ui/__tests__/hover-dropdown.test.ts` | geometry check, capture-phase scroll listener and disposer |
| Document viewer | `shared/ui/__tests__/docx-viewer.test.ts`, `docx-viewer-lifecycle.test.tsx`, `docx-viewer-scheduling.test.ts` | marker highlighting, stale generation/render guards, debounce; injectable `DocxViewer.renderDocument` seam leaves the lazy default unchanged |
| Header | `shared/ui/__tests__/theme-switcher.test.ts`, `support-button.test.ts` | theme options, mailto parsing |
| Masking workflow | `features/masking/__tests__/use-mode-drafts.test.ts`, `use-masker-workflow-downloads.test.ts`, `use-job-lifecycle.test.ts`, `use-file-input.test.ts`, `result-download.test.ts`, `mask-requests.test.ts`, `keys-parse.test.ts`, `demask-guard.test.ts` | per-mode drafts, downloads, job lifecycle/cancellation, file input, request guards, YAML key parsing |
| App / tooling | `app/__tests__/globals-conventions.test.ts`, `src/__tests__/check-coverage.test.ts` | global CSS conventions (instant theme switching, restored 180/220 ms `gap`/`max-width` expansion with no `translateX` reveal, zeroed motion tokens under reduce, pinned HeroUI breakpoints); coverage gate is a thin CLI wrapper over `scripts/check-coverage-parse.mjs` — malformed thresholds/LCOV fail deterministically, 65% floor unchanged; the fail-closed LCOV parser rejects duplicate/out-of-order/incomplete counters, a premature second `SF` and unsafe aggregate sums, while accepting bun's LF/CRLF, multi-file and zero-hit reports |

### Browser smoke (Playwright, project `smoke`, needs a running stack)

Seven spec files, **55 tests** at the current `--list` run: 16 in
`smoke.spec.ts` (page/mode/layout and text mask+unmask), 4 in
`smoke-file.spec.ts` (DOCX masking, placeholder highlighting, unused keys,
stats parity), 3 in `smoke-job.spec.ts` (job submission, active-job cancel,
result manifest), 20 in `smoke-a11y.spec.ts` (6 support-link/motion/focus,
11 axe scans, 3 keyboard/perceivability), 10 hover, 1 rerender, 1 health:
`front/e2e/health.spec.ts`, `front/e2e/smoke.spec.ts`,
`front/e2e/smoke-file.spec.ts`, `front/e2e/smoke-job.spec.ts`,
`front/e2e/smoke-a11y.spec.ts`, `front/e2e/rerender-smoke.spec.ts`,
`front/e2e/hover-smoke.spec.ts`. The split moved whole `test(...)` blocks with
no assertion changes; shared constants (`MASK_DATA`, `DEMASK_DATA`, both DOCX
fixtures) and `switchMode()` live in `front/e2e/_helpers.ts`, and the one
helper used only by the a11y spec (`focusByKeyboard`) stays local to it.
`front/playwright.config.ts` discovers them with
`testMatch: /(?:health|mask|[a-zA-Z0-9_-]*smoke[a-zA-Z0-9_-]*)\.spec\.ts/`
(`mask` is a future slot; the `smoke` alternative covers every `smoke-*` spec),
`workers: 1`, Chromium, `E2E_BASE_URL` default `http://127.0.0.1:5001`;
`*-probe*` and `probe/` files are excluded. CI's `production` job first runs
`bunx --no-install playwright test --project=smoke --list` and passes the
listing to `bun e2e/check-smoke-discovery.mjs smoke-tests.txt`. That guard
requires all seven exact spec basenames, at least 16 tests in the main spec,
4 in the file spec, 3 in the job spec, 20 in the a11y spec and 55 in the
project — the per-spec floors preserve the old 43-test main-suite coverage
(16+4+3+20) individually, so a broken `testMatch` or a rename cannot silently
drop a suite (`.github/workflows/ci.yaml`, mirrored by
`.github/workflows/nightly.yaml`). Locally, neither step needs a running stack:

```sh
cd front
bunx --no-install playwright test --project=smoke --list > smoke-tests.txt
bun e2e/check-smoke-discovery.mjs smoke-tests.txt
```

Historical negative proofs (recorded before the split, in **copies** of the
listing — no committed spec was touched) renamed or removed the
`smoke.spec.ts` entries and the guard exited 1. The current guard checks all
seven basenames plus the 16/4/3/20 per-spec floors and the 55-total floor,
so the same rename/removal drill still fails. `front/local.playwright.config.ts` is
the local-only Chrome-on-PATH helper; it now derives `testDir`, `testIgnore`,
project names and `testMatch` from the committed config and overrides only the
launcher/reporter, so local and CI discovery cannot drift.

Shared E2E helpers: `front/e2e/_helpers.ts` holds the data/keys textarea names,
both DOCX fixtures and `switchMode()` (used by more than one spec);
`front/e2e/helpers/` holds `selectors.ts` (hidden file input), `timing.ts`
(two-frame post-gesture grace plus the single named close-grace period,
250 ms + margin) and `a11y.ts` (explicit axe WCAG 2/2.1 A/AA ruleset and
settled-UI wait, no sleep-as-evidence). `focusByKeyboard` stays local to
`smoke-a11y.spec.ts` because only that spec uses it. The automated scans and
the manual keyboard/focus outcomes
have one canonical home in `docs/ACCESSIBILITY.md`; passing scans are not an
accessibility claim. Screen-reader verification is OUT OF SCOPE by maintainer
decision (2026-11-28); automated axe scans and manual keyboard/focus review are
the agreed coverage. Do not claim a screen-reader audit.

Sleep-as-evidence is gone: the empty-keys test counts requests on an intercepted
`**/api/unmask` route and uses one valid demask as a synchronisation barrier;
the rerender test observes node identity plus the named debounce pass; hover's
explicitly shorter before-grace re-entry delay remains because that timing is
the behaviour under test.
Older 23/35/40/41 counts are dated records in git; rely on `--list` for the
current inventory. A1, A2, A4 and C1/C2 are implemented, A3 is **DEFERRED** by
maintainer decision. Current status: root `TODO.md` §"Follow-up remediation —
current state"; contracts: `docs/FRONTEND-AUDIT.md`.

## 2. Required failure scenarios (Brief §14, condensed)

| Scenario | Status | Evidence / gap |
|---|---|---|
| Migration from an empty DB | **Covered** | CI `back` job runs `alembic upgrade head`; isolated prod stack migrates before smoke (`.github/workflows/ci.yaml`) |
| Previous-schema / live-data migration | **Recorded** | The pre-migration SQLite `jobs` table had 0 rows; the approved path was quiesce/reset, not a data migration (ADR-0003 §Migration notes; history in git). No live-data migration drill |
| Two workers claim without duplicate live ownership; `SKIP LOCKED` | **Covered** | `test_jobs_store.py::test_concurrent_claims_never_share_a_lease`, `::test_claim_is_exclusive_and_bounded`, `::test_staging_is_not_claimable` |
| Heartbeat survives a blocking operation beyond the lease | **Covered** | `test_jobs_worker.py::test_heartbeat_keeps_a_long_operation_owned` (sleeps >2 lease intervals), `::test_lease_loss_stops_execution` |
| Stale fencing token cannot heartbeat / fail / cancel / succeed / publish | **Covered** | `test_jobs_worker.py::test_stale_token_cannot_publish_a_result`; `test_jobs_store.py::test_heartbeat_fails_without_ownership` |
| Lease expiry increments attempts and fails at the configured maximum | **Covered** | `test_jobs_store.py::test_stale_lease_is_reclaimed`, `::test_retryable_failure_requeues_then_fails`; `test_jobs_worker.py::test_failed_job_is_retried_then_final` |
| Queue full → stable 429, no staging artifacts | **Covered** | `test_jobs_store.py::test_queue_full_is_bounded`, `::test_queue_full_leaves_no_staging_row`, `test_jobs_api.py::test_queue_full_returns_429` |
| Cancellation: queued immediate, running at checkpoint, no result/cache | **Covered** | `test_jobs_api.py::test_cancel_job`; `test_jobs_store.py::test_cancel_only_affects_active_jobs`, `::test_running_cancel_is_a_request_acknowledged_by_the_worker`; `test_jobs_worker.py::test_cancel_before_processing_publishes_nothing`, `::test_cancelled_job_never_populates_cache`; e2e `smoke-job.spec.ts` Cancel scenario |
| TTL: result clock starts on completion; active jobs never expire; cleanup idempotent | **Covered** | `test_jobs_store.py::test_result_ttl_starts_on_completion`, `::test_active_jobs_are_never_swept`, `::test_ttl_sweep_removes_rows_and_artifacts`; `test_jobs_worker.py::test_sweep_removes_expired_jobs_and_artifacts` |
| SIGTERM: stop claims, drain in-flight jobs, heartbeats continue | **Drilled (local)** | Controlled handler-equivalent drill (`test_operational_drills.py::test_sigterm_drains_without_stale_publication`): exactly one claim after the handler, in-flight DOCX job `succeeded` with a valid ZIP, the second job stayed `queued` with no result. Real POSIX signal delivery on the target host remains an operator gate |
| Transient PostgreSQL outage must not kill the worker | **Covered** | `test_jobs_worker.py::test_worker_poll_survives_transient_db_error`, `::test_heartbeat_survives_transient_db_error`; live CI-stack database restart in Phase 8: `/ready` briefly 503 then ready, worker stayed up and the in-flight job succeeded |
| Crash after staging / DB commit / processing / ZIP creation / before commit | **Drilled (local)** | `test_crash_window_matrix` + `test_cross_job_artifact_isolation`: staging abandoned → `failed/staging_abandoned` with 0 artifacts; committed row → restarted worker succeeded; lease-lost claim's publish rejected, `fail` → `ownership_lost`, heartbeat false, new owner succeeded at attempt 2 with stale key absent; ENOSPC at ZIP write → queued, no partial result, retry succeeded; result bytes written before DB commit → stale key rejected and removed, only the current fenced key published. Target-hardware repetition remains OPEN |
| Invalid user data non-retryable; transient failures bounded backoff | **Covered** | `test_jobs_store.py::test_non_retryable_failure_is_final`, `::test_retryable_failure_requeues_then_fails`; `back/src/jobs/worker.py::classify` |
| PostgreSQL outage/pool exhaustion returns a bounded error, health never hangs | **Drilled (local)** | `/ready` 503 covered by `test_readiness.py`; the `/metrics` scrape is DB-independent by construction (`test_metrics.py::test_backend_metrics_endpoint_is_prometheus_only`, `test_readiness.py::test_metrics_scrape_is_prometheus_only_and_db_independent`); Phase 8 live PostgreSQL restart recorded. Local pool drill (`test_pool_exhaustion_is_bounded`) pinned `pool_size=1/max_overflow=0` with a 0.2 s checkout timeout: checkout and one worker poll both failed in ~0.21 s with `TimeoutError`, worker stayed alive, job succeeded after release (0.003 s recovery checkout) |
| Legacy JSON `/metrics` removed; dashboards/alerts stay Prometheus-only | **Covered** | `test_metrics.py::test_backend_metrics_endpoint_is_prometheus_only` (plain/`text/plain`/`application/json` all return `dm_*` text, no `active_jobs`), `test_readiness.py::test_metrics_scrape_is_prometheus_only_and_db_independent`; `test_monitoring_contract.py` proves every provisioned dashboard/alert series is `dm_*`/`up`, no legacy JSON key, and both scrape jobs keep `metrics_path: /metrics` |
| Identical inputs → deterministic key + hit; keys bytes change the key | **Covered** | `test_cache.py::test_same_inputs_produce_the_same_key`, `::test_different_docx_bytes_produce_different_keys`, `::test_different_keys_yaml_changes_demask_key`, `::test_different_output_filename_changes_key` |
| Config digest / ruleset / manifest / namespace version invalidation | **Covered** | `test_cache.py::test_exception_digest_changes_key`, `::test_ruleset_version_bump_invalidates`, `::test_manifest_and_namespace_versions_invalidate` |
| Corrupt/missing artifact or Valkey loss → miss, not failure | **Covered** | `test_cache.py::test_corrupt_artifact_is_a_miss`, `::test_valkey_outage_degrades_to_misses`, `::test_real_valkey_corrupt_entry_evicts` |
| Cache hit verifies integrity; writes only after fenced success; no failed/cancelled cached | **Covered** | `test_cache.py::test_artifact_store_verifies_integrity`; `test_jobs_worker.py::test_cancelled_job_never_populates_cache`; `back/src/jobs/AGENTS.md` |
| Two concurrent identical jobs cannot corrupt a shared artifact | **Covered** | `test_jobs_worker.py::test_identical_concurrent_submissions_attach_to_one_job` (PostgreSQL dedupe, not a Valkey lock) |
| Cache TTL/memory/eviction bounded and observable; key names leak no PII | **Covered** | `test_cache.py::test_hit_miss_write_and_eviction`, `::test_key_does_not_leak_source_hash`; limits in `.env.example`; target-hardware memory pressure TODO |
| Entity spans survive window boundaries; offsets stay absolute | **Covered** | `test_windowed.py::test_all_entity_types_survive_window_boundaries`, `::test_long_address_crossing_boundary_is_not_cut`, `::test_windowed_offsets_are_absolute`, `::test_dedupe_boundary_spans_is_deterministic` |
| OOXML part coverage, namespace variants, >64 KiB markers, unsafe/embedded content | **Covered** | `test_ooxml.py::test_all_supported_parts_are_masked_and_restored`, `::test_default_namespace_is_processed_identically`, `::test_marker_after_64kib_is_still_found`, `::test_malformed_xml_is_rejected`, `::test_embedded_binary_object_is_rejected`; `test_docx_validation.py` |
| Sync vs job manifest parity; frontend reads manifest, not headers | **Covered** | `test_ooxml.py::test_sync_and_job_manifest_parity`, `::test_manifest_validation_rejects_bad_schema`, `::test_successful_manifests_are_complete_with_empty_coverage_arrays`, `test_mask_file.py`; front `manifest-validation.test.ts` / `mask-archive.test.ts` completeness cases; e2e `smoke-job.spec.ts` manifest scenario |
| Stable error envelope, no PII/path/traceback in errors or rows or keys | **Covered** | `test_errors.py::test_every_error_has_code_detail_request_id_and_header`, `::test_request_validation_error_does_not_echo_submitted_pii`; `test_jobs_store.py::test_no_pii_columns_in_schema`; `test_cache.py::test_key_does_not_leak_source_hash` |
| Bounded route timeouts; streaming upload counts real bytes; results streamed | **Covered** | `timeouts.test.ts`, `mask.test.ts::proxy timeouts`; `test_uploads.py::test_stage_upload_counts_real_bytes_not_content_length`, `::test_stage_upload_rejects_oversized_stream_and_cleans_up`; `docs/API.md` §Proxy timeout policy |
| `Cache-Control: no-store` on every `/api/*` response (PII JSON, ZIPs, typed errors) | **Covered** | `test_response_headers.py`, `test_jobs_api.py::test_result_serves_zip_with_no_store`; Next proxies forward both headers (`docs/API.md`, `back/AGENTS.md` §11) |
| OpenAPI → generated TS types with CI freshness | **Covered** | `python -m src.openapi_export` + pinned dev-only generator; `gen:openapi --check` in CI (`docs/API.md` §"OpenAPI contract") |
| Restore procedure for PostgreSQL + artifacts + config | **Drilled (local)** | Disposable `pg_dump`/artifact-copy/`pg_restore` rehearsal in `test_backup_restore_rehearsal`: 2 rows restored identically, 2 artifact SHA-256s identical, synthetic exception-config digest unchanged; no private exception file read. Target-host rehearsal with the real RO-mounted config remains **OPEN** (`docs/PRODUCTION_READINESS.md`) |
| Liveness/readiness documented and exercised | **Partial** | `/health` used by Compose healthchecks and `health.spec.ts`; `/ready` implemented and unit-tested (`test_readiness.py`) but not wired into Compose/CI (deferred stage 6) |
| Operations runbooks for worker/DB/cache/storage failures | **Covered** | `docs/OPERATIONS.md` §6 |
| Target-hardware stress: queue pressure, worker/DB/Valkey restart, disk full, result expiry, cancellation, N concurrent large documents | **Drilled (local), target host OPEN** | Phase 8 isolated CI-stack drill covered 3 concurrent jobs, dedupe, cancel, worker/DB restarts, Valkey flush and result download. The DM-3 drill adds simulated ENOSPC, expiry/byte audit (2 rows swept; 1 missing artifact counted; 1 orphan reported and retained), and 1/2/N synthetic DOCX batches with wall/CPU/RSS/queue-wait/pool-wait recorded in `back/benchmarks/operational-drill-results.json` and `docs/PERFORMANCE.md`. No throughput target is asserted. Target hardware, a real disk-full filesystem and POSIX SIGTERM remain operator gates |
| Precision/recall corpus, TLS-bypass replacement, container hardening, branch protection | **TODO / deferred** | `TODO.md` §"Deferred by maintainer decision" and §"Blocked / external"; branch protection is a GitHub-side setting (`docs/WORKFLOW.md`) |

## 3. Commands

From the repository root (toolchain: `uv` for Python, `bun` for JS/TS,
`rg` for search — `docs/WORKFLOW.md`):

```sh
# Backend: lint, format check, types, offline unit + integration tests
uv run --directory back ruff check
uv run --directory back ruff format --check
uv run --directory back ty check
uv run --directory back pytest -q --cov=src --cov-branch --cov-fail-under=80 \
  --cov-report=term --cov-report=xml:coverage.xml --junitxml=test-results.xml

# Integration tests need the stand and URLs. CI sets them in ci.yaml:
#   TEST_DATABASE_URL=postgresql+psycopg://dm:ci_password@127.0.0.1:15432/dm_ci
#   TEST_VALKEY_URL=redis://127.0.0.1:16379/0
# Locally: just db-stand publishes only PostgreSQL on loopback (15433 by
# default); Valkey has no host port in infra/compose.db.yaml, so run the
# cache-integration tests inside the Compose network or publish a temporary
# loopback port. Adjust credentials to your .env.
just db-stand
just seed
TEST_DATABASE_URL=postgresql+psycopg://dm:change-me@127.0.0.1:15433/docmasker \
  uv run --directory back pytest -q

# Frontend: types, lint, format, unit tests
bun run --cwd front typecheck
bun run --cwd front lint:check
bun run --cwd front format:check
bun run --cwd front test
bun run --cwd front build        # production build; also resolves the lazy chunks
bun run --cwd front gen:labels   # then: git diff --exit-code -- front/src/shared/api/labels.generated.ts

# Frontend coverage gate (CI-equivalent, run from front/)
cd front && bun test --coverage --coverage-reporter=lcov && bun scripts/check-coverage.mjs 65

# Aggregators from the root
just lint
just typecheck
just test
```

### Isolated drill harness (DM-3)

Provision a *disposable* PostgreSQL (never the dev/prod stand), then run the
conductor. The placeholder credentials below are deliberately not real:

```sh
docker run -d --name dm-drill-pg-YYYYMMDD --label dm.drill=disposable \
  -e POSTGRES_USER=<user> -e POSTGRES_PASSWORD=<synthetic> \
  -e POSTGRES_DB=dm_drill_YYYYMMDD -p 127.0.0.1:15533:5432 postgres:18-alpine
uv run --directory back python -m src.scripts.operational_drills \
  --database-url "postgresql+psycopg://<user>:<synthetic>@127.0.0.1:15533/dm_drill_YYYYMMDD" \
  --pg-container dm-drill-pg-YYYYMMDD \
  --report back/benchmarks/operational-drill-results.json
docker rm -f dm-drill-pg-YYYYMMDD
```

The conductor applies Alembic, creates a temporary `JOBS_DIR`, runs only
`tests/test_operational_drills.py` and writes JSON evidence (no credentials,
no input text). Omit `--pg-container` to skip the backup rehearsal and record
it as an OPEN gate instead. The local run produced **13 passed**; the full
suite with `TEST_DATABASE_URL` set to that disposable DB produced **504 passed,
4 skipped, 86.80% branch coverage**.

Gates: backend **≥80% branch coverage** (`--cov-fail-under=80`,
`back/pyproject.toml` `[tool.coverage]`); frontend **65% line coverage**
(`front/scripts/check-coverage.mjs` over bun's lcov). Pytest uses
`asyncio_mode=auto` and `filterwarnings=error` with the scoped
`pkg_resources`/psycopg ignores (`back/pyproject.toml`). Unit tests are
deterministic and offline: no model downloads, no network.

## 4. Browser smoke and local Chrome

The `smoke` project needs a running stack (no auto-start):

```sh
just prod                      # or just dev on 5001
E2E_BASE_URL=http://127.0.0.1:5001 bun run --cwd front test:e2e --project=smoke
```

On this host Chrome may be installed via a package manager shim, which
Playwright's `channel: "chrome"` does not locate by itself. Use the local-only
config (gitignored; resolve `chrome` from PATH):

```sh
cd front
E2E_BASE_URL=http://127.0.0.1:5001 \
  bunx --no-install playwright test --config=local.playwright.config.ts --project=smoke
```

CI's `production` job instead uses the bundled Chromium on the isolated CI
stack (front **15001**, back **18001**) and first verifies exact discovery of
`health.spec.ts`, `smoke.spec.ts`, `smoke-file.spec.ts`, `smoke-job.spec.ts`,
`smoke-a11y.spec.ts`, `rerender-smoke.spec.ts` and `hover-smoke.spec.ts`
plus the per-spec and total minimum test counts (§1). The CI front service
sets a synthetic `SUPPORT_MAIL` fixture: the header support link is rendered only when that runtime env var is
present. The smoke suite asserts that control's restored 180/220 ms expansion
(computed durations/easing plus a deterministic intermediate width in Chromium),
its hover, keyboard-focus and compact-Tooltip end states, that reduced motion
zeros all three DocMasker tokens while HeroUI's own motion-reduce CSS stays in
the cascade, and that visually disabled actions keep `aria-disabled`/focus but
accept no pointer or keyboard operation. Never run smoke against an unprepared
stack; never paste real PII into a live browser session.

## 5. Clean-install / Compose / prod-image verification

Minimum release verification (mirrors CI; `docs/WORKFLOW.md` §CI):

```sh
cp .env.example .env
just deps
just db-stand
just seed                     # Alembic 0001_jobs + jobs table check
just migrate                  # explicit migration step; just revision "msg" for new ones

# Config checks (CI `config` job)
docker compose --profile prod --profile dev config --quiet
docker compose -f infra/compose.ci.yaml config --quiet
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:1.7.7 -color

# Quality gates from §3, then e2e against the dev stack (§4)

# Isolated production-image stack (local reproduction of CI `production`)
docker compose -f infra/compose.ci.yaml build
docker compose -f infra/compose.ci.yaml up -d --no-build --wait --wait-timeout 180 back worker front
docker compose -f infra/compose.ci.yaml exec -T back alembic upgrade head
docker compose -f infra/compose.ci.yaml restart worker
E2E_BASE_URL=http://127.0.0.1:15001 bun run --cwd front test:e2e --project=smoke --reporter=list
docker compose -f infra/compose.ci.yaml down --volumes --remove-orphans
```

`infra/compose.ci.yaml` never loads the developer's `.env` or volumes; the
`--volumes` teardown is safe **only** for that isolated CI stack — never run it
against the dev/prod data stand. The prod image is built once and shared by
`back` and `worker` (private `jobs-data` volume). OPEN gates for repeating this
on the authorized host are in `docs/PRODUCTION_READINESS.md`; local drill
evidence is in §2 above. Never inherit a recorded pass. CI branch protection /
`CI required` remains a GitHub-side setting and cannot be asserted from the
repository.
