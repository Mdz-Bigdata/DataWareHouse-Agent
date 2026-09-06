"""Phase 4.3：OpenTelemetry 初始化与便捷工具。

职责：
1. setup_telemetry()：配置 TracerProvider + exporter，应在应用启动最早处调用。
2. 装饰器 @telemetry_span()：给函数自动加 span（用在 graph 节点）。
3. get_current_span()：获取当前 span，便于手动加属性。

导出策略：
- otlp_endpoint 非空：用 OTLP HTTP exporter 导出到 Collector/Jaeger/Tempo
- otlp_endpoint 为空：用 ConsoleSpanExporter（本地调试，span 打到 stdout）
- enable=false：完全跳过（开发环境减少噪声）

与 request_id 的关联：
- FastAPIInstrumentor 自动为每个 HTTP 请求创建 span，含 request 路径/方法/状态码。
- 手动 span 通过 set_attribute 关联 request_id，便于在日志与 trace 间互查。
"""

from __future__ import annotations

import contextlib
import functools
from collections.abc import Callable
from typing import Any, TypeVar

from app.conf.app_config import app_config
from app.core.context import request_id_ctx_var
from app.core.log import logger

T = TypeVar("T")

_initialized = False


def setup_telemetry() -> None:
    """初始化 OpenTelemetry。幂等，重复调用安全。"""

    global _initialized
    if _initialized:
        return
    if not app_config.otel.enable:
        logger.info("OpenTelemetry 已禁用（otel.enable=false）")
        _initialized = True
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create({"service.name": app_config.otel.service_name})
        provider = TracerProvider(resource=resource)

        endpoint = app_config.otel.otlp_endpoint
        exporter: Any  # OTLPSpanExporter 或 ConsoleSpanExporter，运行时确定
        if endpoint:
            # OTLP HTTP 导出到 Collector/Jaeger/Tempo
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            exporter = OTLPSpanExporter(endpoint=endpoint)
            logger.info("OpenTelemetry 启用，导出到 OTLP: {}", endpoint)
        else:
            # 无 endpoint：用 Console 输出（本地调试）
            from opentelemetry.sdk.trace.export import ConsoleSpanExporter

            exporter = ConsoleSpanExporter()
            logger.info("OpenTelemetry 启用，导出到控制台（未配置 OTLP endpoint）")

        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _initialized = True
        logger.info("OpenTelemetry 初始化完成: service={}", app_config.otel.service_name)
    except Exception as exc:
        # OTel 初始化失败不应阻断应用启动
        logger.warning("OpenTelemetry 初始化失败，tracing 降级为禁用: {}", exc)
        _initialized = True


def instrument_fastapi(app) -> None:
    """自动埋点 FastAPI（HTTP 请求 span）。"""

    if not app_config.otel.enable or not _initialized:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
        logger.info("FastAPI OTel 自动埋点已启用")
    except Exception as exc:
        logger.warning("FastAPI OTel 埋点失败: {}", exc)


def telemetry_span(
    name: str | None = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """装饰器：为函数自动创建 span。

    用法：
        @telemetry_span("generate_sql")
        async def generate_sql(state, runtime):
            ...

    span 名默认用函数名。异常会记录到 span 的 status。
    """

    def decorator(func: Callable[..., T]) -> Any:
        span_name = name or func.__name__

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            return await _trace_async(span_name, func, *args, **kwargs)

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            return _trace_sync(span_name, func, *args, **kwargs)

        import inspect

        return async_wrapper if inspect.iscoroutinefunction(func) else sync_wrapper

    return decorator


async def _trace_async(name, func, *args, **kwargs):
    if not _initialized or not app_config.otel.enable:
        return await func(*args, **kwargs)
    from opentelemetry import trace

    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span(name) as span:
        _attach_request_id(span)
        try:
            return await func(*args, **kwargs)
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)))
            raise


def _trace_sync(name, func, *args, **kwargs):
    if not _initialized or not app_config.otel.enable:
        return func(*args, **kwargs)
    from opentelemetry import trace

    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span(name) as span:
        _attach_request_id(span)
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)))
            raise


def _attach_request_id(span) -> None:
    """把 request_id 写入 span 属性，关联日志与 trace。"""

    try:
        request_id = request_id_ctx_var.get()
        if request_id and request_id != 1:
            span.set_attribute("request_id", str(request_id))
    except Exception:
        pass


@contextlib.contextmanager
def manual_span(name: str, **attributes: Any):
    """上下文管理器：手动创建 span 并设置属性。

    用法：
        with manual_span("db_query", table="album"):
            ...
    """

    if not _initialized or not app_config.otel.enable:
        yield None
        return
    from opentelemetry import trace

    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span(name) as span:
        _attach_request_id(span)
        for key, value in attributes.items():
            with contextlib.suppress(Exception):
                span.set_attribute(key, value)
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)))
            raise
