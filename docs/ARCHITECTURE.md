# DocMasker Architecture

Sensitive-information masking in text via a deterministic rule-based pipeline. Monorepo: `front/` (Next.js), `back/` (FastAPI), `docs/`.

## System diagram

```
Browser ── http://<host>:5001 ──► front (Next.js, dev: bun dev / prod: standalone)
                                   ├── /            → the single masking page
                                   └── /api/health  → healthcheck
                                   └── /api/mask    → server-side proxy (text)
                                   └── /api/mask-file → server-side proxy (DOCX)
front ── BACKEND_INTERNAL_URL ──► back (FastAPI/Granian, :8001)
                                     ├── POST /api/mask        → masker → masked text + stats + keys
                                     ├── POST /api/mask-file   → masker → ZIP (masked .docx + keys)
                                     ├── POST /api/unmask      → keys → restored text
                                     └── POST /api/unmask-file → masked .docx + keys → ZIP (restored .docx + manifest)
```

The browser talks only to the front origin; the front proxies `/api/mask` to the back server-side, so there is **no CORS for the browser** (CORS is configured on the back only for direct dev access, e.g. curl). The backend starts instantly — there is no model to load.

## Services and compose profiles

`compose.yaml` (name: `docmasker`) defines two service pairs:

| Service | Container | Profile | What |
|---|---|---|---|
| `back` / `back-dev` | `dm-back` / `dm-back-dev` | prod / dev | FastAPI on Granian, port `BACKEND_PORT` |
| `front` / `front-dev` | `dm-front` / `dm-front-dev` | prod / dev | Next.js standalone (prod) or `bun dev` (dev, hot-reload), port `FRONT_PORT` |

Key point: **ports and hosts are set once in the root `.env`** and spread into the Dockerfiles (build-arg `PORT`/`BACKEND_PORT`), port publishing, healthchecks, and internal URLs. All mandatory (`:?` in compose — fails with a clear error instead of staying silent). `BACKEND_INTERNAL_URL` is derived from the service name + `BACKEND_PORT` (e.g. `http://back-dev:8001` in dev).

## Toolchain versions (canonical)

This table is the single source of truth for versions. It separates the
manifest constraint from the lock-resolved version, the CI version and the
Docker/production runtime. When a doc mentions a version, it must agree with
this table. Python 3.15.0 is the current stable release (final 2026-10-09,
bugfix support until ~2028-10); the backend targets it explicitly.

| Component | Manifest constraint | Resolved (lock/host) | CI | Docker / production runtime |
|---|---|---|---|---|
| Python | `==3.15.*` (`back/pyproject.toml`, `uv.lock`) | 3.15.0 (uv-managed) | 3.15 (`setup-uv` python-version) | builder `uv:python3.15-alpine`, runtime `python:3.15-alpine` |
| uv | — | 0.13.8 (host) | `astral-sh/setup-uv@v10.0.1` | `ghcr.io/astral-sh/uv:python3.15-alpine` (floating tag) |
| Bun | `engines >=1.4` (`front/package.json`) | 1.4.4 (host) | `setup-bun@v2` bun-version `1.4` | `oven/bun:1.4-slim` (build) |
| Node.js | — | 26.13.1 (host) | `setup-node@v7` version `26` | `node:26-alpine` (prod runtime, `node:26` in CI) |
| FastAPI | `>=0.165.0` | 0.165.0 | — | — |
| natasha | `>=1.6.0` | 1.6.0 | — | — |
| phonenumbers | `>=9.0.44` | 9.0.44 | — | — |
| python-docx | `>=1.2.0` | 1.2.0 | — | — |
| python-multipart | `>=0.0.34` | 0.0.34 | — | — |
| pydantic / pydantic-settings | `>=2.16.3` (settings) | 2.14.4 / 2.16.3 | — | — |
| py-yaml12 | `>=0.2.1` | 0.2.1 | — | — |
| granian | `>=2.8.9` | 2.8.9 | — | ASGI server in both Docker stages |
| setuptools | `>=80.0.0,<81` | 80.10.2 | — | — |
| ruff | `>=0.17.6` | 0.17.6 | — | — |
| ty | `>=0.0.107` | 0.0.107 | — | — |
| pytest / pytest-asyncio / pytest-cov | `>=9.2.0` / `>=1.4.0` / `>=7.1.0` | 9.2.0 / 1.5.0 / 7.1.0 | — | — |
| Next.js | `16.4.6` (pinned) | 16.4.6 | — | — |
| React / ReactDOM | `19.3.1` (pinned) | 19.3.1 | — | — |
| @heroui/react, @heroui/styles | `^3.2.7` | 3.2.7 | — | — |
| docx-preview | `^0.4.1` | 0.4.1 | — | — |
| jszip | `^3.10.2` | 3.10.2 | — | — |
| undici | `^8.13.1` | 8.13.1 | — | — |
| Tailwind CSS / @tailwindcss/postcss | `^4.3.5` | 4.3.5 | — | — |
| TypeScript | `^7.0.2` | 7.0.2 | — | — |
| oxlint / oxfmt | `^1.94.0` / `^0.79.0` | 1.94.0 / 0.79.0 | — | — |
| @playwright/test | `^1.65.1` | 1.65.1 | — | — |

