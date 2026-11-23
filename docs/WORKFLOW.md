# Workflow (environment, quality, conventions)

Durable standards for working in DocMasker. Hot-path facts and landmines live in
`AGENTS.md`; this file is the detail behind them. When behavior changes, update
this file in the same change (see "Documentation discipline").

## Environment

1. Copy the config: `cp .env.example .env` (root — single source).
2. Dependencies: `just deps` (`uv sync --directory back` + `bun install --cwd front`).
3. Run: `just dev` (compose, profile `dev`, hot-reload via `--watch`) — starts `dm-back-dev` and `dm-front-dev`.

There are no host-run recipes for the servers (`just back` / `just front` are commented out in the `justfile`). If needed, run the backend manually with Granian: `uv run --directory back granian --interface asgi --reload --reload-ignore-dirs logs src.main:app --port 8001`. For host-run installs the `justfile` exports `UV_SYSTEM_CERTS=1` and `NODE_OPTIONS=--use-system-ca` so corporate CAs are trusted.

Ports come from the root `.env` (`BACKEND_PORT`=8001, `FRONT_PORT`=5001); they are mandatory and fail loudly if unset. The backend starts instantly — the masker is deterministic, there is no model to download and no cache volume.

CI prerequisites (once, in GitHub repo settings): push the repo, create the `main` and `dev` branches remotely (push triggers fire only for `main`/`dev` — pushes to other branches run no CI), make `dev` the default branch, and set **`CI required`** as the required status check for the protected branches. A job *named* `CI required` is not a required check by itself — branch rulesets live in GitHub repository settings. CodeQL additionally needs code scanning enabled (see "CI" below).

## Quality (what we check before delivery)

| Side | Lint | Format | Typecheck | Tests |
|---|---|---|---|---|
| back | `uv run --directory back ruff check --fix` | `uv run --directory back ruff format` | `uv run --directory back ty check` | `uv run --directory back pytest -q` |
| front | `bun run --cwd front lint` (oxlint --fix) | `bun run --cwd front format` (oxfmt) | `bun run --cwd front typecheck` (bun check) | `bun run --cwd front test` (bun test, happy-dom) |
| e2e | — | — | — | `bun run --cwd front test:e2e` (Playwright, chromium, workers=1) |

Aggregators: `just lint`, `just typecheck`, `just test`, `just e2e`.

### Back

- `ruff` with rule `ALL` and explicit exclusions (`back/pyproject.toml`); Cyrillic `RUF001-003` allowed, docstrings optional.
- `ty check` — static type checker (**not mypy**).
- `pytest`: `asyncio_mode=auto`, `filterwarnings=error` (with a scoped ignore for the `pkg_resources` deprecation that `natasha`→`pymorphy2` emits). Tests are deterministic and offline — no model, no network.
- Test files: `back/tests/test_rule_based.py` (CAPSLOCK, inflected names, phone/email/address/org cases, label overrides), `test_mask.py` (service + `/api/mask` endpoint), `test_docx.py` (DOCX masking + formatting preservation), `test_mask_file.py` (multipart endpoints + download headers).
- Coverage: `--cov=src --cov-branch`; CI requires ≥80% (see `[tool.coverage]` in `back/pyproject.toml`).

### Front

