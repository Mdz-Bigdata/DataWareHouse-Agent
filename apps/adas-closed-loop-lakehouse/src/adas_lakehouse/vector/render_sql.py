"""把向量子系统的全部 SQL 渲染成脚本文件。

产出两份，路径固定：
  · flink/sql/vector_image_vector_detail.sql   Paimon 向量明细表 DDL（单一事实源）
  · flink/sql/vector_embedding_pipeline.sql    T+1 向量化流水线的 Flink SQL（增量 + 幂等写回）
  · flink/sql/vector_version_switch.sql        embedding_version 灰度切换与一键回滚
  · ddl/starrocks_vector.sql                   External Catalog、双 HNSW 索引、分区刷新、
                                               四类检索 SQL、降级内表与同步对账

用法::

    python -m adas_lakehouse.vector.render_sql            # 写进仓库默认路径
    python -m adas_lakehouse.vector.render_sql --stdout   # 只打印不落盘

SQL 由代码渲染而不是手写，保证「参数改一处、脚本全跟着变」——特别是 HNSW 的
M / efConstruction 在 POC 定参后只需要改 params.py 一处。
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from ..config import settings
from .backend import render_reconcile_sql, render_sync_sql
from .embedding import render_upsert_sql
from .index import render_create_index_ddl, render_partition_refresh_sql
from .params import (
    DEFAULT_HNSW_PARAMS,
    DEFAULT_TOP_K,
    EXAMPLE_SCALAR_FILTER_RECENT_DAYS,
    PIPELINE_DEADLINE_HOUR,
    PIPELINE_STEPS,
    POC_CHECKLIST,
    SEARCH_P95_SLA_SECONDS,
    SEARCH_SLA_SCALE_CN,
    RetrievalMode,
    VectorBackend,
)
from .schema import (
    VECTOR_TABLE_NAME,
    render_external_catalog_ddl,
    render_internal_table_ddl,
    render_paimon_ddl,
)
from .search import ScalarFilters, SearchRequest, render_search_sql
from .variant import (
    INFER_SHREDDING_OPTIONS,
    SQL_ENGINE_REQUIREMENT,
    VECTOR_META_HOT_PATHS,
    VECTOR_META_SHREDDING_SCHEMA,
    render_variant_get,
)
from .versioning import render_activate_sql, render_deprecate_sql, render_rollback_sql

__all__ = ["render_all", "write_all", "main"]

#: 仓库内的固定输出位置（相对 apps/adas-closed-loop-lakehouse/）。
FLINK_SQL_DIR = Path("flink/sql")
DDL_DIR = Path("ddl")

#: 渲染示例 SQL 时用的占位向量（真实 query 向量由 CLIP 在运行时算出）。
_DEMO_DIM_PREVIEW = 4
_SAMPLE_DT = "2026-09-06"  # 原文发表日，作为示例分区值


def _banner(title: str, source: str) -> str:
    return (
        "-- =============================================================================\n"
        f"-- {title}\n"
        f"-- 来源：{source}\n"
        "-- 本文件由 `python -m adas_lakehouse.vector.render_sql` 生成，请勿手改；\n"
        "-- 参数改动请改 src/adas_lakehouse/vector/params.py。\n"
        "-- =============================================================================\n\n"
    )


def _demo_vector() -> list[float]:
    """示例向量：只写前几维 + 省略号注释，避免脚本里塞 512 个浮点数。"""
    return [0.0] * _DEMO_DIM_PREVIEW


def render_table_sql() -> str:
    """flink/sql/vector_image_vector_detail.sql —— 向量明细表 DDL。"""
    cfg = settings()
    hot_paths = "\n".join(f"--     {p} -> {t}" for p, t in VECTOR_META_HOT_PATHS.items())
    shredding = (
        "-- vector_meta 的显式 shredding schema（热路径物化成带类型的 Parquet 子列）\n"
        f"--   'variant.shreddingSchema' = '{VECTOR_META_SHREDDING_SCHEMA}'\n"
        "-- 探索期可改用自动推断（a9 原文第 04 节参数，逐字）：\n"
        + "\n".join(f"--   '{k}' = '{v}'" for k, v in INFER_SHREDDING_OPTIONS.items())
        + "\n"
    )
    return (
        _banner(
            "向量明细表 dwd_mining_image_vector_detail（Paimon，单一事实源）",
            "a7 第二章《向量落湖：一张千万级的向量明细表》 + a9（vector_meta VARIANT）",
        )
        + "-- 三个设计决策：\n"
        "--   1. 图文双向量同行——同一个 CLIP 模型双塔编码，保证图文向量在同一空间；\n"
        "--   2. 按 dt 分区——全湖唯一按日期分区的明细表，支撑生命周期降冷与分区级索引增量刷新；\n"
        "--   3. embedding_version 入主键——模型换代新旧向量并存，vector_status 区分 active/deprecated。\n"
        f"-- VARIANT 列要求：Spark {SQL_ENGINE_REQUIREMENT['spark']} 或 Flink "
        f"{SQL_ENGINE_REQUIREMENT['flink']}，数据文件必须是 {SQL_ENGINE_REQUIREMENT['file_format']}（a9 第 03 节）。\n"
        "-- vector_meta 热路径：\n"
        + hot_paths
        + "\n\n"
        + shredding
        + "\n"
        + f"USE CATALOG `{cfg.paimon.catalog}`;\n"
        + f"USE `{cfg.paimon.database}`;\n\n"
        + render_paimon_ddl()
    )


def render_pipeline_sql() -> str:
    """flink/sql/vector_embedding_pipeline.sql —— 五步流水线里的 SQL 部分。"""
    steps = "\n".join(f"--   {s.ordinal}. {s.name_cn}：{s.mechanism_cn}" for s in PIPELINE_STEPS)
    refresh = "\n".join(render_partition_refresh_sql(_SAMPLE_DT))
    return (
        _banner(
            "Embedding T+1 向量化流水线（增量识别 → 幂等写回 → 分区级索引刷新）",
            "a7 第三章《Embedding 五步流水线：向量是怎么算出来的》",
        )
        + f"-- 节拍：T+1，每日凌晨 {PIPELINE_DEADLINE_HOUR} 点前完成增量处理，新数据当日可检索。\n"
        "-- 五步：\n" + steps + "\n"
        "-- 第 ② ③ 步（成本分级 / GPU 双路编码）在 Spark / Ray + GPU 算子里完成，不在 Flink SQL 内；\n"
        "-- 本文件覆盖第 ① 步的增量水位筛选、第 ④ 步的幂等写回、第 ⑤ 步的索引刷新通知。\n\n"
        "-- ① 增量识别：按 create_time / update_time 水位，仅取新增与标签变更图片\n"
        "--    :create_wm / :update_wm 由调度注入（见 embedding.Watermark）\n"
        "CREATE TEMPORARY VIEW `image_increment` AS\n"
        "SELECT *\n"
        "FROM `dwd_mining_image_frame_detail`\n"
        "WHERE `create_time` > CAST(:create_wm AS TIMESTAMP(3))\n"
        "   OR `update_time` > CAST(:update_wm AS TIMESTAMP(3));\n\n"
        "-- ④ 幂等写回：向量由 GPU 算子算好后落在 `tmp_image_vector_staging`\n"
        + render_upsert_sql(
            embedding_version="clip_v1",
            dt=_SAMPLE_DT,
            source_table="tmp_image_vector_staging",
        )
        + "\n"
        "-- ④' 标签变图片不变：不重算向量，只更新 vector_meta 的对应路径\n"
        "--     Python 侧走 pypaimon variant_set（a9 基准：比 JSON 解析-修改-回写快 22.23×~36.98×）\n"
        "--     SQL 侧可读回热路径校验：\n"
        f"SELECT `image_id`, {render_variant_get('vector_meta', '$.perception.weather', 'string', 'weather_raw')}\n"
        f"FROM `{VECTOR_TABLE_NAME}` WHERE `dt` = '{_SAMPLE_DT}' LIMIT 10;\n\n"
        "-- ⑤ 索引刷新：写入完成后通知 StarRocks 增量刷新当日分区索引（在 StarRocks 侧执行）\n"
        + "\n".join(f"--   {line}" for line in refresh.splitlines())
        + "\n"
    )


def render_version_switch_sql() -> str:
    """flink/sql/vector_version_switch.sql —— 灰度切换与一键回滚。"""
    return (
        _banner(
            "embedding_version 灰度切换与一键回滚",
            "a7 第二章设计决策三：模型换代新旧向量并存，切换与回滚都不需要重写数据",
        )
        + "-- 顺序很重要：先激活新版本，再退役旧版本，否则会出现「一瞬间没有 active 版本」的检索空窗。\n\n"
        "-- 1) 影子写入：新版本以 vector_status='deprecated' 先行入湖，不影响线上检索\n"
        "--    （由 Embedding 流水线带 embedding_version='clip_v2' 跑一遍历史分区）\n\n"
        "-- 2) 灰度切换\n"
        + render_activate_sql("clip_v2")
        + render_deprecate_sql("clip_v1")
        + "\n-- 3) 一键回滚（新旧向量并存，无需重算、无需重建索引）\n"
        + render_rollback_sql(from_version="clip_v2", to_version="clip_v1")
    )


def render_starrocks_sql() -> str:
    """ddl/starrocks_vector.sql —— StarRocks 侧全部对象与检索 SQL。"""
    poc = "\n".join(
        f"--   {i.ordinal}. {i.name_cn}（{i.question_cn}）{' [硬验收线]' if i.blocking else ''}"
        for i in POC_CHECKLIST
    )
    params = DEFAULT_HNSW_PARAMS
    demo_filters = ScalarFilters(
        dt_from=date(2026, 8, 7),
        dt_to=date(2026, 9, 6),
        equals={"city_code": "SH", "weather": "rain"},
    )
    text_sql, text_params = render_search_sql(
        SearchRequest(
            RetrievalMode.TAG_PLUS_VECTOR, text="雨天夜间高速行人横穿", filters=demo_filters
        ),
        text_vector=_demo_vector(),
    )
    image_sql, _ = render_search_sql(
        SearchRequest(RetrievalMode.IMAGE_TO_IMAGE, image_uri="s3://badcase/img_001.jpg"),
        image_vector=_demo_vector(),
    )
    hybrid_sql, _ = render_search_sql(
        SearchRequest(
            RetrievalMode.HYBRID,
            text="雨天夜间高速行人横穿",
            image_uri="s3://badcase/img_001.jpg",
            filters=demo_filters,
        ),
        image_vector=_demo_vector(),
        text_vector=_demo_vector(),
    )

    def _with_note(sql: str) -> str:
        return sql.replace(
            "[0.000000, 0.000000, 0.000000, 0.000000]",
            f"[/* {params.dim} 维 query 向量，由同一个 CLIP 模型在检索服务层实时编码 */]",
        )

    return (
        _banner(
            "StarRocks 向量检索：External Catalog + 双 HNSW 索引 + 检索 SQL + 降级内表",
            "a7 第四 / 五 / 六章",
        )
        + f"-- 唯一验收线：{SEARCH_SLA_SCALE_CN}数据量单次向量检索 P95 ≤ {SEARCH_P95_SLA_SECONDS} 秒。\n"
        "-- POC 五项前置验证（外部表向量索引是相对新的能力，全量上线前必须先过）：\n" + poc + "\n\n"
        "-- ---------------------------------------------------------------- 1. 外部表（第一档）\n"
        + render_external_catalog_ddl()
        + "\n"
        + render_create_index_ddl(backend=VectorBackend.EXTERNAL_PAIMON, params=params)
        + "\n-- 分区级增量刷新（每日向量写入完成后执行，只刷当日分区）\n"
        + "\n".join(render_partition_refresh_sql(_SAMPLE_DT))
        + "\n\n"
        "-- ---------------------------------------------------------------- 2. 检索 SQL（四类能力）\n"
        f"-- 检索时下推 efSearch（TopK 默认 {DEFAULT_TOP_K}，不为用不到的长尾结果付出检索成本）\n"
        f"SET ann_params = '{params.ann_params()}';\n\n"
        f"-- 2.1 文搜图 / 标签+向量：最近 {EXAMPLE_SCALAR_FILTER_RECENT_DAYS} 天 + 某城市 + 雨天\n"
        f"--     参数（按顺序绑定）：{text_params}\n"
        + _with_note(text_sql)
        + "\n\n-- 2.2 图搜图：Badcase 找相似样本\n"
        + _with_note(image_sql)
        + "\n\n-- 2.3 混合检索：图文双向量加权融合（image×w1 + text×w2）+ 服务层重排\n"
        + _with_note(hybrid_sql)
        + "\n\n"
        "-- ---------------------------------------------------------------- 3. 降级内表（第二档）\n"
        "-- 触发条件：POC 硬验收线不达标，或线上 P95 连续超线\n"
        + render_internal_table_ddl()
        + "\n"
        + render_create_index_ddl(backend=VectorBackend.INTERNAL_STARROCKS, params=params)
        + "\n-- 定时同步\n"
        + render_sync_sql(_SAMPLE_DT)
        + "\n-- 主键对账（以 Paimon 外部表为基准）\n"
        + render_reconcile_sql(_SAMPLE_DT)
    )


def render_all() -> dict[Path, str]:
    """渲染全部脚本，返回 {相对路径: 内容}。"""
    return {
        FLINK_SQL_DIR / "vector_image_vector_detail.sql": render_table_sql(),
        FLINK_SQL_DIR / "vector_embedding_pipeline.sql": render_pipeline_sql(),
        FLINK_SQL_DIR / "vector_version_switch.sql": render_version_switch_sql(),
        DDL_DIR / "starrocks_vector.sql": render_starrocks_sql(),
    }


def write_all(root: str | Path = ".") -> list[Path]:
    """把脚本写到磁盘，返回实际写入的绝对路径。

    :param root: 仓库内 apps/adas-closed-loop-lakehouse 的路径
    """
    base = Path(root).resolve()
    written: list[Path] = []
    for rel, content in render_all().items():
        target = base / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(target)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="渲染向量子系统的 Flink / StarRocks SQL 脚本")
    parser.add_argument("--root", default=".", help="apps/adas-closed-loop-lakehouse 路径")
    parser.add_argument("--stdout", action="store_true", help="只打印，不落盘")
    args = parser.parse_args(argv)
    if args.stdout:
        for rel, content in render_all().items():
            print(f"===== {rel} =====")
            print(content)
        return 0
    for path in write_all(args.root):
        print(f"已写入 {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
