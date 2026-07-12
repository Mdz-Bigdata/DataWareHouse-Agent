# -*- coding: utf-8 -*-
from fastapi import APIRouter, HTTPException, Query, Body
import os
from app.schema.developer import DevRequest, DevResponse
from app.service.dev_agent_coordinator import dev_agent_coordinator

# NOTE: API 控制器层 - 数仓开发多 Agent 协作流接口路由，处理需求调度及物理文件的读取与在线更新。

router = APIRouter(prefix="/developer", tags=["数仓开发Agent"])

@router.post("/run", response_model=DevResponse)
def run_developer_workflow(request: DevRequest):
    """
    接收数仓开发需求，调度顶层协调者及子 Agent 协同开发代码与文档
    """
    try:
        res = dev_agent_coordinator.start_dev_workflow(
            requirement=request.requirement,
            datasource=request.datasource,
            sql_engine=request.sql_engine
        )
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"数仓开发协作链调度失败: {str(e)}")

@router.get("/file")
def get_file_content(path: str = Query(..., description="相对工作区的路径，例如: init/doris/dws_trade_order_summary_daily.sql")):
    """
    获取生成的 DDL、ETL SQL、Job 配置或文档的源码内容
    """
    base_dir = "/Users/mindezhi/DataWareHouse-Agent"
    # 限制路径防越权
    safe_path = os.path.normpath(os.path.join(base_dir, path))
    if not safe_path.startswith(base_dir):
        raise HTTPException(status_code=403, detail="拒绝访问非项目目录文件")
    
    if not os.path.exists(safe_path):
        raise HTTPException(status_code=404, detail="文件未找到，请先运行协作流生成文件。")
    
    try:
        with open(safe_path, "r", encoding="utf-8") as f:
            content = f.read()
        return {"path": path, "content": content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取文件失败: {str(e)}")

@router.post("/file")
def update_file_content(path: str = Query(...), content: str = Body(..., embed=True)):
    """
    允许用户在前端直接修改生成的 DDL 或 SQL
    """
    base_dir = "/Users/mindezhi/DataWareHouse-Agent"
    safe_path = os.path.normpath(os.path.join(base_dir, path))
    if not safe_path.startswith(base_dir):
        raise HTTPException(status_code=403, detail="拒绝访问非项目目录文件")
        
    try:
        # 确保父目录存在
        os.makedirs(os.path.dirname(safe_path), exist_ok=True)
        with open(safe_path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"status": "success", "message": f"成功修改并保存 {path}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新文件失败: {str(e)}")
