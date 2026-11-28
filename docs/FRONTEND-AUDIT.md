# Frontend audit & remediation — DocMasker (2026-11-27)

## 1. Executive summary

Audited commit `e7ae19558ea54f612fc75ba7e7abac79c4b5d2ef`; final local `dev` =
`78487f6`. Worktree was clean at start and end. Environment: Bun 1.4.4, Node
v26.13.1, Next 16.4.6, React/ReactDOM 19.3.1, HeroUI 3.2.8, Tailwind 4.3.5,
TypeScript 7.0.2, docx-preview 0.4.1, JSZip 3.10.2, Playwright 1.65.1. All task
branches were merged with `--no-ff`; nothing was pushed.

Six verified defects were fixed (manifest cast, two highlight defects, two strict-TS
semantic gaps, contradictory theme transition, layout-animating support link), plus
bounded placeholder counting and abort-aware archive unpacking. No runtime dependency
was added and no speculative framework was introduced. All checks and 40/40 E2E smoke
tests passed on the dev stand at audit time (2026-11-27); the current inventory is
re-measured in §10.

## 2. Baseline and after measurements

Baseline evidence: `docs/FRONTEND-AUDIT-BASELINE.md` (`9515f4b`), produced with
`next analyze` — the Turbopack-supported analyzer in installed Next 16.4.6
(`experimental-analyze` is an alias); huge JSONL was kept in temp/gitignored output.

| Measure (MEASURED) | Baseline | After |
|---|---:|---:|
| `/` client JS | 1,189,488 B raw / 451,000 B gzip | unchanged (no bundle-growth change) |
| `/` CSS | 484,697 B raw / 45,897 B gzip | unchanged except motion declarations |
| `/` total | 3,932,188 B raw / 1,237,958 B gzip | unchanged |
| Unit tests | 248 pass | **287 pass** |
| Frontend line coverage | (not separately reported) | **89.59%** (gate 65) |
| Build | 4,979 ms, 9 routes | 574–964 ms, 9 routes |
| E2E smoke | 35 tests / 4 specs | **40 tests / 4 specs, 40/40 passed** |
| TS diagnostics with new options | — | 21 → **0** |

All figures in this table are audit-time (2026-11-27) measurements, not the
current inventory; see §10 for the re-measured smoke count.

No bundle improvement is claimed: the verified defects were correctness, retention and
animation behavior, and the fixes were deliberately bundle-neutral.

## 3. Verified findings (severity / impact)

| # | Severity | Finding & evidence | Fix |
|---|---|---|---|
| 1 | P1, correctness/privacy | `JSON.parse()` result cast to `ResultManifest`; partial fields defaulted with `?? 0`/`?? {}`, so corrupt data rendered fake stats (`mask-archive.ts`). | Parse as `unknown`; full explicit guard; stable Russian errors without PII (`59dc3d9`). |
| 2 | P1, correctness | Shorter needle listed first shadowed a longer one (`["abc","abcdef"]`, restored `["Иван","Иван Петров"]`); text split across neighbouring DOM text nodes never highlighted. Tests first: 11 pass / 4 fail (`bb43fb0`). | Earliest position wins, longest needle at that position wins regardless of key order; spans consecutive text nodes within one paragraph (`9959062`). |
| 3 | P2, type safety | `strict: true` only; unsafe index/optional semantics. 21 diagnostics under two RC-level options (`9f5b710`). | Enabled `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`, `noImplicitReturns`, `noFallthroughCasesInSwitch`; zero suppressions (`e755973`). |
| 4 | P2, jank | `html` theme transition declared while `disableTransitionOnChange` injects `transition:none` — dead/smooth contradiction. Support-link `gap`/`max-width` animated: **444 layout events / 63.92 ms** over 8 hover cycles (`02f2d58`). | Instant theme switching (dead CSS removed); only `background-color`/`opacity`/`transform` animate: **16 layout events / 3.12 ms** (`d471c66`). |
| 5 | P2, memory/semantics | Demask placeholder count reopened a ZIP on every mode switch; uncancelled archive unpack could retain large Blobs after unmount/stale job. | Per-Blob `WeakMap` single-flight + bounded scan (`a9c00fd`); abort at every unpack boundary, `entries.clear()` on failure, no server `DELETE` on unmount (`78487f6`). |
| 6 | P3, docs | Stale `5 ms` debounce comment (actual 120 ms) and missing documented overlap rule. | Comment fixed; rule documented in `docs/DOCX_SUPPORT.md` and `front/AGENTS.md`. |

