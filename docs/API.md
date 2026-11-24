# HTTP API

The single source for the DocMasker HTTP contract. Implementation:
`back/src/main.py` (app + `GET /health`), `back/src/masking/router.py` (sync
mask/unmask), `back/src/jobs/api.py` (`/api/jobs/*`), `back/src/masking/schemas.py`
and `back/src/jobs/schemas.py` (Pydantic models). All `/api/*` routes are
mounted under one `APIRouter(prefix="/api")` (`back/src/api.py`).

The **browser never calls the backend directly**: `front/src/app/api/*/route.ts`
proxies each path server-side to `BACKEND_INTERNAL_URL`. CORS is enabled on the
backend only for direct dev access (curl/tools); there is no browser CORS hop.
Errors are `{"detail": "<Russian message>"}` — user-facing strings stay Russian.

Interactive OpenAPI docs: `/docs`, `/redoc`, `/openapi.json` (FastAPI defaults).
Generating client types from that schema is a deferred item (`TODO.md`), so the
shapes below are currently duplicated by hand in `front/src/shared/api/`.

## Conventions

| Item | Rule |
|---|---|
| Content types | JSON bodies for text endpoints; `multipart/form-data` for file endpoints; `application/zip` for all file responses |
| Filenames | RFC 5987 `Content-Disposition`: ASCII fallback + `filename*=UTF-8''…` for Cyrillic names |
| Errors | Russian `detail`; statuses below are the only ones the client must handle |
| PII | Raw entity values/offsets never leave the API in JSON (`stats` is aggregate-only); keys files are the only carrier of originals |
| Caching | `/api/jobs/{id}/result` sends `Cache-Control: no-store`; proxies use `cache: "no-store"` |

### Status codes

| Code | Meaning | Where |
|---|---|---|
| `200` | success | all |
| `202` | accepted as a durable job (`JobAccepted`) | oversized sync input, `POST /api/jobs` |
| `204` | cancelled | `DELETE /api/jobs/{id}` |
| `400` | malformed JSON body / Pydantic validation | any JSON endpoint (FastAPI default) |
| `404` | unknown job id or expired result artifact | `/api/jobs/{id}`, `/result` |
| `409` | job not ready yet | `/api/jobs/{id}/result` |
| `413` | text/keys over `MAX_TEXT_CHARS`/`MAX_KEYS_CHARS`, or upload size/entry/zip-bomb limits | text + file endpoints |
| `415` | malformed / non-Word / encrypted OPC-ZIP package | file endpoints |
| `422` | empty text, invalid keys YAML, unknown profile, wrong file extension | text + file endpoints |
| `429` | bounded sync executor or job queue full | sync endpoints, `POST /api/jobs` |

DOCX uploads are validated as OPC/ZIP packages (`docx_validation.py`): entry
count, uncompressed size, compression ratio (zip-bomb), encryption. Limits come
from `.env` (`UPLOAD_MAX_MB`, `MAX_TEXT_CHARS`, `MAX_KEYS_CHARS`). The frontend
is **not** a trust boundary — the backend enforces everything.

## `GET /health`

Liveness probe used by the Docker healthcheck. `{"status": "ok"}`, always 200 —
the masker is deterministic, there is no model to load or warm up. Splitting
liveness/readiness is deferred to stage 6.

## `GET /api/profiles`

Read-only profile registry (ADR-0001). No mutation endpoint; profiles are
code-configured. Response (`ProfilesResponse`):

```json
{
  "profiles": [
    {
      "profile_id": "russian-default",
      "version": 1,
      "display_name": "Русский по умолчанию",
      "labels": [
        {"id": "person", "title": "Физлица", "order": 0}
      ]
    }
  ]
}
```

The built-in profile reports labels `person, phone, email, link, address,
organization` with Russian section titles (keys-file order) `Физлица, Номера
телефонов, Email, Ссылки, Организации, Адреса`. Mask endpoints accept an
optional `profile_id` (omitted → `russian-default`). Setting `MASK_LABELS` in
the environment now fails backend startup with a Russian error.

