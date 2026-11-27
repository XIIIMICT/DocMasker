# HTTP API

The single source for the DocMasker HTTP contract. Implementation:
`back/src/main.py` (app + `GET /health`), `back/src/masking/router.py` (sync
mask/unmask), `back/src/jobs/api.py` (`/api/jobs/*`), `back/src/masking/schemas.py`
and `back/src/jobs/schemas.py` (Pydantic models), `back/src/errors.py` (sanitized
error envelope), `back/src/openapi_export.py` (deterministic schema export).
All `/api/*` routes are mounted under one `APIRouter(prefix="/api")`
(`back/src/api.py`).

The **browser never calls the backend directly**: `front/src/app/api/*/route.ts`
proxies each path server-side to `BACKEND_INTERNAL_URL`. CORS is enabled on the
backend only for direct dev access (curl/tools); there is no browser CORS hop.

Interactive OpenAPI docs: `/docs`, `/redoc`, `/openapi.json` (FastAPI defaults).
The schema is an **explicit contract**: response models and status codes are
declared per route instead of inferred, binary responses are `application/zip`
with `{"type": "string", "format": "binary"}`, and every `/api/*` error is the
sanitized `ApiErrorResponse` envelope. Compile-time HTTP DTOs are generated from
it into `front/src/shared/api/generated/openapi.d.ts`; see §"OpenAPI contract"
below. Generated types provide **no runtime validation**.

## Conventions

| Item | Rule |
|---|---|
| Content types | JSON bodies for text endpoints; `multipart/form-data` for file endpoints; `application/zip` for all file responses |
| Filenames | RFC 5987 `Content-Disposition`: ASCII fallback + `filename*=UTF-8''…` for Cyrillic names |
| Errors | one stable envelope (below): Russian user-facing `detail` + machine `code` + `request_id` |
| PII | Raw entity values/offsets never leave the API in JSON (`stats` is aggregate-only); keys files are the only carrier of originals. Error bodies never contain tracebacks, paths, DB messages, submitted PII or keys |
| Caching | Every `/api/*` response sends `Cache-Control: no-store` and `Pragma: no-cache` (ASGI middleware); per-route `Content-Disposition`/`Content-Type` are preserved. Proxies use `cache: "no-store"` and forward both headers |

### Stable error contract (Phase 6, §8.1)

Every error response (validation, domain, not-found, conflict, queue-full,
storage and unexpected 500) has the same shape and carries an `X-Request-ID`
header with the same value:

```json
{
  "code": "invalid_keys",
  "detail": "Файл ключей не является корректным YAML.",
  "request_id": "3f9a…"
}
```

A typed `queue_full` 429 additionally carries `Retry-After: <seconds>` (integer,
rounded up from `JOBS_RETRY_AFTER_SECONDS`). The envelope, the localized Russian
`detail`, `request_id` and `X-Request-ID` are unchanged; no other error status
receives this header. The Next proxies forward it verbatim. There is no
server-side automatic retry: the client resubmits after the hint.

`code` is stable and machine-readable (`empty_text`, `text_too_large`,
`invalid_keys`, `invalid_extension`, `invalid_package`, `encrypted_package`,
`zip_bomb`, `unsafe_xml`, `executor_busy`, `queue_full`, `result_not_ready`,
`result_expired`, `job_not_found`, `validation_error`, `bad_request`,
`not_found`, `conflict`, `payload_too_large`, `unsupported_media_type`,
`unprocessable`, `artifact_storage_unavailable`, `internal_error`). `detail`
stays Russian. Handler is `back/src/errors.py::install_error_handlers`; it logs
only exception *types*, never messages. The Next proxies forward `X-Request-ID`
verbatim. OpenAPI
declares this envelope (model `ApiErrorResponse`) for every non-2xx `/api/*`
response, including sanitized 422 validation errors.

### OpenAPI contract and generated types (§8.2)

