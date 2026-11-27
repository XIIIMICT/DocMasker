# Frontend audit — Phase 0 reproducible baseline

Status: research only. No source/config changed; no commit, push or remote change.
Tags: **MEASURED** (executed this session), **STATIC** (read from source/docs), **BLOCKED** (not executed / external gate).

## 1. Environment — MEASURED

| Item | Value |
|---|---|
| Repo / worktree | `D:\Work\DocMasker`, single worktree `D:/Work/DocMasker` |
| Branch / SHA | `dev` / `e7ae19558ea54f612fc75ba7e7abac79c4b5d2ef` |
| Dirty state | clean at start (`git status --porcelain` empty) and clean at end |
| Toolchain | `bun 1.4.4`, `node v26.13.1`, `rg 16.0.0` |
| Install command | CI: `bun install --frozen-lockfile` from `front/`; root guide: `bun install`. `node_modules` already present; Phase 0 did **not** reinstall. |
| Analyzer metadata | `nextVersion 16.4.6`, `gitBranch dev`, `gitSha e7ae195…`, `gitDirty false`, snapshot `dm-baseline` |

**Exact installed versions** (verified in `front/node_modules/<pkg>/package.json` and `front/bun.lock`; CLI binaries re-checked):

| Package | Version | Package | Version |
|---|---|---|---|
| `next` | 16.4.6 | `react` / `react-dom` | 19.3.1 / 19.3.1 |
| `@heroui/react` / `@heroui/styles` | 3.2.8 / 3.2.8 | `tailwindcss` / `@tailwindcss/postcss` | 4.3.5 / 4.3.5 |
| `typescript` | 7.0.2 | `docx-preview` | 0.4.1 |
| `jszip` | 3.10.2 | `undici` | 8.13.1 |
| `iconoir-react` | 7.12.2 | `next-themes` | 0.4.6 |
| `oxlint` / `oxfmt` | 1.94.0 / 0.79.0 | `@playwright/test` | 1.65.1 |
| `happy-dom` | 21.0.4 | `@testing-library/react` / `/dom` | 16.3.3 / 10.4.2 |
| `swagger-typescript-api` | 13.15.0 | `babel-plugin-react-compiler` | 1.0.0 |
| `@types/node` | 26.6.6 | `@types/bun` | 1.4.5 |
| `@types/react` / `/react-dom` | 19.3.0 / 19.3.0 | `tsc` / `oxlint` / `oxfmt` CLI | 7.0.2 / 1.94.0 / 0.79.0 |

Note: `bun.lock` also pins a nested `swagger-typescript-api/typescript@6.0.3` used only by the generator; the app compiler is 7.0.2.

**Scripts** (STATIC, `front/package.json`): `dev`=`next dev`; `build`=`next build`; `start`=`next start`; `lint`=`oxlint --fix`; `lint:check`=`oxlint`; `format`=`oxfmt`; `format:check`=`oxfmt --check`; `test`=`bun test`; `test:e2e`=`playwright test`; `typecheck`=`bun check`; `gen:labels`=`bun scripts/gen-labels.mjs`; `gen:openapi`=`bun scripts/gen-openapi.mjs`. Coverage gate (no script): `bun test --coverage --coverage-reporter=lcov` → `bun scripts/check-coverage.mjs 65`.

**Config surface** (STATIC): `next.config.ts` = `output:"standalone"`, `reactCompiler:true`, `allowedDevOrigins` (127.0.0.1/localhost), four security headers. `postcss.config.ts` = `@tailwindcss/postcss` only. `oxfmt.config.ts` = tabWidth 4, sortImports/sortTailwindcss/sortPackageJson. `bunfig.toml` = test preload `src/test-setup.ts`, root `./src`. `playwright.config.ts` = `testDir ./e2e`, `testIgnore **/probe/**`/`**/*-probe*`, `testMatch /(?:health|smoke|mask)\.spec\.ts/`, `workers 1`, Chromium, `E2E_BASE_URL ?? http://127.0.0.1:5001`, retries 2 in CI, html+list reporters. `local.playwright.config.ts` derives discovery from the committed config, overrides launcher (`channel:"chrome"` resolved from PATH) + list reporter. CI `front` job: bun 1.4.4 / node 26, `bun install --frozen-lockfile`, `next typegen`, `gen:labels` freshness, typecheck, lint:check, format:check, coverage+65% gate; `openapi-contract` job runs `gen:openapi --check`; `production` job runs smoke discovery guard then smoke on the isolated CI stack.

## 2. Canonical checks — MEASURED

