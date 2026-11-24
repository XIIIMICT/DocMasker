# TODO — DocMasker production readiness

Living list of **genuinely deferred** work. Completed phases and evidence live
in `docs/PRODUCTION_READINESS.md`; durable decisions in `docs/adr/`. Source of
the original findings: the implementation brief in `docs/` (untracked user
file, never commit). Legend: `todo` · `deferred` (maintainer decision) ·
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

## Deferred by maintainer decision

- **§8.2 — TypeScript types generated from live OpenAPI.** `openapi-typescript`
  (or equivalent), export from the running FastAPI app in CI, commit
  `front/src/shared/api/generated/schema.ts`, freshness gate, replace
  handwritten DTOs. Decision (2026-11-24): «поместим в TODO».
- **Old stage 6 — observability/deployment hardening.** Already done earlier:
  `X-Request-ID`, minimal `/ready` + `/metrics`, liveness/readiness split.
  Remaining: structured JSON logs with `request_id`, fuller metrics
  (latency/errors/input-output sizes/duration/concurrency), compose/CI
  healthchecks wired to `/ready`, front readiness route, startup/shutdown
  hooks check on the live stack, container hardening (`read_only`, `tmpfs`,
  `cap_drop`, `no-new-privileges`, `pids_limit`, limits, internal-only network),
  reverse-proxy/TLS example, rate limits, log rotation.
- **Frontend DOCX preview/highlighting performance** — explicitly deferred in
  brief §0; keep current preview behavior working.
- **OCR of raster image pixels in DOCX** — out of scope, documented as
  `raster_image_ocr: not_performed` in `manifest.json`.
- **Accuracy corpus** (versioned precision/recall/F1 per label; `score=1.0` is
  a tiebreaker constant, not confidence) and broad NER benchmark platform —
  deferred in brief §0.

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
