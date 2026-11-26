# Security

This is the canonical home for DocMasker's deployment boundary, PII handling,
cache/artifact security and input-threat assumptions. Hot-path rules live in
`back/AGENTS.md`, `back/src/jobs/AGENTS.md`, `back/src/masking/AGENTS.md`,
`front/AGENTS.md` and `infra/AGENTS.md`; the HTTP contract lives in
`docs/API.md`. Deferred hardening is tracked in `TODO.md`. Operational
procedures (backup, probes, runbooks) live in `docs/OPERATIONS.md`.

## 1. Deployment boundary: private, single-tenant, no application auth

- **No authentication, authorization, tenants, SSO, user accounts or
  user-level quotas are implemented.** This is an explicit maintainer decision
  (Implementation Brief §0.11 and §11; `TODO.md`, "Blocked / external — Auth"). The service is
  intended for a trusted, private deployment only. Do not describe it as
  authenticated, multi-tenant or safe to expose to the public Internet.
- **The reverse proxy / network perimeter is the trust boundary.** A concrete
  reverse-proxy, TLS and auth example is still deferred (`TODO.md`, stage 6);
  the repository does not ship one. Whoever can reach the front origin can use
  the service and read any job result.
- **Job IDs are bearer capabilities, not authentication.** A `uuid4` job id
  grants access to that job's status and result by design (Brief §3.11;
  `docs/API.md` §Durable jobs). There is no per-user ownership check — the
  opaque id is the IDOR guard in this single-tenant deployment, not a credential.
  The worker logs opaque ids for correlation, so operational logs must stay
  access-controlled; the brief recommends avoiding ids in public logs where
  practical. The browser keeps only the active id in
  `sessionStorage["docmasker.activeJob"]`, never input data or keys
  (`front/AGENTS.md` §Job Cancel/resume).
- **Network layout:** the browser calls only its own origin; Next.js proxies
  `/api/*` server-side to `BACKEND_INTERNAL_URL` (`docs/API.md`, `front/AGENTS.md`).
  In prod the backend publishes only on loopback
  (`compose.yaml`: `127.0.0.1:${BACKEND_PORT}:${BACKEND_PORT}`) so it cannot be
  reached from the network. PostgreSQL and Valkey stay on private Compose
  networks; PostgreSQL exposes a loopback-only host port solely for host-side
  `just seed`/migrations (`infra/compose.db.yaml`), and CI publishes no data
  service ports (`infra/compose.ci.yaml`).
- Backend CORS is enabled only to allow direct dev access (curl/tools); there
  is no browser CORS hop (`docs/API.md`, `back/src/main.py`).
- **Out of scope by decision:** user-editable profiles/regex execution, OCR,
  clustering/Kubernetes, and an ML-based detector (Brief §0). Adding auth or
  multi-tenancy is a separate design decision, not a configuration tweak.

## 2. PII handling

Original sensitive values have exactly one carrier: the **keys YAML that the
user downloads** from the result ZIP. Masked text, DOCX parts and `manifest.json`
carry placeholders, aggregates and hashes only.

| Surface | Rule | Where / evidence |
|---|---|---|
| API responses | `stats` is aggregate-only; raw values and offsets never leave JSON. No semantic `X-Masked-*` / `X-Unused-Keys` headers; statistics live in `manifest.json` | `docs/API.md`; `back/src/masking/schemas.py`; `back/src/masking/ooxml/manifest.py` |
| Error bodies | `{code, detail, request_id}` + `X-Request-ID`. `detail` is a fixed Russian phrase; never a traceback, path, DB message, submitted value or keys text | `back/src/errors.py`; `docs/API.md` §Stable error contract; `test_errors.py::test_every_error_has_code_detail_request_id_and_header`, `::test_request_validation_error_does_not_echo_submitted_pii` |
| PostgreSQL rows | Opaque artifact ids and a safe `error_code` only — no document text, keys, entity values, filenames or raw exception text | ADR-0003 §Schema; `back/src/jobs/schema.py`; `test_jobs_store.py::test_no_pii_columns_in_schema` |
| Logs | The app logs exception **types**, not exception messages; startup logs a label count and a short config digest. Opaque ids/codes may appear in diagnostics, never input text | `back/src/errors.py`; `back/src/main.py`; `back/src/jobs/worker.py` |
| `/metrics` | Coarse counters (`active_jobs`, cache hits/misses/writes/failures/evictions) — no ids, keys, inputs or key names | `back/src/main.py`; `docs/API.md` §`GET /metrics`; `test_readiness.py::test_metrics_are_coarse_and_db_outage_degrades` |
| Valkey key names | HMAC-SHA256 over a canonical fingerprint; no raw source hashes or PII in the key string | `back/src/jobs/cache.py::build_cache_key`; `test_cache.py::test_key_does_not_leak_source_hash` |
| Test data | Synthetic fixtures only; no real PII is committed | `test_ooxml.py` ("No real PII is used"), `back/config/masking/exceptions.example.yaml` ("Synthetic examples only") |

