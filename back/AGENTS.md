# back/ — backend agent guide

Hot-path landmines for the FastAPI/Granian service. The root `AGENTS.md` is
the short index; this file owns the backend details. Keep each fact in exactly
one home — architecture detail lives in `docs/ARCHITECTURE.md`.

## Stack & commands

- Python **3.15** (`uv`), FastAPI on Granian, `natasha` + `phonenumbers` +
  regex detectors, `python-docx`, `py-yaml12` keys. Backend image is Alpine,
  runs as `appuser` (non-root). Dev CMD adds `--reload-ignore-dirs jobs` —
  Granian otherwise reloads on every SQLite WAL write.
- `uv run --directory back ruff check --fix` · `ruff format` · `ty check` ·
  `pytest -q`. Tests are offline/deterministic, `filterwarnings=error`,
  **≥80% branch coverage** enforced in CI. Test files: `test_rule_based.py`,
  `test_mask.py`, `test_mapping.py`, `test_keys_v2.py`, `test_profiles.py`,
  `test_profile_api.py`, `test_docx.py`, `test_mask_file.py`,
  `test_unmask.py`, `test_jobs_*.py`, `test_executor.py`.

## Critical landmines

1. **`setuptools<81` is a hard runtime pin, not a lint convenience.**
   `natasha`→`pymorphy2` imports `pkg_resources` at runtime; on Python 3.15
   setuptools is not a transitive dep (same since 3.12), and setuptools ≥81
   removed `pkg_resources`. Without the pin the first `MorphVocab()` raises
   `ModuleNotFoundError`. It also emits a `UserWarning`, so
   `filterwarnings=error` needs the scoped ignore in `pyproject.toml`.
2. **`natasha`'s morphological dictionary lives in memory and is expensive to
   build.** `MorphVocab()` + extractors load the pymorphy2 dictionary on
   first construction, so the masker is cached (`get_masker`, `lru_cache`) —
   never build a `RuleBasedMasker` per request. Tests construct one per test
   on purpose (isolated, still fast). The worker keeps one warm masker per
   thread (`get_masker_for` per profile).
3. **`natasha` extractors are not wired the way the docs imply.** In 1.6 the
   *multi-match* API is `extractor(text)` (calling the instance) —
   `extractor.find(text)` returns only the **first** `Match` (and `Match` is
   iterable, so a naive `for m in find(text)` silently unpacks into ints).
   There is no `natasha.OrganisationsExtractor`; `AddrExtractor` yields one
   match per component (`AddrPart`), not one whole address.
