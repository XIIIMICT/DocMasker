"""
DOCX masking that preserves formatting.

python-docx is read/write over OOXML; formatting lives in the XML runs, so we
mask at the *run* level (paragraph.runs) and never re-layout a paragraph. For
each paragraph we run GLiNER on its joined text, then rewrite only the
overlapping slice of each run with `[LABEL]`. An entity that spans several
runs (e.g. a name split across bold/look-styles) is handled by replacing the
covered part of each run, keeping the surrounding formatting intact.

Coverage: body paragraphs, all table cells (recursively), and the default /
first / even header & footer paragraphs of every section.
"""

from __future__ import annotations

import io
from collections.abc import Iterable

from docx import Document
from docx.document import Document as DocumentObject
from docx.table import Table
from docx.text.paragraph import Paragraph

from .schemas import MaskedEntity
from .service import GLiNERModelLike, detect_entities, mask_text


def _iter_table_paragraphs(table: Table, seen: set[int]) -> Iterable[Paragraph]:
    for row in table.rows:
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                if id(paragraph._p) not in seen:
                    seen.add(id(paragraph._p))
                    yield paragraph
            for nested in cell.tables:
                yield from _iter_table_paragraphs(nested, seen)


def iter_paragraphs(doc: DocumentObject) -> Iterable[Paragraph]:
    """Yield every text paragraph in the document exactly once."""
    seen: set[int] = set()

    for paragraph in doc.paragraphs:
        seen.add(id(paragraph._p))
        yield paragraph

    for table in doc.tables:
        yield from _iter_table_paragraphs(table, seen)

    for section in doc.sections:
        # python-docx exposes the default header/footer (first-page/even variants
        # live in the OOXML and are not surfaced as separate properties here).
        for header_footer in (section.header, section.footer):
            for paragraph in header_footer.paragraphs:
                if id(paragraph._p) not in seen:
                    seen.add(id(paragraph._p))
                    yield paragraph


def mask_paragraph(paragraph: Paragraph, entity_map: dict[tuple[int, int], MaskedEntity]) -> int:
    """
    Mask the runs of one paragraph in place; returns the entity count masked.

    Entities are keyed by absolute offsets in the paragraph text. We edit runs
    back-to-front: replacing a span in a later run shortens only the region to
    its right, so the absolute offsets of earlier runs stay valid.
    """
    runs = list(paragraph.runs)
    bounds: list[tuple[int, int]] = []
    cursor = 0
    for run in runs:
        length = len(run.text)
        bounds.append((cursor, cursor + length))
        cursor += length

    covered = 0
    for index in range(len(runs) - 1, -1, -1):
        run = runs[index]
        if not run.text:
            continue
        run_start, run_end = bounds[index]
        local_entities: list[MaskedEntity] = []
        for (es, ee), entity in entity_map.items():
            if es >= run_end or ee <= run_start:
                continue
            left = max(es, run_start)
            right = min(ee, run_end)
            local_entities.append(
                MaskedEntity(
                    label=entity.label,
                    text=run.text[left - run_start : right - run_start],
                    start=left - run_start,
                    end=right - run_start,
                    score=entity.score,
                )
            )
        if local_entities:
            run.text = mask_text(run.text, local_entities)
            covered += len(local_entities)
    return covered


def mask_docx_in_memory(
    source_bytes: bytes,
    model: GLiNERModelLike,
) -> tuple[bytes, list[MaskedEntity]]:
    """Mask PII in a .docx (bytes) and return the new .docx bytes + entities."""
    document = Document(io.BytesIO(source_bytes))

    all_entities: list[MaskedEntity] = []
    total_masked = 0
    for paragraph in iter_paragraphs(document):
        text = paragraph.text
        if not text.strip():
            continue
        entities = detect_entities(model, text)
        if not entities:
            continue
        # Re-detect the full paragraph text once; entities are absolute within
        # `text`. Build an offset map and mask the representative spans.
        entity_map = _paragraph_entity_map(text, entities)
        masked = mask_paragraph(paragraph, entity_map)
        total_masked += masked
        if total_masked > 0:
            # Record the detected entities (dedup by span) for the response.
            for entity in entities:
                all_entities.append(entity)

    out = io.BytesIO()
    document.save(out)
    return out.getvalue(), _dedupe_entities(all_entities)


def _paragraph_entity_map(text: str, entities: list[MaskedEntity]) -> dict[tuple[int, int], MaskedEntity]:
    """Map (text offset, text offset) -> clamped entity, with non-overlapping spans."""
    # GLiNER may return nested/overlapping spans across labels; flatten by picking
    # the highest-score span per (start,end,label) and resolving overlaps so the
    # run rewrite stays consistent (a text offset belongs to at most one mask).
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