Result responses use `Cache-Control: no-store` (explicitly verified for
`/api/jobs/{id}/result` by `test_jobs_api.py::test_result_serves_zip_with_no_store`);
extending `no-store` to every document/keys response is still a `TODO.md`
stage-6 item. Structured JSON logs with request/job/worker correlation and log
rotation/retention are also deferred there — the current plain-text logs must
still be treated as sensitive operational data.

## 3. Cache and artifact security

- **Versioned HMAC keys.** Valkey keys have the shape
  `docmasker:result:v{CACHE_NAMESPACE_VERSION}:{operation}:{HMAC-SHA256}`, where
  the HMAC payload is a canonical JSON fingerprint of the operation, exact
  input/keys hashes, normalized output name, masking ruleset version, config
  digest, result-manifest version, keys-format version, DOCX-transformer version
  and engine/entry/key-schema versions (`back/src/jobs/cache.py`, ADR-0003 §Phase 3
  implementation note). The canonical config snapshot digest is always in the
  payload: a manual `MASKING_CONFIG_DIGEST` can only add invalidation, never
  disable it. `CACHE_KEY_SECRET` must be stable across restarts
  (≥16 chars); rotating it deliberately invalidates the whole cache. Bump the
  version settings whenever output semantics change, or stale results will be
  served (`back/src/jobs/AGENTS.md`).
- **References, not payloads.** Valkey stores a compact artifact-reference
  entry (artifact id, content hash, size, content type, manifest version). Large
  ZIP/DOCX results stay in the private `cache/` spool on the `jobs-data` volume;
  hits are hash-verified and a missing/corrupt artifact becomes a miss plus
  eviction, never a failed request (`back/src/jobs/cache.py`,
  `test_cache.py::test_artifact_store_verifies_integrity`,
  `::test_corrupt_artifact_is_a_miss`, `::test_real_valkey_corrupt_entry_evicts`).
- **Private spool.** Job inputs/keys/results are id-keyed files
  (`<jobs_dir>/<job_id>.input|.keys|.result`) in the private `dm-jobs-data`
  volume, shared only by `back` and `worker`; paths are uuid4-validated (no
  traversal) and writes are atomic temp-file + `os.replace`
  (`back/src/jobs/storage.py`, `compose.yaml`, `infra/AGENTS.md`). Nothing is
  served from a public static directory; results are streamed with `FileResponse`.
- **Cache writes are post-fenced.** An entry is written only after the guarded
  `succeeded` transition; cancelled/failed jobs and validation errors never
  populate the cache, and a Valkey outage degrades to misses, never a 500
  (`back/src/jobs/AGENTS.md`,
  `test_jobs_worker.py::test_cancelled_job_never_populates_cache`,
  `test_cache.py::test_valkey_outage_degrades_to_misses`). Duplicate active
  submissions dedupe in PostgreSQL, not via an unfenced Valkey lock
  (`test_jobs_worker.py::test_identical_concurrent_submissions_attach_to_one_job`).

## 4. Input threat assumptions and limits

All uploaded content is hostile; the frontend is explicitly **not** a trust
boundary (`docs/API.md`, `docs/PRODUCTION_READINESS.md` F-04). The backend
enforces every limit.

- **DOCX uploads are OPC/ZIP packages, never trusted by extension.**
  `back/src/masking/docx_validation.py` checks required parts, entry count
   (10,000), total uncompressed bytes (10x `UPLOAD_MAX_MB`), compression ratio
   (>1,000 on a large entry = zip bomb), the ZIP encryption flag, and hostile
   entry names (absolute/drive/traversal/backslash/NUL/control/duplicate).
   Output ZIP entry names are flat and derived from a shared sanitised stem
   (`archives.py::safe_stem`), so a Windows-style upload filename cannot create
   a path in a Windows extractor. Tests: `test_docx_validation.py`,
   `test_archives.py`, `test_mask_file.py`, `test_jobs_api.py`.
  `test_oversized_uncompressed_content_is_rejected_with_413`) and
  `test_mask_file.py` (`test_mask_file_rejects_garbage_bytes_with_415`).
