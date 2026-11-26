"""
OPC package inventory and namespace-aware text-node transformer (§6.1–6.4).

No hardcoded namespace prefixes: parts are resolved through
`[Content_Types].xml` and relationship files via QName/URI. XML is parsed with
`defusedxml.lxml` (no external entities, no network). Text nodes are collected
per logical container (`w:p` or a whole metadata/relationship part) in document
order; a detected entity spanning several nodes becomes ONE placeholder. Only
modified ZIP entries are re-serialized — all other entries are copied verbatim.
"""

import io
import shutil
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from defusedxml.lxml import fromstring as safe_fromstring

from src.masking.mapping import MarkerCollisionError, compile_placeholders, marker_tokens

# lxml ships no PEP 561 type information; a dynamic import keeps static type
# checkers quiet while still using lxml's serializer/QName helpers at runtime.
etree = __import__("lxml.etree", fromlist=["etree"])

# --- Namespaces by URI (prefix-independent) ---------------------------------
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
CP = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC = "http://purl.org/dc/elements/1.1/"
DCTERMS = "http://purl.org/dc/terms/"
DCMITYPE = "http://purl.org/dc/dcmitype/"
EP = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
VT = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"

#: Text-bearing element QNames whose .text carries user-visible content.
_TEXT_TAGS: frozenset[str] = frozenset(
    {
        f"{{{W}}}t",
        f"{{{W}}}delText",
        f"{{{A}}}t",
        # Core properties (dc:creator, cp:lastModifiedBy, dc:title, …)
        f"{{{DC}}}creator",
        f"{{{DC}}}title",
        f"{{{DC}}}subject",
        f"{{{DC}}}description",
        f"{{{CP}}}lastModifiedBy",
        f"{{{CP}}}keywords",
        f"{{{CP}}}category",
        f"{{{DCTERMS}}}created",
        f"{{{DCTERMS}}}modified",
        # Extended properties: Company / Manager
        f"{{{EP}}}Company",
        f"{{{EP}}}Manager",
        # docProps/custom.xml user-defined property values.
        f"{{{VT}}}lpwstr",
        f"{{{VT}}}lpstr",
        f"{{{VT}}}bstr",
    }
)

#: Attribute allowlist for drawing alternative text (§6.2 item 10).
_TEXT_ATTRS: frozenset[tuple[str, str]] = frozenset(
    {
        (f"{{{WP}}}docPr", "descr"),
        (f"{{{WP}}}docPr", "title"),
        (f"{{{WP}}}docPr", "name"),
        # Comment/tracked-change authors and initials can identify a person
        # even after the body text is masked (P0.3).
        (f"{{{W}}}comment", f"{{{W}}}author"),
        (f"{{{W}}}comment", f"{{{W}}}initials"),
        (f"{{{W}}}ins", f"{{{W}}}author"),
        (f"{{{W}}}del", f"{{{W}}}author"),
        (f"{{{W}}}moveFrom", f"{{{W}}}author"),
        (f"{{{W}}}moveTo", f"{{{W}}}author"),
        (f"{{{W}}}person", f"{{{W}}}author"),
    }
)

#: Logical paragraph containers: text nodes are grouped by the closest `w:p`.
_PARAGRAPH_TAG = f"{{{W}}}p"
#: Relationship parts: external Target may contain an email/URL (§6.2 item 3).
_RELS_TAG = f"{{{PR}}}Relationship"
_HYPERLINK_TAG = f"{{{W}}}hyperlink"

#: Text-bearing part categories keyed by relationship type URI / name.
_CATEGORY_BY_PART: dict[str, str] = {
    "word/document.xml": "main_document",
    "word/footnotes.xml": "footnotes",
    "word/endnotes.xml": "endnotes",
    "word/comments.xml": "comments",
    "docProps/core.xml": "core_properties",
    "docProps/app.xml": "app_properties",
    "docProps/custom.xml": "custom_properties",
}

_REL_TYPES: dict[str, str] = {
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/header": "headers_footers",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer": "headers_footers",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes": "footnotes",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/endnotes": "endnotes",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments": "comments",
}


