"""全湖表注册表：11 数据域 + 质量门禁，共 88 张 Paimon 表。

每个数据域一个模块（tables/_<domain>.py），此处只做聚合与全局校验。
拆模块的目的一是可读，二是让各域的表定义可以独立演进而不打架。

⚠️ 表数量与原文的对账（详见 docs/source-deviations.md）：
  原文正文写「79+ 张」「ODS 28 张 / DWD 27 张」，但同一篇给出的显式表名清单
  实际是 ODS 32 / DWD 30 / DWS 14 / ADS 11；另一篇的 11 数据域统计表写
  「合计 87+ 张（含质量门禁 1 张）」。本项目以显式表名清单为准，并补上只在
  分区全景表中出现的 ods_production_kafka_event，最终 88 张。

除了逐表校验（``validate_all()``），本模块还做**反向对账**
（``reconcile_source_matrices()``）：[a12] 第三~五章的四张决策表点名了哪些代表表、
哪几张分区表、哪几个示例主键，注册表里就必须真的是那个取值。逐表校验管「不许出圈」，
反向对账管「原文点名的那几张别悄悄改掉」——两者缺一不可。
"""

from __future__ import annotations

from collections import defaultdict
from functools import lru_cache

from ..domains import DataDomain, Layer
from .spec import (
    BUCKET_TIERS,
    CHANGELOG_MODES,
    PARTITION_PANORAMA,
    PRIMARY_KEY_PRINCIPLES,
    ChangelogProducer,
    PartitionRule,
    TableSpec,
    partition_rule_for,
)
from .tables import (  # noqa: F401 - 每个数据域一个模块
    _closed_loop,
    _collect,
    _dataset,
    _deployment,
    _evaluation,
    _issue,
    _mining,
    _production,
    _quality,
    _simulation,
    _training,
    _trigger,
)

__all__ = [
    "SOURCE_MATRIX_KEY",
    "all_tables",
    "audit_notes",
    "bucket_distribution",
    "by_domain",
    "by_layer",
    "by_name",
    "changelog_distribution",
    "counts",
    "partition_distribution",
    "reconcile_source_matrices",
    "render_all_ddl",
    "validate_all",
]

#: 各数据域的表模块。新增域时在此登记。
_MODULES = (
    _closed_loop,
    _collect,
    _dataset,
    _deployment,
    _evaluation,
    _issue,
    _mining,
    _production,
    _quality,
    _simulation,
    _training,
    _trigger,
)

#: ``validate_all()`` 里存放全湖级（非单表）违规的伪表名。
SOURCE_MATRIX_KEY = "[全湖] 原文决策表对账（[a12] 第三~五章）"


@lru_cache(maxsize=1)
def all_tables() -> tuple[TableSpec, ...]:
    """全部表规格，按 层级 → 数据域 → 表名 排序。"""
    tables: list[TableSpec] = []
    for mod in _MODULES:
        tables.extend(mod.TABLES)
    order = {layer: i for i, layer in enumerate(Layer)}
    tables.sort(key=lambda t: (order[t.layer], t.domain.ordinal, t.name))

    seen: dict[str, str] = {}
    for t in tables:
        if t.name in seen:
            raise ValueError(f"表名重复: {t.name}（{seen[t.name]} 与 {t.domain.key}）")
        seen[t.name] = t.domain.key
    return tuple(tables)


def by_name(name: str) -> TableSpec:
    for t in all_tables():
        if t.name == name:
            return t
    raise KeyError(f"未注册的表: {name!r}")


def by_layer(layer: Layer) -> tuple[TableSpec, ...]:
    return tuple(t for t in all_tables() if t.layer is layer)


def by_domain(domain: DataDomain) -> tuple[TableSpec, ...]:
    return tuple(t for t in all_tables() if t.domain is domain)


def counts() -> dict[str, dict[str, int]]:
    """按「数据域 × 层级」统计表数，用于跟原文的 11 数据域表对账。"""
    out: dict[str, dict[str, int]] = {}
    for t in all_tables():
        # 伪域（质量门禁）单列，这样 11 个数据域的小计才能直接跟原文统计表对账
        key = f"[伪域]{t.pseudo_domain}" if t.pseudo_domain else t.domain.name_cn
        out.setdefault(key, {}).setdefault(t.layer.value, 0)
        out[key][t.layer.value] += 1
    return out


