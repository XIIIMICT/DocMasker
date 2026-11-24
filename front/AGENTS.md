# front/ — frontend agent guide

This file owns the frontend landmines: exact stack versions, commands, the
HeroUI rules every agent must follow when touching UI code, and the state/UI
invariants. It is a level-2 guide: contracts live in `docs/API.md`, testing in
`docs/TESTING.md`, architecture in `docs/ARCHITECTURE.md`. Deployment facts live
in `infra/AGENTS.md`, backend facts in `back/AGENTS.md` and its nested guides.
OpenAPI→TS client generation is **deferred** (`TODO.md`); `labels.generated.ts`
is generated from the backend catalogue and is the only generated API file.

## Stack (exact installed versions, resolved from `bun.lock`)

Manifest ranges in `package.json` are not the installed versions — always read
`bun.lock` when you need the precise number. As of 2026-11-23:

| Package                            | Manifest              | Installed       |
| ---------------------------------- | --------------------- | --------------- |
| next                               | `16.4.6` (pinned)     | 16.4.6          |
| react / react-dom                  | `19.3.1` (pinned)     | 19.3.1          |
| @heroui/react                      | `^3.2.8`              | **3.2.8**       |
| @heroui/styles                     | `^3.2.8`              | 3.2.8           |
| docx-preview                       | `^0.4.1`              | 0.4.1           |
| jszip                              | `^3.10.2`             | 3.10.2          |
| undici                             | `^8.13.1`             | 8.13.1          |
| next-themes                        | `^0.4.6`              | 0.4.6           |
| iconoir-react                      | `^7.12.2`             | 7.12.2          |
| tailwindcss / @tailwindcss/postcss | `^4.3.5`              | 4.3.5           |
| typescript                         | `^7.0.2`              | 7.0.2           |
| oxlint / oxfmt                     | `^1.94.0` / `^0.79.0` | 1.94.0 / 0.79.0 |
| @playwright/test                   | `^1.65.1`             | 1.65.1          |
| happy-dom                          | `^21.0.4`             | 21.0.4          |

Runtime: Bun ≥ 1.4 (dev/typecheck — `bun check` needs ≥ 1.4.3); production
container runs **Node 26** (`node:26-alpine`). HeroUI is **v3** — never mix in
v2 APIs or examples.

## HeroUI — mandatory reference

Official full-text React reference: **<https://heroui.com/react/llms-full.txt>**

Rules:

1. Whenever a task touches HeroUI components — props, slots, composition,
   variants, styling, forms, overlays, accessibility — consult that reference
   for the specific component before writing code. Do not guess props from
   memory and do not reuse search-result examples blindly.
2. The site's current docs may describe an API newer than the installed
   package. Check the exact `@heroui/react` version in `front/bun.lock` first
   and only use APIs that version supports; when in doubt, verify against the
   package's own TypeScript declarations in `node_modules/@heroui/react`.
3. Never copy `llms-full.txt` (or any part of it) into the repository.
4. After changing HeroUI components, run the checks below (typecheck, lint,
   unit tests, and the relevant Playwright scenarios when UI behavior changed).

### HeroUI v3 facts (verified against 3.2.8)

- **Slot-based compound components, not v2.** `Button` has no `color` or
  `isLoading` — use `variant="primary"` and `isDisabled`.
- **`TextArea`** (exact spelling) is the bare react-aria primitive: `value` /
  `onChange`, `disabled`, `readOnly`. No `label`, `onValueChange`, `isDisabled`,
  `minRows` — those v2-style props do not exist and surface as TS errors.
- **`Tabs` needs composition** to look right: the pill background lives on
  `Tabs.ListContainer` (`.tabs__list-container`, `bg-default`) and the moving
  highlight on a `<Tabs.Indicator />` rendered inside every `Tabs.Tab` — omit
  either and the tabs render as bare text. `Tabs.Tab` renders a `<button>`, so
  never nest another button inside it; wrap icons in `Tooltip.Trigger` instead.
- The theme switcher (`shared/ui/theme-switcher.tsx`) uses icon-only `Tabs`
  with **no** `Tabs.Panel` — selection drives `next-themes` as a side effect;
  names appear only in hover `Tooltip`s.

## Layout map

