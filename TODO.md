# TODO — DocMasker open work

Living list of **genuinely deferred** work and OPEN gates. Implemented work is
in the code and its git history; durable decisions live in `docs/adr/`, current
contracts in the per-area guides and `docs/` files, and operator sign-off gates
in `docs/PRODUCTION_READINESS.md`. Legend: `todo` · `deferred` (maintainer
decision) · `blocked` (external step) · `OPEN` (operator gate).

## Follow-up remediation — current state

- **A1 bounded placeholder scan — done.** Contract and exact limits:
  `docs/DOCX_SUPPORT.md` §"Demasking input placeholder count"; measured evidence:
  `docs/PERFORMANCE.md` §"Frontend bounded placeholder scan". No runtime
  dependency was added; ordinary documents get a hard bound, not a throughput
  claim.
- **A2 strict result-manifest completeness — done.** Contract:
  `docs/API.md` §`manifest.json`; producer/consumer parity:
  `docs/DOCX_SUPPORT.md` §"Result manifest".
- **A4 smoke discovery — done.** Current inventory is **55 tests / 4 specs**
  (43/10/1/1) with the guard passing and full smoke 55/55 on `:5001`; the guard
  and `--list` commands are in `front/AGENTS.md` and `docs/TESTING.md` §1.
  Earlier 23/35/40/41 counts are dated evidence in git.
- **C1/C2 accessibility — done.** One canonical record:
  `docs/ACCESSIBILITY.md`. Screen-reader verification is **OUT OF SCOPE** by
  maintainer decision; automated axe scans and manual keyboard/focus review are
  the agreed coverage. Do not claim a screen-reader audit; a green axe scan is
  not an accessibility claim.
- **A3 job/sessionStorage runtime validation — DEFERRED by maintainer decision**
  (see below). Current summary, rejected alternatives and cross-project
  comparison: `docs/FRONTEND-AUDIT.md`.

Cross-project comparison (ReportCollector vs DocMasker): shared Next 16.4.6 /
React 19.3.1 / HeroUI 3.2.8 / Tailwind 4.3.5 / TS 7.0.2, React Compiler,
Turbopack `next analyze`, compile-only generated OpenAPI DTOs (dev-only
`swagger-typescript-api@13.15.0`), dev-only `@axe-core/playwright@4.14.1`, smoke
discovery guards and identical lint/format/typecheck/test scripts. Intentional
domain differences: DocMasker's DOCX preview/ZIP and masking workflow vs
ReportCollector's RevoGrid/collab; no cross-repository package and no new
runtime dependency.

## One-off Docker migrations — target-host gate OPEN

- Deployments migrate from the **same pinned prod image** as the app:
  `just migrate-docker` / `just migrate-docker-current` via
  `infra/compose.migrate.yaml` with `--no-deps` and `DM_BACK_IMAGE` set to a
  release tag/digest, never `latest`. No second migrate image, no new secret
  store; the root `.env` stays the only credential source.
- `back/src/scripts/check_migration_target.py` is a loud, stdlib-only preflight:
  target host/port/database/schema/image, never a password; `DATABASE_URL` wins
  over `POSTGRES_HOST_DOCKER`; an ambiguous local target needs `ALLOW_LOCAL_DB=1`.
  Run to `head` (`0001_jobs` → `0002_staging_recovery`) only; there is no
  automatic row deletion or destructive cleanup. `dm-jobs-data` is **not**
  covered by migrations.
- Canonical procedure: `docs/OPERATIONS.md` §2.2; maintenance window and rollback:
  `docs/RUNBOOKS.md` «Maintenance window»; recipes: `docs/WORKFLOW.md`. Nightly CI
  exercises this only on a throwaway schema in a disposable Postgres service;
  it is not part of `CI required`.
- **OPEN:** rehearsal on the authorized target host with the real deployed image
  pin and a matching backup/artifact copy. No production migration is claimed.

## Completed (index only)

Phases 0–8 and the follow-up fixes are merged into `dev`. Per-change evidence,
test counts and SHAs are in git history; current contracts are here:

