from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Iterable
from urllib.parse import urlsplit


def _enabled(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Subsystem:
    slug: str
    name: str
    route_prefix: str
    upstream_url: str
    ui_url: str = ""
    enabled: bool = True
    health_path: str = "/health"
    description: str = ""
    ui_port: int = 0

    def public_dict(self) -> dict[str, object]:
        value = asdict(self)
        value.pop("upstream_url")
        return value


class CapabilityRegistry:
    def __init__(self, subsystems: Iterable[Subsystem]) -> None:
        items = tuple(subsystems)
        slugs = [item.slug for item in items]
        prefixes = [item.route_prefix for item in items]
        if len(slugs) != len(set(slugs)):
            raise ValueError("duplicate subsystem slug")
        if len(prefixes) != len(set(prefixes)):
            raise ValueError("duplicate route prefix")
        if any(not prefix.startswith("/") for prefix in prefixes):
            raise ValueError("route prefix must start with /")
        for item in items:
            if item.ui_url:
                parsed = urlsplit(item.ui_url)
                if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError("ui_url must be an absolute HTTP(S) URL without credentials")
        self._items = items
        self._by_slug = {item.slug: item for item in items}

    @classmethod
    def from_environment(cls) -> "CapabilityRegistry":
        return cls(
            [
                Subsystem(
                    "core",
                    "DataWareHouse Agent",
                    "/platform/core",
                    os.getenv("PLATFORM_CORE_URL", "http://127.0.0.1:8000"),
                    os.getenv("PLATFORM_CORE_UI_URL", ""),
                    _enabled("PLATFORM_CORE_ENABLED"),
                    description="Existing NL2SQL, semantic layer, guardrails, and feedback flywheel",
                    ui_port=3000,
                ),
                Subsystem(
                    "data-api",
                    "NanZi Data API Platform",
                    "/platform/data-api",
                    os.getenv("PLATFORM_DATA_API_URL", "http://127.0.0.1:8020"),
                    os.getenv("PLATFORM_DATA_API_UI_URL", ""),
                    _enabled("PLATFORM_DATA_API_ENABLED"),
                    description="Governed data APIs, SQL Lab, metadata, catalog, RBAC, and audit",
                    ui_port=8020,
                ),
                Subsystem(
                    "agents",
                    "NanZi AI Agent Platform",
                    "/platform/agents",
                    os.getenv("PLATFORM_AGENTS_URL", "http://127.0.0.1:8030"),
                    os.getenv("PLATFORM_AGENTS_UI_URL", ""),
                    _enabled("PLATFORM_AGENTS_ENABLED"),
                    description="Enterprise agents, ChatBI, tools, knowledge, memory, and scheduling",
                    ui_port=8030,
                ),
                Subsystem(
                    "audio",
                    "Listen Book Data Agent",
                    "/platform/audio",
                    os.getenv("PLATFORM_AUDIO_URL", "http://127.0.0.1:8040"),
                    os.getenv("PLATFORM_AUDIO_UI_URL", ""),
                    _enabled("PLATFORM_AUDIO_ENABLED", default=False),
                    health_path="/ready",
                    description="LangGraph audio-domain analytics, governance, and insights",
                    ui_port=8040,
                ),
            ]
        )

    def all(self) -> tuple[Subsystem, ...]:
        return self._items

    def enabled(self) -> tuple[Subsystem, ...]:
        return tuple(item for item in self._items if item.enabled)

    def get(self, slug: str) -> Subsystem:
        try:
            return self._by_slug[slug]
        except KeyError as exc:
            raise KeyError(f"unknown subsystem: {slug}") from exc
