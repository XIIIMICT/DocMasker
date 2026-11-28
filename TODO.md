# TODO — DocMasker production readiness

Living list of **genuinely deferred** work. Completed phases and evidence live
in `docs/PRODUCTION_READINESS.md`; durable decisions in `docs/adr/`. The
implementation brief was reviewed and removed after folding its remaining items
into this file. Legend: `todo` · `deferred` (maintainer decision) ·
`blocked` (external step).

## Completed (index only)

Phases 0–8 of the Implementation Brief are merged into `dev` (one task branch
per phase, `git merge --no-ff`): PostgreSQL foundation + durable jobs, Valkey
cache, single masking configuration, OPC-aware DOCX transformer + one manifest,
stable API errors/streaming/event-loop safety, windowed detection, the
ReportCollector-style data stand with `just seed`, and the final three-level
AGENTS/doc refresh with release verification. Per-phase evidence, test counts
and before/after benchmarks: `docs/PRODUCTION_READINESS.md`,
`docs/PERFORMANCE.md`.

## Test-quality follow-ups (senior-qa audit, 2026-11-26) — CLOSED

All items below were implemented on `fix/dm-test-quality`; no assertion was
weakened. Evidence is from local runs on 2026-11-27, not from GitHub CI.

- **CI smoke discovery guard.** `grep -q "smoke.spec.ts"` was replaced by
  `front/e2e/check-smoke-discovery.mjs`, called from `.github/workflows/ci.yaml`.
  It requires the exact basenames, ≥20 tests in `smoke.spec.ts` and ≥34 in the
  project (current listing: 35/4 specs). Negative proof: temporarily renaming
  `smoke.spec.ts` made the guard exit 1 (`missing smoke spec: smoke.spec.ts`;
  12 tests found) and the file was restored.
- **Sleep-as-evidence.** The empty-keys test now intercepts `**/api/unmask`,
  counts requests, uses a two-frame grace and one valid demask as a barrier,
  then asserts exactly one request. The rerender test keeps a rendered-paragraph
  node identity as the render counter and waits for the named debounce pass.
  Eight hover grace sleeps use `waitForCloseGrace` (250 ms + margin); the 80 ms
  before-grace re-entry sample is intentionally retained as the behaviour under
  test.
- **Unit timing.** `jobs.test.ts` and `docx-viewer-scheduling.test.ts` use
  `jest.useFakeTimers()` with restore in `finally`/`afterEach`;
  `use-job-lifecycle.test.ts` `waitFor` calls have a 2 s timeout and named
  `onTimeout` errors.
- **Cleanup.** File-input CSS is one shared selector; three `expect.poll` value
  checks are web-first `toHaveValue`; the synthetic coverage test no longer
  claims a live baseline; exact-equality/no-PII and duplicate-enumeration
  assertions were collapsed without dropping any contract; the two initial-
  render smoke tests were merged (36 → 35 tests). `local.playwright.config.ts`
  now derives discovery from the committed config.
- **Verified:** `bun run --cwd front format:check`, `lint:check`, `typecheck`,
  `test` (233 pass / 0 fail; pre-existing HeroUI act warnings only) and `build`
  green; `--list` 35 tests from both configs; guard positive/negative as above;
  changed specs 2/2 runs at 34 passed each; full smoke 2/2 runs at 35 passed
  each against the local stack on `:5001`.

## Frontend DOCX preview generation isolation (2026-11-27) — CLOSED

- Finding 1 (P1): an already-started `docx-preview` `renderAsync` could keep
  writing into the shared live surface after a newer Blob committed. Each
  generation now renders into its own **detached** staging host and only the
  current generation moves those nodes into `.docx-viewer__surface`; stale
  completions, rejections and highlight timers are discarded without touching
  React state or the newer DOM. Evidence from the installed library (it only
  clears/appends into the supplied container; its measuring paths are
  experimental-only) and the regression list are in `docs/DOCX_SUPPORT.md`;
  the contract is in `front/AGENTS.md` §2 and `docs/ARCHITECTURE.md`. No new
  dependencies, no CSS change.
- **Verified:** `bun run --cwd front format:check`, `lint:check`, `typecheck`,
  `test` (237 pass / 0 fail) and `build` green; the lifecycle suite passed 3/3
  consecutive targeted runs (15 tests each). Pre-existing HeroUI act warnings
  in unrelated component tests only.

## Frontend DOCX highlight correctness (audit Phase 3.1, 2026-11-27) — CLOSED

Deterministic tests were written first in
`front/src/shared/ui/__tests__/docx-viewer.test.ts` for overlapping needles,
prefix collisions, repeated occurrences, empty needles, regex-literal needles,
non-BMP and combining Unicode, matches split across neighbouring text nodes,
paragraph boundaries and re-application after previous marks. Four cases
failed against the branch-base implementation and are the demonstrated defects:
a shorter needle listed first shadowed a longer one («Иван» vs «Иван Петров»),
and a needle split across rendered runs was never highlighted (two tests).
The other cases were already correct and are now locked by passing assertions;
no assertion was weakened. No full re-render was added, and the dynamic import,
detached per-generation host, generation guard, 120 ms debounce and timer
cancellation are unchanged.

