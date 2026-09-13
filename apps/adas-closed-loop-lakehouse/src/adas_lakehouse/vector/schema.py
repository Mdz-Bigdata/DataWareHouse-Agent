"""向量明细表 dwd_mining_image_vector_detail 的字段契约与三种物理形态的 DDL 渲染。

来源：原文第二章《向量落湖：一张千万级的向量明细表》。三个设计决策逐字落地：
  1. 图文双向量同行——image_embedding 与 text_embedding 都来自同一个 CLIP 模型的双塔
     编码，保证图文向量在同一空间，这是文搜图 / 图搜图 / 混合检索共用一张表的前提；
  2. 按 dt 分区——全湖唯一按日期分区的明细表，支撑生命周期降冷与索引的分区级增量刷新；
  3. embedding_version 入主键——模型换代时新旧向量并存，用 vector_status 区分
     active / deprecated，检索默认只查 active 版本。

三种物理形态：
  · Paimon 明细表（单一事实源）           -> paimon_table_spec() / render_paimon_ddl()
  · StarRocks External Catalog 外部表      -> render_external_catalog_ddl()（第一档，免冗余）
  · StarRocks 内表（降级冗余）             -> render_internal_table_ddl()（第二档，见 backend.py）

⚠️ 表结构的唯一事实源是 :mod:`adas_lakehouse.catalog.registry`（定义在
   ``catalog/tables/_mining.py``）。本模块**不再**自带一份 TableSpec——列、主键、分区、
   bucket 全部按表名从 registry 取，子系统只引用不定义。向量子系统唯一往规格上追加的
   是一项**表属性**（不是结构）：vector_meta 的 ``variant.shreddingSchema``。
   registry 的表注释里写明了这条分工——catalog 是最底层契约，不反向依赖子系统，
   所以热路径的 shredding schema 由向量子系统在建表时按 :mod:`.variant` 追加。

⚠️ 近义异名归一（详见 ``catalog/tables/_mining.py`` 模块 docstring）：向量侧概念里的
   caption / encoded_at，在 registry 里的列名是 ``caption_text`` / ``embed_time``，
   见 :data:`CAPTION_COLUMN` / :data:`EMBED_TIME_COLUMN`。
"""

from __future__ import annotations

from dataclasses import replace
from enum import Enum
from functools import lru_cache

# 只取 TableSpec 这个类型契约（catalog/__init__.py 是空的，不会连带拉起 registry）；
# registry 本身在 paimon_table_spec() 里延迟导入。
from ..catalog.spec import TableSpec
from .params import DEFAULT_VECTOR_DIM

__all__ = [
    "VECTOR_TABLE_NAME",
    "PARTITION_FIELD",
    "UPSERT_KEY",
    "PRIMARY_KEY",
    "CAPTION_COLUMN",
    "EMBED_TIME_COLUMN",
    "SCALAR_FILTER_COLUMNS",
    "VectorStatus",
    "paimon_table_spec",
    "render_paimon_ddl",
    "render_external_catalog_ddl",
    "render_internal_table_ddl",
    "external_table_ref",
    "internal_table_ref",
    "vector_column_names",
    "vector_columns_for_projection",
]


#: 原文第二章：「图片向量统一落湖于 DWD 层的 dwd_mining_image_vector_detail」。
VECTOR_TABLE_NAME: str = "dwd_mining_image_vector_detail"

#: 原文第二章 + 全湖分区全景：这是全湖仅有的 6 张分区表之一，且是唯一按 dt 分区的明细表。
PARTITION_FIELD: str = "dt"

#: 原文第三章第 ④ 步：「按 (image_id, embedding_version) Upsert，重跑无副作用」。
#: 这是业务语义上的幂等键。
UPSERT_KEY: tuple[str, ...] = ("image_id", "embedding_version")

#: 落到 Paimon 的物理主键 = 幂等键 + 分区字段。
#: 主键三原则之三：分区表主键必须包含分区字段（Paimon 硬要求，spec.validate 会拦）。
PRIMARY_KEY: tuple[str, ...] = ("image_id", "embedding_version", "dt")


