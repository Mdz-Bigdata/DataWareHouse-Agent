from __future__ import annotations

import inspect
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Callable, Mapping


TOOL_NAMES = (
    "health_check",
    "ontology_search",
    "ontology_traverse",
    "mql_validate",
    "mql_explain",
    "semantic_translate",
    "execute_sql",
    "metric_disambiguate",
    "term_normalize",
    "intent_classify",
    "metadata_search",
    "ddl_generate",
    "etl_generate",
    "modeling_plan",
    "ontology_register",
    "ontology_reload",
    "explore_validate",
    "explore_execute",
    "explore_promote",
    "scheduler_submit",
)

MUTATING_TOOLS = frozenset({"ontology_register", "scheduler_submit"})


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: tuple[str, ...]
    mutating: bool

    def public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": list(self.parameters),
            "mutating": self.mutating,
        }


class DataEngineRuntime:
    def __init__(
        self,
        module: ModuleType | Any,
        *,
        allow_mutations: bool,
        overrides: Mapping[str, Callable[..., Any]] | None = None,
    ) -> None:
        self._allow_mutations = allow_mutations
        self._overrides = dict(overrides or {})
        unknown = set(self._overrides) - set(TOOL_NAMES)
        if unknown:
            raise ValueError(f"unknown data-engine tool overrides: {sorted(unknown)}")
        self._tools: dict[str, Any] = {}
        self._specs: tuple[ToolSpec, ...] = tuple(
            self._register(module, name) for name in TOOL_NAMES
        )

    def _register(self, module: ModuleType | Any, name: str) -> ToolSpec:
        value = self._overrides.get(name, getattr(module, name, None))
        if not callable(value):
            raise RuntimeError(f"native data-engine tool is missing: {name}")
        self._tools[name] = value
        signature = inspect.signature(value)
        description = inspect.getdoc(value) or ""
        return ToolSpec(
            name=name,
            description=description,
            parameters=tuple(signature.parameters),
            mutating=name in MUTATING_TOOLS,
        )

    def tools(self) -> tuple[ToolSpec, ...]:
        return self._specs

    def invoke(self, name: str, arguments: dict[str, Any]) -> Any:
        try:
            tool = self._tools[name]
        except KeyError as exc:
            raise KeyError(f"unknown data-engine tool: {name}") from exc
        if name in MUTATING_TOOLS and not self._allow_mutations:
            raise PermissionError(f"HTTP mutation is disabled for tool: {name}")
        if not isinstance(arguments, dict):
            raise TypeError("tool arguments must be an object")
        inspect.signature(tool).bind(**arguments)
        return tool(**arguments)
