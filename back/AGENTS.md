# back/ — backend agent guide

Hot-path landmines for the FastAPI/Granian service. The root `AGENTS.md` is
the short index; this file owns the backend details. Keep each fact in exactly
one home — architecture detail lives in `docs/ARCHITECTURE.md`.

## Stack & commands

- Python **3.15** (`uv`), FastAPI on Granian, `natasha` + `phonenumbers` +
  regex detectors, `python-docx`, `py-yaml12` keys. Backend image is Alpine,
  runs as `appuser` (non-root). Dev CMD adds `--reload-ignore-dirs jobs` so
  Granian does not watch the artifact spool.
- `uv run --directory back ruff check --fix` · `ruff format` · `ty check` ·
  `pytest -q`. Tests are offline/deterministic, `filterwarnings=error`,
  **≥80% branch coverage** enforced in CI. Test files: `test_rule_based.py`,
  `test_mask.py`, `test_mapping.py`, `test_keys_v2.py`, `test_config.py`,
  `test_labels_api.py`, `test_docx.py`, `test_mask_file.py`, `test_unmask.py`,
  `test_jobs_*.py`, `test_cache.py`, `test_executor.py`. Config validation CLI:
  `uv run --directory back python -m src.masking.validate_config`.

## Critical landmines

1. **`setuptools<81` is a hard runtime pin, not a lint convenience.**
   `natasha`→`pymorphy2` imports `pkg_resources` at runtime; on Python 3.15
   setuptools is not a transitive dep (same since 3.12), and setuptools ≥81
   removed `pkg_resources`. Without the pin the first `MorphVocab()` raises
   `ModuleNotFoundError`. It also emits a `UserWarning`, so
   `filterwarnings=error` needs the scoped ignore in `pyproject.toml`.
2. **`natasha`'s morphological dictionary lives in memory and is expensive to
   build.** `MorphVocab()` + extractors load the pymorphy2 dictionary on
   first construction, so the masker is cached (`get_masker`, `lru_cache`) —
   never build a `RuleBasedMasker` per request. Tests construct one per test
   on purpose (isolated, still fast). Phase 4 removed profiles: there is exactly
   one configuration. `get_masker()` is **one masker per thread** since Phase 7
   (`threading.local()`), not a process-wide `@cache`: natasha extractors hold
   mutable yargy state and are not proven thread-safe. Each thread pays ~110 MB
   once — keep `JOBS_CONCURRENCY` conservative.
3. **`natasha` extractors are not wired the way the docs imply.** In 1.6 the
   *multi-match* API is `extractor(text)` (calling the instance) —
   `extractor.find(text)` returns only the **first** `Match` (and `Match` is
   iterable, so a naive `for m in find(text)` silently unpacks into ints).
   There is no `natasha.OrganisationsExtractor`; `AddrExtractor` yields one
   match per component (`AddrPart`), not one whole address.
