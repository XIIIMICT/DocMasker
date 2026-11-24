# Production readiness (living matrix)

Tracked findings from the production-readiness effort. Statuses:
`confirmed` (evidence recorded), `not reproduced` (evidence contradicts the
original claim), `already fixed`, `needs validation` (cannot be verified from
this repo alone), `planned` (accepted, scheduled for a specific stage).

Evidence for performance claims lives in `back/benchmarks/` (reproducible
probes, see `back/benchmarks/README.md`), interpreted in `docs/PERFORMANCE.md`.

Stage-7 verification (2026-11-23, this host): back 174 tests, front 53 unit +
65% coverage gate, ruff/ty/oxlint/oxfmt/actionlint, both compose configs green;
dev stack + isolated prod-image CI stack each passed **21/21** Playwright smoke
(incl. the 202→worker job path and the DOCX warning). The prod stack booted
`back`+`worker`+`front` all healthy on 18001/15001 and was torn down afterwards.
Still outstanding: target-hardware run and the job-lifecycle stress scenarios
(crash/restart, queue full, disk full, expired result, cancel) — see `TODO.md`.

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
| F-01 | 3.2 async endpoints run CPU-bound masking in the event loop | critical | already fixed | bounded `run_cpu` executor + warm worker pool (ADR-0002); `/health` stays responsive during a mask (executor test) | — | done |
| F-02 | 7.2 detect() performance | critical | confirmed | `benchmarks.text_mask`: 36 KB ≈ 5.4 s, 360 KB ≈ **92.8 s** and **7.3 GB RSS** (OOM before finishing); cProfile: **91%** of detect is natasha/yargy `AddrExtractor` chart parsing; no windowing (`detect()` feeds the whole text) | window/batch long inputs before natasha, sweep-line span merge, cache lexicons; re-measure | 4 |
| F-03 | 3.5 DOCX dedupe loses cross-paragraph occurrences | high | already fixed | cross-paragraph dedupe removed; 5000 identical occurrences now report 5000; regression tests for body+body, body+table+header collisions and endpoint counts (`test_docx.py`, `test_mask_file.py`) | — | done |
| F-04 | 3.7 malformed uploads → 500, no limits | high | already fixed | `validate_docx_package` (`docx_validation.py`) checks OPC/ZIP structure, entry count, uncompressed size, compression ratio and encryption; endpoints answer 413/415 with Russian details; text/keys limits (`MAX_TEXT_CHARS`/`MAX_KEYS_CHARS`) → 413. Tests: `test_docx_validation.py` + endpoint tests. Front is not a trust boundary (backend enforces everything) | three-tier enforcement (proxy) when a reverse proxy exists | 6 |
| F-05 | 3.10 unused keys in HTTP header | high | already fixed | `/api/unmask-file` now returns a ZIP (restored `.docx` + `<base> (отчёт о демаскировке).json` manifest); `X-Unused-Keys` removed; front unpacks via `unpackRestoreArchive` (tests in mask-archive.test.ts, test_unmask.py) | — | done |
| F-06 | 3.14 TLS bypasses + root frontend container | high | partially fixed | front prod image now runs as `node` (`USER node` + chowned copies); compose prod publishes the backend port loopback-only; TLS build bypasses kept **by maintainer decision** (mirrors ReportCollector; prod Node runtime verifies TLS) | replace build bypasses with a trusted CA when available | 2 |
| F-07 | 3.3 multiple full copies of a file in memory | medium | partially fixed | proxy responses are now streamed (no `arrayBuffer` copy) and job inputs/results are spooled files; python-docx still builds an in-memory DOM | streaming upload / OOXML streaming transformer | deferred |
| F-08 | 3.4 long operations over a single HTTP request | medium | already fixed | durable job API + worker (ADR-0002), threshold redirect to 202, bounded per-route timeouts, UI polling with backoff; global 0/0 timeouts and maxDuration=3600 removed | — | done |
| F-09 | 3.9 masked_entities carries raw PII to the client | medium | already fixed | `MaskResponse{text, stats{total, occurrences_by_label, unique_by_label}, keys}` — raw values/offsets no longer leave the API; front consumes the aggregates (`statsFromAggregate`) | — | done |
| F-10 | 3.15 unknown labels accepted silently | medium | already fixed | `resolve_labels()` validates against `DEFAULT_LABELS` in the Settings validator (startup fail-fast) and the masker constructor; duplicates/unknown raise `LabelsValidationError` | — | done |
| F-11 | 3.6 DOCX traversal docstring overclaims first/even headers | medium | already fixed | `iter_paragraphs` now covers all six header/footer variants + tables inside them (python-docx 1.2 resolves the OOXML references); unsupported parts documented in `docs/DOCX_SUPPORT.md` and reported via `X-Docx-Unsupported-Parts` with a UI warning | — | done | `docx_service.py:13` docstring claims first/even variants; code walks default only (`docx_service.py:106-109`); tables in headers/footers not covered | fix docstring, coverage matrix (DOCX_SUPPORT), extend traversal if testable | 5 |
| F-12 | 3.8 no template/profile subsystem | medium | already fixed | ADR-0001 implemented: `profiles.py` (typed `MaskingProfile`, `russian-default/1`, registry) + `detectors.py` (stable-id registry over the providers) + keys `Профиль` marker with legacy envelope-less reader (retired pre-prod v2 version keys) + `GET /api/profiles` + optional `profile_id`; `Росхим` moved into profile config; `MASK_LABELS` removed with a loud startup error (maintainer decision). Tests: `test_profiles.py`, `test_keys_v2.py`, `test_profile_api.py` | new profile = one registry entry | done |
| F-13 | 3.12 CI topology / branch protection | medium | partially fixed | CI now has explicit gates (back ≥80% branch coverage, front 65% line coverage via `scripts/check-coverage.mjs`, `production` needs config+back+front, boots `back`+`worker`+`front`, asserts the smoke spec list) and triggers on `main`/`dev`; **branch ruleset + required check `CI required` remain a GitHub-side action** (origin currently holds only `feat/demasking`, `main`/`dev` are local) | push `main`/`dev`, make `dev` default, configure ruleset + required check (maintainer) | 7 |
| F-14 | 3.11 docs contradicted implementation | medium | already fixed | fixed in stage 0 (`docs/sync-toolchain`): Granian vs uvicorn, Python 3.15, `link` label, keys validation 422, stale commands/comments | — | done |
| F-15 | 3.13 rerender-smoke.spec.ts not in smoke project | low | not reproduced | `playwright test --project=smoke --list` → **21 tests in 3 files**, `rerender-smoke.spec.ts`, `smoke.spec.ts`, `health.spec.ts` all included (unanchored `testMatch` matches the substring); CI's `--list` step now asserts all three files are discovered so a rename cannot silently drop out | none; keep the listing check in CI | 7 |
| F-16 | 3.1 Python version mismatch | — | already fixed | maintainer decision (2026-11-22): Python **3.15** everywhere; CI `python-version` fixed to 3.15; `uv sync --locked` verified green on 3.15 (and proven failing under `UV_PYTHON=3.14` before the fix) | — | done |

