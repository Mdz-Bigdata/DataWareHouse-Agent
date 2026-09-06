"""Small dependency-readiness aggregator used by the HTTP health routes."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


async def readiness_report(
    probes: dict[str, Callable[[], Awaitable[None]]], timeout_seconds: float = 3.0
) -> dict:
    results = await asyncio.gather(
        *(_run_probe(name, probe, timeout_seconds) for name, probe in probes.items())
    )
    dependencies = {name: result for name, result in results}
    ready = all(result["status"] == "ok" for result in dependencies.values())
    return {
        "status": "ready" if ready else "unavailable",
        "dependencies": dependencies,
    }


async def _run_probe(
    name: str, probe: Callable[[], Awaitable[None]], timeout_seconds: float
) -> tuple[str, dict]:
    try:
        await asyncio.wait_for(probe(), timeout=timeout_seconds)
        return name, {"status": "ok"}
    except Exception as exc:
        return name, {"status": "error", "detail": type(exc).__name__}
