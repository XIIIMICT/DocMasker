"""
Bounded windowing for long-text detection (Phase 7, §9.1).

`natasha.AddrExtractor` (yargy) is superlinear on a whole document: the
recorded baseline for ~360 KB was ~113 s and ~10 GB peak RSS just for address
parts. Detection is therefore split into overlapping windows aligned to
structural boundaries (newline, sentence end, semicolon). Every span is
re-based to an absolute offset and boundary duplicates are removed
deterministically before the existing `_merge_spans` overlap policy runs.

Window size/overlap are explicit, validated settings (see `config.py`), tuned
with `benchmarks/profile_detection.py`. Nothing here changes the detectors
themselves — short texts keep the exact old single-pass behavior.
"""

from collections.abc import Callable, Iterator

#: Preferred structural boundaries, in priority order. A window always ends at
#: one of these when one exists inside the last `tail` characters; otherwise it
#: ends at the hard chunk limit (never silently dropping text).
_BOUNDARIES = ("\n", ". ", "! ", "? ", "; ", ", ")


def iter_windows(
    text: str,
    *,
    min_chars: int,
    chunk_chars: int,
    overlap_chars: int,
) -> Iterator[tuple[int, str]]:
    """
    Yield `(absolute_offset, window)` pairs covering `text` exactly once.

    Below `min_chars` a single window `(0, text)` is returned — no behavior
    change for normal inputs. Otherwise each window is at most `chunk_chars`
    long, ends at a structural boundary near the limit when possible, and the
    next window starts `overlap_chars` before that boundary so a candidate
    straddling the cut is fully present in at least one window.
    """
    if len(text) <= min_chars:
        yield 0, text
        return

    start = 0
    length = len(text)
    while start < length:
        hard_end = min(start + chunk_chars, length)
        end = _boundary_end(text, start, hard_end)
        window = text[start:end]
        if window:
            yield start, window
        if end >= length:
            return
        # Overlap backwards from the boundary, but never go before `start`
        # (an overlap of 0 would still be correct for non-straddling spans).
        next_start = max(end - overlap_chars, start + 1)
        start = next_start


def _boundary_end(text: str, start: int, hard_end: int) -> int:
    """Prefer a structural boundary in the tail; never cut through a token."""
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
    last_space = text.rfind(" ", start + 1, hard_end)
    if last_space > start:
        return last_space + 1
    return hard_end


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
