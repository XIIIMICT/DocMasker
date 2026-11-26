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

## Follow-up remediation (2026-11-26)

The follow-up brief `DocMasker_Follow_Up_Fixes_Agent_Prompt.md` supersedes the
historical evidence counts in the table below. Verified on `dev` after the
remediation pass: **361 back tests (3 skipped), 86.22% branch**, 74 front unit
tests, ruff + ty(linux) + oxlint/oxfmt + actionlint + both compose configs green,
24/24 Playwright smoke on the running dev stack. No push was made; `dev` is
ahead of `origin/dev`.

- P0/P1 complete: fenced worker ownership and attempt-scoped results, staging/
  cancelling recovery, cooperative cancellation inside DOCX detection, honest
  OPC inventory/coverage, unified input limits and hostile ZIP-name checks,
  full canonical config digest with real exception match modes, bounded
  Valkey-referenced cache-artifact GC, atomic queue admission, monotonic job
  progress, frontend cancellation honesty, loopback monitoring bindings and
  truthful `manifest.cache.hit` on hits.
- The historical F-17/F-18/F-20/F-21/F-22/F-23 rows are **baseline** claims;
  their new edge cases are covered by the follow-up matrix in
  `docs/FOLLOW_UP_MATRIX.md` and the tests named there.
- Still deferred: P2.11 bounded-memory DOCX transform path and reproducible
  before/after memory benchmarks; the current worker still materialises one
  XML part tree, the transformed DOCX and the result ZIP in memory (bounded by
  `SYNC_MAX_DOCX_BYTES` on sync routes, not claimed as fully streaming).
  Branch protection / required check remains a GitHub-side setting.

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
| F-02 | 7.2 detect() performance | critical | already fixed | Phase 7 (§9.1): `masking/windowed.py` bounded overlapping windows with absolute offsets, boundary dedupe and checkpoints; `_find_addresses` feeds `AddrExtractor` only address-marker sentences (abbreviation dots are not sentence ends); DOCX prefilter + thread-local masker. Before→after: 360 KB 211.7 s / ~10 GB → **28.3 s / 144 MB**, DOCX 5,000 paragraphs 115 s → **7.4 s**. `test_windowed.py` covers person/phone/URL/org/address crossing boundaries | before/after recorded in `docs/PERFORMANCE.md` | done |
| F-03 | 3.5 DOCX dedupe loses cross-paragraph occurrences | high | already fixed | cross-paragraph dedupe removed; 5000 identical occurrences now report 5000; regression tests for body+body, body+table+header collisions and endpoint counts (`test_docx.py`, `test_mask_file.py`) | — | done |
| F-04 | 3.7 malformed uploads → 500, no limits | high | already fixed | `validate_docx_package` (`docx_validation.py`) checks OPC/ZIP structure, entry count, uncompressed size, compression ratio and encryption; endpoints answer 413/415 with Russian details; text/keys limits (`MAX_TEXT_CHARS`/`MAX_KEYS_CHARS`) → 413. Tests: `test_docx_validation.py` + endpoint tests. Front is not a trust boundary (backend enforces everything) | three-tier enforcement (proxy) when a reverse proxy exists | 6 |
| F-05 | 3.10 unused keys in HTTP header | high | already fixed | `/api/unmask-file` now returns a ZIP (restored `.docx` + `<base> (отчёт о демаскировке).json` manifest); `X-Unused-Keys` removed; front unpacks via `unpackRestoreArchive` (tests in mask-archive.test.ts, test_unmask.py) | — | done |
| F-06 | 3.14 TLS bypasses + root frontend container | high | partially fixed | front prod image now runs as `node` (`USER node` + chowned copies); compose prod publishes the backend port loopback-only; TLS build bypasses kept **by maintainer decision** (mirrors ReportCollector; prod Node runtime verifies TLS) | replace build bypasses with a trusted CA when available | 2 |
| F-07 | 3.3 multiple full copies of a file in memory | medium | already fixed | proxy responses stream (no `arrayBuffer` copy), job inputs/results are spooled files and staged uploads with a byte counter; Phase 5 OPC transformer replaces python-docx paragraph traversal; remaining XML part-level DOM is documented in `docs/DOCX_SUPPORT.md` | Phase 7 prefilter + thread-local masker bound per-paragraph cost | done |
| F-08 | 3.4 long operations over a single HTTP request | medium | already fixed | durable job API + worker (ADR-0002), threshold redirect to 202, bounded per-route timeouts, UI polling with backoff; global 0/0 timeouts and maxDuration=3600 removed | — | done |
| F-09 | 3.9 masked_entities carries raw PII to the client | medium | already fixed | `MaskResponse{text, stats{total, occurrences_by_label, unique_by_label}, keys}` — raw values/offsets no longer leave the API; front consumes the aggregates (`statsFromAggregate`) | — | done |
| F-10 | 3.15 unknown labels accepted silently | medium | already fixed | `resolve_labels()` validates against `DEFAULT_LABELS` in the Settings validator (startup fail-fast) and the masker constructor; duplicates/unknown raise `LabelsValidationError` | — | done |
| F-11 | 3.6 DOCX traversal / silent skipped parts | medium | already fixed | Phase 5: full OPC-aware namespace-independent transformer covers all header/footer variants, footnotes, endnotes, comments, tracked changes, text boxes, content controls, properties, alt-text and hyperlink targets; `X-Docx-Unsupported-Parts` warning removed in favor of strict blocking errors. `docs/DOCX_SUPPORT.md` rewritten | — | done |
| F-12 | 3.8 no template/profile subsystem | medium | already fixed | ADR-0001 implemented a profile registry (historical); **Phase 4 superseded it** with one immutable config + validated exception files, `labels.yaml` catalogue, `GET /api/labels`, generated TS catalogue. Keys `Профиль` marker retained as ignored legacy metadata; `MASK_LABELS` removal kept | — | done |
| F-13 | 3.12 CI topology / branch protection | medium | partially fixed | CI now has explicit gates (back ≥80% branch coverage, front 65% line coverage via `scripts/check-coverage.mjs`, `production` needs config+back+front, boots `back`+`worker`+`front`, asserts the smoke spec list) and triggers on `main`/`dev`; **branch ruleset + required check `CI required` remain a GitHub-side action** (origin currently holds only `feat/demasking`, `main`/`dev` are local) | push `main`/`dev`, make `dev` default, configure ruleset + required check (maintainer) | 7 |
| F-14 | 3.11 docs contradicted implementation | medium | already fixed | fixed in stage 0 (`docs/sync-toolchain`): Granian vs uvicorn, Python 3.15, `link` label, keys validation 422, stale commands/comments | — | done |
| F-15 | 3.13 rerender-smoke.spec.ts not in smoke project | low | not reproduced | `playwright test --project=smoke --list` → **21 tests in 3 files**, `rerender-smoke.spec.ts`, `smoke.spec.ts`, `health.spec.ts` all included (unanchored `testMatch` matches the substring); CI's `--list` step now asserts all three files are discovered so a rename cannot silently drop out | none; keep the listing check in CI | 7 |
| F-16 | 3.1 Python version mismatch | — | already fixed | maintainer decision (2026-11-22): Python **3.15** everywhere; CI `python-version` fixed to 3.15; `uv sync --locked` verified green on 3.15 (and proven failing under `UV_PYTHON=3.14` before the fix) | — | done |

