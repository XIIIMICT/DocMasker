"""
Reproducible bounded-memory benchmark for the DOCX transform path (P2.11).

Usage: uv run --directory back python -m benchmarks.bounded_memory
Generates synthetic DOCX packages, runs the in-memory and file-based OPC
transforms, and prints wall-clock, peak tracked memory, peak RSS (POSIX),
temporary output bytes and input/output sizes as JSON.
"""

from __future__ import annotations

import io
import json
import time
import tracemalloc
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from docx import Document

from src.masking.mapping import MaskKeys
from src.masking.ooxml.package import transform_package_to_file
from src.masking.rule_based import RuleBasedMasker


def _build_docx(paragraphs: int, media_bytes: int = 0, many_parts: int = 0) -> bytes:
    doc = Document()
    marker = "Иван Петров, ivan@example.ru"
    for index in range(paragraphs):
        doc.add_paragraph(f"Запись {index}: {marker}")
    out = io.BytesIO()
    doc.save(out)
    payload = out.getvalue()
    if media_bytes or many_parts:
        buffer = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(payload)) as source, zipfile.ZipFile(buffer, "w") as target:
            for info in source.infolist():
                target.writestr(info, source.read(info.filename))
            if media_bytes:
                target.writestr("word/media/image1.png", _png(media_bytes))
            for index in range(100, 100 + many_parts):
                target.writestr(
                    f"customXml/item{index}.xml",
                    f'<root xmlns="urn:test"><value>Запись {index}</value></root>',
                )
        payload = buffer.getvalue()
    return payload


def _png(size: int) -> bytes:
    """Minimal PNG-like opaque payload (content is irrelevant to the copy path)."""
    return b"\x89PNG\r\n\x1a\n" + bytes(size)


def _peak_rss_bytes() -> int | None:
    try:
        import resource  # noqa: PLC0415 - POSIX-only optional measurement
    except ImportError:
        return None
    getter = getattr(resource, "getrusage", None)
    measure_self = getattr(resource, "RUSAGE_SELF", None)
    if getter is None or measure_self is None:
        return None
    return int(getter(measure_self).ru_maxrss) * 1024


def _measure(name: str, payload: bytes, tmp: Path, workers: int = 1) -> dict[str, Any]:
    source = tmp / f"{name}.docx"
    source.write_bytes(payload)
    masker = RuleBasedMasker()
    tracemalloc.start()
    started = time.perf_counter()
    out = tmp / f"{name}-out.docx"
    if workers == 1:
        transform_package_to_file(source, out, mode="mask", masker=masker, keys=MaskKeys())
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(
                pool.map(
                    lambda index: transform_package_to_file(
                        source,
                        tmp / f"{name}-out-{index}.docx",
                        mode="mask",
                        masker=masker,
                        keys=MaskKeys(),
                    ),
                    range(workers),
                )
            )
    wall = time.perf_counter() - started
    _current, peak_tracked = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    outputs = list(tmp.glob(f"{name}-out*.docx"))
    return {
        "case": name,
        "input_bytes": len(payload),
        "uncompressed_bytes": sum(info.file_size for info in zipfile.ZipFile(io.BytesIO(payload)).infolist()),
        "output_bytes": sum(path.stat().st_size for path in outputs),
        "temp_files": len(outputs),
        "wall_seconds": round(wall, 3),
        "peak_tracked_bytes": peak_tracked,
        "peak_rss_bytes": _peak_rss_bytes(),
        "workers": workers,
    }


def main() -> None:
    cases: list[tuple[str, dict[str, int]]] = [
        ("small", {"paragraphs": 10}),
        ("medium", {"paragraphs": 500}),
        ("large-near-max", {"paragraphs": 200, "media_bytes": 22 * 1024 * 1024}),
        ("large-media", {"paragraphs": 20, "media_bytes": 8 * 1024 * 1024}),
        ("large-xml", {"paragraphs": 6000}),
        ("many-xml-parts", {"paragraphs": 50, "many_parts": 200}),
        ("high-concurrency", {"paragraphs": 200}),
    ]
    results: list[dict[str, Any]] = []
    with TemporaryDirectory() as folder:
        tmp = Path(folder)
        for name, kwargs in cases:
            payload = _build_docx(**kwargs)
            results.append(_measure(name, payload, tmp, workers=4 if name == "high-concurrency" else 1))
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
