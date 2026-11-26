# Performance, limits and benchmarks

Reproducible measurements live in `back/benchmarks/` (see its `README.md`);
this document owns the interpretation, the resource limits and the remaining
hotspots. Run the probes **before and after** any performance-touching change
and record the numbers here or in `docs/PRODUCTION_READINESS.md`.

All benchmark inputs are synthetic Russian texts with PII-shaped patterns (no
real data, no network). Baseline recorded 2026-11-22 on Windows / Docker
Desktop 29.8.2, container `python:3.15-alpine`, CPython 3.15.2.

## Reproduce

From the repo root (host toolchain: `uv run --directory back …`):

```sh
uv run --directory back python -m benchmarks.text_mask
uv run --directory back python -m benchmarks.docx_mask
# server probes need a running backend:
uv run --directory back granian --interface asgi src.main:app --host 127.0.0.1 --port 18009 &
uv run --directory back python -m benchmarks.server_probes
```

Or against the production image (see `back/benchmarks/README.md` for the exact
`docker run` command).

## Recorded baseline (stage 1, before the fixes)

### Text — `benchmarks.text_mask` (container)

| Input | Size | Entities | Detect | Rewrite | Peak RSS |
|---|---|---|---|---|---|
| small (10 sentences) | 4 KB | 52 | 544 ms | 0.1 ms | 177 MB |
| medium (1,000 sentences) | 360 KB | 5,326 | **92.8 s** | 2.0 s | **7.3 GB → OOM-killed** |

Super-linear: 36 KB ≈ 5.4 s, 360 KB ≈ 92.8 s. cProfile on 36 KB: **91% of
detect** is natasha/yargy `AddrExtractor` chart parsing (`predict` 845K calls,
2.1M parser states). The address extractor is applied to the **whole text** with
no windowing; the person extractor is comparatively cheap (1.3 s / 36 KB).

### DOCX — `benchmarks.docx_mask` (container)

Detection runs per paragraph, so memory stays flat — but natasha's fixed
per-paragraph cost dominates:

| Document | Size | Entities | Mask | Unmask | Peak RSS |
|---|---|---|---|---|---|
| 100 paragraphs | 37 KB | 28 | 1.8 s | 36 ms | 168 MB |
| 1,000 paragraphs (many PII-free) | 44 KB | 39 | 20.1 s | 215 ms | 180 MB |
| 5,000 paragraphs | 77 KB | 55 | **115 s** | 997 ms | 193 MB |
| 200 paragraphs + 200×2 nested table | 41 KB | 57 | 10.5 s | 152 ms | 202 MB |

### Server probes (stage-1 context, now fixed)

- `/api/mask` with 36 KB text: 5.4 s wall; concurrent `/health` p95 jumped
  from 2–3 ms to **5,353 ms** — the async endpoint blocked the event loop with
  the synchronous masker (F-01).
- `X-Unused-Keys` percent-encoded JSON: 28 KB @ 100 values, 285 KB @ 1,000,
  1.4 MB @ 5,000 — header-based metadata does not scale and leaks PII (F-05).
- Garbage / non-DOCX ZIP uploads → HTTP 500; no size limits (F-04).

## What was fixed (stages 2–4)