| F-16 | 3.x FIO false negatives | medium | already fixed | `NamesExtractor` fails on out-of-vocabulary surnames (`Поповцев`, `Старухин`, incl. two-part `Старухин Александр`) and on role-prefixed names (`Секретарь Т.Ф. Гилязов` → role glued as surname); detector relaxes the surname guard for verified-given-name FIOs, recovers initials+surname / strict name pairs from the text, and excludes roles/greetings/toponyms/common nouns. Live `/api/mask` round-trip + regression tests | — | done |

| F-17 | 3.x durable-job lifecycle correctness | high | already fixed | Phase 2 (ADR-0003): staged atomic enqueue, `SKIP LOCKED` claim with fencing token/worker_id, independent heartbeat, cooperative cancellation (`cancelling` + checkpoints), classified retry/backoff, active-row-safe TTL/GC, immutable fenced result publication, graceful drain, UI Cancel + session resume. 187 back tests incl. failure-injection (stale token, cancellation without publication, long blocking operation across lease intervals) | — | done |

| F-18 | 4.x result cache | medium | already fixed | Phase 3: versioned HMAC cache keys, artifact-reference entries (no large Valkey blobs), hash-verified hits, TTL/GC, outage fallback, PostgreSQL dedupe, sync+job paths, failed/cancelled never cached. 206 back tests incl. real-Valkey integration | — | done |