| Area | Canonical current home |
|---|---|
| PostgreSQL foundation, durable jobs, caching, config, OOXML, API, windowing, ops docs | `docs/ARCHITECTURE.md`, `docs/API.md`, `docs/MASKING_CONFIGURATION.md`, `docs/DOCX_SUPPORT.md`, `docs/adr/` |
| Performance limits, measured before/after evidence | `docs/PERFORMANCE.md` |
| Startup, migrations, probes, backpressure, runbooks | `docs/OPERATIONS.md` §§1–6, `docs/RUNBOOKS.md` |
| Test matrix, commands, smoke discovery | `docs/TESTING.md` |
| CI, quality gates, deprecation policy | `docs/WORKFLOW.md` |
| Frontend audit fixes and decisions | `docs/FRONTEND-AUDIT.md`, `docs/ACCESSIBILITY.md`, `front/AGENTS.md` |
| Test-quality, preview isolation, OpenAPI types, coverage LCOV hardening | `docs/TESTING.md` §1, `docs/DOCX_SUPPORT.md`, `docs/API.md`, `front/AGENTS.md` |
| Storage readiness, no-store, worker defaults, legacy mask guard, backup sidecar, nightly suite | `docs/API.md`, `docs/OPERATIONS.md`, `back/AGENTS.md`, `docs/RUNBOOKS.md`, `docs/WORKFLOW.md` |

### Legacy JSON `/metrics` sunset (P1-6)

Breaking, no compatibility window: `GET /metrics` is Prometheus-only
(`text/plain`, `dm_*`) for any `Accept`; the route stays because Prometheus
scrapes it. Replacements and the monitoring-contract test are in
`docs/API.md` §`GET /metrics` and `docs/OPERATIONS.md` §4. The deprecation policy
is in `docs/WORKFLOW.md`.

### Frontend strict TypeScript indexing

The four enabled options and the coding rules are in `front/AGENTS.md` §"Strict
TypeScript options". `noPropertyAccessFromIndexSignature` stays disabled: it
added 146 diagnostics across deliberate record access, so the avalanche was
reverted while the four real guards stayed. No blanket `as`, `!`, `@ts-ignore`
or `any` was introduced.

## Deferred by maintainer decision

- **A3 — runtime validation of job data at untrusted boundaries.** Do not
  implement as part of another fix. `JobAccepted`, `JobStatus`, the
  `{mode, accepted}` envelope, the resumed text-job payload and status/result
  URL allowlisting are **not** runtime-validated; job payloads and stored
  `sessionStorage` values are consumed as-is. Needs its own task branch with
  tests for malformed fields, tampered `sessionStorage`, invalid URLs and no
  unexpected fetch.
- **Old stage 6 — observability/deployment hardening.** Done earlier:
  `X-Request-ID`, minimal `/ready` + `/metrics`, liveness/readiness split,
  metrics/logs for the monitoring stack. Remaining: structured JSON logs with
  `request_id` / job ID / worker ID, operation kind, safe state, durations, size
  buckets and error codes (never untrusted filenames); log rotation and
  long-term retention policy; `/ready` and a front readiness route wired into
  compose/CI healthchecks; a startup/shutdown hook check on the live stack;
  container hardening (`read_only`, `tmpfs`, `cap_drop`, `no-new-privileges`,
  `pids_limit`, limits, internal-only network), reverse-proxy/TLS example and
  rate limits. Valkey failure is a degraded cache state; artifact storage
  readiness is implemented (`docs/OPERATIONS.md` §4).
- **Frontend DOCX preview/highlighting performance.** Explicitly deferred;
  keep the current preview behavior working. Phase 3.3 removed repeat
  decompression on mode switches and bounded the scan; the one-time overlap with
  the `docx-preview` render needs a render-complete callback, not a DOM
  heuristic (`docs/DOCX_SUPPORT.md`).
- **OCR of raster image pixels in DOCX** — out of scope; the manifest reports
  `raster_image_ocr: not_performed` (`docs/DOCX_SUPPORT.md`).
