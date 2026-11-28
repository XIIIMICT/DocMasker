"""
OPC package mask/restore transform (§6.1–6.4).

Inventory and URI-keyed classification live in `inventory.py`; editable text
containers live in `containers.py`. This module owns the single-pass transform
of each processable part and the two package APIs: `transform_package` (bytes,
with occurrence detail) and `transform_package_to_file` (bounded, streamed copy
of unchanged entries returning aggregate `EntityStats`). Every name formerly
defined here is re-exported so existing callers keep working unchanged.
"""

import io
import os
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from defusedxml.lxml import fromstring as safe_fromstring

from src.masking.mapping import MarkerCollisionError, compile_placeholders, marker_tokens

from .containers import (
    _closest_paragraph,
    _Container,
    _containers,
    _generic_containers,
    _rewrite_container,
    _TextRef,
)
from .inventory import (
    _CATEGORY_BY_PART,
    _COPY_SAFE_CONTENT_TYPES,
    _COPY_SAFE_PARTS,
    _HYPERLINK_TAG,
    _MEDIA_SUFFIXES,
    _PARAGRAPH_TAG,
    _PROCESSABLE_CATEGORIES,
    _REL_TYPES,
    _RELS_TAG,
    _TEXT_ATTRS,
    _TEXT_TAGS,
    CP,
    CT,
    DC,
    DCMITYPE,
    DCTERMS,
    EP,
    PR,
    VT,
    WP,
    A,
    CoverageReport,
    OoxmlPackageError,
    PartCategory,
    ProcessedPart,
    R,
    W,
    _content_types,
    _disposition,
    _local,
    _Part,
    _qname,
    _read_part,
    _relationships,
    _resolve,
    _secure_parse,
    build_inventory,
)

# lxml ships no PEP 561 type information; a dynamic import keeps static type
# checkers quiet while still using lxml's serializer/QName helpers at runtime.
etree = __import__("lxml.etree", fromlist=["etree"])

__all__ = [
    "CP",
    "CT",
    "DC",
    "DCMITYPE",
    "DCTERMS",
    "EP",
    "PR",
    "VT",
    "WP",
    "_CATEGORY_BY_PART",
    "_COPY_SAFE_CONTENT_TYPES",
    "_COPY_SAFE_PARTS",
    "_HYPERLINK_TAG",
    "_MEDIA_SUFFIXES",
    "_PARAGRAPH_TAG",
    "_PROCESSABLE_CATEGORIES",
    "_RELS_TAG",
    "_REL_TYPES",
    "_TEXT_ATTRS",
    "_TEXT_TAGS",
    "A",
    "CoverageReport",
    "EntityStats",
    "MarkerCollisionError",
    "OoxmlPackageError",
    "PartCategory",
    "ProcessedPart",
    "R",
    "W",
    "_Container",
    "_Part",
    "_TextRef",
    "_closest_paragraph",
    "_containers",
    "_content_types",
    "_copy_zip_info",
    "_disposition",
    "_fail_on_marker_collision",
    "_generic_containers",
    "_local",
    "_qname",
    "_read_part",
    "_relationships",
    "_resolve",
    "_rewrite_container",
    "_secure_parse",
    "_temp_transform_path",
    "_transform_part",
    "build_inventory",
    "compile_placeholders",
    "etree",
    "marker_tokens",
    "safe_fromstring",
    "transform_package",
    "transform_package_to_file",
]


@dataclass(slots=True)
class EntityStats:
    """
    O(labels + distinct values) aggregate in place of a per-occurrence list.

    `key_number` comes from `MaskKeys`, which already deduplicates by exact
    (label, text), so the maximum number seen per label is the distinct count —
    no second copy of the original values is needed on the job path.
    """

    total: int = 0
    occurrences_by_label: dict[str, int] = field(default_factory=dict)
    unique_by_label: dict[str, int] = field(default_factory=dict)
    _seen: dict[str, set[str]] = field(default_factory=dict)

    def add(self, label: str, text: str, *, key_number: int | None = None) -> None:
        """Record one occurrence without retaining occurrence-level detail."""
        self.total += 1
        self.occurrences_by_label[label] = self.occurrences_by_label.get(label, 0) + 1
        if key_number is not None:
            if key_number > self.unique_by_label.get(label, 0):
                self.unique_by_label[label] = key_number
            return
        values = self._seen.setdefault(label, set())
        if text not in values:
            values.add(text)
            self.unique_by_label[label] = len(values)

    def as_manifest_stats(self) -> dict[str, Any]:
        """Return the versioned `statistics` block consumed by `manifest.json`."""
        return {
            "total_occurrences": self.total,
            "occurrences_by_label": dict(self.occurrences_by_label),
            "unique_by_label": dict(self.unique_by_label),
        }


