"""两张治理表的表规格，以及与全湖注册表的对账。

原文第四章：「两张元数据表统一归入**闭环域（跨域归集）**，与 dwd_closed_loop_trace
通过 data_id 直接关联，形成『闭环追溯 + 存储状态』孪生视图，和效率、贡献度一起构成
『效率 · 贡献 · 成本』三维闭环治理指标体系」。

本模块**不**往 ``catalog/tables/`` 里写文件——那是装配阶段的事。这里声明的是
「生命周期子系统运行所必需的表结构契约」：

* 装配前：``lifecycle_table_spec()`` / ``cost_daily_table_spec()`` 就是事实来源，
  ``render_ddl()`` 可以直接建表把子系统跑起来；
* 装配后（闭环域表模块登记进 ``catalog.registry._MODULES``）：
  ``reconcile_with_registry()`` 逐字段比对注册表里的定义与这里的契约，
  缺字段/主键不符会明确报出来，避免两边悄悄漂移。

物理策略按共享契约的硬性规则推导，逐条记在 ``notes`` 里。
"""

from __future__ import annotations

from ..catalog.spec import BUCKET_TIERS, ChangelogProducer, TableSpec
from ..catalog.spec import Column as C
from ..domains import DataDomain, Layer

__all__ = [
    "LIFECYCLE_TABLE",
    "COST_DAILY_TABLE",
    "TABLES",
    "ACCEPTED_NAMING_WARNINGS",
    "REQUIRED_COLUMNS",
    "lifecycle_table_spec",
    "cost_daily_table_spec",
    "validate_specs",
    "reconcile_with_registry",
    "render_ddl",
]

#: 表名常量——SQL、仓储层、调度器一律引用这里，杜绝字符串散落。
LIFECYCLE_TABLE = "dwd_closed_loop_storage_lifecycle"
COST_DAILY_TABLE = "dws_closed_loop_storage_cost_daily"

D = DataDomain.CLOSED_LOOP

#: 已登记并接受的命名告警。两张表名都是原文原话，第四段不是 naming.py 的 13 种标准
#: 粒度后缀之一（``lifecycle`` 不在表里），但表名不能为了凑后缀而改——
#: 原文、图库、看板、下游 SQL 全按这两个名字走。
ACCEPTED_NAMING_WARNINGS: tuple[str, ...] = ("dwd 层建议带标准粒度后缀（第四段）",)