## `POST /api/mask`

Body (`MaskRequest`): `{"text": "...", "profile_id": "…"}` (`profile_id`
optional). Response (`MaskResponse`, 200):

```json
{
  "text": "…[PERSON_1]…",
  "stats": {
    "total": 2,
    "occurrences_by_label": {"person": 1, "phone": 1},
    "unique_by_label": {"person": 1, "phone": 1}
  },
  "keys": "Профиль: russian-default\n…"
}
```

- Every entity becomes a numbered `[LABEL_N]`; the same exact string repeated
  reuses one number and one key. Numbers are allocated left-to-right before the
  back-to-front rewrite, so they follow document order.
- `stats` is **aggregates only** — no raw values/offsets (F-09).
- `keys` is a YAML v2 string (empty when nothing was masked). See
  `docs/TEMPLATES.md`.
- Empty/whitespace text → 422; over `MAX_TEXT_CHARS` → 413; unknown profile →
  422. Over `SYNC_MAX_TEXT_CHARS` → `202 + JobAccepted` (below).

## `POST /api/unmask`

Body (`UnmaskRequest`): `{"text": "<masked>", "keys": "<YAML>"}`.
Response (`UnmaskResponse`, 200): `{"text": "<restored>"}`.

Restoration is a plain placeholder→value `str.replace` per `[LABEL_N]`
(self-delimiting, order-independent). Unknown placeholders stay as-is. Keys are
validated by `validate_keys_yaml` first (see `docs/TEMPLATES.md`): invalid →
422 with a Russian `detail`. Oversized text/keys → 413. Over
`SYNC_MAX_TEXT_CHARS` → `202 + JobAccepted`.

## `POST /api/mask-file`

Multipart: `file` (`.docx`) + optional `profile_id` form field. Response is a
**ZIP** (200, `application/zip`, `Content-Disposition: attachment`) — not a bare
`.docx` — containing:

| Entry | Content |
|---|---|
| `<base> (замаскированный).docx` | masked document, formatting preserved |
| `<base> (ключи демаскировки).yaml` | demasking keys (v2) |

Response headers:

| Header | Value |
|---|---|
| `X-Masked-Count` | total masked occurrences |
| `X-Masked-Breakdown` | per-label JSON, e.g. `{"person":2,"phone":1}` |
| `X-Docx-Unsupported-Parts` | optional comma-separated safe codes (footnotes, comments, …) when the package has OOXML parts we don't mask — see `docs/DOCX_SUPPORT.md` |

The frontend unpacks the ZIP (`front/src/shared/api/mask-archive.ts`, jszip) and
offers the two files as **separate downloads**; the user never sees the archive.
Non-`.docx`/empty → 422; malformed/oversized/encrypted DOCX → 415/413. Over
`SYNC_MAX_DOCX_BYTES` or `SYNC_MAX_DOCX_PARAGRAPHS` → `202 + JobAccepted`.

## `POST /api/unmask-file`

Multipart: `file` (masked `.docx`) + `keys` (YAML as a form field). Response is
a **ZIP** (200, `application/zip`):

| Entry | Content |
|---|---|
| `<base> (демаскированный).docx` | restored document |
| `<base> (отчёт о демаскировке).json` | `{"unused_keys": [...]}` |

The `<base>` has an optional ` (замаскированный)` suffix stripped so names don't
double up. The manifest replaced the former `X-Unused-Keys` header (F-05): PII
values no longer ride in transport metadata and no header-size limit can
truncate them. `X-Docx-Unsupported-Parts` may also be present. Invalid keys →
422; file validation → 415/413 as above. Over the sync thresholds →
`202 + JobAccepted`.

## Durable jobs (`/api/jobs`, ADR-0002)

The four sync endpoints **transparently return 202** when the input exceeds the
sync thresholds (`SYNC_MAX_TEXT_CHARS=20000`, `SYNC_MAX_DOCX_BYTES=1048576`,
`SYNC_MAX_DOCX_PARAGRAPHS=200`; all configurable in `.env`). The front then
polls and downloads the result with the *same* result panes/download UX.