def _transform_part(
    part: _Part,
    archive: zipfile.ZipFile,
    *,
    mode: str,
    masker: Any,  # noqa: ANN401
    keys: Any,  # noqa: ANN401
    placeholders: Any,  # noqa: ANN401
    pattern: Any,  # noqa: ANN401 - compiled once per transform, not per part
    checkpoint: Callable[[], None] | None,
    coverage: CoverageReport,
    stats: EntityStats,
    detail: list[dict[str, Any]] | None,
    source_markers: set[str],
    allocated_markers: set[str],
    used_placeholders: set[str],
) -> bytes | None:
    """
    Transform one processable part; return its serialised bytes if changed.

    The payload is decompressed here, parsed once and released on return; the
    inventory keeps no XML bytes (DM-3). Marker-shaped source literals are
    collected in the same pass, before any rewrite, so collision detection needs
    no second parse of the package.
    """
    if checkpoint is not None:
        checkpoint()  # coarse boundary: before parsing/detecting a part
    data = _read_part(archive, part.name)
    root = _secure_parse(data)
    del data  # only the tree stays resident until this part is done
    containers = (
        _generic_containers(root, part.name, part.category)
        if part.category == "generic_xml"
        else _containers(root, part.name, part.category)
    )
    text_nodes = sum(len(container.refs) for container in containers)
    # Every inspected supported part is recorded even with zero detections.
    coverage.processed_parts.append(part.category)
    coverage.processed.append(ProcessedPart(part.name, part.category, text_nodes))
    modified = False
    for container in containers:
        text = container.text()
        if not text.strip():
            continue
        local: list[tuple[int, int, str]] = []
        if mode == "mask":
            source_markers.update(marker_tokens(text))
            for item in masker.detect(text, prefilter=True, checkpoint=checkpoint):
                number = keys.number(item.label, item.text)
                marker = keys.placeholder(item.label, item.text)
                allocated_markers.add(marker)
                local.append((item.start, item.end, marker))
                stats.add(item.label, item.text, key_number=number)
                if detail is not None:
                    detail.append(
                        {
                            "label": item.label,
                            "text": item.text,
                            "start": item.start,
                            "end": item.end,
                            "score": item.score,
                        }
                    )
        elif pattern is not None:
            # One bounded non-cascading scan per logical container: inserted
            # values are written afterwards by `_rewrite_container` and are
            # never rescanned, even when split across runs or re-applied.
            for match in pattern.finditer(text):
                placeholder = match.group(0)
                local.append((match.start(), match.end(), placeholders[placeholder]))
                used_placeholders.add(placeholder)
        if local:
            _rewrite_container(container, local)
            modified = True
    result = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True) if modified else None
    del root, containers  # release this part's tree before the next one is read
    return result


def _fail_on_marker_collision(source_markers: set[str], allocated_markers: set[str]) -> None:
    """Fail closed after the single pass when a source literal equals a marker."""
    collisions = sorted(source_markers & allocated_markers)
    if collisions:
        raise MarkerCollisionError(collisions)


def _copy_zip_info(info: zipfile.ZipInfo) -> zipfile.ZipInfo:
    """Preserve metadata so unchanged entries stay semantically equal."""
    clone = zipfile.ZipInfo(info.filename, date_time=info.date_time)
    clone.compress_type = info.compress_type
    clone.external_attr = info.external_attr
    clone.internal_attr = info.internal_attr
    clone.create_system = info.create_system
    return clone


