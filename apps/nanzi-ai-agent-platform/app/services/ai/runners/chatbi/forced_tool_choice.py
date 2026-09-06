"""Inject tool_choice on the first model call of a repair round."""

from __future__ import annotations

import inspect
from typing import Any


class ForcedFirstToolChoiceModel:
    def __init__(self, inner: Any, tool_choice: Any):
        self._inner = inner
        self._tool_choice = tool_choice
        self._consumed = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if not self._consumed and self._tool_choice is not None:
            kwargs["tool_choice"] = self._tool_choice
            self._consumed = True
        result = self._inner(*args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result
