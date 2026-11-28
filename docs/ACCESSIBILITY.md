# Accessibility — DocMasker (C1/C2, 2026-11-28)

This is the **single canonical home** for DocMasker's accessibility strategy,
the automated axe coverage and the manual keyboard/focus review. Static
component contracts stay in `front/AGENTS.md`; how to run the checks is in
`docs/TESTING.md`; scope and deferrals are summarised in
`docs/FRONTEND-AUDIT.md` §"Remaining limitations and deferrals".

**Current status: DONE.** Screen-reader verification is OUT OF SCOPE
by maintainer decision (2026-11-28); automated axe scans and manual
keyboard/focus review are the agreed coverage. Do not claim a screen-reader
audit. The 11 axe scans and
the keyboard/focus outcomes below are the landed evidence; they live in the
`smoke` spec, whose current inventory is **55 tests / 4 specs** with full smoke
**55/55** on `http://127.0.0.1:5001`. No screen reader was performed — see §4.

**Claims discipline:** the scans below are a regression net for a subset of
WCAG rules. Passing them does **not** mean the application is accessible.
Screen-reader verification is OUT OF SCOPE by maintainer decision (2026-11-28)
(see §4); no target-hardware assistive-technology testing was performed.

## 1. Tooling decision (maintainer decision C1)

- `@axe-core/playwright@4.14.1` was added as a **development dependency** with
  `bun add --dev @axe-core/playwright` from `front/`; `bun.lock` was updated by
  Bun. It brings `axe-core@4.14.1` as its own dependency. No runtime dependency
  was added and the production bundle is unchanged.
- Alternatives considered: `axe-core` with hand-written Playwright
  injection/analysis plumbing (same engine, more maintenance, no benefit);
  `jest-axe`/`vitest-axe` (happy-dom has no layout engine, so the
  `color-contrast` rule cannot run — the exact defect class found here would be
  invisible); Lighthouse CI (a whole-page score, not a per-state scan of
  overlays/errors, and a heavier reporting stack). The official Playwright
  adapter is the smallest maintained option for real-browser state scans.
- Every scan uses an **explicit ruleset**: axe tags `wcag2a`, `wcag2aa`,
  `wcag21a`, `wcag21aa` (4.14.1 also ships `wcag22aa`, which is deliberately not
  claimed here). `withTags` selects all rules carrying one of those tags.
  **No rule is disabled, filtered or globally excepted**; there is no proven
  third-party false positive in this scope, so no narrow exception was needed.
- Shared helper: `front/e2e/helpers/a11y.ts` (`WCAG_TAGS`,
  `waitForSettledUi`, `expectNoWcagViolations`). Scans live in the existing
  `smoke` project in `front/e2e/smoke.spec.ts` so the discovery guard keeps its
  exact four spec basenames. Each state is reached with a deterministic
  synthetic fixture or a routed response and web-first assertions; the helper
  then waits until `document.getAnimations()` has no running animation so axe
  never samples a half-faded popover. There are no `waitForTimeout` sleeps as
  evidence.

## 2. Automated coverage and findings

The scans cover the states required by the remediation brief. "Before" lists
the axe findings from the first probe on the unmodified branch (4.14.1, Chrome
on PATH via `local.playwright.config.ts`, light theme unless noted); "after" is
the current state of the same scan.

| State | Before (rule / impact) | Source fix | After |
|---|---|---|---|
| Mask mode, empty panes | `aria-command-name` serious (3 nodes); `nested-interactive` serious (3); `aria-valid-attr-value` critical (2); `color-contrast` serious (label) | See fixes 1–3 below | 0 violations |
| Mask mode, populated result + stats Tooltip | same four plus `color-contrast` on primary/stats/keys-tab text | Same; Tooltip trigger is no longer a nested button | 0 |
| Mask result error (routed 422) | error text low contrast via `--danger` | Darker `--danger`; error keeps `aria-invalid` + `aria-describedby` | 0 |
| Demask mode, empty panes | `color-contrast` on the unselected keys tab | Fix 3 | 0 |
| Demask invalid keys / result error | `aria-valid-attr-value` critical (selected keys tab); `color-contrast` on error text | Fix 2 + darker `--danger` | 0 |
| Brand dropdown open | transient `color-contrast` on menu checkmarks during popover animation | Settled-UI wait in the scan; no source change (the checkmark token already passes once the animation ends) | 0 |
| DOCX preview + waiting status; routed DOCX error | `color-contrast` on subtitle, preview action and error text | Darker `--muted` / `--danger`; status/error stay visible text, not colour-only | 0 |
| Narrow viewport (390×844), mask and demask validation | `aria-valid-attr-value` critical + muted contrast | Fixes 2–3 | 0 |
| Dark theme, populated | `aria-valid-attr-value` critical on the selected theme tab; `color-contrast` on primary/danger buttons | Fix 2 with static panel ids; darker dark-theme `--accent` / `--danger` | 0 |

### Source fixes (semantic, not rule suppression)

1. **Nested tooltip trigger / unnamed command.** `theme-switcher.tsx` wrapped
   the icon in a HeroUI `Tooltip.Trigger`, which React Aria renders as
   `role="button" tabindex="0"` *inside* a `role="tab"`. The wrapper is now
   `role="presentation" tabIndex={-1}`; the tab itself already carries the
   Russian accessible name («Системная», «Светлая», «Тёмная»). No redundant ARIA
   was added and the tooltip still appears on hover.
