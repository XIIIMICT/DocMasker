# Keys, profiles and extension recipes

The "template system" of DocMasker (ADR-0001, finding F-12) is **versioned
built-in masking profiles in code** — not env-var label overrides and not a
user-editable rules database. This document owns the keys-file format and the
how-to-extend recipes; the accepted decisions and rationale live in
`docs/adr/0001-masking-profiles.md`, the endpoint in `docs/API.md`.

## Keys file format

Masking is reversible: every distinct `(label, exact text)` becomes one
placeholder `[LABEL_N]` (number per label, in document order), and the original
strings are written to a YAML file. Restoring is a plain placeholder→value
substitution, so the exact case form of each occurrence comes back. The same
string repeated reuses one number/one key; two wordings of one organisation
(`АО «Росхим»` vs `Акционерного общества «Росхим»`) stay **two** keys — that
is what makes restoration lossless.

Serialization uses **`yaml12` (`py-yaml12`, YAML 1.2)**, not PyYAML 1.1 — the
latter would mangle quoting (`АО "Росхим"` must stay quoted). Implementation:
`back/src/masking/mapping.py`.

### Current (written by every mask)

```yaml
Профиль: russian-default
Физлица:
  - Иван Петров
  - Мария Сидорова
Номера телефонов:
  - +7 999 123-45-67
Email:
  - user@example.ru
Ссылки:
  - https://example.ru/page
Организации:
  - АО «Росхим»
Адреса:
  - г. Москва, ул. Ленина, д. 1
```

- `Профиль` (capital `П`) is a **metadata marker** recording the producing
  profile. **No `версия` / `версия_профиля` keys** are written or read: the
  format has one current shape and we always act on the latest. Those version
  keys existed only in a pre-prod build and are now unknown sections (they
  fail validation) — see `docs/adr/0001-masking-profiles.md`.
- When no profile is supplied, `russian-default` is written (and a file
  **without** the marker also restores as the default profile).
- Restoring never depends on the marker: section titles resolve against the
  profile-independent `registered_titles()` union, so a file from another
  profile still restores.
- **Empty sections are omitted.** `keys` is an empty string when nothing was
  masked.
- Numbers restart at 1 within each section: the n-th value of section `Физлица`
  is `[PERSON_n]`, of `Номера телефонов` → `[PHONE_n]`, etc.

### Legacy (envelope-less, read-only)

A file with only sections (no `Профиль`) is parsed exactly as before and treated
as produced by the default profile:

```yaml
Физлица:
  - Иван Петров
Организации:
  - АО «Росхим»
```

Writers emit the current format only. Both shapes coexist by design (the reader
keeps the envelope-less one).

### Validation (both sides, mirrored)

`validate_keys_yaml` (`back/src/masking/mapping.py`) rejects: empty/malformed
YAML, unknown sections (including the retired version keys), non-list sections,
empty values, **duplicate values within a section**, and a **duplicated section
name**. It raises `KeysValidationError` → 422 with a Russian `detail`. The
`Профиль` marker is accepted and stripped before section checks.

The frontend mirrors all of it in
`front/src/shared/api/mask.ts::validateKeysYaml`, run **on every keystroke**
(derived from `demaskKeys`, not on submit). Both the module and the page import
the shared `KEYS_SECTION_TITLES` list; an invalid editor gets `aria-invalid` and
a red inline message, and the request is never sent. The client constants must
stay in sync with the backend's `profiles.py::registered_titles` union and with
the `Профиль` marker — cross-language agreement is enforced by tests
(`test_keys_v2.py`, `test_profiles.py`, front `__tests__/mask.test.ts`).

File extension: `.yaml` on **both** load and download; `.yml` is also accepted
on read. The keys file is downloaded alongside the masked document as a
separate artifact (`docs/API.md`).

## Profile model (backend, `back/src/masking/`)

| Module | Role |
|---|---|
| `profiles.py` | `LabelMeta{id, title, order}`, `MaskingProfile{profile_id, version, display_name, labels, detectors, always_mask_organizations}`; registry `PROFILES`, `get_profile`, `list_profiles`, `registered_titles` |
| `detectors.py` | stable-id registry mapping a detector id → the `RuleBasedMasker._find_*` provider + reported label |
| `constants.py` | `DEFAULT_LABELS`, `ALWAYS_MASK_ORGANIZATIONS` (`("Росхим",)`), `mask_marker` |
| `rule_based.py` | `RuleBasedMasker` facade — `detect()` runs the profile's detectors in order, merges spans, rewrites back-to-front |