- **Accuracy corpus** (versioned precision/recall/F1 per label; `score = 1.0`
  is a tiebreaker constant, not confidence) and a broad NER benchmark platform.
- **DM-1 — collision-safe keys format.** The implemented guard fails fast with
  422 when a source literal equals a marker allocated by the same mask run;
  collision-safe numbering is not expected in this single-tenant deployment.
  Deferred: `KEYS_FORMAT_VERSION` bump, cache/ruleset namespace bumps for a new
  marker scheme, a versioned keys envelope, reserved marker ids/numbering.
  Reopen only with an ADR when colliding input is observed. Failure-path cleanup
  (no partial `.dm-transform-*`, no cache entry on collision/cancel/parse error)
  is implemented — contract in `docs/DOCX_SUPPORT.md`.
- **P2 triggers — not triggered.** S3/MinIO artifacts + multi-host workers only
  on single-host disk pressure, CPU ceiling or HA need (one host still fits
  measured concurrency 4); OpenTelemetry only for cross-service latency
  debugging; SLOs/error budgets only after a baseline plus weeks of monitoring;
  a broker only if multi-host workers AND object storage AND measured PostgreSQL
  contention all appear. No broker was introduced.
- **Frontend refactor audit.** §4.5 scan/render sequencing is deferred (above);
  ReportCollector's ~15 ad-hoc animation timing values are a visual-risk change
  without a measurement; splitting DocMasker's stylesheet adds indirection
  without an ownership benefit; no React DevTools baseline exists, so no FPS or
  latency improvement is claimed. Zustand was decided against in
  `ReportCollector/docs/FRONTEND-REFACTOR.md`; reopen only on a measured
  subscription/rerender problem. Bundle and non-claim history: `docs/FRONTEND-AUDIT.md`.

## Blocked / external

- `README.md` — the maintainer creates it; do not touch without a request.
- Branch protection / ruleset + required check `CI required` — **GitHub
  repository setting**, not something the repository can assert. Keep the
  checklist; never claim it is active (`docs/WORKFLOW.md`).
- Trusted corporate CA for build-time TLS (`--allow-insecure-host`,
  `NODE_TLS_REJECT_UNAUTHORIZED=0` kept intentionally) — when a corporate
  certificate is available (`docs/SECURITY.md` §6).
- Auth / tenants / SSO — deliberately out of scope; private single-tenant
  deployment with network/reverse-proxy isolation. A public deployment needs
  OIDC/proxy auth + IDOR/CSRF protections and a new ADR.

## Still to verify on target hardware

Local disposable drills cover the crash matrix, handler-equivalent SIGTERM
drain, simulated ENOSPC, 1-slot pool exhaustion, expiry/byte audit and 1/2/N
synthetic DOCX batches (`docs/TESTING.md` §2, `docs/PERFORMANCE.md`); the
backup/restore rehearsal restored rows, artifact checksums and a synthetic
config digest without touching private exception values. These are **local**
passes, not target-hardware gates. Remaining, all OPEN in
`docs/PRODUCTION_READINESS.md`:

- real POSIX SIGTERM delivery, a real full filesystem and target-hardware
  repetitions of the crash/pool/concurrency drills;
- browser peak-memory/wall-time repetition for result-archive unpacking (only
  local SYNTHETIC fixtures were measured);
- backup/restore on the authorized host with the real RO-mounted private config
  and named volumes, plus a configured off-host destination;
- pinned-image migration rehearsal on the deployed schema;
- clean-install → migration → prod-image build → Compose health → e2e repeated
  on the target machine.

## Process

- Stack order: `just db-stand` → `just seed` → `just dev` / `just prod`.
- One task branch per phase from current `dev`; `git merge --no-ff`, no squash;
  `dev` → `main` only squash after release approval.
- Before merge: `just lint`, `just typecheck`, `just test`, e2e, diff review,
  no secrets/debug artifacts. No commit/push without explicit instruction;
  never discard foreign or untracked work.
- Ambiguous product/config/process decisions → ask the maintainer.
- Docs are durable, not a changelog: state how things are now; dates and
  "removed on …" belong in commit messages (`docs/WORKFLOW.md`).
