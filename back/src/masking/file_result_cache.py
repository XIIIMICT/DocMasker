"""Serialization of cached synchronous file results (ZIP plus response headers)."""

import base64
import json
import re
from urllib.parse import quote

from src.jobs.cache import canonical_json

_ZIP_CONTENT_TYPE = "application/zip"


def _ascii_attachment_name(name: str) -> str:
    """Fallback filename for `filename=` (must be ASCII/latin-1 safe)."""
    ascii_name = re.sub(r"[^A-Za-z0-9_.()-]", "_", name)
    return ascii_name or "masked.docx"


def _content_disposition(download_name: str) -> str:
    """RFC 5987 attachment header (ASCII fallback + UTF-8 name for Cyrillic)."""
    return f"attachment; filename=\"{_ascii_attachment_name(download_name)}\"; filename*=UTF-8''{quote(download_name)}"


def _download_headers(download_name: str) -> dict[str, str]:
    """Transport-only headers: semantic metadata lives in `manifest.json` (§7.3)."""
    return {
        "Content-Disposition": _content_disposition(download_name),
        "Content-Type": _ZIP_CONTENT_TYPE,
        "Cache-Control": "no-store",
    }


def _pack_file_result(archive: bytes, headers: dict[str, str]) -> bytes:
    """Cache the ZIP plus its non-semantic response headers as one artifact."""
    return canonical_json(
        {
            "archive_b64": base64.b64encode(archive).decode("ascii"),
            "headers": headers,
        }
    ).encode("utf-8")


def _unpack_file_result(cached: bytes) -> tuple[bytes, dict[str, str]] | None:
    try:
        payload = json.loads(cached)
        return base64.b64decode(payload["archive_b64"]), dict(payload["headers"])
    except Exception:
        return None
