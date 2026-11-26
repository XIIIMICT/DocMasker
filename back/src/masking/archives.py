"""One archive builder + manifest serializer shared by sync and job (§7.1)."""

import contextlib
import io
import json
import os
import re
import shutil
import tempfile
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


def write_archive_to_path(target: Path, entries: dict[str, bytes | Path]) -> None:
    """Write a flat result ZIP to a file; file-valued entries are streamed."""
    with zipfile.ZipFile(target, "w", _ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            if "/" in name or "\\" in name or not name:
                msg = "Некорректное имя файла результата."
                raise ValueError(msg)
            if isinstance(payload, Path):
                with payload.open("rb") as source, archive.open(name, "w") as sink:
                    shutil.copyfileobj(source, sink, length=1024 * 1024)
            else:
                archive.writestr(name, payload)


def mark_cache_hit_file(path: Path) -> None:
    """Rewrite one result ZIP on disk so its manifest reports the current hit."""
    handle, name = tempfile.mkstemp(prefix=".cache-hit-", dir=str(path.parent))
    os.close(handle)
    tmp = Path(name)
    try:
        with zipfile.ZipFile(path) as source, zipfile.ZipFile(tmp, "w", _ZIP_DEFLATED) as target:
            for info in source.infolist():
                clone = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                clone.compress_type = info.compress_type
                clone.external_attr = info.external_attr
                if info.filename == MANIFEST_NAME:
                    manifest = json.loads(source.read(info))
                    manifest["cache"]["hit"] = True
                    target.writestr(clone, json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
                else:
                    with source.open(info) as reader, target.open(clone, "w") as writer:
                        shutil.copyfileobj(reader, writer, length=1024 * 1024)
        tmp.replace(path)
    finally:
        with contextlib.suppress(OSError):
            if tmp.exists():
                tmp.unlink()


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


def mark_cache_hit(archive: bytes) -> bytes:
    """
    Return a new archive whose manifest describes the *current* cache hit.

    The cached artifact is immutable and shared; this never edits it in place.
    A malformed manifest degrades to the original bytes rather than failing a
    verified cache hit.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as source:
            entries = {name: source.read(name) for name in source.namelist()}
        manifest = json.loads(entries[MANIFEST_NAME])
        manifest["cache"]["hit"] = True
        entries[MANIFEST_NAME] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", _ZIP_DEFLATED) as target:
            for name, payload in entries.items():
                target.writestr(name, payload)
        return buffer.getvalue()
    except Exception:
        # A verified hit stays a hit even when cached metadata is unexpected.
        return archive


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