All commands run from `front/`. PowerShell printed harmless `NativeCommandError` lines when `2>&1` piping bun's stderr; the recorded `$LASTEXITCODE` values are authoritative.

| Command | Exit | Result |
|---|---|---|
| `bun run format:check` | 0 | `All matched files use the correct format. Finished in 898ms on 95 files using 16 threads.` |
| `bun run lint:check` | 0 | no findings |
| `bun run typecheck` | 0 | `✓ No type errors in 86 files [1.59s]` |
| `bun run test` | 0 | **248 pass / 0 fail**, 751 `expect()` calls, **248 tests across 29 files**, 8.16 s |
| `bun run build` | 0 | wall **4979 ms**; `next.config.ts` 111 ms; **compiled in 1577 ms**, TypeScript 509 ms, static pages 3/3 in 459 ms (11 workers) |
| `bun run gen:openapi --check` | 0 | `Generated OpenAPI declarations are fresh.` |

**Warnings:** `bun test` emits pre-existing React `not wrapped in act(...)` warnings (HeroUI `Button`, `MenuInner`, `SharedElement`) from `shared/ui/__tests__/brand-accessibility.test.tsx` and `pane-accessibility.test.tsx`; they do not fail the run. Build emitted no warnings.

**Build routes (9):** `ƒ /`, `○ /_not-found`, `ƒ /api/health`, `ƒ /api/jobs/[...path]`, `ƒ /api/labels`, `ƒ /api/mask`, `ƒ /api/mask-file`, `ƒ /api/unmask`, `ƒ /api/unmask-file`. (1 static, 8 dynamic.)

**E2E — BLOCKED (not claimed as passed).** Phase 0 scope excluded browser profiling; no smoke run was performed. Readiness evidence only: `bunx --no-install playwright test --project=smoke --list` → exit 0, **Total: 35 tests in 4 files**; `bun e2e/check-smoke-discovery.mjs` on that listing → exit 0 (`35 tests across 4 specs; required specs present`). Playwright browsers present (`chromium-1134`, `chromium-1255`, headless shell, ffmpeg). Dev stack up: `dm-front-dev`, `dm-back-dev`, `dm-worker-dev`, `dm-postgres`, `dm-valkey`; `http://127.0.0.1:5001/api/health` → HTTP 200 `{"status":"ok"}`. The isolated prod-image CI stack (15001/18001) is not running, so production smoke remains a separate gate.

## 3. Performance baseline — analyzer — MEASURED

**CLI verdict:** installed Next 16.4.6 exposes **`next analyze`** (canonical) with **`experimental-analyze`** as an alias — `Usage: next analyze|experimental-analyze [options] [command] [directory]`; description: *"Analyze production bundle output with an interactive web ui. Does not produce an application build. Only compatible with Turbopack."* Use `next analyze`; the older `next experimental-analyze` name still works in this version. No `@next/bundle-analyzer` dependency exists.

Commands:
- `bunx --no-install next analyze --output --snapshot dm-baseline` → exit 0; internal `Analyze completed in 3.4s` (wall 5462 ms); wrote `front/.next/diagnostics/analyze/`.
- `bunx --no-install next analyze export --snapshot dm-baseline` → exit 0; **7596 JSONL records** (2111 `module`, 4229 `part`, plus `meta`/`output`), 2.47 MB; `schema_version 1`, `routeCount 10`.

**Route bundle table** (from `route-summaries.json`; raw/gzip bytes; `client` includes CSS):

| Route | Total raw | Total gzip | Client raw | Client gzip |
|---|---:|---:|---:|---:|
| `/` | 3,932,188 B (3750.2 KiB) | 1,237,958 B (1209.1 KiB) | **1,674,185 B (1635.1 KiB)** | **496,897 B (485.3 KiB)** |
| `/_not-found` | 3,080,448 B | 913,037 B | 1,209,412 B (1181.1 KiB) | 323,289 B (315.7 KiB) |
| `/_global-error` | 2,283,592 B | 734,126 B | 568,182 B (554.9 KiB) | 210,768 B (205.8 KiB) |
| `/api/health` | 1,749,919 B | 523,839 B | 0 | 0 |
| `/api/jobs/[...path]` | 1,679,191 B | 493,431 B | 0 | 0 |
| `/api/labels` | 1,678,563 B | 493,140 B | 0 | 0 |
| `/api/mask` | 1,679,718 B | 493,574 B | 0 | 0 |
| `/api/mask-file` | 1,679,075 B | 493,432 B | 0 | 0 |
| `/api/unmask` | 1,679,658 B | 493,572 B | 0 | 0 |
| `/api/unmask-file` | 1,679,099 B | 493,444 B | 0 | 0 |

