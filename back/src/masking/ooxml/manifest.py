"""One versioned result manifest for sync and job file operations (§7)."""

import hashlib
from typing import Any

MANIFEST_NAME = "manifest.json"
MANIFEST_SCHEMA_VERSION = 1


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _input_metadata(source_bytes: bytes, digest: tuple[str, int] | None) -> dict[str, Any]:
    """Use the already streamed digest/size when the worker provides one."""
    if digest is not None:
        return {"sha256": digest[0], "size_bytes": digest[1]}
    return {"sha256": _sha256(source_bytes), "size_bytes": len(source_bytes)}


def build_mask_manifest(
    *,
    source_bytes: bytes,
    document_name: str,
    keys_name: str,
    entities: list[dict[str, Any]],
    coverage: dict[str, Any],
    cache_hit: bool,
    namespace_version: int,
    masking_ruleset_version: int,
    exception_config_digest: str,
    input_digest: tuple[str, int] | None = None,
) -> dict[str, Any]:
    """Canonical `manifest.json` for `mask_file` (same from sync and worker)."""
    occurrences: dict[str, int] = {}
    unique_values: dict[str, set[str]] = {}
    for entity in entities:
        label = entity["label"]
        occurrences[label] = occurrences.get(label, 0) + 1
        unique_values.setdefault(label, set()).add(entity["text"])
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "operation": "mask_file",
        "status": "succeeded",
        "complete": not coverage.get("unprocessed_text_parts") and not coverage.get("rejected_parts"),
        "masking_ruleset_version": masking_ruleset_version,
        "exception_config_digest": exception_config_digest,
        "input": _input_metadata(source_bytes, input_digest),
        "output": {"document_name": document_name, "keys_name": keys_name},
        "statistics": {
            "total_occurrences": len(entities),
            "occurrences_by_label": occurrences,
            "unique_by_label": {label: len(values) for label, values in unique_values.items()},
        },
        "coverage": coverage,
        "cache": {"hit": cache_hit, "namespace_version": namespace_version},
    }


def build_unmask_manifest(
    *,
    source_bytes: bytes,
    document_name: str,
    unused_keys: list[str],
    coverage: dict[str, Any],
    cache_hit: bool,
    namespace_version: int,
    masking_ruleset_version: int,
    exception_config_digest: str,
    input_digest: tuple[str, int] | None = None,
) -> dict[str, Any]:
    """Canonical `manifest.json` for `unmask_file` (same from sync and worker)."""
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "operation": "unmask_file",
        "status": "succeeded",
        "complete": not coverage.get("unprocessed_text_parts") and not coverage.get("rejected_parts"),
        "masking_ruleset_version": masking_ruleset_version,
        "exception_config_digest": exception_config_digest,
        "input": _input_metadata(source_bytes, input_digest),
        "output": {"document_name": document_name},
        "statistics": {"unused_keys": unused_keys},
        "coverage": coverage,
        "cache": {"hit": cache_hit, "namespace_version": namespace_version},
    }


def validate_manifest(payload: Any, operation: str) -> dict[str, Any]:  # noqa: ANN401
    """Reject a missing/malformed/manifest-schema-mismatched result (§7.3)."""
    if not isinstance(payload, dict):
        msg = "Отчёт о результате повреждён."
        raise TypeError(msg)
    if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        msg = "Неподдерживаемая версия отчёта о результате."
        raise ValueError(msg)
    if payload.get("operation") != operation or payload.get("status") != "succeeded":
        msg = "Некорректный отчёт о результате."
        raise ValueError(msg)
    if not isinstance(payload.get("statistics"), dict) or not isinstance(payload.get("coverage"), dict):
        msg = "Отчёт о результате не содержит статистику или покрытие."
        raise TypeError(msg)
    return payload