### `POST /api/jobs`

`multipart/form-data` (also used directly by the UI for large inputs):

| Field | Meaning |
|---|---|
| `kind` | `mask_text` \| `mask_file` \| `unmask_text` \| `unmask_file` |
| `text` | input for `*_text` kinds (UTF-8) |
| `file` | `.docx` for `*_file` kinds |
| `keys` | demasking keys YAML for `unmask_*` kinds |
| `profile_id` | optional masking profile |
| `filename` | original file name for the result archive |

Response 202 (`JobAccepted`):

```json
{
  "job_id": "<opaque uuid4>",
  "status": "queued",
  "status_url": "/api/jobs/<id>",
  "result_url": "/api/jobs/<id>/result"
}
```

Inputs are spooled to the private `jobs-data` volume under the opaque id; only
metadata goes into PostgreSQL (authoritative job state). No PII/keys in DB rows, logs or headers. Bounded
queue (`JOBS_MAX_ACTIVE`) → 429. Opaque ids are the IDOR guard in this
single-tenant/private deployment (no auth — see `TODO.md`).

### `GET /api/jobs/{id}`

`JobStatusResponse` (200):

```json
{
  "job_id": "…",
  "status": "queued|running|succeeded|failed|cancelled|expired",
  "phase": "queued|validation|detection|rewrite|archive|completed",
  "progress": 0,
  "attempt": 1,
  "error_code": null,
  "result_url": "/api/jobs/<id>/result"
}
```

`phase`/`progress` are coarse phase indicators, **not** a percent-accurate
widget (explicitly out of scope per maintainer decision). `error_code` is a safe
code — never exception text or input values. Unknown id → 404. There is no PII
in any status field. `front/src/shared/api/jobs.ts::waitForJob` polls
`status_url` with backoff (bounded attempts, `AbortSignal`), then fetches the
result.

### `GET /api/jobs/{id}/result`

One **flat ZIP** (200, `application/zip`, `Cache-Control: no-store`):

| Job kind | Entries |
|---|---|
| `mask_text` / `unmask_text` | `result.json` — the same JSON shape as `/api/mask` / `/api/unmask` |
| `mask_file` | masked `.docx` + keys `.yaml` |
| `unmask_file` | restored `.docx` + manifest `.json` |

409 while the job is not `succeeded`; 404 for an unknown id or when the result
artifact expired (TTL `JOBS_TTL_SECONDS`). The front unpacks `result.json` via
`jobToMaskResponse`/`jobToUnmaskResponse` and the file ZIPs via
`jobBundle`/`jobRestoreBundle` (`front/src/shared/api/mask-helpers.ts`).

### `DELETE /api/jobs/{id}`

Request cancellation of a queued/running job → 204. Unknown id → 404.

## Frontend proxy contract

| Browser path | Next route | Backend path | Timeout |
|---|---|---|---|
| `/api/mask` | `app/api/mask/route.ts` | `/api/mask` | 15 s |
| `/api/mask-file` | `app/api/mask-file/route.ts` | `/api/mask-file` | 10 min |
| `/api/unmask` | `app/api/unmask/route.ts` | `/api/unmask` | 15 s |
| `/api/unmask-file` | `app/api/unmask-file/route.ts` | `/api/unmask-file` | 10 min |
| `/api/jobs/*` | `app/api/jobs/[...path]/route.ts` | `/api/jobs/*` | 15 s (polling) |
| `/api/health` | `app/api/health/route.ts` | front-local | — |

`front/src/instrumentation.ts` installs a global undici `Agent` with
`headersTimeout: 60_000` / `bodyTimeout: 600_000`; `shared/api/proxy.ts`
streams responses through `new Response(response.body)` (no `arrayBuffer` copy)
with a per-route `AbortSignal.timeout`. Long work never depends on a held-open
HTTP connection — it goes through `/api/jobs`.