2. **Tabs without a panel.** React Aria gives the selected tab an
   `aria-controls` reference to `<tabsId>-tabpanel-<key>`. The theme switcher and
   both panes previously rendered no `Tabs.Panel`, so the reference was
   dangling (`aria-valid-attr-value`, critical). Each tab now has a real panel:
   static ids (`data`/`keys`, `result`/`keys`, `system`/`light`/`dark`) with the
   actual body inside. Panel ids are **static** because the collection caches a
   panel's id on first render: a panel whose `id` followed the selected key kept
   the old id after a switch and still produced the violation. Theme panels are
   `hidden` and empty — a preference has no content panel, but the reference
   must resolve. This is the one deliberate change to the old "no Tabs.Panel"
   note in `front/AGENTS.md`.
3. **Contrast tokens and unselected tabs.** Light `--muted` 55.17% → 50%,
   `--accent` 51.12% → 38%, `--danger` 65.32% → 48%; dark `--accent` → 38% and
   `--danger` 59.4% → 52%. HeroUI paints unselected tabs with its own muted
   utility and halves their opacity, so `.pane-tabs .tabs__tab:not([data-selected="true"])`
   now uses the accessible `--muted` at full opacity. `--focus` was left alone.

## 3. Manual keyboard / focus review (Playwright + Chromium, C2)

These scenarios were walked in a real browser against the dev stack at
`http://127.0.0.1:5001`; the repeatable ones are named tests in
`front/e2e/smoke.spec.ts` and are listed by name.

| Scenario | Outcome | Evidence |
|---|---|---|
| Tab order reaches every enabled control once, in DOM order | Pass — brand mode button → support link → theme tabs → input pane → result pane; disabled keys tab/download buttons are skipped by Tab but remain focusable-by-code and guarded | `клавиатура: логичный порядок фокуса, видимый индикатор и выход из страницы`; pre-existing `визуально неактивная кнопка …` |
| Visible focus indicator | Pass after fix — Chromium showed no ring from HeroUI's Tailwind ring variables; an explicit `:focus-visible` outline (`2px solid var(--focus)`, 2 px offset) now applies to buttons, links, textareas, tabs and menu items | Same test asserts `outlineStyle === "solid"` at every stop |
| No keyboard trap | Pass — the last control releases focus on the next Tab; no element captures it | Same test |
| Escape closes the mode menu; focus returns to the trigger | Pass — Enter focuses the selected menu item, ArrowDown moves to «Демаскировка», Escape closes the menu and returns focus to the brand button | `клавиатура: Enter открывает список режимов, Escape закрывает и возвращает фокус`; also `hover-smoke.spec.ts` Escape cases |
| Focus enters/returns for overlays | Pass — Tooltip triggers stay attached to their control; the mode popover is portaled and its menuitems are reachable; closing restores the trigger | Targeted axe Tooltip/dropdown scans plus the Escape test |
| Disabled actions are not operable | Pass — pointer and Enter/Space are intercepted at the workflow guard (`canDemask()`), not by CSS alone; the visually disabled buttons keep `aria-disabled=true` | Pre-existing smoke case; unit `action-boundaries.test.tsx` |
| Loading / success / error without colour alone | Pass — loading changes the button label to «Маскируем…», success puts text in the result textarea plus the `N фрагментов` label, errors put «Ошибка: …» in a read-only textbox with `aria-invalid` and point downloads at it with `aria-describedby` | `состояния загрузки, успеха и ошибки различимы не только цветом`; error-state axe scans |
| Reduced motion | Pass — all three `--app-motion-*` tokens and the computed transitions are `0s` under `prefers-reduced-motion: reduce`, while hover/keyboard end states remain; HeroUI's own reduce rules stay in the cascade | Pre-existing smoke cases; axe scans run in default motion |
| Narrow viewport | Pass — panes stack without overlap, mask input label drops its min-height, support label becomes a Tooltip on focus | Pre-existing narrow smoke cases; narrow axe scan |
| DOCX preview status/error | Pass — the waiting card, rendered paragraphs, highlight marks and routed 422 error are all perceivable as text | DOCX axe scans and the existing preview/highlight smoke cases |

HeroUI/RAC keyboard semantics (roving tab index in tabs and menus, Escape
dismissal) are used as supplied; no hand-built ARIA replaced them. Russian
user-facing strings were preserved.

## 4. Scope and what is NOT verified

- **Screen-reader verification: OUT OF SCOPE.** Screen-reader verification is
  OUT OF SCOPE by maintainer decision (2026-11-28); automated axe scans and
  manual keyboard/focus review are the agreed coverage. Do not claim a
  screen-reader audit. No screen reader was available in this environment and
  none was performed. Names/descriptions/roles were checked structurally (unit
  accessibility tests + automated scans + keyboard walkthrough), not through a
  screen reader.
- axe cannot assess point-of-view contrast, meaningful reading order, focus
  purpose, or whether an accessible name is useful to a person; the scans are
  not a substitute for the manual review above.
- No target-hardware or real assistive-technology/browser matrix run was done;
  all results are from local Chromium on this host with synthetic fixtures.
