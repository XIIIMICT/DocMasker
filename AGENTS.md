# AGENTS.md — DocMasker

Cheat sheet for agents. Entry point: read only the section you need; details — in `docs/*`.

## What it is

DocMasker masks sensitive information in text using a **deterministic rule-based pipeline** (natasha + phonenumbers + regex; no ML). MVP: a single page with two HeroUI text areas («Входной текст» / «Результат») and a «Маскировать» button; the text is sent to the FastAPI backend, masked, and returned. DOCX files are also supported: upload a `.docx`, get the masked `.docx` back for download with formatting preserved.

- `front/` — Next.js 16 (App Router, React 19, HeroUI v3 `@heroui/react`, Tailwind 4, `docx-preview` for in-browser docx rendering), package manager **bun**.
- `back/` — FastAPI (Python 3.14, **uv**) + `natasha` (Russian morphology: names/addresses) + `phonenumbers` + `python-docx` + `python-multipart`. `setuptools<81` is pinned because `natasha`→`pymorphy2` imports the removed `pkg_resources` at runtime.
- Ports: front **5001**, back **8001** — from the root `.env` (`FRONT_PORT`/`BACKEND_PORT`). (5000/8000 are used by ReportCollector — do not reuse them.)
- Docker: `compose.yaml` (profiles `prod`/`dev`). The front proxies `/api/mask` and `/api/mask-file` server-side to the back via `BACKEND_INTERNAL_URL` (no CORS for the browser). The back image is **Alpine** (no PyTorch, no glibc constraint).
- CI: GitHub Actions (`main`/`dev` git-flow). `.github/workflows/ci.yaml` = lint/type/test + prod image build + Playwright smoke; `CI required` is the branch-protection gate. Details — `docs/WORKFLOW.md`.

## Commands (from the repo root)

| What | Command |
|---|---|
| Stack dev (hot-reload) | `just dev` |
| Stack prod | `just prod` |
| Back lint/format/type/test | `uv run --directory back ruff check --fix` · `ruff format` · `ty check` · `pytest -q` |
| Front lint/format/type/test | `bun run --cwd front lint` · `format` · `typecheck` · `test` |
| Everything at once | `just lint` · `just typecheck` · `just test` |
| e2e smoke (needs a running stack) | `just e2e` (or `bun run --cwd front test:e2e --project=smoke`, `E2E_BASE_URL`) |
| Back on host | `just back` (or `uv run --directory back -m uvicorn src.main:app --reload`) |
| Manual mask test | `just mask` (or curl against `/api/mask`, see docs/ARCHITECTURE.md) |

## API

- `GET /health` — readiness probe; always `ok` (the backend starts instantly — no model to load).
- `POST /api/mask` — body `{"text": "..."}` → `{"text": "<masked>", "masked_entities": [{label, text, start, end, score}]}` (score is `1.0` for deterministic matches).
- `POST /api/mask-file` — multipart field `file` (.docx) → the masked `.docx` as the body (`Content-Disposition: attachment`), count in the `X-Masked-Count` header.
- Front routes `/api/mask` and `/api/mask-file` proxy to the back; the browser calls only its own origin.

## Masking pipeline

Deterministic rules in `back/src/masking/rule_based.py` (`RuleBasedMasker`), in this order: emails (regex) → phones (`phonenumbers.PhoneNumberMatcher`, region RU) → persons (`natasha.NamesExtractor`, keeps only full names) → addresses (`natasha.AddrExtractor`, adjacent parts glued) → organisations (legal-prefix regex accepting `«», „“, “”, ‘’, ""` and `< >` quotes plus `, - –` separators; natasha 1.6 has no `OrganisationsExtractor`). Spans are then merged non-overlapping and the text is rewritten back-to-front. Labels: `person, phone, email, address, organization`. Details — `docs/ARCHITECTURE.md`.

## Landmines (critical!)

