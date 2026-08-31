# AGENTS.md — DocMasker

Cheat sheet for agents. Entry point: read only the section you need; details — in `docs/*`.

## What it is

DocMasker masks sensitive information in text using GLiNER (zero-shot PII NER). MVP: a single page with two HeroUI text areas («Входной текст» / «Результат») and a «Маскировать» button; the text is sent to the FastAPI backend, masked, and returned.

- `front/` — Next.js 16 (App Router, React 19, HeroUI v3 `@heroui/react`, Tailwind 4), package manager **bun**.
- `back/` — FastAPI 3 (Python 3.14, **uv**) + GLiNER 0.2.28 (`urchade/gliner_multi_pii-v1`, multi-lingual PII model downloaded from HuggingFace).
- Ports: front **5001**, back **8001** — from the root `.env` (`FRONT_PORT`/`BACKEND_PORT`). (5000/8000 are used by ReportCollector — do not reuse them.)
- Docker: `compose.yaml` (profiles `prod`/`dev`). The front proxies `/api/mask` server-side to the back via `BACKEND_INTERNAL_URL` (no CORS for the browser).

## Commands (from the repo root)

| What | Command |
|---|---|
| Stack dev (hot-reload) | `just dev` |
| Stack prod | `just prod` |
| Back lint/format/type/test | `uv run --directory back ruff check --fix` · `ruff format` · `ty check` · `pytest -q` |
| Front lint/format/type | `bun run --cwd front lint` · `format` · `typecheck` |
| Everything at once | `just lint` · `just typecheck` · `just test` |
| Back on host | `just back` (or `uv run --directory back -m uvicorn src.main:app --reload`) |
| Manual mask test | `just mask` (or curl against `/api/mask`, see docs/ARCHITECTURE.md) |

## API

- `GET /health` — readiness probe. The backend loads the GLiNER model in the FastAPI `lifespan`, so `/health` returns `ok` only after the model is loaded (first boot downloads it, cached in the `dm-hf-cache` volume).
- `POST /api/mask` — body `{"text": "..."}` → `{"text": "<masked>", "masked_entities": [{label, text, start, end, score}]}`.
- Front route `/api/mask` proxies to the back; the browser calls only its own origin.

## Landmines (critical!)

1. **GLiNER needs a glibc base image.** `torch` ships no musl (Alpine) cp314 wheels, so `back/Dockerfile` is Debian (`ghcr.io/astral-sh/uv:python3.14-bookworm` for deps, `python:3.14-slim` for runtime) — deviating from ReportCollector's Alpine convention. Don't "fix" it back to Alpine or the build fails with no torch wheel.
2. **HuggingFace cache volume ownership.** `back/Dockerfile` creates `/app/.cache/huggingface` owned by `appuser` BEFORE the compose volume `dm-hf-cache` mounts. If the volume is created with root ownership (e.g. first run before the mkdir fix), the backend crashes with `PermissionError` writing the HF cache. If that happens: `docker compose down && docker volume rm docmasker_dm-hf-cache && docker compose up --build`.
3. **`bun.lock` is committed and required by `front/Dockerfile`.** Do not put `frozenLockfile=true` in `bunfig.toml` until a lockfile exists — with no lockfile it hangs `bun install`. If the lockfile is missing, run `bun install` (from `front/`) to generate it before building the image.
4. **HeroUI v3 is Slot-based, not v2.** `Button` has no `color`/`isLoading` (use `variant="primary"`, `isDisabled`); `TextArea` is the bare react-aria primitive (`value`/`onChange`, `disabled`, `readOnly` — **no** `label`, `onValueChange`, `isDisabled`, `minRows`). The naming is `TextArea`, not `Textarea`. Wrong names surface as TS errors.
5. **Russian PII detection quality is limited by the model.** At `GLINER_THRESHOLD=0.35` the model reliably catches person/phone/email/location/organization but may miss long composite Russian addresses embedded in busy text (uni-encoder label-lottery). Don't chase false negatives by dropping the threshold below ~0.3 for default use; it trades in false positives.
6. **Windows/PowerShell mangles UTF-8 when sending bodies.** When testing the API from the host, put the JSON in a UTF-8 file and POST it with `curl.exe --data-binary @file`, or run Python inside a container. A Cyrillic body sent literally through PowerShell arrives corrupted and comes back as `?` — that's a test-harness artifact, not a server bug. The server and the Next proxy are verified UTF-8-correct.

## Working rules

- **Git:** root `.gitignore` — deny-all + explicit whitelist (`back/`, `front/`, `docs/`, etc.). A new root file must be explicitly whitelisted. Nested `back/.gitignore`, `front/.gitignore` are whitelist-style too.
- **Files: UTF-8, no BOM, LF.** Enforced by `/.gitattributes`. PowerShell `Set-Content -Encoding UTF8` adds a BOM — use the file tools or Python to write files.
- **Commits:** conventional commits `type(scope): …` + body Why/What/Verified. The user commits themselves; don't commit without an explicit request.
- **Before delivery:** typecheck + lint + tests on both sides (see the table). Back tests run with a stub model (no network/model download).
- **Sync into the running dev stack by restarting with `just dev`** (compose `--watch`), not by `docker cp`.
- **Languages:** code comments/docstrings, commits and this documentation — English; user-facing strings (UI labels, button text, API errors) — Russian.

## Documentation

- `docs/ARCHITECTURE.md` — system diagram, env vars, API, masking pipeline.
- `docs/WORKFLOW.md` — environment, quality commands, commit/format conventions.

**When behavior changes — update the corresponding file in `docs/`.** That's a requirement, not a wish: the docs are the only source of context for future agents.
