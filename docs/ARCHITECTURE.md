# DocMasker Architecture

Sensitive-information masking in text via a deterministic rule-based pipeline. Monorepo: `front/` (Next.js), `back/` (FastAPI), `docs/`. Topic docs: `docs/API.md` (HTTP contract), `docs/MASKING_CONFIGURATION.md` (keys/profiles), `docs/PERFORMANCE.md` (benchmarks/limits).

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

`compose.yaml` (name: `docmasker`) defines the **application** pairs only:

| Service | Container | Profile | What |
|---|---|---|---|
| `back` / `back-dev` | `dm-back` / `dm-back-dev` | prod / dev | FastAPI on Granian, port `BACKEND_PORT` |
| `front` / `front-dev` | `dm-front` / `dm-front-dev` | prod / dev | Next.js standalone (prod) or `bun dev` (dev, hot-reload), port `FRONT_PORT` |
| `worker` / `worker-dev` | `dm-worker` / `dm-worker-dev` | prod / dev | `python -m src.jobs.worker`, polls the PostgreSQL queue |

PostgreSQL and Valkey are in `infra/compose.db.yaml` (included from
`compose.yaml`) and are started **separately** with `just db-stand` — the
ReportCollector pattern. App services have no `depends_on` for them;
`just seed` / `just migrate` create the `jobs` schema explicitly before the
stack starts. `DATABASE_URL` is built from `POSTGRES_*` components: `.env`
uses `POSTGRES_HOST=localhost`/`POSTGRES_PORT=15433` for host-side seed and
migrations, compose overrides it with `POSTGRES_HOST_DOCKER` (service name) for
`back`/`worker` (ReportCollector pattern). `VALKEY_URL` is overridden by compose
for containers.

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
| @heroui/react, @heroui/styles | `^3.2.8` | 3.2.8 | — | — |
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
from the single canonical catalogue `back/config/masking/labels.yaml` —
see `docs/MASKING_CONFIGURATION.md` and `GET /api/labels`.

## Masking pipeline (`back/src/masking/`)

Deterministic, rule-based: `natasha` (morphology/rules) + `phonenumbers` + regular expressions. No model, no PyTorch, no network at runtime.

