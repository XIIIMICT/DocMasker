# Production readiness (living matrix)

Tracked findings from the production-readiness effort. Statuses:
`confirmed` (evidence recorded), `not reproduced` (evidence contradicts the
original claim), `already fixed`, `needs validation` (cannot be verified from
this repo alone), `planned` (accepted, scheduled for a specific stage).

Evidence for performance claims lives in `back/benchmarks/` (reproducible
probes, see `back/benchmarks/README.md`).

## Baseline environment (2026-11-22)

- Host: Windows, Docker Desktop 29.8.2 / compose v5.6.0, uv 0.13.8, Bun 1.4.4, Node 26.13.1.
- Container probes: `dm-back:baseline` (built from `back/`, python:3.15-alpine).
- Probes used: `benchmarks.text_mask`, `benchmarks.docx_mask`, `benchmarks.server_probes`.
- Prod images build and boot: `dm-back:baseline` (35 packages, CPython 3.15.2),
  `dm-front:baseline` (Next 16.4.6, all 6 routes).

## Data flow and buffering (upload path)

```
Browser ──► Next route handler (app/api/mask-file/route.ts)
             1. request.formData()          ← full multipart buffered
             2. new FormData(...) + fetch   ← file re-attached
            ──► FastAPI router (router.py)
             3. await file.read()           ← whole DOCX as one bytes object
             4. python-docx Document model  ← parsed DOM of the OOXML
             5. document.save(BytesIO)      ← masked DOCX bytes
             6. _archive()                  ← second ZIP built in BytesIO
            ◄── response
             7. Next reads response.arrayBuffer()  ← full archive buffered
             8. Browser Blob + JSZip unpack        ← two more blobs
             9. docx-preview renders full DOM      ← preview copy
```

So a large document exists as several full copies simultaneously (per request:
multipart buffer, re-attached FormData, `bytes`, docx model, masked bytes,
ZIP archive, proxy `arrayBuffer`, browser blobs). The unmask path has the same
shape. Latency probes for the processing steps are in `back/benchmarks/README.md`.

## Findings

