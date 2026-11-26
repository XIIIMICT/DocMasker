# DOCX / OOXML coverage (Phase 5)

DocMasker masks `.docx` at the **OPC package level**. `back/src/masking/ooxml/`
builds a namespace-aware inventory from `[Content_Types].xml` and relationship
files (QName/URI, never hardcoded prefixes), parses selected XML parts with
`defusedxml.lxml` (no DTD, no external entities, no network) and rewrites
text-node slices. Only modified ZIP entries are re-serialized; **all other
entries are copied byte-for-byte**. `python-docx` is no longer used for masking.

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

## Tests

`back/tests/test_ooxml.py` builds synthetic OPC fixtures by hand: default
namespace and custom-prefix variants, PII beyond byte 65,536, footnotes,
endnotes, comments, tracked deletions, text boxes, content controls, core/app
properties, alt-text attributes, external relationship targets, shared parts,
malformed XML, embedded objects, and mask → keys → unmask round-trips with
byte-for-byte preservation of an unrelated PNG entry. Sync/job manifest parity
is asserted with the same source and config. `front/e2e/smoke.spec.ts` includes
a manifest-required/validated-statistics case.
