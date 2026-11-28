# DOCX / OOXML coverage (Phase 5)

DocMasker masks `.docx` at the **OPC package level**. `back/src/masking/ooxml/`
builds a namespace-aware inventory from `[Content_Types].xml` and relationship
files (QName/URI, never hardcoded prefixes), parses selected XML parts with
`defusedxml.lxml` (no DTD, no external entities, no network) and rewrites
text-node slices. Only modified ZIP entries are re-serialized; **all other
entries are copied byte-for-byte**. `python-docx` is no longer used for masking.

## Bounded inventory and single-pass transform (DM-3)

`build_inventory()` now returns **metadata only** — part names, content types,
relationship type/mode, category, disposition and `CoverageReport`. It never
retains a processable part's decompressed XML. The transform keeps the input
`ZipFile` open and calls `_read_part()` immediately before parsing each part;
the payload and its tree are released before the next part is read. Secure
parsing (`defusedxml.lxml`, no DTD/entities/network, 32 MiB part cap),
namespace-URI matching, split-across-runs handling, fail-closed unsupported text
parts and the `checkpoint` hooks (before each part and between detection
windows) are unchanged. The path-based worker transform stays on disk: unchanged
entries are stream-copied to a unique sibling `.dm-transform-*` temp file, and
the spool target is published with an atomic `Path.replace` only after the whole
package was transformed and checked. On collision, cancellation, parse/serialization error
or worker failure the temp file is removed and a pre-existing target is never
deleted, so no partial DOCX is downloadable or cacheable.

The job path replaces the per-occurrence `all_entities` list with `EntityStats`
(total, per-label occurrences and distinct counts reusing `MaskKeys` numbering);
`build_mask_manifest()` accepts those aggregate stats. The sync bytes API still
returns occurrence detail for callers that need it. Unmask compiles one
longest-first alternation per transform and performs one bounded scan per
logical text container, so repeated markers and markers split across runs stay
correct and re-applying a restore does not cascade.

## Processed (masked and restored)

| Part / content | How |
|---|---|
| Main document body, tables, nested tables | `w:t` grouped by closest `w:p` |
| Default / first-page / even-page headers & footers, tables inside them | resolved through relationship Type URI; shared parts once |
| Hyperlink visible text **and** external relationship `Target` | `w:t` + `TargetMode="External"` attribute allowlist; link element preserved, target restored on unmask |
| Footnotes / endnotes | `word/footnotes.xml`, `word/endnotes.xml` |
| Comments (text + author/initial metadata) | `word/comments.xml` and its text nodes |
| Tracked changes (`w:ins`, `w:del`, `w:delText`) | `w:delText` is text-bearing; deleted PII is masked |
| Text boxes / shapes | `w:txbxContent` nested paragraphs + DrawingML `a:t` |
| Content controls (`w:sdt`) | nested WordprocessingML paragraphs |
| Document properties | `docProps/core.xml` (creator, title, subject, description, lastModifiedBy, keywords, category) and `docProps/app.xml` (Company, Manager) |
| Alt text / title / description | explicit attribute allowlist on `wp:docPr` (`descr`, `title`, `name`) |
| Other text-bearing XML parts | found through the package inventory; processed when they carry one of the supported text QNames |

One entity spanning several runs/text nodes within one paragraph becomes
**one** placeholder. Independent document-property fields (creator, title,
lastModifiedBy, Company, Manager…) and external relationship targets are each
processed as their own container — they are never concatenated, so two fields
with the same FIO cannot merge into a false cross-field entity. Key numbering is
document-wide across every processed part (`MaskKeys`). Masking and
restoration use the same inverse text-node rewrite. Restoration is a single
non-cascading scan per logical container: a compiled longest-first alternation
finds the known placeholders, then the mapped values are written into the
text nodes afterwards, so a restored value containing another marker is never
rescanned. A marker split across runs still collapses to one value because the
container is joined for matching before the back-to-front rewrite. At mask
time the package is rejected with a clear 422 if a source literal equals a
marker that run allocates (see `docs/MASKING_CONFIGURATION.md`).