### Known configuration mismatches (as of 2026-11-22)

1. **TLS verification is bypassed for dependency installs — maintainer decision.**
   `back/Dockerfile` deps stage runs `uv sync --allow-insecure-host pypi.org
   --allow-insecure-host files.pythonhosted.org`; `front/Dockerfile` base stage
   sets `NODE_TLS_REJECT_UNAUTHORIZED=0` (build/dev stages only — the Node
   production runtime does not inherit it); the host `justfile` exports
   `UV_SYSTEM_CERTS=1` and `NODE_OPTIONS=--use-system-ca`. Accepted as the
   corporate-proxy workaround (mirrors ReportCollector, where the same setup
   works for uv and bun). The `node` prod runtime verifies TLS normally. If a
   corporate CA becomes available, replace the bypasses with
   `NODE_EXTRA_CA_CERTS` / system trust store.
2. **Git/CI topology diverges from the workflow docs.** Origin currently holds
   only `feat/demasking`; `main`/`dev` exist locally. CI push/PR triggers fire
   only for `main`/`dev`, so pushes to other branches run no CI. The job named
   `CI required` is not a required check by itself — branch rulesets live in
   GitHub repository settings and cannot be verified from the repo.

Resolved (kept for the record): the frontend prod container now runs as the
unprivileged `node` user, and compose prod publishes the backend port to
loopback only (`127.0.0.1:$BACKEND_PORT`), so the API is no longer reachable
from the network directly.

## Environment variables (root `.env`, single source)

Mandatory:
- `BACKEND_PORT` (8001), `FRONT_PORT` (5001) — single sources for ports; `BACKEND_HOST`/`BACKEND_HOST_DEV` (`back`/`back-dev`) name the services in the compose networks.
- `AUTH_APP_ORIGIN` — the app host used to derive CORS origins (inherited from the ReportCollector convention); in DocMasker the browser hits only the front origin, so it is mostly a placeholder.

Masking:
There is no env-var label override: `MASK_LABELS` was removed with the
profile registry (setting it now fails backend startup). Entity types come
from masking profiles — see `docs/adr/0001-masking-profiles.md` and
`GET /api/profiles`.

## Masking pipeline (`back/src/masking/`)

Deterministic, rule-based: `natasha` (morphology/rules) + `phonenumbers` + regular expressions. No model, no PyTorch, no network at runtime.