- `rule_based.py` — `RuleBasedMasker`, the core. Runs detectors in a fixed order (**emails → links → phones → persons → addresses → organisations**), merges all spans into one non-overlapping set (start asc, length desc, score desc, detector order — see `_merge_spans`) and rewrites the text once, back-to-front, so offsets stay valid. All curated vocabulary and `always_mask`/`never_mask` exceptions come from the immutable config snapshot, not from Python literals.
- `config.py` (`masking/config.py`) — typed loader for `back/config/masking/`: one immutable, startup-validated snapshot (`labels.yaml` + lexicons + private `exceptions.yaml`). Its non-reversible digest is part of the cache key. Invalid YAML / duplicate / conflicting / unknown values fail startup; `python -m src.masking.validate_config` validates without the app. The legacy `Профиль` keys marker is ignored on read (§5.2).
- `detectors.py` — the detector registry: a stable id + reported label + the `RuleBasedMasker._find_*` provider it invokes. Adding a label = one provider method + one registry entry.
  - *Emails* — a regex covering subdomains and `+`/`.` local parts.
  - *Links* — a regex for any `scheme://…` URL (`http`, `https`, `ftp`, …) with host, path and query; trailing sentence punctuation is trimmed.
  - *Phones* — `phonenumbers.PhoneNumberMatcher(text, "RU")`, so `+7 …`, `8 …` and parenthesised forms are all recognised.
  - *Persons* — `natasha.NamesExtractor`; a match is kept only when the fact has **both** a given name and a surname/patronymic **and** both tokens are plausible proper names (natasha's dictionary carries a `Name`/`Surn`/`Patr` grammeme and the token is capitalised). That drops the extractor's two-word false positives such as `из Сторон` or `Оператор ЭДО`. A FIO relaxes the surname guard (rare surnames like `Поповцев`/`Старухин` are not in natasha's dictionary, including the two-part `Старухин Александр`), and a role-prefixed FIO is recovered from the original text (`Секретарь Т.Ф. Гилязов` → masks `Т.Ф. Гилязов`), because natasha attaches the role as the surname and splits the span; see `back/AGENTS.md` landmine 4.
  - *Addresses* — `natasha.AddrExtractor` yields one match per component; adjacent components are glued into a single `[ADDRESS]` span. A span is kept only if it carries an address keyword (`г.`, `ул.`, …) or mixes letters with digits — a digit-only run (`270023`) is **not** an address, so passport/index-like numbers are not masked. Because natasha drops parts of a long, comma-separated address, a value introduced by `адрес:` (e.g. `Юр. адрес: …`) is masked **whole** up to the sentence boundary (a period after a known abbreviation like `г.` is not a boundary).
  - *Organisations* — no `OrganisationsExtractor` exists in natasha 1.6, so they are matched in two ways. Abbreviated legal forms (`ООО`, `АО`, `ПАО`, `ИП`, …) are a regex: prefix + optional punctuation + the following name, quoted with any of `"" «» „“ “” ‘’ '' <>` (the quotes are part of the mask) or a capitalised word chain. Spelled-out legal forms (`Акционерное общество`, `Общество с ограниченной ответственностью`, `Публичное/Закрытое/Открытое акционерное общество`, `Индивидуальный предприниматель`, `Унитарное предприятие`) are matched **lemma-wise**: natasha lemmatises each word of the form once (`_ORG_FULL_FORMS`) and a text token counts when its lemma is equal or close enough (`_ORG_LEMMA_RATIO`), so **any grammatical case** (`Акционерным обществом`, `Общества с ограниченной ответственностью`) and small typos (`акциноерным`) match without a hand-written case table. A trailing name (quoted, quotes included) is absorbed; the form alone is masked when there is no name. A second regex covers `организация/компания/… <Name>`.
  - `get_masker()` builds the single process-wide masker (`cache`) — the natasha extractors load an in-memory morphological dictionary (`MorphVocab`) and are expensive to construct. There is one configuration; `RuleBasedMasker.for_labels(...)` is a test-only label subset.
- `service.py` — thin orchestration: `mask_document` runs the masker over a `MaskRequest`; `mask_text` performs the back-to-front `[LABEL_N]` rewrite (numbers are assigned left-to-right first, so they follow document order).
- `mapping.py` — `MaskKeys`, the demasking keys. Every masked entity becomes a numbered placeholder `[LABEL_N]` and its original value is recorded. **The numbering is per label, in document order**, and entities are keyed by their exact text: the same string repeated (a phone mentioned twice) reuses one placeholder and yields **one** key, while different wordings (`АО «Росхим»` vs `Акционерного общества «Росхим»`) stay separate keys — only then is the original case form restored exactly. `format_keys_yaml_with_marker` writes a single legacy `Профиль` marker ahead of the Russian sections; `parse_keys_yaml`/`validate_keys_yaml` read both that shape and the legacy envelope-less one. Version/profile-version keys are not written or read - the pre-prod v2 envelope was retired. Section titles resolve against the canonical `labels.yaml` catalogue, so restoring never needs a producing profile. `placeholders_from_document` reverses a parsed keys file into a placeholder→value map for restoring.
- `schemas.py` — `MaskRequest{text}`, `MaskResponse{text, stats, keys}`, `MaskStats{total, occurrences_by_label, unique_by_label}` (aggregates only — raw entity values/offsets never leave the API), `LabelsResponse` (the `GET /api/labels` payload), `UnmaskRequest{text, keys}`, `UnmaskResponse{text}`. `keys` is the demasking YAML (empty string when nothing was masked).
- `docx_service.py` — thin facade over `ooxml/package.py`. Phase 5 replaced the
  python-docx run-level traversal with an **OPC-aware, namespace-independent
  transformer**: `[Content_Types].xml` + relationship files locate every
  text-bearing part (body, tables, all header/footer variants, footnotes,
  endnotes, comments, tracked deletions, text boxes, content controls, core/app
  properties, alt-text attributes and external hyperlink targets). XML is parsed
  securely (`defusedxml.lxml`), text nodes are grouped per logical paragraph,
  masked/restored back-to-front with one document-wide `MaskKeys`, and only
  modified ZIP entries are re-serialized — unrelated entries survive
  byte-for-byte. Genuinely unprocessable embedded objects are a strict error,
  not a warning. See `docs/DOCX_SUPPORT.md`.
- `router.py` — `GET /api/labels`, `POST /api/mask`, `POST /api/mask-file`, `POST /api/unmask`, `POST /api/unmask-file`; inputs over the configurable thresholds are redirected to the job queue with 202 (ADR-0002).
- `jobs/` — durable job execution (ADR-0002 / PostgreSQL substrate ADR-0003): `models.py` (status/phase), `schema.py` (SQLAlchemy `jobs` table + allowed transitions), `repository.py` (pooled sync SQLAlchemy 2 + psycopg 3: staging/enqueue, `FOR UPDATE SKIP LOCKED` claim with a fresh `uuid4()` fencing token, leases/heartbeat, bounded queue → 429, retry/TTL/cancel), `migrations/` (Alembic `0001_jobs`), `storage.py` (atomic id-scoped artifact spool), `routing.py` (threshold + shared enqueue), `api.py` (`/api/jobs` contract), `dependencies.py` (process-wide repo/store). Integration tests require a real PostgreSQL (`TEST_DATABASE_URL`).

The canonical catalogue reports `person, phone, email, link, organization, address` with Russian section titles (in keys-file order): `Физлица, Номера телефонов, Email, Ссылки, Организации, Адреса`. The frontend consumes a generated TypeScript copy (`front/scripts/gen-labels.mjs`, CI checks freshness); there is no `profile_id` and no profile selector.

## API (back, prefix `/api`)

Full request/response schemas, headers, status codes and the job contract —
`docs/API.md`. Summary:

- `GET /labels` — the single canonical label catalogue (`schema_version`, `labels[{id, title}]` in keys-file order). Read-only runtime source for the frontend's generated catalogue.
- `POST /mask` — body `{"text": "..."}` → `{"text": "<masked>", "stats": {"total": 2, "occurrences_by_label": {"person": 1, …}, "unique_by_label": {…}}, "keys": "<YAML>"}`. 422 for empty/whitespace text.
- `POST /mask-file` — multipart field `file` (.docx) → a **flat ZIP** (`Content-Disposition: attachment`, `application/zip`, `Cache-Control: no-store`) holding `<base> (замаскированный).docx`, `<base> (ключи демаскировки).yaml` and `manifest.json`. All statistics/coverage live in the versioned manifest — no `X-Masked-*` headers. The **front unpacks it**, validates the manifest and offers the two files as separate downloads; the user never sees the ZIP. 422 for non-`.docx`/empty files and unprocessable OOXML content.
- `POST /unmask` — body `{"text": "...", "keys": "<YAML>"}` → `{"text": "<restored>"}`. The keys are validated by `validate_keys_yaml` first: empty/malformed YAML, unknown sections, non-list sections, empty values, duplicate values or a duplicated section name all fail with **422** and a Russian `detail`. Unknown placeholders in the text are left as-is.
- `POST /unmask-file` — multipart fields `file` (masked .docx) and `keys` (YAML as a form field) → a **flat ZIP** holding `<base> (демаскированный).docx` and `manifest.json` (with `statistics.unused_keys` — PII never rides in headers). Uploads are validated as OPC/ZIP packages: malformed/non-Word ZIPs → 415, size/entry/zip-bomb limits → 413, encrypted archives → 415.

Both file operations use the **same archive builder and manifest serializer** in sync and job paths (§7); text jobs return the exact canonical `MaskResponse` / `UnmaskResponse` in `result.json`.
- `GET /health` — liveness probe used by the Docker healthcheck; always `ok` (no warm-up).
- `GET /ready` — readiness probe (Phase 6): verifies PostgreSQL; `503` with a
  sanitized Russian reason when the store is unavailable.
- `GET /metrics` — coarse safe counters (`active_jobs`, cache hits/misses/…),
  no PII; degrades instead of 500 on a DB/cache outage.

Errors use the stable envelope `{code, detail, request_id}` + `X-Request-ID`
header (Phase 6 §8.1); `detail` stays Russian and is shown in red inside the
result TextArea. No traceback/path/DB message/PII/keys ever leaves the API.

## Execution model / event-loop safety (Phase 6, §10)

- `masking/executor.py` keeps **two separate bounded pools**: `run_cpu`
  (`JOBS_CONCURRENCY * 2` slots, overflow → 429) for natasha/OOXML work and
  `run_blocking` (`DB_POOL_SIZE + DB_MAX_OVERFLOW` slots) for short
  DB/Valkey/ZIP/filesystem calls. No synchronous DB/ZIP/XML/hash/fs call runs
  directly in an `async def` route.
- Sync file endpoints validate the hard byte limit **before** any ZIP/XML
  paragraph-count heuristic; `jobs/routing.py::count_docx_paragraphs` uses an
  incremental `lxml.iterparse` over `ZipExtFile` (no `archive.read()` of a huge
  XML part).
- `jobs/api.py::submit_job` streams multipart uploads into a private temp file
  with a real byte counter, validates from the path and streams into the spool;
  `/api/jobs/{id}/result` uses `FileResponse`. Cleanup is exception/abort-safe.
- `GET /health` stays responsive during CPU/DB/ZIP work; `/ready` and `/metrics`
  do their DB calls on the IO pool.

## Front (`front/src/`)

- `app/page.tsx` — thin server component: reads `SUPPORT_MAIL` and renders the client `MaskerPage` (`app/masker-page.tsx`). Client component with two top-level modes («Маскировка» / «Демаскировка»). The switcher lives **inside the brand** (`shared/ui/brand.tsx`): the app name followed by a ghost button showing the active mode, opening a hover `Dropdown.Menu` (mirroring ReportCollector's project switcher). The theme switcher and the contact button sit at the header's right edge. Two panes below. Every mode owns its own state slice (`Record<AppMode, ModeState>`), so switching modes swaps the visible draft without losing either one. Since stage 5 the client logic is decomposed: `app/masker-page.tsx` is only a thin view, state/handlers live in `features/masking/use-masker-workflow.ts`, panes in `shared/ui/panes.tsx`, pure API adapters in `shared/api/mask-helpers.ts`, types/constants in `features/masking/types.ts`.

  - *Masking mode.* Left pane holds either the input `TextArea` or (when a `.docx` is selected) a live `DocxViewer` render, with a full-width `ButtonGroup` («Выбрать файл» — tertiary — / «Замаскировать») underneath. A `.txt` file is read client-side and its contents become the input text (it is *not* shown as a file); a `.docx` dropped or picked opens the document preview. The right pane's label row has two `Tabs` («Результат маскировки» / «Ключи демаскировки») plus the stats trigger on the right; the tabs are sized to their content so the stats stay on the same row. The result tab shows the masked `TextArea` (text) or `DocxViewer` (file); the keys tab shows the demasking YAML read-only. Both tabs and the `ButtonGroup` under the pane («Скачать результат маскировки» / «Скачать ключи демаскировки») are **always visible**, disabled until the artifact exists. Masking nothing shows `Маскировка не выполнена: в тексте не нашлось чувствительной информации.` — removing the input (clearing the text or the file) resets the result, so the notice goes away too.
  - *Demasking mode.* The left pane gets its own two tabs — «Данные для демаскировки» (the same text/`.docx` input) and «Ключи демаскировки» (a YAML `TextArea`, pasted or loaded as a `.yaml` file). Both modes render **separate components** (`MaskInputPane` / `DemaskInputPane`), so no input element, preview or text area is shared between them. Three buttons are always present — «Выбрать файл данных», «Выбрать файл ключей» and «Демаскировать» — enabled by completeness: picking a data file switches to the data tab, picking a keys file switches to the keys tab, and «Демаскировать» needs both the data and valid keys. The keys are validated **on the client on every edit** (`validateKeysYaml`): while non-empty and malformed the `TextArea` gets `aria-invalid`/`[data-invalid="true"]`, an inline reason shows under it, and the request is never sent. A stats tooltip (mirroring masking) sits next to the tabs. The right pane (`UnmaskResultPane`) mirrors masking: a `.docx` was demasked → a `DocxViewer` preview; text → a read-only `TextArea`; plus a «Скачать файл» button (always present, disabled until there is something to save).

  - Stats: the shared `shared/mask-stats.ts` builds a `MaskStats{total, byLabel, uniqueByLabel, unused}` and renders both the short result-label (`totalText` → `N фрагментов`) and the `Tooltip` body (`statsBreakdown`). On **demasking** the same shape is used, plus a trailing `Не применено N:` block. Text mode derives it live from the input; **file mode reads it from the validated `manifest.json`** (`statsFromManifest` — never from headers or by parsing key placeholders). When the total is `0` there is no trigger and no tooltip.
- `shared/hooks/use-synced-scroll.ts` — `useSyncedScroll` pairs the input/result surfaces (both text areas in text mode, both DOCX surfaces in file mode) and mirrors their **relative** scroll position (`relativeScrollTop` maps `scrollTop / scrollableRange`, since masked output is shorter). The two `DocxViewer`s are paired via their `surfaceRef`.
- `shared/ui/docx-viewer.tsx` — client component that renders a `.docx` Blob into a scrollable pane via `docx-preview`'s `renderAsync`. Used for the selected source file, the masked result **and** the restored (demasked) result; accepts an optional `surfaceRef` so the parent can sync its scroll. Its optional `highlight` prop lists substrings to wrap in a `<mark class="docx-marker">` (yellow) — **preview-only**: the masked preview highlights the `[LABEL_N]` placeholders (`placeholdersFromKeys`), the demasked preview highlights the restored values (`valuesFromKeys`), and the downloaded document is never altered. The document is rendered **once per blob**; a change to `highlight` re-applies the marks in place (undoing the previous pass first) without re-parsing the document, so editing keys does not re-render the preview.
- `shared/ui/theme-switcher.tsx` — `ThemeSwitcher`, the header theme control: icon-only HeroUI `Tabs` (no panels) with `Computer`/`SunLight`/`HalfMoon` from `iconoir-react`; each tab shows its name in a HeroUI `Tooltip` on hover. Drives `next-themes` (controlled `selectedKey`), so the preference persists in `localStorage` and survives a reload. `app/providers.tsx` mounts the `ThemeProvider` (`attribute="class"`, `defaultTheme="system"`, `enableSystem`).
- `shared/ui/support-button.tsx` — `SupportButton`, «Связаться с разработчиком» in the header's right slot (mirrors ReportCollector): an icon-only HeroUI `Link` (`mailto:`) that expands its label on hover/focus. `SUPPORT_MAIL` is read server-side in `app/page.tsx` and passed as a prop (never a `NEXT_PUBLIC_` value); an empty value hides the control. `supportMailto()` is unit-tested.
- `app/api/mask/route.ts` — server route that validates JSON and calls `proxyMaskRequest`.
- `app/api/mask-file/route.ts` — server route that replays the multipart upload to the back and streams back the flat ZIP plus `Content-Disposition` / `Cache-Control: no-store` (no semantic X-* headers).
- `app/api/labels/route.ts` — proxies the canonical label catalogue.
- `app/api/unmask/route.ts` — server route that validates `{text, keys}` and calls `proxyUnmaskRequest`.
- `app/api/unmask-file/route.ts` — server route that replays the masked file + `keys` form field to the back and streams back the restore ZIP (restored `.docx` + `manifest.json`) with `Cache-Control: no-store`.
- `shared/api/mask.ts` — `proxyMaskRequest` / `proxyUnmaskRequest`: forward to `BACKEND_INTERNAL_URL`, pass the body/status back verbatim.
- `shared/api/mask-archive.ts` — `unpackMaskArchive`: unpacks the `/api/mask-file` ZIP (via `jszip`) into the masked document and the keys file, with the names taken from the archive entries. The two halves are then offered as separate downloads.
- `shared/config.ts` — `getBackendInternalUrl()` (throws if unset).
- `src/instrumentation.ts` — Node-runtime hook that installs a bounded global
  undici dispatcher (`headersTimeout: 60_000` / `bodyTimeout: 600_000`), so a
  large synchronous DOCX round-trip is generous-but-finite; `connectTimeout`
  keeps its default so a dead backend still fails fast. `shared/api/proxy.ts`
  streams backend responses through `new Response(response.body)` (no
  `arrayBuffer` copy) with the canonical per-route map in `front/src/shared/api/timeouts.ts
  (15 s JSON/job, 120 s upload, 300 s result). The old `0`/`0` unbounded dispatcher and
  `maxDuration = 3600` were removed in stage 4 (F-08) — long work goes through
  the durable job API, never a held-open connection.

The masked file is named `<original> (замаскированный).docx` and the keys `<original> (ключи демаскировки).yaml` (inside the ZIP; the front takes the names from the archive entries).

The header brand mirrors ReportCollector: the `ruschem_icon.png` icon (also the favicon/apple-touch icon) next to «ДокМаскер» in the shared brand styling.

## Deployment (prod)

`just prod` builds the prod-profile images and starts `dm-back` + `dm-worker` +
`dm-front` (the worker is mandatory — large jobs need the shared `jobs-data`
volume). The front publishes `FRONT_PORT`, the back publishes `BACKEND_PORT` to
loopback only (`127.0.0.1`). The backend starts instantly — there is no model
download and no persistent cache volume. The base image is Alpine
(`python:3.15-alpine`), matching the project convention, because dropping
PyTorch removed the glibc/musl constraint.

## CI stack (`infra/compose.ci.yaml`)

`infra/compose.ci.yaml` is the isolated stack the `production` CI job boots to run the Playwright smoke tests against the real prod images (`dm-back:ci`, `dm-front:ci`). It never loads the developer's `.env`, volumes or log dirs and uses non-conflicting host ports: front **15001**, back **18001**. The backend answers `/health` immediately, so the stack wait timeout is short. Details and the job table — `docs/WORKFLOW.md`.

## Testing

- **Back** (`back/tests/`): pytest with `filterwarnings=error` and ≥80% branch coverage. The pipeline is deterministic, so tests assert exact output — no stubs and no network. Files: `test_rule_based.py`, `test_config.py`, `test_labels_api.py`, `test_mask.py`, `test_mapping.py`, `test_keys_v2.py`, `test_docx.py`, `test_ooxml.py` (synthetic OPC fixtures, §6.6), `test_unmask.py`, `test_mask_file.py` (multipart + manifest), `test_cache.py`, `test_jobs_*.py`, `test_executor.py`.
- **Front** (`front/src/**/__tests__/`): `bun test` with happy-dom (`front/src/test-setup.ts`). Covers the server-side proxy contract (`shared/api/mask.ts`) and config resolution (`shared/config.ts`).
- **e2e** (`front/e2e/`): Playwright `smoke` project against a running stack (`/api/health`, page render, text-mask round-trip). Setup — `docs/WORKFLOW.md`.
