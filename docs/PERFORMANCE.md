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
| F-02 detect() super-linear on large texts | **Not algorithmically fixed.** Large inputs are redirected to durable jobs (ADR-0002), which bounds one request's blast radius but does not speed up natasha. Windowing/batching is deferred (`TODO.md`) | open |
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

## Remaining hotspots (deferred, see `TODO.md`)

- **F-02** window/batch long texts before natasha `AddrExtractor`; re-measure
  `text_mask`. 91% of detect time is address chart parsing on the whole text.
- **F-07** `python-docx` DOM in memory; streaming upload / OOXML streaming
  transformer is a separate design decision.
- **7.2** cache the prefix lexicon `_suspicious_positions`; cost of
  `SequenceMatcher` / repeated morphology on large texts.
- **7.3** accuracy corpus: versioned precision/recall/F1 per label, false
  positives/negatives. `score = 1.0` is a constant, **not** a confidence — the
  span-overlap policy uses it only as a tiebreaker (see `back/AGENTS.md`).
- **12.2** stress tests: worker crash/restart, queue full, disk full, request
  cancellation, "almost no PII" documents, 1/2/4/N concurrent jobs.
- **6.1** multi-instance scaling (Postgres/Redis + private object storage) —
  needs an ADR; PostgreSQL coordinates workers but the artifact spool is
  single-host by decision (ADR-0003).

## Capacity facts worth keeping

- natasha's `MorphVocab` + extractors cost ~170 MB of in-memory dictionary per
  instance and are not proven thread-safe: the worker keeps **one warm masker
  per thread**, bounded by `JOBS_CONCURRENCY`; the sync path caches one masker
  per profile (`get_masker_for`, `lru_cache`). Never construct a
  `RuleBasedMasker` per request.
- DOCX masking is two-pass over all paragraphs (document-wide numbering), so a
  5,000-paragraph document is ~2 min of CPU — exactly the case the job path
  exists for.
- A `.txt` never reaches the backend: the front reads it into the input text
  client-side, so text-mode limits are pure `MAX_TEXT_CHARS` / threshold math.
