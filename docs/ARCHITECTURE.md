# DocMasker Architecture

Sensitive-information masking in text via a deterministic rule-based pipeline. Monorepo: `front/` (Next.js), `back/` (FastAPI), `docs/`.

## System diagram

```
Browser ── http://<host>:5001 ──► front (Next.js, dev: bun dev / prod: standalone)
                                   ├── /            → the single masking page
                                   └── /api/health  → healthcheck
                                   └── /api/mask    → server-side proxy (text)
                                   └── /api/mask-file → server-side proxy (DOCX)
front ── BACKEND_INTERNAL_URL ──► back (FastAPI/uvicorn, :8001)
                                     ├── POST /api/mask      → masker → masked text
                                     └── POST /api/mask-file → masker → masked .docx
```

The browser talks only to the front origin; the front proxies `/api/mask` to the back server-side, so there is **no CORS for the browser** (CORS is configured on the back only for direct dev access, e.g. curl). The backend starts instantly — there is no model to load.

## Services and compose profiles

`compose.yaml` (name: `docmasker`) defines two service pairs:

| Service | Container | Profile | What |
|---|---|---|---|
| `back` / `back-dev` | `dm-back` / `dm-back-dev` | prod / dev | FastAPI, uvicorn, port `BACKEND_PORT` |
| `front` / `front-dev` | `dm-front` / `dm-front-dev` | prod / dev | Next.js standalone (prod) or `bun dev` (dev, hot-reload), port `FRONT_PORT` |

Key point: **ports and hosts are set once in the root `.env`** and spread into the Dockerfiles (build-arg `PORT`/`BACKEND_PORT`), port publishing, healthchecks, and internal URLs. All mandatory (`:?` in compose — fails with a clear error instead of staying silent). `BACKEND_INTERNAL_URL` is derived from the service name + `BACKEND_PORT` (e.g. `http://back-dev:8001` in dev).

## Environment variables (root `.env`, single source)

Mandatory:
- `BACKEND_PORT` (8001), `FRONT_PORT` (5001) — single sources for ports; `BACKEND_HOST`/`BACKEND_HOST_DEV` (`back`/`back-dev`) name the services in the compose networks.
- `AUTH_APP_ORIGIN` — the app host used to derive CORS origins (inherited from the ReportCollector convention); in DocMasker the browser hits only the front origin, so it is mostly a placeholder.

Masking:
- `MASK_LABELS` (pipe-separated, optional) — overrides `back/src/masking/constants.py::DEFAULT_LABELS`.

## Masking pipeline (`back/src/masking/`)

Deterministic, rule-based: `natasha` (morphology/rules) + `phonenumbers` + regular expressions. No model, no PyTorch, no network at runtime.