Each text-bearing container is passed to the detector with `prefilter=True`
(Phase 7): detectors whose cheap precondition cannot match are skipped, while
`always_mask` literals still run over the full text. The address precondition
uses the same validated, anchored lexicon marker matcher as the expensive
extractor, so short fragments such as `г. Москва, ул. Ленина` are not skipped
while `ш` inside an unrelated word is not treated as an address marker. This
makes documents with thousands of PII-free paragraphs cheap; see
`docs/PERFORMANCE.md`.

Worker jobs thread a cooperative `checkpoint` through `execute()` →
`mask_docx_in_memory()` → `transform_package()` → `detect()`: it runs before
each expensive OPC part and between long-text windows. A cancellation or lease
loss therefore stops additional document work promptly, publishes no result and
leaves no cache entry; the old worker never cleans up artifacts unless PostgreSQL
acknowledges the terminal `cancelled` state.

## Not processed (documented limitations)

- **Raster/vector image pixels** — no OCR in this scope. `raster_image_ocr:
  "not_performed"` is reported in `manifest.json`; images are copied through
  unchanged. Do not claim full image-content anonymization.
- Field codes / `w:instrText`, hidden `w:vanish` text and chart cache values are
  **not** ordinary text-bearing WordprocessingML and remain outside the current
  anonymization promise. Unknown XML parts (e.g. `customXml/item1.xml`) are
  processed generically: only their text nodes are rewritten, attributes and
  structure are preserved. Chart/spreadsheet XML with text-bearing content is
  classified as unprocessed and the package is rejected rather than reported
  complete.

## Rejection (strict blocking, §6.4/§6.5)

A package is rejected with a clear non-PII error (HTTP 422) when it contains
**embedded binary/OLE objects** (`*.bin`, `word/embeddings/*`) that cannot be
inspected safely, or malformed/unsafe XML (DTD, external entities, oversized
part). There is no "best-effort warning" for supported content: the normal path
is a successful result with `complete: true` in `manifest.json`. Parts with an
explicit `copy` disposition (styles, numbering, settings, theme, media) are
copied verbatim; parts with `unprocessed` text-bearing content (charts and
similar) cause the same 422 rejection, so `complete: false` never reaches a
delivered archive.

There is **no** `X-Docx-Unsupported-Parts` plumbing and no frontend warning —
that mechanism was removed in Phase 5.

A source literal equal to a marker allocated by the same run also fails closed:
sync routes return `422` with stable code `marker_collision` and a fixed Russian
detail that names only marker tokens, never source text or keys. The durable
path records a safe `error_code`, publishes no result artifact and writes no
cache entry. `back/tests/test_transform_cleanup.py` pins the cleanup contract
(no partial target, no leftover `.dm-transform-*`, no cache write, no deletion
of a pre-existing artifact) for path and in-memory flows;
`back/tests/test_jobs_worker.py` covers the same outcomes against PostgreSQL.

## Result manifest (§7)

Both sync and job file operations return the same flat ZIP and the same
versioned `manifest.json`:

```text
mask_file:   <base> (замаскированный).docx + <base> (ключи демаскировки).yaml + manifest.json
unmask_file: <base> (демаскированный).docx + manifest.json
```

The manifest carries `schema_version`, `operation`, `status`, `complete`,
ruleset/config versions, input hash/size, output names, `statistics`
(occurrences/unique by label, `unused_keys` for unmask), `coverage`
(processed parts with per-part category and accurate `text_nodes`, unprocessed
*text* parts, rejected parts, OCR status) and `cache`. `processed_parts` lists
every successfully inspected supported part, including parts with zero
detections; `complete` is true only when the supported scope was fully
inspected and no text-bearing part was unprocessed or rejected. The frontend
requires and validates it — a missing/malformed/wrong
version is a clear result error, never "0 entities". Semantic metadata no
longer lives in `X-Masked-Count` / `X-Masked-Breakdown`; both were removed.

Runtime validation lives in `front/src/shared/api/manifest-validation.ts` and
walks the whole producer contract (schema version, operation/status enums, all
nested objects and arrays, item types and non-negative safe-integer counts).
`mask_file` must carry the three occurrence aggregates, `unmask_file` must carry
`unused_keys`; known optional fields are checked when present and unknown extra
fields are tolerated. Generated OpenAPI DTOs are compile-time only and do not
cover this ZIP-internal file, so the guard is not a cast. Rejection is a stable
`ManifestValidationError` (`docs/API.md` §`manifest.json`) with a fixed Russian
detail and no PII. This closes the Phase 3.4 finding in
`docs/FRONTEND-AUDIT-BASELINE.md` §5.