- `app/page.tsx` — thin server component: reads `SUPPORT_MAIL` and renders the
  client `MaskerPage` (`app/masker-page.tsx`). The single page has two top-level
  modes («Маскировка» / «Демаскировка»). Mode state is `Record<AppMode, ModeState>` with an
  `update(patch)` helper: **switching modes keeps both drafts** — never
  reintroduce a reset-on-switch. The mode switcher is a hover dropdown inside
  the brand (`shared/ui/brand.tsx` + `shared/ui/hover-dropdown.tsx`,
  `menuitemradio` items), not `Tabs`.
- `app/masker-page.tsx` — thin view (header + editor grid + hidden file inputs);
  it calls `features/masking/use-masker-workflow.ts` for all state/handlers and
  renders `shared/ui/panes.tsx`; it owns no business logic.
- `features/masking/` — `types.ts` (ModeState, value types, constants,
  `emptyModeState`, `fileKind`), `use-masker-workflow.ts` (state, derived values,
  drag/drop, text/file/job handlers, downloads, scroll-sync refs).
  `shared/api/mask-helpers.ts` holds the pure backend/job response adapters,
  `shared/ui/panes.tsx` the four input/result panes + shared bits. Keep handlers
  in the hook and presentation in panes — a new feature adds a module here, not
  lines to `masker-page.tsx` again.
- `shared/ui/docx-viewer.tsx` — client-only `docx-preview` renderer
  (`"use client"` + `useEffect`; never call `renderAsync` on the server). The
  `highlight` prop wraps substrings in `<mark>` **after** render —
  preview-only; downloaded documents are never altered. Editing keys must not
  re-parse the document (highlights are re-applied in place).
- `shared/hooks/use-synced-scroll.ts` — pairs the two panes by scroll **ratio**
  (masked text is shorter than the original).
- `shared/api/mask.ts` — backend proxy helpers, the client-side keys validator
  (`validateKeysYaml`, runs on every keystroke) and the `Профиль` marker.
  `KEYS_SECTION_TITLES` / `KEYS_TITLE_LABELS` and the tooltip titles come from
  `shared/api/labels.generated.ts`, generated from the backend's canonical
  `back/config/masking/labels.yaml` (`bun run --cwd front gen:labels`; CI
  checks freshness). Never edit the generated file or re-introduce hardcoded
  title lists; `app/api/labels/route.ts` also proxies the runtime catalogue.
  Backend `mapping.py::validate_keys_yaml` remains authoritative.
- `shared/api/mask-archive.ts` — unpacks the `/api/mask-file` ZIP (jszip) into
  the masked document and its demasking keys.
- `shared/api/jobs.ts` / `jobs-types.ts` — client for the durable job API:
  `waitForJob` polls status with backoff and unpacks the flat result ZIP;
  all four mask/unmask handlers accept a 202 and show the same result.
  `app/api/jobs/[...path]/route.ts` proxies `/api/jobs/*` to the backend.
- `shared/mask-stats.ts` — result-label text and the stats tooltip body.
- `app/api/{mask,mask-file,unmask,unmask-file,health}/route.ts` — server-side
  proxies; the browser talks only to its own origin.
- `src/instrumentation.ts` — bounded global undici dispatcher
  (`headersTimeout: 60_000` / `bodyTimeout: 600_000`, finite outer net only).
  `shared/api/timeouts.ts` is the **canonical** route→timeout map
  (15 s JSON/job, 120 s upload, 300 s result, 20 min client job-wait);
  `shared/api/proxy.ts::postToBackend` applies it via `AbortSignal.timeout`
  combined with the incoming request's abort signal (`AbortSignal.any`),
  streams responses through `new Response(response.body)` and forwards
  `X-Request-ID`. File routes forward the multipart stream unchanged
  (`duplex: "half"`) — never rebuild `FormData`.

## Frontend landmines (moved from the root cheat sheet)

1. **HeroUI v3 is Slot-based, not v2** — see the HeroUI section above. `Tabs`
   renders no pill background and no selected indicator unless composed
   (`Tabs.ListContainer` + a `<Tabs.Indicator />` per `Tabs.Tab`); `Tabs.Tab`
   renders a `<button>`, so never nest another button inside it. The in-pane
   result tabs are a separate `Tabs` from the mode switcher and still need both.