- `rule_based.py` — `RuleBasedMasker`, the core. Detects spans in a fixed order — **emails → phones → persons → addresses → organisations** — then merges all spans into one non-overlapping set and rewrites the text once, back-to-front, so offsets stay valid.
  - *Emails* — a regex covering subdomains and `+`/`.` local parts.
  - *Phones* — `phonenumbers.PhoneNumberMatcher(text, "RU")`, so `+7 …`, `8 …` and parenthesised forms are all recognised.
  - *Persons* — `natasha.NamesExtractor`; a match is kept only when the fact has **both** a given name and a surname/patronymic (drops the extractor's lone-token false positives such as prepositions or bare surnames).
  - *Addresses* — `natasha.AddrExtractor` yields one match per component; adjacent components are glued into a single `[ADDRESS]` span. A span is kept only if it carries an address keyword (`г.`, `ул.`, …) or mixes letters with digits — a digit-only run (`270023`) is **not** an address, so passport/index-like numbers are not masked.
  - *Organisations* — no `OrganisationsExtractor` exists in natasha 1.6, so they are matched in two ways. Abbreviated legal forms (`ООО`, `АО`, `ПАО`, `ИП`, …) are a regex: prefix + optional punctuation + the following name, quoted with any of `"" «» „“ “” ‘’ '' <>` (the quotes are part of the mask) or a capitalised word chain. Spelled-out legal forms (`Акционерное общество`, `Общество с ограниченной ответственностью`, `Публичное/Закрытое/Открытое акционерное общество`, `Индивидуальный предприниматель`, `Унитарное предприятие`) are matched **lemma-wise**: natasha lemmatises each word of the form once (`_ORG_FULL_FORMS`) and a text token counts when its lemma is equal or close enough (`_ORG_LEMMA_RATIO`), so **any grammatical case** (`Акционерным обществом`, `Общества с ограниченной ответственностью`) and small typos (`акциноерным`) match without a hand-written case table. A trailing name (quoted, quotes included) is absorbed; the form alone is masked when there is no name. A second regex covers `организация/компания/… <Name>`.
  - `get_masker()` builds the masker once per process (`lru_cache`) — the natasha extractors load an in-memory morphological dictionary (`MorphVocab`) and are expensive to construct.
- `service.py` — thin orchestration: `mask_document` runs the masker over a `MaskRequest`; `mask_text` performs the back-to-front `[LABEL]` rewrite.
- `schemas.py` — `MaskRequest{text}`, `MaskResponse{text, masked_entities}`, `MaskedEntity{label, text, start, end, score}` (score is `1.0` for deterministic matches — kept for API-contract stability).
- `docx_service.py` — DOCX masking that **preserves formatting**: iterates every paragraph (body, all table cells recursively, default header/footer of each section) via `iter_paragraphs`, runs `detect` on each paragraph's joined text, and rewrites only the overlapping slices of the XML runs (`paragraph.runs` plus hyperlinked runs from `iter_inner_content()`). An entity spanning several runs (`АО «Росхим»` = `АО «` | `Росхим` | `»`) collapses into a **single** `[LABEL]` — the marker goes in the run holding the entity start, the covered slices of the following runs are blanked; surrounding formatting (bold, size, styles) is untouched. A hyperlink whose text is fully replaced by a mask (e.g. a `mailto:` email) is **unwrapped**: the `w:hyperlink` element is replaced by its runs and the relationship is dropped, so the masked document carries no dangling link.
- `router.py` — `POST /api/mask` (text) and `POST /api/mask-file` (multipart DOCX → binary response).

Default labels: `person, phone, email, address, organization`.

## API (back, prefix `/api`)

- `POST /mask` — body `{"text": "..."}` → `{"text": "<masked>", "masked_entities": [{label, text, start, end, score}]}`. 422 for empty/whitespace text.
- `POST /mask-file` — multipart field `file` (.docx) → the masked `.docx` as the response body (`Content-Disposition: attachment`, `application/vnd...docx`), with the masked-entity count in the `X-Masked-Count` header and a per-label JSON map in `X-Masked-Breakdown` (e.g. `{"person":2,"phone":1}`). 422 for non-`.docx` or empty files.
- `GET /health` — readiness probe used by Docker healthcheck; always `ok` (no warm-up).

Errors carry a Russian `detail`; the front renders them in red inside the result TextArea.

## Front (`front/src/`)

- `app/page.tsx` — client component: two panes. Left pane holds either the input `TextArea` or (when a `.docx` is selected) a live `DocxViewer` render, with a full-width `ButtonGroup` («Выбрать файл» — tertiary — / «Замаскировать») underneath. A `.txt` file is read client-side and its contents become the input text (it is *not* shown as a file); a `.docx` dropped or picked opens the document preview. Right pane shows the masked result: a `TextArea` for text mode (with a «Скачать файл» button that saves the masked text as a `.txt`), or a `DocxViewer` of the masked document plus «Скачать файл» in file mode. The result label shows `N фрагментов`; hovering it opens a HeroUI `<Tooltip>` with the per-label breakdown (`2 физлица + 2 телефона + 1 организация`). When nothing was masked (`N = 0`) there is no trigger and no tooltip. Any error replaces the result with a red read-only TextArea (the document preview is hidden) and the synced scroll is disabled for that state.

  - Labels/plurals: `totalText`/`statsBreakdown`/`plural` map the backend's entity labels (`person`, `phone`, `email`, `address`, `organization`) to the Russian forms shown in the breakdown.
- `shared/hooks/use-synced-scroll.ts` — `useSyncedScroll` pairs the input/result surfaces (both text areas in text mode, both DOCX surfaces in file mode) and mirrors their **relative** scroll position (`relativeScrollTop` maps `scrollTop / scrollableRange`, since masked output is shorter). The two `DocxViewer`s are paired via their `surfaceRef`.
- `shared/ui/docx-viewer.tsx` — client component that renders a `.docx` Blob into a scrollable pane via `docx-preview`'s `renderAsync`. Used for both the selected source file and the masked result; accepts an optional `surfaceRef` so the parent can sync its scroll.
- `app/api/mask/route.ts` — server route that validates JSON and calls `proxyMaskRequest`.
- `app/api/mask-file/route.ts` — server route that replays the multipart upload to the back and returns the binary plus `X-Masked-Count` / `X-Masked-Breakdown` / `Content-Disposition`.
- `shared/api/mask.ts` — `proxyMaskRequest`: forwards to `BACKEND_INTERNAL_URL/api/mask`, passes the body/status back verbatim.
- `shared/config.ts` — `getBackendInternalUrl()` (throws if unset).
- `src/instrumentation.ts` — Node-runtime hook that installs a global undici dispatcher with `headersTimeout: 0` / `bodyTimeout: 0`, so the server-side backend proxy has **no fetch timeout**: a large DOCX round-trip can never be aborted by an arbitrary ceiling. `connectTimeout` keeps its default, so a dead backend still fails fast. Both proxy routes also set `maxDuration = 3600` for parity with hosted environments.

The masked file is named `<original> (замаскированный).docx` — the back advertises it via an RFC 5987 `filename*=UTF-8''...` (percent-encoded) value in `Content-Disposition`, which the page decodes (`parseDispositionFilename`) so Cyrillic names survive.

The header brand mirrors ReportCollector: the `ruschem_icon.png` icon (also the favicon/apple-touch icon) next to «ДокМаскер» in the shared brand styling.

## Deployment (prod)

`just prod` builds the prod-profile images and starts `dm-back` + `dm-front`. The front publishes `FRONT_PORT`, the back `BACKEND_PORT`. The backend starts instantly — there is no model download and no persistent cache volume. The base image is Alpine (`python:3.14-alpine`), matching the project convention, because dropping PyTorch removed the glibc/musl constraint.

## CI stack (`infra/compose.ci.yaml`)

`infra/compose.ci.yaml` is the isolated stack the `production` CI job boots to run the Playwright smoke tests against the real prod images (`dm-back:ci`, `dm-front:ci`). It never loads the developer's `.env`, volumes or log dirs and uses non-conflicting host ports: front **15001**, back **18001**. The backend answers `/health` immediately, so the stack wait timeout is short. Details and the job table — `docs/WORKFLOW.md`.

## Testing

- **Back** (`back/tests/`): pytest with `filterwarnings=error` and ≥80% branch coverage. The pipeline is deterministic, so tests assert exact output — no stubs and no network. Files: `test_rule_based.py` (CAPSLOCK, inflected names, phone formats, emails, addresses, org prefixes, label overrides), `test_mask.py` (service + `/api/mask` endpoint), `test_docx.py` (run-level formatting preservation), `test_mask_file.py` (multipart endpoints + download headers).
- **Front** (`front/src/**/__tests__/`): `bun test` with happy-dom (`front/src/test-setup.ts`). Covers the server-side proxy contract (`shared/api/mask.ts`) and config resolution (`shared/config.ts`).
- **e2e** (`front/e2e/`): Playwright `smoke` project against a running stack (`/api/health`, page render, text-mask round-trip). Setup — `docs/WORKFLOW.md`.
