# -*- coding: utf-8 -*-
import copy
import json
import logging
import os
import tempfile
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request
from typing import List
from app.schema.chat import (AskRequest, AskResponse, HistoryRecord, PreferenceProfile,
                            ErrorCorrectionRecord, AddErrorCorrectionRequest,
                            DataSourceCatalog, DataSourceInfo, SelectDataSourceRequest)
from app.service.ask_agent import ask_agent
from app.model.user_memory import user_memory

# NOTE: API 控制器层 - 智能问数接口路由，处理自然语言问数、历史记录及偏好查询。

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["智能问数"])

# =====================================================================
# 纠错经验删除保护：软删除 + 二次确认 + 可还原
# ---------------------------------------------------------------------
# 纠错经验是长期沉淀、无法自动重建的资产。历史实现中
# DELETE /chat/corrections/clear 无参数、无确认、无备份，一次误点就把全部
# 经验连同向量索引一起抹掉，且不可恢复。现在的约定是：
#   1. 任何删除（单条 / 清空）都先把记录写入回收站归档文件；归档写失败就
#      放弃删除（fail-closed），宁可删不掉也不能删了找不回来；
#   2. 清空必须显式带 confirm=true，否则返回 400 且不做任何改动；
#   3. 误删可用 GET /chat/corrections/archive 查看、
#      POST /chat/corrections/restore 还原。
# 归档文件与 user_memory.json 同目录（随 user_memory.storage_path 走，
# 因此容器里挂载卷的配置对它同样生效）。
# =====================================================================

CORRECTION_ARCHIVE_FILENAME = "user_memory.corrections_archive.json"


def _correction_archive_path() -> str:
    """回收站归档文件路径，跟随用户记忆的落盘目录（运行期读取，不在导入期固化）。"""
    storage = getattr(user_memory, "storage_path", "") or "user_memory.json"
    directory = os.path.dirname(os.path.abspath(storage)) or "."
    return os.path.join(directory, CORRECTION_ARCHIVE_FILENAME)


def _load_correction_archive() -> List[dict]:
    """读取回收站。文件损坏时直接抛出，由调用方决定 —— 绝不当作空归档覆盖掉。"""
    path = _correction_archive_path()
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    batches = data.get("batches", []) if isinstance(data, dict) else data
    if not isinstance(batches, list):
        raise ValueError(f"回收站归档格式非法: {path}")
    return [b for b in batches if isinstance(b, dict)]


def _write_correction_archive(batches: List[dict]) -> None:
    """临时文件 + 原子替换写回收站，避免写一半把既有归档截断成半个 JSON。"""
    path = _correction_archive_path()
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".corrections-archive-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"batches": batches}, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                logger.warning("清理回收站临时文件失败：%s", tmp_path, exc_info=True)


def _archive_corrections(records: List[dict], reason: str) -> dict:
    """把即将删除的纠错记录存进回收站，返回本次归档批次。"""
    batches = _load_correction_archive()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    batch = {
        "batch_id": f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}",
        "deleted_at": now,
        "reason": reason,
        "count": len(records),
        "restored_at": None,
        "records": copy.deepcopy(records),
    }
    batches.append(batch)
    _write_correction_archive(batches)
    return batch


def _correction_key(record: dict) -> tuple:
    """还原时的去重口径：同一提问 + 同一正确 SQL 视为同一条经验。"""
    return ((record.get("question") or "").strip().lower(),
            (record.get("corrected_sql") or "").strip())


