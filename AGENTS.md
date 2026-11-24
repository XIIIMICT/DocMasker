# AGENTS.md — DocMasker

Short index for agents. **Read only the section you need, then follow the
per-area guide** — each fact has exactly one home so the docs cannot drift:

| Area | Guide | Detail docs |
|---|---|---|
| Backend (`back/`) | `back/AGENTS.md` — stack, commands, natasha/DOCX/keys/jobs landmines | `docs/ARCHITECTURE.md`, `docs/DOCX_SUPPORT.md`, `docs/API.md` |
| Frontend (`front/`) | `front/AGENTS.md` — exact versions, HeroUI v3 rules, UI landmines | `docs/ARCHITECTURE.md` (front section) |
| Deploy / CI (`infra/`, `.github/`) | `infra/AGENTS.md` — compose, ports, volumes, TLS, CI jobs | `docs/WORKFLOW.md` (CI section) |
| Profiles / keys / jobs (decisions) | `docs/adr/0001-masking-profiles.md`, `docs/adr/0002-durable-job-execution.md` | `docs/TEMPLATES.md`, `docs/PERFORMANCE.md` |
| Process, quality, branching | `docs/WORKFLOW.md` | `TODO.md` (living status), `docs/PRODUCTION_READINESS.md` (findings matrix) |

## What it is

DocMasker masks sensitive information in text and `.docx` with a **deterministic
rule-based pipeline** (natasha + phonenumbers + regex; no ML) and **restores** it
from the generated keys. One page, two top-level modes switched by a hover
dropdown in the brand: *Маскировка* (text or `.docx` → masked output + demasking
keys) and *Демаскировка* (masked input + keys → original). Every entity becomes
`[LABEL_N]` and its original goes into a Russian-section YAML keys file; the
same string repeated reuses one number/key. `.docx` is previewed in-page and
returned with formatting preserved; `.txt` is read client-side into the input
text; drag & drop accepts `.docx`/`.txt` (and `.yaml` for the keys field).

- `front/` — Next.js 16 (App Router, React 19, HeroUI v3, Tailwind 4,
  `docx-preview`, `jszip`), package manager **bun**.
- `back/` — FastAPI on **Granian** (Python 3.15, **uv**) + natasha +
  phonenumbers + python-docx + `py-yaml12`, plus a separate **worker** container
  (`python -m src.jobs.worker`). The back image is Alpine.
- Ports from the root `.env`: front **5001**, back **8001** (5000/8000 belong
  to ReportCollector — do not reuse). `compose.yaml` profiles `prod`/`dev`.
- The browser calls only its own origin; the front proxies `/api/*` to the back
  server-side via `BACKEND_INTERNAL_URL` (no CORS for the browser).
- Durable jobs (ADR-0002 / PostgreSQL substrate ADR-0003): `just dev`/`just prod`
  start the worker **plus** `postgres` and `valkey`; PostgreSQL is the
  authoritative job store, the private `jobs-data` volume holds only id-keyed
  artifacts. Never log inputs/keys; `/api/jobs/{id}/result` is one flat ZIP.

## Commands (from the repo root)

| What | Command |
|---|---|
| Data stand (PostgreSQL + Valkey, once) | `just db-stand` |
| Seed / migrate PostgreSQL | `just seed` · `just migrate` · `just revision "msg"` |
| Stack dev (hot-reload, compose `--watch`) | `just dev` (after `db-stand` + `seed`) |
| Stack prod | `just prod` (after `db-stand` + `seed`) |
| Back lint/format/type/test | `uv run --directory back ruff check --fix` · `ruff format` · `ty check` · `pytest -q` |
| Front lint/format/type/test | `bun run --cwd front lint` · `format` · `typecheck` · `test` |
| Everything at once | `just lint` · `just typecheck` · `just test` |
| e2e smoke (needs a running stack) | `just e2e` (or `bun run --cwd front test:e2e --project=smoke`, `E2E_BASE_URL`) |
| Manual mask test | curl against `/api/mask` (see `docs/API.md`); there is no `just mask` recipe |

## API (one-line index; full contract — `docs/API.md`)

- `GET /health` — liveness, always `ok` (no model to load). `GET /ready` —
  PostgreSQL readiness (503 when the store is down); `GET /metrics` — coarse
  counters without PII. `GET /api/labels` — the single canonical label
  catalogue (`labels.yaml`, §5); `profile_id` and `GET /api/profiles` were
  removed in Phase 4. Setting `MASK_LABELS` fails startup.
- `POST /api/mask` / `/api/unmask` — text → JSON (`text`, `stats` aggregates,
  `keys` YAML) / masked text + keys → restored text.
- `POST /api/mask-file` / `/api/unmask-file` — `.docx` → a flat ZIP (masked doc
  + keys + `manifest.json` / restored doc + `manifest.json`). The manifest is
  the single source of statistics/coverage; the front requires and validates
  it, then offers two separate downloads; the user never sees the ZIP.
- `POST /api/jobs` (202 + `job_id`) · `GET /api/jobs/{id}` (status/phase/progress) ·
  `GET /api/jobs/{id}/result` (flat ZIP, `no-store`, 409 until ready) ·
  `DELETE /api/jobs/{id}` (cancel). The four sync endpoints transparently
  return `202 + JobAccepted` above `SYNC_MAX_TEXT_CHARS` /
  `SYNC_MAX_DOCX_BYTES` / `SYNC_MAX_DOCX_PARAGRAPHS`.