Fix: `highlightTexts` deduplicates and sorts literal needles longest-first
before escaping them into one regex, so the earliest position always resolves
to the longest match regardless of keys order (the same rule as the backend's
`compile_placeholders`). Consecutive text nodes with the same nearest logical
container are matched as one string and each covered node slice gets its own
mark; matches never cross a paragraph, and the undo pass precedes every
application. Durable rule: `docs/DOCX_SUPPORT.md` §Frontend preview isolation
and `front/AGENTS.md` §2. The stale 5 ms comment in
`front/e2e/rerender-smoke.spec.ts` now names the actual 120 ms debounce.

**Cost — SYNTHETIC, MEASURED (no committed large DOCX fixture exists):** a
deterministic 6,000-paragraph / 18,000-text-node / 1,285,555-character DOM with
40 needles and 200 generated marks. happy-dom (5 traversal samples, 15 warm
and 6 cold first-pass samples per implementation): traversal median **5.64 ms**,
regex construction median **0.06 ms**, per-node regex search median **5.49 ms**,
container search median **11.85 ms**; warm full pass base **22.31 ms** → current
**21.40 ms** (min 17.48 → 16.25 ms), cold first pass base median **21.42 ms** →
current **30.06 ms**; mark count is identical (200) before and after
re-application. Chromium headless on the same SYNTHETIC DOM (5 rounds): base
first pass median **4.1 ms**, current first pass median **8.6 ms**, re-apply
median **5.8 ms** (well under a 16 ms frame budget); forced layout median
100.9 ms without marks vs 92.3 ms with marks — difference within noise.
`performance.memory` (~10 MB) and happy-dom heap deltas are not reliably
attributable under GC; node counts are the deterministic proxy, with no
duplication across seven repeated passes. No Worker, highlighter engine,
blanket memoization or new dependency: the warm gain is within noise and the
measured browser cost does not block typing or scrolling. Layout/paint was
observable only as forced reflow in Chromium; happy-dom has no layout engine.

- **Verified:** `bun run --cwd front format:check`, `lint:check`, `typecheck`
  (89 files), `test` (**276 pass / 0 fail**, 889 `expect()` calls, 31 files;
  pre-existing HeroUI act warnings only), `build` (9 routes), and
  `prek run --all-files` green. Dev stand already up on :5001 (no container
  restart needed): targeted `rerender-smoke.spec.ts` **1/1**, targeted DOCX
  preview/manifest subset **4/4**, targeted job-lifecycle subset **3/3**, and
  the full `smoke` project **35/35** (42.6 s) against
  `http://127.0.0.1:5001` via `local.playwright.config.ts`. The isolated
  production-image CI stack on :15001 was **not** running, so production smoke
  was not run; no target-hardware browser profiling was attempted.

## Frontend result-archive memory profile (audit Phase 3.2, 2026-11-27) — CLOSED

- **Lifecycle written down** (STATIC, file:line) in `docs/DOCX_SUPPORT.md`
  §"Result-archive unpacking": job handle, status JSON, result Blob, JSZip's
  single compressed `ArrayBuffer` + subarray views, extracted entry Blobs,
  validated manifest, preview DOM, object URLs and the separate demask scan —
  each with the point where it can be released. No large committed browser
  fixture exists; two deterministic flat ZIPs were generated locally and kept
  outside the repo (seed 20261127: 8,624,882 B media-heavy, 2,270,285 B
  text-heavy, `DEFLATE` to match `archives.py:40–48`).
- **SYNTHETIC / MEASURED (local, 7 runs per cell):** Chromium headless wall
  median **28.9 ms** [27.6..183.2] and peak Δ JS heap **25.75 MiB**
  [25.73..32.19] for the media-heavy `waitForJob` unpack, **10.6 ms**
  [9.3..12.3] / **6.89 MiB** [4.37..6.91] for text-heavy; Bun cross-check
  **22.3 ms** [19.4..36.0] / **4.6 ms** [4.3..5.7]. Phase split (media):
  central-directory load 8.9 ms, inflate + Blob materialisation 20.2 ms.
  Post-GC CDP `JSHeapUsedSize` delta ≈ 0.25 MiB — no retained archive. These
  are synthetic local numbers, **not production measurements**; target-hardware
  browser profiling stays in "Still to verify on target hardware".
- **Proven defect, tests first:** four new deterministic tests failed on the
  branch base — an already-finished job whose cancellation was confirmed while
  its ZIP was being read still published a result, an abort during the archive
  read still unpacked, a stale accepted job produced a bundle, and unmount left
  the polling controller live. Fix: abort checks at every await boundary in
  `waitForJob`, `entries.clear()` on failure/abort so no partial result escapes,
  and `useJobLifecycle` aborting its local controller on unmount **without**
  issuing a `DELETE`. Error stays the fixed Russian `JobFailedError("cancelled")`;
  nothing is logged and no filename/value is exposed.