def _persist_corrections() -> bool:
    """把内存中的纠错集合落盘，返回是否确认写盘成功。"""
    save = getattr(user_memory, "_save", None)
    if not callable(save):
        raise RuntimeError("用户记忆模型不支持持久化，无法确认还原结果已落盘")
    return save() is not False

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
def ask_question(request: AskRequest, http_request: Request = None):
    """
    接收自然语言问题，执行 NL2SQL 全链路问数 Agent 动作。

    全链路追溯：platform_gateway 通过 ``x-trace-id`` 头透传它签发的 trace_id，
    这里把它接进 ask()，让同一次外部请求在网关段与问数段用的是同一个 trace_id
    （否则每次 HTTP 请求都会就地新签一个，链路在子系统边界上断掉）。
    头缺失或不合规时由 ask() 自行签发，不影响既有调用方。
    """
    from app.service.run_trace import TRACE_HEADER, normalize_trace_id

    try:
        if request.data_source:
            select_data_source(SelectDataSourceRequest(id=request.data_source))
        inbound_trace = None
        if http_request is not None:
            inbound_trace = normalize_trace_id(
                http_request.headers.get(TRACE_HEADER))
        res = ask_agent.ask(
            question=request.question,
            dialect=request.dialect,
            user=request.user,
            role=request.role,
            trace_id=inbound_trace
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
    删除特定的纠错经验并重载向量索引（软删除：记录先进回收站，可还原）
    """
    matched = [record for record in (user_memory.get_error_corrections() or [])
               if (record.get("question") or "").strip().lower() == question.strip().lower()]
    if not matched:
        raise HTTPException(status_code=404, detail="未找到该提问对应的纠错记录")

    try:
        batch = _archive_corrections(matched, reason=f"delete:{question}")
    except Exception as e:
        logger.error("纠错经验软删除归档失败，已放弃删除：question=%s", question, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"软删除归档失败，已放弃删除以免记录不可恢复: {e}") from None

    try:
        from app.service.vector_service import vector_service
        deleted = user_memory.delete_error_correction(question=question)
        if not deleted:
            raise HTTPException(status_code=404, detail="未找到该提问对应的纠错记录")
        vector_service.ingest_error_corrections()
        return {"status": "success", "message": f"成功删除关于 '{question}' 的纠错记录！",
                "archived_count": batch["count"], "batch_id": batch["batch_id"],
                "restore_hint": f"POST /api/chat/corrections/restore?batch_id={batch['batch_id']}"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除纠错经验失败: {str(e)}")


@router.delete("/corrections/clear")
def clear_all_error_corrections(
    confirm: bool = Query(False, description="必须显式传 confirm=true 才会真正清空；否则本接口不做任何改动"),
    reason: str = Query("", description="可选：本次清空的原因，写入回收站归档便于追溯"),
):
    """
    清空全部纠错经验（软删除 + 二次确认）。

    - 不带 confirm=true：返回 400 并告知将影响多少条，**不做任何改动**；
    - 带 confirm=true：先把全部记录写入回收站归档，再清空并重建向量库；
      之后可用 POST /chat/corrections/restore 还原。
    """
    records = list(user_memory.get_error_corrections() or [])
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail=(f"危险操作已阻止：清空将影响 {len(records)} 条纠错经验，本次未做任何改动。"
                    f"确认要清空请重新调用并显式带上 confirm=true"
                    f"（记录会先进回收站，可用 POST /api/chat/corrections/restore 还原）。"))

    if not records:
        return {"status": "success", "message": "当前没有纠错记录，无需清空。",
                "archived_count": 0, "batch_id": None}

    try:
        batch = _archive_corrections(records, reason=reason or "clear_all")
    except Exception as e:
        logger.error("纠错经验清空前归档失败，已放弃清空", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"软删除归档失败，已放弃清空以免记录不可恢复: {e}") from None

    try:
        from app.service.vector_service import vector_service
        user_memory.clear_error_corrections()
        vector_service.ingest_error_corrections()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"清空纠错经验失败: {str(e)}")

    return {"status": "success",
            "message": f"已清空 {batch['count']} 条纠错经验（软删除，可还原）。",
            "archived_count": batch["count"], "batch_id": batch["batch_id"],
            "restore_hint": f"POST /api/chat/corrections/restore?batch_id={batch['batch_id']}"}


@router.get("/corrections/archive")
def list_correction_archive():
    """
    查看纠错经验回收站：每一次删除/清空的批次、时间、原因与被删记录。
    """
    try:
        batches = _load_correction_archive()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取纠错回收站失败: {e}") from None
    return {"archive_path": _correction_archive_path(),
            "batch_count": len(batches),
            "batches": batches}


@router.post("/corrections/restore")
def restore_error_corrections(
    batch_id: str = Query("", description="要还原的归档批次 ID；留空表示还原最近一次删除"),
):
    """
    从回收站还原被删除的纠错经验（按「提问 + 正确 SQL」去重，重复执行安全）。
    """
    try:
        batches = _load_correction_archive()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取纠错回收站失败: {e}") from None
    if not batches:
        raise HTTPException(status_code=404, detail="纠错回收站为空，没有可还原的批次")

    if batch_id:
        batch = next((b for b in batches if b.get("batch_id") == batch_id), None)
        if batch is None:
            raise HTTPException(status_code=404, detail=f"未找到归档批次 '{batch_id}'")
    else:
        batch = batches[-1]

    existing = {_correction_key(record) for record in (user_memory.get_error_corrections() or [])}
    restored = 0
    for record in batch.get("records", []):
        if not isinstance(record, dict) or _correction_key(record) in existing:
            continue
        user_memory.error_corrections.append(copy.deepcopy(record))
        existing.add(_correction_key(record))
        restored += 1

    persisted = True
    if restored:
        try:
            persisted = _persist_corrections()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"还原纠错经验落盘失败: {e}") from None

    index_refreshed = True
    index_error = None
    try:
        from app.service.vector_service import vector_service
        vector_service.ingest_error_corrections()
    except Exception as e:
        # 记录已经还原到记忆里，向量索引没刷成功不应该把还原结果报成失败。
        index_refreshed = False
        index_error = str(e)
        logger.error("还原纠错经验后重建向量索引失败", exc_info=True)

    batch["restored_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        _write_correction_archive(batches)
    except Exception:
        logger.warning("回写回收站还原标记失败，不影响本次还原结果", exc_info=True)

    return {"status": "success",
            "message": f"已从批次 {batch.get('batch_id')} 还原 {restored} 条纠错经验"
                       f"（批次内共 {batch.get('count', len(batch.get('records', [])))} 条，其余为已存在的重复记录）。",
            "batch_id": batch.get("batch_id"), "restored_count": restored,
            "persisted": persisted, "vector_index_refreshed": index_refreshed,
            "vector_index_error": index_error}


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
    对指定表执行 AI 数据画像与元数据自动补全 (Profiling & Enrichment)。

    table_name 必须是当前数据源中已注册的物理表/视图：非法标识符或未注册表名
    在采样取数之前就会被拒绝（400），不会有任何 SQL 落到数据库上。
    """
    from app.service.metadata_enricher import UnsafeProfilingRequest, metadata_enricher
    try:
        return metadata_enricher.enrich_metadata(table_name)
    except UnsafeProfilingRequest as denied:
        logger.warning("拒绝非法的元数据画像请求: table_name=%r, 原因=%s", table_name, denied)
        raise HTTPException(status_code=400, detail=str(denied)) from None
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
