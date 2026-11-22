"""Text masking benchmark: detect/rewrite wall time and peak RSS (see benchmarks/README.md)."""

import time

from src.masking.mapping import MaskKeys
from src.masking.rule_based import get_masker
from src.masking.service import mask_text

from .synthetic import build_text
from .util import detect_entities, rss_mb


def bench(masker) -> None:
    for label, n in [("small", 10), ("medium", 1_000), ("large", 10_000)]:
        text = build_text(n)
        size_kb = len(text.encode("utf-8")) / 1024

        t0 = time.perf_counter()
        entities = detect_entities(masker, text)
        detect_ms = (time.perf_counter() - t0) * 1000

        t1 = time.perf_counter()
        result = mask_text(text, entities, MaskKeys())
        rewrite_ms = (time.perf_counter() - t1) * 1000
        assert "[PERSON_" in result

        print(
            f"text {label}: {size_kb:8.0f} KB, {len(entities):6d} entities, "
            f"detect {detect_ms:9.1f} ms, rewrite {rewrite_ms:8.1f} ms, "
            f"peak RSS {rss_mb():7.0f} MB"
        )


def main() -> None:
    t0 = time.perf_counter()
    masker = get_masker()
    print(f"masker warm-up: {time.perf_counter() - t0:.2f}s")
    bench(masker)


if __name__ == "__main__":
    main()
