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
- `windowed.py` — `iter_windows`, `dedupe_boundary_spans` for long-text detection.
- `service.py` — text `mask_document` / `unmask_document` and `mask_text` (back-to-front rewrite).
- `schemas.py` — canonical text DTOs (`MaskResponse` / `UnmaskResponse`, `MaskedEntity`, stats).
- `validate_config.py` CLI — `uv run --directory back python -m src.masking.validate_config` validates config without the app.

## One immutable configuration (no profiles)

- `labels.yaml` is the single canonical label catalogue: sequence = keys-section order, no `order` field (an `order` key fails startup). Published via `GET /api/labels`; the front catalogue is generated, not hand-maintained.
- Lexicons (`config/masking/lexicons/*.yaml`) plus `always_mask`/`never_mask` exceptions are literal typed YAML loaded into one validated frozen snapshot at startup; no hot reload. Invalid YAML, duplicate/conflicting/unknown entries → `MaskingConfigError` at startup.
- Private `exceptions.yaml` is gitignored and mounted read-only; only synthetic values go in `exceptions.example.yaml`. Never commit real PII.
- `never_mask` suppresses only an exact whole detected span (never a larger overlapping one); `always_mask` spans pass the normal overlap policy and key numbering, and no detector/prefilter may suppress them.
- `config_digest()` feeds `MASKING_CONFIG_DIGEST` in the cache key: any config change invalidates cached results. Never log the digest inputs.
- `MASK_LABELS` was removed with a loud startup error; setting it fails startup. `profile_id` / `GET /api/profiles` are gone.

## Detector contracts & order

- `DETECTOR_ORDER = (email, link, phone, person, address, organization)` is the stable run order and the final tie-break in span sorting. Adding a label = one `_find_*` provider + one `DETECTORS` entry + one `labels.yaml` row (regenerate front catalogue).
- natasha 1.6: use `extractor(text)` for all matches; `.find(text)` returns only the first `Match` (and is iterable → unpacks to ints). No `natasha.OrganisationsExtractor`; `AddrExtractor` yields one `AddrPart` per component, not one whole address.
- `NamesExtractor` over-fires: keep a match only with a given name **and** surname/patronymic that are plausible proper names (`_is_name_token`), with surname pass `_surname_accepted` (role/greeting/топоним/common-noun exclusions). Initials/name-pair fallbacks recover split FIOs. Never drop these guards: `из Сторон`, `Оператор ЭДО`, `Привет Иван` must stay unmasked.
- Span overlap policy: sort by (start asc, length desc, score desc, detector order); keep a span unless it overlaps a kept one. `_merge_spans` is an O(n log n) sweep, not the old quadratic `any()` scan.

## Keys YAML (reversibility)

- Placeholders are `[LABEL_N]`, numbered per label in document order. `MaskKeys` dedupes by **exact** text: identical repeats reuse one placeholder/key; distinct wordings (case forms of one org) stay separate so restoration is lossless.
- Allocate numbers left-to-right before the back-to-front rewrite in `mask_text`, else the last entity gets `_1`. Unmask is a plain placeholder→value `str.replace`, so order does not matter; `placeholders_from_document` reverses a parsed keys file.
- Keys YAML uses `yaml12` (py-yaml12), not PyYAML 1.1 (which mangles quotes, e.g. `АО "Росхим"`). Writers emit the legacy `Профиль: russian-default` marker; readers accept it, foreign marker values and envelope-less legacy files, ignoring the marker as metadata. No `версия`/`версия_профиля` keys (retired v2 envelope). Section titles come from `labels.yaml`; empty sections are omitted.

## OOXML / DOCX (OPC package level, not python-docx runs)

- `ooxml/package.py` builds a namespace-URI inventory from `[Content_Types].xml` + relationship parts (QName/URI, never hardcoded prefixes), parses with `defusedxml.lxml` (no DTD, entities or network), groups `w:t` nodes by nearest `w:p`, rewrites slices back-to-front. An entity spanning nodes becomes **one** placeholder. Only modified ZIP entries are re-serialized; all others (images, styles, numbering) copied byte-for-byte.
- Standalone text refs (docProps fields, external relationship targets, paragraph-less text) are **separate containers — one field each**, never concatenated. Otherwise `dc:creator` + `cp:lastModifiedBy` with the same FIO form a fake cross-field span and a nonsensical key (`«Иван ПетровИван»`). Regression: `test_ooxml.py::test_metadata_fields_do_not_form_cross_field_entities`.
- All text-bearing parts are processed: body/tables, all header/footer variants, footnotes/endnotes, comments, `w:delText`, `w:txbxContent`, `a:t`, `w:sdt`, docProps core/app, `wp:docPr` alt text, external relationship `Target`. There is no `X-Docx-Unsupported-Parts` plumbing.
- Fail closed with a clear 422 (`OoxmlPackageError`): embedded OLE/`*.bin`, unsafe/malformed XML, oversized parts. Raster pixels are the one documented non-text limit (`raster_image_ocr: not_performed`) — copied, not a blocker.
- `manifest.json` (`ooxml/manifest.py` + `archives.py` + `jobs/operations.py`) is the single source for sync and job file results: versioned `statistics`, `coverage`, input hash, output names, `cache`. The frontend requires and validates it; no `X-Masked-*` headers. Text jobs return the canonical DTOs in `result.json`.
- Flat ZIPs: `mask-file` = masked .docx + keys .yaml + manifest; `unmask-file` = restored .docx + manifest with `statistics.unused_keys`. Never reintroduce `X-Unused-Keys` (Cyrillic PII in headers).

## Long text, prefilter, masker lifetime

- `get_masker()` is one masker **per thread** (`threading.local()`): natasha extractors hold mutable yargy state, not proven thread-safe. Each thread pays ~110 MB once — keep `JOBS_CONCURRENCY` conservative. Never build a `RuleBasedMasker` per request (tests do so deliberately).
- `windowed.py::iter_windows` yields bounded overlapping windows aligned to structural boundaries, never cutting a token; `_detect_long` spans are re-based to absolute offsets and exact boundary duplicates removed before `_merge_spans`; `checkpoint` runs between windows. Below `min_chars`, a single window preserves the old behavior.
- `_find_addresses` feeds `AddrExtractor` only sentences with an anchored address marker (abbreviation dots are not sentence ends). Whole-document address detection caused the recorded 92.8 s / 7.3 GB baseline — see `docs/PERFORMANCE.md`.
- OOXML containers call `detect(text, prefilter=True)`: cheap per-detector preconditions skip impossible detectors but can never suppress `always_mask`. `_suspicious_positions` is a bounded `lru_cache(256)`.

## Required tests

`test_rule_based.py`, `test_keys_v2.py`, `test_config.py`, `test_docx.py`, `test_ooxml.py`,
`test_windowed.py`, `test_mask_file.py`, `test_unmask.py` (plus `test_mask.py`,
`test_mapping.py`, `test_labels_api.py`, `test_docx_validation.py`). Offline/deterministic,
`filterwarnings=error`, ≥80% branch coverage in CI. Synthetic OPC fixtures, mask→keys→unmask
round-trips and sync/job manifest parity live in `test_ooxml.py`.
