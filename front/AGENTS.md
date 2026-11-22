# front/ — frontend agent guide

Hot-path landmines live in the root `AGENTS.md`; this file is the frontend
detail: exact stack versions, commands, and the HeroUI rules every agent must
follow when touching UI code.

## Stack (exact installed versions, resolved from `bun.lock`)

Manifest ranges in `package.json` are not the installed versions — always read
`bun.lock` when you need the precise number. As of 2026-11-22:

| Package | Manifest | Installed |
|---|---|---|
| next | `16.4.6` (pinned) | 16.4.6 |
| react / react-dom | `19.3.1` (pinned) | 19.3.1 |
| @heroui/react | `^3.2.7` | **3.2.7** |
| @heroui/styles | `^3.2.7` | 3.2.7 |
| docx-preview | `^0.4.1` | 0.4.1 |
| jszip | `^3.10.2` | 3.10.2 |
| undici | `^8.13.1` | 8.13.1 |
| next-themes | `^0.4.6` | 0.4.6 |
| iconoir-react | `^7.12.2` | 7.12.2 |
| tailwindcss / @tailwindcss/postcss | `^4.3.5` | 4.3.5 |
| typescript | `^7.0.2` | 7.0.2 |
| oxlint / oxfmt | `^1.94.0` / `^0.79.0` | 1.94.0 / 0.79.0 |
| @playwright/test | `^1.65.1` | 1.65.1 |
| happy-dom | `^21.0.4` | 21.0.4 |

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

### HeroUI v3 facts (verified against 3.2.7)

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

- `app/page.tsx` — the single page, two top-level modes («Маскировка» /
  «Демаскировка»). Mode state is `Record<AppMode, ModeState>` with an
  `update(patch)` helper: **switching modes keeps both drafts** — never
  reintroduce a reset-on-switch. The mode switcher is a hover dropdown inside
  the brand (`shared/ui/brand.tsx` + `shared/ui/hover-dropdown.tsx`,
  `menuitemradio` items), not `Tabs`.
- `shared/ui/docx-viewer.tsx` — client-only `docx-preview` renderer
  (`"use client"` + `useEffect`; never call `renderAsync` on the server). The
  `highlight` prop wraps substrings in `<mark>` **after** render —
  preview-only; downloaded documents are never altered. Editing keys must not
  re-parse the document (highlights are re-applied in place).
- `shared/hooks/use-synced-scroll.ts` — pairs the two panes by scroll **ratio**
  (masked text is shorter than the original).
- `shared/api/mask.ts` — backend proxy helpers, the client-side keys validator
  (`validateKeysYaml`, runs on every keystroke) and `KEYS_SECTION_TITLES`. The
  section titles must stay in sync with the backend's
  `back/src/masking/constants.py::LABEL_TITLES` — add a title in both places or
  neither.
- `shared/api/mask-archive.ts` — unpacks the `/api/mask-file` ZIP (jszip) into
  the masked document + keys; the user never sees the archive itself.
- `shared/mask-stats.ts` — result-label text and the stats tooltip body.
- `app/api/{mask,mask-file,unmask,unmask-file,health}/route.ts` — server-side
  proxies; the browser talks only to its own origin.
- `src/instrumentation.ts` — global undici dispatcher with
  `headersTimeout: 0` / `bodyTimeout: 0`, so a large DOCX proxy round-trip is
  never aborted by Node's default ceiling (root `AGENTS.md` landmine 15). Do
  not "restore" the defaults without replacing the guarantee.

## Commands (from `front/`)

| What | Command |
|---|---|
| Install | `bun install` |
| Dev server | `bun run dev` |
| Production build | `bun run build` |
| Lint (fix) / lint check | `bun run lint` / `bun run lint:check` |
| Format / format check | `bun run format` / `bun run format:check` |
| Typecheck (`bun check`) | `bun run typecheck` |
| Unit tests (bun test, happy-dom) | `bun run test` |
| e2e (needs a running stack) | `bun run test:e2e --project=smoke` with `E2E_BASE_URL` |

Notes:

- `bun run format` (oxfmt) may rewrite line breaks in untouched files — check
  `git status`/`git diff` afterwards and restore foreign files.
- Unit tests live in `__tests__/` next to the code and run via `bunfig.toml`
  with happy-dom (`src/test-setup.ts`).
- The Playwright `smoke` project (`playwright.config.ts`) targets a running
  stack; `local.playwright.config.ts` is a local-only helper (not committed).
  Probe files (`*-probe*`, `probe/`) are excluded from discovery and Git.