| F-19 | 5.x one masking configuration / exception files | medium | already fixed | Phase 4: profiles & `profile_id`/`GET /api/profiles` removed; `labels.yaml` single catalogue without `order` + `GET /api/labels` + generated TS (`gen:labels`, CI freshness); all lexicons/exceptions in typed YAML with strict startup validation, digest→cache invalidation, `validate_config` CLI, private RO-mounted `exceptions.yaml`; legacy marker compatibility. 205 back tests incl. exception behavior/config validation | — | done |

| F-20 | 6.x OOXML coverage / unsafe partial results | high | already fixed | Phase 5: `back/src/masking/ooxml/` OPC inventory by QName/URI, secure XML parsing, text-node rewrite of every supported part, byte-for-byte copy of unchanged entries, strict 422 for embedded objects/bad XML, no warning-only behavior. `test_ooxml.py` synthetic fixtures (default ns, >64 KiB, shared parts, round-trip) | — | done |
| F-21 | 7.x sync/job result contract split | medium | already fixed | Phase 5: one versioned `manifest.json` for mask_file/unmask_file from sync and worker (statistics, coverage, versions, cache), frontend requires/validates it, text-job DTO = canonical response, `X-Masked-*` headers removed, `Cache-Control: no-store` forwarded. Parity test + e2e | — | done |
| F-22 | 8.x API error contract / proxy timeouts | high | already fixed | Phase 6 (§8.1/§8.4): stable `{code, detail, request_id}` + `X-Request-ID`, sanitized handlers (no PII/paths/tracebacks), canonical `timeouts.ts` route→timeout map (15 s JSON/job, 120 s upload, 300 s result, 20 min client wait), incoming abort signal forwarded, no unbounded undici timeouts. `test_errors.py` + `timeouts.test.ts` | — | done |
| F-23 | 8.5/10 streaming & event-loop safety | high | already fixed | Phase 6 (§8.5/§10): Next multipart streamed unchanged (`duplex: "half"`), backend bounded staged upload with real byte counter + path validation + `copy_from` spool, `FileResponse` results, separate `run_cpu`/`run_blocking` pools, incremental `lxml.iterparse` paragraph count, `/ready` + `/metrics`, Pydantic cross-field validators. 260 back tests / 88% branch | — | done |
| F-24 | 8.2 OpenAPI → TypeScript types | medium | deferred | Maintainer decision (2026-11-24): «поместим в TODO». No generator/CI freshness gate yet; handwritten DTOs remain. Tracked explicitly in `TODO.md` → «Deferred by maintainer decision» | — | deferred |
| F-25 | Phase 8 stress: worker died on PostgreSQL restart | high | already fixed | Isolated CI-stack drill killed the worker with an uncaught `OperationalError` from the poll loop. `Worker._claim_safe` / `_sweep_safe` and `Heartbeat._run` now catch transient DB errors (type-only logging) and retry with bounded poll backoff; fencing still protects in-flight jobs. Regression tests `test_worker_poll_survives_transient_db_error`, `test_heartbeat_survives_transient_db_error`; re-run: worker stayed up, `/ready` recovered, in-flight job succeeded | — | done |
| F-26 | docProps fields merged into one fake entity | high | already fixed | Local DOCX had `dc:creator` and `cp:lastModifiedBy` with the same FIO; both standalone refs were concatenated into one container, so detection produced the key «Сергей ЕвтушенкоСергей» spanning the field boundary. `_containers` now creates one container per standalone ref; `DOCX_TRANSFORMER_VERSION` bumped to 2 so cached bad results are invalidated. Regression `test_ooxml.py::test_metadata_fields_do_not_form_cross_field_entities`; verified on the reported file with a fresh cache key | — | done |