| Finding | Fix | Status |
|---|---|---|
| F-01 event loop blocked | `masking/executor.py::run_cpu` offloads natasha/python-docx/ZIP to a bounded `ThreadPoolExecutor` (`JOBS_CONCURRENCY * 2` slots, semaphore per event loop, overflow → 429); `/health` stays responsive | done |
| F-02 detect() super-linear on large texts | **Phase 7 fixed algorithmically**: bounded overlapping windows with absolute offsets and boundary dedupe (`masking/windowed.py`), candidate-limited `AddrExtractor` (address markers only), DOCX prefilter and thread-local masker. 360 KB: 211.7 s / ~10 GB → 28.3 s / 144 MB; DOCX 5,000 paragraphs: 115 s → 7.4 s (before/after above) | done |
| F-03 DOCX cross-paragraph dedupe undercount | dedupe removed; 5,000 identical occurrences report 5,000 | done |
| F-04 upload validation | `docx_validation.py`: OPC/ZIP structure, entry count, uncompressed size, compression ratio, encryption → 413/415 with Russian details; text/keys char limits | done |
| F-05 unused keys in header | restore ZIP manifest `{"unused_keys": [...]}` in the body | done |
| F-07 multiple full copies in memory | proxy responses streamed (`new Response(response.body)`, no `arrayBuffer`), job inputs/results spooled to files; **python-docx still builds an in-memory DOM** | partial |
| F-08 long work over one HTTP request | durable job API + worker, threshold → 202, bounded per-route timeouts, UI polling with backoff; global `0`/`0` undici timeouts and `maxDuration = 3600` removed | done |
| F-09 raw PII in JSON | `stats` is aggregate-only (`total`, `occurrences_by_label`, `unique_by_label`) | done |

Re-run the probes after touching any of these paths — the numbers above are the
*before* baseline, not current throughput. The deferred list in `TODO.md`
tracks re-measurement.

## Resource limits (`.env`, single source)

| Variable | Default | Effect |
|---|---|---|
| `UPLOAD_MAX_MB` | 25 | max `.docx` upload (OPC/ZIP validation layered on top) |
| `MAX_TEXT_CHARS` | 200000 | text in JSON requests → 413 |
| `MAX_KEYS_CHARS` | 1000000 | keys YAML in requests → 413 |
| `SYNC_MAX_TEXT_CHARS` | 20000 | text above this → `202 + JobAccepted` |
| `SYNC_MAX_DOCX_BYTES` | 1048576 | DOCX bytes above this → job |
| `SYNC_MAX_DOCX_PARAGRAPHS` | 200 | paragraph estimate above this → job |
| `DETECTION_MAX_WINDOW_CHARS` | 16384 | hard per-window ceiling for an unbroken token (DM-5; ≥ chunk) |
| `JOBS_MAX_ACTIVE` | 16 | bounded queue → 429 |
| `JOBS_CONCURRENCY` | 2 | worker warm threads / sync executor slots (`×2`) |
| `JOBS_MAX_ATTEMPTS` | 3 | retries with backoff |
| `JOBS_LEASE_SECONDS` | 30 | claim lease / heartbeat |
| `JOBS_POLL_SECONDS` | 1 | worker poll interval |
| `JOBS_TTL_SECONDS` | 3600 | result expiry / sweep |

The thresholds come from the stage-1 measurements and are deliberately
conservative: they keep small inputs on the synchronous path and move large
ones to the worker. They are **estimates**, to be re-anchored by benchmarks
(ADR-0002). `front/src/instrumentation.ts` sets `headersTimeout: 60_000` /
`bodyTimeout: 600_000`; `shared/api/proxy.ts` uses `AbortSignal.timeout` — 15 s
for JSON/job polling, 10 min for the synchronous file endpoints. Long work
never depends on a held-open connection.

### Phase 7 — after (2026-11-24, host toolchain)

`benchmarks/profile_detection.py --size N` (one process per size, so peak RSS
reflects the real `detect()` pipeline; raw extractor diagnostics moved behind
`--raw`). Same synthetic generator and fixed seed as the baseline:

| Input | Detect (before → after) | Peak RSS (before → after) | Entities |
|---|---|---|---|
| ~4 KB | 739 ms → **188 ms** | 231 MB → **131 MB** | 88 |
| ~36 KB | 9.9 s → **2.0 s** | 1325 MB → **139 MB** | 803 |
| ~360 KB | 211.7 s → **28.3 s** | ~10 GB → **144 MB** | 7960 |

