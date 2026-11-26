"""
Bounded windowing for long-text detection (Phase 7, §9.1; DM-5).

`natasha.AddrExtractor` (yargy) is superlinear on a whole document: the
recorded baseline for ~360 KB was ~113 s and ~10 GB peak RSS just for address
parts. Detection is therefore split into overlapping windows aligned to
structural boundaries (newline, sentence end, semicolon). Every span is
re-based to an absolute offset and boundary duplicates are removed
deterministically before the existing `_merge_spans` overlap policy runs.

Window size/overlap are explicit, validated settings (see `config.py`). DM-5
adds a hard ceiling (`DETECTION_MAX_WINDOW_CHARS`): when a chunk contains no
token boundary at all, the window extends forward to the next boundary but
never past the ceiling. A longer unbroken run is rejected with a clear 422
instead of being cut in half. Nothing here changes the detectors themselves —
short texts keep the exact old single-pass behavior.
"""

from collections.abc import Callable, Iterator

from fastapi import status

from src.config import get_settings
from src.errors import ApiError

#: Preferred structural boundaries, in priority order. A window always ends at
#: one of these when one exists inside the last `tail` characters; otherwise it
#: ends at the last whitespace, or at the next whitespace after the hard chunk
#: limit (bounded by `max_window_chars`), never in the middle of a token.
_BOUNDARIES = ("\n", ". ", "! ", "? ", "; ", ", ")


class OversizedTokenError(ApiError):
    """An unbroken token does not fit inside the hard window ceiling."""

    def __init__(self, limit: int) -> None:
        detail = (
            "Текст содержит непрерывную последовательность длиннее "
            f"DETECTION_MAX_WINDOW_CHARS ({limit}). Маскировка прервана, чтобы не разрезать "
            "сущность. Добавьте разделители в текст или увеличьте DETECTION_MAX_WINDOW_CHARS."
        )
        super().__init__("oversized_token", detail, status.HTTP_422_UNPROCESSABLE_CONTENT)


def iter_windows(
    text: str,
    *,
    min_chars: int,
    chunk_chars: int,
    overlap_chars: int,
    max_window_chars: int | None = None,
) -> Iterator[tuple[int, str]]:
    """
    Yield `(absolute_offset, window)` pairs covering `text` exactly once.

    Below `min_chars` a single window `(0, text)` is returned — no behavior
    change for normal inputs. Otherwise each window is at most `chunk_chars`
    long (up to `max_window_chars` when no token boundary fits), ends at a
    structural boundary or whitespace, and the next window starts
    `overlap_chars` before that boundary so a candidate straddling the cut is
    fully present in at least one window. An unbroken run longer than
    `max_window_chars` raises `OversizedTokenError` rather than being split.

    `max_window_chars` defaults to the validated setting so callers that only
    know the chunk/overlap pair (e.g. `rule_based._detect_long`) still use the
    configured hard ceiling.
    """
    if max_window_chars is None:
        max_window_chars = get_settings().detection_max_window_chars
    if max_window_chars < 1:
        msg = "max_window_chars must be positive"
        raise ValueError(msg)

    if len(text) <= min_chars:
        yield 0, text
        return

    start = 0
    length = len(text)
    while start < length:
        hard_end = min(start + chunk_chars, length)
        end = _boundary_end(text, start, hard_end, max_window_chars)
        window = text[start:end]
        if window:
            yield start, window
        if end >= length:
            return
        # Overlap backwards from the boundary, but never go before `start`
        # (an overlap of 0 would still be correct for non-straddling spans).
        next_start = max(end - overlap_chars, start + 1)
        start = next_start


def _boundary_end(text: str, start: int, hard_end: int, max_window_chars: int) -> int:
    """Pick a token boundary at/near the chunk end; extend forward if needed."""
    if hard_end >= len(text):
        return len(text)
    search_from = start + max(1, (hard_end - start) // 2)
    tail = text[search_from:hard_end]
    best = -1
    for marker in _BOUNDARIES:
        position = tail.rfind(marker)
        if position >= 0:
            best = max(best, search_from + position + len(marker))
    if best > start:
        return best
    # No sentence boundary: roll back to the last whitespace in the window so
    # an email/URL/person name is never split in half at the cut (§9.1). This
    # also bounds the overlap needed on the next window.
    last_space = _last_whitespace_end(text, start + 1, hard_end)
    if last_space > start:
        return last_space
    # The whole chunk is one unbroken token (DM-5). Scan forward, but only as
    # far as the explicit ceiling: the first whitespace ends that token and
    # keeps it whole in this window. Beyond the ceiling, fail closed — cutting
    # or dropping the entity is worse than rejecting an unsupported input.
    ceiling = min(max(start + max_window_chars, hard_end), len(text))
    for position in range(hard_end, ceiling):
        if text[position].isspace():
            return position + 1
    if ceiling == len(text):
        return ceiling
    raise OversizedTokenError(max_window_chars)


def _last_whitespace_end(text: str, search_from: int, hard_end: int) -> int:
    """Return the position just after the last whitespace in `[search_from, hard_end)`."""
    for position in range(hard_end - 1, search_from - 1, -1):
        if text[position].isspace():
            return position + 1
    return -1


def dedupe_boundary_spans[T](entities: list[T], key: Callable[[T], tuple[object, ...]]) -> list[T]:
    """
    Drop exact duplicate spans produced by overlapping windows.

    Deterministic: keeps the first occurrence in document order (windows are
    generated left-to-right, and spans within a window are sorted by offset by
    the caller). `key` is `(label, start, end, text)` for entities.
    """
    seen: set[tuple[object, ...]] = set()
    result: list[T] = []
    for entity in entities:
        identity = key(entity)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(entity)
    return result
