"""
DOCX masking/restoration facade (Phase 5).

The real work happens in `masking.ooxml.package.transform_package`: an OPC-aware,
namespace-independent transformer over every text-bearing part. This module
keeps the public signatures used by the sync router and the worker, converting
the transformer's plain entity dicts into the API `MaskedEntity` schema and
building one document-wide `MaskKeys`.
"""

from collections.abc import Callable, Mapping
from pathlib import Path

from .mapping import MaskKeys
from .ooxml.package import (
    CoverageReport,
    OoxmlPackageError,
    transform_package,
    transform_package_to_file,
)
from .rule_based import RuleBasedMasker, get_masker
from .schemas import MaskedEntity

__all__ = [
    "CoverageReport",
    "OoxmlPackageError",
    "mask_docx_in_memory",
    "mask_docx_to_file",
    "unmask_docx_in_memory",
    "unmask_docx_to_file",
]


def mask_docx_in_memory(
    source_bytes: bytes,
    masker: RuleBasedMasker | None = None,
    checkpoint: Callable[[], None] | None = None,
) -> tuple[bytes, list[MaskedEntity], MaskKeys, CoverageReport]:
    """
    Mask PII across the whole OPC package.

    One `MaskKeys` spans every processed part, so placeholder numbering is
    document-wide and deterministic. Unmodified ZIP entries are copied verbatim.
    """
    active = masker or get_masker()
    keys = MaskKeys()
    masked_bytes, raw_entities, _unused, coverage = transform_package(
        source_bytes,
        mode="mask",
        masker=active,
        keys=keys,
        checkpoint=checkpoint,
    )
    entities = [MaskedEntity(**item) for item in raw_entities]  # type: ignore[arg-type]
    return masked_bytes, entities, keys, coverage


def mask_docx_to_file(
    source_path: Path,
    target_path: Path,
    masker: RuleBasedMasker | None = None,
    checkpoint: Callable[[], None] | None = None,
) -> tuple[list[MaskedEntity], MaskKeys, CoverageReport]:
    """Bounded-memory mask: read the source and write the result by path."""
    active = masker or get_masker()
    keys = MaskKeys()
    raw_entities, _unused, coverage = transform_package_to_file(
        source_path,
        target_path,
        mode="mask",
        masker=active,
        keys=keys,
        checkpoint=checkpoint,
    )
    entities = [MaskedEntity(**item) for item in raw_entities]  # type: ignore[arg-type]
    return entities, keys, coverage


def unmask_docx_to_file(
    source_path: Path,
    target_path: Path,
    placeholders: Mapping[str, str],
    checkpoint: Callable[[], None] | None = None,
) -> tuple[list[str], CoverageReport]:
    """Bounded-memory restore: read the source and write the result by path."""
    _entities, unused, coverage = transform_package_to_file(
        source_path,
        target_path,
        mode="unmask",
        placeholders=placeholders,
        checkpoint=checkpoint,
    )
    return unused, coverage


def unmask_docx_in_memory(
    source_bytes: bytes,
    placeholders: Mapping[str, str],
    checkpoint: Callable[[], None] | None = None,
) -> tuple[bytes, list[str], CoverageReport]:
    """
    Restore every placeholder across the whole OPC package.

    Returns (restored bytes, unused key values, coverage). A placeholder split
    across text nodes is collapsed into one value by the transformer.
    """
    restored_bytes, _entities, unused, coverage = transform_package(
        source_bytes,
        mode="unmask",
        placeholders=placeholders,
        checkpoint=checkpoint,
    )
    return restored_bytes, unused, coverage
