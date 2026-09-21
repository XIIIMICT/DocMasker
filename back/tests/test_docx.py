"""Tests for DOCX masking (formatting preservation) with the rule-based masker."""

from __future__ import annotations

import io

from docx import Document as DocxDocument
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml.ns import qn
from docx.shared import Pt
from docx.text.paragraph import Paragraph

from src.masking.docx_service import mask_docx_in_memory
from src.masking.mapping import parse_keys_yaml
from src.masking.rule_based import RuleBasedMasker


def _add_hyperlink(paragraph: Paragraph, text: str, url: str) -> None:
    """Append an external hyperlink run, like Word does for a mailto: link."""
    relationship_id = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
    hyperlink = paragraph._p.makeelement(qn("w:hyperlink"), {qn("r:id"): relationship_id})
    run = paragraph._p.makeelement(qn("w:r"), {})
    text_element = paragraph._p.makeelement(qn("w:t"), {})
    text_element.text = text
    run.append(text_element)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)


def _build_docx() -> bytes:
    doc = DocxDocument()
    # Body paragraph: name (plain) + phone (bold run), plus trailing text.
    p = doc.add_paragraph()
    p.add_run("Доклад: Иван Петров, тел ")
    run_bold = p.add_run("+7 900 123-45-67")
    run_bold.bold = True
    p.add_run(", конец.")

    # Second paragraph containing the name split across two runs.
    p2 = doc.add_paragraph()
    p2.add_run("Письмо от ")
    p2.add_run("Иван")
    p2.add_run(" ")
    p2.add_run("Петрова")
    p2.runs[1].italic = True

    # Header paragraph with the name in a custom-size run.
    header = doc.sections[0].header
    hp = header.paragraphs[0]
    hrun = hp.add_run("Заголовок: Иван Петров")
    hrun.font.size = Pt(16)

    # Table cell with the phone.
    table = doc.add_table(rows=1, cols=1)
    cell = table.cell(0, 0)
    cell.paragraphs[0].add_run("Позвонить: +7 900 123-45-67")

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def test_docx_masking_preserves_formatting_and_runs() -> None:
    source = _build_docx()

    masked_bytes, entities, keys = mask_docx_in_memory(source)

    assert len(entities) >= 4  # person (body) + phone (bold) + header person + table phone
    document = DocxDocument(io.BytesIO(masked_bytes))

    # Body paragraph: name and phone replaced, tail intact, bold survived.
    body = next(p for p in document.paragraphs if "Доклад:" in p.text)
    assert "Иван Петров" not in body.text
    assert "+7 900 123-45-67" not in body.text
    assert "[PERSON_1]" in body.text
    assert "[PHONE_1]" in body.text
    assert "Доклад:" in body.text
    assert "конец." in body.text
    # The bold run that held the phone now holds the [PHONE_1] mask and is still bold.
    bold_runs = [r for r in body.runs if r.bold]
    assert any(r.text == "[PHONE_1]" for r in bold_runs)

    # Split name across two runs collapsed into a single [PERSON_2] in the second paragraph.
    author = next(p for p in document.paragraphs if p.text.startswith("Письмо от"))
    assert "Иван" not in author.text
    assert "[PERSON_2]" in author.text
    # The italicised fragment survives the collapse.
    assert any(r.italic for r in author.runs)

    # Header keeps its 16pt run while the name is masked. The name equals the
    # body one, so it reuses that placeholder.
    header = next(p for p in document.sections[0].header.paragraphs if "Заголовок:" in p.text)
    assert "Иван Петров" not in header.text
    assert "[PERSON_1]" in header.text
    assert any(r.font.size == Pt(16) for r in header.runs)

    # Table cell phone masked; repeated value reuses the placeholder.
    table = document.tables[0]
    cell_text = table.cell(0, 0).text
    assert "+7 900 123-45-67" not in cell_text
    assert "[PHONE_1]" in cell_text

    # Keys carry each distinct value once, per-label, in document order.
    assert parse_keys_yaml(keys.to_yaml()) == {
        "Физлица": ["Иван Петров", "Иван Петрова"],
        "Номера телефонов": ["+7 900 123-45-67"],
    }


def test_numbering_is_continuous_across_the_document() -> None:
    # Two different organisations in two paragraphs must get [ORGANIZATION_1]
    # and [ORGANIZATION_2] — the counter is document-wide, not per paragraph.
    doc = DocxDocument()
    doc.add_paragraph('Место работы — АО "Росхим".')
    doc.add_paragraph('Партнёр — ООО "Ромашка".')
    out = io.BytesIO()
    doc.save(out)

    masked_bytes, _entities, keys = mask_docx_in_memory(out.getvalue())
    paragraphs = [p.text for p in DocxDocument(io.BytesIO(masked_bytes)).paragraphs]
    assert "[ORGANIZATION_1]" in paragraphs[0]
    assert "[ORGANIZATION_2]" in paragraphs[1]
    assert parse_keys_yaml(keys.to_yaml()) == {
        "Организации": ['АО "Росхим"', 'ООО "Ромашка"'],
    }


