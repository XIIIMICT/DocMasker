# DocMasker Architecture

Sensitive-information masking in text via GLiNER (zero-shot PII NER). Monorepo: `front/` (Next.js), `back/` (FastAPI), `docs/`.

## System diagram

```
Browser ── http://<host>:5001 ──► front (Next.js, dev: bun dev / prod: standalone)
                                   ├── /            → the single masking page
                                   └── /api/health  → healthcheck
                                   └── /api/mask    → server-side proxy (text)
                                   └── /api/mask-file → server-side proxy (DOCX)
front ── BACKEND_INTERNAL_URL ──► back (FastAPI/uvicorn, :8001)
                                     ├── POST /api/mask      → GLiNER → masked text
                                     └── POST /api/mask-file → GLiNER → masked .docx
```

The browser talks only to the front origin; the front proxies `/api/mask` to the back server-side, so there is **no CORS for the browser** (CORS is configured on the back only for direct dev access, e.g. curl).

## Services and compose profiles

`compose.yaml` (name: `docmasker`) defines two service pairs:

| Service | Container | Profile | What |
|---|---|---|---|
| `back` / `back-dev` | `dm-back` / `dm-back-dev` | prod / dev | FastAPI, uvicorn, port `BACKEND_PORT` (GLiNER model loaded at startup) |
| `front` / `front-dev` | `dm-front` / `dm-front-dev` | prod / dev | Next.js standalone (prod) or `bun dev` (dev, hot-reload), port `FRONT_PORT` |

Key point: **ports and hosts are set once in the root `.env`** and spread into the Dockerfiles (build-arg `PORT`/`BACKEND_PORT`), port publishing, healthchecks, and internal URLs. All mandatory (`:?` in compose — fails with a clear error instead of staying silent). `BACKEND_INTERNAL_URL` is derived from the service name + `BACKEND_PORT` (e.g. `http://back-dev:8001` in dev).

## Environment variables (root `.env`, single source)

Mandatory:
- `BACKEND_PORT` (8001), `FRONT_PORT` (5001) — single sources for ports; `BACKEND_HOST`/`BACKEND_HOST_DEV` (`back`/`back-dev`) name the services in the compose networks.
- `AUTH_APP_ORIGIN` — the app host used to derive CORS origins (inherited from the ReportCollector convention); in DocMasker the browser hits only the front origin, so it is mostly a placeholder.

GLiNER:
- `GLINER_MODEL` (default `urchade/gliner_multi_pii-v1`) — HuggingFace PII model.
- `GLINER_THRESHOLD` (default `0.35`) — confidence for entity detection.
- `GLINER_LABELS` (pipe-separated, optional) — overrides `back/src/masking/constants.py::DEFAULT_GLINER_LABELS`.

## Masking pipeline (`back/src/masking/`)

- `service.py` — loads the model (`GLiNER.from_pretrained`), splits long text into overlapping windows aligned to `config.max_len` (`prepare_batch` → token/char maps), runs `predict_entities`, deduplicates spans, and replaces each span with `[LABEL]` via the canonical back-to-front recipe.
- `schemas.py` — `MaskRequest{text}`, `MaskResponse{text, masked_entities}`.
- `router.py` — `POST /api/mask` (text) and `POST /api/mask-file` (multipart DOCX → binary response).
- `docx_service.py` — DOCX masking that **preserves formatting**: iterates every paragraph (body, all table cells recursively, default header/footer of each section) via `iter_paragraphs`, runs `detect_entities` on each paragraph's joined text, and rewrites only the overlapping slices of the XML runs (`paragraph.runs`) back-to-front. Entities spanning multiple runs collapse into a single `[LABEL]`; surrounding formatting (bold, size, styles) is untouched.
- The model is loaded once in the FastAPI `lifespan` (single process). `get_gliner_model()` caches it; tests stub it via `dependency_overrides`.

Default labels: `person, phone, email, address, organization, location, passport, inn, snils, date of birth`.

## API (back, prefix `/api`)

- `POST /mask` — body `{"text": "..."}` → `{"text": "<masked>", "masked_entities": [{label, text, start, end, score}]}`. 422 for empty/whitespace text; 500 for inference failure.
- `POST /mask-file` — multipart field `file` (.docx) → the masked `.docx` as the response body (`Content-Disposition: attachment`, `application/vnd...docx`), with the masked-entity count in the `X-Masked-Count` header. 422 for non-`.docx` or empty files.
- `GET /health` — readiness probe used by Docker healthcheck; `ok` only after the model is loaded.

Errors carry a Russian `detail`; the front shows it via the `masker-actions__error` status line.

## Front (`front/src/`)

- `app/page.tsx` — client component: two panes. Left pane holds either the input `TextArea` or (when a file is selected) a live `DocxViewer` render of the file, with a full-width `ButtonGroup` («Выбрать файл» — tertiary — / «Замаскировать») underneath. Right pane shows the masked result: a `TextArea` for text mode, or a `DocxViewer` of the masked document plus a «Скачать файл» button in file mode (the browser prompts the save only on that click). The «Замаскировать» button is disabled until exactly one input (text or file) is present, and all controls are disabled while masking runs. The masked-entity count is surfaced next to the docs preview / the text status line; the bottom stats panel is not built yet.
- `shared/ui/docx-viewer.tsx` — client component that renders a `.docx` Blob into a scrollable pane via `docx-preview`'s `renderAsync`. Used for both the selected source file and the masked result.
- `app/api/mask/route.ts` — server route that validates JSON and calls `proxyMaskRequest`.
- `app/api/mask-file/route.ts` — server route that replays the multipart upload to the back and returns the binary plus `X-Masked-Count` / `Content-Disposition`.
- `shared/api/mask.ts` — `proxyMaskRequest`: forwards to `BACKEND_INTERNAL_URL/api/mask`, passes the body/status back verbatim.
- `shared/config.ts` — `getBackendInternalUrl()` (throws if unset).

The masked file is named `<original> (замаскированный).docx` — the back advertises it via an RFC 5987 `filename*=UTF-8''...` (percent-encoded) value in `Content-Disposition`, which the page decodes (`parseDispositionFilename`) so Cyrillic names survive.

The header brand mirrors ReportCollector: the `ruschem_icon.png` icon (also the favicon/apple-touch icon) next to «ДокМаскер» in the shared brand styling.

## Deployment (prod)

`just prod` builds the prod-profile images and starts `dm-back` + `dm-front`. The front publishes `FRONT_PORT`, the back `BACKEND_PORT`. On first boot the back downloads the GLiNER model, cached in the `dm-hf-cache` volume — the equivalent of the uv/bun layer cache. Restarts and `down`+`up` reuse it; only `docker compose down --volumes` removes it and re-downloads.
