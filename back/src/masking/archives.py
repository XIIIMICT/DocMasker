"""One archive builder + manifest serializer shared by sync and job (§7.1)."""

import io
import json
import re
import unicodedata
import zipfile
from pathlib import Path
from typing import Any

from .ooxml.manifest import MANIFEST_NAME

_ZIP_DEFLATED = zipfile.ZIP_DEFLATED
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_stem(filename: str) -> str:
    """
    Normalise a user-supplied filename into a safe flat ZIP entry stem.

    Both POSIX and Windows separators/drive prefixes are discarded regardless
    of the host OS; control/unsafe characters, reserved names and excessive
    lengths are handled. Safe Unicode names are preserved.
    """
    name = str(filename).replace("\\", "/").rsplit("/", 1)[-1]
    name = name.rsplit(":", 1)[-1]
    name = "".join(char for char in name if unicodedata.category(char)[0] != "C")
    stem = Path(name).stem.strip(" .")
    if not stem or stem in (".", "..") or stem.upper() in _RESERVED:
        stem = "document"
    stem = _UNSAFE.sub("_", stem).strip(" .") or "document"
    return stem[:120]


def _write_archive(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", _ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            if "/" in name or "\\" in name or not name:
                msg = "Некорректное имя файла результата."
                raise ValueError(msg)
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
    """Flat restore ZIP: restored .docx + manifest.json (with unused_keys)."""
    return _write_archive(
        {
            f"{base} (демаскированный).docx": restored_docx,
            MANIFEST_NAME: json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        }
    )
