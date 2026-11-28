"""Synthetic OPC builders shared by the OOXML test suites.

Every required text-bearing part is built by hand with namespace URIs (including
a default-namespace variant and a custom-prefix variant), so the suites fail if
a supported part is silently skipped. No real PII is used.
"""

import io
import zipfile

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
