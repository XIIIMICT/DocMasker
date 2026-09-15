"""Tests for DOCX masking (formatting preservation) with the rule-based masker."""

from __future__ import annotations

import io

from docx import Document as DocxDocument
from docx.shared import Pt

from src.masking.docx_service import mask_docx_in_memory
from src.masking.rule_based import RuleBasedMasker


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

    masked_bytes, entities = mask_docx_in_memory(source)

    assert len(entities) >= 4  # person (body) + phone (bold) + header person + table phone
    document = DocxDocument(io.BytesIO(masked_bytes))

    # Body paragraph: name and phone replaced, tail intact, bold survived.
    body = next(p for p in document.paragraphs if "Доклад:" in p.text)
    assert "Иван Петров" not in body.text
    assert "+7 900 123-45-67" not in body.text
    assert "[PERSON]" in body.text
    assert "[PHONE]" in body.text
    assert "Доклад:" in body.text
    assert "конец." in body.text
    # The bold run that held the phone now holds the [PHONE] mask and is still bold.
    bold_runs = [r for r in body.runs if r.bold]
    assert any(r.text == "[PHONE]" for r in bold_runs)

    # Split name across two runs collapsed into a single [PERSON] in the second paragraph.
    author = next(p for p in document.paragraphs if p.text.startswith("Письмо от"))
    assert "Иван" not in author.text
    assert "[PERSON]" in author.text
    # The italicised fragment survives the collapse.
    assert any(r.italic for r in author.runs)

    # Header keeps its 16pt run while the name is masked.
    header = next(p for p in document.sections[0].header.paragraphs if "Заголовок:" in p.text)
    assert "Иван Петров" not in header.text
    assert "[PERSON]" in header.text
    assert any(r.font.size == Pt(16) for r in header.runs)

    # Table cell phone masked.
    table = document.tables[0]
    cell_text = table.cell(0, 0).text
    assert "+7 900 123-45-67" not in cell_text
    assert "[PHONE]" in cell_text


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
    masked_bytes, entities = mask_docx_in_memory(out.getvalue())
    document = DocxDocument(io.BytesIO(masked_bytes))
    assert document.paragraphs == []
    assert entities == []


def test_docx_accepts_explicit_masker() -> None:
    only_email = RuleBasedMasker(["email"])
    doc = DocxDocument()
    doc.add_paragraph("Иван Петров, ivan@mail.ru")
    out = io.BytesIO()
    doc.save(out)

    masked_bytes, entities = mask_docx_in_memory(out.getvalue(), only_email)
    text = "\n".join(p.text for p in DocxDocument(io.BytesIO(masked_bytes)).paragraphs)
    assert "[EMAIL]" in text
    assert "Иван Петров" in text
    assert [entity.label for entity in entities] == ["email"]


def test_entity_spanning_three_runs_collapses_to_one_marker() -> None:
    # «АО «Росхим»» split as «АО «» | «Росхим» | «», оно...» — must yield a
    # single [ORGANIZATION], not one marker per covered run.
    doc = DocxDocument()
    p = doc.add_paragraph()
    p.add_run("Место работы это АО «")
    p.add_run("Росхим")
    p.add_run("», оно находится здесь.")
    out = io.BytesIO()
    doc.save(out)

    masked_bytes, _entities = mask_docx_in_memory(out.getvalue())
    paragraph = DocxDocument(io.BytesIO(masked_bytes)).paragraphs[0]
    assert paragraph.text.count("[ORGANIZATION]") == 1
    assert "Росхим" not in paragraph.text
    assert "[ORGANIZATION]" in paragraph.text
    assert "оно находится здесь." in paragraph.text
    assert len(paragraph.runs) == 3  # run formatting preserved