Not defects (verified, left unchanged): repeated occurrences, empty needles, literal
regex metacharacters, non-BMP/combining Unicode, paragraph boundaries, reapplication
after previous marks; `--breakpoint-sm/md: 0rem` overrides (compiled HeroUI CSS shows
75 `min-width:0` rules and no HeroUI breakpoint rules); `.action-disabled` (measured
`aria-disabled=true`, tabIndex 0, pointer and Enter both intercept 0 `/api/mask`
requests, guarded by `canDemask()`); object-URL download (already revoked in `finally`).

## 4. Implemented changes and design rationale

- **Manifest validation** (`manifest-validation.ts`, ~200 lines, stdlib): validates
  `schema_version` literal 1, operation/status enums, booleans, safe non-negative
  integers, `output`/`statistics`/`coverage` nested objects and arrays, unknown-field
  tolerance for compatibility. Errors: `manifest_malformed` / `_version_unsupported` /
  `_invalid`. Generated OpenAPI DTOs stay compile-time only and untouched.
- **Highlight resolution** (`docx-viewer.tsx`): one deterministic rule, longest match at
  the earliest position, matching across consecutive text nodes but never across
  paragraphs; each slice gets its own `<mark>` and prior marks are removed first.
  Generation-private staging, debounce and stale rejection preserved.
- **Strict TS**: real semantic fixes (`undefined` guards on regex captures,
  `signal: signal ?? null`, props omitted rather than passed as `undefined`), no `as`,
  `!`, `@ts-ignore` or `any`. Generated files untouched.
- **Motion**: support-link `gap`/`max-width` snapped and only compositor-friendly
  properties animated; hero animations and reduced-motion cascade preserved. This
  choice was reverted post-audit on user feedback — see §9.
- **Placeholder/ZIP**: one decompression per Blob, 10,000-part / 64 MiB bounds derived
  from the backend's hard part limit; abort-aware result reads with no partial results.

## 5. Rejected alternatives (Phase 7 decision record)

| Candidate | Decision | Evidence / current solution |
|---|---|---|
| Zod for manifests | Reject | No existing validation dep; ~1.6 MiB client route; contract small and fully enumerable — explicit guards suffice. |
| `fflate` / `zip.js` | Reject (keep JSZip 3.10.2) | `fflate` 11.8 ms but synchronous + rewrite of every call site for ~20 ms synthetic gain; `zip.js` streaming **slower** (23.0 ms / 77.11 MiB peak vs JSZip 22.3 ms / 67.36 MiB). Reactivity/retention fixes address real waste. |
| Separate backend result streams / no client unpack | Reject | Atomic flat ZIP with one fenced, attempt-scoped artifact and global `no-store`; splitting adds partial states, DB keys and manifest ordering risk. |
| Web Worker for highlighting | Reject | Chromium first pass 8.6 ms, re-apply 5.8 ms (below 16 ms frame budget); warm 22.31→21.40 ms is within noise. Transfer cost would exceed benefit. |
| RxJS / Zustand / Redux Toolkit / TanStack Query / Framer Motion / React Hook Form / virtualization / microfrontends | Reject | No measured problem the current React + HeroUI + browser model cannot solve. `docs/DOCX_SUPPORT.md` and this report keep the reasoning; no new runtime dependency. |
| `noPropertyAccessFromIndexSignature` | Reject | 146 additional diagnostics (79 in manifest tests, 39 in guards) — unreviewable on deliberate record access. |
| Timing-token consolidation | Defer | No visual regression harness; prior note deliberately deferred. |

Dev-only tooling: `swagger-typescript-api@13.15.0` (already adopted earlier for
compile-time HTTP DTOs, `--no-client`, TS 7 compatible, CI freshness) is unchanged.

