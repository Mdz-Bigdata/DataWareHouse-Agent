# -*- coding: utf-8 -*-
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import re
import os
import sqlglot

# =====================================================================
# 1. 语义建模实体定义
# =====================================================================

class Metric(BaseModel):
    name: str  # 指标名称：gmv
    aliases: List[str]  # 别名：["交易额", "销售额", "成交金额"]
    description: str  # 指标描述说明
    calculation: str  # 计算口径/物理公式：SUM(gmv)
    unit: str  # 单位：元
    available_dimensions: List[str]  # 可用维度：["region_name", "parent_region_name", "category_name", "month"]
    default_agg: str = "SUM"  # 默认聚合方式
    source_table: str  # 来源事实表：dws_trade_order_daily
    authorized_roles: List[str] = ["admin", "analyst", "user"]  # 授权访问的角色

class Dimension(BaseModel):
    name: str  # 维度名称：region_name
    aliases: List[str]  # 别名：["区域", "地区", "大区"]
    source_table: str  # 来源表：dim_region
    source_column: str  # 来源物理字段：region_name
    value_range: Optional[List[str]] = None  # 取值范围限制，如 ["华北", "华东", ...]

class JoinPath(BaseModel):
    from_table: str  # 主事实表
    to_table: str  # 关联维表
    join_type: str = "LEFT"  # JOIN 类型：LEFT / INNER
    condition: str  # 关联条件，例如 "dws_trade_order_daily.region_id = dim_region.region_id"

# =====================================================================
# 2. 动态术语词典、时区配置（不再硬编码仿真数据）
# =====================================================================

# NOTE: 术语同义词典将由自动发现流程从真实 Schema 中动态填充，
# 不再硬编码任何特定业务场景的表名/列名映射。
TERM_DICTIONARY: Dict[str, Any] = {}

TIMEZONE_CONFIG = {
    "database": "America/Chicago",  # 数据库存储时区（存的是芝加哥时间）
    "business": "Asia/Shanghai"     # 业务时区（北京时间）
}

# NOTE: 表元数据配置与 SQL 示例将由自动发现流程从真实物理库 Schema 中动态生成，
# 不再硬编码任何仿真电商/金融/制造/政企场景的表结构和示例 SQL。
TABLE_CONFIG: Dict[str, Any] = {}
EXAMPLE_SQL: Dict[str, Any] = {}

# =====================================================================
# 3. 语义层注册中心
# =====================================================================

