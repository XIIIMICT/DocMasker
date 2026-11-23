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

### v2 (current, written by every mask)

```yaml
версия: 2
профиль: russian-default
версия_профиля: 1
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

- The **envelope** (`версия`, `профиль`, `версия_профиля`) is metadata that
  records provenance; it is **never** the restore authority. Only the listed
  version `2` is accepted; a foreign version fails loudly.
- Section titles are the profile's Russian `LabelMeta.title` values. Titles
  resolve against the profile-independent `registered_titles()` union, so
  restoring never needs the producing profile to be loaded.
- **Empty sections are omitted.** `keys` is an empty string when nothing was
  masked.
- Numbers restart at 1 within each section: the n-th value of section `Физлица`
  is `[PERSON_n]`, of `Номера телефонов` → `[PHONE_n]`, etc.

### v1 (legacy, read-only)

A file without the envelope (sections only) is treated as legacy v1 and parsed
exactly as before, so existing keys files keep restoring:

```yaml
Физлица:
  - Иван Петров
Организации:
  - АО «Росхим»
```

Writers emit v2 only. Both formats coexist indefinitely by design (the reader
keeps v1). `docs/adr/0001` records the cost.

### Validation (both sides, mirrored)

`validate_keys_yaml` (`back/src/masking/mapping.py`) rejects: empty/malformed
YAML, unknown sections, non-list sections, empty values, **duplicate values
within a section**, and a **duplicated section name** (YAML keeps only the last
of repeated keys, so duplicates are detected from the raw lines). It raises
`KeysValidationError` → 422 with a Russian `detail`. Envelope keys are accepted
and stripped before section checks.

The frontend mirrors all of it in
`front/src/shared/api/mask.ts::validateKeysYaml`, run **on every keystroke**
(derived from `demaskKeys`, not on submit). Both the module and the page import
the shared `KEYS_SECTION_TITLES` list; an invalid editor gets `aria-invalid` and
a red inline message, and the request is never sent. The client constants must
stay in sync with the backend's `profiles.py::registered_titles` union and with
the v2 envelope keys — cross-language agreement is enforced by tests
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

### Add a keys envelope key

Teach **both** `validate_keys_yaml` (backend) and `validateKeysYaml` (frontend)
the new envelope key, bump `версия` with a reader branch for the old version,
and add a v2→v3 round-trip test. Restoring must never depend on the envelope.

### Deferred (do not implement without an ADR / maintainer decision)

- User-editable profiles, CRUD endpoints, DB-stored rules (would require auth —
  `TODO.md`).
- User-supplied regex / dictionaries with limits and timeouts.
- Generated TypeScript types from OpenAPI (single source of truth), a stable
  error envelope (`code`, `detail`, `request_id`), dropping the manual
  `KEYS_SECTION_TITLES` duplication.
- A frontend profile-selection UI.