`/` client split: **CSS 484,697 B (473.3 KiB) raw / 45,897 B (44.8 KiB) gzip** + **JS 1,189,488 B (1161.6 KiB) raw / 451,000 B (440.4 KiB) gzip**. The CSS is one chunk (`globals.css`, analyzer id `09fc0lsfw9b9x.css`, on disk `2_nwxkuoi0-au.css`) — it dominates raw bytes but gzips very well.

**Largest client chunks on `/`** (analyzer virtual id → on-disk chunk of matching size; raw/gzip):

| Analyzer chunk | Raw / gzip | Contents |
|---|---:|---|
| `09fc0lsfw9b9x.css` | 473.3 / 44.8 KiB | 1 module: `src/app/globals.css` (Tailwind + `@heroui/styles`) |
| `3uz6y8429zu_n.js` | 223.8 / 74.4 KiB | 23 modules (react-dom/Next runtime) |
| `0aosvdu38vaz5.js` | 191.1 / 84.2 KiB | 1 module: `src/app/masker-page.tsx` |
| `233p8e-xpji03.js` | 189.8 / 65.2 KiB | 58 modules: **jszip + pako (async/lazy)** |
| `1gghwpfpite64.js` | 178.2 / 74.0 KiB | 105 modules: Next App Router client runtime / segment cache |
| `2az05sy6mnr6p.js` | 117.6 / 49.8 KiB | 27 modules: HeroUI button/tabs/textarea/tooltip, `next-themes`, react-aria Button |
| `0cz1d0mv5g_q7.js` | 110.0 / 38.5 KiB | 1 module: `polyfill-nomodule.js` |
| `1b9-33pz_n62t.js` | 72.6 / 19.9 KiB | 1 module: **docx-preview.mjs (async/lazy)** |
| `1b9…`/`3zugwmf…`/`30uap…`/`0uzz…` | 35.3 / 15.3, 19.5 / 8.0, 13.6 / 6.5, 9.8 / 4.4 KiB | providers chunk, framework runtime, turbopack runtime |

Largest client source modules on `/`: `globals.css` 473.3/44.8 KiB, `react-dom-client.production.js` 198.6/62.4 KiB, `polyfill-nomodule.js` 110.0/38.5 KiB, `docx-preview.mjs` 72.6/19.9 KiB, `stream-browserify` 40.0/11.6 KiB, `util` 31.2/9.2 KiB, `tailwind-variants/chunk-RIONEO55` 25.1/10.3 KiB, `react-server-dom-turbopack-client` 23.5/7.6 KiB, `buffer` 22.0/6.9 KiB, `pako deflate` 13.7/3.7 KiB.

**Import chains (reverse dependency scan, depth ≤4):** `src/app/masker-page.tsx` → `input-panes.tsx` / `result-panes.tsx` / `theme-switcher.tsx` / `support-button.tsx` → `@heroui/react` (button-group, tabs, textarea, tooltip, link) → `react-aria-components` → `react-aria` internals (Menu, Tabs, Tooltip, selection/focus hooks). `brand.tsx` + `hover-dropdown.tsx` → `@heroui/react/dist/components/dropdown` → react-aria Menu (largest deep chain, includes all `intl/*` locale modules). `app/providers.tsx` → `@heroui/react/dist/components/toast` → `tailwind-variants`. `docx-viewer.tsx` → dynamic `import("docx-preview")`; `lazy-zip.ts` → dynamic `import("jszip")`: the analyzer records async-loader edges on `[client-fs]/_next/static/chunks/03r27evdcojp5.js` (and the SSR counterpart) targeting `docx-preview.mjs` and `jszip/lib/index.js`, confirming both are **lazy chunks**, absent from a text-only initial load.

