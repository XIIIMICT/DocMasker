"""One archive builder + manifest serializer shared by sync and worker (§7.1)."""

import io
import json
import zipfile
from typing import Any

from .ooxml.manifest import MANIFEST_NAME

_ZIP_DEFLATED = zipfile.ZIP_DEFLATED


def _write_archive(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", _ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def mask_archive(
    masked_docx: bytes,
    keys_yaml: str,
    base: str,
    manifest: dict[str, Any],
) -> bytes:
    """Flat mask ZIP: masked .docx + keys .yaml + versioned manifest.json."""
    return _write_archive(
        {
            f"{base} (замаскированный).docx": masked_docx,
            f"{base} (ключи демаскировки).yaml": keys_yaml.encode("utf-8"),
            MANIFEST_NAME: json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        }
    )


def restore_archive(
    restored_docx: bytes,
    base: str,
    manifest: dict[str, Any],
) -> bytes:
    """Flat restore ZIP: restored .docx + versioned manifest.json (with unused_keys)."""
    return _write_archive(
        {
            f"{base} (демаскированный).docx": restored_docx,
            MANIFEST_NAME: json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        }
    )
