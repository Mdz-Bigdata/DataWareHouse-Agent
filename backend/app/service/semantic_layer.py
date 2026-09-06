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

def singular_table_term(table: str) -> str:
    """Return the singular business term a table name refers to (categories -> category)."""
    base = table.lower()
    if base.startswith("dim_"):
        base = base[4:]
    if base.endswith("ies"):
        return base[:-3] + "y"
    if base.endswith("s") and not base.endswith("ss"):
        return base[:-1]
    return base


def dimension_name_for(table: str, column: str) -> str:
    """
    维度命名：通用的 `name` 列必须按所属表限定（categories.name -> category_name），
    否则不同业务域会注册出同名维度，查询时无法确定归属。
    """
    if column == "name":
        return f"{singular_table_term(table)}_name"
    return column


class SemanticLayer:
    def __init__(self):
        self.metrics: Dict[str, Metric] = {}
        self.dimensions: Dict[str, Dimension] = {}
        self.join_paths: List[JoinPath] = []
        self.discovered_table_columns: Dict[str, List] = {}
        self.table_dimensions = {}
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
        self.table_dimensions[(dimension.source_table, dimension.name)] = dimension

    def register_join_path(self, join_path: JoinPath):
        self.join_paths.append(join_path)

    def _auto_discover_and_register_schemas(self):
        """
        自动从当前物理/仿真数据库中发现所有的 schema，
        并为尚未在语义层建模的业务表加工指标、维度与 JOIN 路径关系。
        """
        from app.service.db_service import db_service
        from sqlalchemy import inspect
        
        # 元数据必须与执行使用同一数据源，不能把演示表注册到物理库中。
        table_columns = {} # table_name -> list of (column_name, data_type)
        
        if db_service.real_engine is not None:
            try:
                inspector = inspect(db_service.real_engine)
                # Walk schemas in resolution order so a name defined in several of
                # them is modeled from the one an unqualified query actually reads.
                schemas = getattr(db_service, "query_schemas", None) or [None]
                for schema in schemas:
                    names = inspector.get_table_names(schema=schema) + inspector.get_view_names(schema=schema)
                    for tbl in dict.fromkeys(names):
                        if tbl in table_columns:
                            continue
                        columns = inspector.get_columns(tbl, schema=schema)
                        if columns:
                            table_columns[tbl] = [(col["name"], str(col["type"])) for col in columns]
            except Exception as e:
                print(f"[Auto Schema] Physical schema discovery failed: {e}")
                return
        else:
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

        # 2. 自动指标/维度加工逻辑 (融合电商通用与 ListenBook 听书业务域)
        translation_dict = {
            "title": ["标题", "文章标题", "文章名称", "title"],
            "name": ["名称", "名字", "类别名称", "分类", "类别", "name"],
            "status": ["状态", "发布状态", "status"],
            "view_count": ["浏览量", "点击量", "阅读数", "阅读量", "view_count"],
            "created_at": ["创建时间", "发布时间", "created_at"],
            "source_platform": ["来源", "来源平台", "发布平台", "文章来源", "source_platform"],
            "id": ["标识", "主键", "id"],
            "gmv": ["交易额", "销售额", "销售总额", "交易金额", "销售金额", "gmv"],
            "refund_amount": ["退款额", "退款金额", "退款总额", "refund_amount"],
            "order_count": ["订单数", "订单数量", "成交笔数", "order_count"],
            "region_name": ["区域", "区域名称", "地区", "地区名称", "region_name"],
            "category_name": ["品类", "品类名称", "分类", "类目", "分类名称", "category_name"],
            "play_count": ["播放量", "播放数", "收听量", "播放次数", "收听次数", "play_count"],
            "play_duration_seconds": ["收听时长", "播放时长", "收听时间", "听书时长", "play_duration"],
            "completion_rate": ["完播率", "完播比例", "完播", "completion_rate"],
            "album_name": ["专辑", "专辑名称", "有声书", "书籍", "听书名", "album_name"],
            "anchor_name": ["主播", "主播名称", "演播人", "播音员", "anchor_name"],
            "plan_name": ["会员套餐", "VIP套餐", "订阅方案", "套餐名称", "plan_name"],
            "paid_users": ["付费人数", "购买人数", "付费用户数", "paid_users"]
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
                        dim_name = dimension_name_for(tbl, c)
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
                            if k == c:
                                aliases.extend([f"总{x}" for x in v])
                                aliases.extend(v)
                            elif c.startswith("audio_") and c[6:] == k:
                                aliases.extend([f"听书{x}" for x in v])
                                aliases.extend([f"会员{x}" for x in v])
                        if c == "audio_gmv":
                            aliases.extend(["听书会员收入", "会员收入", "听书收入"])
                        if c == "audio_refund_amount":
                            aliases.extend(["听书会员退款", "会员退款", "听书退款"])
                        self.register_metric(Metric(
                            name=sum_metric_name,
                            aliases=aliases,
                            description=f"自动加工发现的物理列 {tbl}.{c} 累加指标",
                            calculation=c,
                            unit=("元" if c.endswith(("amount", "gmv")) else
                                  "%" if c.endswith(("rate", "ratio")) else
                                  "秒" if c.endswith("seconds") else
                                  "次" if c == "play_count" else "个"),
                            available_dimensions=[dimension_name_for(tbl, col[0]) for col in cols
                                                  if col[0] not in ["id", c] and not col[0].endswith("_id")],
                            default_agg="SUM",
                            source_table=tbl,
                            authorized_roles=["admin", "analyst", "user"]
                        ))
                        print(f"[Auto Schema] Automatically processed and registered metric: {sum_metric_name}")

            # (C) 自动加工维度
            for c, dtype in cols:
                if c.endswith("_id") or c == "id":
                    continue
                dim_name = dimension_name_for(tbl, c)
                aliases = [dim_name]
                for k, v in translation_dict.items():
                    if k == dim_name:
                        aliases.extend(v)

                if dim_name == "category_name":
                    aliases.extend(["每类文章", "类别", "分类", "文章类别"])


                if (tbl, dim_name) not in self.table_dimensions:
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
                        tbl_B_stripped = tbl_B_clean[4:] if tbl_B_clean.startswith("dim_") else tbl_B_clean
                        match_found = False
                        if tbl_B_stripped == prefix or tbl_B_stripped == f"{prefix}s" or (prefix.endswith("y") and tbl_B_stripped == f"{prefix[:-1]}ies") or tbl_B_stripped.startswith(prefix) or prefix.startswith(tbl_B_stripped):
                            match_found = True
                        
                        if match_found:
                            cols_B_names = [col[0] for col in table_columns[tbl_B]]
                            target_pk = None
                            if c in cols_B_names:
                                target_pk = c
                            elif "id" in cols_B_names:
                                target_pk = "id"
                                
                            if target_pk:
                                jp = JoinPath(
                                    from_table=tbl_A,
                                    to_table=tbl_B,
                                    join_type="LEFT",
                                    condition=f"{tbl_A}.{c} = {tbl_B}.{target_pk}"
                                )
                                if not any(x.from_table == tbl_A and x.to_table == tbl_B for x in self.join_paths):
                                    self.register_join_path(jp)
                                    print(f"[Auto Schema] Automatically discovered JOIN path: {tbl_A} -> {tbl_B} via {tbl_A}.{c} = {tbl_B}.{target_pk}")
        # 4. 基于图联通性，自动扩散可用维度 (多跳)
        # 对每一个注册的指标，如果它所在的表能够通过 get_join_path_chain 连通到某个维度的源表，则该维度可用。
        for m_name, m in list(self.metrics.items()):
            for d in self.table_dimensions.values():
                dim_name = d.name
                if dim_name not in m.available_dimensions:
                    # 如果维度就在当前表，或者存在连通路径，则加入可用维度
                    if m.source_table == d.source_table or self.get_join_path_chain(m.source_table, d.source_table):
                        m.available_dimensions.append(dim_name)
                        print(f"[Auto Schema] Appended reachable dimension '{dim_name}' to metric '{m_name}' (Path: {m.source_table} -> {d.source_table})")

    def mentioned_tables(self, question: str) -> List[str]:
        """Honor physical table names explicitly supplied by the user."""
        question = question.lower()
        tables = set(self.discovered_table_columns) | {m.source_table for m in self.metrics.values()}
        return sorted(table for table in tables if re.search(
            r"(?<![a-z0-9_])" + re.escape(table.lower()) + r"(?![a-z0-9_])", question))

    @staticmethod
    def mentions_term(question: str, term: str) -> bool:
        term = term.strip().lower()
        if not term:
            return False
        if re.fullmatch(r"[a-z0-9_]+", term):
            return bool(re.search(r"(?<![a-z0-9_])" + re.escape(term) + r"(?![a-z0-9_])", question.lower()))
        return term in question.lower()

    def suggested_dimensions(self, metric: Metric) -> List[str]:
        """Return reachable grouping fields suitable for public query suggestions."""
        measures = {(m.source_table, m.calculation) for m in self.metrics.values()
                    if m.default_agg.upper() in ("SUM", "AVG")}
        names = []
        for name in metric.available_dimensions:
            dimension = self.resolve_dimension(name, metric.source_table)
            if (dimension is None or (dimension.source_table, dimension.source_column) in measures
                    or any(token in name.lower() for token in
                           ("phone", "mobile", "card", "email", "password", "token", "secret", "address"))
                    or name in ("title", "content", "dt", "date", "created_at", "updated_at")):
                continue
            names.append(name)
        return names

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

    def resolve_dimension(self, term: str, table_context: str = None) -> Optional[Dimension]:
        """通过名字或别名查找维度，支持就近消歧绑定到指定表上"""
        term = term.strip().lower()
        if table_context and (table_context, term) in self.table_dimensions:
            return self.table_dimensions[(table_context, term)]
        if table_context:
            candidates = [dimension for dimension in self.table_dimensions.values()
                          if term == dimension.name or term in dimension.aliases]
            local = [dimension for dimension in candidates if dimension.source_table == table_context]
            reachable = [dimension for dimension in candidates
                         if self.get_join_path_chain(table_context, dimension.source_table)]
            if local:
                return local[0]
            if len(reachable) == 1:
                return reachable[0]
            if candidates:
                return None  # Never bind an identically named dimension in an unrelated business domain.
            
        if term in self.dimensions:
            return self.dimensions[term]
            
        if table_context:
            for d in self.dimensions.values():
                if d.source_table == table_context and (term == d.name or term in d.aliases):
                    return d
                    
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
        if not hasattr(self, '_join_chain_cache'):
            self._join_chain_cache = {}
        cache_key = (from_table, to_table)
        if cache_key in self._join_chain_cache:
            return self._join_chain_cache[cache_key]

        if from_table == to_table:
            self._join_chain_cache[cache_key] = []
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
            
        res_path = []
        while queue:
            curr_table, path = queue.pop(0)
            if curr_table == to_table:
                res_path = path
                break
            
            for edge in adj.get(curr_table, []):
                if edge.to_table not in visited:
                    visited.add(edge.to_table)
                    queue.append((edge.to_table, path + [edge]))
                    
        self._join_chain_cache[cache_key] = res_path
        return res_path

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

    @staticmethod
    def aggregate_alias(metric_name: str) -> str:
        """Share aggregate column naming with consumers of compiled SQL results."""
        return metric_name if metric_name.startswith("total_") else f"total_{metric_name}"

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
        构建表引用：PostgreSQL / SQLite 使用 schema 而非 database 前缀，直接返回表名。
        其他数据库（Doris/StarRocks/MySQL）使用 database.table 前缀。
        """
        from app.service.db_service import db_service
        active_type = getattr(db_service, 'active_db_type', '').lower()
        if ("postgres" in active_type or "sqlite" in active_type
                or os.getenv("DB_TYPE", "").lower().startswith(("postgres", "sqlite"))):
            return table_name
        db_name = db_service.get_active_db_name()
        # 只有合法标识符才能作为库名前缀；空库名或 ":memory:" 会生成无法解析的 SQL。
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", db_name or ""):
            return table_name
        return f"{db_name}.{table_name}"

    def _reject_unbound_dimension(self, dim_name: str, main_table: str) -> None:
        """
        维度未能绑定到具体物理表时，绝不能退化成 `主表.维度名` 猜列：
        该列可能根本不存在，或恰好命中另一个业务域的同名列，从而静默返回错误结果。
        """
        columns = {name for name, _ in
                   getattr(self.layer, "discovered_table_columns", {}).get(main_table, [])}
        if not columns or dim_name in columns:
            return
        owners = sorted({f"{dimension.source_table}.{dimension.source_column}"
                         for dimension in self.layer.table_dimensions.values()
                         if dim_name == dimension.name or dim_name in dimension.aliases})
        detail = f"，同名维度分别来自：{'、'.join(owners)}" if owners else ""
        raise ValueError(
            f"编译 SQL 错误: 维度 '{dim_name}' 不是 {main_table} 的字段，且无法确定唯一归属{detail}。"
            "请改用更明确的维度名称。")

    def compile(self, dsl: Dict[str, Any]) -> str:
        """
        将意图 QueryDSL 编译为标准的 SQL 方言。该过程纯代码拼接，不接触 LLM。
        """
        # 支持单测自定义物理 SQL 直出
        if "custom_select" in dsl:
            # 优先从传入参数中动态获取主表，其次根据指标的 source_table 自动解析，最后默认兜底
            primary_table = dsl.get("custom_table") or dsl.get("primary_table")
            if not primary_table and dsl.get("metrics"):
                metric_name = dsl["metrics"][0].get("name")
                metric_info = self.layer.resolve_metric(metric_name)
                if metric_info:
                    primary_table = metric_info.source_table
            if not primary_table:
                primary_table = "dws_trade_order_daily"
                
            sql = f"SELECT {dsl['custom_select']} FROM {primary_table}"
            if "custom_join" in dsl:
                sql += f" {dsl['custom_join']}"
            return sql

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
                dim = self.layer.resolve_dimension(field, main_table)
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

            dim = self.layer.resolve_dimension(dim_name, main_table)
            if not dim:
                self._reject_unbound_dimension(dim_name, main_table)
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
        metric_output_aliases = {}
        for m_item in metrics:
            m_name = m_item.get("name")
            agg = m_item.get("agg", None)
            ratio_type = m_item.get("ratio_type", None)

            metric = self.layer.resolve_metric(m_name)
            if not metric:
                raise ValueError(f"编译 SQL 错误: 指标未注册 - '{m_name}'")

            calc = metric.calculation
            base_col = f"{main_table}.{calc}" if "." not in calc else calc
            output_alias = self.aggregate_alias(metric.name)
            
            # 高阶分析函数物理生成逻辑 (同比/环比/累计/排名)
            if ratio_type == "mom":
                output_alias = f"{m_name}_mom"
                # 环比计算：(当前期 - 上一期) / 上一期 (使用 LAG 窗口函数)
                select_parts.append(
                    f"(SUM({base_col}) - LAG(SUM({base_col}), 1) OVER (ORDER BY {main_table}.dt)) "
                    f"/ NULLIF(LAG(SUM({base_col}), 1) OVER (ORDER BY {main_table}.dt), 0) AS {output_alias}"
                )
            elif ratio_type == "yoy":
                output_alias = f"{m_name}_yoy"
                # 同比计算：(当前期 - 去年同期) / 去年同期 (使用 LAG 窗口天数对齐)
                select_parts.append(
                    f"(SUM({base_col}) - LAG(SUM({base_col}), 365) OVER (ORDER BY {main_table}.dt)) "
                    f"/ NULLIF(LAG(SUM({base_col}), 365) OVER (ORDER BY {main_table}.dt), 0) AS {output_alias}"
                )
            elif ratio_type == "cumulative":
                output_alias = f"cumulative_{m_name}"
                # 累计计算 (ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                select_parts.append(
                    f"SUM(SUM({base_col})) OVER (ORDER BY {main_table}.dt ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS {output_alias}"
                )
            elif ratio_type == "rank":
                output_alias = f"{m_name}_rank"
                # 排名计算 (DENSE_RANK)
                select_parts.append(
                    f"DENSE_RANK() OVER (ORDER BY SUM({base_col}) DESC) AS {output_alias}"
                )
            elif metric.default_agg == "formula" or agg == "formula":
                if calc == "refund_amount / NULLIF(gmv, 0)":
                    output_alias = "refund_ratio"
                    select_parts.append("SUM(refund_amount) / NULLIF(SUM(gmv), 0) AS refund_ratio")
                else:
                    output_alias = metric.name
                    select_parts.append(f"({calc}) AS {output_alias}")
            else:
                agg_func = agg if agg else metric.default_agg
                select_parts.append(f"{agg_func}({base_col}) AS {output_alias}")
            metric_output_aliases.setdefault(metric.name, output_alias)

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

        # 动态解析表的实际时间列，若表无时间列则跳过时间过滤
        time_col = self._resolve_time_column(main_table)
        if time_col:
            column_types = dict(getattr(self.layer, "discovered_table_columns", {}).get(main_table, []))
            time_type = str(column_types.get(time_col, "")).lower()
            if "timestamp" in time_type or "datetime" in time_type or time_col == "publish_time":
                # Timestamp columns need full converted instants, not truncated dates.
                range_start, range_end = align_timezone_range(start_date, end_date)
            else:
                # Daily partitions identify business dates and must not shift timezones.
                range_start, range_end = start_date[:10], end_date[:10]
            where_conds.append(f"{main_table}.{time_col} BETWEEN '{range_start}' AND '{range_end}'")

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
            dim = self.layer.resolve_dimension(field, main_table)
            if dim:
                tbl_prefix = dim.source_table
                phys_col = dim.source_column
            else:
                self._reject_unbound_dimension(field, main_table)

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
                        ob_col = metric_output_aliases.get(m.name, ob_field)
                        order_cols.append(f"{ob_col} {direction}")
                    else:
                        order_cols.append(f"{ob_field} {direction}")
            sql_parts.append("ORDER BY " + ", ".join(order_cols))
        else:
            if has_month_trend:
                _tc = self._resolve_time_column(main_table) or "created_at"
                sql_parts.append(f"ORDER BY DATE_TRUNC('month', {main_table}.{_tc}) ASC")
            elif group_by_parts:
                ob_col = metric_output_aliases[primary_metric.name]
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