- **Decision: KEEP JSZip 3.10.2.** It is lazy/single-flight, and its reader
  keeps one `ArrayBuffer` with subarray views (not one copy per entry).
  Explicitly deleting `zip.files` after extraction changed the Chromium peak
  25.75 → 25.74 MiB and wall 10.6 → 9.6 ms (within spread), so no release
  code was added on that basis. Local tarball probes: `fflate@0.8.3`
  `unzipSync` 11.8 ms in Bun but synchronous on the main thread; `@zip.js/zip.js@2.25.0`
  streaming `BlobReader` 23.0 ms / 77.11 MiB peak versus JSZip 22.3 ms /
  67.36 MiB. Neither justifies a new runtime dependency. Rewriting every ZIP
  call site for a synthetic ~20 ms win was rejected.
- **No backend protocol change:** the flat ZIP is built atomically
  (`archives.py:40–62`) and job results are one fenced, attempt-scoped artifact
  (`back/src/jobs/AGENTS.md` §"Artifacts & cache ownership") under the global
  `no-store` policy. Separate streams would add partial states, new DB keys and
  filename/manifest-ordering risk — rejected for a few client lines. Object
  URLs are already revoked in `finally` on success/failure; no copy or change.
- **Verified:** `bun run --cwd front format:check`, `lint:check`, `typecheck`
  (89 files), `test` (**287 pass / 0 fail**, 913 `expect()` calls, 31 files;
  pre-existing HeroUI act warnings only), `build` (9 routes, compiled in
  574 ms on the final run / 899 ms on the first) and `prek run --all-files`
  green. Targeted Playwright (job + manifest subset)
  **3/3** in 3.3 s and full `smoke` project **40/40** in 41.4 s against
  `http://127.0.0.1:5001` via `local.playwright.config.ts`; smoke discovery
  guard **40 tests / 4 specs**. No push or merge.

## OpenAPI → TypeScript declarations (finding 8, 2026-11-27) — CLOSED

The earlier maintainer deferral (2026-11-24, «поместим в TODO») is superseded by
an evidence-based decision: compile-time HTTP DTOs are generated from the live
schema; runtime behavior is unchanged.

- **Installed toolchain evidence:** TypeScript **7.0.2** in `front/bun.lock` and
  `node_modules/typescript/package.json`; `bunx --no-install tsc --version`
  reports 7.0.2. Node 26.13.1, Bun 1.4.4.
- **Rejected — `openapi-typescript@7.13.0`:** peer `typescript: ^5.x`, so TS7
  needs an unsafe override; on TS7 it also fails at runtime
  (`ts.factory.createKeywordTypeNode` is undefined).
- **Rejected — `@hey-api/openapi-ts@0.99.0`:** peer
  `typescript: >=5.5.3 || >=6.0.0` (semver-compatible with 7.0.2) but it fails
  at runtime on TS7 (`ts.SyntaxKind` is undefined). Both would need a second
  TypeScript 5 toolchain solely for generation.
- **Chosen — `swagger-typescript-api@13.15.0`:** no TypeScript peer, Node ≥20,
  pinned **dev-only** in `front/package.json`/`bun.lock`. With
  `--no-client --enum-style=union --type-only-imports` it emits only
  `front/src/shared/api/generated/openapi.d.ts`: no HTTP client SDK and no new
  runtime dependency. Two runs were byte-identical; the output compiles with
  TypeScript 7 (`tsc --noEmit --strict`).
- **Contract source:** `python -m src.openapi_export` (deterministic in-process
  `app.openapi()` export). Regenerate: `bun run --cwd front gen:openapi`; CI gate:
  `bun run --cwd front gen:openapi --check` (job `openapi-contract`). Negative
  proof: tampering with the committed declaration made the check exit 1.
- **Scope:** generated types are compile-time only and assert no runtime
  validation. Not covered (still handwritten/other contracts): the ZIP-internal
  `manifest.json`, keys YAML, `labels.yaml` → `labels.generated.ts`, Next.js
  proxy routes, and the UI-only `JobResult`.
- **Revisit only if** a generator exposes named DTOs with a TS7-compatible
  compiler API and peer range without a second toolchain, or the HTTP contract
  itself changes (then regenerate).

## Frontend strict TypeScript indexing (audit Phase 4.1, 2026-11-27) — CLOSED

`front/tsconfig.json` now matches ReportCollector's two safeguards and adds two
zero-diagnostic guards: `noUncheckedIndexedAccess`,
`exactOptionalPropertyTypes`, `noImplicitReturns` and
`noFallthroughCasesInSwitch`. Measured with TypeScript 7.0.2
`bunx --no-install tsc --noEmit --incremental false -p tsconfig.json` from
`front/`: baseline 0 diagnostics; the two primary options gave **21** (13 from
`noUncheckedIndexedAccess`, 8 from `exactOptionalPropertyTypes`, no overlap);
after honest fixes **0**. `noPropertyAccessFromIndexSignature` was evaluated
separately: **167 total = 21 + 146 new**, concentrated in
`manifest-validation.ts` (39) and `__tests__/manifest-validation.test.ts` (79)
across 17 files — an unreviewable avalanche on deliberate dot access to record
types, so it was reverted while the other four options stayed. A temporary
probe confirmed the two extra guards are not vacuous (TS7030 for a partial
return, TS7029 for a switch fallthrough; probe removed, final count 0). No new
dependency; generated `openapi.d.ts` and `labels.generated.ts` are untouched
and compile unchanged. No blanket `as`, `!`, `@ts-ignore`, `any`, widened types
or weakened tests. Durable coding rules: `front/AGENTS.md` §"Strict TypeScript
options".