- `oxlint --fix`, `oxfmt` (oxc formatter), `bun check` (Bun's built-in type checker — needs **bun ≥ 1.4.3**).
- **Attention:** `bun format` (oxfmt) may rewrite line breaks in untouched files. After format, check `git status`/`git diff`; restore foreign files with `git checkout -- <file>`.
- Unit tests — `bun test` with happy-dom (`front/src/test-setup.ts`, wired via `bunfig.toml`); they live in `__tests__` next to the code and run from `src` only.
- e2e — Playwright (`front/playwright.config.ts`, `front/e2e/`). The `smoke` project hits a running stack; see below.

### e2e (Playwright)

The `smoke` project needs a running stack (no auto-start): start it with `just prod` (or the CI compose stack) and point `E2E_BASE_URL` at the front origin (default `http://127.0.0.1:5001`). Covered (17 scenarios in 3 files, including the `rerender-smoke.spec.ts` regression — the `smoke` project's `testMatch` regex is unanchored, so it matches): `/api/health`, page rendering, tabs/mode switching, text mask and unmask round-trips, invalid-keys UX, DOCX preview highlight counts, unused-keys reporting. Probe files (`*-probe*`, `probe/`) are excluded from discovery and Git. Never run it against an unprepared stack.

### Live UI checks (browser)

For visual checks use the already-running dev stack — **`just dev`** (`http://127.0.0.1:5001`); compose `--watch` rebuilds on change, so no image rebuild is needed. Browser options available in this environment: **BrowserMCP**, the **built-in Orca browser** (`orca tab create --url …`, `set device --name "iPhone 12"`, `snapshot`, `screenshot`, `eval` — see the orca-cli skill), or **local Chrome via Playwright**. Prefer the dev stack over temporary prod containers; it also avoids competing for ports 5001/8001. Do not paste real PII into a live session/screenshot.

## Pre-commit (prek)

Root `prek.toml` — builtin hooks (check-added-large-files, check-merge-conflict, check-yaml, end-of-file-fixer, trailing-whitespace, mixed-line-ending `--fix=lf`, fix-byte-order-marker). `back/prek.toml` — ruff format, ruff check --fix, ty check, uv lock. `front/prek.toml` — oxfmt, oxlint, bun check, bun lock. Run on `git commit` (`prek install`). If a hook fixes something itself, re-check the diff and commit again.

### Line endings & encoding (LF, UTF-8, no BOM)

`/.gitattributes` (`* text=auto eol=lf`) normalizes every text file to LF in the index and in working copies, overriding `core.autocrlf` — so no "phantom" modifications on any OS. prek strips CRLF and BOMs on commit.

**Write files as UTF-8 without BOM.** PowerShell `Set-Content -Encoding UTF8` adds a BOM — use the file tools or Python instead.

## Languages (standard)

Language is chosen by audience — a hard rule, not a suggestion:

| What | Language | Why |
|---|---|---|
| Identifiers, variable/function names | EN | already the case |
| Code comments and docstrings (new **and existing**) | EN | code is read by agents on every task; one language beats a mix |
| Commit messages | EN | conventional headers; body keeps the Why/What/Verified structure |
| Agent documentation (`AGENTS.md`, `docs/*`) | EN | hot path: re-read every task; EN costs ~1.5–2× fewer tokens for the same meaning |
| User-facing strings: UI labels, button text, API errors shown to the user | RU | the product is for Russian users — content, not documentation. **Never translate these** |
| Future human/developer documentation | RU | read by humans, not agents |

Exact UI labels, screen/column names and error examples stay in Russian as quotations inside English prose.

## Commits (conventional)

`type(scope): short English summary` + body `Why/What/Verified`. Types: `feat`/`fix`/`refactor`/`docs`/`test`/`chore`; scope — module (`masking`, `docx`, `front`, `api`, `docs`, …). Header ≤ ~72 chars; body 3–10 lines — enough for an agent to reconstruct intent without token bloat:

```
fix(docx): keep run formatting when an entity spans several runs

Why: masking rewrote whole runs and dropped bold/size on names split
across runs.
What: replace only the covered slice of each run, back-to-front; a
multi-run entity collapses to one [LABEL].
Verified: pytest test_docx.py, ruff, ty.
```

The user commits themselves; **never commit or push without an explicit request.** Before any git operation check `git status` and the current branch — this repo can hold foreign edits; never discard untracked or foreign work.

### Branching and commits (git-flow-lite)

```
main        ← prod. squash-merge from dev + release tag vX.Y.Z; human documentation is updated here
dev         ← integration. squash-merge from feature/*; agent documentation is updated here
feature/*   ← from dev: granular conventional commits (Why/What/Verified)
```

1. **Branches** — short-lived, from `dev`: `fix/<short>`, `feat/<short>`, `docs/<short>` (ASCII, lowercase). One logical task per branch; start the next task only after the previous one is merged into `dev`.
2. **Commits** — conventional commits: `type(scope): short English summary`. Scope — module (`masking`, `docx`, `front`, `api`, `ci`, `docs`, …). Body — three sections, enough context for an agent without token bloat (header ≤ ~72 chars, body 3–10 lines).
3. **task/feature → dev:** only after green checks and user confirmation; merge with **`git merge --no-ff <branch>`** — a regular merge commit that keeps the branch's granular history (no squash). After the merge, run the integration checks affected by the change on `dev`, then delete the local branch; never delete a remote branch without permission.
4. **Agent documentation** (`AGENTS.md` + `docs/*`) is updated at this step — not earlier. Exception: landmines in `AGENTS.md` are written down immediately when discovered.
5. **dev → main:** squash-merge (release boundary, clean changelog), tag `vX.Y.Z`; human documentation is updated here. Release promotion requires explicit release approval.

### Merge checklist (into dev)

- [ ] all checks green (CI + local quality table);
- [ ] user confirmed the behavior works (for DOCX output — open the masked file; tests don't replace it);
- [ ] `AGENTS.md` and `docs/*` updated for the new behavior;
- [ ] debug files removed (probe scripts, `debug_*`);
- [ ] merge message per template `type(scope) + Why/What/Verified`.

## CI (GitHub Actions)

`.github/workflows/ci.yaml` is the single CI entry point (PRs and pushes to `main`/`dev`, manual runs, reusable release checks). Configure **`CI required`** as the required branch-protection check — it aggregates every job and there are no path filters that could leave it pending.

| Job | What |
|---|---|
| `config` | actionlint + `docker compose config` for `prod`/`dev` and `infra/compose.ci.yaml` |
| `back` | locked uv sync, ruff check/format, ty, pytest with branch coverage (artifact: coverage + JUnit) |
| `front` | Bun 1.4 + Node 26, `next typegen`, typecheck, oxlint, oxfmt `--check`, `bun test --coverage` |
| `production` | builds the prod images once, boots them via `infra/compose.ci.yaml`, runs the Playwright `smoke` project, exports tested images on a `v*` tag |
| `required` | `jq -e 'all(...result == "success")'` over `needs` — the branch-protection gate |

Notes:
- The backend job enforces **80% branch coverage** (`--cov-fail-under=80`); it fails below that.
- `production` builds the **backend image once** and starts it through compose; the deterministic masker means `/health` answers immediately, so the stack `--wait-timeout` is 120s.
- CI ports are isolated: front **15001**, back **18001** (`infra/compose.ci.yaml` never loads the developer's `.env` or volumes).
- `.github/workflows/codeql.yaml` runs CodeQL for `javascript-typescript`, `python` and `actions` (build-mode `none`). Enable code scanning in repo settings; use advanced setup, not default setup. CodeQL ≠ `CI required`.
- `.github/dependabot.yaml` — weekly updates for actions, docker, uv and npm (patch bumps ignored for deps).

Local reproduction of the `production` job: build the images (`docker compose -f infra/compose.ci.yaml build`), start them (`docker compose -f infra/compose.ci.yaml up -d --no-build --wait --wait-timeout 600`), then `E2E_BASE_URL=http://127.0.0.1:15001 bun run --cwd front test:e2e --project=smoke`. Tear down with `docker compose -f infra/compose.ci.yaml down --volumes --remove-orphans`.

## Definition of done

- [ ] quality checks green: back — typecheck + lint + tests; front — typecheck + lint + tests
- [ ] CI green (`CI required`) for the pushed branch/PR
- [ ] behavior verified by hand where tests can't see it — open the masked `.docx` for formatting fidelity; sanity-check the masker on real Russian text (names, addresses, orgs)
- [ ] `AGENTS.md` and the matching `docs/*` file updated for the new behavior
- [ ] no debug artifacts left behind (`debug_*`, `*-probe*`, stray `.mjs`/`.cjs`)
- [ ] commit message is a conventional commit + Why/What/Verified

## Documentation discipline

- **Documentation is part of the change.** Behavior, API, config, timings or a landmine changed → update `AGENTS.md` and the corresponding `docs/` file in the same change.
- **Record a landmine immediately** when you hit it — one line in `AGENTS.md`. Easy to lose by delivery time.
- **Docs are durable, not a changelog.** State how things are now; dates and "removed on …" belong in commit messages. Exact Russian quotations are the exception — keep them to map docs to the UI.
- **One home per fact.** A fact lives either in `AGENTS.md` (hot path) or in `docs/` (detail) — never duplicated, so the two cannot drift.

## Working principles

- **When in doubt, ask the maintainer — do not guess.** If a product, config or
  process decision is ambiguous (semantics, versions, naming, what to commit),
  stop and ask; a wrong silent guess costs more than a short question. Safe,
  reversible technical choices may proceed and be documented, but anything with
  product or convention weight needs an answer first.
- **Tooling preference: use ripgrep (`rg`) over grep, `uv` over bare Python, and
  `bun` over node.** `rg` — every code/doc search; `uv run --directory back …` —
  any Python execution (the interpreter is uv-managed, do not call `python`
  directly); `bun` — package management, scripts and TS/JS execution (do not
  call `node`/`npm`/`npx` outside containers). This keeps one toolchain per
  language and avoids drift with the lockfiles.
- **Minimal changes:** edit existing files; follow the file's style and the project's vocabulary (HeroUI v3 slots, the existing masking pipeline) — don't invent parallel patterns.
- **Precision over recall on names, measured not guessed.** The natasha guard (given name + surname/patronymic) and the address/org heuristics are empirical (see `docs/ARCHITECTURE.md`); change one rule at a time and back it with a test case.
- **Check before delivery** with the quality table above.
- **Sync into a running dev stack by restarting with `just dev`** (compose `--watch`), never by `docker cp`.