## Demasking input placeholder count (Phase 3.3)

The demasking input pane shows `N фрагментов` **before** the operation runs.
Two different numbers are displayed and must not be conflated: the headline is
the number of **distinct values** in the parsed keys (`uniqueTotal`), while the
tooltip line `unique (total с повторами)` adds the **total occurrences** of
each `[LABEL_N]` in the uploaded masked DOCX. The unused-key line comes from the
`unmask_file` manifest after the operation (`docs/API.md` §`manifest.json`).

The backend does **not** already compute those occurrence aggregates:
`ooxml/package.py::_transform_part` scans each logical container with the
longest-first alternation built by `mapping.py::compile_placeholders` and keeps
only a `used_placeholders` set (presence, for `unused_keys`); `EntityStats` and
`build_mask_manifest` aggregate **detected entities of the mask input**, a
different document and a different meaning. `UnmaskResponse` is text-only and
`unmask_file` answers with a flat ZIP, so there is no typed HTTP response to
extend. Emitting counts from the unmask manifest would make them available only
after the operation, change the meaning from "every marker-shaped token in the
uploaded package" to "known markers in processable parts", and break the
existing pre-operation smoke assertion (`front/e2e/smoke.spec.ts`, demask stats
visible before the run). Adding a pre-operation counting endpoint would be a new
product/API surface. Both alternatives were rejected; the client scan stays.

`front/src/shared/api/placeholder-count.ts` therefore keeps the established
raw-XML semantics (every non-directory `.xml` part except
`[Content_Types].xml`, `/\[([A-Z]+)_\d+\]/g`, labels lowercased — no inference
from filenames, headers, YAML text or placeholder uniqueness). The scan now
uses a **narrow read-only ZIP walker** (`front/src/shared/api/bounded-zip-scan.ts`)
and the browser-native `DecompressionStream("deflate-raw")`. JSZip 3.10.2 is
**not** used on this path any more; it stays for result-archive unpacking only.

The exact guarantees (all failures surface through the workflow's existing
Russian result-pane error, never a fabricated `0`):

1. `blob.size` is checked against `maxSourceBytes` (**25 MiB**, the default
   `UPLOAD_MAX_MB` from `back/src/config.py` / `.env.example`, pinned by a drift
   test) **before** `blob.arrayBuffer()`. No metadata read or inflate happens
   on an oversized file.
2. The end-of-central-directory and central directory are parsed once. More
   than `maxParts` (**10,000**, the backend hard limit) entries, ZIP64 markers,
   encryption flags, unsupported compression methods, or declared per-entry /
   aggregate XML sizes above `maxXmlBytes` (**64 MiB**) fail closed **before any
   part is inflated**. Declared sizes are untrusted: this is defence in depth,
   **not** proof of a decompression bound.
3. Each eligible XML part is inflated incrementally, one part at a time.
   **Actual decompressed bytes** are counted per output chunk against the
   cumulative 64 MiB ceiling; a chunk that would cross it is discarded without
   decoding, so **no byte past the ceiling is decoded or retained**. The
   platform may inflate up to one further output chunk before termination
   (observed ≤ 64 KiB in local Bun/Chromium probes; not a spec guarantee) — that
   chunk is never processed. A `.xml`-named entry without XML markup is skipped
   at its first non-whitespace character.
4. Text is decoded with `TextDecoder("utf-8", { stream: true })`, so multi-byte
   characters split between chunks are correct. Marker matching uses a carry
   capped at **512 characters** and counts a match only when it finishes in the
   new chunk; repeated, adjacent and chunk-spanning markers are therefore
   counted exactly once with the original whole-string regex meaning.
5. Parts are processed sequentially; at most one compressed view and one
   decompressed chunk are live. No decompressed string or byte array is kept
   between parts — only the per-label counts and the bounded carry.
6. `countPlaceholdersInDocx` memoises one promise per Blob in a `WeakMap`, so a
   mode switch or re-selection never scans twice, and concurrent callers share
   one scan. Genuine inflate/ZIP errors propagate as errors. Stale results are
   already rejected by the `active` flag in `use-masker-workflow.ts`; this path
   adds no second generation mechanism.