| ID | Original claim | Severity | Status | Evidence | Remediation | Stage |
|---|---|---|---|---|---|---|
| F-01 | 3.2 async endpoints run CPU-bound masking in the event loop | critical | confirmed | `benchmarks.server_probes`: `/health` 2–3 ms baseline → **5,353 ms** during a 36 KB `/api/mask` (host granian) | move masking out of the event loop (bounded worker pool/processes), cap concurrency | 4 |
| F-02 | 7.2 detect() performance | critical | confirmed | `benchmarks.text_mask`: 36 KB ≈ 5.4 s, 360 KB ≈ **92.8 s** and **7.3 GB RSS** (OOM before finishing); cProfile: **91%** of detect is natasha/yargy `AddrExtractor` chart parsing; no windowing (`detect()` feeds the whole text) | window/batch long inputs before natasha, sweep-line span merge, cache lexicons; re-measure | 4 |
| F-03 | 3.5 DOCX dedupe loses cross-paragraph occurrences | high | confirmed | `benchmarks.docx_mask`: 3 identical paragraphs → 1 entity; **5,000 occurrences reported as 55**; unit repro: `_dedupe_entities([same,same,same]) == 1` (`docx_service.py:310-317` keys on paragraph-local `(start,end,label)`) | key by paragraph id (or drop cross-paragraph dedupe); separate occurrence vs unique counts; regression test | 2 |
| F-04 | 3.7 malformed uploads → 500, no limits | high | already fixed | `validate_docx_package` (`docx_validation.py`) checks OPC/ZIP structure, entry count, uncompressed size, compression ratio and encryption; endpoints answer 413/415 with Russian details; text/keys limits (`MAX_TEXT_CHARS`/`MAX_KEYS_CHARS`) → 413. Tests: `test_docx_validation.py` + endpoint tests. Front is not a trust boundary (backend enforces everything) | three-tier enforcement (proxy) when a reverse proxy exists | 6 |
| F-05 | 3.10 unused keys in HTTP header | high | already fixed | `/api/unmask-file` now returns a ZIP (restored `.docx` + `<base> (отчёт о демаскировке).json` manifest); `X-Unused-Keys` removed; front unpacks via `unpackRestoreArchive` (tests in mask-archive.test.ts, test_unmask.py) | — | done |
| F-06 | 3.14 TLS bypasses + root frontend container | high | confirmed | `front/Dockerfile:8-9` (`NODE_TLS_REJECT_UNAUTHORIZED=0`), `back/Dockerfile:12-13` (`--allow-insecure-host`), no `USER` in front prod stage; host `UV_SYSTEM_CERTS`/`NODE_OPTIONS=--use-system-ca` | proper CA trust (`NODE_EXTRA_CA_CERTS`), non-root front image, no insecure flags | 2 |
| F-07 | 3.3 multiple full copies of a file in memory | medium | confirmed | data-flow map above (8 buffer points); static read of route/proxy code | streaming proxy, spooled temp files, bounded concurrency | 4 |
| F-08 | 3.4 long operations over a single HTTP request | medium | confirmed | `instrumentation.ts` sets global undici `headersTimeout/bodyTimeout: 0`; `maxDuration = 3600` on proxy routes; F-01/F-02 show requests can hold the worker for minutes | durable job API (202 + status + result), bounded timeouts back | 4 |
| F-09 | 3.9 masked_entities carries raw PII to the client | medium | confirmed | `schemas.py` MaskResponse includes `text/start/end` per entity; front uses it only for counts (`page.tsx:914` → `countLabels`) | return aggregates; raw spans behind an opt-in if ever needed | 2 |
| F-10 | 3.15 unknown labels accepted silently | medium | confirmed | `benchmarks.docx_mask`: `RuleBasedMasker(labels=['person','banana'])` constructs fine (`rule_based.py:394-398`); keys writer skips unknown labels → placeholders without key sections possible | strict config validation at startup + clear error | 2 |
| F-11 | 3.6 DOCX traversal docstring overclaims first/even headers | medium | confirmed | `docx_service.py:13` docstring claims first/even variants; code walks default only (`docx_service.py:106-109`); tables in headers/footers not covered | fix docstring, coverage matrix (DOCX_SUPPORT), extend traversal if testable | 5 |
| F-12 | 3.8 no template/profile subsystem | medium | confirmed | `MASK_LABELS` env + `constants.py` + duplicated label tables in `front/src/shared/{api/mask.ts, mask-stats.ts}`; `ALWAYS_MASK_ORGANIZATIONS=("Росхим",)` hardcoded | profile registry + detector registry (ADR first) | 3 |
| F-13 | 3.12 CI topology / branch protection | medium | needs validation | `.github/workflows/ci.yaml` triggers only `main`/`dev`; origin currently holds only `feat/demasking`; rulesets live in GitHub settings (not verifiable from the repo) | push `main`/`dev`, make `dev` default, configure ruleset + required check (maintainer) | 7 |
| F-14 | 3.11 docs contradicted implementation | medium | already fixed | fixed in stage 0 (`docs/sync-toolchain`): Granian vs uvicorn, Python 3.15, `link` label, keys validation 422, stale commands/comments | — | done |
| F-15 | 3.13 rerender-smoke.spec.ts not in smoke project | low | not reproduced | `playwright test --project=smoke --list` → 17 tests in 3 files, `rerender-smoke.spec.ts` included (unanchored `testMatch` matches the substring) | none; keep the listing check in CI when touching Playwright config | — |
| F-16 | 3.1 Python version mismatch | — | already fixed | maintainer decision (2026-11-22): Python **3.15** everywhere; CI `python-version` fixed to 3.15; `uv sync --locked` verified green on 3.15 (and proven failing under `UV_PYTHON=3.14` before the fix) | — | done |

## Stage plan (mapping to the request's §13)

1. ✅ Этап 0 — docs/toolchain sync (branch `docs/sync-toolchain`, merged into `dev`).
2. ▶ Этап 1 — baseline + this matrix (branch `docs/readiness-baseline`).
3. Этап 2 — P0 fixes: F-03, F-04, F-05, F-06, F-09, F-10 (+ span policy).
4. Этап 3 — profile/template registry (F-12) with ADR.
5. Этап 4 — worker/job execution + streaming (F-01, F-02, F-07, F-08) with ADR; ReportCollector's durable-job design (leases, bounded queue, process isolation) is the reference implementation to adapt.
6. Этап 5 — DOCX coverage + frontend decomposition (F-11).
7. Этап 6 — observability/deployment.
8. Этап 7 — final docs, CI wiring, F-13.
