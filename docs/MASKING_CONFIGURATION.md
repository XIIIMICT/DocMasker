# Keys, configuration and extension recipes

Phase 4 replaced the profile system with **one immutable masking configuration**
plus validated exception/lexicon files. The canonical label catalogue, every
curated vocabulary list and all `always_mask`/`never_mask` exceptions live in
`back/config/masking/`; Python keeps only algorithms, normalization, span
merging and safe matching. Decisions: `docs/adr/0001-masking-profiles.md`
(historical, superseded), `docs/adr/0003-postgres-job-foundation.md`.

## Configuration layout

```text
back/config/masking/
  labels.yaml                 # canonical catalogue; sequence = keys-section order
  lexicons/
    organizations.yaml        # legal-form abbreviations/phrases, lead words
    persons.yaml              # role/greeting exclusions
    addresses.yaml            # abbreviations, keywords
    common.yaml               # literal trim chars
  exceptions.example.yaml     # synthetic values only (committed)
  exceptions.yaml             # private runtime file (gitignored, mounted RO)
```

`back/src/masking/config.py` loads the files once at startup into an immutable
`MaskingConfig` snapshot. Invalid YAML, unknown label/match mode, duplicate
normalized values, `always_mask` ↔ `never_mask` conflicts, a leftover `order`
field or a missing required file fail startup with a clear
`MaskingConfigError`. The non-reversible digest is computed from the **entire
canonical effective snapshot** — schema version, ordered label ids/titles,
every lexicon value, all `common.yaml` character sets, and every exception's
primary literal, match mode and ordered variants. YAML comments/formatting alone
do not change it; any effective value does. The digest is always part of the
versioned cache key (§4.3), even when `MASKING_CONFIG_DIGEST` is pinned manually,
so no config change can be silently served from a stale cache entry.

Validate without starting the app:

```sh
uv run --directory back python -m src.masking.validate_config
uv run --directory back python -m src.masking.validate_config --dir ./config/masking
```

Never log exception values — only the fixed config path, schema version, entry
counts and the digest prefix. Do not commit real names/PII into
`exceptions.yaml`; mount it read-only in production. Hot reload is deferred: a
running job uses one immutable snapshot, restart is the update mechanism.

## Labels catalogue

`labels.yaml` is the single source (no `order` field — the sequence defines
output order):

```yaml
schema_version: 1
labels:
  - id: person
    title: Физлица
  - id: phone
    title: Номера телефонов
  - id: email
    title: Email
  - id: link
    title: Ссылки
  - id: organization
    title: Организации
  - id: address
    title: Адреса
```

It is published as `GET /api/labels` and compiled into
`front/src/shared/api/labels.generated.ts` by
`bun run --cwd front gen:labels` (CI verifies the generated file is fresh).
`KEYS_SECTION_TITLES` / `KEYS_TITLE_LABELS` and the tooltip titles in the
frontend come from that generated file — no independently maintained hardcoded
lists. Backend validation remains authoritative.

## Keys file format

### Current (written by every mask)

```yaml
Профиль: russian-default
Физлица:
  - Иван Петров
```

- `Профиль` (capital `П`) is **legacy metadata** now that there is one runtime
  configuration. Writers still emit it for compatibility; readers accept and
  ignore it (including a foreign value). **No `версия` / `версия_профиля` keys**
  are written or read — that pre-prod envelope was retired (§5.2).
- Section titles come from `labels.yaml`; a file **without** the marker (legacy
  envelope-less shape) also restores.
- **Empty sections are omitted.** `keys` is an empty string when nothing was
  masked.
- Numbers restart at 1 within each section: the n-th value of `Физлица` is
  `[PERSON_n]`.

### Legacy (envelope-less, read-only)

```yaml
Физлица:
  - Иван Петров
Организации:
  - АО «Росхим»
```

### Validation (both sides)

`validate_keys_yaml` (`back/src/masking/mapping.py`) rejects empty/malformed
YAML, unknown sections (including retired version keys), non-list sections,
empty values, duplicate values within a section and a duplicated section name.
The frontend mirrors it in `shared/api/mask.ts::validateKeysYaml` on every
keystroke; the section-title list is generated from `labels.yaml`, so the two
validators cannot drift. Cross-language agreement is covered by tests.

### Restoration is single-pass (DM-1)

Unmasking compiles the known placeholders into one longest-first alternation and
scans the input once. Each match is replaced by its mapped value as opaque
output — `re.sub` does not rescan replacements — so a value that itself contains
a valid marker cannot cascade into a second restoration. Text with no known
placeholder is returned unchanged, unknown markers stay in place, and repeated
values restore independently. The same rule applies to every DOCX logical text
container (see `docs/DOCX_SUPPORT.md`). The keys YAML format and `KEYS_FORMAT_VERSION`
are unchanged.

### Mask-time marker collision guard (DM-1)

Before a mask operation produces output, source text is scanned for literals
matching the project's marker pattern (`[LABEL_N]`). If a literal equals a marker
that operation allocates, masking fails with a clear Russian 422 error
(`marker_collision`) instead of giving one marker two meanings on restoration. A
marker-shaped literal that is not allocated is ordinary text and masks normally;
this also applies to each DOCX part. Collisions are not expected in this
deployment — the collision-safe versioned format is deferred by maintainer
decision (`TODO.md`).

## Exceptions (`exceptions.yaml`)

Typed, literal data — never executable code or regex:

```yaml
schema_version: 1
always_mask:
  organization:
    - value: "Росхим"
      match: case_insensitive
      variants: ["Росхима", "Росхиме", "Росхимом"]
  person: []
  address: []
never_mask:
  organization: []
  person:
    - value: "Иван Иванов"
      match: case_insensitive
  address: []
```

