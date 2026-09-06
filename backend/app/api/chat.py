# -*- coding: utf-8 -*-
from fastapi import APIRouter, HTTPException, Query
from typing import List
from app.schema.chat import (AskRequest, AskResponse, HistoryRecord, PreferenceProfile,
                            ErrorCorrectionRecord, AddErrorCorrectionRequest,
                            DataSourceCatalog, DataSourceInfo, SelectDataSourceRequest)
from app.service.ask_agent import ask_agent
from app.model.user_memory import user_memory

# NOTE: API 控制器层 - 智能问数接口路由，处理自然语言问数、历史记录及偏好查询。

router = APIRouter(prefix="/chat", tags=["智能问数"])

@router.get("/data-source")
def get_data_source():
    from app.service.data_source_manager import data_source_manager
    return data_source_manager.describe_active()

@router.get("/data-sources", response_model=DataSourceCatalog)
def list_data_sources():
    """列出所有受支持的数据源引擎及其配置状态，供前端筛选与切换。"""
    from app.service.data_source_manager import data_source_manager
    return data_source_manager.catalog()

@router.post("/data-source", response_model=DataSourceInfo)
def select_data_source(request: SelectDataSourceRequest):
    """切换当前问数使用的数据源；不可用的数据源不会改变当前连接。"""
    from app.service.data_source_manager import DataSourceError, data_source_manager
    try:
        return data_source_manager.activate(request.id)
    except DataSourceError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None

@router.post("/ask", response_model=AskResponse)
def ask_question(request: AskRequest):
    """
    接收自然语言问题，执行 NL2SQL 全链路问数 Agent 动作
    """
    try:
        if request.data_source:
            select_data_source(SelectDataSourceRequest(id=request.data_source))
        res = ask_agent.ask(
            question=request.question,
            dialect=request.dialect,
            user=request.user,
            role=request.role
        )
        source_info = get_data_source()
        res["data_source_info"] = source_info
        if isinstance(res.get("details"), dict):
            res["details"]["data_source"] = source_info["mode"]
        return res
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"智能问数内部错误: {str(e)}")

@router.get("/history", response_model=List[HistoryRecord])
def get_chat_history(user: str = Query("anonymous", description="用户名")):
    """
    获取 L1 查询历史记录
    """
    return user_memory.get_history(user=user)

@router.get("/preference", response_model=PreferenceProfile)
def get_user_preference(user: str = Query("anonymous", description="用户名")):
    """
    获取 L2 用户画像偏好
    """
    profile = user_memory.get_preference_profile(user=user)
    return profile

@router.post("/preference", response_model=PreferenceProfile)
def update_user_preference(request: PreferenceProfile):
    """
    更新/覆盖 L2 用户画像偏好
    """
    try:
        updated_profile = user_memory.update_preference_profile(
            user=request.user,
            profile_update={
                "common_tables": request.common_tables,
                "common_metrics": request.common_metrics,
                "common_dimensions": request.common_dimensions,
                "common_time_ranges": request.common_time_ranges
            }
        )
        return updated_profile
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存画像失败: {str(e)}")

@router.get("/recommendations", response_model=List[str])
def get_active_recommendations(user: str = Query("anonymous", description="用户名")):
    """
    获取 L3 主动建议推荐列表
    """
    return user_memory.get_active_recommendations(user=user)


@router.get("/corrections", response_model=List[ErrorCorrectionRecord])
def get_all_error_corrections():
    """
    获取全部纠错记忆记录
    """
    return user_memory.get_error_corrections()


@router.post("/corrections", response_model=ErrorCorrectionRecord)
def add_manual_error_correction(req: AddErrorCorrectionRequest):
    """
    手动录入一条成功的纠错经验并更新向量索引
    """
    try:
        from app.service.vector_service import vector_service
        record = user_memory.add_error_correction(
            question=req.question,
            error_message=req.error_message,
            wrong_sql=req.wrong_sql,
            corrected_sql=req.corrected_sql
        )
        # 同步更新向量库
        vector_service.ingest_error_corrections()
        return record
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"录入纠错经验失败: {str(e)}")


@router.delete("/corrections/delete")
def delete_single_error_correction(question: str = Query(..., description="要删除的纠错提问句")):
    """
    删除特定的纠错经验并重载向量索引
    """
    try:
        from app.service.vector_service import vector_service
        deleted = user_memory.delete_error_correction(question=question)
        if deleted:
            vector_service.ingest_error_corrections()
            return {"status": "success", "message": f"成功删除关于 '{question}' 的纠错记录！"}
        else:
            raise HTTPException(status_code=404, detail="未找到该提问对应的纠错记录")
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"删除纠错经验失败: {str(e)}")


@router.delete("/corrections/clear")
def clear_all_error_corrections():
    """
    一键清空全部纠错经验并重载向量库
    """
    try:
        from app.service.vector_service import vector_service
        user_memory.clear_error_corrections()
        vector_service.ingest_error_corrections()
        return {"status": "success", "message": "已成功清空全部纠错记录！"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"清空纠错经验失败: {str(e)}")


@router.get("/cache/stats")
def get_cache_statistics():
    """
    获取多级语义缓存性能与命中率统计
    """
    from app.service.semantic_cache import semantic_cache
    return semantic_cache.get_stats()


@router.post("/cache/clear")
def clear_semantic_cache():
    """
    一键清空语义缓存池
    """
    from app.service.semantic_cache import semantic_cache
    semantic_cache.invalidate_all()
    return {"status": "success", "message": "语义缓存池已成功清空"}


@router.get("/lineage")
def get_warehouse_lineage():
    """
    获取湖仓端到端数据血缘与分层链路图谱
    """
    from app.service.skills.lineage_skill import lineage_skill
    return lineage_skill.lineage_graph


@router.post("/metadata/enrich")
def enrich_table_metadata(table_name: str = Query(..., description="目标物理表名")):
    """
    对指定表执行 AI 数据画像与元数据自动补全 (Profiling & Enrichment)
    """
    try:
        from app.service.metadata_enricher import metadata_enricher
        enrich_result = metadata_enricher.enrich_metadata(table_name)
        return enrich_result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"元数据自动补全失败: {str(e)}")


@router.get("/tables", response_model=List[str])
def list_warehouse_tables():
    """
    获取数仓中当前所有可用于问数与元数据画像的物理表名列表
    """
    try:
        from app.service.metadata_enricher import metadata_enricher
        return metadata_enricher.get_available_tables()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取表列表失败: {str(e)}")
