"""
DOCX masking that preserves formatting.

python-docx is read/write over OOXML; formatting lives in the XML runs, so we
mask at the *run* level (paragraph.runs) and never re-layout a paragraph. For
each paragraph we run the deterministic masker on its joined text, then rewrite
only the overlapping slice of each run with ``[LABEL]``. An entity that spans
several runs (e.g. a name split across bold/italic styles) is handled by
replacing the covered part of each run, keeping the surrounding formatting
intact.

Coverage: body paragraphs, all table cells (recursively), and the default /
first / even header & footer paragraphs of every section.
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterable

from docx import Document
from docx.document import Document as DocumentObject
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.hyperlink import Hyperlink
from docx.text.paragraph import Paragraph
from docx.text.run import Run

from .constants import mask_marker
from .rule_based import RuleBasedMasker, get_masker
from .schemas import MaskedEntity

#: A hyperlink whose visible text is nothing but mask markers (``[EMAIL]``, …).
_MASK_ONLY_RE = re.compile(r"(?:\[[A-Z ]+\]\s*)+")


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
        # python-docx exposes the default header/footer (first-page/even variants
        # live in the OOXML and are not surfaced as separate properties here).
        for header_footer in (section.header, section.footer):
            for paragraph in header_footer.paragraphs:
                if id(paragraph._p) not in seen:
                    seen.add(id(paragraph._p))
                    keep_alive.append(paragraph._p)
                    yield paragraph


def mask_paragraph(paragraph: Paragraph, entity_map: dict[tuple[int, int], MaskedEntity]) -> int:
    """
    Mask the runs of one paragraph in place; returns the entity count masked.

    Entities are keyed by absolute offsets in the paragraph text. An entity may
    span several runs (e.g. ``АО «Росхим»`` split as ``АО «`` | ``Росхим`` |
    ``»``); it must still collapse to a **single** ``[LABEL]``. So we process
    entities back-to-front and, for each one, write the marker into the run
    that contains its start and blank out the covered slices of the following
    runs. Working back-to-front keeps the absolute offsets of earlier entities
    valid, since edits only ever shorten text to the right of the cursor.

    Hyperlinks covered by an entity are unwrapped (their runs are lifted out of
    the ``w:hyperlink`` element) so a masked email/URL is no longer clickable.
    """
    runs = _paragraph_runs(paragraph)
    bounds: list[tuple[int, int]] = []
    cursor = 0
    for run in runs:
        length = len(run.text)
        bounds.append((cursor, cursor + length))
        cursor += length

    covered = 0
    for (entity_start, entity_end), entity in sorted(entity_map.items(), reverse=True):
        marker = mask_marker(entity.label)
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
) -> tuple[bytes, list[MaskedEntity]]:
    """Mask PII in a .docx (bytes) and return the new .docx bytes + entities."""
    active = masker or get_masker()
    document = Document(io.BytesIO(source_bytes))

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
        # Build an offset map and mask the representative spans.
        entity_map = _paragraph_entity_map(text, entities)
        mask_paragraph(paragraph, entity_map)
        all_entities.extend(entities)

    out = io.BytesIO()
    document.save(out)
    return out.getvalue(), _dedupe_entities(all_entities)


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


def _dedupe_entities(entities: list[MaskedEntity]) -> list[MaskedEntity]:
    seen: dict[tuple[int, int, str], MaskedEntity] = {}
    for entity in entities:
        key = (entity.start, entity.end, entity.label)
        prev = seen.get(key)
        if prev is None or entity.score > prev.score:
            seen[key] = entity
    return list(seen.values())