`app.openapi()` is the single machine-readable contract. The deterministic
in-process export lives in `back/src/openapi_export.py` (no HTTP server, no DB);
the pinned, dev-only generator `swagger-typescript-api@13.15.0` emits types only
(`front/src/shared/api/generated/openapi.d.ts`) — no client SDK, no runtime
dependency. Regenerate and check freshness from the repo root:

```sh
bun run --cwd front gen:openapi
bun run --cwd front gen:openapi --check
```

The second command is the CI freshness gate (job `openapi-contract`). Generated
declarations are **compile-time only**; they do not validate responses at
runtime. PII-bearing internal models (`MaskedEntity`, `JobSubmitText`,
`JobKindInfo`) and FastAPI's `HTTPValidationError` are not in the schema.
Non-HTTP contracts are explicitly *not* covered: the ZIP-internal
`manifest.json`, the demasking keys YAML format, the `labels.yaml` →
`labels.generated.ts` catalogue, and the Next.js proxy routes. The client-only
`JobResult` (unpacked ZIP view) also stays handwritten in `jobs-types.ts`.

### Status codes

| Code | Meaning | Where |
|---|---|---|
| `200` | success | all |
| `202` | accepted as a durable job (`JobAccepted`) | oversized sync input, `POST /api/jobs` |
| `204` | cancelled (no response body) | `DELETE /api/jobs/{id}` |
| `404` | unknown job id or expired result artifact | `/api/jobs/{id}`, `/result` |
| `409` | job not ready yet | `/api/jobs/{id}/result` |
| `413` | text/keys over `MAX_TEXT_CHARS`/`MAX_KEYS_CHARS`, or upload size/entry/zip-bomb limits | text + file endpoints |
| `415` | malformed / non-Word / encrypted OPC-ZIP package | file endpoints |
| `422` | request validation, empty text, invalid keys YAML, unknown job kind, wrong file extension | text + file + job endpoints |
| `429` | bounded sync executor or job queue full | sync endpoints, `POST /api/jobs` |
| `503` | artifact storage not ready (full/read-only volume): `artifact_storage_unavailable` | `POST /api/jobs`, oversized sync inputs redirected to jobs |

### Artifact storage readiness

Before a staging row or artifact is created, admission checks the private spool
(`JOBS_DIR`): a free-space floor `JOBS_MIN_FREE_BYTES` (default **1 GiB**) and one
small write/remove probe. The worker runs the same check at startup and exits
non-zero on failure. Both map every failure to the 503 envelope above with the
single code `artifact_storage_unavailable` and one of three Russian details
(uninspectable volume / insufficient free space / not writable). No job row,
partial artifact or ambiguous 500 is produced; worker-side transient job I/O
failures still use the existing bounded retry semantics. Tune the floor through
`JOBS_MIN_FREE_BYTES` on small volumes and keep the Prometheus alert threshold
in sync (see `docs/OPERATIONS.md` §"Self-monitoring and checks").

DOCX uploads are validated as OPC/ZIP packages (`docx_validation.py`):
actual streamed byte count (never Content-Length), entry count, uncompressed
size, compression ratio (zip-bomb), encryption, and hostile entry names
(absolute/drive/traversal/backslash/NUL/control/duplicate). Limits come from
`.env` (`UPLOAD_MAX_MB`, `MAX_TEXT_CHARS`, `MAX_KEYS_CHARS`) and apply to sync
routes and `POST /api/jobs` alike, before cache lookup or job staging. Output
ZIP entry names are flat and built from a shared sanitised filename stem. The
frontend is **not** a trust boundary — the backend enforces everything.

## `GET /health`

Liveness probe used by the Docker healthcheck. `{"status": "ok"}`, always 200 —
the masker is deterministic, there is no model to load or warm up. Carries
`X-Request-ID`.

## `GET /ready` (Phase 6, §10)

