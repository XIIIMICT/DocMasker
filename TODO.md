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

## Deferred by maintainer decision

- **Old stage 6 — observability/deployment hardening.** Already done earlier:
  `X-Request-ID`, minimal `/ready` + `/metrics`, liveness/readiness split.
  Remaining from Brief §11/§14: structured JSON logs with `request_id` / job ID
  / worker ID, operation kind, safe state, durations, size buckets and error
  codes (never untrusted filenames); essential metrics (request latency/error
  counts, queue depth and oldest queued age, running jobs, lease losses,
  retries, cancellations, job durations, artifact sizes, DB pool saturation,
  worker concurrency, memory/CPU where available); `Cache-Control: no-store`
  on every result/document/key response (file ZIPs already send it, text/keys
  responses do not yet); readiness must also verify **artifact storage**
  availability (currently PostgreSQL only) while Valkey failure stays a
  degraded cache state; compose/CI healthchecks wired to `/ready`; front
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
  a DOM-observation heuristic was rejected as brittle.
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
- backup/restore on the authorized host with the real RO-mounted exception
  config and named volumes; only the synthetic-config rehearsal exists locally;
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