## 6. Validation (exact commands and outcomes)

All commands and outcomes in this section are from the 2026-11-27 audit run.

- `bun run --cwd front format:check` — all 98 files formatted, exit 0.
- `bun run --cwd front lint:check` — no findings, exit 0.
- `bun run --cwd front typecheck` — no type errors in 89 files, exit 0.
- `bun run --cwd front test` — **287 pass / 0 fail**, 913 `expect()` calls, 31 files
  (pre-existing HeroUI `act(...)` warnings only).
- `bun test --coverage --coverage-reporter=lcov` + `bun scripts/check-coverage.mjs 65`
  — **89.59%** line coverage (2237/2497), gate passed.
- `bun run --cwd front build` — 9 routes, no warnings.
- `bun run --cwd front gen:openapi --check` — fresh; generated files untouched.
- `prek run --all-files` — all hooks passed at every merge.
- Playwright via `local.playwright.config.ts`, `E2E_BASE_URL=http://127.0.0.1:5001`:
  targeted preview/highlight/job/manifest specs (1/1, 4/4, 3/3), reduced-motion/theme/
  support-link/disabled (6/6), and full smoke **40/40** in 41–48 s. Discovery guard:
  40 tests in 4 specs, required specs present.
- Backend checks (no backend behavior changed except none): `ruff check`,
  `ruff format --check`, `ty check`, full pytest **543 passed / 4 skipped, 87.19%**.

## 7. Phase 6/8 notes

Deduplication: the manifest cast/zero-fallback duplication was removed in favour of one
validated model; API DTOs remain generated and distinct from runtime validation. No
two-line wrappers, generic hooks or all-purpose `utils.ts` were created; no shared
cross-repository package. Tests were added for pure algorithms (highlight, manifest,
placeholder bounds), component behavior (safe error UI, action boundaries), lifecycle
(cancellation/unmount/stale jobs), plus E2E for keyboard, reduced motion, theme and
DOCX flows. Performance artifacts (analyzer graph, CDP traces, synthetic ZIPs) are in
OS temp/gitignored output, not committed.

## 8. Remaining work / limitations

- No committed large DOCX fixture: highlight and ZIP numbers use deterministic SYNTHETIC
  DOM/archives; target-hardware browser profiling is OPEN.
- Production-image CI stack smoke (:15001) was down; dev-stand smoke is green.
- Visual regression for timing tokens and an axe scan were not added; automated scans
  would not prove accessibility.
- Backend has no pre-operation placeholder aggregate; a new endpoint would be a product
  surface change and was not introduced.
- RevoGrid/collab, 300/500-client presence, Keycloak OIDC browser CI and live migration
  gates belong to ReportCollector and are tracked there.

## 9. Post-audit maintainer decisions (final state)

- The support-pill snap introduced by the audit was REJECTED by the maintainer as
  "слишком быстро". Original timing was restored (`gap 180 ms`, label `max-width 220 ms`,
  `background/opacity 140 ms`, same easing; no `translateX`), with reduced motion still
  instant. Measured computed values and the accepted layout cost are in
  `docs/ARCHITECTURE.md` and `front/AGENTS.md` (`fe178b8`).
- The active-job cancel control was renamed to **«Отменить операцию»**; DELETE semantics
  and its E2E regression are unchanged (`55683f7`).

Cross-project comparison is in `ReportCollector/docs/FRONTEND-AUDIT.md` and the final
response; both apps keep intentionally different DOM/workflow architectures.

## 9. Post-audit decision (2026-11-28) — restore original support-pill timing

- **User feedback (authoritative):** after the audit change the «Связаться с
  разработчиком» pill expands "слишком быстро"; the maintainer wants the ORIGINAL
  expansion timing back. This is a product decision, not a regression in the audit
  measurements.
- **Decision:** revert only the support-pill motion. All other audit fixes stay
  (manifest guard, highlight ordering, strict TS, ZIP cancellation, placeholder
  bounds, instant theme switching, reduced-motion zeroing). `docs/FRONTEND-AUDIT-BASELINE.md`
  remains untouched as historical evidence of the audit itself.
