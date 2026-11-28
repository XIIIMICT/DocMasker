# Frontend audit — current state

This is the current contract and decision record for the frontend audit work.
Implementation is in the code; per-change evidence, SHAs and dated counts live
in git history. The canonical inventory commands and current test counts are in
`docs/TESTING.md` §1; tooling and hot-path rules are in `front/AGENTS.md`.

## Current frontend contracts

- **Manifest validation.** `manifest-validation.ts` parses external bytes as
  `unknown` and validates the whole `ResultManifest` (schema version literal 1,
  operation/status enums, booleans, nested objects and arrays, non-negative
  safe-integer counts, unknown extras tolerated). `complete` must be exactly
  `true` and both `coverage.unprocessed_text_parts` and
  `coverage.rejected_parts` must be empty; failures raise
  `ManifestValidationError` with a stable code and a fixed Russian detail that
  echoes no input, value or filename. Generated OpenAPI DTOs stay compile-time
  only. Full contract: `docs/API.md` §`manifest.json`; runtime guard:
  `front/src/shared/api/manifest-validation.ts`.
- **Highlight resolution.** `docx-viewer.tsx` uses one deterministic rule:
  scanning starts at the earliest position and the longest needle at that
  position wins regardless of keys order; matching spans consecutive text nodes
  inside one logical container but never crosses a paragraph. Each covered node
  slice gets its own `<mark class="docx-marker">` and prior marks are removed
  first. Preview generation isolation, the 120 ms debounce and stale rejection
  are unchanged. Detail: `docs/DOCX_SUPPORT.md` §"Frontend preview isolation".
- **Strict TypeScript.** `tsconfig.json` keeps `strict` and adds
  `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`, `noImplicitReturns`
  and `noFallthroughCasesInSwitch` with real semantic fixes and no suppressions.
  `noPropertyAccessFromIndexSignature` stays off: it produced 146 additional
  diagnostics on deliberate record access. Rules: `front/AGENTS.md` §"Strict
  TypeScript options".
- **Motion.** All other audit fixes remain, but the support-pill snap was
  rejected by the maintainer: the original `gap`/`max-width` timing is
  restored, the layout cost is explicitly accepted, and
  `prefers-reduced-motion: reduce` still zeroes the tokens. Current values,
  computed Chromium checks and regression coverage: `docs/ARCHITECTURE.md` and
  `front/AGENTS.md` invariant 9. No composited substitute is to be reintroduced.
- **Bounded placeholder scan and result unpacking.** One native
  `DecompressionStream("deflate-raw")` pass per Blob counts actual decoded bytes
  against the 64 MiB ceiling; ZIP metadata preflight is defence in depth, not
  the bound. JSZip 3.10.2 remains only for result archives; unpacking checks the
  abort signal at every await boundary and clears partial Blobs. Exact limits,
  non-guarantees and measured evidence: `docs/DOCX_SUPPORT.md` §"Demasking input
  placeholder count" and §"Result-archive unpacking", `docs/PERFORMANCE.md`.

## Decisions kept (alternatives rejected)

| Candidate | Decision | Reason / current solution |
|---|---|---|
| Zod for manifests | Reject | No existing validation dependency; contract is small and fully enumerable — explicit guards suffice. |
| `fflate` / `zip.js` | Reject (keep JSZip 3.10.2) | Synchronous main-thread work or slower streaming plus a rewrite of every call site; no measured problem it solves. |
| Separate backend result streams / no client unpack | Reject | One atomic flat ZIP with a fenced, attempt-scoped artifact and global `no-store`; splitting adds partial states, DB keys and manifest ordering risk. |
| Web Worker for highlighting | Reject | First pass well under a frame budget; transfer cost would exceed the benefit. |
| RxJS / Zustand / Redux Toolkit / TanStack Query / Framer Motion / React Hook Form / virtualization / microfrontends | Reject | No measured problem the current React + HeroUI + browser model cannot solve; no new runtime dependency. |
| `noPropertyAccessFromIndexSignature` | Reject | 146 additional diagnostics on deliberate record access — unreviewable. |
| Timing-token consolidation | Defer | No visual regression harness; the maintainer accepted the original support-pill timing. |

`swagger-typescript-api@13.15.0` stays a pinned dev-only tool for compile-time
HTTP DTOs (`--no-client`, CI freshness check).

## Validation and current inventory

Canonical commands (from `front/`, with the listing written outside the repo):

```sh
bunx --no-install playwright test --project=smoke --list > <temp>/smoke-tests.txt
bun e2e/check-smoke-discovery.mjs <temp>/smoke-tests.txt
```

The guard requires the exact four spec basenames (`health`, `smoke`,
`rerender-smoke`, `hover-smoke`), ≥20 tests in `smoke.spec.ts` and ≥34 overall;
no threshold was changed. Current inventory and quality-gate commands:
`docs/TESTING.md` §1 and §3.

## Remaining limitations and deferrals

- No committed large DOCX fixture: highlight and ZIP numbers use deterministic
  SYNTHETIC DOM/archives; target-hardware browser profiling stays OPEN
  (`docs/PRODUCTION_READINESS.md`).
- **A3 — runtime validation of job/sessionStorage data — DEFERRED by maintainer
  decision.** Job/stored-payload fields and status/result URL allowlisting are
  **not** runtime-validated and no such claim is made. Revisit on its own task
  branch with tests for malformed fields, tampered `sessionStorage`, invalid
  URLs and no unexpected fetch. Current statement: `TODO.md` §"Deferred by
  maintainer decision".
- Screen-reader verification is **OUT OF SCOPE** by maintainer decision; the
  agreed coverage is automated axe scans plus manual keyboard/focus review. A
  green axe scan is not an accessibility claim — `docs/ACCESSIBILITY.md`.
- Backend has no pre-operation placeholder aggregate; adding one would be a new
  API surface and was not done.
- RevoGrid/collab, 300/500-client presence and Keycloak OIDC browser CI belong
  to ReportCollector and are tracked there.

Cross-project comparison (ReportCollector vs DocMasker) has one current home:
`TODO.md` §"Follow-up remediation — current state".
