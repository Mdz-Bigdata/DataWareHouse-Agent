"""全局异常处理器（Phase 0.3）。

职责：
1. 兜底未捕获的 Exception -> 500 结构化 JSON 响应，记录完整异常栈到 loguru，
   并在响应里携带 request_id 便于客户端上报与日志关联排查。
2. 生产环境（environment != development）不向客户端泄露内部错误细节，
   只返回通用提示；开发环境返回 repr 以便本地定位。

设计约定：
- 不接管 FastAPI 对 RequestValidationError 的默认处理（其响应已合理）。
- 兜底"裸 Exception"，避免业务代码里散落的 try/except 转换为 500 的样板。
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.conf.app_config import app_config
from app.core.context import request_id_ctx_var
from app.core.log import logger


def _current_request_id() -> str:
    """获取当前请求 ID，上下文缺失时回退为 "unknown"。"""

    try:
        # request_id_ctx_var 的 default=1（历史遗留），统一转为字符串，避免类型混淆。
        request_id = request_id_ctx_var.get()
        return str(request_id) if request_id and request_id != 1 else "unknown"
    except LookupError:
        # ContextVar 尚未在该上下文设置过（理论不应发生，防御性处理）
        return "unknown"


def _is_dev() -> bool:
    return app_config.app.environment.lower() == "development"


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """兜底所有未被业务代码显式转换为 HTTPException 的异常。"""

    request_id = _current_request_id()
    logger.exception(
        "未捕获的服务端异常: path={} method={} request_id={} error={}",
        request.url.path,
        request.method,
        request_id,
        exc,
    )
    # 开发环境附带异常 repr 便于本地定位；生产环境仅返回通用提示，避免泄露内部细节。
    detail = f"服务内部错误: {exc!r}" if _is_dev() else "服务内部错误，请稍后重试或联系管理员"
    return JSONResponse(
        status_code=500,
        content={"detail": detail, "request_id": request_id},
        headers={"X-Request-Id": request_id},
    )


async def http_exception_with_request_id_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """为 HTTPException 响应附加 request_id 头，便于端到端追踪。

    Starlette/FastAPI 默认的 HTTPException handler 不带 request_id。
    这里在保持原状态码与 detail 的前提下补充该头，不改变响应体语义。
    """

    request_id = _current_request_id()
    headers = dict(exc.headers or {})
    headers["X-Request-Id"] = request_id
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """在 FastAPI 应用上注册全局异常处理器。"""

    app.add_exception_handler(Exception, unhandled_exception_handler)
    # Starlette 的 add_exception_handler 类型签名要求 (Request, Exception)，
    # 但针对具体异常子类的 handler 在运行时是正确的（协变）。
    app.add_exception_handler(
        StarletteHTTPException,
        http_exception_with_request_id_handler,  # type: ignore[arg-type]
    )