_PROCESSABLE_CATEGORIES = frozenset(
    {
        *_CATEGORY_BY_PART.values(),
        *_REL_TYPES.values(),
        "relationships",
        "generic_xml",
    }
)

#: Explicit processor/disposition registry (P0.3). Unknown XML is never
#: silently treated as safe: it is marked unprocessed and the package is
#: rejected instead of returning a misleading "complete" manifest.
_COPY_SAFE_PARTS = frozenset(
    {
        "word/styles.xml",
        "word/stylesWithEffects.xml",
        "word/numbering.xml",
        "word/settings.xml",
        "word/fontTable.xml",
        "word/webSettings.xml",
        "word/theme/theme1.xml",
        "word/commentsExtended.xml",
        "word/commentsIds.xml",
        "word/commentsExtensible.xml",
    }
)
_COPY_SAFE_CONTENT_TYPES = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.fontTable+xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.webSettings+xml",
        "application/vnd.openxmlformats-officedocument.theme+xml",
    }
)
_MEDIA_SUFFIXES = (".png", ".jpeg", ".jpg", ".gif", ".bmp", ".emf", ".wmf")


def _disposition(name: str, ctype: str, rel_category: str) -> str:
    """Classify one part into process / copy / reject semantics."""
    if name.endswith(".rels"):
        return "process"  # external relationship Target values are masked
    if name in _CATEGORY_BY_PART or rel_category in _REL_TYPES.values():
        return "process"
    if name == "[Content_Types].xml":
        return "copy"
    if name.endswith(_MEDIA_SUFFIXES):
        return "copy"
    if name.endswith(".bin") or "/embeddings/" in name:
        return "reject"
    if ctype in _COPY_SAFE_CONTENT_TYPES:
        return "copy"
    if name.endswith((".xml", ".rels")):
        # Chart/drawing text-bearing parts are not in the anonymisation scope:
        # mark them unprocessed and fail closed instead of claiming complete.
        if any(marker in ctype for marker in ("chart+xml", "spreadsheetml")):
            return "unprocessed"
        # Unknown XML (e.g. customXml/item1.xml) is inspected generically:
        # only text nodes are rewritten, never attributes or structure.
        return "process"
    return "copy"  # opaque binary with no embedded-object semantics


class OoxmlPackageError(ValueError):
    """A package is malformed, unsafe or contains unprocessable content."""


@dataclass(frozen=True, slots=True)
class PartCategory:
    name: str
    disposition: str  # "process" | "copy" | "reject"


@dataclass(frozen=True, slots=True)
class ProcessedPart:
    name: str
    category: str
    text_nodes: int


@dataclass(slots=True)
class CoverageReport:
    processed_parts: list[str] = field(default_factory=list)
    processed: list[ProcessedPart] = field(default_factory=list)
    unprocessed_text_parts: list[str] = field(default_factory=list)
    rejected_parts: list[str] = field(default_factory=list)

    def as_manifest(self) -> dict[str, Any]:
        return {
            "processed_parts": sorted(set(self.processed_parts)),
            "processed": [
                {"name": item.name, "category": item.category, "text_nodes": item.text_nodes}
                for item in sorted(self.processed, key=lambda item: item.name)
            ],
            "unprocessed_text_parts": sorted(set(self.unprocessed_text_parts)),
            "rejected_parts": sorted(set(self.rejected_parts)),
            "raster_image_ocr": "not_performed",
        }


@dataclass(frozen=True, slots=True)
class _Part:
    name: str
    #: Only processable parts are materialised; copy-only media stay on disk.
    data: bytes | None
    category: str
    is_xml: bool


def _qname(tag: str, uri: str) -> str:
    return f"{{{uri}}}{tag}"


def _local(tag: str) -> str:
    return etree.QName(tag).localname


