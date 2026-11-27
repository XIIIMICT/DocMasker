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

## Test-quality follow-ups (senior-qa audit, 2026-11-26)

- **CI smoke discovery guard is weaker than documented.** The workflow's
  `grep -q "smoke.spec.ts"` also matches `rerender-smoke.spec.ts` and
  `hover-smoke.spec.ts`, so losing the main spec (24 of 36 tests) would still
  pass. Acceptance: anchored filename match plus a minimum `--list` test count.
- **Sleep-as-evidence waits.** `front/e2e/smoke.spec.ts:226-238` uses a 300 ms
  sleep before asserting no request was sent, and `rerender-smoke.spec.ts:31`
  sleeps 500 ms to prove "no re-render". Replace with deterministic route
  interception / named debounce and a render counter. Nine magic-number sleeps
  in `hover-smoke.spec.ts` can collapse to one named grace-period helper.
- **Timing-dependent unit tests.** `jobs.test.ts:173` and
  `docx-viewer-scheduling.test.ts:6` use real timers; switch to fake timers and
  give `use-job-lifecycle.test.ts` `waitFor` calls explicit timeouts.
- **Small cleanup:** extract the repeated raw CSS file-input selector, use
  web-first `toHaveValue` instead of `expect.poll(inputValue())`, rename the
  misleading "fresh baseline" coverage test, align
  `local.playwright.config.ts` `testIgnore` with the CI config, and merge the
  same-level duplicate assertions listed in `TEST-QUALITY-AUDIT.md`.

## Deferred by maintainer decision

- **§8.2 — TypeScript types generated from live OpenAPI.** `openapi-typescript`
  (or equivalent), export from the running FastAPI app in CI, commit
  `front/src/shared/api/generated/schema.ts`, freshness gate, replace
  handwritten DTOs. Decision (2026-11-24): «поместим в TODO».
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
Valkey flush and result download; see `docs/TESTING.md` §2. Remaining:

- full controlled crash-window matrix (staging / DB commit / processing / ZIP
  creation / before result commit), SIGTERM-with-in-flight drill, disk full,
  pool exhaustion, result expiry, 1/2/N concurrent large documents;
- **backup/restore drill**: the PostgreSQL + artifacts + exception-config
  procedure is documented (`docs/OPERATIONS.md` §3) but has not been executed
  end-to-end; a consistency check for missing artifact objects should be run;
- Clean-install → migration → prod-image build → Compose startup → e2e repeated
  on the target machine (already green on this host).

## Process

- Stack order: `just db-stand` → `just seed` → `just dev` / `just prod`.
- One task branch per phase from current `dev`; `git merge --no-ff`, no squash;
  `dev` → `main` only squash after release approval.
- Before merge: `just lint`, `just typecheck`, `just test`, e2e, diff review,
  no secrets/debug artifacts. No commit/push without explicit instruction;
  never discard foreign or untracked work.
- Ambiguous product/config/process decisions → ask the maintainer.