_LIFECYCLE_SPEC = TableSpec(
    name=LIFECYCLE_TABLE,
    layer=Layer.DWD,
    domain=D,
    comment="存储生命周期状态明细表：全闭环每个数据单元的当前存储状态快照",
    bucket=16,
    primary_key=("data_id", "file_path"),
    changelog_producer=ChangelogProducer.LOOKUP,
    notes=(
        "分区：不分区。主键 Upsert 且无明确分区维度（分区决策规则三）——"
        "治理扫描是全表按规则逐条算，不是按 dt 取范围，按 dt 分区反而每天全表重扫所有分区；"
        "全湖仅 6 张分区表，本表不在其中。"
        "Bucket=16：超大表 / 高并发写入——粒度到「每个数据单元的每个文件」，"
        "PB 级数据下行数远超任何业务明细表，且预热/淘汰/降冷每天批量 Upsert。"
        "changelog-producer=lookup：DWD 层默认，本表是典型 Upsert 明细，"
        "下游成本日表要拿到 -U/+U 才能算准当日治理动作量。"
        "命名：第四段 lifecycle 非 naming.GRANULARITY_SUFFIXES 的 13 种标准粒度后缀之一，"
        "表名取自原文，已在 "
        "ACCEPTED_NAMING_WARNINGS 登记。"
    ),
    columns=[
        # ---- 原文第四章①点名的字段 ----
        C("data_id", "STRING", "全局数据 ID，关联 dwd_closed_loop_trace（主键一）", nullable=False),
        C("file_path", "STRING", "文件路径（主键二）", nullable=False),
        C(
            "storage_media",
            "STRING",
            "当前介质：oss_standard / oss_ia / oss_archive / oss_deep_archive / nas",
        ),
        C(
            "lifecycle_stage",
            "STRING",
            "分层状态：hot / warm / cold / archive / pending_delete / deleted",
        ),
        C("last_access_time", "TIMESTAMP(3)", "最后访问时间（降冷驱动）"),
        C("access_count_30d", "INT", "近 30 天访问次数（LRU 淘汰依据）"),
        C("lineage_ref_count", "INT", "下游血缘引用数——删除保护依据，自 2.4 血缘关系汇总复用"),
        C("whitelist_flag", "BOOLEAN", "白名单豁免标志"),
        C("expire_policy", "STRING", "保留策略：raw_365d / dataset_forever / model_top_n 等"),
        C("preheat_task_id", "STRING", "最近预热任务 ID（预热归因）"),
        C("evict_status", "STRING", "淘汰状态：none / pending / done / skipped"),
        # ---- ⚠️ 原文未明确，本项目补充（理由见 records.LifecycleRecord）----
        C("artifact_id", "STRING", "⚠️ 本项目补充：二级 ID，处理产物落盘时的归属"),
        C("project_code", "STRING", "⚠️ 本项目补充：跨域公共键，成本按项目归集"),
        C("preheat_time", "TIMESTAMP(3)", "⚠️ 本项目补充：最近一次预热至 NAS 的时间"),
        C("tier_down_time", "TIMESTAMP(3)", "⚠️ 本项目补充：最近一次降冷/归档流转时间"),
        C(
            "monthly_cost_yuan",
            "DOUBLE",
            "⚠️ 本项目补充：当前介质下的月成本折算（容量 × 介质单价）",
        ),
        C(
            "data_type",
            "STRING",
            "⚠️ 本项目补充：raw/intermediate/dataset/model/temp——保留期表按数据类型配置",
        ),
        C("source_domain", "STRING", "⚠️ 本项目补充：来源域，成本日表按此聚合"),
        C("file_size_bytes", "BIGINT", "⚠️ 本项目补充：文件大小，容量与成本的计算基数"),
        C("checksum_md5", "STRING", "⚠️ 本项目补充：淘汰校验闸比对 NAS 副本与 OSS 对象"),
        C("create_time", "TIMESTAMP(3)", "⚠️ 本项目补充：落湖时间，温层「创建 30 天内」的基准"),
        C("stage_entered_at", "TIMESTAMP(3)", "⚠️ 本项目补充：进入当前分层的时间，用于冷→归档计时"),
    ],
)