def _secure_parse(data: bytes) -> etree._Element:
    """Parse one XML part with external entities disabled and bounded size."""
    if len(data) > 32 * 1024 * 1024:
        msg = "Слишком большая XML-часть документа."
        raise OoxmlPackageError(msg)
    try:
        return safe_fromstring(data, forbid_dtd=True, forbid_entities=True)
    except Exception as error:
        msg = "Документ содержит некорректную или небезопасную XML-часть."
        raise OoxmlPackageError(msg) from error


def _content_types(archive: zipfile.ZipFile) -> tuple[dict[str, str], dict[str, str]]:
    """Part name → content type, resolved by URI (no prefix assumptions)."""
    try:
        root = _secure_parse(archive.read("[Content_Types].xml"))
    except KeyError as error:
        msg = "Пакет DOCX не содержит [Content_Types].xml."
        raise OoxmlPackageError(msg) from error
    result: dict[str, str] = {}
    defaults: dict[str, str] = {}
    for element in root:
        tag = _local(element.tag)
        if tag == "Override":
            name = element.get("PartName", "").lstrip("/")
            ctype = element.get("ContentType", "")
            if name:
                result[name] = ctype
        elif tag == "Default":
            extension = element.get("Extension", "").lower()
            ctype = element.get("ContentType", "")
            if extension:
                defaults[extension] = ctype
    return result, defaults


def _relationships(archive: zipfile.ZipFile, part_name: str) -> list[tuple[str, str, str]]:
    """(Type, Target, TargetMode) triples of one part's .rels, by QName."""
    if part_name == "":
        rels_path = "_rels/.rels"
    else:
        parent, _, leaf = part_name.rpartition("/")
        rels_path = f"{parent}/_rels/{leaf}.rels" if parent else f"_rels/{leaf}.rels"
    try:
        root = _secure_parse(archive.read(rels_path))
    except KeyError:
        return []
    result: list[tuple[str, str, str]] = []
    for element in root:
        if element.tag == _RELS_TAG:
            result.append(
                (
                    element.get("Type", ""),
                    element.get("Target", ""),
                    element.get("TargetMode", ""),
                )
            )
    return result