4. **`NamesExtractor` over-fires on lone tokens and on two common words.** It
   matches prepositions, single letters, bare surnames (`fact.last` only) and
   any two adjacent words as a "given name + surname" (`из Сторон`,
   `Оператор ЭДО`). `RuleBasedMasker` keeps a match only when the fact has a
   given name **and** a surname/patronymic **and** both tokens are plausible
   proper names (`_is_name_token`: capitalised *and* a `Name`/`Surn`/`Patr`
   grammeme in natasha's dictionary). natasha's dictionary is incomplete, so a
   three-part FIO** and a verified given name in any FIO relax the surname
   guard to `_surname_accepted` (a capitalised surname-shaped word that is not
   a role/greeting/топоним/common inanimate noun): `Поповцев`/`Старухин` have
   no `Surn` grammeme but must still be masked — including the two-part
   `Старухин Александр`. A role word before a FIO makes natasha attach the
   role as the surname and split the name (`Секретарь Т.Ф. Гилязов` →
   `Секретарь Т.Ф.` + `Гилязов`), so `_find_persons` also recovers
   `Т.Ф. Гилязов` / `Гилязов Т.Ф.` with the two-initials regexes and a strict
   name-pair fallback for `Иван Петров`.
   **Never drop the given-name `_is_name_token` guard or the surname
   exclusions** — `из Сторон`, `Оператор ЭДО`, `Привет Иван`, `Дорогой Иван`,
   `Г. Москва` and bare `Секретарь` must stay unmasked. Regressions: `test_rule_based.py::test_masks_full_fio_*`,
   `::test_masks_initials_with_surname_*`, `::test_fio_recovery_does_not_create_false_positives`.
5. **Deprecated APIs fail the suite** (`filterwarnings=error`). Use
   `status.HTTP_422_UNPROCESSABLE_CONTENT`, not the deprecated
   `..._UNPROCESSABLE_ENTITY`. Any new endpoint path the tests may hit must
   use non-deprecated symbols.
6. **Windows/PowerShell mangles UTF-8 when sending bodies.** When testing the
   API from the host, put the JSON in a UTF-8 file and POST it with
   `curl.exe --data-binary @file`, or run Python inside a container. A
   Cyrillic body sent literally through PowerShell arrives corrupted and comes
   back as `?` — that's a test-harness artifact, not a server bug.

## DOCX masking (`masking/docx_service.py`)

7. **Masking works at the run level**, replacing only the covered slice of
   each run so styles/sizes are preserved. An entity spanning **several runs**
   (e.g. `АО «Росхим»` = `АО «` | `Росхим` | `»`) must still yield **one**
   `[LABEL]` — don't revert to per-run `mask_text`, which produces
   `[ORGANIZATION][ORGANIZATION][ORGANIZATION]`. `mask_paragraph` writes the
   marker into the run holding the entity start, blanks the covered slices of
   the following runs, and **recomputes the run bounds for every entity** — a
   `bounds` list cached once goes stale as later (right-to-left) entities
   shorten runs and emits the marker once per covered run
   (`[PERSON_1][PERSON_1][ORGANIZATION_1]`). No highlight is written to the
   file: the yellow tint is **preview-only** (front `DocxViewer`).
8. **Don't compare python-docx `Run` objects by identity.** `add_run(...)`
   returns a fresh proxy each call; assert on `.text`/formatting, not
   `run is run`.
9. **`iter_paragraphs` must keep a strong reference to `paragraph._p`.**
   python-docx hands out fresh proxies, so deduplicating by `id(paragraph._p)`
   alone drops paragraphs once an earlier proxy is garbage-collected and its
   address is reused by a later element (header/footer paragraphs vanished
   after repeated calls). The generator holds every seen element in a
   `keep_alive` list — keep it.
10. **Hyperlinked text lives outside `paragraph.runs`.** A `mailto:`/URL
    hyperlink is a `w:hyperlink` wrapper; its runs are invisible to
    `paragraph.runs` but present in `paragraph.text`. `_paragraph_runs` walks
    `iter_inner_content()` so masks land inside links too, and a link whose
    text becomes only mask markers is unwrapped (element replaced by its runs,
    relationship dropped) so no dangling `mailto:` remains. Don't switch back
    to `paragraph.runs`.
11. **`iter_paragraphs` covers all six header/footer variants** per section
    (`header`, `footer`, `first_page_*`, `even_page_*`, plus tables inside
    them). python-docx 1.2 exposes these properties and resolves the OOXML
    `w:headerReference`/`w:footerReference` parts — older notes claiming only
    default variants exist are obsolete. Variants with
    `is_linked_to_previous` are skipped (their content belongs to the previous
    section). Unsupported OOXML parts + the warning mechanism:
    `docs/DOCX_SUPPORT.md`.
12. **Document-wide numbering needs two passes.** `mask_docx_in_memory`
    detects+assigns in a first pass over all paragraphs and rewrites in a
    second; `unmask_docx_in_memory` collapses a marker spanning several runs
    the same way `mask_paragraph` does. Don't fold either into one pass.

## Keys, spans, reversibility

13. **`MaskKeys` (`mapping.py`) numbers each entity per label in document
    order** and dedupes by exact text: the same string repeated reuses one
    placeholder and one key; distinct wordings (different cases of one org)
    stay separate — that is what keeps restoration lossless. Numbers are
    allocated **left-to-right before** the back-to-front rewrite
    (`mask_text`), or the last entity gets `_1`. Unmasking is a plain
    placeholder→value `str.replace` (self-delimiting), so order does not
    matter there. Placeholders are `[LABEL_N]`; `placeholders_from_document`
    reverses a parsed keys file.
14. **Keys YAML uses `yaml12` (the `py-yaml12` package), not PyYAML.** YAML
    1.2 preserves quoting (`АО "Росхим"` stays quoted); PyYAML 1.1 would
    mangle it. Writers emit one `Профиль: <id>` marker (default
    `russian-default` when no profile is supplied) ahead of the Russian
    sections; readers accept that shape and the legacy envelope-less one (a
    file without the marker restores as the default profile). **No
    `версия`/`версия_профиля` keys** are written or read — the pre-prod v2
    envelope was retired, those keys are now unknown sections. Section titles
    come from the profile registry (`profiles.py::registered_titles`); empty
    sections are omitted.
15. **The `/api/mask-file` response is a ZIP, not a `.docx`.** It bundles
    `<base> (замаскированный).docx` + `<base> (ключи демаскировки).yaml`;
    the front unpacks it with jszip and offers two separate downloads. Don't
    return the `.docx` alone (the keys would be lost) and don't hand the
    archive to the user. `/api/unmask-file` likewise returns a ZIP with the
    restored `.docx` + `<base> (отчёт о демаскировке).json` manifest
    (`{"unused_keys": [...]}`) — the former `X-Unused-Keys` header carried
    percent-encoded Cyrillic PII in transport metadata and could outgrow
    every header size limit; never reintroduce it.
16. **Span overlap policy is formal**: entities are sorted by (start asc,
    length desc, score desc, detector order); a span is kept unless it
    overlaps a kept span. `_merge_spans` uses a sweep (O(n log n)) — the old
    `any()` scan was quadratic. Regressions for equal/nested/partial/touching
    spans live in `test_rule_based.py`.

## API & execution

17. **CPU work must leave the event loop** (`masking/executor.py::run_cpu`):
    the four sync endpoints offload natasha/python-docx/ZIP to a bounded
    `ThreadPoolExecutor` (`JOBS_CONCURRENCY * 2` slots, semaphore per event
    loop); overflow → 429. `/health` stays responsive during a mask — never
    call `mask_document`/`mask_docx_in_memory` directly in an `async def`.
18. **Large inputs become durable jobs** (ADR-0002): SQLite WAL
    (`jobs/repository.py`) + private id-keyed spool (`jobs/storage.py`),
    long-lived warm worker (`python -m src.jobs.worker`), leases/heartbeat/
    retry/TTL/cancel. Job rows, logs and metrics must never contain PII or
    keys. The four sync endpoints transparently return `202 + JobAccepted`
    above `SYNC_MAX_TEXT_CHARS` / `SYNC_MAX_DOCX_BYTES` /
    `SYNC_MAX_DOCX_PARAGRAPHS`; `/api/jobs/{id}/result` is one flat ZIP
    (`result.json` for text jobs, `.docx` + `.yaml`/manifest for files).
19. **Error/status conventions**: `validate_keys_yaml` raises
    `KeysValidationError` → 422 with a Russian `detail`; malformed/oversized/
    encrypted DOCX → 415/413; bounded queue/executor → 429; job result not
    ready → 409; unknown job id → 404. Unknown placeholders stay as-is.
    `GET /api/profiles` is read-only; `profile_id` is optional on mask
    requests (default `russian-default`). Setting `MASK_LABELS` fails startup.
20. **Profiles are immutable & validated** (`masking/profiles.py`,
    ADR-0001): label metadata, detector registry (`detectors.py`), always-mask
    organizations as profile data. A new label = one detector + one registry
    entry + one `LabelMeta`; a new profile = one config entry. `MASK_LABELS`
    was removed with a loud startup error.