_COST_DAILY_SPEC = TableSpec(
    name=COST_DAILY_TABLE,
    layer=Layer.DWS,
    domain=D,
    comment="存储成本日指标表：按日期 × 介质 × 分层 × 数据类型 × 来源域聚合容量与成本",
    bucket=2,
    primary_key=("stat_date", "storage_media", "lifecycle_stage", "data_type", "source_domain"),
    changelog_producer=ChangelogProducer.FULL_COMPACTION,
    notes=(
        "分区：不分区。五维聚合后行数很小（介质 5 × 分层 6 × 数据类型 5 × 来源域 ~11，"
        "单日上限千行量级），按 dt 分区只会产生大量小文件；全湖仅 6 张分区表，本表不在其中。"
        "Bucket=2：DWS 汇总表档位。"
        "changelog-producer=full-compaction：DWS 层默认，批量聚合后整体刷新。"
        "原文称本表由 StarRocks 离线聚合——Paimon 侧同名表作为落湖副本，"
        "StarRocks 内表 DDL 见 ddl/starrocks_lifecycle.sql。"
    ),
    columns=[
        C("stat_date", "DATE", "统计日期（主键一）", nullable=False),
        C("storage_media", "STRING", "介质（主键二）", nullable=False),
        C("lifecycle_stage", "STRING", "分层（主键三）", nullable=False),
        C("data_type", "STRING", "数据类型（主键四）", nullable=False),
        C("source_domain", "STRING", "来源域（主键五）", nullable=False),
        C("file_count", "BIGINT", "文件数"),
        C("total_capacity_tb", "DECIMAL(18,6)", "容量合计（TB）"),
        C("daily_cost_yuan", "DECIMAL(18,4)", "当日折算成本（元，按云厂商计价折算）"),
        C("baseline_cost_yuan", "DECIMAL(18,4)", "无治理基线成本（元），节省额对照基准"),
        C("saved_cost_yuan", "DECIMAL(18,4)", "治理释放成本 = 基线成本 − 实际成本"),
        C("cost_mom_rate", "DECIMAL(12,6)", "成本环比增长率（> 0.10 触发预算告警）"),
        C("preheat_volume_tb", "DECIMAL(18,6)", "当日预热数据量（TB）"),
        C("evict_volume_tb", "DECIMAL(18,6)", "当日淘汰数据量（TB）"),
        C("tier_down_volume_tb", "DECIMAL(18,6)", "当日降冷数据量（TB）"),
        C("delete_volume_tb", "DECIMAL(18,6)", "当日删除数据量（TB）"),
        C("nas_peak_usage", "DECIMAL(6,4)", "NAS 峰值使用率（0~1，> 0.80 告警）"),
        C("preheat_hit_rate", "DECIMAL(6,4)", "预热命中率 = 训练预热命中 / 总预热请求"),
        C("archive_restore_count", "INT", "归档取回次数，反哺保留期与降冷阈值调优"),
    ],
)


#: 本子系统的两张表。
TABLES: tuple[TableSpec, ...] = (_LIFECYCLE_SPEC, _COST_DAILY_SPEC)


def lifecycle_table_spec() -> TableSpec:
    """``dwd_closed_loop_storage_lifecycle`` 的表规格。

    注册表里已登记同名表时返回注册表的版本（以全湖注册表为准），
    否则返回本模块的契约版本。
    """
    return _from_registry_or(LIFECYCLE_TABLE, _LIFECYCLE_SPEC)


def cost_daily_table_spec() -> TableSpec:
    """``dws_closed_loop_storage_cost_daily`` 的表规格。"""
    return _from_registry_or(COST_DAILY_TABLE, _COST_DAILY_SPEC)


#: 本子系统的决策引擎硬依赖的字段——少一个就有规则算不出来。
#: 对账时单独点名，是为了让装配阶段一眼看到「缺这个会坏哪条规则」。
REQUIRED_COLUMNS: dict[str, str] = {
    "data_id": "主键一，与 dwd_closed_loop_trace 的关联键",
    "file_path": "主键二，治理粒度到文件",
    "storage_media": "当前介质，成本计算与降冷目标的依据",
    "lifecycle_stage": "分层状态，五级模型的落点",
    "last_access_time": "降冷驱动：连续无访问天数由它算",
    "access_count_30d": "LRU 淘汰依据：近 30 天访问次数",
    "lineage_ref_count": "删除三重确认之二（血缘零引用）与「提升一档保留」",
    "whitelist_flag": "删除三重确认之三，也是跳过分层流转的开关",
    "expire_policy": "保留策略标识",
    "evict_status": "淘汰状态流转",
    "file_size_bytes": "容量与成本的计算基数，没有它成本日表全是 0",
    "data_type": "保留期表按数据类型配置，没有它 TTL 规则无从选起",
    "source_domain": "成本日表的聚合维度之一",
    "checksum_md5": "淘汰校验闸（第二道安全闸）的比对依据",
    "create_time": "温层进入条件「创建 30 天内」与全部保留期的计时起点",
}


def _closed_loop_module_specs() -> dict[str, TableSpec]:
    """装配前的过渡通道：直接读闭环域表模块（若已存在）。

    闭环域表模块可能已经写好、但还没登记进 ``catalog.registry._MODULES``。
    这段时间里注册表查不到表，可模块里其实已有定义——提前对上账，
    比等到装配完再发现字段对不上要便宜得多。
    """
    try:
        from ..catalog.tables import _closed_loop  # type: ignore
    except ImportError:
        return {}
    return {t.name: t for t in getattr(_closed_loop, "TABLES", ())}