class VectorStatus(str, Enum):
    """向量行状态。原文第二章：「用 vector_status 区分 active / deprecated，
    检索默认只查 active 版本——灰度切换与一键回滚都不需要重写数据」。

    注意与 ids.ArtifactStatus（active / superseded / invalid）区分：
    ArtifactStatus 描述产物血缘，VectorStatus 描述某个 embedding_version 的向量是否在役。
    """

    ACTIVE = "active"
    DEPRECATED = "deprecated"


#: 向量侧概念 -> registry 列名的两处近义异名归一（catalog/tables/_mining.py 已登记）。
#: 写回与 StarRocks 侧渲染一律用这两个常量，不再在各处硬编码字符串。
#:   向量侧 caption    -> registry caption_text（与图片标签表的 CAPTION 正文同名同义）
#:   向量侧 encoded_at -> registry embed_time
CAPTION_COLUMN: str = "caption_text"
EMBED_TIME_COLUMN: str = "embed_time"


#: 原文第五章第 ② 步「标量过滤编译」涉及的字段：时间 / GPS / 摄像头 / 标签。
#: search.py 的过滤器编译白名单以此为准，不在白名单的字段一律拒绝拼进 SQL（防注入）。
SCALAR_FILTER_COLUMNS: frozenset[str] = frozenset(
    {
        "dt",
        "capture_time",
        "camera_id",
        "camera_position",
        "gps_lat",
        "gps_lon",
        "geo_grid",
        "city_code",
        "weather",
        "light_condition",
        "road_type",
        "scene_tag",
        "project_code",
        "vehicle_code",
        "dataset_id",
        "dataset_version",
        "cost_tier",
    }
)


def vector_column_names() -> frozenset[str]:
    """registry 里这张表的全部列名（含系统字段）。写回 / 投影的列名自检以它为准。"""
    return frozenset(c.name for c in paimon_table_spec().all_columns())


def _check_contract(spec: TableSpec) -> None:
    """把「子系统依赖的列 / 键不在 registry 里」这件事变成一次响亮的失败。

    列不存在不会报错，只会让 SQL 读到 NULL——过滤器静默失效、caption 永远为空。
    这类缺陷在真实 Paimon 上才暴露，所以在取规格时就拦下来。
    """
    columns = {c.name for c in spec.all_columns()}
    problems: list[str] = []
    if tuple(spec.primary_key) != PRIMARY_KEY:
        problems.append(f"主键 registry={spec.primary_key} 与本模块声明 {PRIMARY_KEY} 不一致")
    if tuple(spec.partition_by) != (PARTITION_FIELD,):
        problems.append(
            f"分区 registry={spec.partition_by} 与本模块声明 {(PARTITION_FIELD,)} 不一致"
        )
    missing_filters = sorted(SCALAR_FILTER_COLUMNS - columns)
    if missing_filters:
        problems.append(
            f"标量预过滤白名单里的列 registry 没有: {missing_filters}"
            "（过滤条件会编译出引用不存在列的 SQL）"
        )
    missing_named = sorted({CAPTION_COLUMN, EMBED_TIME_COLUMN} - columns)
    if missing_named:
        problems.append(f"写回用到的列 registry 没有: {missing_named}")
    if problems:
        raise ValueError(
            f"{VECTOR_TABLE_NAME} 的 registry 定义与向量子系统的契约对不上："
            + "；".join(problems)
            + "。表结构的唯一事实源是 catalog/tables/_mining.py，请在那里补列，"
            "不要在子系统里重新本地定义。"
        )