Readiness probe: verifies the PostgreSQL substrate (and that the immutable
masking config is loaded). `200 {"status": "ready", "labels": 6}` or
`503 {"status": "not_ready", "reason": "Хранилище заданий недоступно."}`.
A DB failure logs only the exception type — no DSN, path, PII or keys. The
full readiness/liveness rollout (compose/CI healthchecks, front readiness)
remains part of the deferred observability stage.

## `GET /metrics` (Phase 6, §10)

Coarse counters only, never PII/keys/job ids/cache keys:

```json
{"active_jobs": 0, "cache": {"hits": 3, "misses": 1, "writes": 1, "failures": 0, "evictions": 0, "gc_candidates": 0, "gc_deleted": 0, "gc_reclaimed_bytes": 0, "gc_failures": 0}}
```

A PostgreSQL outage degrades to `active_jobs: null` instead of a 500; a cache
outage yields zeroed cache counters. The `gc_*` fields are safe aggregate counts
from the bounded cache-artifact collection pass (`docs/OPERATIONS.md` §"Cache
artifact GC"). Full metrics/latency/structured logs remain deferred (TODO stage 6).

## Proxy timeout policy (canonical: `front/src/shared/api/timeouts.ts`)

| Route class | Timeout | Where |
|---|---|---|
| short JSON `/api/mask`, `/api/unmask`, `/api/labels` | 15 s | `JSON_TIMEOUT_MS` |
| job submit / status / cancel | 15 s | `JOB_TIMEOUT_MS` |
| multipart upload forwarding | 120 s | `UPLOAD_TIMEOUT_MS` |
| result download/streaming | 300 s | `RESULT_TIMEOUT_MS` |
| overall client job wait (workflow, not a request) | 20 min | `JOB_WAIT_TIMEOUT_MS` |

The global undici dispatcher is a finite outer bound only
(`headersTimeout: 60 s`, `bodyTimeout: 600 s`; never `0`/unbounded). Per-route
`AbortSignal.timeout` is combined with the incoming request's abort signal
(`AbortSignal.any`), so an aborted client upload does not leave a staged
artifact. `timeoutForRoute()` is unit-tested. Large operations become durable
jobs (202) instead of holding one HTTP request open.

### Streaming / buffering (Phase 6, §8.5/§10)

- Next file proxies forward the original multipart stream unchanged
  (`Content-Type` + boundary preserved, `duplex: "half"`), without
  `request.formData()` and a rebuilt `FormData`.
- Backend job submit streams the upload in 64 KiB chunks into a private temp
  file with a **real byte counter** (Content-Length is never trusted), validates
  the OPC package **from the staged path**, then streams it into the artifact
   the OPC package **from the staged path**, then either enqueues from that
   path (no full file in RAM) or materialises it only after the hard limits
   passed. Cleanup is exception/abort-safe on both file routes.
  `Path.read_bytes()` of a large ZIP. Sync file endpoints still build the small
  ZIP in memory because the OPC transformer needs the whole package — that
  part-level DOM memory is documented, not claimed as streaming.
- All DB/Valkey/ZIP/XML/filesystem calls in async routes run through
  `run_blocking` (separate bounded IO pool) or `run_cpu` (bounded CPU pool);
  the paragraph-count heuristic uses an incremental `lxml.iterparse` over
  `ZipExtFile` instead of reading the whole `word/document.xml`.

## `GET /api/labels`

The single canonical label catalogue (Phase 4). Read-only; loaded from
`back/config/masking/labels.yaml`, no `order` field (the sequence defines
keys-section order). Response (`LabelsResponse`):

```json
{
  "schema_version": 1,
  "labels": [
    {"id": "person", "title": "Физлица"},
    {"id": "phone", "title": "Номера телефонов"}
  ]
}
```

`GET /api/profiles` and `profile_id` were removed; setting `MASK_LABELS` in the
environment still fails backend startup. The frontend consumes a generated
copy of this catalogue (`gen:labels`, CI freshness check).

## `POST /api/mask`

Body (`MaskRequest`): `{"text": "..."}`. Response (`MaskResponse`, 200):

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
  `docs/MASKING_CONFIGURATION.md`.
- Empty/whitespace text → 422; over `MAX_TEXT_CHARS` → 413; unknown profile →
  422. Over `SYNC_MAX_TEXT_CHARS` → `202 + JobAccepted` (below).

## `POST /api/unmask`

Body (`UnmaskRequest`): `{"text": "<masked>", "keys": "<YAML>"}`.
Response (`UnmaskResponse`, 200): `{"text": "<restored>"}`.

Restoration is a plain placeholder→value `str.replace` per `[LABEL_N]`
(self-delimiting, order-independent). Unknown placeholders stay as-is. Keys are
validated by `validate_keys_yaml` first (see `docs/MASKING_CONFIGURATION.md`): invalid →
422 with a Russian `detail`. Oversized text/keys → 413. Over
`SYNC_MAX_TEXT_CHARS` → `202 + JobAccepted`.

## `POST /api/mask-file`

Multipart: `file` (`.docx`). Response is a **flat ZIP** (200,
`application/zip`, `Content-Disposition: attachment`, `Cache-Control: no-store`)
— not a bare `.docx` — containing the same entries for sync and job paths (§7):

| Entry | Content |
|---|---|
| `<base> (замаскированный).docx` | masked document, formatting preserved |
| `<base> (ключи демаскировки).yaml` | demasking keys |
| `manifest.json` | versioned result manifest (statistics, coverage, versions) |

There are **no semantic `X-Masked-*` / `X-Docx-*` headers**: all metadata
lives in `manifest.json`. The frontend requires and validates the manifest.
Genuinely unprocessable content (embedded OLE/binary objects, unsafe XML) is a
strict 422 — no best-effort warning. Non-`.docx`/empty → 422;
malformed/oversized/encrypted DOCX → 415/413. Over `SYNC_MAX_DOCX_BYTES` or
`SYNC_MAX_DOCX_PARAGRAPHS` → `202 + JobAccepted`.

## `POST /api/unmask-file`

Multipart: `file` (masked `.docx`) + `keys` (YAML as a form field). Response is
a **flat ZIP** (200, `application/zip`, `Cache-Control: no-store`):

| Entry | Content |
|---|---|
| `<base> (демаскированный).docx` | restored document |
| `manifest.json` | versioned manifest with `statistics.unused_keys` |

The `<base>` has an optional ` (замаскированный)` suffix stripped so names don't
double up. PII values never ride in headers (`X-Unused-Keys` was removed in
F-05). Invalid keys → 422; file validation → 415/413 as above. Over the sync
thresholds → `202 + JobAccepted`.

### `manifest.json` (canonical, `schema_version: 1`)

```json
{
  "schema_version": 1,
  "operation": "mask_file",
  "status": "succeeded",
  "complete": true,
  "masking_ruleset_version": 1,
  "exception_config_digest": "...",
  "input": { "sha256": "...", "size_bytes": 12345 },
  "output": { "document_name": "...", "keys_name": "..." },
  "statistics": {
    "total_occurrences": 5,
    "occurrences_by_label": { "person": 2 },
    "unique_by_label": { "person": 1 }
  },
  "coverage": {
    "processed_parts": ["main_document", "footnotes"],
    "processed": [
      { "name": "word/document.xml", "category": "main_document", "text_nodes": 3 }
    ],
    "unprocessed_text_parts": [],
    "rejected_parts": [],
    "raster_image_ocr": "not_performed"
  },
  "cache": { "hit": false, "namespace_version": 1 }
}
```

`unmask_file` reports `statistics.unused_keys` instead of occurrence counts.
`processed_parts` includes every inspected supported part even with zero
detections; `complete` is true only when the supported scope was fully
inspected and no text-bearing part was unprocessed/rejected — such documents
are rejected with 422 rather than delivered as a misleading success. The
frontend rejects a missing/malformed/wrong-version manifest with a clear
error. `cache.hit` describes **the request currently served**: a cache hit
returns a freshly built ZIP whose manifest says `true`, while the immutable
content-addressed cached artifact keeps its original `false` and is never
mutated in place. This is identical on sync file routes and job results.
See `docs/DOCX_SUPPORT.md`.

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
  "status": "staging|queued|running|cancelling|succeeded|failed|cancelled|expired",
  "phase": "queued|validation|detection|rewrite|archive|completed|cancelled",
  "progress": 0,
  "attempt": 1,
  "error_code": null,
  "cancel_requested": false,
  "created_at": 1795500000.0,
  "finished_at": null,
  "result_url": "/api/jobs/<id>/result"
}
```

`cancel_requested` is true after `DELETE` on a running job (status
`cancelling` until the worker acknowledges it at a checkpoint). `phase`/`progress` are coarse phase indicators, **not** a percent-accurate
widget (explicitly out of scope per maintainer decision). `error_code` is a safe
code — never exception text or input values. Unknown id → 404. There is no PII
in any status field. `front/src/shared/api/jobs.ts::waitForJob` polls
`status_url` with backoff (bounded attempts, `AbortSignal`), then fetches the
result.

### `GET /api/jobs/{id}/result`

One **flat ZIP** (200, `application/zip`, `Cache-Control: no-store`):

| Job kind | Entries |
|---|---|
| `mask_text` / `unmask_text` | `result.json` — the **exact** canonical `MaskResponse` / `UnmaskResponse` (ZIP wrapping is transport only) |
| `mask_file` | masked `.docx` + keys `.yaml` + `manifest.json` (same as sync) |
| `unmask_file` | restored `.docx` + `manifest.json` (same as sync) |

409 while the job is not `succeeded`; 404 for an unknown id or when the result
artifact expired (TTL `JOBS_TTL_SECONDS`). The front unpacks `result.json` via
`jobToMaskResponse`/`jobToUnmaskResponse` and the file ZIPs via
`jobBundle`/`jobRestoreBundle` (`front/src/shared/api/mask-helpers.ts`).

### `DELETE /api/jobs/{id}`

Request cancellation of a queued/running job → 204. Unknown id → 404; a job
that already reached a terminal state returns 409/404 (the cancellation was
not accepted). The client must check the HTTP status: only a 2xx counts as
confirmed, a network error or 5xx leaves the job active and polling running.
Aborting local polling (`AbortController`) never cancels the server job.
Worker cancellation is cooperative at window/part checkpoints and is fenced;
a lease loss is reported separately and never triggers stale-owner cleanup.

## Frontend proxy contract

| Browser path | Next route | Backend path | Timeout |
|---|---|---|---|
| `/api/mask` | `app/api/mask/route.ts` | `/api/mask` | 15 s |
| `/api/mask-file` | `app/api/mask-file/route.ts` | `/api/mask-file` | 120 s (streamed upload) |
| `/api/unmask` | `app/api/unmask/route.ts` | `/api/unmask` | 15 s |
| `/api/unmask-file` | `app/api/unmask-file/route.ts` | `/api/unmask-file` | 120 s (streamed upload) |
| `/api/jobs/*` | `app/api/jobs/[...path]/route.ts` | `/api/jobs/*` | 15 s submit/status/cancel, 300 s result download |
| `/api/health` | `app/api/health/route.ts` | front-local | — |

`front/src/instrumentation.ts` installs a global undici `Agent` with
`headersTimeout: 60_000` / `bodyTimeout: 600_000` as a finite outer net only.
The canonical map is `front/src/shared/api/timeouts.ts` (see the timeout table
earlier in this document); `shared/api/proxy.ts::postToBackend` combines it with
the incoming request's abort signal. Long work never depends on a held-open
HTTP connection — it goes through `/api/jobs`.