# --------------------------------------------------------------------------- 物理策略分布


def bucket_distribution() -> dict[int, tuple[str, ...]]:
    """Bucket 五档 → 取该档的表名（按档位升序）。见 spec.BUCKET_TIERS。"""
    out: dict[int, list[str]] = defaultdict(list)
    for t in all_tables():
        out[t.bucket].append(t.name)
    return {b: tuple(out[b]) for b in sorted(out)}


def changelog_distribution() -> dict[ChangelogProducer, tuple[str, ...]]:
    """changelog-producer 三选一 → 采用该模式的表名。见 spec.CHANGELOG_MODES。"""
    out: dict[ChangelogProducer, list[str]] = defaultdict(list)
    for t in all_tables():
        out[t.changelog_producer].append(t.name)
    return {p: tuple(out[p]) for p in ChangelogProducer if p in out}


def partition_distribution() -> dict[PartitionRule, tuple[str, ...]]:
    """分区决策三规则 → 命中该规则的表名。见 spec.PARTITION_RULES。

    多级分区不在三规则内，``partition_rule_for`` 会抛 ValueError；此处把它归到
    规则三之外单独暴露是没意义的，所以直接让异常冒出来——``validate_all()`` 已经
    在单表层面把这种表报成违规了。
    """
    out: dict[PartitionRule, list[str]] = defaultdict(list)
    for t in all_tables():
        out[partition_rule_for(t.partition_by)].append(t.name)
    return {r: tuple(out[r]) for r in PartitionRule if r in out}


# --------------------------------------------------------------------------- 校验与对账


def reconcile_source_matrices() -> list[str]:
    """与 [a12] 第三~五章的四张决策表反向对账，返回不一致清单（空表示全对得上）。

    逐表校验（``TableSpec.validate()``）只保证「不出圈」——bucket 落在五档内、
    changelog 合层、分区表主键含分区字段。但原文还**点名**了具体的表：
    哪张表是 16 档的代表、哪 6 张表分区、哪张表的 PK 是 (dataset_id, version)。
    这些点名值如果被悄悄改掉，逐表校验一条都不会响。本函数补的就是这个缺口：

    1. Bucket 五档：每档的原文代表表必须真取该档；五档必须都有表在用（无死档）。
    2. 分区全景：注册表的分区表集合必须与原文 6 行**逐行**一致（表名 + 分区字段 + 类型）。
    3. changelog 三选一：三种模式都必须被用到，且使用者的层级与口诀一致。
    4. 主键三原则：原文点名的示例主键必须逐字（含顺序）一致。

    :returns: 人类可读的不一致描述列表。
    """
    problems: list[str] = []
    tables = {t.name: t for t in all_tables()}

    # 1. Bucket 五档：代表表取值 + 无死档
    used_buckets = {t.bucket for t in all_tables()}
    for bucket, tier in BUCKET_TIERS.items():
        if bucket not in used_buckets:
            problems.append(f"Bucket 五档: {bucket} 档（{tier.scenario}）在全湖 88 张表里无人使用")
        for name in tier.example_tables:
            spec = tables.get(name)
            if spec is None:
                problems.append(f"Bucket 五档: {bucket} 档的原文代表表 {name} 未在注册表登记")
            elif spec.bucket != bucket:
                problems.append(
                    f"Bucket 五档: 原文点名 {name} 为 {bucket} 档（{tier.scenario}），"
                    f"注册表却取了 {spec.bucket}"
                )

    # 2. 分区全景：逐行比对，且总数必须恰好等于原文行数
    expected_parted = {e.table: e for e in PARTITION_PANORAMA}
    actual_parted = {t.name: t for t in all_tables() if t.partition_by}
    for extra in sorted(set(actual_parted) - set(expected_parted)):
        problems.append(
            f"分区全景: {extra} 是分区表，但不在原文分区全景表的 "
            f"{len(PARTITION_PANORAMA)} 行内（规则三：无明确分区维度则不分区）"
        )
    for missing in sorted(set(expected_parted) - set(actual_parted)):
        problems.append(f"分区全景: 原文列为分区表的 {missing} 在注册表里没有分区")
    for name, entry in expected_parted.items():
        spec = actual_parted.get(name)
        if spec is None:
            continue
        if spec.partition_by != (entry.field,):
            problems.append(
                f"分区全景: {name} 原文分区字段为 {entry.field!r}（{entry.kind}，{entry.reason}），"
                f"注册表却是 {spec.partition_by}"
            )
            continue
        if partition_rule_for(spec.partition_by) is not entry.rule:
            problems.append(
                f"分区全景: {name} 的分区类型应为「{entry.kind}」（{entry.rule.value}），"
                f"实际归档为 {partition_rule_for(spec.partition_by).value}"
            )

    # 3. changelog 三选一：三档都在用，且层级与口诀一致
    dist = changelog_distribution()
    for producer, mode in CHANGELOG_MODES.items():
        names = dist.get(producer, ())
        if not names:
            problems.append(f"changelog 三选一: {producer.value}（{mode.scenario}）在全湖无人使用")
            continue
        wrong = sorted(n for n in names if tables[n].layer not in mode.layers)
        if wrong:
            problems.append(
                f"changelog 三选一: {producer.value} 的适用层级是 "
                f"{[x.value for x in mode.layers]}，但 {wrong} 不在其中"
            )

    # 4. 主键三原则：原文点名的示例主键逐字一致
    for principle in PRIMARY_KEY_PRINCIPLES:
        for name, pk in principle.examples:
            spec = tables.get(name)
            if spec is None:
                problems.append(f"主键三原则: {principle.title} 的示例表 {name} 未在注册表登记")
            elif spec.primary_key != pk:
                problems.append(
                    f"主键三原则: {principle.title} 点名 {name} 的 PK 为 {pk}，"
                    f"注册表却是 {spec.primary_key}"
                )
    return problems


