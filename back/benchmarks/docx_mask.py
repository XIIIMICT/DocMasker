"""DOCX masking benchmark plus reproducers for findings 3.5 and 3.15 (see benchmarks/README.md)."""

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
        masked, entities, keys = mask_docx_in_memory(data)
        mask_ms = (time.perf_counter() - t0) * 1000

        t1 = time.perf_counter()
        restored, _unused = unmask_docx_in_memory(masked, keys.placeholders())
        unmask_ms = (time.perf_counter() - t1) * 1000

        assert not _unused
        assert restored.startswith(b"PK")
        print(
            f"{label}: {size_kb:7.0f} KB, {len(entities):6d} entities, "
            f"mask {mask_ms:8.1f} ms, unmask {unmask_ms:8.1f} ms, "
            f"peak RSS {rss_mb():7.0f} MB"
        )


def _repro_finding(bug_present: bool, finding: str) -> None:
    """Report a finding repro; the benchmark must not crash while the bug exists."""
    if bug_present:
        print(f"{finding} -> CONFIRMED (bug present; the fixing stage must flip this to FIXED)")
    else:
        print(f"{finding} -> FIXED")


def repro_dedupe_across_paragraphs() -> None:
    """3.5: identical local spans in different paragraphs collapse into one entity."""
    from io import BytesIO  # noqa: PLC0415

    from docx import Document  # noqa: PLC0415

    from src.masking.docx_service import _dedupe_entities  # noqa: PLC0415
    from src.masking.schemas import MaskedEntity  # noqa: PLC0415

    doc = Document()
    doc.add_paragraph("Первый: Иванов Иван Иванович подписал.")
    doc.add_paragraph("Второй: Иванов Иван Иванович тоже.")
    doc.add_paragraph("Третий: Иванов Иван Иванович снова.")
    buffer = BytesIO()
    doc.save(buffer)

    masked, entities, _keys = mask_docx_in_memory(buffer.getvalue())
    occurrences = masked.count(b"[PERSON_1]")
    print(
        f"3.5 dedupe e2e: 3 identical-name paragraphs -> masked_entities={len(entities)}, "
        f"placeholders in text={occurrences} (expected 3/3)"
    )
    _repro_finding(len(entities) != 3 or occurrences != 3, "3.5 dedupe e2e")

    # Direct unit-level proof: identical (start, end, label) across paragraphs.
    same = MaskedEntity(label="person", text="Иванов Иван Иванович", start=8, end=27, score=1.0)

    deduped = _dedupe_entities([same, same, same])
    print(f"3.5 dedupe unit: three identical local spans -> {len(deduped)} entity after _dedupe_entities")
    _repro_finding(len(deduped) == 1, "3.5 dedupe unit")


def repro_unknown_label() -> None:
    """3.15: arbitrary labels are accepted at construction with no validation."""
    masker = RuleBasedMasker(labels=["person", "banana"])
    bug_present = "banana" in masker.labels
    _repro_finding(bug_present, f"3.15 unknown label: labels={masker.labels} accepted silently")


def main() -> None:
    bench(RuleBasedMasker())
    print()
    repro_dedupe_across_paragraphs()
    repro_unknown_label()


if __name__ == "__main__":
    main()
