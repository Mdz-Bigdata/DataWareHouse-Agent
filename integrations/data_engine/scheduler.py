from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx


class AgentSchedulerBridge:
    """Submit modeling jobs to the integrated NanZi scheduler with idempotency."""

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        if not endpoint.startswith(("http://", "https://")):
            raise ValueError("scheduler endpoint must be an absolute HTTP(S) URL")
        if not api_key:
            raise ValueError("scheduler API key is required")
        self._endpoint = endpoint.rstrip("/") + "/"
        self._headers = {"X-API-Key": api_key}
        self._client = client or httpx.Client(timeout=httpx.Timeout(15.0, connect=3.0))

    def submit(self, task_spec: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(task_spec, dict):
            raise TypeError("task_spec must be an object")

        values = {
            "name": task_spec.get("name") or task_spec.get("task_name"),
            "agent_id": task_spec.get("agent_id"),
            "cron_expr": task_spec.get("cron_expr") or task_spec.get("cron"),
            "prompt": task_spec.get("prompt") or task_spec.get("command"),
        }
        missing = [name for name, value in values.items() if not isinstance(value, str) or not value.strip()]
        if missing:
            raise ValueError(f"task_spec missing required fields: {', '.join(missing)}")

        config = dict(task_spec.get("config") or {})
        for field in ("dependencies", "alert_channels", "timezone", "sla", "dqc"):
            if field in task_spec:
                config[field] = task_spec[field]
        key = task_spec.get("idempotency_key")
        if not isinstance(key, str) or not key.strip():
            material = json.dumps(
                {**values, "config": config}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            key = hashlib.sha256(material.encode("utf-8")).hexdigest()
        config["data_engine_idempotency_key"] = key
        config["source"] = "data-agent-engine"

        try:
            existing_response = self._client.get(self._endpoint, headers=self._headers)
            existing_response.raise_for_status()
            existing = existing_response.json().get("data", [])
            if isinstance(existing, list):
                for task in existing:
                    if isinstance(task, dict) and isinstance(task.get("config"), dict):
                        if task["config"].get("data_engine_idempotency_key") == key:
                            return {
                                "ok": True,
                                "scheduler": "nanzi-agent-platform",
                                "duplicate": True,
                                "task": task,
                            }

            response = self._client.post(
                self._endpoint,
                headers=self._headers,
                json={**values, "config": config},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return {
                "ok": False,
                "scheduler": "nanzi-agent-platform",
                "error": f"scheduler submission failed: {type(exc).__name__}",
            }

        task = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(task, dict):
            return {
                "ok": False,
                "scheduler": "nanzi-agent-platform",
                "error": "scheduler returned an invalid response",
            }
        return {
            "ok": True,
            "scheduler": "nanzi-agent-platform",
            "duplicate": False,
            "task": task,
        }