def test_hyperlinked_email_collapses_to_plain_mask() -> None:
    # Word represents a mailto: link as a w:hyperlink whose run holds the email.
    # The masked result must be a plain [EMAIL_1] run with no link left behind.
    doc = DocxDocument()
    p = doc.add_paragraph()
    p.add_run("Пишите на ")
    _add_hyperlink(p, "dksir@ruschem.ru", "mailto:dksir@ruschem.ru")
    out = io.BytesIO()
    doc.save(out)

    masked_bytes, entities, _keys = mask_docx_in_memory(out.getvalue())

    document = DocxDocument(io.BytesIO(masked_bytes))
    paragraph = document.paragraphs[0]
    assert "[EMAIL_1]" in paragraph.text
    assert "dksir@ruschem.ru" not in paragraph.text
    # The hyperlink was unwrapped: no w:hyperlink element and no relationship.
    assert paragraph._p.findall(qn("w:hyperlink")) == []
    hyperlink_rels = [rel for rel in paragraph.part.rels.values() if rel.reltype == RT.HYPERLINK]
    assert hyperlink_rels == []
    assert [entity.label for entity in entities] == ["email"]


def test_repeated_masking_keeps_all_paragraphs() -> None:
    # Regression: python-docx hands out fresh proxy objects, so deduplicating by
    # `id(paragraph._p)` without holding a reference dropped header paragraphs
    # once an earlier proxy was garbage-collected and its address reused.
    source = _build_docx()
    counts = {len(mask_docx_in_memory(source)[1]) for _ in range(6)}
    assert counts == {5}


def test_docx_empty_file_is_fine() -> None:
    doc = DocxDocument()
    out = io.BytesIO()
    doc.save(out)
    masked_bytes, entities, keys = mask_docx_in_memory(out.getvalue())
    document = DocxDocument(io.BytesIO(masked_bytes))
    assert document.paragraphs == []
    assert entities == []
    assert keys.to_yaml() == ""


def test_docx_accepts_explicit_masker() -> None:
    only_email = RuleBasedMasker(["email"])
    doc = DocxDocument()
    doc.add_paragraph("Иван Петров, ivan@mail.ru")
    out = io.BytesIO()
    doc.save(out)

    masked_bytes, entities, _keys = mask_docx_in_memory(out.getvalue(), only_email)
    text = "\n".join(p.text for p in DocxDocument(io.BytesIO(masked_bytes)).paragraphs)
    assert "[EMAIL_1]" in text
    assert "Иван Петров" in text
    assert [entity.label for entity in entities] == ["email"]


def test_entity_spanning_three_runs_collapses_to_one_marker() -> None:
    # «АО «Росхим»» split as «АО «» | «Росхим» | «», оно...» — must yield a
    # single [ORGANIZATION_1], not one marker per covered run.
    doc = DocxDocument()
    p = doc.add_paragraph()
    p.add_run("Место работы это АО «")
    p.add_run("Росхим")
    p.add_run("», оно находится здесь.")
    out = io.BytesIO()
    doc.save(out)

    masked_bytes, _entities, _keys = mask_docx_in_memory(out.getvalue())
    paragraph = DocxDocument(io.BytesIO(masked_bytes)).paragraphs[0]
    assert paragraph.text.count("[ORGANIZATION_1]") == 1
    assert "Росхим" not in paragraph.text
    assert "[ORGANIZATION_1]" in paragraph.text
    assert "оно находится здесь." in paragraph.text
    assert len(paragraph.runs) == 3  # run formatting preserved


def test_masked_document_has_no_highlight() -> None:
    # The yellow tint is preview-only (front `DocxViewer`); the downloaded file
    # must stay plain.
    doc = DocxDocument()
    doc.add_paragraph("Контакты: Иван Петров, тел +7 900 123-45-67.")
    out = io.BytesIO()
    doc.save(out)

    masked_bytes, _entities, _keys = mask_docx_in_memory(out.getvalue())
    document = DocxDocument(io.BytesIO(masked_bytes))
    for paragraph in document.paragraphs:
        assert paragraph.text == "Контакты: [PERSON_1], тел [PHONE_1]."
        assert all(run.font.highlight_color is None for run in paragraph.runs)


def test_entity_split_across_many_runs_yields_one_marker() -> None:
    # Regression: a stale `bounds` list (computed once) emitted one marker per
    # covered run ("[PERSON_1][PERSON_1][ORGANIZATION_1]"). The bounds must be
    # recomputed per entity so each span yields exactly one marker.
    doc = DocxDocument()
    paragraph = doc.add_paragraph()
    for chunk in [
        "Привет! Меня зовут ",
        "Иван ",
        "Петров",
        ", я работаю в ",
        "АО «",
        "Росхим",
        "» и живу в ",
        "Донецке",
        "…",
    ]:
        paragraph.add_run(chunk)
    out = io.BytesIO()
    doc.save(out)

    masked_bytes, _entities, _keys = mask_docx_in_memory(out.getvalue())
    text = DocxDocument(io.BytesIO(masked_bytes)).paragraphs[0].text
    assert text == "Привет! Меня зовут [PERSON_1], я работаю в [ORGANIZATION_1] и живу в Донецке…"
    assert text.count("[PERSON_1]") == 1
    assert text.count("[ORGANIZATION_1]") == 1