def _from_registry_or(name: str, fallback: TableSpec) -> TableSpec:
    """注册表优先 → 闭环域表模块 → 本模块契约。

    注册表在装配阶段才会登记闭环域模块，此前 ``by_name`` 必然 KeyError，
    这不是异常而是预期路径，因此只吞 KeyError，其他异常照抛。
    """
    from ..catalog import registry

    try:
        return registry.by_name(name)
    except KeyError:
        return _closed_loop_module_specs().get(name, fallback)


def validate_specs() -> dict[str, list[str]]:
    """校验两张表规格，过滤掉已登记接受的命名告警。

    :returns: ``{表名: 问题列表}``，只含真有问题的表；空字典表示全部合规。
    """
    out: dict[str, list[str]] = {}
    for spec in TABLES:
        problems = [p for p in spec.validate() if not any(w in p for w in ACCEPTED_NAMING_WARNINGS)]
        if spec.bucket not in BUCKET_TIERS:  # 冗余保险：Bucket 五档是硬约束
            problems.append(f"Bucket {spec.bucket} 不在五档 {sorted(BUCKET_TIERS)} 内")
        if problems:
            out[spec.name] = problems
    return out


def reconcile_with_registry() -> dict[str, object]:
    """与全湖注册表对账：装配后两边定义是否还一致。

    比对三项：表是否已登记、本子系统依赖的字段是否都在、主键是否一致。
    字段只查「缺不缺」不查「多不多」——注册表可以比本契约更宽，不能更窄。

    :returns: 每张表的对账结论。``registered=False`` 表示尚未装配，属正常状态。
    """
    from ..catalog import registry

    module_specs = _closed_loop_module_specs()
    result: dict[str, object] = {}
    for spec in TABLES:
        try:
            other = registry.by_name(spec.name)
            source = "catalog.registry"
        except KeyError:
            other = module_specs.get(spec.name)
            source = "catalog.tables._closed_loop（尚未登记进 registry._MODULES）"
        if other is None:
            result[spec.name] = {
                "source": None,
                "note": "闭环域表模块尚未提供该表；本子系统以 lifecycle.tables 的契约为准",
            }
            continue

        have = {c.name for c in other.all_columns()}
        need = {c.name for c in spec.all_columns()}
        missing = sorted(need - have)
        missing_required = {c: REQUIRED_COLUMNS[c] for c in missing if c in REQUIRED_COLUMNS}
        pk_match = tuple(other.primary_key) == tuple(spec.primary_key)
        result[spec.name] = {
            "source": source,
            "missing_columns": missing,
            "missing_required_columns": missing_required,
            "extra_columns": sorted(have - need),
            "primary_key_match": pk_match,
            "registry_primary_key": tuple(other.primary_key),
            "expected_primary_key": tuple(spec.primary_key),
            "bucket_match": other.bucket == spec.bucket,
            "partition_match": tuple(other.partition_by) == tuple(spec.partition_by),
            "changelog_match": other.changelog_producer is spec.changelog_producer,
            # 多字段无所谓，少必需字段才是硬伤
            "ok": not missing_required
            and pk_match
            and other.bucket == spec.bucket
            and tuple(other.partition_by) == tuple(spec.partition_by),
        }
    return result


def render_ddl(*, catalog: str | None = None, database: str | None = None) -> str:
    """渲染两张表的 Flink SQL 建表语句。

    :param catalog: Paimon catalog 名；默认取 ``config.settings().paimon.catalog``。
    :param database: 库名；默认取 ``config.settings().paimon.database``。
    """
    from ..config import settings

    cfg = settings().paimon
    cat = catalog or cfg.catalog
    db = database or cfg.database
    return "\n".join(spec.render_ddl(catalog=cat, database=db) for spec in TABLES)
