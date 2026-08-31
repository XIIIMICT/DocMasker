# Workflow (environment, quality)

## Environment

1. Copy the config: `cp .env.example .env` (root — single source).
2. Dependencies: `just deps` (`uv sync --directory back` + `bun install --cwd front`).
3. Run: `just dev` (compose, profiles `dev`, hot-reload via `--watch`) — starts `dm-back-dev` and `dm-front-dev`. Or on the host `just back` / `just front`.

Persistence: the HuggingFace model cache lives in the `dm-hf-cache` volume, so `docker compose down` keeps the model; `just clean` removes volumes and re-downloads it.

## Quality (what we check before delivery)

| Side | Lint | Format | Typecheck | Tests |
|---|---|---|---|---|
| back | `uv run --directory back ruff check --fix` | `uv run --directory back ruff format` | `uv run --directory back ty check` | `uv run --directory back pytest -q` |
| front | `bun run --cwd front lint` (oxlint --fix) | `bun run --cwd front format` (oxfmt) | `bun run --cwd front typecheck` (tsc --noEmit) | — (none yet) |

Aggregators: `just lint`, `just typecheck`, `just test`.

### Back

- `ruff` with rule `ALL` and explicit exclusions (`back/pyproject.toml`); Cyrillic `RUF001-003` allowed, docstrings optional.
- `ty check` — static type checker (not mypy!).
- `pytest`: `asyncio_mode=auto`, `filterwarnings=error`. Back tests run **without** the real GLiNER model — they use a deterministic stub (`tests/test_mask.py::StubModel`, `tests/test_docx.py::StubDocxModel`) so no network/download is needed.
- Test files: `back/tests/test_mask.py` (text masking) and `back/tests/test_docx.py` (DOCX masking + formatting preservation).

### Front

- `oxlint --fix`, `oxfmt` (oxc formatter), `tsc --noEmit`.
- **Attention:** `bun format` (oxfmt) may rewrite line breaks in untouched files. After format, check `git status`/`git diff` and restore foreign files.

## Pre-commit (prek)

Root `prek.toml` — builtin hooks (mixed-line-ending `--fix=lf`, fix-byte-order-marker, trailing-whitespace, end-of-file-fixer, check-yaml, check-added-large-files, check-merge-conflict). `back/prek.toml` — ruff format, ruff check --fix, ty check, uv lock. `front/prek.toml` — oxfmt, oxlint, tsc, bun lock. Run on `git commit` (`prek install`).

### Line endings & encoding (LF, UTF-8, no BOM)

Delivered by `/.gitattributes` (`* text=auto eol=lf`) and prek. **Write files as UTF-8 without BOM.** PowerShell `Set-Content -Encoding UTF8` adds a BOM — use the file tools or Python to write files.

## Languages (standard)

Identifiers/comments/commits/docs — English; **user-facing strings (UI labels, button text, API errors) — Russian**. See the fuller table in ReportCollector's `docs/WORKFLOW.md`.

## Commits (conventional)

`type(scope): short English summary` + body `Why/What/Verified`. The user commits; don't commit without an explicit request.