- **Verified:** `bun run --cwd front format:check`, `lint:check`, `typecheck`
  (89 files), `test` (276 pass / 0 fail, 889 `expect()` calls, 31 files;
  pre-existing HeroUI act warnings only), `build` (9 routes) and
  `gen:openapi --check` green; `prek run --all-files` all hooks passed; full
  `smoke` project **35/35** (43.2 s) against `http://127.0.0.1:5001` via
  `local.playwright.config.ts`. Committed on `fix/strict-ts-indexing`; no
  push or merge.

## Coverage gate LCOV hardening (finding 2, 2026-11-27) — CLOSED

- `front/scripts/check-coverage-parse.mjs::parseLcov` now validates each source
  record with a strict `SF → LF → LH → end_of_record` state machine. Duplicate
  `LF`/`LH` (including `LF` after `LH`), `LH` before `LF`, a second `SF` before
  finalizing, incomplete pairs, `LH > LF` and per-record counts or aggregate
  sums outside the safe integer range all throw `CoverageGateError` before the
  record is accepted. Unrelated LCOV tokens (`TN`, `DA`, `FN`, ...) stay ignored;
  parsing/evaluation remain pure — file reads and process exit stay in
  `check-coverage.mjs`. No new dependencies.
- **Verified:** `bun run --cwd front format:check`, `lint:check`, `typecheck`,
  `test` (244 pass / 0 fail) and `build` green; coverage gate
  `bun test --coverage --coverage-reporter=lcov` → `bun scripts/check-coverage.mjs 65`
  reported 86.40% (2002/2317). The malformed `LF:100 / LF:10 / LH:10` record
  failed the CLI gate with `Duplicate LF ...` and exit code 1. Valid multi-file,
  CRLF and zero-hit reports are pinned by tests.

## Storage readiness and API cache policy (2026-11-27) — CLOSED

- `ApiNoStoreMiddleware` sets `Cache-Control: no-store` + `Pragma: no-cache`
  on every `/api/*` response (PII JSON, ZIPs, errors); the Next proxies forward
  both headers. The pre-existing ZIP `no-store` is preserved. Evidence:
  `back/tests/test_response_headers.py`, front proxy tests.
- `jobs/readiness.py` checks `JOBS_MIN_FREE_BYTES` (default 1 GiB) + one write
  probe at worker startup and job admission, before any staging row/artifact;
  failure is the typed 503 `artifact_storage_unavailable` envelope (Russian
  `detail`), never an ambiguous 500. Full/read-only disk paths are simulated in
  `back/tests/test_storage_readiness.py`. No new metric/log field: the existing
  `dm_artifact_disk_free_bytes` panel/alert carry the 1 GiB threshold together
  with `docs/OPERATIONS.md`.
- Verified locally: ruff/ty, backend pytest with the disposable PostgreSQL stand,
  front typecheck/test, prek, compose config and actionlint; target-hardware
  full-filesystem rehearsal remains in the "Still to verify" list below.

## Worker concurrency and explicit backpressure (2026-11-27) — CLOSED

- Defaults are measurement-derived and pinned in tests: `JOBS_CONCURRENCY=4`
  (`floor(1024 / (159 MB × 1.5)) = 4` from the 16-job RSS/latency burst),
  `JOBS_MAX_ACTIVE=32` (2 × the measured 16-submission burst),
  `JOBS_RETRY_AFTER_SECONDS=45` (drain of the 28-slot backlog at p95 6.2 s).
  Config rejects `JOBS_MAX_ACTIVE <= JOBS_CONCURRENCY`.
- Typed `queue_full` 429 keeps the `{code, detail, request_id}` + `X-Request-ID`
  envelope and adds integer `Retry-After`; the Next proxies forward it, with
  no client auto-retry. Alerts (24 active, 4 running) and two worker panels
  were added; thresholds are PROVISIONAL and must move with the settings.
- **Verified:** backend 533 passed / 4 skipped, 87.01% coverage; front
  typecheck/lint/format/test (248 pass), build and 86.44% line gate;
  `promtool check rules` 20 rules SUCCESS; prek and both compose configs green.
  A live drill accepted 5 × 202 then returned the typed 429 with Russian
  detail and `Retry-After: 45` (no server-side retry).
- **Soak evidence:** a full **1800.5 s** (30-minute target met) run at
  concurrency 4 completed 7,824 synthetic DOCX jobs with 0 failures; 346 raw
  RSS samples are preserved outside the repo (`…/Temp/opencode/dm_bp_soak_c4_final.jsonl`).
  No unbounded growth appeared in the Windows working-set proxy, but its
  ~15 GB plateau is a harness-process figure, not container RSS; the 1 GiB
  budget assumption and target-host Linux RSS soak remain **OPEN** gates.
  Numbers, method and caveats: `docs/PERFORMANCE.md` §Concurrency and queue budget.

