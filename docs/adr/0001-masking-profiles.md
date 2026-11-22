# ADR-0001: Masking profiles, detector registry, and keys schema v2

- Status: accepted (2026-11-23)
- Stage: production-readiness stage 3 (finding F-12 / request §3.8, §5)

## Context

DocMasker masks PII with a deterministic pipeline. Adding one entity type
today requires synchronized edits in unrelated places:

- `back/src/masking/constants.py` (`DEFAULT_LABELS`, `LABEL_TITLES`, `ALWAYS_MASK_ORGANIZATIONS`),
- `back/src/masking/rule_based.py` (a new `_find_*` provider, hard-coded order),
- `back/src/masking/mapping.py` (keys schema — Russian sections),
- `front/src/shared/api/mask.ts` (`KEYS_SECTION_TITLES`, `KEYS_TITLE_LABELS`),
- `front/src/shared/mask-stats.ts` (`LABEL_LABELS`, `LABEL_TITLES` duplicates).

`MASK_LABELS` is an env var that only narrows the active label set — it is not
a template system. `ALWAYS_MASK_ORGANIZATIONS = ("Росхим",)` is a
project-specific rule hard-coded in the algorithm. The request (§5) asks for a
versioned, typed profile model with a detector registry, safe configuration,
and a keys format that survives profile evolution — without breaking existing
keys files.

Constraints from the request and the repo:

- No arbitrary user code from configuration (no eval, no dynamic imports, no
  user-supplied regex in this stage).
- Single-tenant deployment; no CRUD/multi-user requirements yet.
- Unmask must never depend on the current profile contents: the keys file is
  the only restore-time authority.
- Backend stays a modular monolith; no new services.

## Options considered

1. **Keep the env-var label override, document it as "the template system".**
   Cheapest, but does not solve extensibility: new label still touches five
   files, org always-mask list stays hard-coded.
2. **Full dynamic profile CRUD (DB-stored profiles, user-editable rules).**
   Over-engineered for single-tenant, adds auth requirements the product does
   not have, and invites unsafe configuration.
3. **Versioned built-in profiles in code + typed config, detector registry,
   keys v2 with profile metadata and a legacy reader.** Extensible by data
   (a new profile = a config entry; a new label = one detector + one registry
   entry), no unsafe surfaces, backward compatible.

## Decision

Option 3, with the following concrete shape.

### Profile model (backend, `back/src/masking/profiles.py`)

- `LabelMeta`: stable machine `id` (e.g. `person`), Russian display `title`
  (keys section), plural forms for the UI, `order`.
- `MaskingProfile`: `profile_id`, `version`, `display_name`, `labels`
  (ordered `LabelMeta` list), `always_mask_organizations` (moved out of the
  algorithm), `enabled_detectors` (subset of the registry ids).
- A profile validates as a whole: unknown detector ids, unknown labels,
  duplicate ids/titles, or a label whose title collides with another label's
  title are construction errors. Immutable (`frozen`), cached per
  `(profile_id, version)`.
- The built-in profile `russian-default/1` reproduces today's behavior exactly
  (labels, titles, order, detectors, `Росхим` always-mask list). It is the
  single default. **`MASK_LABELS` is removed entirely (maintainer decision):**
  a configuration that still sets it fails startup with a loud Russian error
  pointing at profiles — no silent subset filter, no deprecation window.

### Detector registry (`back/src/masking/detectors.py`)

- A `Detector` is a thin callable wrapper over the existing provider methods:
  `detector_id`, `label`, `run(text, ctx) -> Iterable[Entity]` where `ctx`
  carries the shared `MorphVocab`/extractors. No new detection logic — the
  bodies move from `RuleBasedMasker._find_*` unchanged.
- A module-level ordered registry maps `detector_id -> Detector`. Profiles
  select detectors by id; the orchestrator runs them in the profile's order.
- `RuleBasedMasker` remains the facade (constructor and `detect()` API
  unchanged) so tests, DOCX service, and the benchmark suite keep working.

### Keys schema v2 (`back/src/masking/mapping.py`)

- v2 file = one YAML mapping with an explicit envelope:

  ```yaml
  версия: 2
  профиль: russian-default
  версия_профиля: 1
  Физлица:
    - Иван Петров
  ```

- Writer: masks produced by a profile write v2 (envelope + Russian sections).
- Reader: a file without the envelope is treated as legacy v1 and parsed
  exactly as today (sections → placeholders). A file with `версия: 2` is
  parsed with envelope validation (unknown sections still rejected, so a
  profile mismatch fails loudly instead of restoring garbage).
- Unmask stays profile-independent: placeholders are `[LABEL_N]`, titles are
  resolved through the profile-independent title→label table; a v2 file from
  any profile restores without that profile being loaded.

### API

- `GET /api/profiles` — list of profiles (id, version, display name, labels
  with titles/order). Read-only; no profile mutation endpoint (single-tenant;
  profiles are code-configured).
- Mask endpoints accept an optional `profile_id` (default:
  `russian-default`). The response `stats` and keys v2 record the profile.

### Frontend

- Stats module consumes label metadata from the API response (labels are
  already aggregate-only since F-09). `KEYS_SECTION_TITLES`/`KEYS_TITLE_LABELS`
  remain as the *keys-format* constants (they mirror `LABEL_TITLES` of the
  default profile and are validated against it by tests) — the full
  label-metadata list moves to the API. Tooltip plural forms stay client-side
  (presentation concern) but fall back to the generic form for unknown labels.

## Consequences

Positive:

- New profile = one typed config entry; new label = one detector + one
  `LabelMeta` + one detector registration. No frontend constant chain edits.
- `ALWAYS_MASK_ORGANIZATIONS` becomes per-profile configuration.
- Keys carry provenance (which profile produced the file) and remain
  backward/forward compatible through the legacy reader.
- The request's extensibility criterion (§5.5) is testable: an integration
  test masks/unmasks with an alternative profile without touching the
  orchestrator.

Negative / accepted costs:

- Two keys formats exist indefinitely (reader keeps v1); writers emit v2.
- The detector registry adds a level of indirection over direct method calls.
- `MASK_LABELS` is removed with no compatibility window (maintainer decision):
  deployments that set it must delete the variable when upgrading.
- Frontend keys-section constants still mirror the backend titles until the
  labels-metadata API is consumed everywhere (partial in this stage; the
  validator stays in sync via a cross-language test vector).

## Verification plan

- Profile validation unit tests (duplicates, unknown detector/label, title collisions).
- Legacy v1 keys restore test vectors (existing suite must pass unchanged).
- v2 round-trip: mask → keys v2 → unmask restores exactly.
- Alternative-profile integration test: profile with fewer detectors masks a
  document, keys restore on the default profile.
- `GET /api/profiles` contract test; frontend typecheck/unit tests.
