"""ADS 内表的 StarRocks 物理模型：从 Paimon 表规格派生，而不是另抄一份。

[S1-05] 第一章定位：「StarRocks 离线加工 DWS/DWD 层数据，物化为 StarRocks 内表，
T+1 批加工、毫秒级直查」；[S1-全景] 第七章双路查询：「高频访问的应用指标物化为
StarRocks 内表——毫秒级响应、稳定可控，服务监控大屏与固定报表」。

⚠️ 原文两处口径的处理（本项目设计，已如实登记）：
  [S1-全景] 第四章把 ADS 列为湖仓四层的第四层（11 张 Paimon 表），
  [S1-05] 第一章又说 ADS 是 StarRocks 内表。本项目实现为**两段式**，两处口径都落地：
    ① Flink 批作业：DWS/DWD（Paimon）→ ADS（Paimon）           口径固化在湖内，单一事实源
    ② StarRocks INSERT OVERWRITE：ADS（Paimon 外部表）→ ADS 内表  毫秒直查出口
  因此本模块不重复声明字段，而是**读 catalog 注册表里那 11 张 ADS 表的 TableSpec**，
  按类型映射渲染 StarRocks 内表 DDL——湖表改字段，内表 DDL 重新生成即可，不会漂移。

本模块只读 catalog，不修改 catalog 的任何文件。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib import import_module
from typing import Final

from ..catalog.spec import TableSpec
from ..config import settings
from .errors import BackendUnavailableError, UnknownColumnError
from .products import PRODUCTS, AdsProduct, get_product

__all__ = [
    "StarRocksColumn",
    "StarRocksTable",
    "KEY_VARCHAR_LENGTH",
    "DEFAULT_VARCHAR_LENGTH",
    "load_table_spec",
    "starrocks_table",
    "column_names",
    "require_column",
    "verify_against_catalog",
    "render_all_starrocks_ddl",
]

#: ⚠️ 原文未明确，本项目设计：StarRocks 主键模型的 key 列不能用 VARCHAR(65533)
#: （主键总长度受限），统一收敛到 128 字节。ADS 的主键全是日期/编码/版本号这类短值，
#: 128 足够；超长业务键应该先在 DWD 层哈希再上 ADS。
KEY_VARCHAR_LENGTH: Final[int] = 128
#: ⚠️ 原文未明确，本项目设计：非 key 的 STRING 列统一 VARCHAR(65533)，即 StarRocks 上限。
DEFAULT_VARCHAR_LENGTH: Final[int] = 65533

#: Paimon/Flink SQL 类型 → StarRocks 类型。STRING 需要按是否 key 列分流，单独处理。
_TYPE_MAP: Final[dict[str, str]] = {
    "BOOLEAN": "BOOLEAN",
    "TINYINT": "TINYINT",
    "SMALLINT": "SMALLINT",
    "INT": "INT",
    "BIGINT": "BIGINT",
    "FLOAT": "FLOAT",
    "DOUBLE": "DOUBLE",
    "DATE": "DATE",
    "TIMESTAMP(3)": "DATETIME",
    "TIMESTAMP(6)": "DATETIME",
    "TIMESTAMP": "DATETIME",
    "TIMESTAMP_LTZ(3)": "DATETIME",
    "BYTES": "VARBINARY",
}


def _map_type(paimon_type: str, *, is_key: bool) -> str:
    """Paimon 类型字面量 → StarRocks 类型字面量。

    Args:
        paimon_type: TableSpec 里写的 Flink/Paimon SQL 类型，如 ``TIMESTAMP(3)``。
        is_key: 是否为 StarRocks 主键列（影响 STRING 的目标长度）。

    Returns:
        StarRocks 类型字面量。

    Raises:
        BackendUnavailableError: 出现了映射表里没有的类型——宁可报错也不猜，
            因为猜错会让内表与湖表的语义悄悄分叉。
    """
    t = paimon_type.strip().upper()
    if t == "STRING":
        return f"VARCHAR({KEY_VARCHAR_LENGTH if is_key else DEFAULT_VARCHAR_LENGTH})"
    if t.startswith("DECIMAL"):
        return t
    if t.startswith("VARCHAR") or t.startswith("CHAR"):
        return t
    try:
        return _TYPE_MAP[t]
    except KeyError:
        raise BackendUnavailableError(
            f"Paimon 类型 {paimon_type!r} 没有登记 StarRocks 映射；"
            f"请在 ads.schema._TYPE_MAP 里补充后再生成 DDL"
        ) from None


@dataclass(frozen=True, slots=True)
class StarRocksColumn:
    """StarRocks 内表的一列。"""

    name: str
    type: str
    comment: str
    nullable: bool
    is_key: bool

    def render(self) -> str:
        null = "NULL" if self.nullable else "NOT NULL"
        cmt = self.comment.replace("'", "''")
        return f"  `{self.name}` {self.type} {null} COMMENT '{cmt}'"


@dataclass(frozen=True, slots=True)
class StarRocksTable:
    """一张 ADS StarRocks 内表：由 Paimon TableSpec + 产品元数据派生。"""

    product: AdsProduct
    columns: tuple[StarRocksColumn, ...]
    primary_key: tuple[str, ...]
    buckets: int
    comment: str

    @property
    def name(self) -> str:
        return self.product.table

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)

    # ---- 渲染 ----

    def render_ddl(self, *, database: str | None = None) -> str:
        """渲染 StarRocks 主键模型建表语句。

        主键模型（PRIMARY KEY）而非明细模型的理由：T+1 重刷要覆盖同一天同一维度的行，
        主键模型的 UPSERT 语义让重跑天然幂等，不会出现重刷后一天两行。
        """
        db = database or settings().starrocks.internal_database
        cols = ",\n".join(c.render() for c in self.columns)
        pk = ", ".join(f"`{k}`" for k in self.primary_key)
        # 分桶键取主键第一列：ADS 表的第一主键列不是 stat_date 就是业务主键，
        # 分布均匀且天然是最常用的过滤列。
        dist_key = self.primary_key[0]
        cmt = self.comment.replace("'", "''")
        return (
            f"-- [{self.product.ordinal}] {self.product.title_cn}"
            f"（{self.product.serves_cn}）\n"
            f"-- 计算维度: {self.product.dimensions_cn}\n"
            f"-- 核心指标: {' / '.join(self.product.core_metrics_cn)}\n"
            f"-- 上游: {' + '.join(self.product.source_tables)}\n"
            f"DROP TABLE IF EXISTS `{db}`.`{self.name}`;\n"
            f"CREATE TABLE `{db}`.`{self.name}` (\n{cols}\n)\n"
            f"PRIMARY KEY ({pk})\n"
            f"COMMENT '{cmt}'\n"
            f"DISTRIBUTED BY HASH(`{dist_key}`) BUCKETS {self.buckets}\n"
            f"PROPERTIES (\n"
            f"  'replication_num' = '1',\n"
            f"  'enable_persistent_index' = 'true'\n"
            f");\n"
        )

    def render_insert_overwrite(self, *, database: str | None = None) -> str:
        """渲染「Paimon ADS 外部表 → StarRocks 内表」的 T+1 物化语句。

        用 INSERT OVERWRITE 而不是 INSERT INTO：T+1 重跑必须是幂等覆盖，
        否则补数/重刷会把同一天的指标翻倍。
        """
        cfg = settings().starrocks
        db = database or cfg.internal_database
        paimon = settings().paimon
        cols = ",\n".join(f"  `{c}`" for c in self.column_names)
        # _materialized_at 是内表独有字段，湖表没有，物化时现取当前时间
        select = ",\n".join(
            "  now()" if c == "_materialized_at" else f"  `{c}`" for c in self.column_names
        )
        date_col = self.product.date_column
        where = (
            f"WHERE `{date_col}` = '${{stat_date}}'\n"
            if date_col
            else "-- 该表无日期维度（主键为业务主键），全量覆盖\n"
        )
        return (
            f"-- {self.name}: Paimon ADS → StarRocks 内表（{self.product.title_cn}）\n"
            f"INSERT OVERWRITE `{db}`.`{self.name}` (\n{cols}\n)\n"
            f"SELECT\n{select}\n"
            f"FROM `{cfg.external_catalog}`.`{paimon.database}`.`{self.name}`\n"
            f"{where};\n"
        )


# --------------------------------------------------------------------------- 载入 catalog


def _load_via_registry(table: str) -> TableSpec | None:
    try:
        from ..catalog import registry
    except Exception:  # pragma: no cover - catalog 是同包模块，正常不会失败
        return None
    try:
        return registry.by_name(table)
    except KeyError:
        return None


def _load_via_domain_module(product: AdsProduct) -> TableSpec | None:
    """registry 还没登记该域模块时的兜底：按数据域直接 import 表定义模块。

    catalog.registry._MODULES 需要逐个登记数据域模块（见其 docstring），
    装配顺序上 ADS 服务层可能早于登记完成——这里按 ``tables/_<domain>.py`` 的约定
    直接取 TABLES，避免服务层被装配顺序卡住。只读，不修改 catalog。
    """
    module_name = f"..catalog.tables._{product.domain.key}"
    try:
        mod = import_module(module_name, package=__package__)
    except ImportError:
        return None
    for spec in getattr(mod, "TABLES", ()):
        if spec.name == product.table:
            return spec
    return None


@lru_cache(maxsize=len(PRODUCTS))
def load_table_spec(table: str) -> TableSpec:
    """取某张 ADS 表在 catalog 里的 Paimon 表规格。

    先走 catalog.registry（权威聚合入口），registry 未登记该域时退回按域模块直读。

    Args:
        table: ADS 表名。

    Returns:
        catalog 里声明的 :class:`TableSpec`。

    Raises:
        UnknownTableError: 表不在 11 张产品矩阵内。
        BackendUnavailableError: catalog 里找不到这张表的定义。
    """
    product = get_product(table)
    spec = _load_via_registry(table) or _load_via_domain_module(product)
    if spec is None:
        raise BackendUnavailableError(
            f"catalog 里找不到 {table!r} 的 TableSpec："
            f"请确认 catalog/tables/_{product.domain.key}.py 已定义该表，"
            f"且已在 catalog.registry._MODULES 中登记"
        )
    return spec


@lru_cache(maxsize=len(PRODUCTS))
def starrocks_table(table: str) -> StarRocksTable:
    """把 Paimon 表规格翻译成 StarRocks 内表模型。

    Raises:
        BackendUnavailableError: catalog 缺表、类型无法映射，
            或主键不是表的前若干列（StarRocks 主键模型硬要求 key 列必须排在最前）。
    """
    product = get_product(table)
    spec = load_table_spec(table)
    pk = tuple(spec.primary_key)

    business_cols = list(spec.columns)
    leading = tuple(c.name for c in business_cols[: len(pk)])
    if leading != pk:
        raise BackendUnavailableError(
            f"{table}: StarRocks 主键模型要求 key 列位于表最前且顺序一致，"
            f"当前前 {len(pk)} 列为 {leading}，主键为 {pk}；"
            f"请调整 catalog 里该表的字段顺序"
        )

    cols: list[StarRocksColumn] = []
    for col in business_cols:
        is_key = col.name in pk
        cols.append(
            StarRocksColumn(
                name=col.name,
                type=_map_type(col.type, is_key=is_key),
                comment=col.comment,
                # 主键列必须 NOT NULL；其余列一律可空，避免 T+1 上游缺数直接写失败
                nullable=not is_key,
                is_key=is_key,
            )
        )
    # 物化时间戳：内表独有，用于大屏显示「数据截至」与排查物化是否跑过
    cols.append(
        StarRocksColumn(
            name="_materialized_at",
            type="DATETIME",
            comment="内表物化时间（本项目补充，非湖表字段）",
            nullable=True,
            is_key=False,
        )
    )
    return StarRocksTable(
        product=product,
        columns=tuple(cols),
        primary_key=pk,
        # 分桶数沿用 Paimon 的 bucket 档位（ADS 为 1 或 2，见 spec.BUCKET_TIERS）：
        # ADS 表行数量级极小，档位一致便于两边对账。
        buckets=spec.bucket,
        comment=spec.comment,
    )


@lru_cache(maxsize=len(PRODUCTS))
def column_names(table: str) -> tuple[str, ...]:
    """该 ADS 表的字段白名单（含内表补充的 ``_materialized_at``）。

    查询层的标识符只从这里取，用户输入永远不拼进 SQL 标识符位置。
    """
    return starrocks_table(table).column_names


def require_column(table: str, column: str) -> str:
    """校验字段属于该表并回传字段名，供拼 SQL 前调用。

    Raises:
        UnknownColumnError: 字段不在该表的白名单里。
    """
    allowed = column_names(table)
    if column not in allowed:
        raise UnknownColumnError(f"{table} 没有字段 {column!r}；可用字段：{', '.join(allowed)}")
    return column


def verify_against_catalog() -> dict[str, list[str]]:
    """产品矩阵与 catalog 注册表的对账，返回 {表名: 问题列表}（只含有问题的表）。

    对三件事：表是否存在、主键是否与产品定义的下钻键一致、日期列是否真的存在。
    装配阶段与 CI 都应该跑一次——这是防止「服务层按老口径查、湖表已经改了」的护栏。
    """
    problems: dict[str, list[str]] = {}
    for product in PRODUCTS:
        issues: list[str] = []
        try:
            spec = load_table_spec(product.table)
        except BackendUnavailableError as exc:
            problems[product.table] = [str(exc)]
            continue
        if tuple(spec.primary_key) != product.key_columns:
            issues.append(
                f"主键漂移：catalog={tuple(spec.primary_key)}，产品矩阵={product.key_columns}"
            )
        if spec.layer.value != "ads":
            issues.append(f"层级异常：catalog 声明为 {spec.layer.value}，应为 ads")
        if spec.domain is not product.domain:
            issues.append(
                f"数据域漂移：catalog={spec.domain.name_cn}，产品矩阵={product.domain.name_cn}"
            )
        names = {c.name for c in spec.columns}
        if product.date_column and product.date_column not in names:
            issues.append(f"日期列 {product.date_column!r} 不存在于 catalog 字段清单")
        for key in product.key_columns:
            if key not in names:
                issues.append(f"下钻键 {key!r} 不存在于 catalog 字段清单")
        if issues:
            problems[product.table] = issues
    return problems


def render_all_starrocks_ddl(*, database: str | None = None) -> str:
    """渲染 ddl/starrocks_ads.sql 的完整内容：建库 + 11 张内表 + T+1 物化语句。"""
    cfg = settings().starrocks
    db = database or cfg.internal_database
    paimon = settings().paimon
    head = [
        "-- ===========================================================================",
        "-- ADS 数据产品矩阵 · StarRocks 内表（11 张，T+1 物化，毫秒级直查）",
        "--",
        "-- 来源：[S1-05]《11 张 ADS 数据闭环表开箱即用：智驾数据产品矩阵全览》",
        "--       https://mp.weixin.qq.com/s/c2IZlxJvV8XVBMNUQyGH0Q",
        "--       [S1-全景] 第七章「双路查询」：ADS 内表物化 → 毫秒级响应、稳定可控",
        "--       https://mp.weixin.qq.com/s/UmHoxjBwRtZT0PgwjkL9DQ",
        "--",
        "-- ⚠️ 本文件由 adas_lakehouse.ads.materialize 生成，不要手工编辑：",
        "--      python -m adas_lakehouse.ads.materialize --write",
        "--    字段定义来自 catalog 注册表里 11 张 ADS 表的 TableSpec（单一事实源）。",
        "-- ===========================================================================",
        "",
        f"CREATE DATABASE IF NOT EXISTS `{db}`;",
        "",
        "-- 双路查询的另一路：External Catalog 直查 Paimon（零冗余零搬运，探索式分析走这条）",
        f"-- CREATE EXTERNAL CATALOG `{cfg.external_catalog}` PROPERTIES (",
        "--   'type' = 'paimon',",
        f"--   'paimon.catalog.type' = '{paimon.metastore}',",
        f"--   'paimon.catalog.warehouse' = '{settings().minio.warehouse_path}'",
        "-- );",
        "",
        "-- ---------------------------------------------------------------------------",
        "-- 一、内表 DDL",
        "-- ---------------------------------------------------------------------------",
        "",
    ]
    body = [starrocks_table(p.table).render_ddl(database=db) for p in PRODUCTS]
    tail = [
        "-- ---------------------------------------------------------------------------",
        "-- 二、T+1 物化：Paimon ADS 外部表 → StarRocks 内表",
        "--     ${stat_date} 由调度器按 T+1 传入（yyyy-MM-dd），重跑用 INSERT OVERWRITE 幂等覆盖",
        "-- ---------------------------------------------------------------------------",
        "",
    ]
    inserts = [starrocks_table(p.table).render_insert_overwrite(database=db) for p in PRODUCTS]
    return "\n".join(head) + "\n".join(body) + "\n" + "\n".join(tail) + "\n".join(inserts)
