# -*- coding: utf-8 -*-
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import os

from app.api import chat, developer, llm

# NOTE: FastAPI 应用主入口。初始化 CORS 中间件，组装路由并运行服务。

app = FastAPI(
    title="DataWareHouse Multi-Agent System",
    description="多 Agent 协同数仓与智能问数决策引擎后端",
    version="1.0.0"
)

# 允许跨域，支持 React 前端本地开发连调
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # 连调阶段允许所有来源
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册 API 路由
app.include_router(chat.router, prefix="/api")
app.include_router(developer.router, prefix="/api")
app.include_router(llm.router, prefix="/api")

@app.get("/health")
def health_check():
    """
    系统健康检查接口
    """
    from app.service.db_service import db_service
    from app.service.data_source_info import describe_data_source
    from sqlalchemy import text
    try:
        if db_service.real_engine is not None:
            with db_service.real_engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        else:
            db_service.conn.execute("SELECT 1")
        source = describe_data_source(db_service)
    except Exception:
        raise HTTPException(status_code=503, detail="当前数据库连接不可用") from None
    return {"status": "healthy", "service": "DataWareHouse-Agent Backend",
            "db_type": db_service.active_db_type,
            "data_source": source["mode"],
            "engine": source["engine"],
            "data_origin": source["data_origin"],
            "database_identity": source["database_identity"]}

if __name__ == "__main__":
    # 获取环境变量 PORT，默认 8000
    port = int(os.getenv("PORT", 8000))
    print(f"Starting DataWareHouse-Agent Backend on port {port}...")
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)