The ~4.6–7.5× speedup and flat ~144 MB RSS come from these changes:
1. `masking/windowed.py` splits long text into overlapping structural windows
   (8 KB min / 4096 chars / 512 overlap), re-bases spans to absolute offsets,
   never cuts through a token and deduplicates boundary spans deterministically
   (§9.1). `checkpoint` runs between windows. DM-5 adds a 16,384-char hard
   ceiling: when no boundary fits in a chunk, that window extends forward to the
   next whitespace but no further; an even longer unbroken run fails with a 422
   instead of being cut. The common case adds no extra window.
2. `_find_addresses` feeds `AddrExtractor` only sentence-sized ranges carrying
   an anchored address marker (abbreviations/postcode/`адрес:`), instead of
   the whole document. Abbreviation dots are not treated as sentence ends.
3. DOCX OOXML containers use `detect(text, prefilter=True)`: detectors whose
   cheap precondition cannot match are skipped; `always_mask` still runs over
   the full text. The address condition reuses the anchored lexicon marker
   matcher, so short fragments without digits still run while one-letter
   abbreviations inside unrelated words do not match. `get_masker()` is
   thread-local (natasha extractors are not proven thread-safe); each thread
   pays ~110 MB once — keep `JOBS_CONCURRENCY` conservative.
4. Worker DOCX jobs pass a `checkpoint` into the OPC transformer: it runs before
   each text-bearing part and is forwarded to `detect()` for window boundaries.
   Cancellation therefore stops at coarse boundaries rather than after the
   whole package; no per-character DB query is involved.

`benchmarks.docx_mask` (after):

| Document | Mask (before → after) | Unmask (before → after) | Peak RSS |
|---|---|---|---|
| 100 paragraphs | 1.8 s → **207 ms** | 36 ms → **24 ms** | 142 MB |
| 1,000 paragraphs | 20.1 s → **1.13 s** | 215 ms → **53 ms** | 147 MB |
| 5,000 paragraphs | 115 s → **7.4 s** | 997 ms → **209 ms** | 165 MB |
| 200 + nested table | 10.5 s → **842 ms** | 152 ms → **59 ms** | 165 MB |

Baseline entity counts differ because stage 2 fixed the cross-paragraph
dedupe undercount; the wall-time comparison is still valid (same input shapes).

## Remaining hotspots (deferred, see `TODO.md`)

- **F-02** — **closed in Phase 7** (windowing + candidate-limited
  `AddrExtractor` + prefilter); see the before/after table above.
- **F-07** obsolete: Phase 5 replaced python-docx DOM traversal with the OPC
  transformer; the remaining part-level DOM cost is documented in
  `docs/DOCX_SUPPORT.md`.
- **7.2** `_suspicious_positions` is now a bounded `lru_cache(256)`; remaining
  `SequenceMatcher` / repeated morphology cost on large texts is unmeasured.
- **7.3** accuracy corpus: versioned precision/recall/F1 per label, false
  positives/negatives. `score = 1.0` is a constant, **not** a confidence — the
  span-overlap policy uses it only as a tiebreaker (see `back/AGENTS.md`).
- **12.2** stress tests: worker crash/restart, queue full, disk full, request
  cancellation, "almost no PII" documents, 1/2/4/N concurrent jobs.
- **6.1** multi-instance scaling (Postgres/Redis + private object storage) —
  needs an ADR; PostgreSQL coordinates workers but the artifact spool is
  single-host by decision (ADR-0003).

## P2.11 bounded-memory DOCX path (2026-11-26)

File jobs now use `transform_package_to_file()`: the OPC inventory materialises
only parts that will be transformed, unchanged media/binary entries are copied
from the source ZIP by 1 MiB chunks, one XML part tree is processed at a time,
and the transformed DOCX / result ZIP are written to files in the private spool
before an atomic fenced result publication. Cache artifacts are published and
read by path (`CacheArtifactStore.publish_from_path` / `copy_to`) with streaming
hash verification — no base64 ZIP in JSON and no full in-memory result on a hit.

