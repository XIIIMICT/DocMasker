"""DM-3: bounded OPC inventory, single-pass transforms and aggregate stats.

All fixtures are synthetic OPC packages built here; no real personal data.
"""

from __future__ import annotations

import gc
import io
import json
import re
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self, cast

import pytest

from src.masking.docx_service import mask_docx_in_memory, mask_docx_to_file, unmask_docx_in_memory
from src.masking.mapping import MaskKeys, parse_keys_yaml, placeholders_from_document
from src.masking.ooxml import package
from src.masking.ooxml.manifest import build_mask_manifest, validate_manifest
from src.masking.ooxml.package import (
    build_inventory,
    transform_package,
    transform_package_to_file,
)
from src.masking.rule_based import RuleBasedMasker

if TYPE_CHECKING:
    from src.jobs.storage import ArtifactStore

CT = "http://schemas.openxmlformats.org/package/2006/content-types"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _package(parts: dict[str, str]) -> bytes:
    """Minimal OPC package: metadata parts + the named XML parts."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            f'<Types xmlns="{CT}">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/></Types>',
        )
        z.writestr(
            "_rels/.rels",
            f'<Relationships xmlns="{PR}"><Relationship Id="rId1" '
            f'Type="{R}/officeDocument" Target="word/document.xml"/></Relationships>',
        )
        z.writestr(
            "word/document.xml",
            f'<w:document xmlns:w="{W}"><w:body>'
            "<w:p><w:r><w:t>user0@example.ru</w:t></w:r></w:p></w:body></w:document>",
        )
        for name, xml in parts.items():
            z.writestr(name, xml.encode("utf-8"))
    return out.getvalue()


def _generic_payload(count: int, filler: str = "запись") -> dict[str, str]:
    return {
        f"customXml/item{index}.xml": (f'<root xmlns="urn:test"><value>{filler}{index}</value></root>')
        for index in range(count)
    }


class _TrackedBytes(bytes):
    """A bytes payload that reports when its last reference is released."""

    name: str
    live: set[str]

    def __new__(cls, value: bytes, name: str, live: set[str]) -> Self:
        obj = super().__new__(cls, value)
        obj.name = name
        obj.live = live
        live.add(name)
        return obj

    def __del__(self) -> None:
        self.live.discard(self.name)


# --- inventory holds metadata, not payloads ----------------------------------


def test_inventory_keeps_metadata_and_no_xml_payload() -> None:
    source = _package(_generic_payload(3))
    parts, coverage = build_inventory(source)
    by_name = {part.name: part for part in parts}
    custom = by_name["customXml/item1.xml"]
    assert custom.content_type == "application/xml"
    assert custom.disposition == "process"
    assert custom.category == "generic_xml"
    assert custom.relationship_type == "other"
    assert custom.is_xml
    assert by_name["word/document.xml"].category == "main_document"
    assert by_name["_rels/.rels"].category == "relationships"
    assert coverage.unprocessed_text_parts == []
    assert coverage.rejected_parts == []


def test_only_one_decompressed_part_is_resident_at_a_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _package(_generic_payload(6, filler="ф " * 10_000))
    source_path = tmp_path / "source.docx"
    source_path.write_bytes(source)
    live: set[str] = set()
    reads: list[str] = []
    real_read = package._read_part

    def tracking_read(archive: zipfile.ZipFile, name: str) -> bytes:
        assert not live, f"previous part still resident: {live}"
        reads.append(name)
        return _TrackedBytes(real_read(archive, name), name, live)

    monkeypatch.setattr(package, "_read_part", tracking_read)
    gc.collect()
    transform_package_to_file(
        source_path,
        tmp_path / "out.docx",
        mode="mask",
        masker=RuleBasedMasker.for_labels(["email"]),
        keys=MaskKeys(),
    )
    gc.collect()
    assert live == set()  # every decompressed payload was released
    assert len(reads) == len(set(reads)) == 8  # rels + document.xml + six custom parts, each once


# --- aggregate stats replace the occurrence list -----------------------------


def test_high_occurrence_file_transform_keeps_manifest_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    occurrences = 3_000
    text = " ".join(f"user{index}@example.ru" for index in range(occurrences))
    source = _package({"customXml/item1.xml": f'<root xmlns="urn:test"><value>{text}</value></root>'})
    source_path = tmp_path / "source.docx"
    source_path.write_bytes(source)
    detail_args: list[Any] = []
    real_transform = package._transform_part

    def spy(*args: Any, **kwargs: Any) -> bytes | None:  # noqa: ANN401
        detail_args.append(kwargs["detail"])
        return real_transform(*args, **kwargs)

    monkeypatch.setattr(package, "_transform_part", spy)
    stats, _unused, coverage = mask_docx_to_file(
        source_path,
        tmp_path / "out.docx",
        RuleBasedMasker.for_labels(["email"]),
    )
    assert detail_args
    assert all(item is None for item in detail_args)
    assert not hasattr(stats, "entities")  # no per-occurrence list on the job path
    assert stats._seen == {}  # distinct values come from MaskKeys numbering
    expected = {
        "total_occurrences": occurrences + 1,  # one email in word/document.xml
        "occurrences_by_label": {"email": occurrences + 1},
        "unique_by_label": {"email": occurrences},  # user0 repeats in document.xml
    }
    assert stats.as_manifest_stats() == expected
    manifest = build_mask_manifest(
        source_bytes=b"",
        document_name="doc.docx",
        keys_name="doc.yaml",
        stats=stats.as_manifest_stats(),
        coverage=coverage.as_manifest(),
        cache_hit=False,
        namespace_version=1,
        masking_ruleset_version=1,
        exception_config_digest="digest",
        input_digest=("deadbeef", len(source)),
    )
    assert validate_manifest(manifest, "mask_file")["statistics"] == expected


# --- path / in-memory parity and cancellation --------------------------------


def test_file_and_memory_transforms_are_equivalent(tmp_path: Path) -> None:
    source = _package({})
    source_path = tmp_path / "source.docx"
    source_path.write_bytes(source)
    memory, entities, keys, coverage = mask_docx_in_memory(source, RuleBasedMasker.for_labels(["email"]))
    stats, _unused, file_coverage = mask_docx_to_file(
        source_path,
        tmp_path / "out.docx",
        RuleBasedMasker.for_labels(["email"]),
    )
    assert (tmp_path / "out.docx").read_bytes() == memory
    assert stats.total == len(entities)
    assert file_coverage.as_manifest() == coverage.as_manifest()
    placeholders = placeholders_from_document(parse_keys_yaml(keys.to_yaml()))
    from_memory, unused_memory, _ = unmask_docx_in_memory(memory, placeholders)
    from_file, unused_file, _ = unmask_docx_in_memory((tmp_path / "out.docx").read_bytes(), placeholders)
    assert from_memory == from_file
    assert unused_memory == unused_file == []


def test_checkpoint_stops_before_next_part(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _package(_generic_payload(6))
    source_path = tmp_path / "source.docx"
    source_path.write_bytes(source)
    reads: list[str] = []
    real_read = package._read_part

    def counting_read(archive: zipfile.ZipFile, name: str) -> bytes:
        reads.append(name)
        return real_read(archive, name)

    monkeypatch.setattr(package, "_read_part", counting_read)
    calls = 0

    def stop() -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            msg = "cancelled"
            raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="cancelled"):
        transform_package_to_file(
            source_path,
            tmp_path / "out.docx",
            mode="mask",
            masker=RuleBasedMasker.for_labels(["email"]),
            keys=MaskKeys(),
            checkpoint=stop,
        )
    assert len(reads) == 1  # no second payload was decompressed


def test_checkpoint_stops_between_detection_windows(tmp_path: Path) -> None:
    text = " ".join(f"user{index}@example.ru" for index in range(3_000))
    source = _package({"customXml/item1.xml": f'<root xmlns="urn:test"><value>{text}</value></root>'})
    calls = 0

    def stop() -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            msg = "cancelled"
            raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="cancelled"):
        transform_package(
            source,
            mode="mask",
            masker=RuleBasedMasker.for_labels(["email"]),
            keys=MaskKeys(),
            checkpoint=stop,
        )
    assert calls == 3  # forwarded into the windowed detector, not only part boundaries


def test_job_execute_builds_manifest_from_bounded_stats(tmp_path: Path) -> None:
    """The worker path never builds an occurrence list for the manifest."""
    from src.jobs.operations import execute
    from src.masking.ooxml.manifest import MANIFEST_NAME

    source = _package({})
    source_path = tmp_path / "job.input"
    source_path.write_bytes(source)

    class FakeStore:
        root = tmp_path

        def read(self, job_id: str, suffix: str) -> bytes:
            return {"kind": b"mask_file", "name": b"doc.docx"}[suffix]

        def path_for(self, job_id: str, suffix: str) -> Path:
            return source_path if suffix == "input" else tmp_path / f"job.{suffix}"

    result = execute(cast("ArtifactStore", FakeStore()), "job", progress=lambda _phase, _pct: None)
    manifest = json.loads(cast("bytes", result[MANIFEST_NAME]))
    assert manifest["statistics"] == {
        "total_occurrences": 1,
        "occurrences_by_label": {"email": 1},
        "unique_by_label": {"email": 1},
    }
    assert manifest["complete"] is True


# --- one bounded unmask scan per logical container ----------------------------


def test_unmask_uses_one_scan_per_container_and_stays_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    markers = [f"[PERSON_{index}]" for index in range(1, 51)]
    text = " ".join(markers[1:])  # PERSON_1 is split across runs below
    body = (
        f'<w:document xmlns:w="{W}"><w:body><w:p>'
        "<w:r><w:t>[PER</w:t></w:r><w:r><w:t>SON_1]</w:t></w:r>"
        f"<w:r><w:t> {text}</w:t></w:r></w:p></w:body></w:document>"
    )
    source = _package({})
    buffer = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(source)) as read_archive,
        zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as write_archive,
    ):
        for info in read_archive.infolist():
            payload = body.encode("utf-8") if info.filename == "word/document.xml" else read_archive.read(info)
            write_archive.writestr(info, payload)
    masked = buffer.getvalue()

    real_compile = package.compile_placeholders
    compiled: list[CountingPattern | None] = []

    class CountingPattern:
        def __init__(self, pattern: re.Pattern[str]) -> None:
            self.pattern = pattern
            self.calls = 0

        def finditer(self, value: str) -> Iterator[re.Match[str]]:
            self.calls += 1
            return self.pattern.finditer(value)

    def compiling(placeholders: dict[str, str]) -> CountingPattern | None:
        pattern = real_compile(placeholders)
        if pattern is None:
            compiled.append(None)
            return None
        result = CountingPattern(pattern)
        compiled.append(result)
        return result

    monkeypatch.setattr(package, "compile_placeholders", compiling)
    placeholders = {marker: f"значение{index}" for index, marker in enumerate(markers)}
    restored, unused, _coverage = unmask_docx_in_memory(masked, placeholders)
    assert unused == []
    assert len(compiled) == 1  # compiled once per transform, not per part/placeholder
    assert compiled[0] is not None
    assert compiled[0].calls == 1  # one bounded pass for the single container
    with zipfile.ZipFile(io.BytesIO(restored)) as archive:
        body_out = archive.read("word/document.xml").decode("utf-8")
    assert "SON_1]" not in body_out
    assert "[PERSON_" not in body_out
    assert "значение0" in body_out
    again, unused_again, _ = unmask_docx_in_memory(restored, placeholders)
    assert again == restored  # re-applying changes nothing
    assert unused_again == list(placeholders.values())