- **Restored exactly:** `.support-link` → `gap var(--app-motion-gap) cubic-bezier(0.22, 1, 0.36, 1), background-color var(--app-motion-fast) ease`; `.support-link__label` → `max-width var(--app-motion-expand) cubic-bezier(0.22, 1, 0.36, 1), opacity var(--app-motion-fast) ease`; tokens `--app-motion-fast: 140ms`, `--app-motion-gap: 180ms`, `--app-motion-expand: 220ms`. The added `transform: translateX(-0.25rem)` reveal and its hover/compact reset were removed. `prefers-reduced-motion: reduce` still zeroes all three tokens.
- **Accepted tradeoff:** animating `gap`/`max-width` forces layout (the audit measured
  444 Layout events / 63.9 ms over eight hover cycles). The maintainer accepts that
  measured cost in exchange for the original feel; no composited shorthand, snap or
  "performance-optimal" substitute was kept.
- **Chromium verification (dev stand, `http://127.0.0.1:5001`):** computed link
  `transitionProperty/Duration/TimingFunction` = `gap, background-color` /
  `0.18s, 0.14s` / `cubic-bezier(0.22, 1, 0.36, 1), ease`; label =
  `max-width, opacity` / `0.22s, 0.14s` / same easing. Live samples at ~70 ms:
  gap 6.83 px, max-width 289 px, opacity 0.88; end state gap 7.2 px / max-width
  320 px. `getAnimations()` reports `column-gap` 180 ms and `max-width` 220 ms.
  Under reduced motion the tokens and both `transitionDuration` values are `0s`
  and the end state is present on the first sample.
- **Regression coverage:** `globals-conventions.test.ts` pins the restored
  declarations/values and the absence of `translateX`; `smoke.spec.ts` asserts the
  computed durations/easing and a frozen-at-80 ms intermediate width in real
  Chromium, plus instant reduced-motion, hover/keyboard and compact-Tooltip end
  states. No assertion was weakened.
- **Validation:** `bun run --cwd front format:check` (98 files), `lint:check`,
  `typecheck` (89 files), `test` (**287 pass / 0 fail**, 926 `expect()` calls,
  31 files; pre-existing HeroUI `act(...)` warnings only) and `build` (9 routes)
  green; `prek run --all-files` green. Playwright via `local.playwright.config.ts`
  on the dev stand: targeted support-link/reduced-motion/keyboard/Tooltip **4/4**,
  full `smoke` **41/41** (47.8 s), discovery guard 41 tests in 4 specs — a pre-C1
  measurement; the current inventory is 55 tests in 4 specs (§10). No push,
  no merge.

## 10. Smoke discovery reconciliation and current inventory (2026-11-28)

Re-measured on `docs/smoke-count-reconciliation` at `82de28f` (merged `761e6e2`)
with Bun 1.4.4 / Node 26.13.1 — the **pre-C1** inventory. From `front/`, with the
listing written to a temp path outside the repository:

```sh
bunx --no-install playwright test --project=smoke --list > <temp>/smoke-tests.txt
bun e2e/check-smoke-discovery.mjs <temp>/smoke-tests.txt
```

- Discovery: exit 0, `Total: 41 tests in 4 files` — `smoke.spec.ts` 29,
  `hover-smoke.spec.ts` 10, `rerender-smoke.spec.ts` 1, `health.spec.ts` 1.
- Guard positive: exit 0, `Smoke discovery guard: 41 tests across 4 specs;
  required specs present.` This agrees with `testMatch`
  `/(?:health|smoke|mask)\.spec\.ts/`, the four required basenames and the
  floors `MIN_MAIN_SPEC_TESTS = 20` / `MIN_TOTAL_TESTS = 34`; no threshold was
  changed.
- Guard negative (on a **copy** of the listing; no committed spec touched):
  renaming the `smoke.spec.ts` entries gave exit 1 with `missing smoke spec:
  smoke.spec.ts` and `smoke.spec.ts lists 0 tests, expected at least 20`;
  removing those lines instead added `smoke project lists 12 tests, expected at
  least 34`. The copies were deleted; the original listing stayed at 41.
