# ADR-0005: One masking configuration and versioned result cache

- Status: **accepted** (Phases 4 and 3, 2026-11-24)
- Supersedes: ADR-0001's profile/template registry (the keys-file `Профиль`
  marker stays as ignored legacy metadata). Cache ownership builds on
  ADR-0003's PostgreSQL substrate.
- Related: `docs/MASKING_CONFIGURATION.md`, `docs/ARCHITECTURE.md`,
  `back/src/masking/AGENTS.md`, `back/src/jobs/AGENTS.md`

## Context

The former profile abstraction multiplied configuration states: labels and
titles were duplicated in Python and in the frontend, lexicons were hardcoded,
and cache invalidation had no single identity. Multi-profile runtime selection
added surface without changing the deterministic algorithms. At the same time,
recomputing masking for identical inputs on every request was expensive, but a
cache that stores raw inputs, keys or full documents would create a second PII
store and a correctness hazard.

## Decision

- **One immutable configuration snapshot.** `back/config/masking/labels.yaml`
  is the single canonical label catalogue (sequence = Russian keys-section
  order, no `order` field). Lexicons and `always_mask` / `never_mask`
  exceptions are literal typed YAML under `config/masking/lexicons/` and the
  private `exceptions.yaml`. Invalid, duplicate, conflicting or unknown entries
  fail startup with `MaskingConfigError`; there is no hot reload.
  `profile_id`, `GET /api/profiles` and `MASK_LABELS` are removed; setting
  `MASK_LABELS` fails loudly. `GET /api/labels` publishes the catalogue and
  the frontend copy is generated (`gen:labels`, CI freshness gate).
- **Two distinct exception semantics.** `always_mask` values are force-masked
  through the normal overlap/key-numbering policy; `never_mask` suppresses
  only an exact whole detected span, never a larger overlapping one. Neither
  is an arbitrary regex/code extension.
- **Keys compatibility.** `yaml12` preserves quoting. Writers emit the legacy
  `Профиль: russian-default` marker followed by Russian sections; readers
  accept that shape, foreign marker values and envelope-less legacy files.
  No `версия`/`версия_профиля` keys are written or read.
- **Versioned HMAC cache.** Valkey keys are
  `docmasker:result:v{N}:{operation}:{HMAC-SHA256}` over a canonical fingerprint
  of exact input/keys bytes, normalized output name and every version/digest
  that can change output (ruleset, config digest, manifest, keys format, DOCX
  transformer, cache namespace). Valkey stores only compact artifact
  references; large blobs stay in the private hash-verified spool. An outage
  or corrupt artifact is a miss, never a 500. Writes happen only after a fenced
  `succeeded` transition; failed/cancelled jobs are never cached. Identical
  active jobs dedupe in PostgreSQL, not via a Valkey lock.

## Consequences

- One config identity makes cache invalidation deterministic; bump the version
  settings whenever output semantics change.
- The private `exceptions.yaml` is gitignored, excluded from the image and
  mounted read-only; CI uses the synthetic example. Real PII must never enter
  either file.
- Old keys files remain restorable; no user migration step is required.
- The cache never becomes a second PII store: no raw input, keys, entity values
  or filenames in key names, values or logs.
- Verification: `test_config.py`, `test_keys_v2.py`, `test_cache.py`,
  `test_jobs_worker.py`, `test_settings_validation.py`; CLI
  `uv run --directory back python -m src.masking.validate_config`.
