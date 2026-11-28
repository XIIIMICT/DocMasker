"""Synthetic OPC fixtures and coverage tests (Phase 5, §6.6).

Every required text-bearing part is built by hand with namespace URIs (including
a default-namespace variant and a custom-prefix variant), so the suite fails if
a supported part is silently skipped. No real PII is used.
"""

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from src.masking.docx_service import mask_docx_in_memory, unmask_docx_in_memory
from src.masking.mapping import MaskKeys, parse_keys_yaml, placeholders_from_document
from src.masking.ooxml.manifest import build_mask_manifest, build_unmask_manifest, validate_manifest
from src.masking.ooxml.package import (
    OoxmlPackageError,
    build_inventory,
    transform_package,
    transform_package_to_file,
)
from src.masking.rule_based import RuleBasedMasker

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
CP = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC = "http://purl.org/dc/elements/1.1/"
EP = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
VT = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"

_PERSON = "Иван Петров"
_EMAIL = "ivan@example.ru"


def _doc(prefix: str = "w", *, default_ns: bool = False) -> str:
    """Build a main document with every WordprocessingML container variant."""
    if default_ns:
        w = ""
        open_tag = f'<document xmlns="{W}" xmlns:a="{A}" xmlns:wp="{WP}">'
        t = "t"
        p = "p"
        r = "r"
        del_text = "delText"
        txbx = "txbxContent"
        sdt = "sdt"
    else:
        w = f"{prefix}:"
        open_tag = f'<{prefix}:document xmlns:{prefix}="{W}" xmlns:a="{A}" xmlns:wp="{WP}">'
        t = f"{prefix}:t"
        p = f"{prefix}:p"
        r = f"{prefix}:r"
        del_text = f"{prefix}:delText"
        txbx = f"{prefix}:txbxContent"
        sdt = f"{prefix}:sdt"
    body = f"<{p}><{r}><{t}>{_PERSON}</{t}></{r}></{p}>"
    # Tracked deletion (w:delText) still carries PII.
    deleted = f"<{p}><{w}del><{r}><{del_text}>{_EMAIL}</{del_text}></{r}></{w}del></{p}>"
    # Textbox (w:txbxContent) with nested paragraph and DrawingML text (a:t).
    textbox = (
        f"<{p}><{r}><{txbx}><{p}><{r}><{t}>{_PERSON}</{t}></{r}></{p}>"
        f"</{txbx}></{r}></{p}>"
        f"<{p}><{r}><a:t>{_EMAIL}</a:t></{r}></{p}>"
    )
    # Content control with a nested paragraph.
    control = f"<{sdt}><{p}><{r}><{t}>{_PERSON}</{t}></{r}></{p}></{sdt}>"
    # Alt-text attribute allowlist.
    alt = f'<{p}><{r}><wp:docPr descr="{_PERSON}" title="{_EMAIL}"/></{r}></{p}>'
    return f'<?xml version="1.0" encoding="UTF-8"?>{open_tag}{body}{deleted}{textbox}{control}{alt}</{w}document>'


def _simple_part(tag: str, uri: str, value: str, *, prefix: str = "w") -> str:
    return f'<?xml version="1.0"?><{prefix}:{tag} xmlns:{prefix}="{uri}"><{prefix}:p><{prefix}:r><{prefix}:t>{value}</{prefix}:t></{prefix}:r></{prefix}:p></{prefix}:{tag}>'


