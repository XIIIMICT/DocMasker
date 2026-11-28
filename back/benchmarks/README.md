# Baseline benchmarks

Reproducible probes for the masking pipeline. Run them before and after any
performance-touching change; record the numbers in `docs/PERFORMANCE.md`.

All inputs are synthetic Russian texts with PII-shaped patterns (no real data,
no network). The generators live in `benchmarks/synthetic.py`.

## Probes

| Probe | What it measures | Run |
|---|---|---|
| `text_mask` | detect+rewrite wall time and peak RSS for 4 text sizes | see below |
| `docx_mask` | mask/unmask wall time and peak RSS for paragraph/table DOCX shapes | see below |
| `docx_dedupe` | reproduces the cross-paragraph `_dedupe_entities` counting bug (3.5) | see below |
| `api_health_gap` | `/health` latency while a heavy `/api/mask` runs on the same server (3.2) | see below |
| `unmask_header` | `X-Unused-Keys` percent-encoded header size for N unused values (3.10) | see below |
| `malformed_upload` | HTTP status for garbage-bytes / non-DOCX ZIP uploads (3.7) | see below |
| `bounded_memory` | path-based OPC transform: wall time, tracked peak, temp disk for small/medium/large DOCX, large media, many XML parts and high concurrency (P2.11) | see below |

From `back/` (host toolchain: `uv run --directory back …`):

```sh
# text + event-loop probes (no server needed)
uv run --directory back python -m benchmarks.text_mask

# DOCX benchmarks + the 3.5/3.15 reproducers (no server needed)
uv run --directory back python -m benchmarks.docx_mask

# P2.11 bounded-memory transform matrix (prints JSON)
uv run --directory back python -m benchmarks.bounded_memory

# server probes: start the backend first, then run the client
uv run --directory back granian --interface asgi src.main:app --host 127.0.0.1 --port 18009 &
uv run --directory back python -m benchmarks.server_probes
```

Alternatively run the container probes against the production image:

```sh
docker build --target prod -t dm-back:baseline ./back
docker run --rm -v "$(pwd)/benchmarks:/benchmarks:ro" -e PYTHONPATH=/app dm-back:baseline python /benchmarks/text_mask.py
```

## Recorded bounded-memory run (host Windows)

`python -m benchmarks.bounded_memory` generates synthetic DOCX packages and
runs `transform_package_to_file`. Raw output: `benchmarks/bounded-memory-results.json`.
`peak_rss_bytes` is POSIX-only and therefore `null` on this host; use
`peak_tracked_bytes` (tracemalloc) here and record RSS when running on Linux.

| Case | Input / uncompressed | Wall | Peak tracked | Temp output |
|---|---|---|---|---|
| small | 35 KB / 827 KB | 0.09 s | 3.4 MB | 1 file |
| medium | 37 KB / 871 KB | 1.42 s | 5.1 MB | 1 file |
| large-near-max (22 MB media) | 23.1 MB / 23.9 MB | 0.58 s | 4.5 MB | 1 file |
| large-media (8 MB) | 8.4 MB / 9.2 MB | 0.11 s | 3.6 MB | 1 file |
| large-xml (6,000 paragraphs) | 53 KB / 1.37 MB | 17.9 s | 12.8 MB | 1 file |
| many-xml-parts (200) | 71 KB / 843 KB | 0.36 s | 3.8 MB | 1 file |
| high-concurrency (4 workers) | 4 × 36 KB | 2.70 s | 10.4 MB | 4 files |

Peak tracked memory does **not** scale with the 23.9 MB decompressed package
(4.5 MB), showing unchanged media is copied by chunks rather than retained.

## Recorded baseline (host: Windows, Docker Desktop; container: python:3.15-alpine)

### text_mask (container, python:3.15-alpine)

| Input | Size | Entities | Detect | Rewrite | Peak RSS |
|---|---|---|---|---|---|
| small (10 sentences) | 4 KB | 52 | 544 ms | 0.1 ms | 177 MB |
| medium (1,000) | 360 KB | 5,326 | **92.8 s** | 2.0 s | **7.3 GB → OOM-killed before finishing large** |

The detect step is **super-linear**: 36 KB ≈ 5.4 s (host profile, 16 s under
cProfile), 360 KB ≈ 92.8 s, and memory explodes with it (ru_maxrss 7.3 GB).
cProfile (36 KB input): 91% of detect time is natasha/yargy `AddrExtractor`
chart parsing (`predict` 845K calls, 2.1M parser states, `__hash__` 2.1M calls).
The address extractor is applied to the whole text with no windowing; the
person extractor is comparatively cheap (1.3 s / 36 KB).

### docx_mask (container, python:3.15-alpine)

DOCX detection runs per paragraph, so memory stays flat — but natasha's fixed
per-paragraph cost dominates:

| Document | Size | Entities | Mask | Unmask | Peak RSS |
|---|---|---|---|---|---|
| 100 paragraphs | 37 KB | 28 | 1.8 s | 36 ms | 168 MB |
| 1,000 paragraphs (many PII-free) | 44 KB | 39 | 20.1 s | 215 ms | 180 MB |
| 5,000 paragraphs | 77 KB | 55 | **115 s** | 997 ms | 193 MB |
| 200 paragraphs + 200×2 nested table | 41 KB | 57 | 10.5 s | 152 ms | 202 MB |

A 5,000-paragraph DOCX masks in ~2 minutes — one HTTP request, event loop
blocked (see server_probes), no way to retry without repeating the whole work.

### docx_dedupe (3.5 — confirmed, end-to-end)

A document with three identical-name paragraphs reports **1** `masked_entities`
entry and 0 placeholders in text (the marker must be `[PERSON_1]`; the count is
wrong because `_dedupe_entities` keys on `(start, end, label)` — paragraph-local
offsets). With 5,000 structurally-repeated paragraphs the service reported 55
entities for 5,000 occurrences: `X-Masked-Count` / `X-Masked-Breakdown` and the
UI stats undercount occurrences massively.

### api_health_gap (host granian, 3.2 — confirmed)

`/api/mask` with 36 KB text: 5.4 s wall; concurrent `/health` p95 jumped from
2–3 ms to **5,353 ms** — the async endpoint blocks the event loop with the
synchronous masker.

### unmask_header (3.10 — confirmed by construction)

`X-Unused-Keys` percent-encoded JSON: **28 KB @ 100 unused values, 285 KB @
1,000, 1.4 MB @ 5,000** — header-based metadata does not scale and leaks PII
into transport metadata. (Latency probe: unmask itself is fast — 1.5 ms/29 KB —
the problem is the transport, not the algorithm.)

### malformed_upload (3.7 — confirmed)

`POST /api/mask-file` with garbage bytes → **HTTP 500** (`zipfile.BadZipFile`
escapes the endpoint); a valid ZIP that is not a DOCX → **HTTP 500**. No size
limits anywhere on the path.
