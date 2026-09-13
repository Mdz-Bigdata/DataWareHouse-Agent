# -*- coding: utf-8 -*-
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd
from sqlglot import exp
from app.service.db_service import db_service

logger = logging.getLogger(__name__)

# =====================================================================
# 取数收口（安全边界）
# ---------------------------------------------------------------------
# Profiling 采样是本模块唯一会真正落到物理库上的动作。历史实现直接用
# f-string 把外部传入的 table_name 拼进 `SELECT * FROM {table_name}`，
# 再直连 db_service.execute_query —— 绕开了仓库里全部 SQL 准入网闸，
# 等于给 `/chat/metadata/enrich?table_name=...` 开了一条任意 SQL 通道。
#
# 现在任何表名都必须依次通过三道关卡，缺一不可：
#   1. 标识符形状校验：非法字符一律「拒绝」，绝不做转义修复；
#   2. 注册表白名单：只认当前数据源里真实存在的表/视图（服务端清单），
#      拼进 SQL 的永远是白名单里的规范写法，而不是用户输入；
#   3. 统一安全出口：SQL 交给 guardrail.check_sql 审计后才执行。
# =====================================================================

# 字母或下划线开头（含中文等 Unicode 字母），其余为字母/数字/下划线/$。
# 引号、分号、空白、括号、减号、注释符都无法匹配，从而在第 1 关被拒绝。
_IDENTIFIER_RE = re.compile(r"^[^\W\d][\w$]{0,62}$", re.UNICODE)

DEFAULT_SAMPLE_SIZE = 1000
MAX_SAMPLE_SIZE = 10000


class UnsafeProfilingRequest(ValueError):
    """采样画像请求未通过安全校验，拒绝进入取数通道。"""


class UnsafeTableNameError(UnsafeProfilingRequest):
    """表名非法或未注册：不是已知物理表/视图，一律拒绝。"""


