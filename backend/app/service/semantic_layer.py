# -*- coding: utf-8 -*-
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import re
import os
import sqlglot

# =====================================================================
# 0. 数仓表名分层解析 & 物理列类型分类（通用工具，无业务硬编码）
#
# §7.8 复用 vs 重建 —— 为什么这里有一份解析器，而 apps/data-agent-engine
# （core/table_naming.py + core/translator.py）里还有另一份？
# 结论与逐条差异登记见 backend/docs/table-routing-reuse-decision.md，
# 一致性由 tests/test_s7_reuse.py::TestEngineParserConformance 用「契约测试」
# 钉住（测试里按文件路径加载 engine 模块比对，运行时不 import 引擎）。
# 一句话：引擎路由的输入是人工策展的 Ontology YAML（authority/granularities/
# pre_aggregated/status 都是声明出来的），backend 路由的输入是 information_schema
# 自动发现的裸 Schema（这些元数据一个都没有，只能从表名推断）——
# 输入不同，算法无法直接复用；可复用的是「命名规范」这份数据，已在下方对齐并做超集。
# =====================================================================

# 数仓分层前缀（{layer}_{domain}_{subject}_{grain} 命名规范）
# 注意：这是引擎 core/table_naming.LAYER_PREFIXES 的**超集**——多出 DWT / DM。
# 引擎缺这两个前缀时 dm_trade_gmv 会被解析成 domain="dm"（业务域解析错位），
# 详见 docs/table-routing-reuse-decision.md「差异 D1」。
LAYER_PREFIXES = ("ODS", "DWD", "DWS", "DWT", "ADS", "DM", "DIM")

# 取数优先级：数值越小越"上层"（越接近可直接取数的预聚合结果）。
_LAYER_RANK: Dict[str, int] = {
    "ADS": 0, "DM": 1, "DWT": 1, "DWS": 2, "DIM": 3, "DWD": 4, "ODS": 5, "": 6,
}

# 被视为"预聚合/汇总"的分层：路由时优先于明细层。
PRE_AGGREGATED_LAYERS = ("ADS", "DM", "DWT", "DWS")

# 业务粒度：数值越小越细（细粒度可以向上汇总，粗粒度不能向下拆）。
_GRAIN_ORDER: Dict[str, int] = {
    "minute": 0, "hour": 1, "day": 2, "week": 3, "month": 4, "quarter": 5, "year": 6,
}

# 表名末段 → 业务粒度（di/df/da 为数仓通用的 增量/全量/累积 日粒度后缀）
_GRAIN_SUFFIX: Dict[str, str] = {
    "di": "day", "df": "day", "da": "day", "dd": "day", "1d": "day", "daily": "day",
    "hi": "hour", "hf": "hour", "1h": "hour", "hourly": "hour",
    "wi": "week", "wf": "week", "1w": "week", "weekly": "week",
    "mi": "month", "mf": "month", "1m": "month", "monthly": "month",
    "qi": "quarter", "qf": "quarter", "quarterly": "quarter",
    "yi": "year", "yf": "year", "1y": "year", "yearly": "year",
}


def parse_table_name(table: str) -> Tuple[str, str, str]:
    """
    按数仓命名规范解析表名 → (layer, domain, subject)。

    dwd_ord_order_di -> ("DWD", "ord", "order")
    ads_trade_gmv_1d -> ("ADS", "trade", "gmv")
    dim_store        -> ("DIM", "store", "")
    articles         -> ("",    "articles", "")   # 不规范名尽力解析，不猜测
    """
    parts = [p for p in str(table or "").split("_") if p]
    if not parts:
        return "", "", ""
    head = parts[0].upper()
    if head in LAYER_PREFIXES:
        layer = head
        domain = parts[1] if len(parts) > 1 else ""
        subject = parts[2] if len(parts) > 2 else ""
    else:
        layer = ""
        domain = parts[0]
        subject = parts[1] if len(parts) > 1 else ""
    return layer, domain, subject


def parse_table_grain(table: str) -> str:
    """表名末段 → 业务粒度（day/hour/month...）；无法判定返回 ""（不假设粒度）。"""
    parts = [p for p in str(table or "").lower().split("_") if p]
    if not parts:
        return ""
    return _GRAIN_SUFFIX.get(parts[-1], "")


def table_layer(table: str) -> str:
    """表名 → 分层（ADS/DWS/DWD/...）；不规范表名返回 ""。"""
    return parse_table_name(table)[0]


def table_layer_rank(layer: str) -> int:
    """分层取数优先级，越小越优先（未知分层排在最后，绝不优于已知分层）。"""
    return _LAYER_RANK.get((layer or "").upper(), _LAYER_RANK[""])


def is_pre_aggregated_layer(layer: str) -> bool:
    return (layer or "").upper() in PRE_AGGREGATED_LAYERS


def grain_rank(grain: str) -> int:
    """粒度序号；未知粒度返回 -1（表示未声明，不参与粗细比较）。"""
    return _GRAIN_ORDER.get((grain or "").lower(), -1)


