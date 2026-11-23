"""Tests: synchronous CPU work leaves the event loop (F-01)."""

import asyncio
import threading
import time
from collections.abc import Iterator

import pytest
from httpx2 import ASGITransport, AsyncClient

from src.config import Settings
from src.main import app
from src.masking.executor import ExecutorBusyError, reset_executor, run_cpu


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    reset_executor()
    yield
    reset_executor()


async def test_health_stays_responsive_during_mask() -> None:
    """The blocking masker must not delay a concurrent /health request."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Warm the masker in a thread so the first-call dictionary build is
        # not mistaken for event-loop blocking.
        await client.post("/api/mask", json={"text": "Иван Петров"})
        text = "Иван Петров, ivan@mail.ru " * 20
        mask_task = asyncio.create_task(client.post("/api/mask", json={"text": text}))
        await asyncio.sleep(0)  # let the mask task reach the executor
        started = time.perf_counter()
        health = await client.get("/health")
        elapsed_ms = (time.perf_counter() - started) * 1000
        mask = await mask_task
    assert health.status_code == 200
    assert mask.status_code == 200
    # /health is pure Python; with the mask in a thread it stays fast.
    assert elapsed_ms < 500, f"/health was blocked for {elapsed_ms:.0f} ms"


async def test_run_cpu_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Over the semaphore cap a sync operation fails fast with 429 semantics."""
    settings = Settings.model_validate({"jobs_concurrency": 0})
    monkeypatch.setattr("src.config.get_settings", lambda: settings)
    monkeypatch.setattr("src.masking.executor.get_settings", lambda: settings)
    reset_executor()

    gate = threading.Event()

    def slow() -> str:
        return "done"

    async def hold() -> str:
        def blocking() -> str:
            gate.wait(2)
            return "held"

        return await run_cpu(blocking)

    # Acquire the only slot directly through run_cpu with a blocking fn.
    task = asyncio.create_task(hold())
    await asyncio.sleep(0.05)
    with pytest.raises(ExecutorBusyError):
        await run_cpu(slow)
    gate.set()
    assert await task == "held"
