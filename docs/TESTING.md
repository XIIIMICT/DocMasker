# Testing

This is the canonical home for the component-to-test map, the required
failure-scenario matrix and the verification commands. Toolchain conventions,
CI job descriptions and the quality table live in `docs/WORKFLOW.md`; backend
hot-path test notes in `back/AGENTS.md`, `back/src/jobs/AGENTS.md` and
`back/src/masking/AGENTS.md`; frontend notes in `front/AGENTS.md`. The scenario
source is Implementation Brief §14 (the implementation brief (removed after Phase 8; remaining items in `TODO.md`)).

Status labels below mean: **Covered** = a named test exists in the repository;
**Recorded** = a historical run is documented, not re-run here; **TODO** =
genuinely deferred and must not be claimed as passed. A named test proves the
code path at test time; it is not a substitute for a live stress drill.

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
| Readiness and coarse metrics | `test_readiness.py` |
| Cross-field settings validation | `test_settings_validation.py` |

`test_jobs_*.py`, `test_cache.py` and parts of `test_jobs_api.py` skip without
a real PostgreSQL/Valkey stand (`just db-stand` + `just seed`, then
`TEST_DATABASE_URL` / `TEST_VALKEY_URL`); see `docs/WORKFLOW.md` and
`back/src/jobs/AGENTS.md`.

### Frontend unit (`bun test` + happy-dom, `__tests__/` next to the code)

`shared/__tests__/config.test.ts`, `shared/__tests__/mask-stats.test.ts`,
`shared/api/__tests__/mask.test.ts`, `shared/api/__tests__/mask-archive.test.ts`,
`shared/api/__tests__/jobs.test.ts`, `shared/api/__tests__/timeouts.test.ts`,
`shared/ui/__tests__/docx-viewer.test.ts`,
`shared/ui/__tests__/docx-viewer-lifecycle.test.tsx` (injectable
`DocxViewer.renderDocument` seam — lazy default renderer is unchanged),
`shared/ui/__tests__/theme-switcher.test.ts`,
`shared/ui/__tests__/support-button.test.ts`,
`shared/hooks/__tests__/use-synced-scroll.test.ts`, plus the gate's own
`src/__tests__/check-coverage.test.ts`. The coverage gate is a thin CLI wrapper
around the pure `front/scripts/check-coverage-parse.mjs`; malformed thresholds
and LCOV reports fail deterministically without affecting the 65% floor.
Setup: `front/bunfig.toml` → `front/src/test-setup.ts` (`front/AGENTS.md`).

### Browser smoke (Playwright, project `smoke`, needs a running stack)

`front/e2e/health.spec.ts`, `front/e2e/smoke.spec.ts`,
`front/e2e/rerender-smoke.spec.ts`. CI explicitly asserts that all three files
are discovered via `playwright test --project=smoke --list` before running them
(`.github/workflows/ci.yaml`, `production` job). Config: `front/playwright.config.ts`
(`E2E_BASE_URL`, default `http://127.0.0.1:5001`, `workers: 1`, Chromium);
`front/local.playwright.config.ts` is the local-only Chrome-on-PATH helper.
Probe files (`*-probe*`, `probe/`) are excluded. Recorded run counts differ
between `docs/WORKFLOW.md` and `docs/PRODUCTION_READINESS.md` — treat those as
historical records and rely on the `--list` assertion for the current set.

## 2. Required failure scenarios (Brief §14, condensed)