- Errors use one stable envelope `{code, detail, request_id}` + `X-Request-ID`
  header (`back/src/errors.py`); `detail` is Russian, no PII/paths/tracebacks.
  413/415/422/429/409/404 conventions, all headers and the canonical
  route→timeout map (`front/src/shared/api/timeouts.ts`: 15 s JSON/job,
  120 s upload, 300 s result) are in `docs/API.md`.
- Execution: `run_cpu` (bounded CPU pool) and `run_blocking` (separate bounded
  IO pool) keep DB/ZIP/XML/filesystem work off the ASGI event loop; Next streams
  multipart unchanged, the backend stages uploads with a real byte counter and
  validates from the path, job results are `FileResponse`. §8.2 OpenAPI→TS
  types are deferred by maintainer decision (`TODO.md`).

## Masking pipeline (one-line index; detail — `docs/ARCHITECTURE.md`)

Deterministic rules in `back/src/masking/rule_based.py`, in profile order:
emails → links → phones → persons (natasha `NamesExtractor` with a
both-parts-plausible-proper-name guard) → addresses (natasha `AddrExtractor`,
adjacent parts glued, digit-only runs rejected, `адрес:` line masked whole) →
organisations (regex prefixes `ООО/АО/…`, spelled-out legal forms matched
lemma-wise, natasha 1.6 has no `OrganisationsExtractor`). Spans are merged
non-overlapping (start asc, length desc, score desc, detector order) and the
text is rewritten back-to-front. Labels: `person, phone, email, link, address,
organization`. Masking is **reversible**: `mapping.py::MaskKeys` numbers each
entity per label in document order and records the originals as keys (YAML v2
via `yaml12`, Russian section titles, empty sections omitted); unmasking is a
plain placeholder→value substitution. **Landmines for all of this live in
`back/AGENTS.md` — read it before touching the pipeline.**

## Working rules

- **Git:** root `.gitignore` is deny-all + explicit whitelist (`back/`, `front/`,
  `docs/`, `infra/`, `.github/`, …); nested `back/`/`front/` `.gitignore` are
  whitelist-style too. A new root file must be whitelisted. Before any git
  operation check `git status` and the branch — never discard foreign or
  untracked work.
- **Don't commit debug artifacts:** scratch scripts (`debug_*`, `*-probe*`,
  stray `.mjs`/`.cjs`, Playwright `probe/`) are not committed — delete them.
- **Files: UTF-8, no BOM, LF.** Enforced by `/.gitattributes` + prek.
  PowerShell `Set-Content -Encoding UTF8` adds a BOM — use the file tools.
- **Commits:** conventional `type(scope): …` + body Why/What/Verified;
  `main`/`dev`/`feature/*` git-flow (`master` is not used) — template,
  branching and merge checklist in `docs/WORKFLOW.md`. The user commits
  themselves; don't commit without an explicit request.
- **Before delivery:** run the quality table (back: typecheck + lint + tests;
  front: typecheck + lint + tests) and make sure CI is green. Back tests are
  deterministic (no network), `filterwarnings=error`, ≥80% branch coverage;
  front has a 65% line-coverage gate. Full "Definition of done" —
  `docs/WORKFLOW.md`.
- **When in doubt, ask the maintainer — do not guess.** Ambiguous
  product/config/process decisions go to the user first; only safe, reversible
  technical choices may proceed with documentation. See "Working principles" in
  `docs/WORKFLOW.md`.
- **Tooling: ripgrep (`rg`) over grep, `uv` over bare Python, `bun` over node**
  — one toolchain per language, aligned with the lockfiles.
- **Sync into the running dev stack by restarting with `just dev`** (compose
  `--watch`), not by `docker cp`.
- **Languages:** code comments/docstrings, commits and agent docs — English;
  user-facing strings (UI labels, button text, API errors) — Russian. Full
  table — `docs/WORKFLOW.md`.

## Documentation

- `TODO.md` — живой список оставшихся работ по production-readiness (обновлять
  при каждом этапе).
- `back/AGENTS.md`, `front/AGENTS.md`, `infra/AGENTS.md` — per-area agent guides
  (landmines, commands, exact versions).
- `docs/ARCHITECTURE.md` — system diagram, env vars, pipeline, canonical
  toolchain version table. `docs/API.md` — HTTP contract. `docs/TEMPLATES.md`
  — keys/masking configuration/exception recipes. `docs/PERFORMANCE.md` — benchmarks and limits.
- `docs/WORKFLOW.md` — environment, quality commands, languages,
  commits/branching, CI, definition of done, working principles.
- `docs/PRODUCTION_READINESS.md` — living findings matrix (statuses, evidence,
  stage plan). `docs/DOCX_SUPPORT.md` — OOXML coverage/limitations.
- `docs/adr/` — accepted decisions. `back/benchmarks/` — reproducible perf
  probes (run before/after perf-touching changes).

**When behavior changes — update the matching per-area guide and `docs/` file
in the same change.** Record a new landmine immediately in that area's
`AGENTS.md`; keep each fact in exactly one home (hot path in the per-area
guide, detail in `docs/`) so the two cannot drift.