@lru_cache(maxsize=1)
def paimon_table_spec() -> TableSpec:
    """返回向量明细表的 Paimon 规格——列 / 主键 / 分区 / bucket 全部取自 catalog.registry。

    本模块不再持有第二份定义。唯一的追加是一项**表属性**：vector_meta 的
    ``variant.shreddingSchema``（a9 第 04 节：热路径稳定的生产表用显式 shredding schema，
    把热路径物化成带类型子列）。registry 的表注释里写明了这条分工——catalog 是最底层契约，
    不反向依赖子系统，因此 shredding schema 由这里在建表时按 :mod:`.variant` 追加。
    ``file.format=parquet``（a9 对 VARIANT 列的硬要求）已由 registry 自己带上。

    物理策略依据（由 registry 声明，此处只复述）：
      · 分区：规则一（大体量 + 时间范围查询 → 按 dt）
      · Bucket：16 档「超大表 / 高并发写入（DWD）」——全湖体量最大的明细表
      · changelog-producer：DWD 层默认 lookup，未偏离
    """
    # 延迟导入：registry 会拉起全部表模块，没必要在 import 本模块时就付这个代价
    from ..catalog import registry
    from .variant import VECTOR_META_SHREDDING_SCHEMA

    spec = registry.by_name(VECTOR_TABLE_NAME)
    _check_contract(spec)
    # 不就地改 registry 的对象——它是全局共享的单一事实源，只返回一份带属性的副本
    return replace(
        spec,
        extra_options={
            **spec.extra_options,
            "variant.shreddingSchema": VECTOR_META_SHREDDING_SCHEMA,
        },
    )


def render_paimon_ddl(*, catalog: str | None = None, database: str | None = None) -> str:
    """渲染 Paimon 侧 Flink SQL 建表语句（单一事实源）。"""
    from ..config import settings

    cfg = settings().paimon
    return paimon_table_spec().render_ddl(
        catalog=catalog or cfg.catalog, database=database or cfg.database
    )


def render_external_catalog_ddl() -> str:
    """渲染 StarRocks External Catalog 定义（第一档：直查 Paimon，向量只存一份）。

    原文第一章：「索引建在 Paimon 外部表上，向量本体只存一份，Paimon 仍是单一事实源与
    血缘基准」。External Catalog 建好后，外部表以
    ``<catalog>.<database>.<table>`` 三段式直接访问，StarRocks 不持有主数据。
    """
    from ..config import settings

    cfg = settings()
    sr, paimon, minio = cfg.starrocks, cfg.paimon, cfg.minio
    return (
        f"-- StarRocks External Catalog：直查 Paimon，StarRocks 只提供检索加速，不持有主数据\n"
        f"CREATE EXTERNAL CATALOG IF NOT EXISTS {sr.external_catalog}\n"
        f"PROPERTIES (\n"
        f'    "type" = "paimon",\n'
        f'    "paimon.catalog.type" = "{paimon.metastore}",\n'
        f'    "paimon.catalog.warehouse" = "{minio.warehouse_path}",\n'
        f'    "aws.s3.endpoint" = "{minio.endpoint}",\n'
        f'    "aws.s3.enable_path_style_access" = "true",\n'
        f'    "aws.s3.access_key" = "${{MINIO_ACCESS_KEY}}",\n'
        f'    "aws.s3.secret_key" = "${{MINIO_SECRET_KEY}}"\n'
        f");\n"
    )


def external_table_ref() -> str:
    """外部表的三段式引用：``<external_catalog>.<paimon_db>.<table>``。"""
    from ..config import settings

    cfg = settings()
    return f"{cfg.starrocks.external_catalog}.{cfg.paimon.database}.{VECTOR_TABLE_NAME}"


def internal_table_ref() -> str:
    """降级内表的引用：``<internal_db>.<table>``。"""
    from ..config import settings

    return f"{settings().starrocks.internal_database}.{VECTOR_TABLE_NAME}"


#: Paimon 类型 -> StarRocks 类型的映射（内表冗余时用）。
_SR_TYPE_MAP: dict[str, str] = {
    "STRING": "VARCHAR(256)",
    "INT": "INT",
    "BIGINT": "BIGINT",
    "DOUBLE": "DOUBLE",
    "FLOAT": "FLOAT",
    "BOOLEAN": "BOOLEAN",
    "TIMESTAMP(3)": "DATETIME",
    "DATE": "DATE",
    "VARIANT": "JSON",  # ⚠️ 原文未明确：StarRocks 侧用 JSON 承接 Paimon VARIANT
}