Reproduce with `uv run --directory back python -m benchmarks.bounded_memory`;
raw JSON is `back/benchmarks/bounded-memory-results.json`. Host (Windows) values:

| Case | Input / uncompressed | Wall | Peak tracked | Temp files |
|---|---|---|---|---|
| small | 35 KB / 827 KB | 0.09 s | 3.4 MB | 1 |
| medium | 37 KB / 871 KB | 1.42 s | 5.1 MB | 1 |
| large-near-max (22 MB media) | 23.1 MB / 23.9 MB | 0.58 s | 4.5 MB | 1 |
| large-media (8 MB) | 8.4 MB / 9.2 MB | 0.11 s | 3.6 MB | 1 |
| large-xml (6,000 paragraphs) | 53 KB / 1.37 MB | 17.9 s | 12.8 MB | 1 |
| many XML parts (200) | 71 KB / 843 KB | 0.36 s | 3.8 MB | 1 |
| 4 concurrent transforms | 4 × 36 KB | 2.70 s | 10.4 MB | 4 |

The 23.9 MB decompressed package needs only 4.5 MB of tracked Python peak:
memory no longer scales with all ZIP entries at once. `peak_rss_bytes` is
POSIX-only and was `null` on this Windows host; record it in the Linux target
run. Remaining bounded limits, stated explicitly: **one XML part tree** per
worker (32 MiB XML-part cap in `_secure_parse`), the current container's
serially written result ZIP, and the browser's full-result Blob + JSZip
unpacking (`front/src/shared/api/jobs.ts`) — client-side streaming is not
claimed. Sync routes remain bounded by `SYNC_MAX_DOCX_BYTES`.

## DM-4 one-pass output construction (2026-11-26)

### Re-verified finding

`masking/service.py::mask_text` rebuilt the whole result once per detected
entity (`masked = masked[:start] + marker + masked[end:]`), so output
construction was **O(spans × input length)** even though numbering and the
`MaskKeys` de-duplication contract were correct. The detector, not this step,
still dominates end-to-end latency (see the Phase 7 table above).

### What changed

Span validation (integer offsets, `0 <= start < end <= len(text)`, non-empty,
non-overlapping, and `entity.text == text[start:end]`) runs **before** any
placeholder is allocated; malformed or overlapping spans fail closed with a
422 `invalid_span` (`InvalidSpanError`) carrying fixed Russian detail and no
entity values. Numbers are then allocated left-to-right in document order (so
identical repeats reuse one key and distinct wordings stay separate), the
marker-collision guard runs unchanged, and untouched slices plus markers are
appended to one list and joined **once**. Each source character is read at most
once: output construction is **O(input length + spans)**.

### Environment and commands

Windows 11 10.0.26300, CPython 3.15.0 in the uv-managed `back/.venv` (host
toolchain, no container), `time.perf_counter`, **best of 3** runs. Inputs are
synthetic: `n` occurrences of `Иван` separated by single spaces (`4n + n - 1`
characters), one label, one key, no detection step. Reproduce with:

```sh
uv run --directory back pytest -q tests/test_mask_one_pass.py -s
```

The old column was measured in the same process by running the pre-change
back-to-front loop verbatim against the same entities; both produce identical
output (asserted in the probe).

| Spans | Chars | Old rewrite | One-pass join | Speedup |
|---|---|---|---|---|
| 250 | 1,249 | 0.317 ms | 0.135 ms | 2.35× |
| 500 | 2,499 | 0.883 ms | 0.264 ms | 3.35× |
| 1,000 | 4,999 | 2.842 ms | 0.534 ms | 5.32× |
| 2,000 | 9,999 | 10.194 ms | 1.018 ms | 10.02× |
| 4,000 | 19,999 | 41.037 ms | 2.112 ms | 19.43× |
| 8,000 | 39,999 | 157.198 ms | 4.180 ms | 37.61× |
| 16,000 | 79,999 | 606.487 ms | 8.879 ms | 68.31× |