- The earlier 23/35/40 counts in §1–§9 are dated historical audit evidence.
  Rely on `--list` for the current inventory, as recorded in `docs/TESTING.md` §1.
- Re-checked after the doc/comment edits: `prek run --all-files` exit 0, front
  `format:check` (99 files) / `lint:check` / `typecheck` (90 files) / `test`
  (313 pass / 0 fail) green, discovery positive exit 0 and negative exit 1.

### Current state — after C1/C2 (`3f4913e`, merged `13f562e`)

- `bunx --no-install playwright test --project=smoke --list` now reports **55
  tests in 4 files**: `smoke.spec.ts` 43 (29 existing + 11 axe scans + 3
  keyboard/perceivability walkthroughs), `hover-smoke.spec.ts` 10,
  `rerender-smoke.spec.ts` 1, `health.spec.ts` 1; the discovery guard exits 0.
  The 41/4 figures above are dated pre-C1 evidence; `testMatch`, the four
  required basenames and the 20/34 floors are unchanged.
- Full smoke **55/55** on `http://127.0.0.1:5001` via `local.playwright.config.ts`.
  These are regression-net counts, not an accessibility claim — screen-reader
  verification is OUT OF SCOPE by maintainer decision (2026-11-28)
  (`docs/ACCESSIBILITY.md` §4); automated axe scans and manual keyboard/focus
  review are the agreed coverage. Do not claim a screen-reader audit.

## 11. Follow-up remediation — final status (2026-11-28)

Final state of the five items tracked in root `TODO.md`; each was merged into
`dev` with `--no-ff` and nothing was pushed.

| Item | Status | Final evidence |
|---|---|---|
| A1 bounded placeholder scan | DONE | `4158221` (merged `4ed0b39`). Narrow native `DecompressionStream("deflate-raw")` walker in `shared/api/bounded-zip-scan.ts`; no runtime dep (native 0 B vs `fflate` 33 KB / `zip.js` 210 KB). Incremental inflate counts decoded bytes against the 64 MiB ceiling; ZIP metadata preflight is defence in depth, **not** a hard bound. Understated-metadata bomb test stopped at 991,506 decoded bytes vs 61 MB materialised before. 304 unit pass, E2E 41/41 then; no speed/memory gain claimed for ordinary documents. |
| A2 strict result manifest | DONE | `ca61d5e` (merged `d7d2b6a`). `complete === true` plus empty coverage arrays for both operations, unknown extras tolerated; backend parity test only, no producer semantics change. 313 unit pass, E2E 41/41. |
| A3 job/sessionStorage runtime validation | **DEFERRED by maintainer decision** | Not implemented. Independent boundary-hardening work with its own protocol/UX decisions; revisit after A1/A2/C1 on its own branch with tests for malformed fields, tampered `sessionStorage`, invalid URLs and no unexpected fetch. No runtime validation of job payloads was added. |
| A4 discovery reconciliation | DONE | `82de28f` (merged `761e6e2`). Fresh 41/4 measurement (29/10/1/1), guard positive exit 0 / negative exit 1; older 23/35/40 figures are dated. Superseded for current state by C1's 55/4 (above). |
| C1/C2 accessibility | **DONE** | `3f4913e` (merged `13f562e`). Dev-only `@axe-core/playwright@4.14.1`, 11 axe scans on WCAG 2/2.1 A/AA, source fixes (`aria-command-name`, `nested-interactive`, `aria-valid-attr-value`, `color-contrast`) plus keyboard/focus walkthroughs. Screen-reader verification is OUT OF SCOPE by maintainer decision (2026-11-28); automated axe scans and manual keyboard/focus review are the agreed coverage. Do not claim a screen-reader audit. A green scan is not an accessibility audit. Current 55/4, smoke 55/55. |

§8's "an axe scan was not added" is the 2026-11-27 audit-time record; C1/C2
supersedes it. The cross-project (ReportCollector) comparison and the explicit
deferral/blocked list are in root `TODO.md` §"Follow-up remediation
2026-11-28 — FINAL".
