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
