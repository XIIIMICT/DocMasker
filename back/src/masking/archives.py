"""Archive builders shared by the sync router and the worker (ADR-0002)."""

import io
import json
import zipfile

_ZIP_DEFLATED = zipfile.ZIP_DEFLATED


def mask_archive(masked_docx: bytes, keys_yaml: str, base: str) -> bytes:
    """Bundle the masked document and its keys into one ZIP."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", _ZIP_DEFLATED) as archive:
        archive.writestr(f"{base} (замаскированный).docx", masked_docx)
        archive.writestr(f"{base} (ключи демаскировки).yaml", keys_yaml)
    return buffer.getvalue()


def restore_archive(restored_docx: bytes, unused_keys: list[str], base: str) -> bytes:
    """Bundle the restored document and a manifest of unapplied keys into one ZIP."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", _ZIP_DEFLATED) as archive:
        archive.writestr(f"{base} (демаскированный).docx", restored_docx)
        archive.writestr(
            f"{base} (отчёт о демаскировке).json",
            json.dumps({"unused_keys": unused_keys}, ensure_ascii=False, indent=2),
        )
    return buffer.getvalue()