class TableProfile(BaseModel):
    """表的分层画像：供预聚合优先路由与血缘/提示词使用。"""
    table: str
    layer: str = ""
    domain: str = ""
    subject: str = ""
    grain: str = ""
    pre_aggregated: bool = False
    rank: int = _LAYER_RANK[""]


def table_profile(table: str) -> TableProfile:
    layer, domain, subject = parse_table_name(table)
    return TableProfile(
        table=table,
        layer=layer,
        domain=domain,
        subject=subject,
        grain=parse_table_grain(table),
        pre_aggregated=is_pre_aggregated_layer(layer),
        rank=table_layer_rank(layer),
    )


# 半结构化物理类型：json / jsonb / array / struct / map 等。
# 这类列直接 GROUP BY 会产生无法聚合的脏维度（或在多数引擎上直接报错），
# 必须先做抽取（->> / JSON_EXTRACT / UNNEST）才能当维度使用。
_SEMI_STRUCTURED_TOKENS: Tuple[Tuple[str, str], ...] = (
    ("jsonb", "json"),
    ("json", "json"),
    ("array", "array"),
    ("[]", "array"),
    ("struct", "struct"),
    ("map<", "map"),
    ("map(", "map"),
    ("hstore", "map"),
    ("variant", "json"),
    ("super", "json"),
    ("nested", "struct"),
    ("list<", "array"),
    ("set<", "array"),
    ("tuple(", "struct"),
)


def semi_structured_kind(data_type: Any) -> str:
    """物理类型 → 半结构化种类（json/array/struct/map）；普通标量列返回 ""。"""
    text = str(data_type or "").lower()
    if not text:
        return ""
    for token, kind in _SEMI_STRUCTURED_TOKENS:
        if token in text:
            return kind
    return ""


def is_semi_structured_type(data_type: Any) -> bool:
    return bool(semi_structured_kind(data_type))


# 显式业务分区/日期列（created_at / updated_at 属审计时间戳，不在此列）
TIME_PARTITION_COLUMNS: Tuple[str, ...] = ("dt", "date", "publish_date", "publish_time")


# =====================================================================
# 1. 语义建模实体定义
# =====================================================================

METRIC_STATUS_ACTIVE = "active"
METRIC_STATUS_DRAFT = "draft"
METRIC_STATUS_DEPRECATED = "deprecated"
METRIC_STATUSES = (METRIC_STATUS_ACTIVE, METRIC_STATUS_DRAFT, METRIC_STATUS_DEPRECATED)

# §7.9-5：只有 active 口径对生产查询可见。
# draft（探索固化草稿，口径尚未人工确认）与 deprecated（已下线）都不参与默认解析、
# 不进入 self.metrics（= LLM 提示词 / 推荐 / 检索能看到的「在用指标」清单），
# 只能被「显式指定版本」这一个入口取出——显式指定即为使用者的知情选择。
PRODUCTION_METRIC_STATUSES = (METRIC_STATUS_ACTIVE,)


class DraftMetricNotPublished(ValueError):
    """指标只有草稿（draft）口径，草稿不进生产查询路径——除非显式指定该草稿版本。"""

    def __init__(self, metric_name: str, versions: List[str]):
        self.metric_name = metric_name
        self.versions = versions
        super().__init__(
            f"指标 '{metric_name}' 目前只有草稿（draft）口径版本 {versions}，草稿口径不参与生产查询。"
            f"请先将其发布为生效版本（publish_metric_version），"
            f"或在 DSL 中显式指定 version 以明确使用草稿口径。")


