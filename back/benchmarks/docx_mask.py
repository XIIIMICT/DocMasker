"""DOCX masking benchmark plus reproducers for findings 3.5 and 3.15 (see benchmarks/README.md)."""

import io
import time

from src.masking.docx_service import mask_docx_in_memory, unmask_docx_in_memory
from src.masking.rule_based import RuleBasedMasker

from .synthetic import DocxShape, build_docx
from .util import rss_mb


def bench(masker) -> None:
    for label, shape in [
        ("docx 100 paragraphs", DocxShape(paragraphs=100)),
        ("docx 1000 paragraphs", DocxShape(paragraphs=1_000)),
        ("docx 5000 paragraphs", DocxShape(paragraphs=5_000)),
        ("docx 200 paragraphs + 200x2 nested table", DocxShape(paragraphs=200, table_rows=200, nested=True)),
    ]:
        data = build_docx(shape)
        size_kb = len(data) / 1024

        t0 = time.perf_counter()
        masked, entities, keys, _coverage = mask_docx_in_memory(data)
        mask_ms = (time.perf_counter() - t0) * 1000

        t1 = time.perf_counter()
        restored, _unused, _coverage = unmask_docx_in_memory(masked, keys.placeholders())
        unmask_ms = (time.perf_counter() - t1) * 1000

        assert not _unused
        assert restored.startswith(b"PK")
        print(
            f"{label}: {size_kb:7.0f} KB, {len(entities):6d} entities, "
            f"mask {mask_ms:8.1f} ms, unmask {unmask_ms:8.1f} ms, "
            f"peak RSS {rss_mb():7.0f} MB"
        )


def _repro_finding(*, bug_present: bool, finding: str) -> None:
    """Report a finding repro; the benchmark must not crash while the bug exists."""
    if bug_present:
        print(f"{finding} -> CONFIRMED (bug present; the fixing stage must flip this to FIXED)")
    else:
        print(f"{finding} -> FIXED")


def repro_dedupe_across_paragraphs() -> None:
    """3.5: identical local spans in different paragraphs must all be reported."""
    from docx import Document  # noqa: PLC0415

    doc = Document()
    doc.add_paragraph("Первый: Иванов Иван Иванович подписал.")
    doc.add_paragraph("Второй: Иванов Иван Иванович тоже.")
    doc.add_paragraph("Третий: Иванов Иван Иванович снова.")
    buffer = io.BytesIO()
    doc.save(buffer)

    masked, entities, _keys, _coverage = mask_docx_in_memory(buffer.getvalue())
    document = Document(io.BytesIO(masked))
    occurrences = sum(paragraph.text.count("[PERSON_1]") for paragraph in document.paragraphs)
    print(
        f"3.5 dedupe e2e: 3 identical-name paragraphs -> masked_entities={len(entities)}, "
        f"placeholders in text={occurrences} (expected 3/3)"
    )
    _repro_finding(bug_present=len(entities) != 3 or occurrences != 3, finding="3.5 dedupe e2e")


def repro_unknown_label() -> None:
    """3.15: unknown labels must be a loud configuration error, not silent."""
    try:
        RuleBasedMasker.for_labels(["person", "banana"])
    except ValueError:
        print("3.15 unknown label: rejected with ValueError as required")
        _repro_finding(bug_present=False, finding="3.15 unknown label")
        return
    _repro_finding(
        bug_present=True,
        finding="3.15 unknown label: labels=('person', 'banana') accepted silently",
    )


def main() -> None:
    bench(RuleBasedMasker())
    print()
    repro_dedupe_across_paragraphs()
    repro_unknown_label()


if __name__ == "__main__":
    main()
