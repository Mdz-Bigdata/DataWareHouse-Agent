import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.routers.admin_analytics_router import admin_analytics_router
from app.api.routers.admin_datasource_router import admin_datasource_router
from app.api.routers.admin_governance_router import admin_governance_router
from app.api.routers.admin_llm_router import admin_llm_router
from app.api.routers.admin_semantic_router import admin_semantic_router
from app.api.routers.auth_router import auth_router
from app.api.routers.conversation_router import conversation_router
from app.api.routers.debug_router import debug_router
from app.api.routers.health_router import health_router
from app.api.routers.insight_card_router import insight_card_router
from app.api.routers.metrics_router import metrics_router
from app.api.routers.query_router import query_router
from app.conf.app_config import app_config
from app.core.context import set_request_id
from app.core.error_handlers import register_exception_handlers
from app.core.lifespan import lifespan
from app.core.log import logger
from app.core.metrics import app_info
from app.core.rate_limit import limiter
from app.core.telemetry import instrument_fastapi

# 定义fastAPI实例
app = FastAPI(lifespan=lifespan)

# Phase 0.5：注册 slowapi 限流异常处理器。
# 注意：不挂 SlowAPIMiddleware（在 StreamingResponse/SSE 上会崩溃），
# 只用 @limiter.limit() 装饰器模式，因此不需要 app.state.limiter。
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

# Phase 0.3：注册全局异常处理器（兜底 500 + HTTPException 附加 request_id）
register_exception_handlers(app)

# Phase 0.4：CORS 中间件。allowed_origins 在 yaml 里是逗号分隔字符串，这里拆分。
if app_config.cors.enable:
    origins = [o.strip() for o in app_config.cors.allowed_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=app_config.cors.allow_credentials,
        allow_methods=app_config.cors.allowed_methods,
        allow_headers=app_config.cors.allowed_headers,
    )

# Phase 4.3：FastAPI 自动埋点（HTTP 请求 span）。必须在路由注册前调用。
instrument_fastapi(app)

# 引入外部路由
app.include_router(query_router)
app.include_router(insight_card_router)
app.include_router(conversation_router)
app.include_router(auth_router)
app.include_router(admin_llm_router)
app.include_router(admin_semantic_router)
app.include_router(admin_governance_router)
app.include_router(admin_datasource_router)
app.include_router(admin_analytics_router)
app.include_router(health_router)
app.include_router(debug_router)
app.include_router(metrics_router)

# Phase 0.6：初始化应用信息指标（常量标签，值恒为 1）
app_info.labels(name=app_config.app.name, environment=app_config.app.environment).set(1)


@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.perf_counter()
    set_request_id(str(uuid.uuid4()))
    response = await call_next(request)
    process_time = time.perf_counter() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    logger.debug("请求处理完成: path={}, process_time={:.4f}s", request.url.path, process_time)
    return response
