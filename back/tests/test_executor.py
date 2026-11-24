"""Tests: synchronous CPU work leaves the event loop (F-01)."""

import asyncio
import threading
import time
from collections.abc import Iterator

import pytest
from httpx2 import ASGITransport, AsyncClient

from src.config import Settings
from src.main import app
from src.masking.executor import ExecutorBusyError, reset_executor, run_blocking, run_cpu


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


async def test_run_blocking_uses_a_separate_io_pool() -> None:
    """Short blocking IO must run off the event loop, on the dm-io pool."""
    loop_thread = threading.get_ident()

    def blocking_io() -> tuple[int, str]:
        time.sleep(0.05)  # a slow DB/Valkey call
        return threading.get_ident(), threading.current_thread().name

    started = time.perf_counter()
    worker_id, thread_name = await run_blocking(blocking_io)
    # The call did not run on the event loop thread.
    assert worker_id != loop_thread
    assert thread_name.startswith("dm-io")
    # And the event loop stayed schedulable while it ran (bounded < 50 ms here).
    assert (time.perf_counter() - started) * 1000 < 500


async def test_run_blocking_propagates_exceptions() -> None:
    def failing() -> None:
        msg = "db down"
        raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="db down"):
        await run_blocking(failing)


async def test_run_cpu_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Over the semaphore cap a sync operation fails fast with 429 semantics."""
    settings = Settings.model_validate({"jobs_concurrency": 1})
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

    # Acquire every slot (limit = jobs_concurrency * 2 = 2) with blocking fns.
    tasks = [asyncio.create_task(hold()) for _ in range(2)]
    await asyncio.sleep(0.05)
    with pytest.raises(ExecutorBusyError):
        await run_cpu(slow)
    gate.set()
    assert await asyncio.gather(*tasks) == ["held", "held"]
