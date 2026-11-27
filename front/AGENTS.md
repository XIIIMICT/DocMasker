# front/ — frontend agent guide (level 2)

Root `AGENTS.md` is the entry point. Durable contracts: `docs/API.md`,
`docs/ARCHITECTURE.md`, `docs/TESTING.md`, `docs/DOCX_SUPPORT.md`; deployment —
`infra/AGENTS.md`; backend — `back/AGENTS.md`. OpenAPI→TS generation is deferred
(`TODO.md`): `labels.generated.ts`, produced from the backend's
`back/config/masking/labels.yaml`, is the only generated API file.

## Stack and commands (from `front/`)

Next 16.4.6 / React 19.3.1 / HeroUI **3.2.8 (v3 only)** / Tailwind 4, Bun ≥1.4.3
for `bun check`, Node 26 in the production container. **Exact versions are in
`bun.lock`**, not in `package.json` ranges.

| What                         | Command                                                                                 |
| ---------------------------- | --------------------------------------------------------------------------------------- |
| Install / dev / prod build   | `bun install` · `bun run dev` · `bun run build`                                         |
| Lint / check                 | `bun run lint` · `bun run lint:check`                                                   |
| Format / check               | `bun run format` · `bun run format:check`                                               |
| Typecheck                    | `bun run typecheck` (`bun check`)                                                       |
| Unit tests                   | `bun run test` (`bunfig.toml` + `src/test-setup.ts`, happy-dom)                         |
| Coverage gate                | `bun test --coverage --coverage-reporter=lcov` then `bun scripts/check-coverage.mjs 65` |
| e2e (running stack required) | `bun run test:e2e --project=smoke` with `E2E_BASE_URL`                                  |

`bun run format` can rewrite line breaks in untouched files — check `git status`
and `git diff` afterwards. The current unit inventory (29 test files) and the
Playwright `smoke` discovery guard live in `docs/TESTING.md` §1. The `smoke`
project currently lists 35 tests in four spec files (`health`, `smoke`,
`rerender-smoke`, `hover-smoke`) and needs a prepared stack; probe files are
excluded. CI pipes `playwright test --project=smoke --list` into
`bun e2e/check-smoke-discovery.mjs`, which requires the exact four basenames,
≥20 tests in the main spec and ≥34 overall — `smoke.spec.ts` can no longer be
satisfied by the two other `*smoke.spec.ts` names. The listing needs no running
stack:

```sh
bunx --no-install playwright test --project=smoke --list > smoke-tests.txt
bun e2e/check-smoke-discovery.mjs smoke-tests.txt
```

`local.playwright.config.ts` is the local-only Chrome-on-PATH helper; it
derives discovery from the committed config and overrides only the launcher
and reporter. Shared E2E helpers: `e2e/helpers/selectors.ts` (hidden file input)
and `e2e/helpers/timing.ts` (two-frame gesture grace, close-grace helper).
Unit timing tests use `jest.useFakeTimers()` from `bun:test` with `useRealTimers()`
in `finally`/`afterEach`.

## HeroUI v3 — mandatory reference

Index: <https://heroui.com/react/llms.txt>; full text: `llms-full.txt` (~5.8 MB —
fetch one page from the index or `rg` a local copy, **never load it whole**);
theme guide: `DESIGN.md`. The `heroui-react` MCP serves the same docs. Site docs
may be newer than 3.2.8 — verify the installed version and its TypeScript
declarations before using an unfamiliar API. Never copy `llms-full.txt` into the
repo. Verified gotchas:

- `Button`: `variant="primary"` / `isDisabled` — no `color` or `isLoading`.
- `TextArea` (exact spelling) is the bare react-aria primitive: `value` /
  `onChange`, `disabled`, `readOnly`; no `label`, `onValueChange`, `isDisabled`,
  `minRows`.
- `Tabs` needs `Tabs.ListContainer` (pill) **and** `<Tabs.Indicator />` in every
  `Tabs.Tab` (moving highlight); a `Tabs.Tab` is a `<button>` — never nest a
  button, wrap icons in `Tooltip.Trigger`. Theme switcher: icon-only `Tabs`,
  **no `Tabs.Panel`**, `next-themes` persists to `localStorage`.

## Critical invariants