class MetricVersionAmbiguity(ValueError):
    """同名指标存在多个生效口径版本且未指定默认版本——必须反问，绝不静默选最新。"""

    def __init__(self, metric_name: str, versions: List[str]):
        self.metric_name = metric_name
        self.versions = versions
        super().__init__(
            f"指标 '{metric_name}' 存在多个生效口径版本 {versions}，且未设置默认版本。"
            f"请显式指定版本（metrics[].version）或先设置默认版本。")


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
    # --- 口径版本化：口径变更必须新开版本，历史版本保留以便历史报表可复现 ---
    version: str = "v1"  # 口径版本号，同名指标可并存多个版本
    status: str = METRIC_STATUS_ACTIVE  # active / draft / deprecated（只有 active 对生产可见）
    changelog: str = ""  # 本版本口径的变更说明（为什么改、什么时候生效）

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
    def __init__(self, database=None):
        self.metrics: Dict[str, Metric] = {}
        self.dimensions: Dict[str, Dimension] = {}
        self.join_paths: List[JoinPath] = []
        self.discovered_table_columns: Dict[str, List] = {}
        self.table_dimensions = {}
        # 口径版本台账：metric name -> [Metric(各版本)]，以及人工指定的默认版本。
        self.metric_versions: Dict[str, List[Metric]] = {}
        self.metric_default_versions: Dict[str, str] = {}
        # 半结构化列台账：table -> {column: kind}（json/array/struct/map）。
        # 这些列不注册为普通维度，避免产生无法聚合的脏维度。
        self.semi_structured_columns: Dict[str, Dict[str, str]] = {}
        # 表分层画像：table -> TableProfile
        self.table_profiles: Dict[str, TableProfile] = {}
        # A layer may model a source other than the currently active singleton,
        # so metadata discovery never reads a database it was not built for.
        self.database = database
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

    # -----------------------------------------------------------------
    # 3.1 口径版本化：注册 / 解析 / 下线
    # -----------------------------------------------------------------
    def _version_ledger(self) -> Dict[str, List[Metric]]:
        """容忍历史状态（如 __dict__ 整体替换）中缺少版本台账的情况。"""
        ledger = getattr(self, "metric_versions", None)
        if ledger is None:
            ledger = {}
            self.metric_versions = ledger
        if not ledger and self.metrics:
            # 旧状态里只有单版本指标，补齐台账后语义完全等价。
            for name, metric in self.metrics.items():
                ledger[name] = [metric]
        return ledger

    def _default_versions(self) -> Dict[str, str]:
        defaults = getattr(self, "metric_default_versions", None)
        if defaults is None:
            defaults = {}
            self.metric_default_versions = defaults
        return defaults

    def register_metric(self, metric: Metric):
        """
        注册指标口径。同名同版本 = 幂等覆盖；同名新版本 = 并存（历史版本永不被静默丢弃），
        这样口径变更后历史报表仍可通过指定版本复现。
        """
        if metric.status not in METRIC_STATUSES:
            raise ValueError(f"非法的指标状态 '{metric.status}'，只能是 {METRIC_STATUSES}")
        versions = self._version_ledger().setdefault(metric.name, [])
        for index, existing in enumerate(versions):
            if existing.version == metric.version:
                versions[index] = metric
                break
        else:
            versions.append(metric)

        resolved = self.resolve_metric_version(metric.name)
        if resolved is not None:
            self.metrics[metric.name] = resolved
        elif self.active_metric_versions(metric.name):
            if metric.name not in self.metrics:
                # 多版本且无默认版本：登记一个代表项供检索/列举，
                # 但 resolve_metric() 仍会拒绝解析，强制调用方反问。
                self.metrics[metric.name] = metric
        else:
            # §7.9-5：一个生效版本都没有（只有 draft，或全部已下线）时，
            # 该指标不进"在用指标"清单——否则草稿口径会随提示词/推荐直接对生产可见。
            # 版本台账仍保留，显式指定版本依旧可取用。
            current = self.metrics.get(metric.name)
            if current is None or current.status != METRIC_STATUS_ACTIVE:
                self.metrics.pop(metric.name, None)

    def metric_version_candidates(self, name: str) -> List[Metric]:
        """返回该指标已登记的所有版本（含 deprecated），按注册顺序。"""
        return list(self._version_ledger().get(name, []))

    def active_metric_versions(self, name: str) -> List[Metric]:
        return [m for m in self.metric_version_candidates(name)
                if m.status == METRIC_STATUS_ACTIVE]

    def draft_metric_versions(self, name: str) -> List[Metric]:
        """该指标的草稿口径版本（不参与生产查询，仅供治理界面/显式复核）。"""
        return [m for m in self.metric_version_candidates(name)
                if m.status == METRIC_STATUS_DRAFT]

    def is_draft_only_metric(self, name: str) -> bool:
        """只有草稿口径、没有任何生效版本 → 生产查询必须拒绝（除非显式点名版本）。"""
        candidates = self.metric_version_candidates(name)
        return bool(candidates) and not self.active_metric_versions(name) \
            and any(m.status == METRIC_STATUS_DRAFT for m in candidates)

    def production_metrics(self) -> Dict[str, Metric]:
        """对生产查询/提示词可见的指标（已排除 draft 与全部下线的指标）。"""
        return {name: metric for name, metric in self.metrics.items()
                if metric.status in PRODUCTION_METRIC_STATUSES}

    def resolve_metric_version(self, name: str, version: str = "") -> Optional[Metric]:
        """
        口径版本解析三级规则（与湖仓引擎 ontology 的 resolve_version 同义）：
        1) 显式版本命中 → 直接返回（允许 deprecated/draft：前者用于复现历史报表，
           后者是使用者显式点名草稿口径的唯一入口——§7.9-5 要求的"显式请求"）；
        2) 只有一个 active 版本 → 返回该版本；
        3) 多个 active 版本 → 有 default_version 用默认，否则返回 None（调用方必须反问）。
        绝不静默返回"最新"版本，也绝不在无人点名时返回 draft 版本。
        """
        candidates = self.metric_version_candidates(name)
        if not candidates:
            return None
        if version:
            for metric in candidates:
                if metric.version == version:
                    return metric
            return None
        actives = [m for m in candidates if m.status == METRIC_STATUS_ACTIVE]
        if len(actives) <= 1:
            return actives[0] if actives else None
        default_version = self._default_versions().get(name)
        if default_version:
            for metric in actives:
                if metric.version == default_version:
                    return metric
        return None

    def metric_version_ambiguity(self, name: str) -> List[str]:
        """歧义时返回候选 active 版本号列表，无歧义返回 []。"""
        if not name or self.resolve_metric_version(name) is not None:
            return []
        actives = self.active_metric_versions(name)
        return [m.version for m in actives] if len(actives) > 1 else []

    def set_default_metric_version(self, name: str, version: str) -> Metric:
        """人工治理动作：为多版本指标指定默认口径版本。"""
        for metric in self.active_metric_versions(name):
            if metric.version == version:
                self._default_versions()[name] = version
                self.metrics[name] = metric
                return metric
        raise ValueError(f"指标 '{name}' 不存在生效版本 '{version}'，无法设为默认版本。")

    def publish_metric_version(self, name: str, version: str) -> Metric:
        """
        人工治理动作：把草稿口径发布为生效口径（draft -> active）。
        这是草稿进入生产查询路径的唯一非显式入口，必须由人显式调用，
        绝不能由自动发现/探索流程代劳。
        """
        for metric in self.metric_version_candidates(name):
            if metric.version == version:
                if metric.status == METRIC_STATUS_DEPRECATED:
                    raise ValueError(
                        f"指标 '{name}' 的版本 '{version}' 已下线，不能直接发布；请新开一个版本。")
                metric.status = METRIC_STATUS_ACTIVE
                resolved = self.resolve_metric_version(name)
                if resolved is not None:
                    self.metrics[name] = resolved
                elif name not in self.metrics:
                    self.metrics[name] = metric
                return metric
        raise ValueError(f"指标 '{name}' 不存在版本 '{version}'。")

    def deprecate_metric_version(self, name: str, version: str) -> Metric:
        """下线某个口径版本：不再参与默认解析，但仍可被显式版本复现。"""
        for metric in self.metric_version_candidates(name):
            if metric.version == version:
                metric.status = METRIC_STATUS_DEPRECATED
                if self._default_versions().get(name) == version:
                    self._default_versions().pop(name, None)
                resolved = self.resolve_metric_version(name)
                if resolved is not None:
                    self.metrics[name] = resolved
                elif not self.active_metric_versions(name):
                    # 全部版本已下线：移出在用清单（检索/推荐不再提议），
                    # 但版本台账保留，显式指定版本仍可复现历史报表。
                    self.metrics.pop(name, None)
                return metric
        raise ValueError(f"指标 '{name}' 不存在版本 '{version}'。")

    def canonical_metric_name(self, term: str) -> Optional[str]:
        """名称/别名 → 指标标准名（不做版本解析），用于歧义提示与版本查询。"""
        if not term:
            return None
        term = term.strip().lower()
        if term in self.metrics:
            return term
        for name, metric in self.metrics.items():
            if term == metric.name or term in metric.aliases:
                return name
        for name, versions in self._version_ledger().items():
            for metric in versions:
                if term == metric.name or term in metric.aliases:
                    return name
        mapped = TERM_DICTIONARY.get(term)
        if isinstance(mapped, str) and mapped in self.metrics:
            return mapped
        return None

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
        from sqlalchemy import inspect

        if self.database is not None:
            db_service = self.database
        else:
            from app.service.db_service import db_service

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
        # （元素保持 (列名, 类型字符串) 二元组，下游 dict(...) / 解包用法不变）
        self.discovered_table_columns = table_columns

        # 1.5 表名分层解析 + 半结构化列识别
        # json/jsonb/array/struct/map 列不能直接当维度 GROUP BY，单独登记。
        self.table_profiles = {tbl: table_profile(tbl) for tbl in table_columns}
        self.semi_structured_columns = {}
        for tbl, cols in table_columns.items():
            semi = {c: semi_structured_kind(dtype) for c, dtype in cols
                    if is_semi_structured_type(dtype)}
            if semi:
                self.semi_structured_columns[tbl] = semi
                print(f"[Auto Schema] Detected semi-structured columns on {tbl}: {semi} "
                      f"(excluded from plain dimensions)")

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
                for c, c_type in cols:
                    if c != pk_col and not c.endswith("_id") and not is_semi_structured_type(c_type):
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
                                                  if col[0] not in ["id", c] and not col[0].endswith("_id")
                                                  and not is_semi_structured_type(col[1])],
                            default_agg="SUM",
                            source_table=tbl,
                            authorized_roles=["admin", "analyst", "user"]
                        ))
                        print(f"[Auto Schema] Automatically processed and registered metric: {sum_metric_name}")

            # (C) 自动加工维度
            for c, dtype in cols:
                if c.endswith("_id") or c == "id":
                    continue
                # 半结构化列（json/jsonb/array/struct/map）不能无条件注册成维度：
                # 直接 GROUP BY 会得到无法聚合的脏维度，或在多数引擎上直接报错。
                # 这类列已登记在 semi_structured_columns，需显式抽取后才可作维度。
                if is_semi_structured_type(dtype):
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

    def resolve_metric(self, term: str, version: str = "") -> Optional[Metric]:
        """
        通过指标名字或别名查找匹配，并按口径版本三级规则定版。
        多个生效版本且无默认版本时返回 None（绝不静默选最新），
        调用方可用 metric_version_ambiguity() 拿到候选版本去反问用户。
        """
        if not term:
            return None
        name = self.canonical_metric_name(term)
        if name is None:
            return None
        resolved = self.resolve_metric_version(name, version)
        if resolved is not None:
            return resolved
        if version:
            return None
        # 无版本台账（例如外部直接写 self.metrics）时退回原有行为。
        if name not in self._version_ledger():
            return self.metrics.get(name)
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

    # -----------------------------------------------------------------
    # 3.2 表分层画像 / 半结构化列 / 预聚合优先路由
    # -----------------------------------------------------------------
    def profile_of(self, table: str) -> TableProfile:
        """表 → 分层画像（layer/domain/subject/grain），缺失时按表名现算。"""
        profiles = getattr(self, "table_profiles", None)
        if profiles is None:
            profiles = {}
            self.table_profiles = profiles
        if table not in profiles:
            profiles[table] = table_profile(table)
        return profiles[table]

    def semi_structured_map(self, table: str) -> Dict[str, str]:
        """表上的半结构化列 → 种类（json/array/struct/map）。"""
        return dict(getattr(self, "semi_structured_columns", {}).get(table, {}))

    def is_semi_structured_column(self, table: str, column: str) -> bool:
        if column in self.semi_structured_map(table):
            return True
        for name, dtype in getattr(self, "discovered_table_columns", {}).get(table, []):
            if name == column:
                return is_semi_structured_type(dtype)
        return False

    def column_names_of(self, table: str) -> List[str]:
        return [name for name, _ in
                getattr(self, "discovered_table_columns", {}).get(table, [])]

    def time_column_of(self, table: str) -> Optional[str]:
        """显式业务分区列（created_at/updated_at 属审计列，不在此列）。"""
        columns = self.column_names_of(table)
        for candidate in TIME_PARTITION_COLUMNS:
            if candidate in columns:
                return candidate
        return None

    @staticmethod
    def preagg_routing_enabled() -> bool:
        """开关：SEMANTIC_PREAGG_ROUTING=0 可关闭预聚合优先路由，回到「指标 source_table」行为。"""
        return os.getenv("SEMANTIC_PREAGG_ROUTING", "1").strip().lower() not in (
            "0", "false", "off", "no")

    def _grain_compatible(self, table: str, required_grain: str) -> bool:
        """候选表粒度必须不粗于请求粒度；未声明粒度视为兼容（不臆测）。"""
        candidate = grain_rank(self.profile_of(table).grain)
        required = grain_rank(required_grain)
        if candidate < 0 or required < 0:
            return True
        return candidate <= required

    def route_primary_table(self, metrics: List[Metric],
                            dimension_names: Optional[List[str]] = None,
                            required_grain: str = "day",
                            filter_fields: Optional[List[str]] = None) -> Tuple[str, str]:
        """
        预聚合优先路由：在物理上能覆盖「全部度量列 + 全部维度列」的表中，
        优先选择 ADS/DWS 等汇总层，而不是盲目使用 metrics[0].source_table（往往是明细表）。

        返回 (table, mode)，mode 为 "preagg"（改路由到汇总层）或 "base"（维持原表）。
        只有当候选表分层严格优于原表、粒度不更粗、且同样具备业务时间列时才改路由；
        任何一条不满足都退回原表，保证既有行为不被破坏。

        §7.8：与引擎 core/translator._select_table_multi 是**同名不同题**的两个算法——
        引擎按人工策展的 authority/pre_aggregated/granularities 字段选表，
        这里只有 information_schema 能给的东西（表名 + 列名 + 类型），
        所以用分层前缀推断预聚合、用列集合覆盖判断可行性。
        逐条差异与"为什么不合并"见 backend/docs/table-routing-reuse-decision.md。
        """
        if not metrics:
            raise ValueError("route_primary_table 需要至少一个已解析的指标。")
        base = metrics[0].source_table
        if not self.preagg_routing_enabled():
            return base, "base"

        table_columns = getattr(self, "discovered_table_columns", {}) or {}
        if base not in table_columns:
            return base, "base"

        # 度量列必须是裸列名，表达式口径（含函数/运算）无法跨表平移。
        measure_cols = set()
        for metric in metrics:
            calc = (metric.calculation or "").strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", calc):
                return base, "base"
            measure_cols.add(calc)

        # 分组维度与过滤字段都必须在候选表上直连存在：
        # 汇总表往往没有明细表的 JOIN 路径，缺列就会退化成错误的兜底 JOIN。
        needed_cols = set(measure_cols)
        for field in list(dimension_names or []) + list(filter_fields or []):
            if not field or field == "month" or field in TIME_PARTITION_COLUMNS:
                continue
            if field in ("created_at", "updated_at"):
                continue
            dimension = self.resolve_dimension(field, base)
            needed_cols.add(dimension.source_column if dimension else field)

        base_profile = self.profile_of(base)
        base_has_time = self.time_column_of(base) is not None

        candidates = []
        for table in table_columns:
            if table == base:
                continue
            profile = self.profile_of(table)
            if not profile.pre_aggregated or profile.rank >= base_profile.rank:
                continue
            if not needed_cols.issubset(set(self.column_names_of(table))):
                continue
            if not self._grain_compatible(table, required_grain):
                continue
            if base_has_time and self.time_column_of(table) is None:
                continue
            candidates.append((profile, table))

        if not candidates:
            return base, "base"

        candidates.sort(key=lambda item: (
            item[0].rank,
            item[0].domain != base_profile.domain,   # 同业务域优先
            len(self.column_names_of(item[1])),      # 同层级下列更少 = 更贴合的汇总表
            item[1],
        ))
        return candidates[0][1], "preagg"

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
# 3.9 §7.9-6 截断声明：结果被截断必须在返回结果里说出来
#
# 两处会静默丢数据：
#   * DSL 默认 LIMIT 10（dsl.get("limit", 10)）——用户问"各分类文章数"，
#     实际 40 个分类只回 10 个，看起来却像全部；
#   * 执行器行数上限（引擎 core/executor.py MAX_ROWS=1000，backend 侧由
#     EXECUTOR_MAX_ROWS 声明同一口径）。
# 截断本身没问题，**不声明**才是正确性问题：用户会拿不完整的数据下结论。
# 这里只负责"生成可直接贴进答案的声明"，由调用方（ask_agent / skills）挂到
# conclusion 与 details 上。
# =====================================================================