The `pytest -s` structural output records source slices and characters read by
the renderer: at 250/500/1000/2000/4000 spans it is 249/499/999/1999/3999
slices and exactly the same number of source characters (only the separator
spaces) — never proportional to `spans × length`.

These timings cover **output construction only**. Detection is unchanged and
dominates the text pipeline (seconds for hundreds of KB), so **no end-to-end
runtime improvement is claimed** from this change alone; the measured win is
the removal of the quadratic rewrite, which previously dominated for dense,
PII-heavy inputs.

## DM-3 bounded OPC inventory (2026-11-26)

### Re-verified mismatch

`build_inventory()` used to call `archive.read()` for **every processable part**
and store the bytes on each `_Part`, while `docs/DOCX_SUPPORT.md` and
`back/src/masking/AGENTS.md` claimed only one XML part tree is resident at a
time. On the synthetic fixture below the pre-change inventory retained **202
decompressed XML payloads / 3,133,094 bytes**, i.e. the whole processable XML
content, before detection started. A second hotspot was the per-occurrence
`all_entities` dict list used only for aggregate manifest statistics.

### Environment and method

Windows 11 10.0.26300, CPython 3.15.0 in the uv-managed `back/.venv` (host
toolchain, no container), `RuleBasedMasker.for_labels(["email"])` so detection
is regex-only and identical in both columns, `transform_package_to_file()` (the
job path) writing to a temporary spool path. `time.perf_counter`,
`tracemalloc.get_traced_memory()` and Windows PSAPI `GetProcessMemoryInfo`
(`WorkingSetSize` sampled from the `checkpoint` hook, `PeakWorkingSetSize` for
the process — the closest Windows equivalent of RSS; POSIX `getrusage` is
unavailable here). **Best of 3 separate processes per column**, same detached
worktree and the same freshly created uv venv. Synthetic DOCX: 200 `customXml`
XML parts of 7,800 characters each plus the required OPC parts; 43,954 B
compressed / 3,133,468 B uncompressed; 201 email occurrences; 54,447 B output.
No production data, no network.

| Metric (best of 3) | Before | After | Delta |
|---|---|---|---|
| Wall time | 1.470 s | 1.454 s | ~1% (noise) |
| Peak tracked Python allocations | 4,613,956 B (4.40 MiB) | 1,436,642 B (1.37 MiB) | 3.21× lower |
| Peak working set, process | 142,008,320 B (135.4 MiB) | 138,924,032 B (132.5 MiB) | 3,084,288 B lower |
| Peak working set sampled during transform | 141,963,264 B | 138,911,744 B | 3,051,520 B lower |
| Inventory-retained XML payload bytes | 3,133,094 B (202 parts) | 0 B | removed |

The memory reduction is measured; **no end-to-end speedup is claimed** —
detection and ZIP I/O dominate this fixture and the wall-time difference is
within run-to-run noise. Structural linearity is asserted in
`tests/test_ooxml_bounded.py` (tracked bytes are refcount-checked one part at a
time, and the worker path passes `detail=None` to the transformer). The
browser's full-result Blob and JSZip unpacking remain the client-side bound.

## Capacity facts worth keeping

- natasha's `MorphVocab` + extractors cost ~110–170 MB of in-memory dictionary
  per instance and are not proven thread-safe. Since Phase 7 `get_masker()`
  returns **one masker per thread** (`threading.local()`), not one process-wide
  `@cache` instance; the sync CPU pool and worker use their own. Keep
  `JOBS_CONCURRENCY` conservative and measure peak RSS when changing it.
  Never construct a `RuleBasedMasker` per request.
- DOCX masking is two-pass over all paragraphs (document-wide numbering), so a
  5,000-paragraph document is ~2 min of CPU — exactly the case the job path
  exists for.
- A `.txt` never reaches the backend: the front reads it into the input text
  client-side, so text-mode limits are pure `MAX_TEXT_CHARS` / threshold math.