2. **`docx-preview` is a client-only renderer.** `DocxViewer`
   (`shared/ui/docx-viewer.tsx`) renders a Blob into a scrollable pane inside a
   `"use client"` component via `useEffect` — never call `renderAsync` in an
   RSC/server context. The rendered DOM is thrown away on unmount
   (`container.innerHTML = ""`). Its `highlight` prop wraps listed substrings in
   `<mark>` **after** render — preview-only (mask placeholders for a masked
   preview, restored values for a demasked one); never write a highlight into
   the document on the backend. A generation guard (`generationRef` bumped on
   every render/cleanup) prevents a stale `renderAsync()` from writing the
   previous Blob's DOM after a fast file switch or unmount; re-applying
   highlights is idempotent (no nested `<mark>`).
3. **The two panes scroll in lock-step.** `useSyncedScroll`
   (`shared/hooks/use-synced-scroll.ts`) pairs the input/result surfaces (both
   text areas, or both DOCX previews) mapping the **scroll ratio**, not pixels —
   masked text is shorter. `DocxViewer` takes a `surfaceRef` so the parent can
   observe its scrollable element. It is disabled while the result pane shows an
   error (there is no document to align with). On narrow screens (`< 64rem`)
   the panes stack in one column: `.editor-grid` uses
   `grid-auto-rows: min-content; align-content: start` so each pane grows to
   its textarea + buttons — with `1fr` rows the first pane's buttons spill over
   the next pane's tab row/label. **Only the masking input label**
   (`.editor-pane__label--mask-input`) drops its desktop `min-height: 2.5rem`
   under this breakpoint: that height existed solely to centre the label
   against the result `Tabs` on the same row, and the Tabs move below on
   mobile. The grid keeps `overflow-y: auto` for tall
   documents but hides the bar (`scrollbar-width: none` +
   `::-webkit-scrollbar { display: none }`) — the page itself must not show a
   vertical scrollbar.
4. **A `.txt` is input text, not a document.** The front reads an uploaded
   `.txt` client-side into the input `TextArea` (its name is never shown) and
   never posts it to `/api/mask-file`; only `.docx` goes to the backend.
   Masked text is always downloadable as `.txt`. The result label reads
   `N фрагментов`; the `Tooltip` body (`shared/mask-stats.ts::statsBreakdown`)
   lists `Тип: occurrences (uniques уникальных)` per label, and on demasking
   appends a `Не применено N:` block of key values whose placeholder was absent
   from the masked input (no tooltip when `N = 0`).
5. **The theme switcher is icon-only `Tabs` with `Tooltip`, and theme state
   lives in `next-themes`.** `shared/ui/theme-switcher.tsx` uses `Tabs` with
   **no `Tabs.Panel`** (the selection is a `setTheme` side-effect) and one icon
   per `Tabs.Tab`; the name is shown only in the hover `Tooltip`. `next-themes`
   already persists the choice to `localStorage` (default `storageKey: "theme"`)
   — per browser, not per account; there is no server-side/user DB storage and
   none is planned. Render `"system"` until mounted (`useEffect`), or the
   server-rendered selected tab mismatches the client.
6. **The two top-level modes are switched by a hover dropdown inside the
   brand, not `Tabs`.** `shared/ui/brand.tsx` renders «ДокМаскер» + a ghost
   button with the active mode, opening a `Dropdown.Menu` on hover via
   `shared/ui/hover-dropdown.tsx` (the mode items are `menuitemradio`). Mode
   state is per-mode: `features/masking/use-masker-workflow.ts` keeps
   `Record<AppMode, ModeState>` and an `update(patch)` helper, so **switching
   modes keeps both drafts** — never reintroduce a reset-on-switch.
7. **Tab title and mode persistence.** `document.title` is always
   `ДокМаскер / Маскировка` or `ДокМаскер / Демаскировка` (never the old
   em-dash form); the static `<title>` in `app/layout.tsx` is only the
   pre-hydration label — there is deliberately **no `metadata.title`**, because
   Next 16/React 19 re-applies a metadata title after hydration and would
   overwrite the client-set per-mode title. The last mode is stored in
   `localStorage["docmasker.mode"]` and restored on load; the restore and the
   title write live in one `useEffect([mode])` so they cannot disagree.