## Legacy quadratic mask path (P1-5, 2026-11-27) — CLOSED

- Phase 0 fact re-verified from code: `RuleBasedMasker.mask()` has zero production call sites (AST guard over `back/src` finds no `.mask` attribute access; the only `rg '\.mask\(' back/src` hits are docstrings/messages, and `RuleBasedMasker` imports in `service.py`/`docx_service.py`/`detectors.py` use `detect()`/types only). No migration was needed: request/worker paths already run `detect()` + `service.mask_text` (one validated join, O(|text| + entities)).
- Kept as a deprecated test/benchmark reference rather than removed: deletion would break legitimate reference assertions in `test_rule_based.py`, `test_config.py`, `test_address_prefilter.py`, `test_unmask.py` and `back/benchmarks/docx_mask.py`. `warnings.warn(DeprecationWarning, stacklevel=2)` + a contract docstring mark it; the linear path and its metrics are untouched.
- Added `back/src/scripts/check_legacy_mask_path.py`: AST guard over `back/src` rejecting `.mask` attribute access and `getattr`/`methodcaller(..., "mask")` lookups, wired into the CI `back` job. `back/tests/test_legacy_mask_path_guard.py` proves the real tree is clean, direct/dynamic calls fail, `detect()` + `mask_text()` pass, an allow-list entry is skipped, and both CLI exit codes.
- Verified (local, 2026-11-27): `python -m src.scripts.check_legacy_mask_path` exit 0 on the real tree; temporary `back/src/_guard_probe.py` with `masker.mask(text)` → exit 1 (`_guard_probe.py:5`), removed → exit 0; `pytest -q tests/test_legacy_mask_path_guard.py` 8 passed; full `pytest -q --cov=src --cov-branch --cov-fail-under=80` with the disposable PG/Valkey stand 543 passed / 2 skipped, 87.28% branch coverage; ruff check/format, `ty check --python-platform linux`, `bun run --cwd front typecheck` (86 files) and `test` (248 pass / 0 fail, pre-existing HeroUI act warnings), `prek run --all-files`, actionlint 1.7.7 — all exit 0.

## Legacy JSON `/metrics` sunset (P1-6, 2026-11-27) — CLOSED (breaking)

**Release note — BREAKING, no compatibility window.** `GET /metrics` no longer
has a legacy JSON representation. Every request (any `Accept`) now returns the
`dm_*` Prometheus exposition with `Content-Type: text/plain`, so a stale JSON
client fails loudly on the content type instead of receiving
`active_jobs`/`cache.*`. There is no deprecation header, log-only shim,
fallback representation or scheduled follow-up removal. The scrape path,
Grafana dashboards, alert rules and Prometheus config are unchanged.

**Interpretation of the deprecation policy.** `docs/WORKFLOW.md` §Deprecation
policy declares no timed compatibility window: breaking removals are loud
(precedents: `MASK_LABELS` fails startup, ADR-0001; `GET /api/profiles` removed,
ADR-0005). Keeping a shim "for one release" would invent the very window the
policy forbids, and keeping both representations forever is not acceptable
either. So the JSON representation is removed now, with the breaking change
recorded here and in `docs/API.md`. The `/metrics` **route** cannot be deleted:
Prometheus scrapes it (`infra/prometheus/prometheus.yml.tpl`) and the
dashboards/alerts are live consumers of `dm_*`. "Removed" therefore means the
legacy JSON contract is gone; the endpoint is Prometheus-only (not a 404,
which would break monitoring).

- Endpoint: `back/src/main.py` — no PostgreSQL/Valkey call on the scrape, so a
  dependency outage cannot degrade it. Replacements: `dm_jobs_active{kind}`
  for `active_jobs`, `dm_cache_operations_total` / `dm_cache_gc_*` for cache
  counters, `dm_dependency_check_duration_seconds{outcome="error"}` plus a
  `/ready` 503 for dependency failures.
- Tests: `test_metrics.py::test_backend_metrics_endpoint_is_prometheus_only`
  (plain / `text/plain` / `application/json` all return Prometheus text, no
  `active_jobs`, not JSON), `test_readiness.py::test_metrics_scrape_is_prometheus_only_and_db_independent`,
  and `test_monitoring_contract.py` (every provisioned dashboard/alert series
  is `dm_*`/`up`, no legacy JSON key in any expression, both scrape jobs keep
  `metrics_path: /metrics`). No assertion was weakened.
- Verified (local, 2026-11-27): ruff check/format and `ty check` green; full
  pytest with the disposable PostgreSQL/Valkey stand (`DATABASE_URL` +
  `TEST_DATABASE_URL`) 543 passed / 4 skipped, 87.19% branch coverage; front
  typecheck (86 files) and `bun test` 248 pass / 0 fail (pre-existing HeroUI
  act warnings); `prek run --all-files` all hooks passed. No workflow or alert
  rule was edited, so actionlint / `promtool check rules` were not applicable;
  the monitoring contract test parses the unchanged rules and dashboards.