## Stage plan (mapping to the request's §13)

1. ✅ Этап 0 — docs/toolchain sync (branch `docs/sync-toolchain`, merged into `dev`).
2. ✅ Этап 1 — baseline + this matrix (branch `docs/readiness-baseline`); `back/benchmarks/` + `docs/PERFORMANCE.md`.
3. ✅ Этап 2 — P0 fixes: F-03 (occurrence counting), F-04 (upload validation 413/415), F-05 (manifest instead of header), F-06 (non-root front, loopback backend port; TLS bypasses accepted by decision), F-09 (aggregates), F-10 (label validation), formal span-overlap policy + O(n log n) merge. Six task branches merged into `dev`.
4. ✅ Этап 3 — profile/template registry (F-12) with ADR-0001, keys v2 + legacy reader, `GET /api/profiles`, optional `profile_id`.
5. ✅ Этап 4 — worker/job execution + streaming: ADR-0002 accepted; SQLite WAL store (исторически) + private spool; `/api/jobs` contract; threshold redirect (20 KB / 1 MB / 200 paragraphs); long-lived warm worker container; `run_cpu` executor; bounded/streaming proxies; job UI polling. ReportCollector's lease/queue design was the reference; its Postgres/Redis were replaced by SQLite per the maintainer decision — **superseded by ADR-0003 (Phase 1 заменил SQLite на PostgreSQL)**.
6. ✅ Этап 5 — (historical) DOCX coverage + frontend decomposition (F-11), superseded by Phase 5's full OPC transformer + common manifest (F-20/F-21).
7. ⏸ Этап 6 — observability/deployment/operations — **deferred** by maintainer (2026-11-23); tracked in `TODO.md`. Minimal `GET /ready` + `GET /metrics` and `X-Request-ID` were nevertheless delivered as part of Implementation Brief Phase 6 (§8.1/§10); the full metrics/logs/healthcheck-rollout/hardening scope stays here.
8. ✅ Этап 7 — final docs/CI (the stage-7 part that does not depend on stage 6): short root `AGENTS.md` index + `back/`/`infra/` guides, `docs/API.md` / `docs/MASKING_CONFIGURATION.md` / `docs/PERFORMANCE.md`, CI gates (front coverage, worker in prod smoke, explicit spec listing, `production` needs prerequisites). Branch ruleset + required check remain external; final clean-install/prod verification on target hardware is tracked in `TODO.md`.
9. ✅ Implementation Brief Phase 6 — API contracts, timeouts, streaming, event-loop safety (F-22/F-23); §8.2 OpenAPI types deferred by maintainer decision (F-24).
10. ✅ Implementation Brief Phase 7 — windowed detection + candidate-limited AddrExtractor + DOCX prefilter, measured before/after (F-02 closed); infra stand moved to the ReportCollector pattern with `just seed`.
11. ✅ Implementation Brief Phase 8 — three-level AGENTS hierarchy (root → area → nested masking/jobs guides), level-3 specs (`ARCHITECTURE`, `API`, `MASKING_CONFIGURATION`, `PERFORMANCE`, `DOCX_SUPPORT`, new `SECURITY`/`OPERATIONS`/`TESTING`, ADR-0004/0005), TODO reduced to genuine deferred work; clean install, empty-DB migration, prod-image build, isolated CI e2e and a local lifecycle drill (F-25 found and fixed). Branch protection remains a GitHub-side setting.