def _build_package(
    *,
    default_ns: bool = False,
    big_marker: bool = False,
    extra: dict[str, bytes] | None = None,
    core_xml: str | None = None,
    comments_xml: str | None = None,
    content_types_extra: str = "",
    root_rels_extra: str = "",
) -> bytes:
    """Minimal but structurally valid OPC package with all required parts."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            f'<Types xmlns="{CT}"><Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            f"{content_types_extra}</Types>",
        )
        z.writestr(
            "_rels/.rels",
            f'<Relationships xmlns="{PR}"><Relationship Id="rId1" '
            f'Type="{R}/officeDocument" Target="word/document.xml"/>{root_rels_extra}</Relationships>',
        )
        z.writestr(
            "word/_rels/document.xml.rels",
            f'<Relationships xmlns="{PR}">'
            f'<Relationship Id="rId1" Type="{R}/footnotes" Target="footnotes.xml"/>'
            f'<Relationship Id="rId2" Type="{R}/endnotes" Target="endnotes.xml"/>'
            f'<Relationship Id="rId3" Type="{R}/comments" Target="comments.xml"/>'
            f'<Relationship Id="rId4" Type="{R}/header" Target="header1.xml"/>'
            f'<Relationship Id="rId5" Type="{R}/hyperlink" '
            f'Target="mailto:{_EMAIL}" TargetMode="External"/>'
            "</Relationships>",
        )
        doc = _doc(default_ns=default_ns)
        if big_marker:
            # A PII marker well beyond the old 64 KiB prefix scan.
            doc = doc.replace(
                f"<{('' if default_ns else 'w:')}t>{_EMAIL}</{('' if default_ns else 'w:')}t>",
                f"<{('' if default_ns else 'w:')}t>{'x' * 70000}{_EMAIL}</{('' if default_ns else 'w:')}t>",
                1,
            )
        z.writestr("word/document.xml", doc)
        z.writestr("word/footnotes.xml", _simple_part("footnotes", W, _PERSON))
        z.writestr("word/endnotes.xml", _simple_part("endnotes", W, _EMAIL))
        z.writestr("word/comments.xml", comments_xml or _simple_part("comments", W, _PERSON))
        z.writestr("word/header1.xml", _simple_part("hdr", W, _PERSON, prefix="w"))
        z.writestr(
            "docProps/core.xml",
            core_xml
            or (
                f'<cp:coreProperties xmlns:cp="{CP}" xmlns:dc="{DC}">'
                f"<dc:creator>{_PERSON}</dc:creator><dc:title>{_EMAIL}</dc:title>"
                "<cp:lastModifiedBy>Иван Петров</cp:lastModifiedBy></cp:coreProperties>"
            ),
        )
        z.writestr(
            "docProps/app.xml",
            f'<Properties xmlns="{EP}"><Company>{_PERSON}</Company><Manager>{_EMAIL}</Manager></Properties>',
        )
        # Unrelated binary entry must survive byte-for-byte.
        z.writestr("word/media/image1.png", b"\x89PNG\r\n\x1a\nFAKE")
        if extra:
            for name, payload in extra.items():
                z.writestr(name, payload)
    return buf.getvalue()


def _all_text(data: bytes) -> str:
    """Concatenate every text node and allowed attribute in the package."""
    from defusedxml import ElementTree as SafeET

    chunks: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            if not name.endswith((".xml", ".rels")):
                continue
            root = SafeET.fromstring(z.read(name))
            for element in root.iter():
                if isinstance(element.tag, str) and element.text:
                    chunks.append(element.text)
                for attr in ("descr", "title", "Target"):
                    if element.get(attr):
                        chunks.append(element.get(attr) or "")
    return " ".join(chunks)


# --- §6.6 coverage -----------------------------------------------------------


def test_all_supported_parts_are_masked_and_restored() -> None:
    source = _build_package()
    masked, _entities, keys, coverage = mask_docx_in_memory(source)
    assert _PERSON not in _all_text(masked)
    assert _EMAIL not in _all_text(masked)
    assert "[PERSON_1]" in _all_text(masked)
    categories = set(coverage.processed_parts)
    assert {"main_document", "footnotes", "endnotes", "comments", "headers_footers"} <= categories, categories
    assert any(part.category in ("core_properties", "other") for part in coverage.processed)
    # Unrelated binary part survives byte-for-byte.
    with zipfile.ZipFile(io.BytesIO(source)) as a, zipfile.ZipFile(io.BytesIO(masked)) as b:
        assert a.read("word/media/image1.png") == b.read("word/media/image1.png")

    placeholders = placeholders_from_document(parse_keys_yaml(keys.to_yaml()))
    restored, unused, _coverage = unmask_docx_in_memory(masked, placeholders)
    assert unused == []
    assert _PERSON in _all_text(restored)
    assert _EMAIL in _all_text(restored)
    with zipfile.ZipFile(io.BytesIO(source)) as a, zipfile.ZipFile(io.BytesIO(restored)) as b:
        assert a.read("word/media/image1.png") == b.read("word/media/image1.png")


def test_default_namespace_is_processed_identically() -> None:
    source = _build_package(default_ns=True)
    masked, _entities, _keys, _coverage = mask_docx_in_memory(source)
    assert _PERSON not in _all_text(masked)
    assert _EMAIL not in _all_text(masked)


def test_marker_after_64kib_is_still_found() -> None:
    source = _build_package(big_marker=True)
    masked, _entities, _keys, _coverage = mask_docx_in_memory(source)
    assert _EMAIL not in _all_text(masked)
    assert "[EMAIL_1]" in _all_text(masked)


def test_metadata_fields_do_not_form_cross_field_entities() -> None:
    """Adjacent docProps values must not be concatenated into one fake person.

    Regression: dc:creator + cp:lastModifiedBy with the same FIO produced the
    key «Иван ПетровИван» because both standalone refs shared one container.
    """
    core = (
        f'<cp:coreProperties xmlns:cp="{CP}" xmlns:dc="{DC}">'
        f"<dc:creator>{_PERSON}</dc:creator>"
        f"<cp:lastModifiedBy>{_PERSON}</cp:lastModifiedBy>"
        "</cp:coreProperties>"
    )
    source = _build_package(core_xml=core)
    masked, _entities, keys, _coverage = mask_docx_in_memory(source)
    values = parse_keys_yaml(keys.to_yaml()).get("Физлица", [])
    assert values == [_PERSON]
    with zipfile.ZipFile(io.BytesIO(masked)) as z:
        masked_core = z.read("docProps/core.xml").decode("utf-8")
    assert _PERSON not in masked_core
    assert masked_core.count("[PERSON_1]") == 2

    restored, _unused, _coverage = unmask_docx_in_memory(
        masked, placeholders_from_document(parse_keys_yaml(keys.to_yaml()))
    )
    with zipfile.ZipFile(io.BytesIO(restored)) as z:
        restored_core = z.read("docProps/core.xml").decode("utf-8")
    assert restored_core.count(_PERSON) == 2


def test_relationship_target_and_metadata_are_masked() -> None:
    source = _build_package()
    masked, _entities, _keys, _coverage = mask_docx_in_memory(source)
    with zipfile.ZipFile(io.BytesIO(masked)) as z:
        rels = z.read("word/_rels/document.xml.rels").decode("utf-8")
        core = z.read("docProps/core.xml").decode("utf-8")
        app = z.read("docProps/app.xml").decode("utf-8")
    assert f"mailto:{_EMAIL}" not in rels
    assert _PERSON not in core
    assert _EMAIL not in core
    assert _PERSON not in app
    assert _EMAIL not in app


def test_shared_part_is_processed_exactly_once() -> None:
    source = _build_package()
    parts, _coverage = build_inventory(source)
    names = [part.name for part in parts]
    assert len(names) == len(set(names))
    assert names.count("word/header1.xml") == 1


def test_malformed_xml_is_rejected() -> None:
    from src.masking.mapping import MaskKeys

    source = _build_package(extra={"word/broken.xml": b"<not-closed"})
    with pytest.raises(OoxmlPackageError):
        transform_package(source, mode="mask", masker=RuleBasedMasker(), keys=MaskKeys())


def test_embedded_binary_object_is_rejected() -> None:
    from src.masking.mapping import MaskKeys

    source = _build_package(extra={"word/embeddings/oleObject1.bin": b"\x00\x01"})
    with pytest.raises(OoxmlPackageError, match="неподдерживаемое"):
        transform_package(source, mode="mask", masker=RuleBasedMasker(), keys=MaskKeys())


def test_custom_properties_and_author_attributes_are_masked() -> None:
    """Custom vt:* values and comment authors are identifying text (P0.3)."""
    custom = (
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties">'
        f'<CustomDocumentProperties xmlns:vt="{VT}">'
        f"<vt:lpwstr>{_PERSON}</vt:lpwstr>"
        f"<vt:lpstr>{_EMAIL}</vt:lpstr>"
        "</CustomDocumentProperties></Properties>"
    )
    comments = (
        f'<w:comments xmlns:w="{W}"><w:comment w:author="{_PERSON}" w:initials="{_PERSON}">'
        f"<w:p><w:r><w:t>{_PERSON}</w:t></w:r></w:p></w:comment></w:comments>"
    )
    source = _build_package(
        comments_xml=comments,
        extra={"docProps/custom.xml": custom.encode("utf-8")},
    )
    masked, _entities, _keys, coverage = mask_docx_in_memory(source)
    assert "custom_properties" in coverage.processed_parts
    with zipfile.ZipFile(io.BytesIO(masked)) as archive:
        custom_out = archive.read("docProps/custom.xml").decode("utf-8")
        comments_out = archive.read("word/comments.xml").decode("utf-8")
    assert _PERSON not in custom_out
    assert _EMAIL not in custom_out
    assert _PERSON not in comments_out
    assert 'w:author="[PERSON_1]"' in comments_out
    assert 'w:initials="[PERSON_1]"' in comments_out


def test_coverage_records_clean_parts_with_accurate_text_node_counts() -> None:
    """A supported part with zero detections is still processed and counted."""
    _masked, _entities, _keys, coverage = mask_docx_in_memory(_build_package())
    assert "headers_footers" in coverage.processed_parts
    header = next(part for part in coverage.processed if part.name == "word/header1.xml")
    assert header.text_nodes == 1
    assert header.category == "headers_footers"
    assert coverage.unprocessed_text_parts == []
    assert coverage.rejected_parts == []


def test_unknown_text_bearing_part_is_rejected_not_reported_complete() -> None:
    """A chart-like part with PII-capable text must fail closed (P0.3)."""
    chart_ct = (
        '<Override PartName="/word/charts/chart1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.drawingml.chart+xml"/>'
    )
    source = _build_package(
        content_types_extra=chart_ct,
        extra={"word/charts/chart1.xml": f"<c:chart xmlns:c='x'><c:t>{_EMAIL}</c:t></c:chart>".encode()},
    )
    from src.masking.mapping import MaskKeys

    parts, coverage = build_inventory(source)
    assert any(part.category == "unprocessed" for part in parts)
    assert "word/charts/chart1.xml" in coverage.unprocessed_text_parts
    with pytest.raises(OoxmlPackageError, match="неподдерживаемое"):
        transform_package(source, mode="mask", masker=RuleBasedMasker(), keys=MaskKeys())


def test_root_and_nested_relationship_targets_resolve_to_owning_part() -> None:
    """Root targets use package root; part targets are relative to that part."""
    source = _build_package(
        root_rels_extra=(f'<Relationship Id="rId9" Type="{R}/customXml" Target="customXml/item1.xml"/>'),
        extra={"customXml/item1.xml": f"<root>{_PERSON}</root>".encode()},
    )
    parts, coverage = build_inventory(source)
    categories = {part.name: part.category for part in parts}
    assert categories["customXml/item1.xml"] == "generic_xml"
    assert coverage.unprocessed_text_parts == []
    masked, _entities, _keys, _coverage = mask_docx_in_memory(source)
    with zipfile.ZipFile(io.BytesIO(masked)) as archive:
        assert _PERSON not in archive.read("customXml/item1.xml").decode("utf-8")


# --- §7 manifest contract ----------------------------------------------------


def test_manifest_validation_rejects_bad_schema() -> None:
    good = build_mask_manifest(
        source_bytes=b"x",
        document_name="a.docx",
        keys_name="a.yaml",
        entities=[{"label": "person", "text": _PERSON, "start": 0, "end": 11, "score": 1.0}],
        coverage={
            "processed_parts": [],
            "unprocessed_text_parts": [],
            "rejected_parts": [],
            "raster_image_ocr": "not_performed",
        },
        cache_hit=False,
        namespace_version=1,
        masking_ruleset_version=1,
        exception_config_digest="digest",
    )
    assert validate_manifest(good, "mask_file")["schema_version"] == 1
    with pytest.raises(TypeError):
        validate_manifest("not-a-dict", "mask_file")
    with pytest.raises(ValueError, match="версия"):
        validate_manifest({}, "mask_file")
    with pytest.raises(ValueError, match="версия"):
        validate_manifest({**good, "schema_version": 99}, "mask_file")
    with pytest.raises(ValueError, match="Некорректный"):
        validate_manifest(good, "unmask_file")


def test_successful_manifests_are_complete_with_empty_coverage_arrays() -> None:
    """Producer parity with the client's strict result guard (A2)."""
    clean = {
        "processed_parts": [],
        "unprocessed_text_parts": [],
        "rejected_parts": [],
        "raster_image_ocr": "not_performed",
    }

    def build(operation: str, coverage: dict[str, Any]) -> dict[str, Any]:
        common: dict[str, Any] = {
            "source_bytes": b"x",
            "document_name": "doc.docx",
            "coverage": coverage,
            "cache_hit": False,
            "namespace_version": 1,
            "masking_ruleset_version": 1,
            "exception_config_digest": "digest",
        }
        if operation == "mask_file":
            return build_mask_manifest(keys_name="doc.yaml", entities=[], **common)
        return build_unmask_manifest(unused_keys=[], **common)

    for operation in ("mask_file", "unmask_file"):
        delivered = build(operation, clean)
        assert delivered["complete"] is True
        assert delivered["coverage"]["unprocessed_text_parts"] == []
        assert delivered["coverage"]["rejected_parts"] == []
        assert validate_manifest(delivered, operation) is delivered
        # A partial/rejected package can never be labelled as a complete success.
        for field in ("unprocessed_text_parts", "rejected_parts"):
            partial = {**clean, field: ["word/charts/chart1.xml"]}
            assert build(operation, partial)["complete"] is False


