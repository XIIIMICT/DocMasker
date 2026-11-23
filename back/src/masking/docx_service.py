"""
DOCX masking that preserves formatting.

python-docx is read/write over OOXML; formatting lives in the XML runs, so we
mask at the *run* level (paragraph.runs) and never re-layout a paragraph. For
each paragraph we run the deterministic masker on its joined text, then rewrite
only the overlapping slice of each run with ``[LABEL]``. An entity that spans
several runs (e.g. a name split across bold/italic styles) is handled by
replacing the covered part of each run, keeping the surrounding formatting
intact.

Coverage: body paragraphs, all table cells (recursively), and every
header/footer variant of each section — default, first-page and even-page —
including tables inside them. python-docx 1.2 exposes all variants as
properties (`first_page_header` / `even_page_header` / …); it resolves the
OOXML `w:headerReference` / `w:footerReference` parts for us. Variants linked
to the previous section are skipped (their content belongs to that section).
"""

import io
import re
from collections.abc import Iterable, Mapping

from docx import Document
from docx.document import Document as DocumentObject
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.hyperlink import Hyperlink
from docx.text.paragraph import Paragraph
from docx.text.run import Run

from .mapping import MaskKeys
from .rule_based import RuleBasedMasker, get_masker
from .schemas import MaskedEntity

#: A hyperlink whose visible text is nothing but mask markers (``[EMAIL_1]``, …).
_MASK_ONLY_RE = re.compile(r"(?:\[[A-Z]+_\d+\]\s*)+")


def _paragraph_runs(paragraph: Paragraph) -> list[Run]:
    """
    All runs whose text is part of `paragraph.text`, in document order.

    Hyperlinked runs are included: `paragraph.text` renders them inline, so a
    mask offset can land inside a hyperlink. `paragraph.runs` alone skips them,
    which is why we walk `iter_inner_content()`.
    """
    runs: list[Run] = []
    for item in paragraph.iter_inner_content():
        if isinstance(item, Run):
            runs.append(item)
        elif isinstance(item, Hyperlink):
            runs.extend(item.runs)
    return runs


def _unwrap_hyperlink(hyperlink: Hyperlink) -> None:
    """
    Replace a ``w:hyperlink`` element with its child runs, dropping the link.

    The relationship is deleted too, so the saved document carries no unused
    ``mailto:``/``http:`` relationship pointing at masked text.
    """
    element = hyperlink._hyperlink
    relationship_id = element.get(qn("r:id"))
    parent = element.getparent()
    index = parent.index(element)
    for child in list(element):
        parent.insert(index, child)
        index += 1
    parent.remove(element)
    if relationship_id:
        hyperlink.part.drop_rel(relationship_id)


def _iter_table_paragraphs(table: Table, seen: set[int], keep_alive: list[object]) -> Iterable[Paragraph]:
    for row in table.rows:
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                if id(paragraph._p) not in seen:
                    seen.add(id(paragraph._p))
                    keep_alive.append(paragraph._p)
                    yield paragraph
            for nested in cell.tables:
                yield from _iter_table_paragraphs(nested, seen, keep_alive)


def iter_paragraphs(doc: DocumentObject) -> Iterable[Paragraph]:
    """
    Yield every text paragraph in the document exactly once.

    Paragraphs are deduplicated by the identity of their underlying XML element
    (``paragraph._p``). python-docx hands out fresh proxy objects, so we keep a
    strong reference to every element we have seen: otherwise a proxy can be
    garbage-collected mid-iteration and its address reused by a later element,
    which would make ``id()`` collide and silently drop paragraphs.
    """
    seen: set[int] = set()
    keep_alive: list[object] = []

    for paragraph in doc.paragraphs:
        seen.add(id(paragraph._p))
        keep_alive.append(paragraph._p)
        yield paragraph

    for table in doc.tables:
        yield from _iter_table_paragraphs(table, seen, keep_alive)

    for section in doc.sections:
        # All variants python-docx resolves from the OOXML references. A
        # variant linked to the previous section has no content of its own —
        # masking it would double-count the previous section's paragraphs.
        header_footers = (
            section.header,
            section.footer,
            section.first_page_header,
            section.first_page_footer,
            section.even_page_header,
            section.even_page_footer,
        )
        for header_footer in header_footers:
            if header_footer.is_linked_to_previous:
                continue
            for paragraph in header_footer.paragraphs:
                if id(paragraph._p) not in seen:
                    seen.add(id(paragraph._p))
                    keep_alive.append(paragraph._p)
                    yield paragraph
            for table in header_footer.tables:
                yield from _iter_table_paragraphs(table, seen, keep_alive)