This frontend pass is a **usability/resource layer, not a security boundary**.
The backend remains authoritative (`docx_validation.py`: streamed upload bytes,
entry count, uncompressed package size, compression ratio, encryption, hostile
names — §`Rejection` above and `docs/API.md`). Alternative readers, bundle cost
and before/after measurements are in `docs/PERFORMANCE.md` §"Frontend bounded
placeholder scan". The remaining overlap with the `docx-preview` render is the
deferred §4.5 finding in `TODO.md` (it needs a render-complete callback, not a
DOM heuristic). Edge cases are pinned in
`front/src/shared/api/__tests__/placeholder-count.test.ts`.

**Known scope boundary.** The scan deliberately keeps its established scope:
it counts marker-shaped tokens in every XML part (including copy-safe parts
such as `word/styles.xml`) and does not scan `.rels` parts. A hand-crafted
package with markers in a part the backend copies verbatim, or in an external
relationship target, can therefore differ from what the backend actually
restores. Documents produced by DocMasker place markers only in processed text
nodes, so the two agree in normal use. Narrowing or widening the client scope
would change the displayed meaning (and the pinned unit/e2e contracts), so it
is a product decision rather than part of this safety change.

## Frontend preview isolation

`front/src/shared/ui/docx-viewer.tsx` renders every Blob generation into its
own **detached** `document.createElement("div")` staging host. Only a still
current generation moves those nodes (via `replaceChildren(...nodes)`) into
the visible `.docx-viewer__surface`; an already-started `renderAsync` that
finishes after a blob switch can only write into its own obsolete host and is
never committed. Hosts are cleared on blob switch, unmount, success and
failure; stale completions never apply highlights, set error state or leave
timers behind. The lazy dynamic import, 120 ms highlight debounce,
`surfaceRef` scroll contract, preview-only idempotent marks, existing Russian
error message and "no re-render on highlight-only changes" are unchanged.

Detached staging was chosen after reading the installed `docx-preview@0.4.1`
(`front/node_modules/docx-preview/dist/docx-preview.js`): `renderAsync`
(lines 3998–4009) only calls `parseAsync` + `renderDocument`, clears the
supplied body/style containers and appends the generated nodes; it never
measures or reads computed styles from them. The only size-measuring paths —
`computePixelToPoint` (lines 2795–2802, `offsetWidth` on a probe in
`document.body`) and `updateTabStop` (lines 2803–2807,
`getBoundingClientRect`) — are reached solely from `refreshTabStops`, which is
gated by `options.experimental` (lines 3934–3943) and is off by default. No new
dependency or CSS is needed. Regression coverage:
`front/src/shared/ui/__tests__/docx-viewer-lifecycle.test.tsx` (late A with
`isCurrent` ignored, switch before import resolves, late resolve/reject after
unmount, current vs stale failures, highlight-timer cancellation, detached
host identity and committed-node preservation).

The preview highlight pass (`highlightTexts`) applies literal needles to the
rendered DOM with a **durable overlap rule**: scanning starts at the earliest
position and, at that position, the **longest** matching needle wins — never
the one listed first in the keys. This mirrors the backend's longest-first
unmask alternation (`mapping.py::compile_placeholders`), so a shorter key such
as «Иван» cannot shadow «Иван Петров» in a demasked preview. Needles are
deduplicated and sorted longest-first before they are escaped into one regex;
equal-length needles cannot both match the same position, so keys order cannot
change which value is highlighted. Consecutive text nodes inside one logical
container (nearest `p`, `div`, list cell, heading or blockquote) are matched as
one string, so a placeholder split across rendered runs is highlighted; a
match never crosses a container boundary. Each covered node slice receives its
own `<mark class="docx-marker">`, and every pass first removes previous marks,
so re-application after a keys edit never nests or duplicates a mark. These
cases are pinned by `front/src/shared/ui/__tests__/docx-viewer.test.ts`.

## Result-archive unpacking (Phase 3.2)

Both sync file responses and `/api/jobs/{id}/result` are **one flat ZIP**
(`docs/API.md` §`POST /api/mask-file`, §`GET /api/jobs/{id}/result`). The
frontend has to unpack it: `manifest.json` is the only authoritative source of
statistics/coverage, the entry names are the download filenames, and the
`.docx` Blob feeds the in-page preview. JSZip **3.10.2** is kept.

### Data lifecycle (STATIC — read from source)

