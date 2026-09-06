"""JSON-safe serialization for AgentScope runtime payloads.

AgentScope / tool hooks may leak un-awaited coroutines into agent.state.
Pydantic ``model_dump(mode="json")`` then raises:
``Unable to serialize unknown type: <class 'coroutine'>``.
Resolve awaitables first, then dump JSON-compatible structures.
"""

from __future__ import annotations

import inspect
from typing import Any

from pydantic import TypeAdapter


async def resolve_awaitables(value: Any, *, path: str = "value") -> Any:
    """Recursively await leaked awaitables in nested dict/list/tuple payloads."""
    if inspect.isawaitable(value):
        try:
            resolved = await value
        except Exception as exc:
            raise RuntimeError(
                f"Failed to resolve awaitable while serializing {path}: {exc}",
            ) from exc
        return await resolve_awaitables(resolved, path=path)
    if isinstance(value, dict):
        return {
            key: await resolve_awaitables(item, path=f"{path}.{key}")
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            await resolve_awaitables(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, tuple):
        return tuple(
            [
                await resolve_awaitables(item, path=f"{path}[{index}]")
                for index, item in enumerate(value)
            ],
        )
    if isinstance(value, set):
        return [
            await resolve_awaitables(item, path=f"{path}[]")
            for item in value
        ]
    return value


def _to_python_tree(value: Any) -> Any:
    if hasattr(value, "model_dump") and callable(value.model_dump):
        try:
            return value.model_dump(mode="python")
        except TypeError:
            return value.model_dump()
    return value


async def serialize_jsonable(value: Any, *, path: str = "value") -> Any:
    """Convert a runtime object/tree into a JSON-compatible Python structure."""
    python_value = _to_python_tree(value)
    resolved = await resolve_awaitables(python_value, path=path)
    return TypeAdapter(Any).dump_python(resolved, mode="json")


async def serialize_agent_state(state: Any) -> dict[str, Any]:
    """Serialize AgentScope agent.state for Redis / pending snapshots."""
    serialized = await serialize_jsonable(state, path="agent_state")
    if isinstance(serialized, dict):
        return serialized
    return {"value": serialized}