class UnsafeSampleSizeError(UnsafeProfilingRequest):
    """采样行数非法：无法安全地作为 LIMIT 使用。"""

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

    # ------------------------------------------------------------------
    # 安全边界：表名白名单 / 标识符校验 / 统一安全出口
    # ------------------------------------------------------------------
    def _known_schemas(self) -> List[str]:
        """当前数据源实际用于解析表名的 schema 清单（服务端事实，非用户输入）。"""
        schemas = getattr(db_service, "query_schemas", None) or []
        return [s for s in schemas if isinstance(s, str) and s]

    def registered_relations(self) -> List[str]:
        """
        采样画像白名单：当前数据源中真实存在的物理表 + 视图。
        视图一并纳入是为了保持既有行为（此前可以对视图做画像）。
        """
        names = set(self.get_available_tables())
        try:
            if db_service.real_engine is not None:
                from sqlalchemy import inspect
                inspector = inspect(db_service.real_engine)
                for schema in (self._known_schemas() or [None]):
                    names.update(inspector.get_view_names(schema=schema))
            elif getattr(db_service, "conn", None) is not None:
                cur = db_service.conn.cursor()
                cur.execute("SELECT name FROM sqlite_master WHERE type='view' AND name NOT LIKE 'sqlite_%';")
                names.update(row[0] for row in cur.fetchall() if row and row[0])
        except Exception as e:
            logger.warning("探查视图清单失败，白名单仅包含物理表: %s", e)
        return sorted(n for n in names if isinstance(n, str) and n)

    def resolve_registered_table(self, table_name: Any) -> Tuple[Optional[str], str]:
        """
        把外部传入的表名收口成「已注册物理关系」的规范写法。

        校验不通过一律抛 UnsafeTableNameError —— 绝不尝试转义/清洗后放行，
        因为清洗过的注入串依然是注入串。返回 (schema, table)，其中 table
        取自服务端白名单的原始拼写，而不是调用方给的字符串。
        """
        if not isinstance(table_name, str) or not table_name.strip():
            raise UnsafeTableNameError("安全拦截: 表名必须是非空字符串。")

        raw = table_name.strip()
        parts = raw.split(".")
        if len(parts) > 2:
            raise UnsafeTableNameError(
                f"安全拦截: 表名 '{raw}' 层级非法，只接受 `table` 或 `schema.table`。")
        if not all(_IDENTIFIER_RE.match(part or "") for part in parts):
            raise UnsafeTableNameError(
                f"安全拦截: 表名 '{raw}' 含有非法标识符字符（仅允许字母/数字/下划线/$，"
                f"且不能以数字开头），已拒绝执行采样取数。")

        schema = parts[0] if len(parts) == 2 else None
        requested = parts[-1]

        if schema is not None:
            known = self._known_schemas()
            if schema.casefold() not in {s.casefold() for s in known}:
                raise UnsafeTableNameError(
                    f"安全拦截: schema '{schema}' 不在当前数据源的可查询范围 {known or '[]'} 内。")
            schema = next(s for s in known if s.casefold() == schema.casefold())

        registered = self.registered_relations()
        if requested in registered:
            return schema, requested
        matched = [n for n in registered if n.casefold() == requested.casefold()]
        if len(matched) == 1:
            return schema, matched[0]
        raise UnsafeTableNameError(
            f"安全拦截: 表 '{raw}' 未在当前数据源中注册，拒绝采样取数。"
            f"可用表请调用 GET /api/chat/tables 获取。")

    def _normalize_sample_size(self, sample_size: Any) -> int:
        """采样行数必须是可安全用作 LIMIT 的整数，并受上限保护。"""
        if isinstance(sample_size, bool) or not isinstance(sample_size, int):
            try:
                sample_size = int(str(sample_size).strip())
            except (TypeError, ValueError):
                raise UnsafeSampleSizeError(
                    f"安全拦截: 采样行数 '{sample_size}' 不是合法整数。") from None
        if sample_size < 1:
            raise UnsafeSampleSizeError("安全拦截: 采样行数必须大于 0。")
        return min(sample_size, MAX_SAMPLE_SIZE)

    def build_sampling_sql(self, schema: Optional[str], table: str, sample_size: int) -> str:
        """
        用 AST 构造采样 SQL，标识符由 sqlglot 负责加引号，
        LIMIT 只接受已经过校验的 int —— 全程没有字符串拼接。
        """
        table_node = exp.Table(
            this=exp.to_identifier(table, quoted=True),
            db=exp.to_identifier(schema, quoted=True) if schema else None,
        )
        return exp.select("*").from_(table_node).limit(int(sample_size)).sql(dialect="mysql")

    def _run_sampling_query(self, sql: str) -> pd.DataFrame:
        """统一安全出口：先过 guardrail 网闸审计，再交给数据源执行。"""
        from app.service.guardrail import guardrail
        guardrail.check_sql(sql, dialect="mysql")
        return db_service.execute_query(sql)

    def profile_table(self, table_name: str, sample_size: int = DEFAULT_SAMPLE_SIZE) -> Dict[str, Any]:
        """
        对物理表进行自动数据画像（Profiling）采样分析。

        表名未通过白名单/标识符校验时抛 UnsafeTableNameError（绝不执行取数）；
        取数本身失败仍沿用既有的 {"error": ...} 返回契约。
        """
        from app.service.guardrail import GuardrailException

        schema, table = self.resolve_registered_table(table_name)
        limit = self._normalize_sample_size(sample_size)
        canonical = f"{schema}.{table}" if schema else table
        query = self.build_sampling_sql(schema, table, limit)
        try:
            df = self._run_sampling_query(query)
        except GuardrailException as e:
            logger.warning("对表 %s 的采样查询被安全网闸拦截: %s", canonical, e.message)
            return {"table_name": canonical, "error": f"安全网闸拦截: {e.message}", "columns": []}
        except Exception as e:
            logger.warning("对表 %s 采样分析失败: %s", canonical, str(e))
            return {"table_name": canonical, "error": str(e), "columns": []}

        total_rows = len(df)
        columns_profile = []

        for col in df.columns:
            series = df[col]
            null_cnt = int(series.isna().sum())
            try:
                distinct_vals = series.dropna().unique()
                samples = distinct_vals[:5].tolist()
            except TypeError:
                # 数组 / JSON 等不可哈希列（PostgreSQL 常见）在 unique() 上会直接抛
                # TypeError，整个画像接口随之 500。退化成字符串形态去重，
                # 让这一列仍然产出画像，而不是拖垮整张表。
                as_text = series.dropna().astype(str)
                distinct_vals = as_text.unique()
                samples = distinct_vals[:5].tolist()
            distinct_cnt = len(distinct_vals)

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
            "table_name": canonical,
            "total_sampled_rows": total_rows,
            "columns": columns_profile
        }

    def enrich_metadata(self, table_name: str, table_profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        基于采样画像与语义库，补全表及字段业务描述与口径定义。

        表名在这里同样先收口为白名单中的规范写法：即便调用方自带 table_profile
        （不触发采样取数），产出的 `calculation` / `table` 字段也会被写进语义层、
        最终参与 SQL 编译，属于二级注入落点，不能放行未注册表名。
        """
        schema, table = self.resolve_registered_table(table_name)
        table_name = f"{schema}.{table}" if schema else table

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