| Object | Coexists with | Released when |
|---|---|---|
| `JobAccepted` handle + `sessionStorage` copy | polling, result fetch, unpack | `endJob()` on terminal state or dismissal (`use-job-lifecycle.ts:42–61`); only opaque id persisted |
| Status JSON (`jobs.ts:77–82`) | the next fetch decision | after the terminal status is read; the URL string lives only in the local call |
| Result response Blob (`jobs.ts:85–91`) | the JSZip ArrayBuffer during unpack | at function exit/throw; JSZip copies it into one `ArrayBuffer` via `Blob.arrayBuffer()` (`lazy-zip.ts:21–31`, JSZip `lib/utils.js::prepareContent`) |
| JSZip instance + compressed subarray views | every extracted entry Blob | when the local `zip` leaves scope at return/throw (`jobs.ts:93–107`); clearing `zip.files` early was measured to give no benefit (below) |
| Extracted entry Blobs (`jobs.ts:95–107`) | `JobResult.entries` → `MaskedBundle`/`RestoredBundle` | after `jobBundle`/`jobToMaskResponse` (`mask-helpers.ts:22–56`) commits state (`use-masker-workflow.ts:239–250`, `290–294`) or the error branch clears it (`:251–257`, `:303–309`); on abort/failure `entries.clear()` runs first (`jobs.ts:101–105`) |
| `manifest.json` text → validated `ResultManifest` | the bundle | text released after `parseResultManifest`; object lives with the bundle (`mask-archive.ts:41–43`, `mask-helpers.ts:39–55`) |
| Preview DOM (detached staging → surface) | committed nodes + highlight timers | cleared on switch/unmount/success/failure (`docx-viewer.tsx:125–141`, `157–220`); rendered nodes do not retain the source Blob |
| Download object URL (`result-download.ts:4–24`) | the anchor until `click()` hands off | anchor removed immediately, URL revoked on the next macrotask in `finally`; the browser reads the same Blob, so no copy is made |
| Demask input scan (`placeholder-count.ts` + `bounded-zip-scan.ts`) | selected `File`, one compressed view + one decompressed chunk at a time | promise cached per Blob in a `WeakMap`; released when the Blob is collected (separate pre-operation path, not a result archive; native streaming inflate, no JSZip on this path) |

Abort semantics (durable): `waitForJob` checks the signal before the result
fetch, after the fetch, after `blob()`, after `loadAsync()` and before/after
every entry read (`jobs.ts:84–100`). JSZip has no `AbortSignal`, so an already
started inflate cannot be stopped through its API; the loop stops at the next
boundary and clears any already extracted Blobs instead of publishing a
partial result. `useJobLifecycle` aborts its local polling controller on
unmount (`use-job-lifecycle.ts:184–194`) without issuing a `DELETE` — only a
confirmed server cancellation may claim the job was cancelled. Object URLs
are already revoked on both success and failure; no change was needed.

### Measurements — SYNTHETIC / MEASURED (local, 2026-11-27)

No large committed browser fixture exists, so two deterministic archives were
generated locally with JSZip 3.10.2 (seed 20261127), never committed:
**media-heavy** = 8,624,882 B flat ZIP (inner `.docx` 8,620,578 B with 8 MiB
pseudo-random media + 6,000 paragraphs), **text-heavy** = 2,270,285 B flat ZIP
(60,000 paragraphs, ~12 MiB XML before deflate) + keys/manifest. All entries
use `DEFLATE`, matching `back/src/masking/archives.py:40–48`.

| Scenario (SYNTHETIC) | Chromium wall median [min..max] | Chromium peak Δ JS heap median [min..max] | Bun wall median [min..max] |
|---|---:|---:|---:|
| `waitForJob` unpack, media-heavy | 28.9 ms [27.6..183.2] | 25.75 MiB [25.73..32.19] | 22.3 ms [19.4..36.0] |
| `waitForJob` unpack, text-heavy | 10.6 ms [9.3..12.3] | 6.89 MiB [4.37..6.91] | 4.6 ms [4.3..5.7] |
| sync mask unpack, media-heavy | 30.6 ms [30.1..50.4] | 25.87 MiB [25.86..26.63] | 20.2 ms [19.3..23.0] |

