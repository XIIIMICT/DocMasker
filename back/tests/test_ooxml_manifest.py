"""Result-manifest contract tests: schema, completeness and sync/job parity (§7)."""

import json
from typing import Any

import pytest
from ooxml_fixtures import _PERSON, _build_package

from src.masking.docx_service import mask_docx_in_memory
from src.masking.ooxml.manifest import build_mask_manifest, build_unmask_manifest, validate_manifest

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