def transform_package(
    data: bytes,
    *,
    mode: str,
    masker: Any = None,  # noqa: ANN401
    keys: Any = None,  # noqa: ANN401
    placeholders: Any = None,  # noqa: ANN401
    checkpoint: Callable[[], None] | None = None,
) -> tuple[bytes, list[Any], list[str], CoverageReport]:
    """
    Mask or restore every supported text-bearing part.

    Other ZIP entries are copied byte-for-byte. `checkpoint` is called before
    each expensive OPC part and forwarded to long-text window detection, so a
    worker cancellation stops work promptly. Raises `OoxmlPackageError` for
    unsafe/uninspectable content.
    """
    parts, coverage = build_inventory(data)
    if coverage.rejected_parts or coverage.unprocessed_text_parts:
        msg = "Документ содержит неподдерживаемое содержимое, которое нельзя безопасно обработать."
        raise OoxmlPackageError(msg)
    pattern = compile_placeholders(placeholders) if mode == "unmask" else None
    source_markers: set[str] = set()
    allocated_markers: set[str] = set()
    stats = EntityStats()
    # The bytes API still returns occurrence detail to its callers (§6.6); only
    # the bounded file path drops it in favour of `stats`.
    all_entities: list[dict[str, Any]] = []
    used_placeholders: set[str] = set()

    with zipfile.ZipFile(io.BytesIO(data)) as source:
        infos = {info.filename: info for info in source.infolist() if not info.is_dir()}
        edited: dict[str, bytes] = {}

        for part in parts:
            if part.category not in _PROCESSABLE_CATEGORIES:
                continue
            result = _transform_part(
                part,
                source,
                mode=mode,
                masker=masker,
                keys=keys,
                placeholders=placeholders,
                pattern=pattern,
                checkpoint=checkpoint,
                coverage=coverage,
                stats=stats,
                detail=all_entities,
                source_markers=source_markers,
                allocated_markers=allocated_markers,
                used_placeholders=used_placeholders,
            )
            if result is not None:
                edited[part.name] = result
        if mode == "mask":
            _fail_on_marker_collision(source_markers, allocated_markers)

        unused = (
            [value for placeholder, value in placeholders.items() if placeholder not in used_placeholders]
            if mode == "unmask"
            else []
        )

        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
            for name in sorted(infos):
                info = infos[name]
                # Do not decompress an entry that was already rewritten.
                payload = edited[name] if name in edited else source.read(name)
                # Preserve the original ZipInfo (timestamps, external attrs,
                # compression type) so unchanged entries stay semantically equal.
                clone = zipfile.ZipInfo(name, date_time=info.date_time)
                clone.compress_type = info.compress_type
                clone.external_attr = info.external_attr
                clone.internal_attr = info.internal_attr
                clone.create_system = info.create_system
                target.writestr(clone, payload)
        return out.getvalue(), all_entities, unused, coverage


def _temp_transform_path(target: Path) -> Path:
    """Create a free sibling temp path for a streamed transform."""
    handle, name = tempfile.mkstemp(prefix=".dm-transform-", suffix=".tmp", dir=str(target.parent))
    os.close(handle)
    Path(name).unlink()
    return Path(name)


def transform_package_to_file(
    source: Path,
    target: Path,
    *,
    mode: str,
    masker: Any = None,  # noqa: ANN401
    keys: Any = None,  # noqa: ANN401
    placeholders: Any = None,  # noqa: ANN401
    checkpoint: Callable[[], None] | None = None,
) -> tuple[EntityStats, list[str], CoverageReport]:
    """
    Bounded-memory transform: one XML part at a time, streamed copy for the rest.

    Unchanged media/binary entries are never decompressed into `bytes`. Only
    the currently processed XML part's tree and its serialised bytes are
    resident at any moment; the caller gets aggregate `EntityStats`, never an
    occurrence-level list.

    Output is written to a unique sibling temp file and published to `target`
    with an atomic `Path.replace` only after the whole package was transformed
    and checked.
    A collision, cancellation, parse/serialization error or any other failure
    removes the temp file and leaves a pre-existing `target` untouched, so no
    partial DOCX can be downloaded or cached.
    """
    parts, coverage = build_inventory(source)
    if coverage.rejected_parts or coverage.unprocessed_text_parts:
        msg = "Документ содержит неподдерживаемое содержимое, которое нельзя безопасно обработать."
        raise OoxmlPackageError(msg)
    pattern = compile_placeholders(placeholders) if mode == "unmask" else None
    processable = {part.name: part for part in parts if part.category in _PROCESSABLE_CATEGORIES}
    stats = EntityStats()
    source_markers: set[str] = set()
    allocated_markers: set[str] = set()
    used_placeholders: set[str] = set()
    temp_path = _temp_transform_path(target)
    try:
        with zipfile.ZipFile(source) as original, zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED) as output:
            for info in sorted(original.infolist(), key=lambda item: item.filename):
                if info.is_dir():
                    continue
                part = processable.get(info.filename)
                payload = (
                    _transform_part(
                        part,
                        original,
                        mode=mode,
                        masker=masker,
                        keys=keys,
                        placeholders=placeholders,
                        pattern=pattern,
                        checkpoint=checkpoint,
                        coverage=coverage,
                        stats=stats,
                        detail=None,
                        source_markers=source_markers,
                        allocated_markers=allocated_markers,
                        used_placeholders=used_placeholders,
                    )
                    if part is not None
                    else None
                )
                if payload is not None:
                    output.writestr(_copy_zip_info(info), payload)
                    continue
                # Byte-for-byte semantics without materialising the entry.
                with original.open(info) as src, output.open(_copy_zip_info(info), "w") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
        if mode == "mask":
            _fail_on_marker_collision(source_markers, allocated_markers)
        unused = (
            [value for placeholder, value in placeholders.items() if placeholder not in used_placeholders]
            if mode == "unmask"
            else []
        )
        temp_path.replace(target)
    except BaseException:
        with suppress(OSError):
            temp_path.unlink()
        raise
    return stats, unused, coverage