def _resolve(base: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    parts = (base.rpartition("/")[0] + "/" + target).split("/")
    stack: list[str] = []
    for part in parts:
        if part in ("", "."):
            continue
        if part == "..":
            if stack:
                stack.pop()
        else:
            stack.append(part)
    return "/".join(stack)


def build_inventory(source: bytes | Path) -> tuple[list[_Part], CoverageReport]:
    """
    Deterministic OPC inventory, resolved by URI.

    Content type, relationship type, part name, processor and disposition
    (§6.1). Shared parts are listed exactly once. Only parts that will be
    transformed are read into memory; unchanged media/binary entries are left
    in the archive for a bounded streaming copy.
    """
    coverage = CoverageReport()
    try:
        archive = zipfile.ZipFile(source if isinstance(source, Path) else io.BytesIO(source))
    except zipfile.BadZipFile as error:
        msg = "Файл не является корректным DOCX (повреждённый архив)."
        raise OoxmlPackageError(msg) from error
    with archive:
        names = {info.filename for info in archive.infolist() if not info.is_dir()}
        if "[Content_Types].xml" not in names or "word/document.xml" not in names:
            msg = "Файл не является корректным DOCX (отсутствуют части пакета Word)."
            raise OoxmlPackageError(msg)
        overrides, defaults = _content_types(archive)

        def content_type_of(name: str) -> str:
            if name in overrides:
                return overrides[name]
            return defaults.get(name.rsplit(".", 1)[-1].lower(), "")

        def safe_name(name: str) -> str:
            if (
                not name
                or name.startswith("/")
                or "\\" in name
                or "//" in name
                or ".." in name.split("/")
                or any(segment.startswith(".") and segment != ".rels" for segment in name.split("/"))
            ):
                msg = "Пакет DOCX содержит некорректный путь части."
                raise OoxmlPackageError(msg)
            return name

        by_name: dict[str, tuple[str, str]] = {}
        # Root relationships are resolved from the package root; every part's
        # relationships are resolved relative to that owning part (P0.3).
        rel_sources = ["", *[name for name in sorted(names) if not name.endswith(".rels")]]
        for owner in rel_sources:
            for rel_type, target, mode in _relationships(archive, owner):
                resolved = _resolve(owner, target)
                if resolved in names:
                    by_name[safe_name(resolved)] = (_REL_TYPES.get(rel_type, "other"), mode)
        for name in names:
            if name.startswith("word/") and name.endswith(".xml"):
                by_name.setdefault(name, ("other", ""))
            if name.startswith("docProps/") and name.endswith(".xml"):
                by_name.setdefault(name, ("other", ""))
            if name.endswith(".rels"):
                by_name.setdefault(name, ("relationships", "Internal"))
        by_name.setdefault("word/document.xml", (_CATEGORY_BY_PART["word/document.xml"], ""))

        parts: list[_Part] = []
        for name in sorted(names):
            safe_name(name)
            ctype = content_type_of(name)
            rel_category, _mode = by_name.get(name, ("other", ""))
            disposition = _disposition(name, ctype, rel_category)
            category = _CATEGORY_BY_PART.get(name, rel_category) if disposition == "process" else disposition
            if disposition == "process" and category in ("other", ""):
                category = "generic_xml"
            if disposition == "reject":
                coverage.rejected_parts.append(name)
            elif disposition == "unprocessed":
                coverage.unprocessed_text_parts.append(name)
            is_xml = name.endswith((".xml", ".rels"))
            data = archive.read(name) if disposition == "process" else None
            parts.append(_Part(name, data, category, is_xml=is_xml))
    return parts, coverage


@dataclass(slots=True)
class _TextRef:
    """One editable text slice: an element's .text or an allowed attribute."""

    element: etree._Element
    is_attribute: bool = False
    attr_name: str = ""

    def get(self) -> str:
        if self.is_attribute:
            return self.element.get(self.attr_name) or ""
        return self.element.text or ""

    def set(self, value: str) -> None:
        if self.is_attribute:
            self.element.set(self.attr_name, value)
        else:
            self.element.text = value


@dataclass(slots=True)
class _Container:
    """A logical paragraph (or whole small part) with its ordered text refs."""

    refs: list[_TextRef]
    part_name: str
    category: str

    def text(self) -> str:
        return "".join(ref.get() for ref in self.refs)


def _containers(root: etree._Element, part_name: str, category: str) -> list[_Container]:
    """
    Collect editable text refs grouped by logical container.

    Namespace URIs (not prefixes) drive tag matching, so a document using a
    default namespace or custom prefixes is processed identically. Shared parts
    are listed once by `build_inventory`, so no double processing occurs.
    """
    result: list[_Container] = []
    by_para: dict[int, list[_TextRef]] = {}
    standalone: list[_TextRef] = []

    for element in root.iter():
        tag = element.tag if isinstance(element.tag, str) else ""
        if tag in _TEXT_TAGS and element.text:
            para = _closest_paragraph(element)
            if para is None:
                standalone.append(_TextRef(element))
            else:
                by_para.setdefault(id(para), []).append(_TextRef(element))
        for attr_tag, attr_name in _TEXT_ATTRS:
            if tag == attr_tag and element.get(attr_name):
                para = _closest_paragraph(element)
                ref = _TextRef(element, is_attribute=True, attr_name=attr_name)
                if para is None:
                    standalone.append(ref)
                else:
                    by_para.setdefault(id(para), []).append(ref)
        # Relationship Target attributes: external URLs / mailto: (§6.2 item 3).
        if tag == _RELS_TAG and element.get("TargetMode") == "External":
            value = element.get("Target", "")
            if value:
                standalone.append(_TextRef(element, is_attribute=True, attr_name="Target"))

    # Keep paragraph groups in document order (dict preserves insertion order,
    # which follows root.iter() document order).
    for refs in by_para.values():
        if refs:
            result.append(_Container(refs=refs, part_name=part_name, category=category))
    # Standalone refs are INDEPENDENT values (docProps fields, external
    # relationship targets, paragraph-less text). Concatenating them into one
    # container would create false entities that span field boundaries — e.g.
    # dc:creator «Иван Петров» + cp:lastModifiedBy «Иван Петров» becomes
    # «Иван ПетровИван», producing a nonsensical key. One ref = one container.
    for ref in standalone:
        result.append(_Container(refs=[ref], part_name=part_name, category=category))
    return result


def _generic_containers(root: etree._Element, part_name: str, category: str) -> list[_Container]:
    """
    Text nodes of an unknown XML part (e.g. customXml/item1.xml).

    Only element text is touched — attributes and structure are preserved.
    """
    refs = [
        _TextRef(element)
        for element in root.iter()
        if isinstance(element.tag, str) and element.text and element.text.strip()
    ]
    return [_Container(refs=refs, part_name=part_name, category=category)] if refs else []


def _source_marker_tokens(parts: list[_Part]) -> frozenset[str]:
    """
    Collect marker-shaped literals from every processable part before masking.

    This only inspects already-loaded XML parts and keeps marker strings, not a
    second copy of the document. It lets the mask path reject a source literal
    that collides with any marker allocated anywhere in the package, without
    running detection twice or buffering whole documents in memory.
    """
    found: set[str] = set()
    for part in parts:
        if part.data is None or part.category not in _PROCESSABLE_CATEGORIES:
            continue
        root = _secure_parse(part.data)
        containers = (
            _generic_containers(root, part.name, part.category)
            if part.category == "generic_xml"
            else _containers(root, part.name, part.category)
        )
        for container in containers:
            found.update(marker_tokens(container.text()))
    return frozenset(found)


def _closest_paragraph(element: etree._Element) -> etree._Element | None:
    current = element.getparent()
    while current is not None:
        if current.tag == _PARAGRAPH_TAG:
            return current
        current = current.getparent()
    return None


def _rewrite_container(container: _Container, spans: list[tuple[int, int, str]]) -> None:
    """
    Rewrite text refs back-to-front so earlier offsets stay valid.

    `spans` are (start, end, replacement) over the concatenated container text.
    A span covering several refs emits the replacement once into the ref holding
    its start and removes only the covered slices from continuation refs.
    """
    for start, end, replacement in sorted(spans, reverse=True):
        bounds: list[tuple[int, int]] = []
        cursor = 0
        for ref in container.refs:
            length = len(ref.get())
            bounds.append((cursor, cursor + length))
            cursor += length
        for index, (ref_start, ref_end) in enumerate(bounds):
            if start >= ref_end or end <= ref_start:
                continue
            left = max(start, ref_start) - ref_start
            right = min(end, ref_end) - ref_start
            ref = container.refs[index]
            value = ref.get()
            if start >= ref_start and start < ref_end:
                ref.set(value[:left] + replacement + value[right:])
            else:
                ref.set(value[:left] + value[right:])


def _transform_part(
    part: _Part,
    *,
    mode: str,
    masker: Any,  # noqa: ANN401
    keys: Any,  # noqa: ANN401
    placeholders: Any,  # noqa: ANN401
    source_markers: frozenset[str],
    checkpoint: Callable[[], None] | None,
    coverage: CoverageReport,
    all_entities: list[Any],
    used_placeholders: set[str],
) -> bytes | None:
    """Transform one processable part; return its serialised bytes if changed."""
    if checkpoint is not None:
        checkpoint()  # coarse boundary: before parsing/detecting a part
    data = part.data
    if data is None:
        msg = "Внутренняя ошибка: часть документа не была загружена для обработки."
        raise OoxmlPackageError(msg)
    root = _secure_parse(data)
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
    pattern = compile_placeholders(placeholders) if mode == "unmask" else None
    for container in containers:
        text = container.text()
        if not text.strip():
            continue
        local: list[tuple[int, int, str]] = []
        if mode == "mask":
            for item in masker.detect(text, prefilter=True, checkpoint=checkpoint):
                marker = keys.placeholder(item.label, item.text)
                if marker in source_markers:
                    raise MarkerCollisionError([marker])
                local.append((item.start, item.end, marker))
                all_entities.append(
                    {
                        "label": item.label,
                        "text": item.text,
                        "start": item.start,
                        "end": item.end,
                        "score": item.score,
                    }
                )
        elif pattern is not None:
            # One non-cascading scan per logical container: inserted values are
            # written afterwards by `_rewrite_container` and are never rescanned.
            for match in pattern.finditer(text):
                placeholder = match.group(0)
                local.append((match.start(), match.end(), placeholders[placeholder]))
                used_placeholders.add(placeholder)
        if local:
            _rewrite_container(container, local)
            modified = True
    if modified:
        return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    return None


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
    source_markers = _source_marker_tokens(parts) if mode == "mask" else frozenset()

    with zipfile.ZipFile(io.BytesIO(data)) as source:
        infos = {info.filename: info for info in source.infolist() if not info.is_dir()}
        edited: dict[str, bytes] = {}
        all_entities: list[Any] = []
        used_placeholders: set[str] = set()

        for part in parts:
            if part.category not in _PROCESSABLE_CATEGORIES:
                continue
            result = _transform_part(
                part,
                mode=mode,
                masker=masker,
                keys=keys,
                placeholders=placeholders,
                source_markers=source_markers,
                checkpoint=checkpoint,
                coverage=coverage,
                all_entities=all_entities,
                used_placeholders=used_placeholders,
            )
            if result is not None:
                edited[part.name] = result

        unused = (
            [value for placeholder, value in placeholders.items() if placeholder not in used_placeholders]
            if mode == "unmask"
            else []
        )

        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
            for name in sorted(infos):
                info = infos[name]
                payload = edited.get(name, source.read(name))
                # Preserve the original ZipInfo (timestamps, external attrs,
                # compression type) so unchanged entries stay semantically equal.
                clone = zipfile.ZipInfo(name, date_time=info.date_time)
                clone.compress_type = info.compress_type
                clone.external_attr = info.external_attr
                clone.internal_attr = info.internal_attr
                clone.create_system = info.create_system
                target.writestr(clone, payload)
        return out.getvalue(), all_entities, unused, coverage


def transform_package_to_file(
    source: Path,
    target: Path,
    *,
    mode: str,
    masker: Any = None,  # noqa: ANN401
    keys: Any = None,  # noqa: ANN401
    placeholders: Any = None,  # noqa: ANN401
    checkpoint: Callable[[], None] | None = None,
) -> tuple[list[Any], list[str], CoverageReport]:
    """
    Bounded-memory transform: one XML part at a time, streamed copy for the rest.

    Unchanged media/binary entries are never decompressed into `bytes`, and the
    output DOCX is written straight to `target`. Only the currently processed
    XML part's tree and its serialised bytes are resident at any moment.
    """
    parts, coverage = build_inventory(source)
    if coverage.rejected_parts or coverage.unprocessed_text_parts:
        msg = "Документ содержит неподдерживаемое содержимое, которое нельзя безопасно обработать."
        raise OoxmlPackageError(msg)
    source_markers = _source_marker_tokens(parts) if mode == "mask" else frozenset()

    processable = {part.name: part for part in parts if part.category in _PROCESSABLE_CATEGORIES}
    all_entities: list[Any] = []
    used_placeholders: set[str] = set()
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as output:
        for info in sorted(original.infolist(), key=lambda item: item.filename):
            if info.is_dir():
                continue
            part = processable.get(info.filename)
            payload = (
                _transform_part(
                    part,
                    mode=mode,
                    masker=masker,
                    keys=keys,
                    placeholders=placeholders,
                    source_markers=source_markers,
                    checkpoint=checkpoint,
                    coverage=coverage,
                    all_entities=all_entities,
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
    unused = (
        [value for placeholder, value in placeholders.items() if placeholder not in used_placeholders]
        if mode == "unmask"
        else []
    )
    return all_entities, unused, coverage
