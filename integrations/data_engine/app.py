from __future__ import annotations

import os
import secrets
from importlib import import_module
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from .runtime import DataEngineRuntime


class ToolRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


def public_health_snapshot(native: Any) -> dict[str, Any]:
    """Expose readiness data without returning a credential-bearing native DSN."""
    if not isinstance(native, dict):
        return {"ok": False}
    snapshot = {
        key: native[key]
        for key in ("ok", "dialect", "ontology", "checks")
        if key in native
    }
    database = native.get("db")
    if isinstance(database, dict):
        configured = database.get("configured_remote")
        snapshot["db"] = {
            "configured_remote": configured if isinstance(configured, dict) else {},
        }
    return snapshot


def create_app(runtime: DataEngineRuntime, *, service_token: str) -> FastAPI:
    if len(service_token) < 16:
        raise RuntimeError("DATA_ENGINE_SERVICE_TOKEN must contain at least 16 characters")

    app = FastAPI(title="Deterministic Data Agent Engine Adapter", version="0.1.0")
    bearer = HTTPBearer(auto_error=True)

    def authorize(
        credentials: HTTPAuthorizationCredentials = Depends(bearer),
    ) -> None:
        if credentials.scheme.lower() != "bearer" or not secrets.compare_digest(
            credentials.credentials, service_token
        ):
            raise HTTPException(status_code=401, detail="invalid service token")

    @app.get("/health")
    def health() -> dict[str, Any]:
        native = runtime.invoke("health_check", {})
        return {
            "status": "ok" if isinstance(native, dict) and native.get("ok") else "degraded",
            "service": "data-engine-adapter",
            "tools": len(runtime.tools()),
            "native": public_health_snapshot(native),
        }

    @app.get("/api/tools", dependencies=[Depends(authorize)])
    def list_tools() -> dict[str, Any]:
        return {"items": [item.public_dict() for item in runtime.tools()]}

    @app.post("/api/tools/{tool_name}", dependencies=[Depends(authorize)])
    async def invoke_tool(tool_name: str, request: ToolRequest) -> dict[str, Any]:
        try:
            result = await run_in_threadpool(runtime.invoke, tool_name, request.arguments)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if tool_name == "health_check":
            result = public_health_snapshot(result)
        return {"tool": tool_name, "result": result}

    return app


def load_native_runtime() -> DataEngineRuntime:
    server = import_module("mcp_servers.server")

    overrides = {}
    scheduler_url = os.getenv("DATA_ENGINE_SCHEDULER_URL", "").strip()
    scheduler_key = os.getenv("DATA_ENGINE_SCHEDULER_API_KEY", "").strip()
    if scheduler_url and scheduler_key:
        from .scheduler import AgentSchedulerBridge

        scheduler = AgentSchedulerBridge(scheduler_url, scheduler_key)
        overrides["scheduler_submit"] = scheduler.submit
    return DataEngineRuntime(
        server,
        allow_mutations=os.getenv("DATA_ENGINE_ALLOW_HTTP_MUTATIONS", "false").lower()
        in {"1", "true", "yes", "on"},
        overrides=overrides,
    )


def create_configured_app() -> FastAPI:
    """Build the production app lazily after the native engine is on PYTHONPATH."""
    return create_app(
        load_native_runtime(),
        service_token=os.environ.get("DATA_ENGINE_SERVICE_TOKEN", ""),
    )
