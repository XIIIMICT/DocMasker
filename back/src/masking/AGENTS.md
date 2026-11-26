# masking/ — masking & OOXML hot-path guide

Level-2 guide for the deterministic Russian-text / OOXML pipeline. Architecture,
contracts and durable specs live in `docs/` — link, do not duplicate:
`docs/ARCHITECTURE.md`, `docs/MASKING_CONFIGURATION.md`, `docs/DOCX_SUPPORT.md`,
`docs/PERFORMANCE.md`. Shared backend commands and safety rules are in `back/AGENTS.md`.

## Module map

- `rule_based.py` — the single `RuleBasedMasker`: natasha + phonenumbers + regex providers, span merging, `detect(text, prefilter=False)`, `DETECTOR_ORDER`, thread-local `get_masker`.
- `detectors.py` — registry binding stable detector id → label → provider *method name* (`getattr` at run time; import-light, avoids circular import).
- `config.py` — loads/validates/freezes one `MaskingConfig`; `get_config`, `config_digest` (SHA-256 over canonical validated data).
- `mapping.py` — `MaskKeys` placeholder allocation and keys YAML read/write (`yaml12`).
- `ooxml/package.py` — OPC inventory + namespace-aware text-node transformer.
- `ooxml/manifest.py` — the single versioned `manifest.json` builder (mask and unmask).
- `archives.py` — one flat-ZIP builder shared by sync and job paths.
- `docx_service.py` — `mask_docx_in_memory` → (bytes, entities, keys, `CoverageReport`); `unmask_docx_in_memory` → (bytes, unused, coverage).
- `docx_validation.py` — `validate_docx_package(_path)`, `upload_limits`; hard size/part checks before OPC work, incremental paragraph counting.
- `executor.py` — `run_cpu` / `run_blocking` bounded offload pools; never call masking or DB work directly in `async def`.
- `windowed.py` — `iter_windows`, `dedupe_boundary_spans`, `OversizedTokenError` for long-text detection.
- `service.py` — text `mask_document` / `unmask_document` and `mask_text` (validated one-pass join).
- `schemas.py` — canonical text DTOs (`MaskResponse` / `UnmaskResponse`, `MaskedEntity`, stats).
- `validate_config.py` CLI — `uv run --directory back python -m src.masking.validate_config` validates config without the app.

## One immutable configuration (no profiles)