8. **Keys are validated on both sides.** Backend `validate_keys_yaml`
   (`back/src/masking/mapping.py`) rejects empty/malformed YAML, unknown
   sections, non-list sections, empty values, duplicate values within a
   section and a duplicated section name, raising `KeysValidationError` → 422
   with a Russian `detail`. The front mirrors all of it in
   `shared/api/mask.ts::validateKeysYaml` — run **on every keystroke** (derived
   from `demaskKeys`, not on submit) — and both the module and the page import
   the generated label catalogue (`labels.generated.ts`) and the `Профиль` marker
   (no version keys — the pre-prod v2 envelope was retired). An invalid keys editor gets
   `aria-invalid` and a red inline message, and the request is never sent. The
   keys file is `.yaml` on **both** load and download (`.yml` also accepted on
   read). File results carry a required, validated `manifest.json` whose
   `statistics` (incl. `unused_keys`) is the single source of UI stats —
   never headers or placeholder counting; a missing/bad manifest is a clear
   error, never "0 entities".
9. **Proxy timeouts are bounded; jobs carry long work.**
   `src/instrumentation.ts` sets a finite global undici Agent
   (`headersTimeout: 60_000` / `bodyTimeout: 600_000`) — an outer net only;
   the old `0`/`0` unbounded dispatcher and `maxDuration = 3600` are gone.
   The **canonical** map is `shared/api/timeouts.ts`: 15 s JSON/job, 120 s
   upload, 300 s result, 20 min overall client job-wait.
   `shared/api/proxy.ts::postToBackend` applies `timeoutForRoute()` plus the
   incoming request's abort signal (`AbortSignal.any`), streams responses
   through `new Response(response.body)` and forwards `X-Request-ID`; file
   routes forward multipart unchanged (`duplex: "half"`, no `formData()`
   rebuild). `timeouts.test.ts` tests the route→timeout mapping.
   `app/api/jobs/[...path]/route.ts` proxies the job API. Long operations never
   depend on a held-open HTTP connection — they go through `/api/jobs`;
   `waitForJob` polls `status_url` with backoff under `JOB_WAIT_TIMEOUT_MS`
   and unpacks the flat result ZIP.

10. **Job Cancel / resume (Phase 2).** `use-masker-workflow.ts` tracks the
    active `JobAccepted` in `ModeState` and
    `sessionStorage["docmasker.activeJob"]` (only the opaque id — never
    input/keys/PII). While a job runs, the file button in both modes becomes
    «Отменить задание» and calls `DELETE /api/jobs/{id}` (`cancelJob`); an
    `AbortController` only stops polling, it does NOT cancel the server job.
    On reload the active job is resumed; file-job results (ZIP without
    `result.json`) clear cleanly with a message. `endJob()` clears the id on
    terminal state or explicit dismissal.

## Commands (from `front/`)

| What                             | Command                                                                                 |
| -------------------------------- | --------------------------------------------------------------------------------------- |
| Install                          | `bun install`                                                                           |
| Dev server                       | `bun run dev`                                                                           |
| Production build                 | `bun run build`                                                                         |
| Lint (fix) / lint check          | `bun run lint` / `bun run lint:check`                                                   |
| Format / format check            | `bun run format` / `bun run format:check`                                               |
| Typecheck (`bun check`)          | `bun run typecheck`                                                                     |
| Unit tests (bun test, happy-dom) | `bun run test`                                                                          |
| Coverage + threshold gate        | `bun test --coverage --coverage-reporter=lcov` then `bun scripts/check-coverage.mjs 65` |
| e2e (needs a running stack)      | `bun run test:e2e --project=smoke` with `E2E_BASE_URL`                                  |

Notes:

- `bun run format` (oxfmt) may rewrite line breaks in untouched files — check
  `git status`/`git diff` afterwards and restore foreign files.
- Unit tests live in `__tests__/` next to the code and run via `bunfig.toml`
  with happy-dom (`src/test-setup.ts`).
- The Playwright `smoke` project (`playwright.config.ts`, 21 tests in 3 files)
  targets a running stack; `local.playwright.config.ts` is a local-only helper
  (not committed). Probe files (`*-probe*`, `probe/`) are excluded from
  discovery and Git. CI lists the spec files explicitly so a renamed
  `rerender-smoke.spec.ts` cannot silently drop out of the suite.
