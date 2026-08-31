"""Tests for DOCX masking (formatting preservation)."""

from __future__ import annotations

import io

from docx import Document as DocxDocument
from docx.shared import Pt

from src.masking.docx_service import mask_docx_in_memory


class _Config:
    def __init__(self, max_len: int) -> None:
        self.max_len = max_len


class StubDocxModel:
    """GLiNER stand-in that detects a fixed person name and a phone."""

    def __init__(self) -> None:
        self.labels = ["person", "phone"]
        self.threshold = 0.99
        self.config = _Config(512)

    def prepare_batch(self, text: str, labels: list[str]) -> dict:
        return {
            "tokens": [text.split()],
            "start_token_map": [[0]],
            "end_token_map": [[len(text)]],
        }

    def predict_entities(self, text: str, labels: list[str], threshold: float = 0.5) -> list[dict]:
        found: list[dict] = []
        person = "Иван Петров"
        pos = text.find(person)
        if pos != -1:
            found.append({"label": "person", "text": person, "score": 0.99, "start": pos, "end": pos + len(person)})
        phone = "+7 900 123-45-67"
        pos = text.find(phone)
        if pos != -1:
            found.append({"label": "phone", "text": phone, "score": 0.99, "start": pos, "end": pos + len(phone)})
        return found


def _build_docx() -> bytes:
    doc = DocxDocument()
    # Body paragraph: name (plain) + phone (bold run), plus a heading style run in between.
    p = doc.add_paragraph()
    p.add_run("Доклад: Иван Петров, тел ")
    run_bold = p.add_run("+7 900 123-45-67")
    run_bold.bold = True
    p.add_run(", конец.")

    # Second paragraph containing the name split across two runs.
    p2 = doc.add_paragraph()
    p2.add_run("Автор ")
    p2.add_run("Иван")
    p2.add_run(" ")
    p2.add_run("Петров")
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
    model = StubDocxModel()

    masked_bytes, entities = mask_docx_in_memory(source, model)

    assert len(entities) >= 3  # person (body) + phone (bold) + header person + table phone
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
    author = next(p for p in document.paragraphs if p.text.startswith("Автор"))
    assert "Иван" not in author.text
    assert "[PERSON]" in author.text

    # Header still bold? Actually we set size, not bold. Check the mask is there.
    header = next(p for p in document.sections[0].header.paragraphs if "Заголовок:" in p.text)
    assert "Иван Петров" not in header.text
    assert "[PERSON]" in header.text
    assert any(r.font.size == Pt(16) for r in header.runs)

    # Table cell phone masked.
    table = document.tables[0]
    cell_text = table.cell(0, 0).text
    assert "+7 900 123-45-67" not in cell_text
    assert "[PHONE]" in cell_text


def test_docx_empty_file_is_fine() -> None:
    doc = DocxDocument()
    out = io.BytesIO()
    doc.save(out)
    masked_bytes, entities = mask_docx_in_memory(out.getvalue(), StubDocxModel())
    # Re-opens and saves cleanly.
    document = DocxDocument(io.BytesIO(masked_bytes))
    assert document.paragraphs == []
    assert entities == []
