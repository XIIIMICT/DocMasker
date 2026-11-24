"""
Phase 7 §9.1: profile full detect() and (optionally) raw natasha extractors.

Run before and after performance changes; record numbers in
`docs/PERFORMANCE.md` / `docs/PRODUCTION_READINESS.md`.

By default this measures ONLY `masker.detect()` so peak RSS reflects the real
pipeline (the raw `AddrExtractor(text)` diagnostic allocates thousands of
`AddrPart` objects and would otherwise dominate the RSS figure). Use `--raw`
to additionally profile the two natasha extractors on the whole text.
"""

import sys
import time

from benchmarks.synthetic import build_text
from benchmarks.util import rss_mb
from src.masking.rule_based import get_masker

SIZES_KB = (4, 36, 360)


def _sample(size_kb: int) -> str:
    """Deterministic prefix of the synthetic corpus of ~size_kb KiB (bytes)."""
    text = build_text(20_000)
    raw = text.encode("utf-8")[: size_kb * 1024]
    # Never cut a multi-byte UTF-8 sequence in half.
    return raw.decode("utf-8", errors="ignore")


def main() -> None:
    args = sys.argv[1:]
    raw = "--raw" in args
    sizes = SIZES_KB
    for index, arg in enumerate(args):
        if arg == "--size" and index + 1 < len(args):
            sizes = (int(args[index + 1]),)
    t0 = time.perf_counter()
    masker = get_masker()
    print(f"masker warm-up: {time.perf_counter() - t0:.2f}s, baseline RSS {rss_mb():.0f} MB")
    for size_kb in sizes:
        text = _sample(size_kb)
        actual_kb = len(text.encode("utf-8")) / 1024
        line = f"text {actual_kb:6.0f} KB: "
        if raw:
            t0 = time.perf_counter()
            addr_parts = list(masker._addresses(text))  # noqa: SLF001 - profiler
            line += f"addr(raw) {(time.perf_counter() - t0) * 1000:9.1f} ms ({len(addr_parts)} parts), "
            t0 = time.perf_counter()
            name_parts = list(masker._names(text))  # noqa: SLF001 - profiler
            line += f"names(raw) {(time.perf_counter() - t0) * 1000:9.1f} ms ({len(name_parts)} parts), "
        t0 = time.perf_counter()
        entities = masker.detect(text)
        line += (
            f"detect {(time.perf_counter() - t0) * 1000:9.1f} ms "
            f"({len(entities)} entities), peak RSS {rss_mb():7.0f} MB"
        )
        print(line)


if __name__ == "__main__":
    main()