| Scenario | Status | Evidence / gap |
|---|---|---|
| Migration from an empty DB | **Covered** | CI `back` job runs `alembic upgrade head`; isolated prod stack migrates before smoke (`.github/workflows/ci.yaml`) |
| Previous-schema / live-data migration | **Recorded** | Baseline had 0 rows; approved quiesce/reset instead of a data migration (`docs/IMPLEMENTATION_BASELINE.md`, ADR-0003). No live-data migration drill |
| Two workers claim without duplicate live ownership; `SKIP LOCKED` | **Covered** | `test_jobs_store.py::test_concurrent_claims_never_share_a_lease`, `::test_claim_is_exclusive_and_bounded`, `::test_staging_is_not_claimable` |
| Heartbeat survives a blocking operation beyond the lease | **Covered** | `test_jobs_worker.py::test_heartbeat_keeps_a_long_operation_owned` (sleeps >2 lease intervals), `::test_lease_loss_stops_execution` |
| Stale fencing token cannot heartbeat / fail / cancel / succeed / publish | **Covered** | `test_jobs_worker.py::test_stale_token_cannot_publish_a_result`; `test_jobs_store.py::test_heartbeat_fails_without_ownership` |
| Lease expiry increments attempts and fails at the configured maximum | **Covered** | `test_jobs_store.py::test_stale_lease_is_reclaimed`, `::test_retryable_failure_requeues_then_fails`; `test_jobs_worker.py::test_failed_job_is_retried_then_final` |
| Queue full → stable 429, no staging artifacts | **Covered** | `test_jobs_store.py::test_queue_full_is_bounded`, `::test_queue_full_leaves_no_staging_row`, `test_jobs_api.py::test_queue_full_returns_429` |
| Cancellation: queued immediate, running at checkpoint, no result/cache | **Covered** | `test_jobs_api.py::test_cancel_job`; `test_jobs_store.py::test_cancel_only_affects_active_jobs`, `::test_running_cancel_is_a_request_acknowledged_by_the_worker`; `test_jobs_worker.py::test_cancel_before_processing_publishes_nothing`, `::test_cancelled_job_never_populates_cache`; e2e `smoke.spec.ts` Cancel scenario |
| TTL: result clock starts on completion; active jobs never expire; cleanup idempotent | **Covered** | `test_jobs_store.py::test_result_ttl_starts_on_completion`, `::test_active_jobs_are_never_swept`, `::test_ttl_sweep_removes_rows_and_artifacts`; `test_jobs_worker.py::test_sweep_removes_expired_jobs_and_artifacts` |
| SIGTERM: stop claims, drain in-flight jobs, heartbeats continue | **Partial** | Implemented in `back/src/jobs/worker.py`; live `docker compose restart worker` during Phase 8 stress did not duplicate/lose a result, but no controlled SIGTERM-with-in-flight drill is recorded (`TODO.md`, "Old stage 6 (deferred)") |
| Transient PostgreSQL outage must not kill the worker | **Covered** | `test_jobs_worker.py::test_worker_poll_survives_transient_db_error`, `::test_heartbeat_survives_transient_db_error`; live CI-stack database restart in Phase 8: `/ready` briefly 503 then ready, worker stayed up and the in-flight job succeeded |
| Crash after staging / DB commit / processing / ZIP creation / before commit | **Partial** | Staging-reconcile, repository-restart, stale-token and cancellation injection tests exist (`test_jobs_store.py::test_state_survives_repository_restart`, `test_jobs_worker.py`); full crash-window matrix on target hardware is TODO |
| Invalid user data non-retryable; transient failures bounded backoff | **Covered** | `test_jobs_store.py::test_non_retryable_failure_is_final`, `::test_retryable_failure_requeues_then_fails`; `back/src/jobs/worker.py::classify` |
| PostgreSQL outage/pool exhaustion returns a bounded error, health never hangs | **Partial** | `/ready` 503 and `/metrics` degradation covered by `test_readiness.py`; live PostgreSQL restart in Phase 8 showed bounded errors, `/health` 200 throughout and recovery after restart; pool exhaustion drill still TODO |
| Identical inputs → deterministic key + hit; keys bytes change the key | **Covered** | `test_cache.py::test_same_inputs_produce_the_same_key`, `::test_different_docx_bytes_produce_different_keys`, `::test_different_keys_yaml_changes_demask_key`, `::test_different_output_filename_changes_key` |
| Config digest / ruleset / manifest / namespace version invalidation | **Covered** | `test_cache.py::test_exception_digest_changes_key`, `::test_ruleset_version_bump_invalidates`, `::test_manifest_and_namespace_versions_invalidate` |
| Corrupt/missing artifact or Valkey loss → miss, not failure | **Covered** | `test_cache.py::test_corrupt_artifact_is_a_miss`, `::test_valkey_outage_degrades_to_misses`, `::test_real_valkey_corrupt_entry_evicts` |
| Cache hit verifies integrity; writes only after fenced success; no failed/cancelled cached | **Covered** | `test_cache.py::test_artifact_store_verifies_integrity`; `test_jobs_worker.py::test_cancelled_job_never_populates_cache`; `back/src/jobs/AGENTS.md` |
| Two concurrent identical jobs cannot corrupt a shared artifact | **Covered** | `test_jobs_worker.py::test_identical_concurrent_submissions_attach_to_one_job` (PostgreSQL dedupe, not a Valkey lock) |
| Cache TTL/memory/eviction bounded and observable; key names leak no PII | **Covered** | `test_cache.py::test_hit_miss_write_and_eviction`, `::test_key_does_not_leak_source_hash`; limits in `.env.example`; target-hardware memory pressure TODO |
| Entity spans survive window boundaries; offsets stay absolute | **Covered** | `test_windowed.py::test_all_entity_types_survive_window_boundaries`, `::test_long_address_crossing_boundary_is_not_cut`, `::test_windowed_offsets_are_absolute`, `::test_dedupe_boundary_spans_is_deterministic` |
| OOXML part coverage, namespace variants, >64 KiB markers, unsafe/embedded content | **Covered** | `test_ooxml.py::test_all_supported_parts_are_masked_and_restored`, `::test_default_namespace_is_processed_identically`, `::test_marker_after_64kib_is_still_found`, `::test_malformed_xml_is_rejected`, `::test_embedded_binary_object_is_rejected`; `test_docx_validation.py` |
| Sync vs job manifest parity; frontend reads manifest, not headers | **Covered** | `test_ooxml.py::test_sync_and_job_manifest_parity`, `::test_manifest_validation_rejects_bad_schema`, `test_mask_file.py`; e2e `smoke.spec.ts` manifest scenario |
| Stable error envelope, no PII/path/traceback in errors or rows or keys | **Covered** | `test_errors.py::test_every_error_has_code_detail_request_id_and_header`, `::test_request_validation_error_does_not_echo_submitted_pii`; `test_jobs_store.py::test_no_pii_columns_in_schema`; `test_cache.py::test_key_does_not_leak_source_hash` |
| Bounded route timeouts; streaming upload counts real bytes; results streamed | **Covered** | `timeouts.test.ts`, `mask.test.ts::proxy timeouts`; `test_uploads.py::test_stage_upload_counts_real_bytes_not_content_length`, `::test_stage_upload_rejects_oversized_stream_and_cleans_up`; `docs/API.md` §Proxy timeout policy |
| `Cache-Control: no-store` on result; verify remaining document/key responses | **Partial** | `test_jobs_api.py::test_result_serves_zip_with_no_store`; other responses still a `TODO.md` stage-6 item |
| OpenAPI → generated TS types with CI freshness | **TODO (deferred)** | Maintainer decision 2026-11-24; handwritten DTOs remain (`TODO.md`, `docs/API.md`) |
| Restore procedure for PostgreSQL + artifacts + config | **Recorded** | Procedure documented in `docs/OPERATIONS.md` §3; no automated restore drill is recorded |
| Liveness/readiness documented and exercised | **Partial** | `/health` used by Compose healthchecks and `health.spec.ts`; `/ready` implemented and unit-tested (`test_readiness.py`) but not wired into Compose/CI (deferred stage 6) |
| Operations runbooks for worker/DB/cache/storage failures | **Covered** | `docs/OPERATIONS.md` §6 |
| Target-hardware stress: queue pressure, worker/DB/Valkey restart, disk full, result expiry, cancellation, N concurrent large documents | **Partial (this host)** | Phase 8 ran an isolated CI-stack drill: 3 concurrent large jobs + identical-job dedupe, queued cancel → 204/cancelled, worker restart, PostgreSQL restart (after fixing transient DB error handling), Valkey `FLUSHDB`, result download 200; target hardware, disk full and controlled crash-window matrix still TODO (`TODO.md`) |
| Precision/recall corpus, TLS-bypass replacement, container hardening, branch protection | **TODO / deferred** | `TODO.md`; branch protection is a GitHub-side setting (`docs/WORKFLOW.md`) |

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
bun run --cwd front gen:labels   # then: git diff --exit-code -- front/src/shared/api/labels.generated.ts

# Frontend coverage gate (CI-equivalent, run from front/)
cd front && bun test --coverage --coverage-reporter=lcov && bun scripts/check-coverage.mjs 65

# Aggregators from the root
just lint
just typecheck
just test
```

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
stack (front **15001**, back **18001**) and first verifies discovery of
`health.spec.ts`, `smoke.spec.ts`, `rerender-smoke.spec.ts` and
`hover-smoke.spec.ts`. The CI front service sets a synthetic `SUPPORT_MAIL`
fixture: the header support link is rendered only when that runtime env var is
present, and the reduced-motion smoke test asserts that real control and its
hover state. Never run smoke against an unprepared stack; never paste real PII
into a live browser session.

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
`back` and `worker` (private `jobs-data` volume). Historical results of this
full matrix are recorded in `docs/PRODUCTION_READINESS.md` and `TODO.md`;
repeat the commands above for the current branch rather than inheriting a
recorded pass. CI branch protection / `CI required` remains a GitHub-side
setting and cannot be asserted from the repository.
