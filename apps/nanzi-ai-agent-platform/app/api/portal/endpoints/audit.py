from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc, or_, case
from typing import Optional, List, Dict, Any
from app.core.dependencies import require_api_key, require_admin
from app.core.orm import get_db_session
from app.models.audit import AccessLog, AgentExecutionTrace
from app.services.audit_service import AuditService
from datetime import datetime
import json
import csv
import io

router = APIRouter()

def is_admin(user: dict) -> bool:
    """Check if user has admin role"""
    if user.get("role") == "admin": return True
    perms = user.get("permissions")
    if not perms: return False
    if isinstance(perms, str):
         try: perms = json.loads(perms)
         except Exception: return False
    return perms.get("role") == "admin"

@router.get("/features")
async def get_audit_features(
    user: dict = Depends(require_api_key)
):
    """Get list of available feature points for filtering"""
    return sorted(list(set(AuditService.FEATURE_MAP.values())))

@router.get("/logs/export")
async def export_logs(
    format: str = Query("csv", pattern="^(csv|json)$"),
    user_name: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    method: Optional[str] = None,
    status_code: Optional[int] = None,
    min_status: Optional[int] = None,
    max_status: Optional[int] = None,
    endpoint: Optional[str] = None,
    client_ip: Optional[str] = None,
    feature_name: Optional[str] = None,
    user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    admin_flag = is_admin(user)
    target_user = user_name if admin_flag else user["user_name"]
    stmt = select(AccessLog).order_by(desc(AccessLog.created_at)).limit(10000)
    if target_user: stmt = stmt.where(AccessLog.user_name == target_user)
    if start_time: 
        stmt = stmt.where(AccessLog.created_at >= start_time)
    else:
        from datetime import timedelta
        default_start = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")
        stmt = stmt.where(AccessLog.created_at >= default_start)
    if end_time: stmt = stmt.where(AccessLog.created_at <= end_time)
    if method: stmt = stmt.where(AccessLog.method == method)
    if status_code: stmt = stmt.where(AccessLog.status_code == status_code)
    if min_status: stmt = stmt.where(AccessLog.status_code >= min_status)
    if max_status: stmt = stmt.where(AccessLog.status_code <= max_status)
    if endpoint: stmt = stmt.where(AccessLog.endpoint.like(f"%{endpoint}%"))
    if client_ip: stmt = stmt.where(AccessLog.client_ip.like(f"%{client_ip}%"))
    if feature_name: stmt = stmt.where(AccessLog.feature_name == feature_name)
    
    result = await db.execute(stmt)
    rows = result.scalars().all()
    if format == "csv":
        output = io.StringIO()
        fieldnames = ["id", "trace_id", "user_name", "feature_name", "endpoint", "method", "status_code", "process_time_ms", "client_ip", "created_at"]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({"id": row.id, "trace_id": row.trace_id, "user_name": row.user_name, "feature_name": row.feature_name, "endpoint": row.endpoint, "method": row.method, "status_code": row.status_code, "process_time_ms": row.process_time_ms, "client_ip": row.client_ip, "created_at": row.created_at})
        csv_content = output.getvalue()
        output.close()
        return StreamingResponse(iter([csv_content]), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=audit_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"})
    else:
        items = [{"id": r.id, "trace_id": r.trace_id, "user_name": r.user_name, "feature_name": r.feature_name, "endpoint": r.endpoint, "method": r.method, "status_code": r.status_code, "process_time_ms": r.process_time_ms, "client_ip": r.client_ip, "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]
        return StreamingResponse(iter([json.dumps(items, ensure_ascii=False, default=str)]), media_type="application/json", headers={"Content-Disposition": f"attachment; filename=audit_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"})

@router.get("/logs")
async def get_audit_logs(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user_name: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    method: Optional[str] = None,
    status_code: Optional[int] = None,
    min_status: Optional[int] = None,
    max_status: Optional[int] = None,
    endpoint: Optional[str] = None,
    client_ip: Optional[str] = None,
    feature_name: Optional[str] = None,
    include_stats: bool = False,
    user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    admin_flag = is_admin(user)
    target_user = user_name if admin_flag else user["user_name"]
    query = select(AccessLog)
    if target_user: query = query.where(AccessLog.user_name == target_user)
    if start_time:
        query = query.where(AccessLog.created_at >= start_time)
    else:
        from datetime import timedelta
        default_start = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")
        query = query.where(AccessLog.created_at >= default_start)
    if end_time: query = query.where(AccessLog.created_at <= end_time)
    if method: query = query.where(AccessLog.method == method)
    if status_code: query = query.where(AccessLog.status_code == status_code)
    if min_status: query = query.where(AccessLog.status_code >= min_status)
    if max_status: query = query.where(AccessLog.status_code <= max_status)
    if endpoint: query = query.where(AccessLog.endpoint.like(f"%{endpoint}%"))
    if client_ip: query = query.where(AccessLog.client_ip.like(f"%{client_ip}%"))
    if feature_name: query = query.where(AccessLog.feature_name == feature_name)
        
    count_stmt = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_stmt)).scalar()
    statistics = None
    if include_stats:
        stats_query = select(func.count().label("total"), func.sum(case(((AccessLog.status_code >= 200) & (AccessLog.status_code < 300), 1), else_=0)).label("success"), func.sum(case((AccessLog.status_code >= 400, 1), else_=0)).label("error"), func.avg(AccessLog.process_time_ms).label("avg_time"))
        if target_user: stats_query = stats_query.where(AccessLog.user_name == target_user)
        if start_time:
            stats_query = stats_query.where(AccessLog.created_at >= start_time)
        else:
            from datetime import timedelta
            default_start = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")
            stats_query = stats_query.where(AccessLog.created_at >= default_start)
        if end_time: stats_query = stats_query.where(AccessLog.created_at <= end_time)
        if method: stats_query = stats_query.where(AccessLog.method == method)
        if status_code: stats_query = stats_query.where(AccessLog.status_code == status_code)
        if min_status: stats_query = stats_query.where(AccessLog.status_code >= min_status)
        if max_status: stats_query = stats_query.where(AccessLog.status_code <= max_status)
        if endpoint: stats_query = stats_query.where(AccessLog.endpoint.like(f"%{endpoint}%"))
        if client_ip: stats_query = stats_query.where(AccessLog.client_ip.like(f"%{client_ip}%"))
        if feature_name: stats_query = stats_query.where(AccessLog.feature_name == feature_name)
        stat_res = (await db.execute(stats_query)).one()
        total_req = stat_res.total or 0
        statistics = {"total_requests": total_req, "success_count": stat_res.success or 0, "error_count": stat_res.error or 0, "success_rate": round(((stat_res.success or 0) / total_req * 100), 2) if total_req > 0 else 0, "avg_response_time": round(float(stat_res.avg_time) if stat_res.avg_time else 0, 2)}

    query = query.order_by(desc(AccessLog.created_at)).offset((page - 1) * size).limit(size)
    rows = (await db.execute(query)).scalars().all()
    items = [{"id": r.id, "trace_id": r.trace_id, "user_name": r.user_name, "feature_name": r.feature_name, "endpoint": r.endpoint, "method": r.method, "status_code": r.status_code, "process_time_ms": r.process_time_ms, "client_ip": r.client_ip, "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]
    result = {"total": total, "page": page, "size": size, "items": items}
    if statistics: result["statistics"] = statistics
    return result

@router.get("/logs/{log_id}")
async def get_log_detail(
    log_id: int, 
    created_at: Optional[str] = Query(None, description="日志产生的大致或精确时间"),
    user: dict = Depends(require_api_key), 
    db: AsyncSession = Depends(get_db_session)
):
    stmt = select(AccessLog).where(AccessLog.id == log_id)
    if created_at:
        from datetime import datetime, timedelta
        try:
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            stmt = stmt.where(
                AccessLog.created_at >= dt - timedelta(seconds=2),
                AccessLog.created_at <= dt + timedelta(seconds=2)
            )
        except ValueError:
            stmt = stmt.where(AccessLog.created_at == created_at)
    if not is_admin(user): stmt = stmt.where(AccessLog.user_name == user["user_name"])
    row = (await db.execute(stmt)).scalar_one_or_none()
    if not row: raise HTTPException(status_code=404, detail="Log not found")
    log_detail = {"id": row.id, "trace_id": row.trace_id, "user_name": row.user_name, "feature_name": row.feature_name, "endpoint": row.endpoint, "method": row.method, "status_code": row.status_code, "process_time_ms": row.process_time_ms, "client_ip": row.client_ip, "error_message": row.error_message, "created_at": row.created_at.isoformat() if row.created_at else None}
    
    # Ensure request_params key exists
    log_detail["request_params"] = None
    if row.request_params:
        try: log_detail["request_params"] = json.loads(row.request_params)
        except: log_detail["request_params"] = row.request_params
        
    # Ensure response_body key exists
    log_detail["response_body"] = None
    if row.response_body:
        try: log_detail["response_body"] = json.loads(row.response_body)
        except: log_detail["response_body"] = row.response_body
        
    return log_detail

@router.get("/traces/{trace_id}")
async def get_execution_trace(trace_id: str, user: dict = Depends(require_api_key), db: AsyncSession = Depends(get_db_session)):
    stmt = select(AgentExecutionTrace).where(AgentExecutionTrace.trace_id == trace_id).order_by(AgentExecutionTrace.step_number)
    rows = (await db.execute(stmt)).scalars().all()
    if not rows: raise HTTPException(status_code=404, detail="Trace not found")
    steps = []
    for row in rows:
        step = {"id": row.id, "step_number": row.step_number, "event_type": row.event_type, "agent_name": row.agent_name, "tool_name": row.tool_name, "status": row.status, "error_message": row.error_message, "execution_time_ms": row.execution_time_ms, "created_at": row.created_at.isoformat() if row.created_at else None}
        if row.tool_input: step["tool_input"] = row.tool_input if isinstance(row.tool_input, (dict, list)) else json.loads(str(row.tool_input))
        if row.tool_output: step["tool_output"] = row.tool_output if isinstance(row.tool_output, (dict, list)) else json.loads(str(row.tool_output))
        steps.append(step)
    return {"trace_id": trace_id, "steps": steps}

@router.get("/traces/{trace_id}/spans")
async def get_execution_trace_spans(
    trace_id: str,
    user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    stmt = select(AgentExecutionTrace).where(AgentExecutionTrace.trace_id == trace_id).order_by(AgentExecutionTrace.step_number)
    rows = (await db.execute(stmt)).scalars().all()
    if not rows:
        raise HTTPException(status_code=404, detail="Trace not found")

    span_list = []
    span_map = {}
    for row in rows:
        tool_input = row.tool_input
        if tool_input and isinstance(tool_input, str):
            try: tool_input = json.loads(tool_input)
            except Exception: pass
        tool_output = row.tool_output
        if tool_output and isinstance(tool_output, str):
            try: tool_output = json.loads(tool_output)
            except Exception: pass
        meta_info = getattr(row, "meta_info", None)
        if meta_info and isinstance(meta_info, str):
            try: meta_info = json.loads(meta_info)
            except Exception: pass

        span_data = {
            "id": row.id,
            "step_number": row.step_number,
            "event_type": row.event_type,
            "agent_name": row.agent_name,
            "tool_name": row.tool_name,
            "tool_input": tool_input,
            "tool_output": tool_output,
            "execution_time_ms": row.execution_time_ms,
            "status": row.status,
            "error_message": row.error_message,
            "model": row.model,
            "temperature": row.temperature,
            "prompt_tokens": row.prompt_tokens,
            "completion_tokens": row.completion_tokens,
            "total_tokens": row.total_tokens,
            "meta_info": meta_info,
            "span_id": getattr(row, "span_id", None),
            "parent_span_id": getattr(row, "parent_span_id", None),
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "children": []
        }
        span_list.append(span_data)
        s_id = getattr(row, "span_id", None)
        if s_id:
            span_map[s_id] = span_data

    roots = []
    for span in span_list:
        parent_id = span["parent_span_id"]
        if parent_id and parent_id in span_map:
            span_map[parent_id]["children"].append(span)
        else:
            roots.append(span)

    return {"trace_id": trace_id, "spans": roots}
