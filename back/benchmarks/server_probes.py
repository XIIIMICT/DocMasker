"""
Server-level probes: /health latency under a heavy /api/mask, malformed uploads, header sizes.

Start the backend first:
    uv run --directory back granian --interface asgi src.main:app --host 127.0.0.1 --port 18009
Then run this probe against it (see benchmarks/README.md).
"""

import asyncio
import io
import json
import os
import time
import zipfile
from urllib.parse import quote

import httpx2 as httpx

BASE = os.environ.get("PROBE_BASE_URL", "http://127.0.0.1:18009")
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


async def health_gap(client: httpx.AsyncClient, text: str) -> None:
    """3.2: /health latency while a heavy /api/mask occupies the event loop."""

    async def heavy() -> float:
        start = time.perf_counter()
        response = await client.post("/api/mask", json={"text": text})
        assert response.status_code == 200
        return (time.perf_counter() - start) * 1000

    async def health(start_gate: asyncio.Event) -> list[float]:
        await start_gate.wait()
        latencies = []
        for _ in range(6):
            t0 = time.perf_counter()
            await client.get("/health")
            latencies.append((time.perf_counter() - t0) * 1000)
            await asyncio.sleep(0.05)
        return latencies

    gate = asyncio.Event()
    health_task = asyncio.create_task(health(gate))
    await asyncio.sleep(0.05)
    gate.set()
    elapsed = await heavy()
    latencies = await health_task
    print(f"3.2 heavy /api/mask: {elapsed:.0f} ms; /health during it: {[f'{v:.0f} ms' for v in latencies]}")


async def malformed_uploads(client: httpx.AsyncClient) -> None:
    """3.7: garbage bytes and non-DOCX ZIPs must not become HTTP 500s."""
    garbage = b"definitely not a zip archive" * 200
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "valid zip, not a docx")
    for label, payload in [("garbage bytes", garbage), ("zip, not a docx", buffer.getvalue())]:
        response = await client.post("/api/mask-file", files={"file": ("report.docx", payload, DOCX_MIME)})
        print(f"3.7 mask-file with {label}: HTTP {response.status_code}")


def header_sizes() -> None:
    """3.10: X-Unused-Keys percent-encoded JSON size for N unused values."""
    for n in (100, 1_000, 5_000):
        values = [f"Сидоров Сидор Сидорович, паспорт {4500 + i} {100000 + i}" for i in range(n)]
        size_kb = len(quote(json.dumps(values, ensure_ascii=False)).encode("utf-8")) / 1024
        print(f"3.10 X-Unused-Keys with {n} unused values: {size_kb:8.0f} KB raw header value")


async def main() -> None:
    from .synthetic import build_text  # noqa: PLC0415 - keeps the server probe import-light

    header_sizes()
    async with httpx.AsyncClient(base_url=BASE, timeout=180) as client:
        await malformed_uploads(client)
        await health_gap(client, build_text(100))


if __name__ == "__main__":
    asyncio.run(main())