def mask_paragraph(paragraph: Paragraph, entity_map: dict[tuple[int, int], str]) -> int:
    """
    Mask the runs of one paragraph in place; returns the entity count masked.

    `entity_map` maps an entity's absolute offsets in the paragraph text to its
    placeholder (``[LABEL_N]``). An entity may span several runs (e.g.
    ``АО «Росхим»`` split as ``АО «`` | ``Росхим`` | ``»``); it must still
    collapse to a **single** marker. So we process entities back-to-front and,
    for each one, write the marker into the run that contains its start and
    blank out the covered slices of the following runs. Working back-to-front
    keeps the absolute offsets of earlier entities valid, since edits only ever
    shorten text to the right of the cursor.

    No highlight is applied here: the yellow tint is a **preview-only** effect
    (`DocxViewer`), so the downloaded file stays plain.

    Hyperlinks covered by an entity are unwrapped (their runs are lifted out of
    the ``w:hyperlink`` element) so a masked email/URL is no longer clickable.
    """
    runs = _paragraph_runs(paragraph)

    covered = 0
    # Work back-to-front so earlier offsets stay valid, and recompute the run
    # bounds each iteration: a later (further-right) entity shortens runs, so a
    # cached `bounds` list would be stale and could emit a marker per run.
    for (entity_start, entity_end), marker in sorted(entity_map.items(), reverse=True):
        bounds: list[tuple[int, int]] = []
        cursor = 0
        for run in runs:
            length = len(run.text)
            bounds.append((cursor, cursor + length))
            cursor += length

        for index, (run_start, run_end) in enumerate(bounds):
            if entity_start >= run_end or entity_end <= run_start:
                continue
            left = max(entity_start, run_start) - run_start
            right = min(entity_end, run_end) - run_start
            run = runs[index]
            if entity_start < run_end and entity_start >= run_start:
                # First covered run: keep the text before the span, emit the
                # marker once, keep the text after the span.
                run.text = run.text[:left] + marker + run.text[right:]
            else:
                # Continuation run: drop only the covered slice.
                run.text = run.text[:left] + run.text[right:]
        covered += 1
    _unwrap_covered_hyperlinks(paragraph)
    return covered


def _unwrap_covered_hyperlinks(paragraph: Paragraph) -> None:
    """
    Un-link hyperlinks whose visible text is now only mask markers.

    An email that was a ``mailto:`` hyperlink becomes a plain ``[EMAIL]`` run,
    so the masked document carries no dangling link. A hyperlink that still
    shows unmasked text (a partially masked address) is left alone.
    """
    for item in list(paragraph.iter_inner_content()):
        if isinstance(item, Hyperlink) and _MASK_ONLY_RE.fullmatch(item.text.strip()):
            _unwrap_hyperlink(item)


def mask_docx_in_memory(
    source_bytes: bytes,
    masker: RuleBasedMasker | None = None,
) -> tuple[bytes, list[MaskedEntity], MaskKeys]:
    """
    Mask PII in a .docx (bytes) and return the new bytes, entities and keys.

    Two passes: the first detects entities in every paragraph and assigns
    placeholders from one document-wide `MaskKeys`, so the per-label numbering
    is continuous across the whole document; the second rewrites the runs with
    the already-assigned markers.
    """
    active = masker or get_masker()
    document = Document(io.BytesIO(source_bytes))
    keys = MaskKeys(active.profile)

    paragraphs: list[tuple[Paragraph, str, list[MaskedEntity], dict[tuple[int, int], str]]] = []
    all_entities: list[MaskedEntity] = []
    for paragraph in iter_paragraphs(document):
        text = paragraph.text
        if not text.strip():
            continue
        entities = [
            MaskedEntity(label=entity.label, text=entity.text, start=entity.start, end=entity.end, score=entity.score)
            for entity in active.detect(text)
        ]
        if not entities:
            continue
        entity_map = _paragraph_entity_map(text, entities)
        placeholders = {
            (entity.start, entity.end): keys.placeholder(entity.label, entity.text) for entity in entity_map.values()
        }
        paragraphs.append((paragraph, text, entities, placeholders))
        all_entities.extend(entities)

    for paragraph, _text, _entities, placeholders in paragraphs:
        mask_paragraph(paragraph, placeholders)

    out = io.BytesIO()
    document.save(out)
    # No cross-paragraph dedupe: `iter_paragraphs` yields every paragraph exactly
    # once and `detect()` already returns unique non-overlapping spans, so equal
    # (start, end, label) pairs from different paragraphs are distinct occurrences
    # that must all be reported (the occurrence counts in the headers/UI rely on it).
    return out.getvalue(), all_entities, keys