1. **`setuptools<81` is a hard runtime pin, not a lint convenience.** `natasha`→`pymorphy2` imports `pkg_resources` at runtime; on Python 3.14 setuptools is not a transitive dep, and setuptools ≥81 removed `pkg_resources`. Without the pin the first `MorphVocab()` raises `ModuleNotFoundError`. It also emits a `UserWarning`, so `filterwarnings=error` needs the scoped ignore in `back/pyproject.toml`.
2. **`natasha`'s morphological dictionary lives in memory and is expensive to build.** `MorphVocab()` + extractors load the pymorphy2 dictionary on first construction, so the masker is cached (`get_masker`, `lru_cache`) — never build a `RuleBasedMasker` per request. Tests construct one per test on purpose (isolated, still fast).
3. **`natasha` extractors are not wired the way the docs imply.** In 1.6 the *multi-match* API is `extractor(text)` (calling the instance) — `extractor.find(text)` returns only the **first** `Match` (and `Match` is iterable, so a naive `for m in find(text)` silently unpacks into ints). There is no `natasha.OrganisationsExtractor`; `AddrExtractor` yields one match per component (`AddrPart`), not one whole address.
4. **`NamesExtractor` over-fires on lone tokens.** It matches prepositions, single letters and bare surnames (`fact.last` only). `RuleBasedMasker` keeps a match only when the fact has a given name **and** a surname/patronymic — don't drop that guard or false positives return.
5. **HeroUI v3 is Slot-based, not v2.** `Button` has no `color`/`isLoading` (use `variant="primary"`, `isDisabled`); `TextArea` is the bare react-aria primitive (`value`/`onChange`, `disabled`, `readOnly` — **no** `label`, `onValueChange`, `isDisabled`, `minRows`). The naming is `TextArea`, not `Textarea`. Wrong names surface as TS errors.
6. **Windows/PowerShell mangles UTF-8 when sending bodies.** When testing the API from the host, put the JSON in a UTF-8 file and POST it with `curl.exe --data-binary @file`, or run Python inside a container. A Cyrillic body sent literally through PowerShell arrives corrupted and comes back as `?` — that's a test-harness artifact, not a server bug. The server and the Next proxy are verified UTF-8-correct.
7. **DOCX masking works at the run level (`paragraph.runs`).** Formatting lives on runs; masking replaces only the covered slice of each run so styles/sizes are preserved. An entity spanning **several runs** (e.g. `АО «Росхим»` = `АО «` | `Росхим` | `»`) must still yield **one** `[LABEL]`: `mask_paragraph` writes the marker into the run that contains the entity start and blanks the covered slices of the following runs — don't revert to per-run `mask_text`, which produces `[ORGANIZATION][ORGANIZATION][ORGANIZATION]`. `iter_paragraphs` covers body + table cells (recursive) + the *default* header/footer of each section — python-docx does not surface first-page/even header/footer variants as separate properties; if those must be masked, the OOXML `w:headerReference` parts need explicit handling.
8. **Don't compare python-docx `Run` objects by identity.** `paragraph.add_run(...)` returns a fresh proxy each call; assert on `.text`/formatting, not `run is run`.
9. **`iter_paragraphs` must keep a strong reference to `paragraph._p`.** python-docx hands out fresh proxies, so deduplicating by `id(paragraph._p)` alone drops paragraphs once an earlier proxy is garbage-collected and its address is reused by a later element (header/footer paragraphs vanished after repeated calls). The generator holds every seen element in a `keep_alive` list — keep it.
10. **`docx-preview` is a client-only renderer.** `DocxViewer` (`front/src/shared/ui/docx-viewer.tsx`) renders a Blob into a scrollable pane inside a `"use client"` component via `useEffect` — never call `renderAsync` in an RSC/server context. The rendered DOM is thrown away on unmount (`container.innerHTML = ""`).
11. **The two panes scroll in lock-step.** `useSyncedScroll` (`front/src/shared/hooks/use-synced-scroll.ts`) pairs the input/result surfaces (both text areas, or both DOCX previews) mapping the **scroll ratio**, not pixels — masked text is shorter. `DocxViewer` takes a `surfaceRef` so the parent can observe its scrollable element.
12. **Backend tests run with `filterwarnings=error` — deprecated APIs fail the suite.** Use `status.HTTP_422_UNPROCESSABLE_CONTENT`, not the deprecated `..._UNPROCESSABLE_ENTITY`. Any new endpoint path that the tests may hit must use non-deprecated symbols.

## Working rules

- **Git:** root `.gitignore` — deny-all + explicit whitelist (`back/`, `front/`, `docs/`, `infra/`, `.github/`, etc.). A new root file must be explicitly whitelisted. Nested `back/.gitignore`, `front/.gitignore` are whitelist-style too. Before any git operation check `git status` and the branch — never discard foreign or untracked work.
- **Don't commit debug artifacts:** scratch scripts (`debug_*`, `*-probe*`, stray `.mjs`/`.cjs`, Playwright `probe/`) are not committed — delete them after use.
- **Files: UTF-8, no BOM, LF.** Enforced by `/.gitattributes` + prek. PowerShell `Set-Content -Encoding UTF8` adds a BOM — use the file tools or Python to write files.
- **Commits:** conventional commits `type(scope): …` + body Why/What/Verified; `main`/`dev`/`feature/*` git-flow (`master` is not used) — template, branching and merge checklist in `docs/WORKFLOW.md`. The user commits themselves; don't commit without an explicit request.
- **Before delivery:** run the quality table (back: typecheck + lint + tests; front: typecheck + lint + tests) and make sure CI is green. Back tests are deterministic (no network) and enforce ≥80% branch coverage. The full "Definition of done" is in `docs/WORKFLOW.md`.
- **Sync into the running dev stack by restarting with `just dev`** (compose `--watch`), not by `docker cp`.
- **Languages:** code comments/docstrings, commits and agent docs — English; user-facing strings (UI labels, button text, API errors) — Russian. Full table — `docs/WORKFLOW.md`.

## Documentation

- `docs/ARCHITECTURE.md` — system diagram, env vars, API, masking pipeline.
- `docs/WORKFLOW.md` — environment, quality commands, languages, commits/branching, definition of done, working principles.

**When behavior changes — update the matching section here and in `docs/` in the same change.** That's a requirement, not a wish: the docs are the only source of context for future agents. Record a new landmine here immediately; keep each fact in exactly one home (hot path in `AGENTS.md`, detail in `docs/`) so the two cannot drift.