## PostgreSQL backup sidecar (P1-8, 2026-11-27) — CLOSED (local)

- Phase 0 fact: no automated PostgreSQL backup; only a manual `pg_dump` +
  artifact-copy procedure, target-host rehearsal OPEN. Chosen `pg_dump -Fc`
  custom-format sidecar (`infra/backup/`, `dm-pgbackup`), **not** WAL-G /
  pgBackRest: no PITR requirement and no server-side archive access in this
  repository. Escalation criteria (stricter RPO, PITR, WAL access, multiple DBs,
  automatic artifact snapshots) are recorded in `docs/RUNBOOKS.md`.
- Assumed **RPO ≤ 24 h** (not an accepted SLO), full dump daily 02:00Z,
  `BACKUP_RETENTION_COUNT=7` in `dm-postgres-backups`, weekly Sunday 03:00Z
  restore drill. `.part` → `pg_restore --list` → restore-based data fingerprint
  → atomic rename; fingerprint mismatch or any tool failure exits non-zero.
- `BACKUP_OFFHOST_TARGET` is OPTIONAL and empty by default: when empty,
  `dm_pg_backup_offhost_configured=0` and **the off-host requirement is NOT
  satisfied**. No credentials are stored in the repo; external/production DB
  caveat and `CREATEDB` requirement are in `docs/RUNBOOKS.md`.
- Monitoring contract landed in the same change: five alerts in the
  `docmasker_pg_backup` group, the `docmasker-pg-backup` scrape job, the
  «Резервные копии PostgreSQL» dashboard and the `dm_pg_backup_*` contract in
  `docs/RUNBOOKS.md` / `back/tests/test_monitoring_contract.py`.
- Verified: exact restore-drill commands, output and data-fidelity check in
  `docs/PRODUCTION_READINESS.md`; `docker compose config`, `promtool check
  rules`, ruff/ty + full pytest (≥80%), front test and `prek run --all-files`.
- **Still OPEN:** off-host destination (nothing configured/contacted), target
  host with real RO config and named volumes, RTO/stricter RPO decision, alert
  mail delivery, PITR/WAL archiving and automatic artifact-spool snapshots.

## Nightly heavy suite (P1-9, DM part, 2026-11-27) — ADDED, schedule pending

- `.github/workflows/nightly.yaml` runs daily **04:13 UTC** (and on
  `workflow_dispatch`) off the per-PR path: full backend coverage on disposable
  PostgreSQL/Valkey, the frontend 65% coverage gate, OpenAPI freshness,
  report-only pip-audit/bun audit plus dependency freshness, blocking gitleaks,
  and Playwright smoke against the production images on the isolated CI stand.
  Artifacts are retained 7 days; stacks are torn down with `if: always()`.
- Not part of `CI required`: no aggregation job, all job names prefixed
  `Nightly`; `ci.yaml` and branch protection untouched. Contract, schedule
  rationale and notification gap: `docs/WORKFLOW.md` §CI.
- Day-one security policy: the two new scans are report-only; gitleaks stays
  blocking as an existing green gate. Failure routing is **GitHub UI
  notifications only** — no Actions→Alertmanager/SMTP wiring exists in any of
  the three repositories (verified 2026-11-27), and no credentials are invented.
- **Still OPEN:** a real scheduled night cannot be observed until this is
  merged/pushed to the default branch — no claim of a passed night is made
  here. Alertmanager/SMTP delivery and promoting the report-only scans to
  blocking gates both need a maintainer decision and, for delivery, a supply
  of credentials.

## Support-pill timing restore (post-audit user decision, 2026-11-28) — CLOSED

- User feedback (authoritative): after audit `02f2d58` the «Связаться с
  разработчиком» pill expanded "слишком быстро"; the maintainer asked to restore
  the ORIGINAL expansion timing. Decision: revert only the support-pill motion —
  all other audit fixes and `docs/FRONTEND-AUDIT-BASELINE.md` stay as evidence.
- Restored exactly in `front/src/app/globals.css`: `gap var(--app-motion-gap)
  cubic-bezier(0.22, 1, 0.36, 1)` on `.support-link`, `max-width
  var(--app-motion-expand) cubic-bezier(0.22, 1, 0.36, 1)` on
  `.support-link__label`, tokens `140ms` / `180ms` / `220ms`; the added
  `transform: translateX(-0.25rem)` reveal and its hover/compact resets were
  removed. `prefers-reduced-motion: reduce` still zeroes all three tokens.
- Accepted tradeoff: animating `gap`/`max-width` forces layout (audit measurement:
  444 Layout events / 63.9 ms over eight hover cycles). The maintainer prefers the
  original feel over that cost; no snap/transform substitute remains. Timing is
  pinned by `globals-conventions.test.ts` and a Chromium Playwright test that
  freezes the real transitions at 80 ms, plus reduced-motion, hover/keyboard and
  compact-Tooltip end-state assertions — no assertion was weakened.