class SemanticLayer:
    def __init__(self):
        self.metrics: Dict[str, Metric] = {}
        self.dimensions: Dict[str, Dimension] = {}
        self.join_paths: List[JoinPath] = []
        self.discovered_table_columns: Dict[str, List] = {}
        self._initialize_registry()

    def _initialize_registry(self):
        """
        语义层初始化注册。
        彻底移除所有硬编码的仿真指标/维度/关联路径（如 dws_trade_order_daily, dim_region 等），
        所有指标、维度和 JOIN 关联路径 100% 由自动发现流程从真实物理数据库 Schema 中动态生成。
        支持 PostgreSQL / MySQL / StarRocks / Doris / ClickHouse 等多数据源。
        """
        # 全自动表 Schema 扫描与指标/维度自适应建模自举
        try:
            self._auto_discover_and_register_schemas()
        except Exception as auto_err:
            print(f"[Auto Schema Discovery Error]: {auto_err}")

    def register_metric(self, metric: Metric):
        self.metrics[metric.name] = metric

    def register_dimension(self, dimension: Dimension):
        self.dimensions[dimension.name] = dimension

    def register_join_path(self, join_path: JoinPath):
        self.join_paths.append(join_path)

    def _auto_discover_and_register_schemas(self):
        """
        自动从当前物理/仿真数据库中发现所有的 schema，
        并为尚未在语义层建模的业务表加工指标、维度与 JOIN 路径关系。
        """
        from app.service.db_service import db_service
        import pandas as pd
        
        # 1. 尝试连接物理数据库，若失败则回退到本地 SQLite
        table_columns = {} # table_name -> list of (column_name, data_type)
        
        if os.getenv("DB_TYPE") != "sqlite" and db_service.real_engine:
            try:
                with db_service.real_engine.connect() as conn:
                    df = pd.read_sql_query(
                        "SELECT table_name, column_name, data_type "
                        "FROM information_schema.columns "
                        "WHERE table_schema = 'public'", conn
                    )
                    for _, row in df.iterrows():
                        tbl = row["table_name"]
                        col = row["column_name"]
                        dtype = row["data_type"]
                        if tbl not in table_columns:
                            table_columns[tbl] = []
                        table_columns[tbl].append((col, dtype))
            except Exception as e:
                print(f"[Auto Schema] PostgreSQL query failed: {e}. Falling back to SQLite.")
                
        if not table_columns:
            try:
                cursor = db_service.conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [r[0] for r in cursor.fetchall()]
                for tbl in tables:
                    if tbl.startswith("sqlite_"):
                        continue
                    cursor.execute(f"PRAGMA table_info({tbl})")
                    columns = cursor.fetchall()
                    table_columns[tbl] = [(c[1], c[2]) for c in columns]
            except Exception as e:
                print(f"[Auto Schema] SQLite query failed: {e}.")
                return

        # NOTE: 保存自动发现的表列映射，供 DSL 编译器动态解析时间列等
        self.discovered_table_columns = table_columns

        # 2. 自动指标/维度加工逻辑
        translation_dict = {
            "title": ["标题", "文章标题", "文章名称", "title"],
            "name": ["名称", "名字", "类别名称", "分类", "类别", "name"],
            "status": ["状态", "发布状态", "status"],
            "view_count": ["浏览量", "点击量", "阅读数", "阅读量", "view_count"],
            "created_at": ["创建时间", "发布时间", "created_at"],
            "id": ["标识", "主键", "id"]
        }

        for tbl, cols in table_columns.items():
            # 跳过系统管理表，只对业务数据表进行自动指标/维度建模
            if tbl in ["admin_logs", "mcp_servers", "system_configs", "system_settings", "skills", "tasks", "plugins", "document_templates"]:
                continue

            col_names = [c[0] for c in cols]
            
            # (A) 自动加工 COUNT 数量指标
            metric_name = f"{tbl}_count"
            if metric_name not in self.metrics:
                aliases = [metric_name]
                unit = "个"
                if "article" in tbl:
                    aliases.extend(["文章数量", "文章数", "文章篇数", "有多少篇", "文章数量"])
                    unit = "篇"
                elif "user" in tbl:
                    aliases.extend(["用户数量", "用户数", "总人数"])
                    unit = "人"
                elif "comment" in tbl:
                    aliases.extend(["评论数", "评论数量"])
                    unit = "条"
                else:
                    aliases.extend([f"{tbl}数量", f"{tbl}数"])

                pk_col = "id" if "id" in col_names else col_names[0]
                
                avail_dims = []
                for c, _ in cols:
                    if c != pk_col and not c.endswith("_id"):
                        dim_name = c
                        if dim_name not in avail_dims:
                            avail_dims.append(dim_name)

                self.register_metric(Metric(
                    name=metric_name,
                    aliases=aliases,
                    description=f"自动加工发现的物理表 {tbl} 记录数指标",
                    calculation=pk_col,
                    unit=unit,
                    available_dimensions=avail_dims,
                    default_agg="COUNT",
                    source_table=tbl,
                    authorized_roles=["admin", "analyst", "user"]
                ))
                print(f"[Auto Schema] Automatically processed and registered metric: {metric_name} with dims {avail_dims}")

            # (B) 自动加工数值列 SUM 指标
            for c, dtype in cols:
                dtype_lower = str(dtype).lower()
                is_numeric = any(n in dtype_lower for n in ["int", "double", "float", "numeric", "decimal", "real"])
                if is_numeric and c not in ["id", "parent_id", "original_id"] and not c.endswith("_id") and not c.endswith("date") and c != "dt":
                    sum_metric_name = f"total_{c}"
                    if sum_metric_name not in self.metrics:
                        aliases = [sum_metric_name, c]
                        for k, v in translation_dict.items():
                            if k in c:
                                aliases.extend([f"总{x}" for x in v])
                                aliases.extend(v)
                        self.register_metric(Metric(
                            name=sum_metric_name,
                            aliases=aliases,
                            description=f"自动加工发现的物理列 {tbl}.{c} 累加指标",
                            calculation=c,
                            unit="个",
                            available_dimensions=[col[0] for col in cols if col[0] not in ["id", c] and not col[0].endswith("_id")],
                            default_agg="SUM",
                            source_table=tbl,
                            authorized_roles=["admin", "analyst", "user"]
                        ))
                        print(f"[Auto Schema] Automatically processed and registered metric: {sum_metric_name}")

            # (C) 自动加工维度
            for c, dtype in cols:
                if c.endswith("_id") or c == "id":
                    continue
                dim_name = c
                aliases = [c]
                for k, v in translation_dict.items():
                    if k == c:
                        aliases.extend(v)
                
                if tbl == "categories" and c == "name":
                    dim_name = "category_name" 
                    aliases.extend(["每类文章", "类别", "分类", "文章类别"])
                
                if dim_name not in self.dimensions:
                    self.register_dimension(Dimension(
                        name=dim_name,
                        aliases=aliases,
                        source_table=tbl,
                        source_column=c
                    ))
                    print(f"[Auto Schema] Automatically processed and registered dimension: {dim_name} ({tbl}.{c}) with aliases {aliases}")

        # 3. 自动推导外键 JOIN 关联关系
        for tbl_A, cols_A in table_columns.items():
            for c, _ in cols_A:
                if c.endswith("_id") and c != "id":
                    prefix = c[:-3]
                    for tbl_B in table_columns.keys():
                        if tbl_B == tbl_A:
                            continue
                        tbl_B_clean = tbl_B.lower()
                        match_found = False
                        if tbl_B_clean == prefix or tbl_B_clean == f"{prefix}s" or (prefix.endswith("y") and tbl_B_clean == f"{prefix[:-1]}ies") or tbl_B_clean.startswith(prefix):
                            match_found = True
                        
                        if match_found:
                            cols_B_names = [col[0] for col in table_columns[tbl_B]]
                            if "id" in cols_B_names:
                                jp = JoinPath(
                                    from_table=tbl_A,
                                    to_table=tbl_B,
                                    join_type="LEFT",
                                    condition=f"{tbl_A}.{c} = {tbl_B}.id"
                                )
                                if not any(x.from_table == tbl_A and x.to_table == tbl_B for x in self.join_paths):
                                    self.register_join_path(jp)
                                    print(f"[Auto Schema] Automatically discovered JOIN path: {tbl_A} -> {tbl_B} via {tbl_A}.{c} = {tbl_B}.id")
        # 4. 基于图联通性，自动扩散可用维度 (多跳)
        # 对每一个注册的指标，如果它所在的表能够通过 get_join_path_chain 连通到某个维度的源表，则该维度可用。
        for m_name, m in list(self.metrics.items()):
            for dim_name, d in self.dimensions.items():
                if dim_name not in m.available_dimensions:
                    # 如果维度就在当前表，或者存在连通路径，则加入可用维度
                    if m.source_table == d.source_table or self.get_join_path_chain(m.source_table, d.source_table):
                        m.available_dimensions.append(dim_name)
                        print(f"[Auto Schema] Appended reachable dimension '{dim_name}' to metric '{m_name}' (Path: {m.source_table} -> {d.source_table})")

    def resolve_metric(self, term: str) -> Optional[Metric]:
        """通过指标名字或别名查找匹配"""
        term = term.strip().lower()
        if term in self.metrics:
            return self.metrics[term]
        for m in self.metrics.values():
            if term == m.name or term in m.aliases:
                return m
        if term in TERM_DICTIONARY and isinstance(TERM_DICTIONARY[term], str):
            mapped = TERM_DICTIONARY[term]
            if mapped in self.metrics:
                return self.metrics[mapped]
        return None

    def resolve_dimension(self, term: str) -> Optional[Dimension]:
        """通过名字或别名查找维度"""
        term = term.strip().lower()
        if term in self.dimensions:
            return self.dimensions[term]
        for d in self.dimensions.values():
            if term == d.name or term in d.aliases:
                return d
        if term in TERM_DICTIONARY and isinstance(TERM_DICTIONARY[term], str):
            mapped = TERM_DICTIONARY[term]
            if mapped in self.dimensions:
                return self.dimensions[mapped]
        return None

    def get_join_path(self, from_table: str, to_table: str) -> Optional[JoinPath]:
        """计算两表之间的单跳关联路径"""
        for jp in self.join_paths:
            if jp.from_table == from_table and jp.to_table == to_table:
                return jp
        return None

    def get_join_path_chain(self, from_table: str, to_table: str) -> List[JoinPath]:
        """计算两表之间的多跳关联路径 (BFS)"""
        if from_table == to_table:
            return []
            
        queue = [(from_table, [])]
        visited = {from_table}
        
        adj = {}
        for jp in self.join_paths:
            if jp.from_table not in adj: adj[jp.from_table] = []
            if jp.to_table not in adj: adj[jp.to_table] = []
            adj[jp.from_table].append(jp)
            
            # 反向边
            rev_jp = JoinPath(
                from_table=jp.to_table,
                to_table=jp.from_table,
                join_type="LEFT",
                condition=jp.condition
            )
            adj[jp.to_table].append(rev_jp)
            
        while queue:
            curr_table, path = queue.pop(0)
            if curr_table == to_table:
                return path
            
            for edge in adj.get(curr_table, []):
                if edge.to_table not in visited:
                    visited.add(edge.to_table)
                    queue.append((edge.to_table, path + [edge]))
                    
        return []

    def resolve_with_preference(self, term: str, preference: dict) -> Optional[Metric]:
        """
        结合用户偏好消歧，如遇到多候选指标时根据习惯返回
        """
        candidates = []
        term_clean = term.strip().lower()
        for m in self.metrics.values():
            if term_clean == m.name or term_clean in m.aliases:
                candidates.append(m)
        
        if len(candidates) > 1 and preference.get("common_metrics"):
            preferred = [p["metric"] for p in preference["common_metrics"]]
            for pref in preferred:
                for c in candidates:
                    if c.name == pref:
                        return c
        return candidates[0] if candidates else None