| F-16 | 3.x FIO false negatives | medium | already fixed | `NamesExtractor` fails on out-of-vocabulary surnames (`Поповцев`, `Старухин`, incl. two-part `Старухин Александр`) and on role-prefixed names (`Секретарь Т.Ф. Гилязов` → role glued as surname); detector relaxes the surname guard for verified-given-name FIOs, recovers initials+surname / strict name pairs from the text, and excludes roles/greetings/toponyms/common nouns. Live `/api/mask` round-trip + regression tests | — | done |

## Stage plan (mapping to the request's §13)

1. ✅ Этап 0 — docs/toolchain sync (branch `docs/sync-toolchain`, merged into `dev`).
2. ✅ Этап 1 — baseline + this matrix (branch `docs/readiness-baseline`); `back/benchmarks/` + `docs/PERFORMANCE.md`.
3. ✅ Этап 2 — P0 fixes: F-03 (occurrence counting), F-04 (upload validation 413/415), F-05 (manifest instead of header), F-06 (non-root front, loopback backend port; TLS bypasses accepted by decision), F-09 (aggregates), F-10 (label validation), formal span-overlap policy + O(n log n) merge. Six task branches merged into `dev`.
4. ✅ Этап 3 — profile/template registry (F-12) with ADR-0001, keys v2 + legacy reader, `GET /api/profiles`, optional `profile_id`.
5. ✅ Этап 4 — worker/job execution + streaming: ADR-0002 accepted; SQLite WAL store (исторически) + private spool; `/api/jobs` contract; threshold redirect (20 KB / 1 MB / 200 paragraphs); long-lived warm worker container; `run_cpu` executor; bounded/streaming proxies; job UI polling. ReportCollector's lease/queue design was the reference; its Postgres/Redis were replaced by SQLite per the maintainer decision — **superseded by ADR-0003 (Phase 1 заменил SQLite на PostgreSQL)**.
6. ✅ Этап 5 — DOCX coverage + frontend decomposition (F-11): all six header/footer variants, `docs/DOCX_SUPPORT.md`, `X-Docx-Unsupported-Parts` warning, `DocxViewer` race fix, full `masker-page.tsx` decomposition.
7. ⏸ Этап 6 — observability/deployment/operations — **deferred** by maintainer (2026-11-23); tracked in `TODO.md`.
8. ✅ Этап 7 — final docs/CI (the stage-7 part that does not depend on stage 6): short root `AGENTS.md` index + `back/`/`infra/` guides, `docs/API.md` / `docs/TEMPLATES.md` / `docs/PERFORMANCE.md`, CI gates (front coverage, worker in prod smoke, explicit spec listing, `production` needs prerequisites). Branch ruleset + required check remain external; final clean-install/prod verification on target hardware is tracked in `TODO.md`.