def test_sync_and_job_manifest_parity() -> None:
    """Same source + config => same logical manifest fields (§7.3)."""
    source = _build_package()
    _masked, entities, _keys, coverage = mask_docx_in_memory(source)
    ctx: dict[str, Any] = {
        "masking_ruleset_version": 1,
        "exception_config_digest": "digest",
        "namespace_version": 1,
    }
    sync = build_mask_manifest(
        source_bytes=source,
        document_name="doc (замаскированный).docx",
        keys_name="doc (ключи демаскировки).yaml",
        entities=[e.model_dump() for e in entities],
        coverage=coverage.as_manifest(),
        cache_hit=False,
        **ctx,
    )
    job = build_mask_manifest(
        source_bytes=source,
        document_name="doc (замаскированный).docx",
        keys_name="doc (ключи демаскировки).yaml",
        entities=[e.model_dump() for e in entities],
        coverage=coverage.as_manifest(),
        cache_hit=False,
        **ctx,
    )
    assert json.dumps(sync, sort_keys=True, ensure_ascii=False) == json.dumps(job, sort_keys=True, ensure_ascii=False)
    assert sync["input"]["size_bytes"] == len(source)
    assert sync["statistics"]["total_occurrences"] == len(entities)


def test_bounded_inventory_leaves_media_unread(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inventory metadata only: no XML payload is read or retained (DM-3)."""
    import dataclasses
    import zipfile as _zipfile

    source = _build_package()
    read_names: list[str] = []
    original = _zipfile.ZipFile.read

    def recording_read(self: _zipfile.ZipFile, name: str, pwd: bytes | None = None) -> bytes:
        read_names.append(name)
        return original(self, name, pwd)

    monkeypatch.setattr(_zipfile.ZipFile, "read", recording_read)
    parts, _coverage = build_inventory(source)
    assert not {field.name for field in dataclasses.fields(parts[0]) if field.name in {"data", "payload", "xml"}}
    assert all(name == "[Content_Types].xml" or name.endswith(".rels") for name in read_names)
    assert "word/document.xml" not in read_names
    media = next(part for part in parts if part.name.endswith(".png"))
    document = next(part for part in parts if part.name == "word/document.xml")
    assert media.disposition == "copy"
    assert document.disposition == "process"


def test_file_transform_matches_in_memory_transform(tmp_path: Path) -> None:
    source = _build_package()
    source_path = tmp_path / "source.docx"
    out_path = tmp_path / "masked.docx"
    source_path.write_bytes(source)

    keys = MaskKeys()
    in_memory, entities, _unused, coverage = transform_package(source, mode="mask", masker=RuleBasedMasker(), keys=keys)
    stats, _unused, file_coverage = transform_package_to_file(
        source_path,
        out_path,
        mode="mask",
        masker=RuleBasedMasker(),
        keys=MaskKeys(),
    )
    assert out_path.read_bytes() == in_memory
    assert stats.total == len(entities)
    assert stats.occurrences_by_label == {
        label: sum(entity["label"] == label for entity in entities)
        for label in {entity["label"] for entity in entities}
    }
    assert stats.unique_by_label == {
        label: len({entity["text"] for entity in entities if entity["label"] == label})
        for label in stats.occurrences_by_label
    }
    assert file_coverage.as_manifest() == coverage.as_manifest()
    assert keys.to_yaml()  # both paths allocate document-wide keys


def test_transform_package_checkpoints_before_each_expensive_part() -> None:
    """A cancellation raised at a part boundary must stop OPC work immediately."""
    calls: list[int] = []

    def stop_early() -> None:
        calls.append(1)
        if len(calls) == 2:
            msg = "cancelled"
            raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="cancelled"):
        transform_package(
            _build_package(),
            mode="mask",
            masker=RuleBasedMasker(),
            keys=MaskKeys(),
            checkpoint=stop_early,
        )
    assert len(calls) == 2  # stopped at the second part, not after the whole document
