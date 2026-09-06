# -*- coding: utf-8 -*-
import json
import logging
from typing import Any, Dict, List, Optional
import pandas as pd
from app.service.db_service import db_service

logger = logging.getLogger(__name__)

# =====================================================================
# AI 驱动的元数据自动补全与特征画像挖掘服务 (Metadata Enricher)
# 对应文章《AI驱动的元数据补全技术方案——让机器帮元数据"填空"》
# 核心解决：企业数仓中大量新接入表缺乏中文注释、缺乏指标口径定义、
# 缺乏枚举字典，导致 LLM Schema Linking 产生严重幻觉与静默错误的问题。
# =====================================================================

class ColumnProfile:
    def __init__(self, column_name: str, dtype: str, total_count: int, null_count: int, distinct_count: int, sample_values: List[Any], min_val: Any = None, max_val: Any = None):
        self.column_name = column_name
        self.dtype = dtype
        self.total_count = total_count
        self.null_count = null_count
        self.distinct_count = distinct_count
        self.sample_values = sample_values
        self.min_val = min_val
        self.max_val = max_val

    def to_dict(self) -> Dict[str, Any]:
        return {
            "column_name": self.column_name,
            "dtype": self.dtype,
            "null_ratio": round(self.null_count / max(self.total_count, 1), 4),
            "distinct_count": self.distinct_count,
            "sample_values": self.sample_values[:5],
            "min": str(self.min_val) if self.min_val is not None else None,
            "max": str(self.max_val) if self.max_val is not None else None
        }