- `labels.yaml` is the single canonical label catalogue: sequence = keys-section order, no `order` field (an `order` key fails startup). Published via `GET /api/labels`; the front catalogue is generated, not hand-maintained.
- Lexicons (`config/masking/lexicons/*.yaml`) plus `always_mask`/`never_mask` exceptions are literal typed YAML loaded into one validated frozen snapshot at startup; no hot reload. Invalid YAML, duplicate/conflicting/unknown entries → `MaskingConfigError` at startup.
- Private `exceptions.yaml` is gitignored and mounted read-only; only synthetic values go in `exceptions.example.yaml`. Never commit real PII.
- `never_mask` suppresses only an exact whole detected span (never a larger overlapping one); `always_mask` spans pass the normal overlap policy and key numbering, and no detector/prefilter may suppress them.
- `config_digest()` covers the whole canonical effective snapshot (schema, ordered labels/titles, lexicons, all `common.yaml` char sets, and each exception's primary/mode/variants), not a partial hand-maintained list. It always feeds the cache key (`CACHE_KEY_SCHEMA_VERSION=2`), even when `MASKING_CONFIG_DIGEST` is pinned manually. Declared match modes are implemented, not ignored: `exact` = case-sensitive literal, `case_insensitive` = case-insensitive literal, `token_sequence` = case-insensitive ordered tokens with arbitrary whitespace. Empty/duplicate variants and literals across entries fail startup. Never log the digest inputs or raw exception values.
- `MASK_LABELS` was removed with a loud startup error; setting it fails startup. `profile_id` / `GET /api/profiles` are gone.

## Detector contracts & order

- `DETECTOR_ORDER = (email, link, phone, person, address, organization)` is the stable run order and the final tie-break in span sorting. Adding a label = one `_find_*` provider + one `DETECTORS` entry + one `labels.yaml` row (regenerate front catalogue).
- natasha 1.6: use `extractor(text)` for all matches; `.find(text)` returns only the first `Match` (and is iterable → unpacks to ints). No `natasha.OrganisationsExtractor`; `AddrExtractor` yields one `AddrPart` per component, not one whole address.
- `NamesExtractor` over-fires: keep a match only with a given name **and** surname/patronymic that are plausible proper names (`_is_name_token`), with surname pass `_surname_accepted` (role/greeting/топоним/common-noun exclusions). Initials/name-pair fallbacks recover split FIOs. Never drop these guards: `из Сторон`, `Оператор ЭДО`, `Привет Иван` must stay unmasked.
- Span overlap policy: sort by (start asc, length desc, score desc, detector order); keep a span unless it overlaps a kept one. `_merge_spans` is an O(n log n) sweep, not the old quadratic `any()` scan.

## Keys YAML (reversibility)

- Placeholders are `[LABEL_N]`, numbered per label in document order. `MaskKeys` dedupes by **exact** text: identical repeats reuse one placeholder/key; distinct wordings (case forms of one org) stay separate so restoration is lossless.
- `mask_text` validates spans first (integer offsets, bounds, non-empty, non-overlapping, value equals source slice) and fails closed with 422 `invalid_span` (`InvalidSpanError`) instead of corrupting text; it allocates numbers left-to-right **before** the single join, else the last entity gets `_1`. Untouched slices and markers go into one list joined once (`_mask_parts`), so output construction is O(length + spans) — see `docs/PERFORMANCE.md` §DM-4. Unmask is ONE compiled longest-first alternation scan (`compile_placeholders` in `mapping.py`): inserted values are opaque and never rescanned, so no cascade, and missing markers stay in place; `placeholders_from_document` reverses a parsed keys file. Masking first fails with 422 `marker_collision` on a source literal equal to a marker it allocates; the collision-safe versioned format is deferred (`TODO.md`).
- Keys YAML uses `yaml12` (py-yaml12), not PyYAML 1.1 (which mangles quotes, e.g. `АО "Росхим"`). Writers emit the legacy `Профиль: russian-default` marker; readers accept it, foreign marker values and envelope-less legacy files, ignoring the marker as metadata. No `версия`/`версия_профиля` keys (retired v2 envelope). Section titles come from `labels.yaml`; empty sections are omitted.

## OOXML / DOCX (OPC package level, not python-docx runs)

- `ooxml/package.py` builds a namespace-URI inventory from `[Content_Types].xml` + relationship parts (QName/URI, never hardcoded prefixes), parses with `defusedxml.lxml` (no DTD, entities or network), groups `w:t` nodes by nearest `w:p`, rewrites slices back-to-front. An entity spanning nodes becomes **one** placeholder. Only modified ZIP entries are re-serialized; all others (images, styles, numbering) copied byte-for-byte. `build_inventory()` materialises only processable parts (media `data is None`); `transform_package_to_file()` processes one XML part at a time and stream-copies unchanged entries to a target path, used by file jobs (P2.11). The sync `transform_package()` bytes API stays for small requests.
- All routes share the staged-upload admission helper: real streamed byte count, hard package validation by path before cache/job, and `finally` cleanup. `MAX_TEXT_CHARS` applies to mask and unmask text; `MAX_KEYS_CHARS` applies to every unmask route including `/api/jobs`. Hostile ZIP entry names and duplicate names are rejected; result ZIP names use `archives.py::safe_stem` (flat, both OS separators, no control/reserved names). Regression tests: `test_docx_validation.py`, `test_archives.py`, `test_mask.py`, `test_jobs_api.py`.
- Standalone text refs (docProps fields, external relationship targets, paragraph-less text) are **separate containers — one field each**, never concatenated. Otherwise `dc:creator` + `cp:lastModifiedBy` with the same FIO form a fake cross-field span and a nonsensical key (`«Иван ПетровИван»`). Regression: `test_ooxml.py::test_metadata_fields_do_not_form_cross_field_entities`.
- All text-bearing parts are processed: body/tables, all header/footer variants, footnotes/endnotes, comments, `w:delText`, `w:txbxContent`, `a:t`, `w:sdt`, docProps core/app, `wp:docPr` alt text, external relationship `Target`. There is no `X-Docx-Unsupported-Parts` plumbing.
- Fail closed with a clear 422 (`OoxmlPackageError`): embedded OLE/`*.bin`, unsafe/malformed XML, oversized parts, and text-bearing parts with no processor (charts/spreadsheets). Raster pixels are the one documented non-text limit (`raster_image_ocr: not_performed`) — copied, not a blocker.
- Coverage is truthful: `processed_parts` includes every inspected supported part (even with zero detections), `processed` carries per-part `text_nodes` counts, and `unprocessed_text_parts` is populated for unhandled text-bearing parts. `manifest.complete` is true only when the supported scope was fully inspected with no unprocessed/rejected text parts. Unknown XML (e.g. `customXml/item*.xml`) is processed generically at the text-node level; `docProps/custom.xml` `vt:lpwstr`/`lpstr`/`bstr` and comment/tracked-change `w:author`/`w:initials` are in the explicit text allowlist. Root relationships resolve from package root, part relationships from their owning part.
- `manifest.json` (`ooxml/manifest.py` + `archives.py` + `jobs/operations.py`) is the single source for sync and job file results: versioned `statistics`, `coverage`, input hash, output names, `cache`. `cache.hit` describes the **current request**, not artifact generation: `archives.py::mark_cache_hit` builds a new ZIP with `hit: true` on sync/job hits and never mutates the shared cached artifact. The frontend requires and validates it; no `X-Masked-*` headers. Text jobs return the canonical DTOs in `result.json`.
- Flat ZIPs: `mask-file` = masked .docx + keys .yaml + manifest; `unmask-file` = restored .docx + manifest with `statistics.unused_keys`. Never reintroduce `X-Unused-Keys` (Cyrillic PII in headers).

## Long text, prefilter, masker lifetime

- `get_masker()` is one masker **per thread** (`threading.local()`): natasha extractors hold mutable yargy state, not proven thread-safe. Each thread pays ~110 MB once — keep `JOBS_CONCURRENCY` conservative. Never build a `RuleBasedMasker` per request (tests do so deliberately).
- `windowed.py::iter_windows` yields bounded overlapping windows aligned to structural boundaries, never cutting a token; `_detect_long` spans are re-based to absolute offsets and exact boundary duplicates removed before `_merge_spans`; `checkpoint` runs between windows. Below `min_chars`, a single window preserves the old behavior. DM-5: when a chunk has no boundary, the window scans forward to the next whitespace up to `DETECTION_MAX_WINDOW_CHARS` (16,384 default); a longer unbroken run raises `OversizedTokenError` (422 `oversized_token`, no input echoed) instead of being cut. Contract and validation: `docs/MASKING_CONFIGURATION.md` §Long-text windowing bounds; cost notes: `docs/PERFORMANCE.md`.
- `_find_addresses` feeds `AddrExtractor` only nominated sentence-sized ranges. `_address_candidate_re()` builds every marker from `lexicons/addresses.yaml` (full words, bare/dotted abbreviations, `адрес:`, postcode) with Unicode word boundaries — no parallel Python list. A match only nominates a range; it never masks. Abbreviation dots are not sentence ends and a wrapping newline is a soft boundary. Whole-document address detection caused the recorded 92.8 s / 7.3 GB baseline — see `docs/PERFORMANCE.md`; the config contract and benchmark are in `docs/MASKING_CONFIGURATION.md` §Address candidate prefilter.
- OOXML containers call `detect(text, prefilter=True)`: cheap per-detector preconditions skip impossible detectors but can never suppress `always_mask`. The address precondition reuses the same config-driven matcher, so short fragments without digits still reach the extractor and one-letter abbreviations inside unrelated words do not match. `_suspicious_positions` is a bounded `lru_cache(256)`.
- Worker jobs thread `checkpoint` through `execute()` → `mask_docx_in_memory()` → `transform_package()` → `detect()`: before each processable OPC part and between long-text windows. A cancellation stops work at the next coarse boundary, never publishes/caches a result, and stale workers never clean up artifacts without DB confirmation. Sync routes pass no checkpoint.

## Required tests

`test_rule_based.py`, `test_address_prefilter.py`, `test_keys_v2.py`, `test_config.py`, `test_docx.py`, `test_ooxml.py`,
`test_windowed.py`, `test_mask_file.py`, `test_unmask.py` (plus `test_mask.py`,
`test_mapping.py`, `test_labels_api.py`, `test_docx_validation.py`). Offline/deterministic,
`filterwarnings=error`, ≥80% branch coverage in CI. Synthetic OPC fixtures, mask→keys→unmask
round-trips and sync/job manifest parity live in `test_ooxml.py`.