- **XML is parsed defensively.** `back/src/masking/ooxml/package.py` uses
  `defusedxml.lxml` with DTD/entities/network disabled and resolves parts by
  namespace URI, not literal prefixes. Embedded OLE/`*.bin` objects, unsafe or
  malformed XML and oversized parts fail closed with 422
  (`OoxmlPackageError`) rather than returning a partially masked document —
  no warning-only "best effort" path exists (`docs/DOCX_SUPPORT.md`,
  `test_ooxml.py::test_malformed_xml_is_rejected`,
  `::test_embedded_binary_object_is_rejected`).
- **Streaming bounds.** `back/src/uploads.py::stage_upload` counts real bytes
  in 64 KiB chunks (Content-Length is never trusted), validates from the staged
  path and cleans up on error/abort; text/keys limits (`MAX_TEXT_CHARS`,
  `MAX_KEYS_CHARS`) return 413 (`test_uploads.py`, `docs/API.md` §Streaming).
- **Raster image pixels are not inspected** (no OCR): they are copied
  byte-for-byte and reported as `raster_image_ocr: not_performed`. This is a
  documented content limitation, not a claim of full image anonymization
  (`docs/DOCX_SUPPORT.md`, Brief §0).
- Request work is bounded by proxy timeouts (15 s JSON/job, 120 s upload, 300 s
  result, 20 min client job wait) and large work becomes a durable job; see
  `docs/API.md` §Proxy timeout policy. Rate limiting is not implemented yet
  (deferred to `TODO.md`, "Old stage 6 (deferred)").

## 5. Secrets and configuration management

- The single source of environment settings is the **root `.env`**, created
  from `.env.example` (`cp .env.example .env`) and gitignored; Compose loads it
  via `env_file` and fails loudly on missing required values (`:?`). Only
  obvious placeholders are committed (`.env.example`, `justfile`,
  `infra/AGENTS.md`).
- `back/config/masking/exceptions.yaml` holds real project exceptions, is
  gitignored, excluded from the image (`back/.dockerignore`) and mounted
  read-only into `back`/`worker` by `compose.yaml`. CI uses the synthetic
  `exceptions.example.yaml`. No real names/PII belong in either file. Validate
  without starting the app:
  `uv run --directory back python -m src.masking.validate_config`
  (`back/src/masking/AGENTS.md`).
- Configuration is one immutable startup snapshot with a non-reversible
  digest; invalid/duplicate/conflicting entries fail startup. There is no hot
  reload — restart to apply changes (`back/src/masking/AGENTS.md`).
- `CACHE_KEY_SECRET`, PostgreSQL credentials and the Valkey password are
  secrets: rotate them deliberately (rotation invalidates cached results) and
  never commit them. CI uses its own isolated credentials in
  `.github/workflows/ci.yaml`.
- Never commit debug artifacts or probe scripts with captured data
  (`AGENTS.md`, `docs/WORKFLOW.md`).

## 6. Known build-time TLS bypasses (accepted, not silently assumed safe)

Both bypasses are kept **by maintainer decision**, mirroring ReportCollector
(`docs/PRODUCTION_READINESS.md` F-06, `TODO.md`, `infra/AGENTS.md`):

- `back/Dockerfile` installs dependencies with
  `uv sync --allow-insecure-host pypi.org --allow-insecure-host files.pythonhosted.org`.
- `front/Dockerfile` sets `NODE_TLS_REJECT_UNAUTHORIZED=0` in its base/build
  stage. It is a build/dev setting: the production stage runs as the image's
  `node` user and does not inherit that variable.

These are workarounds for a corporate proxy, **not** a production TLS trust
decision. Replacing them with a trusted CA (`NODE_EXTRA_CA_CERTS` or the system
trust store, `UV_SYSTEM_CERTS=1` / `NODE_OPTIONS=--use-system-ca` already exported
by the `justfile` for host runs) is tracked in `TODO.md`, "Old stage 6 (deferred)".

## 7. Explicitly deferred security work

From `TODO.md`, "Old stage 6 (deferred)" and `docs/OPERATIONS.md` (do not claim these are done):

- reverse-proxy/TLS/auth example, upload limits, timeouts and rate limits at
  the proxy; auth/SSO for any non-private deployment;
- container hardening (`read_only`, `tmpfs`, `cap_drop`, `no-new-privileges`,
  `pids_limit`, resource limits), removal of the loopback backend port when a
  proxy exists;
- structured PII-safe logs with request/job/worker correlation, log rotation
  and retention; full metrics (latency, queue depth, lease losses, retries);
- wiring `/ready` into Compose/CI healthchecks and a front readiness route;
- extending `Cache-Control: no-store` to all document/keys responses;
- branch protection / required check `CI required` — a **GitHub-side setting**,
  never claim it is enabled from the repository (`docs/WORKFLOW.md`).
