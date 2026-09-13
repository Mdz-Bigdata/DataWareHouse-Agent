from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from starlette.background import BackgroundTask

from .capabilities import CapabilityRegistry
from .proxy import HOP_BY_HOP_HEADERS, build_upstream_url, forwarded_headers, stateless_cookie_jar
from .tracing import TRACE_HEADER, resolve_trace_id, trace_log


registry = CapabilityRegistry.from_environment()

# 健康响应本该很小；超过这个尺寸就不解析（防止上游把大响应塞进聚合里）。
MAX_HEALTH_BODY_BYTES = 256 * 1024


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


def trace_endpoint_enabled() -> bool:
    """运维排障用的追溯查询端点，默认关闭（它会暴露最近被访问的子系统与路径）。"""

    return os.getenv("PLATFORM_TRACE_ENDPOINT", "").strip().lower() in {"1", "true", "yes", "on"}


@app.get("/api/platform/traces")
async def traces(trace_id: str = Query("", max_length=64), limit: int = Query(50, ge=1, le=200)):
    """按 trace_id 回查网关段调用记录；不带 trace_id 则返回最近若干条。

    默认关闭：置 ``PLATFORM_TRACE_ENDPOINT=1`` 才开放。记录只在内存里、条数有界，
    且不含请求体与查询串。
    """

    if not trace_endpoint_enabled():
        raise HTTPException(status_code=404, detail="trace endpoint disabled")
    items = trace_log.for_trace(trace_id) if trace_id else trace_log.recent(limit)
    return {"items": items[:limit], "trace_header": TRACE_HEADER}


@app.get("/api/platform/ready")
async def ready(request: Request) -> JSONResponse:
    """Aggregate subsystem readiness.

    子系统可以在自己的健康响应里把某个依赖标成 ``not_configured``（按设计未在
    当前 profile 启用）。这种依赖**不计入 degraded**——但会原样列进
    ``not_configured``，让人一眼看到平台少了哪块可选能力，而不是被藏起来。
    真故障（子系统 503 / 连不上）仍然照旧拉低整体状态。
    """

    states: list[dict[str, object]] = []
    not_configured: list[dict[str, object]] = []
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
                # 无论子系统本身 ready 与否，都把它的可选依赖如实带出来。
                optional = optional_dependencies(response)
                if optional:
                    state["not_configured"] = [item["dependency"] for item in optional]
                    not_configured.extend({"subsystem": subsystem.slug, **item} for item in optional)
            except httpx.HTTPError as exc:
                state["error"] = type(exc).__name__
        states.append(state)
    enabled_states = [item for item in states if item["enabled"]]
    all_ready = all(bool(item["ready"]) for item in enabled_states)
    return JSONResponse(
        {
            "status": "ready" if all_ready else "degraded",
            "subsystems": states,
            "not_configured": not_configured,
        },
        status_code=200 if all_ready else 503,
    )


def optional_dependencies(response: httpx.Response) -> list[dict[str, object]]:
    """从子系统健康响应里提取 ``status == "not_configured"`` 的依赖。

    上游响应体是外部数据：非 JSON、体积超限、结构不符一律安全地忽略，
    取出的字符串按固定字段截断后才回显。
    """

    if "json" not in response.headers.get("content-type", "").lower():
        return []
    if len(response.content) > MAX_HEALTH_BODY_BYTES:
        return []
    try:
        payload = response.json()
    except ValueError:
        return []
    dependencies = payload.get("dependencies") if isinstance(payload, dict) else None
    if not isinstance(dependencies, dict):
        return []
    items: list[dict[str, object]] = []
    for name, value in dependencies.items():
        if not isinstance(value, dict) or value.get("status") != "not_configured":
            continue
        item: dict[str, object] = {"dependency": _short(name)}
        for field in ("detail", "target"):
            if isinstance(value.get(field), str) and value[field]:
                item[field] = _short(value[field])
        items.append(item)
    return items


def _short(value: str, limit: int = 120) -> str:
    return value[:limit]


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
        port=subsystem.ui_port, path="/", query=subsystem.ui_query, fragment="",
    ))
    return RedirectResponse(target)


@app.api_route(
    "/platform/{subsystem_slug}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
@app.api_route(
    "/api/platform/{subsystem_slug}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy(subsystem_slug: str, path: str, request: Request):
    try:
        subsystem = registry.get(subsystem_slug)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not subsystem.enabled:
        raise HTTPException(status_code=503, detail=f"subsystem disabled: {subsystem_slug}")

    # 入站 trace_id 是外部输入：不合规就重新签发，绝不原样回显进响应头。
    trace_id = resolve_trace_id(request.headers)
    upstream_request = request.app.state.http.build_request(
        request.method,
        build_upstream_url(subsystem.upstream_url, path, request.scope.get("query_string", b"")),
        headers=forwarded_headers(
            request.headers,
            trace_id=trace_id,
            subsystem_token=subsystem.service_token or None,
        ),
        content=await request.body(),
    )
    started = time.perf_counter()
    try:
        upstream = await request.app.state.http.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        trace_log.record(
            trace_id=trace_id, subsystem=subsystem_slug, method=request.method,
            path=request.url.path, status_code=502,
            elapsed_ms=(time.perf_counter() - started) * 1000.0, error=type(exc).__name__,
        )
        raise HTTPException(status_code=502, detail=f"upstream unavailable: {subsystem_slug}") from exc

    # 流式响应下，这里量到的是上游首字节（响应头）耗时——网关这一段的真实开销。
    upstream_ms = (time.perf_counter() - started) * 1000.0
    trace_log.record(
        trace_id=trace_id, subsystem=subsystem_slug, method=request.method,
        path=request.url.path, status_code=upstream.status_code, elapsed_ms=upstream_ms,
    )

    response_headers = [
        (name, value)
        for name, value in upstream.headers.raw
        if name.decode("ascii").lower() not in HOP_BY_HOP_HEADERS | {TRACE_HEADER, "x-gateway-upstream-ms"}
    ]
    response_headers.append((TRACE_HEADER.encode("ascii"), trace_id.encode("latin-1")))
    response_headers.append((b"x-gateway-upstream-ms", f"{upstream_ms:.3f}".encode("ascii")))
    response = StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        background=BackgroundTask(upstream.aclose),
    )
    response.raw_headers = response_headers
    return response