# DSL 未显式给 limit 时的默认行数（与 DSLCompiler.compile 中的默认值同源）
DEFAULT_ROW_LIMIT = 10
# 执行器硬上限；与 apps/data-agent-engine/backend/core/executor.py 的 MAX_ROWS 同口径
EXECUTOR_MAX_ROWS = int(os.getenv("EXECUTOR_MAX_ROWS", "1000") or 1000)

LIMIT_SOURCE_DSL = "dsl"            # 用户/意图解析显式指定
LIMIT_SOURCE_DEFAULT = "default"    # 编译器默认 LIMIT
LIMIT_SOURCE_EXECUTOR = "executor"  # 执行器 MAX_ROWS 兜底截断


class TruncationNotice(BaseModel):
    """一次结果截断的完整声明（可直接序列化进 API 响应的 details）。"""
    truncated: bool = False           # 是否确认被截断
    uncertain: bool = False           # 返回行数正好等于上限，但没探测总量 → 无法确认
    returned_rows: int = 0            # 实际呈现给用户的行数
    total_rows: Optional[int] = None  # 已知总行数（探测/COUNT 得到），未知为 None
    limit: int = 0
    limit_source: str = LIMIT_SOURCE_DEFAULT
    message: str = ""                 # 面向用户的声明文案，空串表示无需声明

    def apply_to(self, conclusion: str) -> str:
        """把声明前置到结论文案上；未截断时原样返回。"""
        if not self.message:
            return conclusion
        return f"{self.message}\n{conclusion}" if conclusion else self.message