- `rule_based.py` — `RuleBasedMasker`, the core. Runs the detectors selected by the profile in the profile's order (the default: **emails → links → phones → persons → addresses → organisations**), merges all spans into one non-overlapping set (start asc, length desc, score desc, detector order — see `_merge_spans`) and rewrites the text once, back-to-front, so offsets stay valid.
- `profiles.py` — `MaskingProfile`, the typed versioned configuration (ADR-0001): label metadata (id/title/order), detector selection, `always_mask_organizations` (moved out of the algorithm). The built-in `russian-default/1` reproduces the pre-profile behavior; profiles are immutable and validated at construction (duplicates/unknown detectors → `ProfilesValidationError`). `get_profile`/`list_profiles`/`registered_titles` are the registry surface.
- `detectors.py` — the detector registry: a stable id + reported label + the `RuleBasedMasker._find_*` provider it invokes. Adding a label = one provider method + one registry entry.
  - *Emails* — a regex covering subdomains and `+`/`.` local parts.
  - *Links* — a regex for any `scheme://…` URL (`http`, `https`, `ftp`, …) with host, path and query; trailing sentence punctuation is trimmed.
  - *Phones* — `phonenumbers.PhoneNumberMatcher(text, "RU")`, so `+7 …`, `8 …` and parenthesised forms are all recognised.
  - *Persons* — `natasha.NamesExtractor`; a match is kept only when the fact has **both** a given name and a surname/patronymic **and** both tokens are plausible proper names (natasha's dictionary carries a `Name`/`Surn`/`Patr` grammeme and the token is capitalised). That drops the extractor's two-word false positives such as `из Сторон` or `Оператор ЭДО`.
  - *Addresses* — `natasha.AddrExtractor` yields one match per component; adjacent components are glued into a single `[ADDRESS]` span. A span is kept only if it carries an address keyword (`г.`, `ул.`, …) or mixes letters with digits — a digit-only run (`270023`) is **not** an address, so passport/index-like numbers are not masked. Because natasha drops parts of a long, comma-separated address, a value introduced by `адрес:` (e.g. `Юр. адрес: …`) is masked **whole** up to the sentence boundary (a period after a known abbreviation like `г.` is not a boundary).
  - *Organisations* — no `OrganisationsExtractor` exists in natasha 1.6, so they are matched in two ways. Abbreviated legal forms (`ООО`, `АО`, `ПАО`, `ИП`, …) are a regex: prefix + optional punctuation + the following name, quoted with any of `"" «» „“ “” ‘’ '' <>` (the quotes are part of the mask) or a capitalised word chain. Spelled-out legal forms (`Акционерное общество`, `Общество с ограниченной ответственностью`, `Публичное/Закрытое/Открытое акционерное общество`, `Индивидуальный предприниматель`, `Унитарное предприятие`) are matched **lemma-wise**: natasha lemmatises each word of the form once (`_ORG_FULL_FORMS`) and a text token counts when its lemma is equal or close enough (`_ORG_LEMMA_RATIO`), so **any grammatical case** (`Акционерным обществом`, `Общества с ограниченной ответственностью`) and small typos (`акциноерным`) match without a hand-written case table. A trailing name (quoted, quotes included) is absorbed; the form alone is masked when there is no name. A second regex covers `организация/компания/… <Name>`.
  - `get_masker_for(profile_id)` builds the masker once per profile (`lru_cache`) — the natasha extractors load an in-memory morphological dictionary (`MorphVocab`) and are expensive to construct.
- `service.py` — thin orchestration: `mask_document` runs the masker over a `MaskRequest`; `mask_text` performs the back-to-front `[LABEL_N]` rewrite (numbers are assigned left-to-right first, so they follow document order).
- `mapping.py` — `MaskKeys`, the demasking keys. Every masked entity becomes a numbered placeholder `[LABEL_N]` and its original value is recorded. **The numbering is per label, in document order**, and entities are keyed by their exact text: the same string repeated (a phone mentioned twice) reuses one placeholder and yields **one** key, while different wordings (`АО «Росхим»` vs `Акционерного общества «Росхим»`) stay separate keys — only then is the original case form restored exactly. `format_keys_yaml_v2` writes the **v2 envelope** (`версия: 2` + `профиль` + `версия_профиля`) ahead of the Russian sections; `parse_keys_yaml`/`validate_keys_yaml` read **both formats** — a file without the envelope is legacy v1 and still restores, an envelope claiming another version is rejected. Section titles resolve against the profile-independent `registered_titles()` union, so restoring never needs the producing profile. `placeholders_from_document` reverses a parsed keys file into a placeholder→value map for restoring.
- `schemas.py` — `MaskRequest{text, profile_id}`, `MaskResponse{text, stats, keys}`, `MaskStats{total, occurrences_by_label, unique_by_label}` (aggregates only — raw entity values/offsets never leave the API), `ProfilesResponse` (the `GET /api/profiles` payload), `UnmaskRequest{text, keys}`, `UnmaskResponse{text}`. `keys` is the demasking YAML v2 (empty string when nothing was masked).
- `docx_service.py` — DOCX masking that **preserves formatting**: iterates every paragraph (body, all table cells recursively, default header/footer of each section) via `iter_paragraphs` in **two passes** — the first detects entities and assigns placeholders from one document-wide `MaskKeys` (so per-label numbering is continuous across the whole document), the second rewrites runs with the already-assigned markers. An entity spanning several runs (`АО «Росхим»` = `АО «` | `Росхим` | `»`) collapses into a **single** `[LABEL_N]`: the marker goes in the run holding the entity start and the covered slices of the following runs are blanked. The run bounds are **recomputed for every entity** (a cached list goes stale as later entities shorten runs and would emit one marker per run). No highlight is written here — the yellow tint is a **preview-only** effect (front `DocxViewer`), so the downloaded file stays plain. `unmask_docx_in_memory` is the inverse: it finds every placeholder in the paragraph text and rewrites the runs, collapsing a marker that happens to span several runs the same way. Only the overlapping slices of the XML runs are changed (`paragraph.runs` plus hyperlinked runs from `iter_inner_content()`); a hyperlink whose text is fully replaced by a mask (e.g. a `mailto:` email) is **unwrapped** (the `w:hyperlink` element is replaced by its runs and the relationship is dropped), so the masked document carries no dangling link.
- `router.py` — `GET /api/profiles`, `POST /api/mask`, `POST /api/mask-file`, `POST /api/unmask`, `POST /api/unmask-file`; inputs over the configurable thresholds are redirected to the job queue with 202 (ADR-0002).
- `jobs/` — durable job execution (ADR-0002): `models.py` (status/phase), `repository.py` (SQLite WAL: transactional claim, leases/heartbeat, bounded queue → 429, retry/TTL/cancel), `storage.py` (atomic id-scoped artifact spool), `routing.py` (threshold + shared enqueue), `api.py` (`/api/jobs` contract), `dependencies.py` (process-wide repo/store).

The default profile reports `person, phone, email, link, address, organization` with Russian section titles (in keys-file order): `Физлица, Номера телефонов, Email, Ссылки, Организации, Адреса`. Profiles are listed at `GET /api/profiles`; mask endpoints accept an optional `profile_id` (omitted = `russian-default`).

## API (back, prefix `/api`)

- `GET /profiles` — the registered masking profiles (id, version, display name, labels with titles/order). Read-only; profiles are code-configured (ADR-0001), there is no mutation endpoint.
- `POST /mask` — body `{"text": "...", "profile_id": "..."}` (profile optional) → `{"text": "<masked>", "stats": {"total": 2, "occurrences_by_label": {"person": 1, …}, "unique_by_label": {…}}, "keys": "<YAML>"}`. 422 for empty/whitespace text.
- `POST /mask-file` — multipart field `file` (.docx) + optional `profile_id` form field → a **ZIP** (`Content-Disposition: attachment`, `application/zip`) holding `<base> (замаскированный).docx` and `<base> (ключи демаскировки).yaml`, with the masked-entity count in the `X-Masked-Count` header and a per-label JSON map in `X-Masked-Breakdown` (e.g. `{"person":2,"phone":1}`). The archive keeps the two artifacts of one run together; the **front unpacks it** and offers them as two separate downloads, the user never sees the ZIP. 422 for non-`.docx` or empty files.
- `POST /unmask` — body `{"text": "...", "keys": "<YAML>"}` → `{"text": "<restored>"}`. The keys are validated by `validate_keys_yaml` first: empty/malformed YAML, unknown sections, non-list sections, empty values, duplicate values or a duplicated section name all fail with **422** and a Russian `detail`. Unknown placeholders in the text are left as-is.
- `POST /unmask-file` — multipart fields `file` (masked .docx) and `keys` (YAML as a form field) → a **ZIP** (`Content-Disposition: attachment`, `application/zip`) holding `<base> (демаскированный).docx` and `<base> (отчёт о демаскировке).json` (a manifest with the `unused_keys` list — in the body, not headers, so PII never rides in transport metadata and no header limit can truncate it). Uploads are validated as OPC/ZIP packages: malformed/non-Word ZIPs → 415, size/entry/zip-bomb limits → 413, encrypted archives → 415.
- `GET /health` — readiness probe used by Docker healthcheck; always `ok` (no warm-up).

Errors carry a Russian `detail`; the front renders them in red inside the result TextArea.

## Front (`front/src/`)

- `app/page.tsx` — client component with two top-level modes («Маскировка» / «Демаскировка»). The switcher lives **inside the brand** (`shared/ui/brand.tsx`): the app name followed by a ghost button showing the active mode, opening a hover `Dropdown.Menu` (mirroring ReportCollector's project switcher). The theme switcher sits at the header's right edge. Two panes below. Every mode owns its own state slice (`Record<AppMode, ModeState>`), so switching modes swaps the visible draft without losing either one.

  - *Masking mode.* Left pane holds either the input `TextArea` or (when a `.docx` is selected) a live `DocxViewer` render, with a full-width `ButtonGroup` («Выбрать файл» — tertiary — / «Замаскировать») underneath. A `.txt` file is read client-side and its contents become the input text (it is *not* shown as a file); a `.docx` dropped or picked opens the document preview. The right pane's label row has two `Tabs` («Результат маскировки» / «Ключи демаскировки») plus the stats trigger on the right; the tabs are sized to their content so the stats stay on the same row. The result tab shows the masked `TextArea` (text) or `DocxViewer` (file); the keys tab shows the demasking YAML read-only. Both tabs and the `ButtonGroup` under the pane («Скачать результат маскировки» / «Скачать ключи демаскировки») are **always visible**, disabled until the artifact exists. Masking nothing shows `Маскировка не выполнена: в тексте не нашлось чувствительной информации.` — removing the input (clearing the text or the file) resets the result, so the notice goes away too.
  - *Demasking mode.* The left pane gets its own two tabs — «Данные для демаскировки» (the same text/`.docx` input) and «Ключи демаскировки» (a YAML `TextArea`, pasted or loaded as a `.yaml` file). Both modes render **separate components** (`MaskInputPane` / `DemaskInputPane`), so no input element, preview or text area is shared between them. Three buttons are always present — «Выбрать файл данных», «Выбрать файл ключей» and «Демаскировать» — enabled by completeness: picking a data file switches to the data tab, picking a keys file switches to the keys tab, and «Демаскировать» needs both the data and valid keys. The keys are validated **on the client on every edit** (`validateKeysYaml`): while non-empty and malformed the `TextArea` gets `aria-invalid`/`[data-invalid="true"]`, an inline reason shows under it, and the request is never sent. A stats tooltip (mirroring masking) sits next to the tabs. The right pane (`UnmaskResultPane`) mirrors masking: a `.docx` was demasked → a `DocxViewer` preview; text → a read-only `TextArea`; plus a «Скачать файл» button (always present, disabled until there is something to save).

  - Stats: the shared `shared/mask-stats.ts` builds a `MaskStats{total, byLabel, uniqueByLabel, unused}` and renders both the short result-label (`totalText` → `N фрагментов`) and the `Tooltip` body (`statsBreakdown`). The label shows the total occurrences; the tooltip lists one line per label with the occurrence and unique counts (`Физлица: 4 (2 уникальных)`). On **demasking** the same shape is used, plus a trailing `Не применено N:` block listing key values whose placeholder never appeared in the masked document. Text mode derives it live from the input; file mode reads it from the restore archive's JSON manifest (`unpackRestoreArchive`). When the total is `0` there is no trigger and no tooltip.
- `shared/hooks/use-synced-scroll.ts` — `useSyncedScroll` pairs the input/result surfaces (both text areas in text mode, both DOCX surfaces in file mode) and mirrors their **relative** scroll position (`relativeScrollTop` maps `scrollTop / scrollableRange`, since masked output is shorter). The two `DocxViewer`s are paired via their `surfaceRef`.
- `shared/ui/docx-viewer.tsx` — client component that renders a `.docx` Blob into a scrollable pane via `docx-preview`'s `renderAsync`. Used for the selected source file, the masked result **and** the restored (demasked) result; accepts an optional `surfaceRef` so the parent can sync its scroll. Its optional `highlight` prop lists substrings to wrap in a `<mark class="docx-marker">` (yellow) — **preview-only**: the masked preview highlights the `[LABEL_N]` placeholders (`placeholdersFromKeys`), the demasked preview highlights the restored values (`valuesFromKeys`), and the downloaded document is never altered. The document is rendered **once per blob**; a change to `highlight` re-applies the marks in place (undoing the previous pass first) without re-parsing the document, so editing keys does not re-render the preview.
- `shared/ui/theme-switcher.tsx` — `ThemeSwitcher`, the header theme control: icon-only HeroUI `Tabs` (no panels) with `Computer`/`SunLight`/`HalfMoon` from `iconoir-react`; each tab shows its name in a HeroUI `Tooltip` on hover. Drives `next-themes` (controlled `selectedKey`), so the preference persists in `localStorage` and survives a reload. `app/providers.tsx` mounts the `ThemeProvider` (`attribute="class"`, `defaultTheme="system"`, `enableSystem`).
- `shared/ui/support-button.tsx` — `SupportButton`, «Связаться с разработчиком» in the header's right slot (mirrors ReportCollector): an icon-only HeroUI `Link` (`mailto:`) that expands its label on hover/focus. `SUPPORT_MAIL` is read server-side in `app/page.tsx` and passed as a prop (never a `NEXT_PUBLIC_` value); an empty value hides the control. `supportMailto()` is unit-tested.
- `app/api/mask/route.ts` — server route that validates JSON and calls `proxyMaskRequest`.
- `app/api/mask-file/route.ts` — server route that replays the multipart upload to the back and returns the ZIP plus `X-Masked-Count` / `X-Masked-Breakdown` / `Content-Disposition`.
- `app/api/unmask/route.ts` — server route that validates `{text, keys}` and calls `proxyUnmaskRequest`.
- `app/api/unmask-file/route.ts` — server route that replays the masked file + `keys` form field to the back and returns the restore ZIP (restored `.docx` + report manifest).
- `shared/api/mask.ts` — `proxyMaskRequest` / `proxyUnmaskRequest`: forward to `BACKEND_INTERNAL_URL`, pass the body/status back verbatim.
- `shared/api/mask-archive.ts` — `unpackMaskArchive`: unpacks the `/api/mask-file` ZIP (via `jszip`) into the masked document and the keys file, with the names taken from the archive entries. The two halves are then offered as separate downloads.
- `shared/config.ts` — `getBackendInternalUrl()` (throws if unset).
- `src/instrumentation.ts` — Node-runtime hook that installs a global undici dispatcher with `headersTimeout: 0` / `bodyTimeout: 0`, so the server-side backend proxy has **no fetch timeout**: a large DOCX round-trip can never be aborted by an arbitrary ceiling. `connectTimeout` keeps its default, so a dead backend still fails fast. All proxy routes also set `maxDuration = 3600` for parity with hosted environments.

The masked file is named `<original> (замаскированный).docx` and the keys `<original> (ключи демаскировки).yaml` (inside the ZIP; the front takes the names from the archive entries).

The header brand mirrors ReportCollector: the `ruschem_icon.png` icon (also the favicon/apple-touch icon) next to «ДокМаскер» in the shared brand styling.

## Deployment (prod)

`just prod` builds the prod-profile images and starts `dm-back` + `dm-front`. The front publishes `FRONT_PORT`, the back `BACKEND_PORT`. The backend starts instantly — there is no model download and no persistent cache volume. The base image is Alpine (`python:3.15-alpine`), matching the project convention, because dropping PyTorch removed the glibc/musl constraint.

## CI stack (`infra/compose.ci.yaml`)

`infra/compose.ci.yaml` is the isolated stack the `production` CI job boots to run the Playwright smoke tests against the real prod images (`dm-back:ci`, `dm-front:ci`). It never loads the developer's `.env`, volumes or log dirs and uses non-conflicting host ports: front **15001**, back **18001**. The backend answers `/health` immediately, so the stack wait timeout is short. Details and the job table — `docs/WORKFLOW.md`.

## Testing

- **Back** (`back/tests/`): pytest with `filterwarnings=error` and ≥80% branch coverage. The pipeline is deterministic, so tests assert exact output — no stubs and no network. Files: `test_rule_based.py` (CAPSLOCK, inflected names, phone formats, emails, addresses, org prefixes, label overrides), `test_mask.py` (service + `/api/mask` endpoint), `test_mapping.py` (keys YAML build/validate), `test_docx.py` (run-level formatting preservation), `test_unmask.py` (keys validation and text/DOCX restore), `test_mask_file.py` (multipart endpoints + download headers).
- **Front** (`front/src/**/__tests__/`): `bun test` with happy-dom (`front/src/test-setup.ts`). Covers the server-side proxy contract (`shared/api/mask.ts`) and config resolution (`shared/config.ts`).
- **e2e** (`front/e2e/`): Playwright `smoke` project against a running stack (`/api/health`, page render, text-mask round-trip). Setup — `docs/WORKFLOW.md`.