4. **`NamesExtractor` over-fires on lone tokens and on two common words.** It
   matches prepositions, single letters, bare surnames (`fact.last` only) and
   any two adjacent words as a "given name + surname" (`из Сторон`,
   `Оператор ЭДО`). `RuleBasedMasker` keeps a match only when the fact has a
   given name **and** a surname/patronymic **and** both tokens are plausible
   proper names (`_is_name_token`: capitalised *and* a `Name`/`Surn`/`Patr`
   grammeme in natasha's dictionary). natasha's dictionary is incomplete, so a
   three-part FIO** and a verified given name in any FIO relax the surname
   guard to `_surname_accepted` (a capitalised surname-shaped word that is not
   a role/greeting/топоним/common inanimate noun): `Поповцев`/`Старухин` have
   no `Surn` grammeme but must still be masked — including the two-part
   `Старухин Александр`. A role word before a FIO makes natasha attach the
   role as the surname and split the name (`Секретарь Т.Ф. Гилязов` →
   `Секретарь Т.Ф.` + `Гилязов`), so `_find_persons` also recovers
   `Т.Ф. Гилязов` / `Гилязов Т.Ф.` with the two-initials regexes and a strict
   name-pair fallback for `Иван Петров`.
   **Never drop the given-name `_is_name_token` guard or the surname
   exclusions** — `из Сторон`, `Оператор ЭДО`, `Привет Иван`, `Дорогой Иван`,
   `Г. Москва` and bare `Секретарь` must stay unmasked. Regressions: `test_rule_based.py::test_masks_full_fio_*`,
   `::test_masks_initials_with_surname_*`, `::test_fio_recovery_does_not_create_false_positives`.
5. **Deprecated APIs fail the suite** (`filterwarnings=error`). Use
   `status.HTTP_422_UNPROCESSABLE_CONTENT`, not the deprecated
   `..._UNPROCESSABLE_ENTITY`. Any new endpoint path the tests may hit must
   use non-deprecated symbols.
6. **Windows/PowerShell mangles UTF-8 when sending bodies.** When testing the
   API from the host, put the JSON in a UTF-8 file and POST it with
   `curl.exe --data-binary @file`, or run Python inside a container. A
   Cyrillic body sent literally through PowerShell arrives corrupted and comes
   back as `?` — that's a test-harness artifact, not a server bug.

## DOCX masking (`masking/ooxml/`)

7. **Masking is OPC-package-level, not python-docx run-level.** `ooxml/package.py`
   builds a namespace-URI inventory from `[Content_Types].xml` + relationship
   files (never hardcoded prefixes), parses XML with `defusedxml.lxml`
   (`forbid_dtd`, `forbid_entities`), groups text nodes by the closest `w:p`,
   and rewrites slices back-to-front. An entity spanning several text nodes
   becomes **one** placeholder. Only modified ZIP entries are re-serialized;
   unchanged entries (images, styles, numbering) are copied byte-for-byte.
8. **Supported parts are processed, not warned about.** Body/tables, all
   header/footer variants, footnotes, endnotes, comments, `w:delText`,
   `w:txbxContent`, `a:t`, `w:sdt`, `docProps/core.xml`/`app.xml`, `wp:docPr`
   alt-text attributes and external relationship `Target` values are all
   text-bearing. There is **no** `X-Docx-Unsupported-Parts` plumbing.
9. **Fail closed on uninspectable content.** Embedded OLE/`*.bin` objects,
   malformed/unsafe XML and oversized parts raise `OoxmlPackageError` → a clear
   422. Raster image pixels are the one documented non-text limitation
   (`raster_image_ocr: not_performed`); they are copied, not a blocker.
10. **One `manifest.json` for sync and job file results** (`ooxml/manifest.py`,
    `masking/archives.py`, `jobs/operations.py`). It carries versioned
    `statistics`, `coverage`, input hash, output names and `cache` — the
    frontend requires/validates it; no `X-Masked-*` headers. Text jobs return
    the exact canonical `MaskResponse`/`UnmaskResponse` in `result.json`.
11. **`mask_docx_in_memory` returns 4 values** (bytes, entities, keys,
    `CoverageReport`); `unmask_docx_in_memory` returns 3 (bytes, unused,
    coverage). Synthetic OPC fixtures + parity tests live in
    `tests/test_ooxml.py`; see `docs/DOCX_SUPPORT.md`.

## Keys, spans, reversibility

12. **`MaskKeys` (`mapping.py`) numbers each entity per label in document
    order** and dedupes by exact text: the same string repeated reuses one
    placeholder and one key; distinct wordings (different cases of one org)
    stay separate — that is what keeps restoration lossless. Numbers are
    allocated **left-to-right before** the back-to-front rewrite
    (`mask_text`), or the last entity gets `_1`. Unmasking is a plain
    placeholder→value `str.replace` (self-delimiting), so order does not
    matter there. Placeholders are `[LABEL_N]`; `placeholders_from_document`
    reverses a parsed keys file.
13. **Keys YAML uses `yaml12` (the `py-yaml12` package), not PyYAML.** YAML
    1.2 preserves quoting (`АО "Росхим"` stays quoted); PyYAML 1.1 would
    mangle it. Writers emit the legacy `Профиль: russian-default` marker ahead
    of the Russian sections; readers accept that shape (including a foreign
    marker value), the legacy envelope-less one, and ignore the marker as
    metadata. **No `версия`/`версия_профиля` keys** are written or read — the
    pre-prod v2 envelope was retired, those keys are now unknown sections.
    Section titles come from the canonical `labels.yaml` catalogue; empty
    sections are omitted.
14. **The `/api/mask-file` response is a flat ZIP, not a `.docx`.** It bundles
    `<base> (замаскированный).docx` + `<base> (ключи демаскировки).yaml` +
    `manifest.json`; the front unpacks it with jszip, **validates the manifest**
    and offers two separate downloads. Don't return the `.docx` alone, don't
    hand the archive to the user, and never put statistics in `X-Masked-*`
    headers. `/api/unmask-file` returns the restored `.docx` + `manifest.json`
    (with `statistics.unused_keys`) — the former `X-Unused-Keys` header carried
    percent-encoded Cyrillic PII in transport metadata and could outgrow
    every header size limit; never reintroduce it.
15. **Span overlap policy is formal**: entities are sorted by (start asc,
    length desc, score desc, detector order); a span is kept unless it
    overlaps a kept span. `_merge_spans` uses a sweep (O(n log n)) — the old
    `any()` scan was quadratic. Regressions for equal/nested/partial/touching
    spans live in `test_rule_based.py`.

## API & execution

16. **CPU *and* IO work must leave the event loop** (`masking/executor.py`,
    Phase 6 §10): `run_cpu` offloads natasha/OOXML/ZIP to a bounded CPU pool
    (`JOBS_CONCURRENCY * 2` slots, semaphore per event loop, overflow → 429);
    `run_blocking` offloads short DB/Valkey/ZIP/filesystem calls to a **separate**
    bounded IO pool (`DB_POOL_SIZE + DB_MAX_OVERFLOW` slots). One semaphore must
    not substitute for all resource limits. `/health` stays responsive during a
    mask — never call `mask_document`/`mask_docx_in_memory`/`job_repository()`
    DB methods directly in an `async def`. `count_docx_paragraphs` uses an
    incremental `lxml.iterparse` over `ZipExtFile` (no `archive.read()` of a
    large XML part), and the hard byte limit is checked before that heuristic.
17. **Large inputs become durable jobs** (ADR-0002 / PostgreSQL substrate
    ADR-0003): PostgreSQL (`jobs/repository.py`, SQLAlchemy 2 sync + psycopg 3,
    Alembic migration `0001_jobs`, `SKIP LOCKED` claim with a fresh `uuid4()`
    lease token) + private id-keyed spool (`jobs/storage.py`), long-lived warm
    worker (`python -m src.jobs.worker`), leases/heartbeat/retry/TTL/cancel.
    Job rows, logs and metrics must never contain PII or keys. The four sync
    endpoints transparently return `202 + JobAccepted` above
    `SYNC_MAX_TEXT_CHARS` / `SYNC_MAX_DOCX_BYTES` /
    `SYNC_MAX_DOCX_PARAGRAPHS`; `/api/jobs/{id}/result` is one flat ZIP
    (`result.json` for text jobs, `.docx` + `.yaml`/manifest for files).
    **No SQLite fallback remains.** Integration tests need a real PostgreSQL
    (`TEST_DATABASE_URL`); without it the job tests skip.

18. **Phase 2 job lifecycle invariants** (`jobs/worker.py`): every claim gets
    a fresh `uuid4()` fencing token + `worker_id` (`claim_with_owner`);
    heartbeat/phase/succeed/fail/acknowledge_cancel are all guarded by that
    token and affected-row count, so a stale worker cannot publish a result.
    A dedicated `Heartbeat` thread renews the lease every `lease_seconds / 3`
    independently of the blocking masking thread. `request_cancel` makes a
    queued job terminal immediately but only marks a running job `cancelling`;
    the worker acknowledges at cooperative checkpoints (never pretends a
    Python thread was killed). Failures are classified (deterministic input
    errors non-retryable; transient/internal bounded retry with backoff), TTL
    cleanup never touches active rows, and SIGTERM keeps heartbeating while
    in-flight jobs drain. Never log inputs/keys/exception text.
19. **Error/status conventions** (`src/errors.py`, Phase 6 §8.1): every error
    is `{code, detail, request_id}` + `X-Request-ID`; `code` is stable and
    machine-readable, `detail` is Russian, and no traceback/path/DB message/
    PII/keys ever leak (validation errors never echo submitted values, 500 logs
    only the exception *type*). `validate_keys_yaml` → 422 `invalid_keys`;
    malformed/oversized/encrypted DOCX → 415/413; bounded queue/executor →
    429; job result not ready → 409; unknown job id → 404. Unknown placeholders
    stay as-is. `GET /api/labels` publishes the canonical catalogue; `profile_id`
    and `GET /api/profiles` were removed in Phase 4. Setting `MASK_LABELS` fails
    startup. `GET /ready` (PostgreSQL) and `GET /metrics` (coarse counters, no
    PII, degrades on DB outage) are in `main.py`.
20. **One immutable masking configuration** (`masking/config.py`, Phase 4):
    `back/config/masking/labels.yaml` is the single label catalogue (sequence
    = keys-section order, no `order` field); all lexicons and
    `always_mask`/`never_mask` exceptions are literal typed YAML loaded into
    one validated snapshot at startup. Invalid YAML/duplicate/conflicting/
    unknown entries fail startup (`MaskingConfigError`). The snapshot digest
    feeds the cache key, so config changes invalidate cached results. The
    private `exceptions.yaml` is gitignored; only synthetic values go into
    `exceptions.example.yaml`. Validate without the app:
    `uv run --directory back python -m src.masking.validate_config`.
    `never_mask` suppresses only an exact whole detected span (never a larger
    overlapping one); `always_mask` spans go through the normal overlap policy
    and key numbering. Adding a label = one detector + one registry entry +
    one `labels.yaml` row (and regenerate the front catalogue).
    `MASK_LABELS` was removed with a loud startup error.
21. **Valkey cache is an optimization, never a correctness dependency**
    (`jobs/cache.py`, Phase 3): keys are HMAC-SHA256 over a canonical
    fingerprint of exact input/keys bytes + normalized filename + every
    version/digest that can change output (`MASKING_RULESET_VERSION`,
    `MASKING_CONFIG_DIGEST`, `RESULT_MANIFEST_VERSION`, `KEYS_FORMAT_VERSION`,
    `DOCX_TRANSFORMER_VERSION`, `CACHE_NAMESPACE_VERSION`). **Bump those
    versions whenever masking/keys/DOCX/manifest semantics change** or stale
    results will be served. Valkey stores only compact artifact references
    (large ZIP/DOCX live in the private `cache/` spool, hash-verified on
    read); a missing/corrupt artifact is a cache miss + eviction. Cache
    outages degrade to misses, never 500s. Writes happen **only after** the
    fenced `succeeded` transition; failed/cancelled jobs never populate it.
    Identical active jobs dedupe in PostgreSQL (`create_staging`), not in a
    Valkey lock. Never log the fingerprint, full key, inputs or keys YAML.
22. **Streaming uploads never become one giant `bytes` object** (`uploads.py`,
    Phase 6 §8.5): `stage_upload` copies 64 KiB chunks into a private temp file
    with a **real byte counter** (Content-Length is not trusted), hashes as it
    goes, and cleans up on any error/abort. `validate_docx_package_path`
    validates from that path (`ZipFile` reads only the central directory);
    `enqueue_from_path` streams it into the spool via `ArtifactStore.copy_from`.
    The sync file endpoints still build a small ZIP in memory because the OPC
    transformer needs the whole package — part-level DOM memory is documented,
    not claimed as streaming. `/api/jobs/{id}/result` uses `FileResponse`.
23. **Settings are cross-validated** (`config.py`, Phase 6 §10): positive
    limits/TTLs/timeouts, PostgreSQL-only URL, lease > heartbeat/DB-connect
    timeout, worker concurrency ≤ pool capacity, sync thresholds ≤ hard request
    limits, cache secret when enabled. `test_settings_validation.py` covers it.
    Never relax these into silent negative/zero values.
24. **§8.2 (OpenAPI → TS types) is deferred by maintainer decision.** Do not
    add `openapi-typescript`/generated `schema.ts`/CI freshness gate in this
    phase; handwritten DTOs remain. Tracked in `TODO.md`.
25. **Large-text detection is windowed, not whole-document (Phase 7 §9.1).**
    `masking/windowed.py::iter_windows` yields bounded overlapping windows
    aligned to structural boundaries and never cutting a token; spans from
    `RuleBasedMasker._detect_long` are re-based to absolute offsets and exact
    boundary duplicates are removed before `_merge_spans`. `checkpoint` runs
    between windows. `_find_addresses` sends `AddrExtractor` only sentences
    with an anchored address marker (abbreviation dots are not sentence ends)
    — feeding it the whole document caused the recorded 92.8 s / 7.3 GB
    baseline. OOXML containers call `detect(text, prefilter=True)`; the cheap
    per-detector preconditions can never suppress `always_mask`.
    `_suspicious_positions` is a bounded `lru_cache(256)`. See
    `docs/PERFORMANCE.md` for before/after numbers.