def describe_truncation(returned_rows: int,
                        limit: Optional[int],
                        limit_source: str = LIMIT_SOURCE_DEFAULT,
                        total_rows: Optional[int] = None) -> TruncationNotice:
    """
    生成截断声明。三种情形，措辞必须如实区分，不许把"可能"说成"确定"：
      1) 已知总行数且大于返回行数 → "共 N 行，仅返回 M 行"；
      2) 未知总行数、但返回行数已顶到上限 → "可能被截断"，并给出确认方法；
      3) 其余 → 不截断，message 为空。
    """
    returned_rows = max(int(returned_rows or 0), 0)
    limit_value = int(limit or 0)
    source_desc = {
        LIMIT_SOURCE_DSL: "查询显式指定",
        LIMIT_SOURCE_DEFAULT: "系统默认上限",
        LIMIT_SOURCE_EXECUTOR: "执行器行数上限",
    }.get(limit_source, limit_source)

    if total_rows is not None and int(total_rows) > returned_rows:
        return TruncationNotice(
            truncated=True, uncertain=False, returned_rows=returned_rows,
            total_rows=int(total_rows), limit=limit_value, limit_source=limit_source,
            message=(f"⚠️ 本次结果被截断：共 {int(total_rows)} 行，仅返回 {returned_rows} 行"
                     f"（LIMIT {limit_value}，{source_desc}）。"
                     f"以下结论与图表仅基于这 {returned_rows} 行，不代表全量数据。"))

    if limit_value > 0 and returned_rows >= limit_value:
        return TruncationNotice(
            truncated=False, uncertain=True, returned_rows=returned_rows,
            total_rows=None, limit=limit_value, limit_source=limit_source,
            message=(f"⚠️ 本次结果可能被截断：返回行数 {returned_rows} 已达上限 "
                     f"{limit_value}（{source_desc}），无法确认是否还有更多数据。"
                     f"请调大 limit 或改用汇总口径后再下结论。"))

    return TruncationNotice(returned_rows=returned_rows, limit=limit_value,
                            limit_source=limit_source, total_rows=total_rows)