Huge outputs kept outside the repo: `C:\Users\Scorpi-ON\AppData\Local\Temp\opencode\dm-frontend-audit\` (`analyze-export.jsonl`, `analyze-report*.txt`, `analyze-chunks.txt`, `analyze-final.txt`, all check logs) and `front/.next/diagnostics/analyze/` (gitignored build output).

## 4. Repeatable runtime scenarios — inventory / readiness

**Deterministic DOCX fixtures (STATIC):** only two committed browser fixtures — `front/e2e/fixtures/sample.docx` **35,351 B** and `sample-masked.docx` **36,386 B** (one paragraph: `Контакты: [PERSON_1], тел [PHONE_1].`). No large DOCX fixture exists in `front/e2e`. Large/synthetic DOCX generation is backend-side: `back/benchmarks/synthetic.py::build_docx(DocxShape)` (seed 42), `back/benchmarks/bounded_memory.py::_build_docx(paragraphs, media_bytes, many_parts)` (shapes incl. 22 MiB media / 6000 paragraphs), `back/tests/drill_support.py::build_docx(paragraphs, marker)`, `back/tests/test_uploads.py::_docx_bytes`, and hand-built OPC fixtures in `test_ooxml.py`/`test_ooxml_bounded.py` (marker beyond byte 65,536, headers/footers, tracked deletions, text boxes, embedded objects, malformed XML). **BLOCKED for front e2e as-is:** those generators are Python and produce no committed browser fixture; a large-file browser scenario would need a generated file written to a temp path first.

**Helpers (STATIC):** `e2e/helpers/selectors.ts::DATA_FILE_INPUT` = `input[type="file"][accept*=".docx"]`; `e2e/helpers/timing.ts` = `GRACE_FRAMES=2` + `waitForGraceFrames`, `CLOSE_GRACE_MS=250`, `GRACE_MARGIN_MS=50`, `CLOSE_GRACE_OBSERVATION_MS=300`, `waitForCloseGrace`. No sleep-as-evidence except the named hover grace.

**Scenario coverage (STATIC, 35 tests / 4 specs):**
- Text-only masking/unmasking round-trip, stats tooltip, no-PII notice, per-mode drafts, invalid keys guard — `smoke.spec.ts`.
- DOCX preview + exactly-once placeholder marks (`mark.docx-marker`), demask-file `unused_keys`, mask↔demask manifest stats parity — `smoke.spec.ts`.
- Highlight re-application without re-parsing (node identity + named debounce pass) — `rerender-smoke.spec.ts`.
- Hover dropdown mode switch, per-mode `document.title`, `localStorage` restore, reduced-motion end states — `smoke.spec.ts` / `hover-smoke.spec.ts`.
- Job lifecycle/error states: text job 202 → poll → result ZIP (`result.json`), cancel control + DELETE, manifest required/malformed → Russian error, manifest-driven stats — `smoke.spec.ts` (routes are intercepted with `page.route`, deterministic, no backend job needed).
- Download/unpack contracts (unit): `mask-archive.test.ts`, `jobs.test.ts`, `placeholder-count.test.ts`, `lazy-zip.test.ts`; viewer lifecycle/scheduling: `docx-viewer*.test.ts*`; boundaries/a11y: `action-boundaries.test.tsx`, `pane-*.test.ts*`, `brand-accessibility.test.tsx`.

**Local vs blocked:** unit suite and all 29 test files run locally (MEASURED above). Smoke is **READY but not executed** (dev stack healthy at :5001, Chromium installed, discovery guard green). Production-image smoke on the isolated CI stack is **BLOCKED** (stack down). Backend integration/job-drill scenarios need `TEST_DATABASE_URL`/`TEST_VALKEY_URL` (stand containers exist) and were not run in this phase. Target-hardware repetitions, real POSIX SIGTERM, real full filesystem and browser memory/FPS profiling remain **BLOCKED/OPEN** per `TODO.md` and phase scope.

## 5. Static inventory (no fixes — informs later phases)

**Motion tokens** (`front/src/app/globals.css:66-69`): `--app-motion-fast: 140ms`, `--app-motion-gap: 180ms`, `--app-motion-theme: 200ms`, `--app-motion-expand: 220ms`. Reduced-motion block (`:608-614`) sets all four to `0ms`; HeroUI's own reduced-motion rules live in `@heroui/styles`. **No app-owned `@keyframes` or `animation` declarations** were found under `front/src/**/*.css`; all three app-owned transition sites are `transition:` only:
1. `html` — `background-color`, `color` (`--app-motion-theme`, ease) at `:108-113` (theme).
2. `.support-link` — `gap` (`--app-motion-gap`, `cubic-bezier(.22,1,.36,1)`) + `background-color` (`--app-motion-fast`, ease) at `:156-171`; hover/focus expands `gap` to `.45rem`.
3. `.support-link__label` — `max-width` (`--app-motion-expand`, same easing) + `opacity` (`--app-motion-fast`) at `:179-189`; hover/focus sets `max-width: 20rem`, `opacity: 1`; `.support-link--compact` suppresses expansion (Tooltip carries the label) and `:has(.support-link--compact)` keeps the trigger `width: fit-content`. Reduced-motion e2e asserts `transitionDuration` splits to `0s, 0s` and `animationName === "none"`.

**Theme-switch interaction:** `app/providers.tsx` mounts `NextThemesProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange`. Because `disableTransitionOnChange` temporarily disables CSS transitions during a theme change, the `html` theme transition above has no visible effect on switch — a latent inconsistency to evaluate in a later phase (no change made).

**Breakpoint overrides:** `@theme { --breakpoint-sm: 0rem; --breakpoint-md: 0rem; }` (`globals.css:16-19`) intentionally keeps HeroUI 3.2.8 sm/md tiers active at every viewport (51 responsive rules emitted as `min-width:0` instead of 40/48rem); app-authored layout uses literal `@media (width < 64rem)` and `< 40rem` and there are no app `sm:`/`md:` utilities.

**`.action-disabled`** (`globals.css:388-399`): `color`/`background`/`cursor` with `!important` plus `pointer-events:none`; deliberately visual-only — HeroUI v3 `isDisabled` on these ButtonGroup children broke Tooltip hover and risked SSR/client mismatch; the real guard is `canDemask()` at the operation boundary (pinned by `action-boundaries.test.tsx` / `pane-boundaries.test.ts`).

**`tsconfig.json` strict options:** only `"strict": true` is enabled. Also set: `target/lib ESNext`, `allowJs`, `skipLibCheck`, `noEmit`, `esModuleInterop`, `module esnext`, `moduleResolution bundler`, `resolveJsonModule`, `isolatedModules`, `jsx react-jsx`, `incremental`, Next plugin, `types [node,bun]`, path `@/*`. **Not set:** `noUnusedLocals`, `noUnusedParameters`, `noImplicitReturns`, `noFallthroughCasesInSwitch`, `exactOptionalPropertyTypes`, `noUncheckedIndexedAccess`, `useUnknownInCatchVariables` (therefore inherited only from `strict`).

**Key file contracts:**
- `front/src/shared/api/placeholder-count.ts::countPlaceholdersInDocx(blob): Promise<Record<string,number>>` — single-flight JSZip; scans every non-dir `.xml` entry except `[Content_Types].xml`; regex `/\[([A-Z]+)_\d+\]/g`; labels lowercased. Per `TODO.md` §4.5, this scan and the preview render start together (double decompression) — deferred.
- `front/src/shared/api/lazy-zip.ts` — `createJsZipLoader(importModule)` caches one promise including rejection; `loadJsZip` dynamic-imports `jszip` with CJS-interop fallback and throws `Не удалось загрузить модуль обработки архивов.` on failure.
- `front/src/shared/api/mask-archive.ts` — `ResultManifest` schema v1; `readManifest` requires `schema_version===1`, matching `operation`, `status:"succeeded"`, `statistics` and `coverage`; `unpackMaskArchive` / `unpackRestoreArchive` select entries by `.docx`/`.yaml`/`manifest.json` and validate the manifest as the single source of stats; `maskBundleFromEntries` / `restoreBundleFromEntries` handle job result ZIPs.
- `front/src/shared/ui/docx-viewer.tsx` — `DocxViewer({blob, surfaceRef?, highlight?, renderDocument?})`, `HIGHLIGHT_DEBOUNCE_MS = 120`, injectable `DocxRender(blob, container, isCurrent)`; one detached staging host per generation, `generationRef` guard, nodes committed only for the current generation; preview-only idempotent `<mark class="docx-marker">` via `highlightTexts`; cancelable debounce; error `Не удалось отобразить предпросмотр документа.`. **Discrepancy:** `e2e/rerender-smoke.spec.ts:9` still documents the debounce as 5 ms while the source constant is 120 ms (comment only; no code change).
- `app/__tests__/globals-conventions.test.ts` reads the stylesheet as text, so `globals.css` declares `@source not "../app/__tests__/globals-conventions.test.ts"` to keep its regex tokens out of Tailwind candidates.

## 6. Reproduce (Phase 0 commands)

```sh
cd front
bun run format:check
bun run lint:check
bun run typecheck
bun run test
bun run build
bun run gen:openapi --check
bunx --no-install next analyze --output --snapshot dm-baseline
bunx --no-install next analyze export --snapshot dm-baseline > <temp>/analyze-export.jsonl
bunx --no-install playwright test --project=smoke --list > <temp>/smoke-tests.txt
bun e2e/check-smoke-discovery.mjs <temp>/smoke-tests.txt
```

No source, config, lockfile or generated file was modified; final `git status --porcelain` is empty.
