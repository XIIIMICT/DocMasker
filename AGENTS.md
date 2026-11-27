# AGENTS.md — DocMasker (level 1: repository entry point)

Read this, then **only the guide for the area you are touching**. Each fact has
one canonical home; the per-area guides own hot-path landmines, `docs/` owns
durable specifications. Do not duplicate algorithms, schemas or API contracts
here.

## What it is

DocMasker masks sensitive data in Russian text and `.docx` with a deterministic
rule-based pipeline (natasha + phonenumbers + regex, no ML) and restores it from
Russian-section YAML keys. One page, two modes via a hover dropdown in the
brand: *Маскировка* and *Демаскировка*. Every entity becomes `[LABEL_N]`; the
original lives only in the keys file. `.docx` is previewed in-page and returned
with formatting preserved; `.txt` is read client-side as text. Large inputs
become durable jobs.

| Area | Guide (level 2) | Specifications (level 3) |
|---|---|---|
| Backend, masking, jobs | `back/AGENTS.md` → `back/src/masking/AGENTS.md`, `back/src/jobs/AGENTS.md` | `docs/ARCHITECTURE.md`, `docs/API.md`, `docs/MASKING_CONFIGURATION.md`, `docs/DOCX_SUPPORT.md`, `docs/PERFORMANCE.md` |
| Frontend | `front/AGENTS.md` | `docs/ARCHITECTURE.md` (front), `docs/API.md` |
| Compose, infra, CI | `infra/AGENTS.md` | `docs/WORKFLOW.md`, `docs/OPERATIONS.md`, `docs/SECURITY.md` |
| Tests / quality | per-area guides | `docs/TESTING.md`, `docs/WORKFLOW.md` |
| Decisions / status | `docs/adr/` | `docs/PRODUCTION_READINESS.md`, `TODO.md` |

Stack: `back/` FastAPI on Granian (Python 3.15, **uv**) + a separate worker;
`front/` Next.js 16 / React 19 / HeroUI v3 / Tailwind 4 (**bun**). Ports: front
**5001**, back **8001** (5000/8000 belong to ReportCollector — do not reuse).
The browser only calls its own origin; Next proxies `/api/*` server-side.

## Canonical commands (from the repo root)

| What | Command |
|---|---|
| Data stand, once | `just db-stand` (PostgreSQL + Valkey from `infra/compose.db.yaml`) |
| Schema / migration | `just seed` · `just migrate` · `just revision "msg"` |
| Dev / prod app stack | `just dev` / `just prod` (after the stand is seeded) |
| Quality | `just lint` · `just typecheck` · `just test` · `just e2e` |
| Monitoring | `just monitoring` (Alloy only, external Loki) · `just monitoring-stand` (full stack) — see `infra/AGENTS.md`, `docs/OPERATIONS.md` §8 |
| Backend direct | `uv run --directory back ruff check --fix` · `ruff format` · `ty check` · `pytest -q` |
| Frontend direct | `bun run --cwd front lint` · `format` · `typecheck` · `test` |

## Global safety rules

- **Never log or leak** inputs, keys, entity values, filenames, DB messages or
  tracebacks. Error responses carry only `{code, detail, request_id}` in Russian
  (`detail`) plus `X-Request-ID`. No auth/tenants in scope — private deployment.
- **Secrets:** credentials live only in the root `.env` (gitignored); private
  `back/config/masking/exceptions.yaml` is gitignored and mounted read-only.
  Never commit real PII or credentials; CI uses `exceptions.example.yaml`.
- **Git:** check `git status` and branch before any operation; never discard
  foreign or untracked work. No commit/push without explicit maintainer
  instruction. Conventional `type(scope)` + Why/What/Verified body; task branch
  per phase from `dev`, `git merge --no-ff`, no squash. `main`/`dev`/`feature/*`.
- **Files:** UTF-8, no BOM, LF (`.gitattributes` + prek). PowerShell
  `Set-Content -Encoding UTF8` adds a BOM — use the file tools.
- **Hook policy (shared, maintainer decision 2026-11-25):** every static checker
  (ruff, ty, oxfmt/oxlint, `bun check`) runs on the **staged files** at commit
  and on the **whole tree** in CI; no `manual` stage in any `prek.toml`.
- **Docstring policy:** D100–D107 are intentionally ignored in
  `back/pyproject.toml`. Write a docstring only for a non-obvious contract or the
  reason behind a decision; do not add trivial ones or mass-delete existing ones.
- **No debug artifacts** (`debug_*`, probes, stray scripts) in commits.
- **Tooling:** `rg` over `grep`, `uv` over bare Python, `bun` over node. Use `gh` to inspect GitHub Actions runs and failed logs (`gh run list`, `gh run view --log-failed`) instead of inferring CI state from local checks. If `rg`, `uv`, `bun` or `gh` is unavailable, ask the maintainer to install it rather than substituting another tool or skipping the check.
- **Ambiguous product/config/process decisions → ask the maintainer.** Only
  safe, reversible technical choices may proceed with documentation.
- **Languages:** code comments/docstrings, commits and agent docs — English;
  user-facing strings (UI, API errors) — Russian.
- Sync the running dev stack by restarting with `just dev` (compose `--watch`),
  never `docker cp`.

## Definition of done

Before delivery: matching per-area guide + `docs/` file updated in the same
change; back lint/typecheck/tests (≥80% branch, deterministic offline), front
lint/typecheck/tests (65% line gate), Playwright smoke on a running stack,
actionlint + both compose configs green. Full matrix — `docs/TESTING.md` and
`docs/WORKFLOW.md`. CI branch protection / required check is a **GitHub-side**
setting — never claim it is enabled from the repository.