- Durable record: `docs/ARCHITECTURE.md`, `docs/FRONTEND-AUDIT.md` §9,
  `front/AGENTS.md` §"Critical invariants", `docs/TESTING.md`.
- **Verified:** front `format:check` / `lint:check` / `typecheck` / `test`
  (**287 pass / 0 fail**) / `build`, `prek run --all-files`, targeted Playwright
  **4/4** and full `smoke` **41/41** on `http://127.0.0.1:5001` via
  `local.playwright.config.ts`; discovery guard 41 tests in 4 specs. No push,
  no merge.

## Bounded DOCX placeholder scan (A1, 2026-11-28) — CLOSED

- The pre-operation demask tooltip scan no longer materialises an XML entry
  before checking its size. `bounded-zip-scan.ts` walks the ZIP central
  directory with a narrow read-only parser and inflates with the browser's
  `DecompressionStream("deflate-raw")`; JSZip 3.10.2 stays only for result
  archives. Guarantees, non-guarantees and the exact limits are in
  `docs/DOCX_SUPPORT.md` §"Demasking input placeholder count".
- Proven bound: with understated ZIP metadata a 61 MB synthetic XML part was
  stopped after 991,506 decoded bytes under a 1 MiB ceiling; the old code
  materialised all 61,260,000 units. Deterministic tests pin exact chunk
  accounting, UTF-8 and marker-boundary semantics, metadata preflight,
  compressed-size rejection before `arrayBuffer()`, malformed/hostile archives
  and single-flight. No runtime dependency was added — reader comparison and
  before/after numbers: `docs/PERFORMANCE.md` §"Frontend bounded placeholder
  scan".
- **Verified:** front `format:check`, `lint:check`, `typecheck`, `test`, coverage
  gate (65%), `build`, `gen:openapi --check`, `prek run --all-files`, Playwright
  discovery guard and smoke on `http://127.0.0.1:5001` — 304 unit tests / 0 fail,
  90.01% line coverage, 99 formatted files, 90 typechecked files, 41 smoke tests
  in 4 specs. Two timing-sensitive animation tests flaked on separate full
  smoke runs and passed on rerun and in isolation; no assertion was weakened.
  No push or merge.

## Deferred by maintainer decision

- **A3 — runtime validation of job data at untrusted boundaries — DEFERRED by
  maintainer decision (2026-11-28).** Do not implement here: this task is scoped
  to A1 only and the maintainer explicitly deferred network/sessionStorage
  validation (`JobAccepted`, `JobStatus`, the `{mode, accepted}` envelope, the
  resumed text-job payload and status/result URL allowlisting). Reason: it is an
  independent boundary-hardening change with its own protocol/UX decisions and
  must not be smuggled into a resource-bound fix. Revisit **after A1, A2
  (manifest `complete`/coverage) and C1 (accessibility scans) are landed and
  green**, as its own task branch with tests for malformed fields, tampered
  `sessionStorage`, invalid URLs and no unexpected fetch. No A3 guard or URL
  allowlist was added on `fix/bounded-placeholder-scan`.
- **Old stage 6 — observability/deployment hardening.** Already done earlier:
  `X-Request-ID`, minimal `/ready` + `/metrics`, liveness/readiness split.
  Remaining from Brief §11/§14: structured JSON logs with `request_id` / job ID
  / worker ID, operation kind, safe state, durations, size buckets and error
  codes (never untrusted filenames); essential metrics (request latency/error
  counts, queue depth and oldest queued age, running jobs, lease losses,
  retries, cancellations, job durations, artifact sizes, DB pool saturation,
  worker concurrency, memory/CPU where available); Valkey failure stays a
  degraded cache state (artifact storage readiness is now implemented, see
  "Storage readiness and API cache policy" above); compose/CI healthchecks
  wired to `/ready`; front
  readiness route; startup/shutdown hook check on the live stack; container
  hardening (`read_only`, `tmpfs`, `cap_drop`, `no-new-privileges`,
  `pids_limit`, limits, internal-only network), reverse-proxy/TLS example,
  rate limits, log rotation.
- **Frontend DOCX preview/highlighting performance** — explicitly deferred in
  brief §0; keep current preview behavior working.
- **OCR of raster image pixels in DOCX** — out of scope, documented as
  `raster_image_ocr: not_performed` in `manifest.json`.
- **Accuracy corpus** (versioned precision/recall/F1 per label; `score=1.0` is
  a tiebreaker constant, not confidence) and broad NER benchmark platform —
  deferred in brief §0.
- **DM-1 — collision-safe keys format (deferred by maintainer decision
  2026-11-26).** The implemented guard fails fast with a 422 when a source
  literal equals a marker allocated by the same mask operation; collisions are
  not expected in this single-tenant deployment. Explicitly deferred: a
  `KEYS_FORMAT_VERSION` bump, `CACHE_NAMESPACE_VERSION` /
  `MASKING_RULESET_VERSION` bumps for a new marker scheme, a versioned keys
  envelope, reserved marker numbering / explicit marker ids, and a reserved
  namespace that makes source collisions impossible. Reopen only with an ADR
  when colliding input is actually observed.