7 runs per cell. Chromium: Chrome on PATH, headless,
`--enable-precise-memory-info`/`--js-flags=--expose-gc`, CDP
`HeapProfiler.collectGarbage` before each run, in-page 1 ms sampler of
`performance.memory.usedJSHeapSize` for the peak and `Performance.getMetrics`
cross-check (post-GC `JSHeapUsedSize` delta ≈ 0.25 MiB — no retained archive).
Bun: `Bun.gc(true)`, 2 ms sampler, `heapUsed + arrayBuffers`; the Bun RSS/heap
proxy is noisier and is reported only as a cross-check. Phase split in
Chromium (media-heavy): central-directory load **8.9 ms**, inflate + Blob
materialisation **20.2 ms** — the cost is proportional to the bytes that must
be decompressed, not a JSZip API hotspot. Explicitly deleting `zip.files`
entries after extraction changed the Chromium peak from 25.75 to 25.74 MiB
(media) and 10.6 → 9.6 ms wall (text, within spread), so no release code was
added on that basis. These are **SYNTHETIC** fixtures on one local host, not
production measurements; target-hardware browser profiling remains open.

### Alternatives evaluated (paper decisions with local probes)

- **Keep JSZip.** It is already lazy-loaded (`lazy-zip.ts`), single-flight, and
  its reader stores the compressed ZIP in one `ArrayBuffer` with subarray
  views — not one copy per entry (JSZip `lib/zipEntries.js`, `lib/reader/Uint8ArrayReader.js`).
- **`fflate@0.8.3`** (npm tarball probed outside the repo): `unzipSync` was
  faster in Bun (11.8 ms [10.0..15.1] media, peak 34.31 MiB) but synchronous on
  the main thread; an async/worker path plus Blob/error adaptation would be a
  new runtime dependency and a rewrite of every ZIP call site for a ~20 ms
  synthetic win. Rejected.
- **`@zip.js/zip.js@2.25.0`** (tarball probed outside the repo): its streaming
  `BlobReader` was **slower** in Bun (23.0 ms [21.1..78.5] media, peak 77.11 MiB)
  than JSZip (22.3 ms, 67.36 MiB). Streaming is not automatically better for a
  flat archive whose central directory is at the end. Rejected.
- **No client unpack / backend streams separate resources:** rejected. The
  backend writes one flat ZIP atomically (`archives.py:40–62`; job results are
  a single attempt-scoped artifact published only after the fenced `succeeded`
  transition, `back/src/jobs/AGENTS.md` §"Artifacts & cache ownership"), and
  every `/api/*` response shares `Cache-Control: no-store` (`docs/API.md`).
  Separate resources would need new DB keys, multiple fetches with partial
  states, filename/manifest ordering and a new cache policy — a protocol
  change that would compromise atomicity/integrity for a few client lines.
- **Object URL / direct download:** already the case; no extra copy and no
  leaked URL. No change.

Regression coverage: `jobs.test.ts` (cancellation after the result arrived,
abort during the archive read), `mask-requests.test.ts` (stale accepted job
publishes no bundle), `use-job-lifecycle.test.ts` (unmount aborts local
polling without a server `DELETE`) and the existing unpack/failure tests.

## Tests

`back/tests/test_ooxml.py` builds synthetic OPC fixtures by hand: default
namespace and custom-prefix variants, PII beyond byte 65,536, footnotes,
endnotes, comments, tracked deletions, text boxes, content controls, core/app
properties, alt-text attributes, external relationship targets, shared parts,
malformed XML, embedded objects, and mask → keys → unmask round-trips with
byte-for-byte preservation of an unrelated PNG entry. Sync/job manifest parity
is asserted with the same source and config. `back/tests/test_ooxml_bounded.py`
adds DM-3 regressions: metadata-only inventory, a refcount-tracked bytes probe
proving only one decompressed part is resident, high-occurrence stats parity
without an occurrence list, file/memory equivalence, cancellation at part and
window boundaries, one unmask scan per container and worker-manifest parity.
`front/e2e/smoke.spec.ts` includes a manifest-required/validated-statistics case.
`back/tests/test_transform_cleanup.py` adds failure-path coverage: sync
`marker_collision` envelope and no cache write for text/DOCX, no partial target
on collision/cancellation/parse/serialization error, preservation of a
pre-existing artifact, and job `execute()` cleanup on collision, cancellation
and worker failure.