def unmask_docx_in_memory(source_bytes: bytes, placeholders: Mapping[str, str]) -> tuple[bytes, list[str]]:
    """
    Restore a masked .docx (bytes) by replacing every placeholder with its value.

    Masking writes each ``[LABEL_N]`` marker into a single run, so a run-level
    substitution is enough for the common case. A marker split across runs is
    handled too: the whole paragraph text is scanned, and when a placeholder
    spans several runs the replacement is written into the run that holds its
    start and the covered slices of the following runs are blanked — the same
    collapse rule `mask_paragraph` uses, so the restored value keeps the
    formatting of the run where the marker began.

    Returns the restored bytes plus the values whose placeholder never appeared
    in the document (the keys the caller should report as "не применено").
    """
    document = Document(io.BytesIO(source_bytes))
    full_text = "\n".join(paragraph.text for paragraph in iter_paragraphs(document))
    for paragraph in iter_paragraphs(document):
        text = paragraph.text
        if not text:
            continue
        spans = _placeholder_spans(text, placeholders)
        if spans:
            _replace_spans(paragraph, spans)
    out = io.BytesIO()
    document.save(out)
    unused = [value for placeholder, value in placeholders.items() if placeholder not in full_text]
    return out.getvalue(), unused


def _placeholder_spans(text: str, placeholders: Mapping[str, str]) -> list[tuple[int, int, str]]:
    """Find every known placeholder in `text` as (start, end, replacement)."""
    spans: list[tuple[int, int, str]] = []
    for placeholder, value in placeholders.items():
        start = text.find(placeholder)
        while start != -1:
            spans.append((start, start + len(placeholder), value))
            start = text.find(placeholder, start + len(placeholder))
    return sorted(spans, key=lambda span: span[0])


def _replace_spans(paragraph: Paragraph, spans: list[tuple[int, int, str]]) -> None:
    """Rewrite runs so each (start, end) span becomes its replacement text."""
    runs = _paragraph_runs(paragraph)
    bounds: list[tuple[int, int]] = []
    cursor = 0
    for run in runs:
        length = len(run.text)
        bounds.append((cursor, cursor + length))
        cursor += length

    for span_start, span_end, replacement in sorted(spans, key=lambda span: span[0], reverse=True):
        for index, (run_start, run_end) in enumerate(bounds):
            if span_start >= run_end or span_end <= run_start:
                continue
            left = max(span_start, run_start) - run_start
            right = min(span_end, run_end) - run_start
            run = runs[index]
            if span_start < run_end and span_start >= run_start:
                run.text = run.text[:left] + replacement + run.text[right:]
            else:
                run.text = run.text[:left] + run.text[right:]


def _paragraph_entity_map(text: str, entities: list[MaskedEntity]) -> dict[tuple[int, int], MaskedEntity]:
    """Map (text offset, text offset) -> clamped entity, with non-overlapping spans."""
    picked: dict[tuple[int, int], MaskedEntity] = {}
    for entity in entities:
        es, ee = entity.start, entity.end
        if es < 0 or es >= ee or ee > len(text):
            continue
        key = (es, ee)
        prev = picked.get(key)
        if prev is None or entity.score > prev.score:
            picked[key] = entity
    # Sort by start; drop spans fully contained in an earlier span (keep the outer).
    ordered = sorted(picked.values(), key=lambda entity: (entity.start, -(entity.end - entity.start)))
    result: dict[tuple[int, int], MaskedEntity] = {}
    current_end = -1
    for entity in ordered:
        if entity.start < current_end and entity.end <= current_end:
            continue  # contained in an already-chosen span
        result[(entity.start, entity.end)] = entity
        current_end = max(current_end, entity.end)
    return result