def validate_all(*, include_source_matrices: bool = True) -> dict[str, list[str]]:
    """全表校验，返回 {表名: 违规列表}，只含有问题的表。

    :param include_source_matrices: 是否一并做全湖级的原文决策表对账
        （``reconcile_source_matrices()``）。开启时，全湖级违规挂在伪表名
        :data:`SOURCE_MATRIX_KEY` 下，和逐表违规一起出现在 ``make validate`` 里。
        需要纯粹的逐表结果（例如子系统只想校验自己那几张表）时置 False。
    """
    out = {t.name: p for t in all_tables() if (p := t.validate())}
    if include_source_matrices and (matrix := reconcile_source_matrices()):
        out[SOURCE_MATRIX_KEY] = matrix
    return out


def audit_notes() -> dict[str, dict[str, list[str]]]:
    """非阻断审计：返回 ``{表名: {"naming": [...], "bucket": [...]}}``，只含有提示的表。

    与 ``validate_all()`` 的区别是**不拦人**。两类提示都是「惯例偏离」而非红线：

    * ``naming`` —— 四段式命名的偏离（缺域段 / 缺粒度后缀 / 后缀用在非惯用层）。
      原文自己给的表名就有大量这类偏离（见 source-deviations A-6），拦死没有意义。
    * ``bucket`` —— 档位用在了五档决策表的「典型层级」之外。目前只有
      ``dwd_mining_tag_dict_detail`` 一张（DWD 层的受控词表取 1 档「字典表/极小表」，
      而 1 档的典型层级写的是 ADS），是有意为之。

    供 CLI 的 ``catalog-validate`` 第二段与人工复核调用，不参与 ``make check`` 的退出码。
    """
    out: dict[str, dict[str, list[str]]] = {}
    for t in all_tables():
        notes = {"naming": t.naming_notes(), "bucket": t.bucket_notes()}
        if any(notes.values()):
            out[t.name] = notes
    return out


def render_all_ddl(layer: Layer | None = None) -> str:
    """渲染建表脚本。layer 为空则渲染全部。"""
    tables = by_layer(layer) if layer else all_tables()
    return "\n".join(t.render_ddl() for t in tables)
