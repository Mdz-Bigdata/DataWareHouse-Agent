import json
import logging
import pandas as pd
from io import BytesIO, StringIO
from datetime import datetime
from typing import Optional, Dict, Any, List
from app.core.orm import AsyncSessionLocal
from app.models.audit import AgentExecutionTrace, AgentExecutionHistory
from sqlalchemy import select, and_
from datetime import timedelta

logger = logging.getLogger(__name__)

class ExportService:
    """
    Service to handle data export from agent execution traces.
    """

    @staticmethod
    async def get_trace_data(trace_id: str, step_number: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """
        终极数据检索：支持父子 Trace 递归搜索，并包含 AI 总结
        """
        async with AsyncSessionLocal() as session:
            # 1. 获取基本历史信息 (问题与总结)
            stmt_h = select(AgentExecutionHistory).where(AgentExecutionHistory.trace_id == trace_id)
            res_h = await session.execute(stmt_h)
            curr_h = res_h.scalars().first()
            
            summary_info = {
                "query": curr_h.query if curr_h else "未知问题",
                "summary": curr_h.summary if curr_h else "无总结",
                "trace_id": trace_id
            }

            # 2. 尝试直接搜索当前 Trace 中的表格数据
            data = await ExportService._search_in_trace(session, trace_id, step_number)
            
            # 3. 如果当前 Trace 没数据，寻找关联 Trace (处理多智能体协同)
            if not data and curr_h:
                stmt_related = select(AgentExecutionHistory.trace_id).where(
                    and_(
                        AgentExecutionHistory.user_id == curr_h.user_id,
                        AgentExecutionHistory.created_at >= curr_h.created_at - timedelta(seconds=15),
                        AgentExecutionHistory.created_at <= curr_h.created_at + timedelta(seconds=15),
                        AgentExecutionHistory.trace_id != trace_id
                    )
                )
                res_related = await session.execute(stmt_related)
                for r_id in res_related.scalars().all():
                    data = await ExportService._search_in_trace(session, r_id)
                    if data: break
            
            if not data and not curr_h:
                return None
                
            return {
                "table": data,
                "meta": summary_info
            }

    @staticmethod
    async def _search_in_trace(session, trace_id: str, step_number: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """内部搜索逻辑：在特定 Trace 中深度探测数据"""
        stmt = select(AgentExecutionTrace).where(AgentExecutionTrace.trace_id == trace_id)
        if step_number is not None:
            stmt = stmt.where(AgentExecutionTrace.step_number == step_number)
        
        stmt = stmt.order_by(AgentExecutionTrace.step_number.desc())
        result = await session.execute(stmt)
        traces = result.scalars().all()
        
        for trace in traces:
            if not trace.tool_output: continue
            try:
                out = trace.tool_output
                if isinstance(out, str):
                    try: out = json.loads(out)
                    except: continue
                
                # 探测数据
                target = out
                if isinstance(out, dict) and "raw" in out:
                    raw_val = out["raw"]
                    if isinstance(raw_val, str):
                        try: target = json.loads(raw_val)
                        except: target = raw_val
                    else:
                        target = raw_val
                
                if isinstance(target, dict) and (target.get("items") or target.get("columns")):
                    return target
                if isinstance(target, list) and len(target) > 0:
                    return target
            except:
                continue
        return None

    @staticmethod
    def json_to_csv(payload: Any) -> str:
        """
        Converts tool output to CSV string with AI summary header.
        """
        if not payload: return ""
        # 兼容性处理：支持直接传入数据或 payload 结构
        if isinstance(payload, dict) and ("table" in payload or "meta" in payload):
            data = payload.get("table")
            meta = payload.get("meta", {})
        else:
            data = payload
            meta = {}
        
        # 1. 构造文件头 (AI 总结)
        header_io = StringIO()
        if meta:
            summary = meta.get('summary', '').replace('\n', ' ')
            header_io.write(f"用户问题: {meta.get('query', '')}\n")
            header_io.write(f"AI 分析结论: {summary}\n")
            header_io.write("-" * 20 + "\n")
        
        # 2. 构造数据体
        df = ExportService._to_dataframe(data)
        if df is None or df.empty:
            return header_io.getvalue()
            
        df.to_csv(header_io, index=False, encoding='utf-8-sig')
        return header_io.getvalue()

    @staticmethod
    def json_to_excel(payload: Any) -> bytes:
        """
        Converts tool output to Excel with multiple sheets.
        """
        if not payload: return b""
        if isinstance(payload, dict) and ("table" in payload or "meta" in payload):
            data = payload.get("table")
            meta = payload.get("meta", {})
        else:
            data = payload
            meta = {}
        
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            # Sheet 1: 原始数据
            df = ExportService._to_dataframe(data)
            has_data_sheet = False
            if df is not None and not df.empty:
                df.to_excel(writer, index=False, sheet_name='数据详情')
                has_data_sheet = True
            
            # Sheet 2: AI 分析报告
            summary_data = [
                ["项目", "内容"],
                ["用户提问", meta.get("query", "N/A")],
                ["AI 总结", meta.get("summary", "N/A")],
                ["导出时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
                ["Trace ID", meta.get("trace_id", "N/A")]
            ]
            summary_df = pd.DataFrame(summary_data[1:], columns=summary_data[0])
            summary_df.to_excel(writer, index=False, sheet_name='分析概览')
            
            # --- 设置 Excel 排版样式 (Analysis Overview Layout) ---
            worksheet = writer.sheets['分析概览']
            from openpyxl.styles import Alignment
            
            # 设置列宽
            worksheet.column_dimensions['A'].width = 15
            worksheet.column_dimensions['B'].width = 80
            
            # 自动换行及置顶对齐
            for row in worksheet.iter_rows(min_row=1, max_row=worksheet.max_row, min_col=1, max_col=2):
                for cell in row:
                    cell.alignment = Alignment(wrap_text=True, vertical='top')
            
            # 兜底：如果没有数据详情，也要确保有一个可见 Sheet
            if not has_data_sheet:
                # 已经有了 '分析概览'，openpyxl 会正常保存
                pass
            
        return output.getvalue()

    @staticmethod
    def _to_dataframe(data: Any) -> Optional[pd.DataFrame]:
        """内部工具：将各种格式转换为 DataFrame"""
        if not data: return None
        
        df = None
        cols = []
        
        if isinstance(data, dict):
            items = data.get("items")
            raw_cols = data.get("columns")
            if isinstance(raw_cols, list):
                for c in raw_cols:
                    cols.append(c.get("name") if isinstance(c, dict) else str(c))
            
            if items and isinstance(items, list):
                if len(items) > 0 and isinstance(items[0], dict):
                    df = pd.DataFrame(items)
                else:
                    df = pd.DataFrame(items, columns=cols if cols and len(cols) == len(items[0]) else None)
            elif cols:
                df = pd.DataFrame(columns=cols)
        elif isinstance(data, list) and len(data) > 0:
            if isinstance(data[0], dict):
                df = pd.DataFrame(data)
            else:
                df = pd.DataFrame(data)
            
        if df is not None and not df.empty and cols:
            existing_cols = [c for c in cols if c in df.columns]
            if existing_cols: df = df[existing_cols]
            
        return df
