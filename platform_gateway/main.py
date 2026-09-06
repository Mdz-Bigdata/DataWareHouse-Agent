from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from starlette.background import BackgroundTask

from .capabilities import CapabilityRegistry
from .proxy import HOP_BY_HOP_HEADERS, build_upstream_url, forwarded_headers, stateless_cookie_jar


registry = CapabilityRegistry.from_environment()


def create_http_client(**kwargs) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(60.0, connect=5.0), cookies=stateless_cookie_jar(), **kwargs,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = create_http_client()
    yield
    await app.state.http.aclose()


app = FastAPI(title="DataWareHouse Unified Platform Gateway", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, object]:
    return {"status": "ok", "service": "platform-gateway"}


@app.get("/api/platform/capabilities")
async def capabilities() -> dict[str, object]:
    return {"items": [item.public_dict() for item in registry.all()]}


@app.get("/api/platform/ready")
async def ready(request: Request) -> JSONResponse:
    states: list[dict[str, object]] = []
    for subsystem in registry.all():
        state: dict[str, object] = {
            "slug": subsystem.slug,
            "enabled": subsystem.enabled,
            "ready": False,
        }
        if subsystem.enabled:
            try:
                response = await request.app.state.http.get(
                    build_upstream_url(subsystem.upstream_url, subsystem.health_path)
                )
                state["ready"] = response.is_success
                state["status_code"] = response.status_code
            except httpx.HTTPError as exc:
                state["error"] = type(exc).__name__
        states.append(state)
    enabled_states = [item for item in states if item["enabled"]]
    all_ready = all(bool(item["ready"]) for item in enabled_states)
    return JSONResponse(
        {"status": "ready" if all_ready else "degraded", "subsystems": states},
        status_code=200 if all_ready else 503,
    )


@app.api_route(
    "/platform/{subsystem_slug}", methods=["GET", "HEAD"],
)
@app.api_route(
    "/platform/{subsystem_slug}/", methods=["GET", "HEAD"],
)
async def launch_ui(subsystem_slug: str, request: Request):
    """Native applications use origin-root assets, routers, API paths and SSE."""
    try:
        subsystem = registry.get(subsystem_slug)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not subsystem.enabled:
        raise HTTPException(status_code=503, detail=f"subsystem disabled: {subsystem_slug}")
    target = subsystem.ui_url or str(request.url.replace(
        port=subsystem.ui_port, path="/", query="", fragment="",
    ))
    return RedirectResponse(target)


@app.api_route(
    "/platform/{subsystem_slug}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy(subsystem_slug: str, path: str, request: Request):
    try:
        subsystem = registry.get(subsystem_slug)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not subsystem.enabled:
        raise HTTPException(status_code=503, detail=f"subsystem disabled: {subsystem_slug}")

    trace_id = request.headers.get("x-trace-id") or uuid.uuid4().hex
    upstream_request = request.app.state.http.build_request(
        request.method,
        build_upstream_url(subsystem.upstream_url, path, request.scope.get("query_string", b"")),
        headers=forwarded_headers(request.headers, trace_id=trace_id),
        content=await request.body(),
    )
    try:
        upstream = await request.app.state.http.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"upstream unavailable: {subsystem_slug}") from exc

    response_headers = [
        (name, value)
        for name, value in upstream.headers.raw
        if name.decode("ascii").lower() not in HOP_BY_HOP_HEADERS | {"x-trace-id"}
    ]
    response_headers.append((b"x-trace-id", trace_id.encode("latin-1")))
    response = StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        background=BackgroundTask(upstream.aclose),
    )
    response.raw_headers = response_headers
    return response