- `match` is `exact` / `case_insensitive` / `token_sequence`, and each mode has
  real, tested semantics:
  - `exact` — case-sensitive literal;
  - `case_insensitive` — case-insensitive literal;
  - `token_sequence` — ordered tokens separated by arbitrary whitespace, also
    case-insensitive (useful when DOCX splits a name across lines or spaces).
  All are NFC normalized, trimmed, whitespace-collapsed and word-boundary
  guarded; `ё`/`е` are **not** equivalent. Configuration supplies literals only,
  never executable regex.
- No substring matching by default (word boundaries); a `never_mask` entry
  suppresses **only** an exact whole detected span, never a larger overlapping
  one. `always_mask` spans pass through the normal overlap policy and key
  numbering. The mode applies to primaries and variants alike.
- Empty, duplicate and case-insensitively repeated variants are rejected; the
  same check covers primaries and variants across all entries of one side. A
  normalized literal present in both `always_mask` and `never_mask` (including
  variants) fails startup. `Росхим` force-mask behavior is preserved from the
  old hardcode.

## Address candidate prefilter (DM-2)

`lexicons/addresses.yaml` is the single source of truth for **nomination** of
address candidates — no parallel Python vocabulary list is allowed:

- `address_keywords`: alphabetic entries of 3+ letters are whole-word markers
  (`улица`, `дом`, `переулок`, …) matched with Unicode `\b`; hyphenated literals
  such as `р-н` are matched verbatim.
- `address_abbreviations`: every entry matches a dotted form (`ул.`) or a bare
  form (`ул Ленина`). Bare forms additionally require a capitalised word or
  digit immediately after the token, so `ш` inside `Строймонтаж` and a bare
  `ш consulted` never nominate. A preceding dot is excluded so the tail of
  `и т.д.` is not a marker.
- The `адрес:` label and a 6-digit postcode are structural signals.

`rule_based._address_candidate_re()` compiles these literals lazily from the
validated snapshot. A match **only nominates** a sentence-sized range for the
expensive `AddrExtractor`; it never auto-masks the sentence. The `адрес:` full-
line path and `AddrExtractor` still decide the actual span, and `never_mask` /
deduplication are unchanged.

Abbreviation dots are not sentence ends, and a bare newline is a soft boundary
when the next line continues the address (trailing comma, or a marker/digit at
the start of the line), so a wrapped address reaches the extractor in one piece.
Text with no marker nominates nothing — the short-text / OOXML fast path is
preserved.

Benchmark (host Windows, uv 0.13.8 / CPython 3.15.0, deterministic synthetic
corpus of 1,000 sentences — 60% prose / 20% dotted / 20% full-or-bare — 3 runs,
median wall; `detect()` over all sentences):

| Prefilter | Wall (median) | Nominated ranges | Address entities |
|---|---|---|---|
| Previous dotted-only | 6.26 s | 201/1000 (20.1%) | 201 |
| Candidate-filtered | 9.40 s | 389/1000 (38.9%) | 389 |
| Previous, prose-only | 1.25 s | 0/1000 (0%) | 0 |
| Candidate, prose-only | 1.21 s | 0/1000 (0%) | 0 |

The extra ~50% wall on the mixed corpus buys 188 previously missed addresses;
prose without address signals is unchanged at 0% nominations. The YAML edit
changes `config_digest()` automatically, so cached results cannot be served from
a stale entry; bumping `MASKING_RULESET_VERSION` is a separate maintainer
decision (`back/src/config.py`, outside this change's scope).

## Recipes

### Add a new label

1. `back/src/masking/rule_based.py` — one `_find_<label>` provider.
2. `back/src/masking/detectors.py` — one `Detector` registry entry.
3. `back/config/masking/labels.yaml` — one row (id + Russian title) in the
   desired output position.
4. `front`: `bun run --cwd front gen:labels` (CI checks freshness).
5. Tests: detector case, keys round-trip, `validate_config`, front validator.

Never hard-code a new label into the orchestrator or duplicate the title chain.

### Add an exception / lexicon entry

Edit the appropriate YAML under `config/masking/` (private `exceptions.yaml`
for real values), run `validate_config`, and bump `MASKING_RULESET_VERSION`
when output semantics change. The config digest already changes automatically
with file contents, so cached results are invalidated.

### Cache invalidation release checklist (Phase 3/4)

| Setting | Bump when |
|---|---|
| `CACHE_NAMESPACE_VERSION` | key construction or stored cache-entry structure changes |
| `MASKING_RULESET_VERSION` | detection/masking semantics change the output |
| `MASKING_CONFIG_DIGEST` | extra manual salt only; the loaded snapshot digest is always included |
| `RESULT_MANIFEST_VERSION` | result archive/manifest contract changes |
| `KEYS_FORMAT_VERSION` | keys YAML format changes |
| `DOCX_TRANSFORMER_VERSION` | OOXML transformation changes |
| `CACHE_KEY_SECRET` | intentional full invalidation (rotating the secret) |

TTL alone is not an invalidation strategy. Never log a full cache key or
fingerprint (they are HMAC'd so predictable PII cannot be guessed).

## Deferred (do not implement without an ADR / maintainer decision)

- User-editable configuration CRUD, DB-stored rules (would require auth —
  `TODO.md`), profile selector, hot reload.
- Generated TypeScript types for the whole API from OpenAPI (Phase 6).
- A broad precision/recall corpus instead of focused exception regressions.