Profiles are immutable (`frozen`) dataclasses, validated at construction:
unknown detector ids, duplicate ids/titles, a detector whose label has no
`LabelMeta`, or always-mask without an `organization` label all raise
`ProfilesValidationError` — an invalid profile is a **startup error**, never a
silent degradation. The built-in `russian-default/1` reproduces the pre-profile
behavior exactly (labels, titles, order, detectors, `Росхим` always-mask list).
Maskers are cached per profile (`get_masker_for`, `lru_cache`) because natasha's
`MorphVocab` is expensive to build — see `back/AGENTS.md`.

`MASK_LABELS` was **removed** with a loud startup error (maintainer decision):
a deployment that still sets it must delete the variable when upgrading. There
is no deprecation window and no silent subset filter. `GET /api/profiles` is
read-only; mask endpoints accept an optional `profile_id`. The UI has **no
profile selector** (maintainer decision — "UI без изменений").

## Recipes

### Add a new entity label

1. `back/src/masking/rule_based.py` — one `_find_<label>` provider returning
   `Entity` spans.
2. `back/src/masking/detectors.py` — one `Detector` registry entry (id →
   provider + label).
3. `back/src/masking/profiles.py` — one `LabelMeta` in the profile's `labels`
   (stable id, Russian section title, order).
4. `back/src/masking/constants.py` — add the id to `DEFAULT_LABELS` if it is
   part of the default profile.
5. Frontend `front/src/shared/api/mask.ts` — add the title to
   `KEYS_SECTION_TITLES` / `KEYS_TITLE_LABELS` and teach **both** validators
   about it; `front/src/shared/mask-stats.ts` — plural/title presentation.
6. Tests: detector unit case, keys v2 round-trip, profile validation,
   front validator vector.

Never hard-code a new label into the orchestrator or duplicate the title chain
in a second module — the registry is the single source.

### Add a new masking profile

One config entry in `profiles.py::PROFILES` with a new `profile_id`/`version`,
a chosen subset of detector ids and its own `always_mask_organizations`. The
restoring side needs no change (keys are profile-independent). Verify with the
alternative-profile integration test (mask with profile A, restore on the
default). `GET /api/profiles` will list it automatically.

### Add a keys metadata key

Teach **both** `validate_keys_yaml` (backend) and `validateKeysYaml` (frontend)
the new marker and add a round-trip test. Restoring must never depend on
metadata. (Adding `версия`/`версия_профиля` back would need an ADR — they were
removed deliberately.)

### Cache invalidation release checklist (Phase 3)

Cached results are keyed by a canonical fingerprint plus these version/digest
settings (`back/src/jobs/cache.py`). **Bump on every output-affecting change**:

| Setting | Bump when |
|---|---|
| `CACHE_NAMESPACE_VERSION` | key construction or stored cache-entry structure changes |
| `MASKING_RULESET_VERSION` | detection/masking semantics change the output |
| `MASKING_CONFIG_DIGEST` | any exception/lexicon file changes (Phase 4 wires the real digest) |
| `RESULT_MANIFEST_VERSION` | result archive/manifest contract changes |
| `KEYS_FORMAT_VERSION` | keys YAML format changes |
| `DOCX_TRANSFORMER_VERSION` | OOXML transformation changes |
| `CACHE_KEY_SECRET` | intentional full invalidation (rotating the secret) |

TTL alone is not an invalidation strategy. Never log a full cache key or
fingerprint (they are HMAC'd exactly so predictable PII cannot be guessed).

### Deferred (do not implement without an ADR / maintainer decision)

- User-editable profiles, CRUD endpoints, DB-stored rules (would require auth —
  `TODO.md`).
- User-supplied regex / dictionaries with limits and timeouts.
- Generated TypeScript types from OpenAPI (single source of truth), a stable
  error envelope (`code`, `detail`, `request_id`), dropping the manual
  `KEYS_SECTION_TITLES` duplication.
- A frontend profile-selection UI.