- **DM-1 failure-path cleanup (done, no format change).** A collision,
  cancellation, parse/serialization error or worker failure now removes every
  `.dm-transform-*` temp/partial output before it can be published or cached;
  a pre-existing artifact is never deleted. The fail-closed `marker_collision`
  contract and legacy keys format stay exactly as above. Evidence:
  `back/tests/test_transform_cleanup.py`, `back/tests/test_jobs_worker.py`.

## Frontend refactor audit (2026-11-26)

- **§4.5 — DOCX scan/highlight sequencing (deferred).** Preview render and
  placeholder counting still start together when a DOCX is selected; both
  decompress the same file, doubling peak memory. Reliable sequencing needs a
  render-complete callback through `DocxViewer` → `masker-page.tsx` → workflow;
  a DOM-observation heuristic was rejected as brittle. Phase 3.3 removed the
  *repeat* decompression on mode switches/re-selection (per-Blob single-flight
  in `placeholder-count.ts`) and bounded the scan; the one-time overlap with the
  preview render remains deferred — evidence in `docs/DOCX_SUPPORT.md`
  §"Demasking input placeholder count".
- **§8.2 — animation tokens on the ReportCollector side (deferred).**
  DocMasker's three app-owned transitions use `--app-motion-*` tokens; the
  ReportCollector stylesheet still has ~15 ad-hoc timing values. Consolidating
  them is a visual-risk change without a measurement.
- **§9 — CSS split (deferred).** DocMasker's stylesheet is ~600 lines with
  stable domains; splitting it into small files would add indirection without
  an ownership benefit. Revisit only if tokens are extracted or a new domain
  appears.
- **§3.2 / §11 — runtime measurements.** No React DevTools / Performance
  baseline was recorded before the frontend refactor, so no FPS or latency
  improvement is claimed. Bundle before/after evidence and the explicit
  non-claims are in the workspace root `FRONTEND-AUDIT-FINAL-REPORT.md`;
  reduced-motion and narrow-screen browser coverage lives in
  `front/e2e/smoke.spec.ts`.
- **Zustand** — decision recorded in `ReportCollector/docs/FRONTEND-REFACTOR.md`:
  not needed. Reopen only if a measured subscription/rerender problem appears.

## Blocked / external

- `README.md` — the maintainer creates it; do not touch without a request.
- Branch protection / ruleset + required check `CI required` — **GitHub
  repository setting**, not something the repository can assert. Keep the
  checklist; never claim it is active.
- Trusted corporate CA for build-time TLS (`--allow-insecure-host`,
  `NODE_TLS_REJECT_UNAUTHORIZED=0` kept intentionally, as in ReportCollector) —
  when a corporate certificate is available.
- Auth / tenants / SSO — deliberately out of scope; private single-tenant
  deployment with network/reverse-proxy isolation. If a public deployment is
  ever wanted: OIDC/proxy auth + IDOR/CSRF protections, new ADR.

## Still to verify on target hardware

The local isolated CI-stack drill covered queue pressure, identical-job
dedupe, cancellation, worker restart, PostgreSQL restart (after F-25 fix),
Valkey flush and result download; see `docs/TESTING.md` §2. The DM-3 harness
(`back/src/scripts/operational_drills.py`) then ran on a dedicated disposable
PostgreSQL container on 2026-11-27: 13 drills passed covering the full
crash-window matrix, handler-equivalent SIGTERM drain, simulated ENOSPC,
1-slot pool exhaustion, expiry/missing/orphan byte audit, cross-job artifact
isolation and 1/2/N synthetic DOCX batches (with wall/CPU/RSS/queue/pool
measurements in `docs/PERFORMANCE.md`). A local-disposable backup/restore
rehearsal also passed (rows, artifact checksums and a synthetic config digest);
no private exception value was read. These are **local** passes, not target
hardware.

Remaining (all OPEN, see the target-host checklist in
`docs/PRODUCTION_READINESS.md`):

- real POSIX SIGTERM delivery, a real full filesystem and target-hardware
  repetitions of the crash/pool/concurrency drills;
- browser peak-memory/wall-time repetition for result-archive unpacking on the
  target host (only local SYNTHETIC fixtures were measured in Phase 3.2);
- backup/restore on the authorized host with the real RO-mounted exception
  config and named volumes (now also the `dm-pgbackup` sidecar + drill); only
  the synthetic-config/disposable-stand rehearsals exist locally;
- Clean-install → migration → prod-image build → Compose health → e2e repeated
  on the target machine (already green on this host, but not a gate discharge).

## Process

- Stack order: `just db-stand` → `just seed` → `just dev` / `just prod`.
- One task branch per phase from current `dev`; `git merge --no-ff`, no squash;
  `dev` → `main` only squash after release approval.
- Before merge: `just lint`, `just typecheck`, `just test`, e2e, diff review,
  no secrets/debug artifacts. No commit/push without explicit instruction;
  never discard foreign or untracked work.
- Ambiguous product/config/process decisions → ask the maintainer.
