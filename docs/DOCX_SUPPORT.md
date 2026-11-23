# DOCX coverage and limitations

DocMasker masks DOCX **at the run level** (`python-docx`), preserving
formatting. Only the OOXML parts listed below are traversed. The output is
**not** a complete anonymization of arbitrary OOXML — a document may carry PII
in parts we do not touch. See `docs/ARCHITECTURE.md` and `AGENTS.md` landmine 7/10/11.

## Supported (masked and restored)

| Part | How | Notes |
|---|---|---|
| Body paragraphs | `document.paragraphs` | All text, including `w:t` runs |
| Body tables, nested | `_iter_table_paragraphs` | Recursive, all cells |
| Default header / footer | `section.header` / `.footer` | Per section |
| First-page header / footer | `section.first_page_header` / `_footer` | Only when `different_first_page_header_footer` is set; `is_linked_to_previous` variants are skipped |
| Even-page header / footer | `section.even_page_header` / `_footer` | `odd_and_even_pages_header_footer` setting |
| Tables inside any header/footer | `header_footer.tables` → recursive | Covered since stage 5 |
| Hyperlinked runs (`w:hyperlink`) | `iter_inner_content()` | Masked inline; a link whose text becomes only markers is unwrapped and its relationship dropped |
| Entities spanning several runs | `mask_paragraph` | Collapsed into one `[LABEL_N]`; run bounds recomputed per entity |

python-docx 1.2 resolves the `w:headerReference` / `w:footerReference`
parts for all variants — no raw XML traversal is needed for these.

## Not supported (PII here is NOT masked)

| Part | Risk |
|---|---|
| Text boxes / shapes (`w:txbxContent`, DrawingML) | High — visible text |
| Footnotes / endnotes | High — visible text |
| Comments | High — author names, text |
| Tracked changes (`w:ins`, `w:del`) | High — deleted text still in the file |
| Document properties (`docProps/core.xml`, `app.xml`) | High — author, company, manager |
| Alt text / image descriptions (`wp:docPr`) | Medium |
| Hyperlink relationship *targets* (`mailto:`, URLs) in attributes | Medium — the visible link text is masked, but a target may still carry a name when the visible text is not fully covered |
| Embedded objects / OLE / charts | Medium |
| Hidden text / `w:vanish` runs | Medium |
| Custom XML, content controls (`w:sdt`), field codes | Low–medium |
| First-page/even variants linked to a previous section | Skipped deliberately: their content belongs to that section and is masked there |

Because of these gaps, the mask-file response carries an explicit
`X-Docx-Unsupported-Parts` warning header / result manifest entry when a
package contains any of the unsupported parts (stage 5). The UI shows it in
the result pane. Treat the download as "masked within the supported scope",
not as a fully anonymized file.

## Test matrix

`back/tests/test_docx.py` covers: run-level formatting, split-across-runs
entities, hyperlink unwrapping, body/table/header phone+person collisions,
all six header/footer variants + a header table, keys round-trip, no
highlight written to the file. Synthetic fixtures only — no real PII.
The e2e suite (`front/e2e/smoke.spec.ts`) covers a text round-trip and a
DOCX file round-trip through the front proxy.

## Adding coverage

To support a new part: extend `iter_paragraphs()` (or add a dedicated
traversal over the corresponding OOXML part), keep the `keep_alive` list,
write a synthetic fixture that actually contains that part, and add both a
mask and a restore test. Update this matrix in the same change.