class MetadataEnricher:
    def __init__(self):
        # 常见字段名语义知识库（启发式增强基座）
        self.semantic_glossary = {
            "gmv": {"display_name": "成交总金额", "metric_type": "sum", "unit": "元", "aliases": ["销售额", "交易额", "流水", "营收"]},
            "refund_amount": {"display_name": "退款金额", "metric_type": "sum", "unit": "元", "aliases": ["退款额", "退单金额"]},
            "order_count": {"display_name": "订单量", "metric_type": "sum", "unit": "笔", "aliases": ["单量", "订单数", "下单量"]},
            "region_name": {"display_name": "区域名称", "dimension_type": "categorical", "aliases": ["大区", "区域", "地区", "省区"]},
            "category_name": {"display_name": "品类名称", "dimension_type": "categorical", "aliases": ["品类", "类目", "行业类目"]},
            "goods_name": {"display_name": "商品名称", "dimension_type": "categorical", "aliases": ["商品", "货物", "货品"]},
            "title": {"display_name": "内容标题", "dimension_type": "text", "aliases": ["文章标题", "文章名", "标题"]},
            "source_platform": {"display_name": "来源平台", "dimension_type": "categorical", "aliases": ["渠道", "平台", "来源渠道"]},
            "status": {"display_name": "状态标识", "dimension_type": "enum", "aliases": ["处理状态", "订单状态", "发布状态"]},
            "dt": {"display_name": "业务日期", "dimension_type": "time", "aliases": ["日期", "时间", "数据日期", "天分区"]}
        }

    def profile_table(self, table_name: str, sample_size: int = 1000) -> Dict[str, Any]:
        """
        对物理表进行自动数据画像（Profiling）采样分析
        """
        try:
            query = f"SELECT * FROM {table_name} LIMIT {sample_size}"
            df = db_service.execute_query(query)
        except Exception as e:
            logger.warning("对表 %s 采样分析失败: %s", table_name, str(e))
            return {"table_name": table_name, "error": str(e), "columns": []}

        total_rows = len(df)
        columns_profile = []

        for col in df.columns:
            series = df[col]
            null_cnt = int(series.isna().sum())
            distinct_vals = series.dropna().unique()
            distinct_cnt = len(distinct_vals)
            samples = distinct_vals[:5].tolist()

            min_val = None
            max_val = None
            if pd.api.types.is_numeric_dtype(series):
                min_val = series.min() if not pd.isna(series.min()) else None
                max_val = series.max() if not pd.isna(series.max()) else None
            elif pd.api.types.is_datetime64_any_dtype(series):
                min_val = str(series.min())
                max_val = str(series.max())

            profile = ColumnProfile(
                column_name=str(col),
                dtype=str(series.dtype),
                total_count=total_rows,
                null_count=null_cnt,
                distinct_count=distinct_cnt,
                sample_values=samples,
                min_val=min_val,
                max_val=max_val
            )
            columns_profile.append(profile.to_dict())

        return {
            "table_name": table_name,
            "total_sampled_rows": total_rows,
            "columns": columns_profile
        }

    def enrich_metadata(self, table_name: str, table_profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        基于采样画像与语义库，补全表及字段业务描述与口径定义
        """
        if not table_profile or "columns" not in table_profile:
            table_profile = self.profile_table(table_name)

        inferred_metrics = []
        inferred_dimensions = []

        # 启发式规则推断表业务域
        domain = "公共数仓"
        if "order" in table_name or "trade" in table_name or "pay" in table_name:
            domain = "交易域"
        elif "article" in table_name or "content" in table_name or "book" in table_name:
            domain = "内容与媒体域"
        elif "user" in table_name or "member" in table_name:
            domain = "用户与会员域"
        elif "region" in table_name or "goods" in table_name or "dim" in table_name:
            domain = "基础主数据域"

        table_desc = f"{domain}核心物理数据表 `{table_name}`"

        for col_info in table_profile.get("columns", []):
            col_name = col_info["column_name"]
            dtype = col_info["dtype"]
            distinct_cnt = col_info["distinct_count"]
            samples = col_info["sample_values"]

            # 匹配已知行业语义词库
            glossary_item = self.semantic_glossary.get(col_name.lower())

            # 1. 指标推测 (数值型且高基数或累加字段)
            is_numeric = any(t in dtype.lower() for t in ["int", "float", "double", "real", "decimal"])
            is_id = col_name.lower().endswith("_id") or col_name.lower() == "id"

            if is_numeric and not is_id and (glossary_item or any(kw in col_name.lower() for kw in ["amount", "count", "num", "gmv", "cnt", "price", "fee", "cost"])):
                m_name = f"total_{col_name}" if not col_name.startswith("total_") else col_name
                aliases = glossary_item["aliases"] if glossary_item else [col_name]
                desc = glossary_item["display_name"] if glossary_item else f"累计{col_name}"
                inferred_metrics.append({
                    "name": m_name,
                    "field": col_name,
                    "table": table_name,
                    "display_name": desc,
                    "calculation": f"SUM({table_name}.{col_name})",
                    "default_agg": "SUM",
                    "aliases": aliases
                })

            # 2. 维度推测
            else:
                aliases = glossary_item["aliases"] if glossary_item else [col_name]
                desc = glossary_item["display_name"] if glossary_item else f"{col_name}维度"
                # 如果是低基数枚举，记录候选枚举值
                enum_values = [str(s) for s in samples if s is not None] if distinct_cnt <= 20 else []
                inferred_dimensions.append({
                    "name": col_name,
                    "table": table_name,
                    "display_name": desc,
                    "aliases": aliases,
                    "value_range": enum_values
                })

        return {
            "table_name": table_name,
            "domain": domain,
            "description": table_desc,
            "metrics": inferred_metrics,
            "dimensions": inferred_dimensions
        }

    def get_available_tables(self) -> List[str]:
        """
        获取当前数仓中可用于 Profiling 和问数的物理表清单
        """
        if db_service.real_engine is not None:
            from sqlalchemy import inspect
            inspector = inspect(db_service.real_engine)
            schemas = getattr(db_service, "query_schemas", None) or [None]
            return sorted({name for schema in schemas
                           for name in inspector.get_table_names(schema=schema)})
        try:
            # 优先从 db_service 探查
            cur = db_service.conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
            rows = cur.fetchall()
            tables = [r[0] for r in rows if r[0]]
            if tables:
                return sorted(tables)
        except Exception as e:
            logger.warning("从数据库探查表列表失败，降级使用语义层配置: %s", e)
        
        # 降级使用语义层注册的表
        from app.service.semantic_layer import semantic_layer
        tables = set()
        for m in semantic_layer.metrics.values():
            if m.table:
                tables.add(m.table)
        for d in semantic_layer.dimensions.values():
            if d.table:
                tables.add(d.table)
        return sorted(list(tables)) if tables else ["dws_trade_order_daily", "dim_region", "articles", "article_history"]

# 单例导出
metadata_enricher = MetadataEnricher()