def split_probe_rows(rows: List[Any], limit: int) -> Tuple[List[Any], bool]:
    """
    探测式截断判定：SQL 按 limit+1 取数，返回 (呈现给用户的行, 是否确认被截断)。
    多取的那一行只用于判定"还有更多"，绝不呈现。
    """
    limit_value = int(limit or 0)
    if limit_value <= 0:
        return list(rows), False
    truncated = len(rows) > limit_value
    return list(rows)[:limit_value], truncated


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
        # 最近一次编译的选表决策（便于上层解释「这条 SQL 为什么走了汇总表」）
        self.last_route: Dict[str, Any] = {}
        # 最近一次编译实际施加的行数上限（§7.9-6 截断声明的依据）：
        # {"limit": int, "limit_source": "dsl"/"default", "probe": bool, "sql_limit": int}
        self.last_limit: Dict[str, Any] = {}

    def _resolve_metric_item(self, m_item: Dict[str, Any]) -> Optional[Metric]:
        """按 DSL 条目定版解析指标；DSL 可用 version 字段锁定历史口径复现报表。"""
        if not isinstance(m_item, dict):
            return None
        return self.layer.resolve_metric(m_item.get("name") or "",
                                         str(m_item.get("version") or ""))

    def _require_metric_item(self, m_item: Dict[str, Any]) -> Metric:
        name = m_item.get("name") if isinstance(m_item, dict) else None
        metric = self._resolve_metric_item(m_item if isinstance(m_item, dict) else {})
        if metric is not None:
            return metric
        canonical = self.layer.canonical_metric_name(name or "")
        requested_version = str(m_item.get("version") or "") if isinstance(m_item, dict) else ""
        if canonical and requested_version:
            known = [m.version for m in self.layer.metric_version_candidates(canonical)]
            raise ValueError(
                f"编译 SQL 错误: 指标 '{name}' 不存在口径版本 '{requested_version}'（已登记版本：{known}）。")
        ambiguous = self.layer.metric_version_ambiguity(canonical) if canonical else []
        if ambiguous:
            raise MetricVersionAmbiguity(canonical, ambiguous)
        # §7.9-5：只有草稿口径时，报错必须说清"是草稿不是没有"，
        # 否则使用者会以为指标缺失而重新造一个同名口径。
        if canonical and getattr(self.layer, "is_draft_only_metric", lambda _n: False)(canonical):
            raise DraftMetricNotPublished(
                canonical, [m.version for m in self.layer.draft_metric_versions(canonical)])
        raise ValueError(f"编译 SQL 错误: 语义层中未注册此指标 - '{name}'")

    # -----------------------------------------------------------------
    # §7.9-6 截断声明 API（供 ask_agent / skills 把声明挂进返回结果）
    # -----------------------------------------------------------------
    def compile_with_probe(self, dsl: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """
        编译一条「多取一行」的 SQL（LIMIT n+1），用于**确定性**判定截断，
        不需要额外跑一次 COUNT。返回 (sql, last_limit)。
        调用方拿到结果后必须用 split_probe_rows(rows, info["limit"]) 裁回 n 行，
        多出来的那一行只用于判定，绝不呈现给用户。
        原 compile() 行为完全不变（SQL 文本里仍是 LIMIT n），这是可选的新入口。
        """
        probe_dsl = dict(dsl)
        probe_dsl["_truncation_probe"] = True
        sql = self.compile(probe_dsl)
        return sql, dict(self.last_limit)

    def truncation_notice(self, returned_rows: int,
                          total_rows: Optional[int] = None) -> TruncationNotice:
        """
        基于最近一次 compile() 施加的上限生成截断声明。
        probe 模式下把「返回行数 > limit」翻译成已知的总量下界，
        措辞退让为"至少 N 行"，绝不谎报精确总数。
        """
        info = self.last_limit or {}
        limit = int(info.get("limit") or 0)
        source = info.get("limit_source") or LIMIT_SOURCE_DEFAULT
        rows = max(int(returned_rows or 0), 0)
        if info.get("probe") and total_rows is None and limit > 0 and rows > limit:
            source_desc = "查询显式指定" if source == LIMIT_SOURCE_DSL else "系统默认上限"
            return TruncationNotice(
                truncated=True, uncertain=False, returned_rows=limit,
                total_rows=None, limit=limit, limit_source=source,
                message=(f"⚠️ 本次结果被截断：仅返回前 {limit} 行"
                         f"（LIMIT {limit}，{source_desc}），实际总行数多于 {limit} 行。"
                         f"以下结论与图表仅基于这 {limit} 行，不代表全量数据。"))
        if info.get("probe") and limit > 0:
            if total_rows is None:
                # 探测过且没多出那一行 = 确定没有更多数据，不能再含糊说"可能被截断"。
                return TruncationNotice(truncated=False, uncertain=False,
                                        returned_rows=min(rows, limit), limit=limit,
                                        limit_source=source)
            rows = min(rows, limit)
        return describe_truncation(rows, limit, source, total_rows=total_rows)

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
        for candidate in TIME_PARTITION_COLUMNS:
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

    def _reject_semi_structured_dimension(self, column: str, table: str) -> None:
        """
        半结构化列（json/jsonb/array/struct/map）不能直接当维度：
        GROUP BY 一个 JSON/数组列要么报错，要么产出无法聚合的脏维度。
        必须先抽取标量字段（-> / ->> / JSON_EXTRACT / UNNEST）再分组。
        """
        kind = self.layer.semi_structured_map(table).get(column) if hasattr(
            self.layer, "semi_structured_map") else None
        if not kind:
            for name, dtype in getattr(self.layer, "discovered_table_columns", {}).get(table, []):
                if name == column:
                    kind = semi_structured_kind(dtype)
                    break
        if not kind:
            return
        raise ValueError(
            f"编译 SQL 错误: {table}.{column} 是半结构化列（{kind}），不能直接作为分组维度。"
            f"请先抽取其中的标量字段（如 {column}->>'key'）后再作为维度使用。")

    def _reject_unbound_dimension(self, dim_name: str, main_table: str) -> None:
        """
        维度未能绑定到具体物理表时，绝不能退化成 `主表.维度名` 猜列：
        该列可能根本不存在，或恰好命中另一个业务域的同名列，从而静默返回错误结果。
        """
        columns = {name for name, _ in
                   getattr(self.layer, "discovered_table_columns", {}).get(main_table, [])}
        if dim_name in columns:
            self._reject_semi_structured_dimension(dim_name, main_table)
            return
        if not columns:
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
        # 每次编译先复位截断台账，避免上一次编译的上限被误当成本次的。
        self.last_limit = {"limit": 0, "limit_source": LIMIT_SOURCE_DEFAULT,
                           "probe": False, "sql_limit": 0}
        # 支持单测自定义物理 SQL 直出
        if "custom_select" in dsl:
            # 优先从传入参数中动态获取主表，其次根据指标的 source_table 自动解析
            primary_table = dsl.get("custom_table") or dsl.get("primary_table")
            if not primary_table:
                for m_item in dsl.get("metrics", []) or []:
                    metric_info = self._resolve_metric_item(m_item)
                    if metric_info:
                        primary_table = metric_info.source_table
                        break
            if not primary_table:
                # 绝不回落到硬编码的示例表名（那会生成指向不存在物理表的 SQL）。
                raise ValueError(
                    "编译 SQL 错误: custom_select 未能确定主表，"
                    "请在 DSL 中提供 custom_table/primary_table，或使用已注册的指标。")


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
        limit_val = dsl.get("limit", DEFAULT_ROW_LIMIT)
        # §7.9-6：记下本次实际施加的行数上限，供上层生成截断声明。
        # probe 模式下 SQL 里写的是 limit+1（多取一行只为判定"还有更多"）。
        probe = bool(dsl.get("_truncation_probe"))
        try:
            effective_limit = int(limit_val) if limit_val else 0
        except (TypeError, ValueError):
            # 非法 limit 不在这里报错（保持既有行为：交给下游网闸/数据库判），
            # 只是本次无法生成截断声明。
            effective_limit = 0
        sql_limit = effective_limit + 1 if (probe and effective_limit > 0) else limit_val
        self.last_limit = {
            "limit": effective_limit,
            "limit_source": LIMIT_SOURCE_DSL if "limit" in dsl else LIMIT_SOURCE_DEFAULT,
            "probe": probe and effective_limit > 0,
            "sql_limit": int(sql_limit) if sql_limit else 0,
        }
        limit_val = sql_limit

        if not metrics:
            raise ValueError("编译 SQL 错误: 意图 DSL 中未提供任何指标 (metrics)。")

        # 1. 确定事实主表：先按口径版本定版，再做预聚合优先路由
        primary_metric_info = metrics[0]
        metric_name = primary_metric_info.get("name")
        primary_metric = self._require_metric_item(primary_metric_info)

        resolved_metrics = [primary_metric]
        for m_item in metrics[1:]:
            extra = self._resolve_metric_item(m_item)
            if extra is not None:
                resolved_metrics.append(extra)

        dimension_names = [d.get("name") for d in dimensions if isinstance(d, dict)]
        filter_fields = [f.get("field") for f in filters if isinstance(f, dict)]
        main_table, route_mode = self.layer.route_primary_table(
            resolved_metrics, dimension_names,
            required_grain="month" if "month" in dimension_names else "day",
            filter_fields=filter_fields)
        self.last_route = {
            "base_table": primary_metric.source_table,
            "main_table": main_table,
            "mode": route_mode,
            "metric_versions": {m.name: m.version for m in resolved_metrics},
        }
        if route_mode == "preagg":
            print(f"[Pre-agg Routing] {primary_metric.source_table} -> {main_table} "
                  f"(layer {self.layer.profile_of(main_table).layer})")


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

            # 已注册维度也可能指向半结构化列（人工登记或历史遗留），同样拒绝直接分组。
            self._reject_semi_structured_dimension(dim.source_column, dim.source_table)

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

            metric = self._resolve_metric_item(m_item)
            if not metric:
                canonical = self.layer.canonical_metric_name(m_name or "")
                ambiguous = self.layer.metric_version_ambiguity(canonical) if canonical else []
                if ambiguous:
                    raise MetricVersionAmbiguity(canonical, ambiguous)
                if canonical and getattr(self.layer, "is_draft_only_metric",
                                         lambda _n: False)(canonical):
                    raise DraftMetricNotPublished(
                        canonical,
                        [m.version for m in self.layer.draft_metric_versions(canonical)])
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