#: 个别长文本列需要更大的 VARCHAR 长度，按列名覆盖默认映射。
_SR_TYPE_OVERRIDE: dict[str, str] = {
    CAPTION_COLUMN: "VARCHAR(2048)",  # 图片描述正文，256 不够
    "artifact_id": "VARCHAR(512)",  # data_id + stage + 算法版本 + content_hash 拼出来很长
    "parent_artifact_id": "VARCHAR(512)",
}


def _starrocks_type(paimon_type: str, column: str = "") -> str:
    """Paimon/Flink 类型字面量 -> StarRocks 类型字面量。

    向量列在 StarRocks 必须 NOT NULL：向量索引不接受 NULL 值。
    """
    if column in _SR_TYPE_OVERRIDE:
        return _SR_TYPE_OVERRIDE[column]
    if paimon_type.startswith("ARRAY<FLOAT>"):
        return "ARRAY<FLOAT> NOT NULL"
    return _SR_TYPE_MAP.get(paimon_type, "VARCHAR(256)")


def render_internal_table_ddl(*, dim: int = DEFAULT_VECTOR_DIM) -> str:
    """渲染 StarRocks 内表 DDL（第二档降级形态）。

    原文第六章：「如果 POC 验证外部表索引性能不达标怎么办？降级路径在设计之初就留好了——
    向量数据冗余写入 StarRocks 内表（定时同步 + 主键对账），检索链路与 API 完全不变」。

    内表选型说明：
      · 模型：主键模型（PRIMARY KEY），与 Paimon 的 Upsert 语义对齐，便于主键对账；
      · 分区：沿用 dt，保持与外部表一致的分区裁剪能力；
      · 分桶：⚠️ 原文未明确，本项目设计——按 image_id 哈希 32 桶。

    向量列在 StarRocks 内表必须 NOT NULL（向量索引不接受 NULL 值），因此同步作业需要
    在源侧过滤掉向量为空的行（见 backend.InternalTableSync）。
    """
    spec = paimon_table_spec()
    lines: list[str] = []
    pk_cols = list(spec.primary_key)  # 主键同样以 registry 为准，不在这里重写一遍
    # 主键模型要求主键列排在最前
    ordered = [c for name in pk_cols for c in spec.all_columns() if c.name == name]
    ordered += [c for c in spec.all_columns() if c.name not in pk_cols]
    for col in ordered:
        sr_type = _starrocks_type(col.type, col.name)
        not_null = " NOT NULL" if (col.name in pk_cols and "NOT NULL" not in sr_type) else ""
        comment = f' COMMENT "{col.comment}"' if col.comment else ""
        lines.append(f"    `{col.name}` {sr_type}{not_null}{comment}")

    from ..config import settings

    db = settings().starrocks.internal_database
    return (
        "-- 降级形态（第二档）：向量冗余写入 StarRocks 内表，定时同步 + 主键对账\n"
        "-- 检索链路与 API 完全不变，Paimon 始终是单一事实源与对账基准\n"
        f"-- 向量维度 {dim}（写在索引 PROPERTIES 的 dim 属性里，见 ddl/starrocks_vector.sql）\n"
        f"CREATE TABLE IF NOT EXISTS `{db}`.`{VECTOR_TABLE_NAME}` (\n" + ",\n".join(lines) + "\n)\n"
        "ENGINE = OLAP\n"
        f"PRIMARY KEY ({', '.join(f'`{c}`' for c in pk_cols)})\n"
        f"PARTITION BY ({', '.join(f'`{p}`' for p in spec.partition_by)})\n"
        "DISTRIBUTED BY HASH(`image_id`) BUCKETS 32\n"
        "PROPERTIES (\n"
        '    "replication_num" = "1",\n'
        '    "enable_persistent_index" = "true"\n'
        ");\n"
    )


def vector_columns_for_projection() -> tuple[str, ...]:
    """检索回补元数据时需要投影的列（原文第五章第 ⑤ 步）。

    刻意**不**投影 image_embedding / text_embedding：千万级下把向量本体拉回服务层是
    最容易打穿 P95 ≤ 2 秒的写法，相似度在 SQL 里算完即可。
    """
    skip = {"image_embedding", "text_embedding"}
    return tuple(c.name for c in paimon_table_spec().all_columns() if c.name not in skip)
