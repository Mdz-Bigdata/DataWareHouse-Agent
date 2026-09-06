from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from fastapi.openapi.utils import get_openapi
from app.core.errors import COMMON_ERROR_RESPONSES
from app.schemas.response import ErrorResponse
import logging

# OpenAPI Tags
tags_metadata = [
    {
        "name": "通用查询",
        "description": "通用逻辑查询接口，支持灵活的条件筛选、分页和排序。",
    },
]

async def custom_openapi(app: FastAPI):
    """
    Generate Custom OpenAPI Schema.
    """
    if app.openapi_schema:
        return app.openapi_schema
    
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        tags=tags_metadata,
    )
    
    # 注入 Schema 定义 (确保 ErrorResponse 可被引用)
    if "schemas" not in openapi_schema["components"]:
        openapi_schema["components"]["schemas"] = {}
    
    if "ErrorResponse" not in openapi_schema["components"]["schemas"]:
        # 使用 Pydantic 的 schema 生成方法 (兼容 v1/v2)
        try:
            schema = ErrorResponse.model_json_schema()
        except AttributeError:
            schema = ErrorResponse.schema() # Fallback for v1
        openapi_schema["components"]["schemas"]["ErrorResponse"] = schema

    # 注入通用错误响应组件
    if "responses" not in openapi_schema["components"]:
        openapi_schema["components"]["responses"] = {}
        
    for status, info in COMMON_ERROR_RESPONSES.items():
        openapi_schema["components"]["responses"][str(status)] = {
            "description": info["description"],
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                }
            }
        }
    
    # Add security scheme
    openapi_schema["components"]["securitySchemes"] = {
        "X-API-Key": {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
            "description": "API 密钥认证，在请求头中携带 `X-API-Key: your_api_key_here`"
        }
    }
    
    app.openapi_schema = openapi_schema
    return app.openapi_schema