# 单例注册中心
semantic_layer = SemanticLayer()

# =====================================================================
# 4. 时区转换工具
# =====================================================================

def align_timezone_range(start_beijing: str, end_beijing: str) -> Tuple[str, str]:
    """
    时区对齐：将北京时间 YYYY-MM-DD HH:MM:SS (或 YYYY-MM-DD)
    转换为目标芝加哥时间 (America/Chicago) 的起止时间戳字符串，用于 SQL 的 WHERE 精确过滤。
    """
    try:
        bj_tz = ZoneInfo(TIMEZONE_CONFIG["business"])
        chi_tz = ZoneInfo(TIMEZONE_CONFIG["database"])

        # 补全为时间戳
        if len(start_beijing) == 10:
            start_dt = datetime.strptime(start_beijing, "%Y-%m-%d").replace(tzinfo=bj_tz)
        else:
            start_dt = datetime.strptime(start_beijing, "%Y-%m-%d %H:%M:%S").replace(tzinfo=bj_tz)

        if len(end_beijing) == 10:
            end_dt = datetime.strptime(end_beijing, "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=bj_tz)
        else:
            end_dt = datetime.strptime(end_beijing, "%Y-%m-%d %H:%M:%S").replace(tzinfo=bj_tz)

        start_chi = start_dt.astimezone(chi_tz)
        end_chi = end_dt.astimezone(chi_tz)

        return start_chi.strftime("%Y-%m-%d %H:%M:%S"), end_chi.strftime("%Y-%m-%d %H:%M:%S")
    except Exception as e:
        print(f"[Timezone Alignment Error]: {e}. Fallback to raw inputs.")
        return start_beijing, end_beijing

# =====================================================================
# 5. DSL 确定性 SQL 编译器 (DSLCompiler)
# =====================================================================

class DSLCompiler:
    def __init__(self, layer: SemanticLayer = semantic_layer, dialect: str = "doris"):
        self.layer = layer
        self.dialect = dialect

    def _resolve_time_column(self, table_name: str) -> Optional[str]:
        """
        动态解析指定表的业务分区时间列。
        只有显式的业务分区列（dt / date / publish_date）才会触发自动时间过滤。
        created_at / updated_at 属于审计时间戳，不应作为默认查询过滤条件，
        否则会导致普通业务表（如 articles）因「最近30天」过滤条件而查不到任何历史数据。
        """
        if not hasattr(self.layer, 'discovered_table_columns'):
            return "dt"
        cols_info = self.layer.discovered_table_columns.get(table_name, [])
        col_names = [c[0] for c in cols_info]
        # NOTE: 只返回显式分区列，created_at / updated_at 不在此列
        for candidate in ["dt", "date", "publish_date", "publish_time"]:
            if candidate in col_names:
                return candidate
        return None

    def _table_ref(self, table_name: str) -> str:
        """
        构建表引用：PostgreSQL 不使用 database.table 语法，直接返回表名。
        其他数据库（Doris/StarRocks/MySQL）使用 database.table 前缀。
        """
        from app.service.db_service import db_service
        active_type = getattr(db_service, 'active_db_type', '').lower()
        if "postgres" in active_type or os.getenv("DB_TYPE", "").lower() == "postgres":
            return table_name
        db_name = db_service.get_active_db_name()
        return f"{db_name}.{table_name}"

    def compile(self, dsl: Dict[str, Any]) -> str:
        """
        将意图 QueryDSL 编译为标准的 SQL 方言。该过程纯代码拼接，不接触 LLM。
        """
        from app.service.db_service import db_service
        metrics = dsl.get("metrics", [])
        dimensions = dsl.get("dimensions", [])
        filters = dsl.get("filters", [])
        time_range = dsl.get("time_range")
        order_by = dsl.get("order_by")
        limit_val = dsl.get("limit", 10)

        if not metrics:
            raise ValueError("编译 SQL 错误: 意图 DSL 中未提供任何指标 (metrics)。")

        # 1. 确定事实主表 (根据首个指标的 source_table)
        primary_metric_info = metrics[0]
        metric_name = primary_metric_info.get("name")
        primary_metric = self.layer.resolve_metric(metric_name)
        if not primary_metric:
            raise ValueError(f"编译 SQL 错误: 语义层中未注册此指标 - '{metric_name}'")
        
        main_table = primary_metric.source_table
        
        # 2. 构建 SELECT 字段 & 收集需要关联的维表
        select_parts = []
        group_by_parts = []
        joined_tables = set()
        join_clauses = []

        # 2.0 收集 filters 里需要关联的维表
        for filt in filters:
            field = filt.get("field")
            if field and field not in ["dt", "created_at", "updated_at", "date"]:
                dim = self.layer.resolve_dimension(field)
                if dim and dim.source_table != main_table:
                    joined_tables.add(dim.source_table)

        # 2.1 添加维度到 SELECT 和 GROUP BY
        has_month_trend = False
        for dim_item in dimensions:
            dim_name = dim_item.get("name")
            if not dim_name:
                continue

            if dim_name == "month":
                has_month_trend = True
                time_col = self._resolve_time_column(main_table) or "created_at"
                select_parts.append(f"DATE_TRUNC('month', {main_table}.{time_col}) AS month")
                group_by_parts.append(f"DATE_TRUNC('month', {main_table}.{time_col})")
                continue

            dim = self.layer.resolve_dimension(dim_name)
            if not dim:
                select_parts.append(f"{main_table}.{dim_name} AS {dim_name}")
                group_by_parts.append(f"{main_table}.{dim_name}")
                continue

            # 如果维度表不是主表本身，记录需要进行 JOIN
            if dim.source_table != main_table:
                joined_tables.add(dim.source_table)
                select_parts.append(f"{dim.source_table}.{dim.source_column} AS {dim.name}")
                group_by_parts.append(f"{dim.source_table}.{dim.source_column}")
            else:
                select_parts.append(f"{main_table}.{dim.source_column} AS {dim.name}")
                group_by_parts.append(f"{main_table}.{dim.source_column}")

        # 2.2 添加指标到 SELECT
        for m_item in metrics:
            m_name = m_item.get("name")
            agg = m_item.get("agg", None)
            ratio_type = m_item.get("ratio_type", None)

            metric = self.layer.resolve_metric(m_name)
            if not metric:
                raise ValueError(f"编译 SQL 错误: 指标未注册 - '{m_name}'")

            calc = metric.calculation
            base_col = f"{main_table}.{calc}" if "." not in calc else calc
            
            # 高阶分析函数物理生成逻辑 (同比/环比/累计/排名)
            if ratio_type == "mom":
                # 环比计算：(当前期 - 上一期) / 上一期 (使用 LAG 窗口函数)
                select_parts.append(
                    f"(SUM({base_col}) - LAG(SUM({base_col}), 1) OVER (ORDER BY {main_table}.dt)) "
                    f"/ NULLIF(LAG(SUM({base_col}), 1) OVER (ORDER BY {main_table}.dt), 0) AS {m_name}_mom"
                )
            elif ratio_type == "yoy":
                # 同比计算：(当前期 - 去年同期) / 去年同期 (使用 LAG 窗口天数对齐)
                select_parts.append(
                    f"(SUM({base_col}) - LAG(SUM({base_col}), 365) OVER (ORDER BY {main_table}.dt)) "
                    f"/ NULLIF(LAG(SUM({base_col}), 365) OVER (ORDER BY {main_table}.dt), 0) AS {m_name}_yoy"
                )
            elif ratio_type == "cumulative":
                # 累计计算 (ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                select_parts.append(
                    f"SUM(SUM({base_col})) OVER (ORDER BY {main_table}.dt ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cumulative_{m_name}"
                )
            elif ratio_type == "rank":
                # 排名计算 (DENSE_RANK)
                select_parts.append(
                    f"DENSE_RANK() OVER (ORDER BY SUM({base_col}) DESC) AS {m_name}_rank"
                )
            elif metric.default_agg == "formula" or agg == "formula":
                if calc == "refund_amount / NULLIF(gmv, 0)":
                    select_parts.append("SUM(refund_amount) / NULLIF(SUM(gmv), 0) AS refund_ratio")
                else:
                    select_parts.append(f"({calc}) AS {metric.name}")
            else:
                agg_func = agg if agg else metric.default_agg
                select_parts.append(f"{agg_func}({base_col}) AS total_{metric.name}")

        # 3. 构造 JOIN 关联子句
        # 为了处理多跳关联，我们需要合并所有的关联边，避免重复 JOIN 同一张表
        all_join_edges = []
        for target_tbl in joined_tables:
            path = self.layer.get_join_path_chain(main_table, target_tbl)
            if path:
                all_join_edges.extend(path)
            else:
                # 兜底：如果完全不可达，尝试硬连主键 (可能报错，但在缺乏schema连接时是最后手段)
                all_join_edges.append(JoinPath(
                    from_table=main_table,
                    to_table=target_tbl,
                    join_type="LEFT",
                    condition=f"{main_table}.id = {target_tbl}.id"
                ))
        
        # 去重并保证顺序 (基于从主表向外辐射的顺序)
        seen_conditions = set()
        for edge in all_join_edges:
            if edge.condition not in seen_conditions:
                seen_conditions.add(edge.condition)
                join_clauses.append(f"{edge.join_type} JOIN {self._table_ref(edge.to_table)} ON {edge.condition}")

        # 4. 构建 WHERE 过滤条件 (处理时区与字段映射)
        where_conds = []

        # 4.1 处理时间过滤器 (优先从 time_range 中抓取，意图解析后可能放在 filters 中)
        start_date, end_date = None, None
        if time_range and isinstance(time_range, dict):
            start_date = time_range.get("start")
            end_date = time_range.get("end")
        else:
            for f in filters:
                if f.get("field") == "dt" and f.get("op") == "between":
                    val = f.get("value")
                    if isinstance(val, list) and len(val) == 2:
                        start_date, end_date = val[0], val[1]
                        break

        if not start_date or not end_date:
            end_date = datetime.now().strftime("%Y-%m-%d")
            start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        start_chi, end_chi = align_timezone_range(start_date, end_date)
        chi_start_day = start_chi[:10]
        chi_end_day = end_chi[:10]
        
        # 动态解析表的实际时间列，若表无时间列则跳过时间过滤
        time_col = self._resolve_time_column(main_table)
        if time_col:
            where_conds.append(f"{main_table}.{time_col} BETWEEN '{chi_start_day}' AND '{chi_end_day}'")

        # 4.2 处理其余普通过滤器
        for filt in filters:
            field = filt.get("field")
            op = filt.get("op")
            val = filt.get("value")

            if not field or not op or val is None:
                continue

            if field in ["dt", "created_at", "updated_at", "date"]:
                continue

            tbl_prefix = main_table
            phys_col = field
            dim = self.layer.resolve_dimension(field)
            if dim:
                tbl_prefix = dim.source_table
                phys_col = dim.source_column

            if op == "eq":
                where_conds.append(f"{tbl_prefix}.{phys_col} = '{val}'")
            elif op == "in" and isinstance(val, list):
                val_list_str = ", ".join([f"'{v}'" for v in val])
                where_conds.append(f"{tbl_prefix}.{phys_col} IN ({val_list_str})")
            elif op == "between" and isinstance(val, list) and len(val) == 2:
                where_conds.append(f"{tbl_prefix}.{phys_col} BETWEEN '{val[0]}' AND '{val[1]}'")

        # 5. 拼装核心 SQL 骨架
        sql_parts = []
        sql_parts.append("SELECT")
        sql_parts.append(", ".join(select_parts))
        sql_parts.append("FROM")
        sql_parts.append(self._table_ref(main_table))
        
        if join_clauses:
            sql_parts.append(" ".join(join_clauses))
        
        if where_conds:
            sql_parts.append("WHERE")
            sql_parts.append(" AND ".join(where_conds))

        if group_by_parts:
            sql_parts.append("GROUP BY")
            sql_parts.append(", ".join(group_by_parts))

        # 6. 处理排序
        if order_by and isinstance(order_by, list):
            order_cols = []
            for ob in order_by:
                ob_field = ob.get("field")
                direction = ob.get("direction", "DESC").upper()
                if ob_field == "month":
                    _tc = self._resolve_time_column(main_table) or "created_at"
                    order_cols.append(f"DATE_TRUNC('month', {main_table}.{_tc}) {direction}")
                else:
                    m = self.layer.resolve_metric(ob_field)
                    if m:
                        ob_col = "refund_ratio" if m.name == "refund_ratio" else f"total_{m.name}"
                        order_cols.append(f"{ob_col} {direction}")
                    else:
                        order_cols.append(f"{ob_field} {direction}")
            sql_parts.append("ORDER BY " + ", ".join(order_cols))
        else:
            if has_month_trend:
                _tc = self._resolve_time_column(main_table) or "created_at"
                sql_parts.append(f"ORDER BY DATE_TRUNC('month', {main_table}.{_tc}) ASC")
            elif group_by_parts:
                m_name = metrics[0].get("name")
                ob_col = "refund_ratio" if m_name == "refund_ratio" else f"total_{m_name}"
                sql_parts.append(f"ORDER BY {ob_col} DESC")

        if limit_val:
            sql_parts.append(f"LIMIT {limit_val}")

        standard_sql = " ".join(sql_parts)

        # 7. 转译为目标数据库方言
        try:
            translated_sqls = sqlglot.transpile(standard_sql, read="mysql", write=self.dialect)
            return translated_sqls[0]
        except Exception as e:
            print(f"[SQLGlot Compiler Error]: {e}. Fallback to standard SQL.")
            return standard_sql