1. Two modes («Маскировка» / «Демаскировка») switch through a hover dropdown in
   the brand (`brand.tsx` + `hover-dropdown.tsx`, `menuitemradio`), **not** Tabs.
   State is `Record<AppMode, ModeState>` + `update(patch)` in
   `features/masking/use-masker-workflow.ts` — switching keeps both drafts.
   `document.title` is per mode; there is deliberately **no `metadata.title`**
   (Next 16/React 19 would overwrite the client-set title after hydration).
   Last mode lives in `localStorage["docmasker.mode"]`; restoring it and writing
   the title share one `useEffect([mode])`. Titles are exactly
   `ДокМаскер / Маскировка` and `ДокМаскер / Демаскировка`.
2. `docx-preview` is client-only (`"use client"` + `useEffect`); never call
   `renderAsync` server-side. Highlights are applied to rendered DOM **after**
   render, preview-only, idempotent, and guarded by `generationRef` against a
   stale file switch. Downloaded documents are never altered.
3. The two panes scroll by **ratio** (`useSyncedScroll`), not pixels. Narrow
   screens (`<64rem`) stack them; only `.editor-pane__label--mask-input` drops
   its desktop `min-height`. Keep `overflow-y: auto` but hide the scrollbar;
   the page itself must not scroll.
4. `.txt` is input text, read client-side and never posted to `/api/mask-file`;
   only `.docx` goes to the backend. The result label `N фрагментов` counts
   distinct values (`uniqueTotal`); `manifest.json` (with `unused_keys`) is the
   single source of UI stats — never headers or placeholder counting. Tooltip
   body: `shared/mask-stats.ts::statsBreakdown` (`unique (total с повторами)`
   per label, plus `Не применено N:` on demasking when `N != 0`). DOCX
   occurrence counts come from `placeholder-count.ts::countPlaceholdersInDocx`.
5. Keys are validated on both sides: `validateKeysYaml` in
   `shared/api/mask.ts` runs on **every keystroke**; the backend authority is
   `back/src/masking/mapping.py::validate_keys_yaml` (422 with Russian `detail`).
   The editor gets `aria-invalid` and a red message; request is not sent. Keys
   file is `.yaml` (read also `.yml`), with the `Профиль` marker. Never edit
   `labels.generated.ts` by hand; regenerate with
   `bun run --cwd front gen:labels` when the backend catalogue changes.
6. Route timeouts are canonical in `shared/api/timeouts.ts` (15 s JSON/job,
   120 s upload, 300 s result, 20 min client job-wait).
   `proxy.ts::postToBackend` combines `AbortSignal.timeout` with the incoming
   abort signal, forwards `X-Request-ID`, and streams multipart unchanged
   (`duplex: "half"`, never `formData()`). Long work goes through `/api/jobs`
   (`app/api/jobs/[...path]/route.ts`): `waitForJob` polls `status_url` with
   backoff under `JOB_WAIT_TIMEOUT_MS`, unpacks the flat result ZIP, and
   `endJob()` clears the id on a terminal state or dismissal.
7. Job cancel/resume: only the opaque job id is kept in
   `sessionStorage["docmasker.activeJob"]`; `DELETE /api/jobs/{id}` cancels the
   server job, an `AbortController` only stops polling. `cancelJob()` returns
   `accepted` / `already_terminal` / `failed` and never treats a non-2xx or
   network error as confirmed; the UI only clears active state on accepted or
   terminal, and leaves polling running when cancellation could not be
   confirmed. `waitForJob()` removes each abort listener when its poll timer
   fires and clears the timer on abort. A result ZIP without `result.json`
   clears cleanly; the browser retains the full result Blob while JSZip
   unpacks it (documented client-side memory limit, not streaming).

## Layout (detail — docs/ARCHITECTURE.md)

`app/page.tsx` reads `SUPPORT_MAIL` and renders `app/masker-page.tsx` (thin view);
handlers live in `features/masking/use-masker-workflow.ts`, presentation in
`shared/ui/input-panes.tsx`, `result-panes.tsx`, `doc-preview-shell.tsx` and
`pane-types.ts`; `shared/api/*` holds adapters and proxies; `app/api/*` are
server-side proxies (browser only calls its own origin);
`src/instrumentation.ts` sets bounded undici timeouts.

## Working rules

- Static checkers run on staged files at commit and the whole tree in CI
  (shared maintainer policy 2026-11-25).
- **Docstring policy:** D100–D107 are ignored in `back/pyproject.toml`; write a
  docstring only for a non-obvious contract, never to satisfy a linter.
- Update the matching `docs/` file in the same change; no debug artifacts; no
  PII in logs, metrics, errors or cache keys.
