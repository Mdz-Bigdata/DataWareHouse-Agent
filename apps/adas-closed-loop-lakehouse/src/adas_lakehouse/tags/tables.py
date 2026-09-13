"""统一标签体系用到的 Paimon 表——结构一律取自 :mod:`catalog.registry`，本模块不再自带定义。

这里原先有一份本地 :class:`~adas_lakehouse.catalog.spec.TableSpec`（四张表的完整字段），
与 registry 里的同名表并存。两套结构只要漂移一点，按本地那份拼出来的 SQL 打到真实
Paimon 上就是「列不存在」——所以本地定义整体删除，改为引用。

四张表（表名常量在 :mod:`.constants`，SQL 与仓储层一律引用那里）::

    dwd_mining_tag_dict_detail    统一标签字典（五大类别受控词表，体系的地基）
    dwd_mining_data_tag_detail    clip 级标签事实（三源收口管道出口之一）
    dwd_mining_image_tag_detail   image 级标签事实（管道出口之二，含 CAPTION 特殊标签）
    dws_mining_tag_coverage_daily 标签覆盖度日指标（统计日期 × 项目 × 标签类别）

本模块现在只做三件事：

1. 按表名从 registry 取规格——:data:`TABLES` / :func:`spec`；
2. 暴露列名集合，给落库行做「列真的存在吗」的自检——:func:`columns` /
   :func:`unknown_columns` / :func:`check_row`；
3. 渲染与校验这四张表——:func:`render_ddl` / :func:`validate`（对外行为不变）。

**近义异名一律以 registry 侧为准。** 子系统内存模型里带类型的字段（枚举、元组、布尔）
保留内存形态，到列名的翻译只在各自的 ``to_row()`` 一处发生，对照表::

    子系统内存字段                 registry 列名                   所在表
    ─────────────────────────────────────────────────────────────────────────
    valid_flag (bool)          →  tag_status（active/invalid）    两张事实表
    tag_time                   →  first_tag_time                  两张事实表
    inherited_from_clip        →  inherited_from_data_tag         image 事实表
    mutex_group                →  mutual_exclusive_group          字典表
    applicable_sources (tuple) →  tag_source_type（逗号分隔）      字典表
    source_ontology            →  ontology_ref                    字典表
    reviewers[0] / [1]         →  review_operator / reviewer_secondary  字典表
    dt                         →  stat_date                       覆盖度日指标
    *_cnt / *_rate             →  *_count / *_ratio               覆盖度日指标

``tag_status`` 与 ``valid_flag`` 是同一件事的两种形态——registry 侧该列的注释写得很明白：
「标签事实状态：active/invalid（互斥裁决落败置 invalid，即 tags 侧的 valid_flag）」。
互斥裁决的落败方在内存里是一个布尔，落库是一个状态字符串，两边都不删除记录。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ..catalog import registry
from ..catalog.spec import TableSpec
from .constants import (
    COVERAGE_TABLE,
    DATA_TAG_TABLE,
    DICT_TABLE,
    IMAGE_TAG_TABLE,
)

__all__ = [
    "TABLE_NAMES",
    "TABLES",
    "spec",
    "columns",
    "unknown_columns",
    "check_row",
    "render_ddl",
    "validate",
]

#: 本子系统写入/读取的四张表，顺序即管道的落库顺序（字典 → clip 事实 → image 事实 → 日指标）。
TABLE_NAMES: tuple[str, ...] = (DICT_TABLE, DATA_TAG_TABLE, IMAGE_TAG_TABLE, COVERAGE_TABLE)


def spec(name: str) -> TableSpec:
    """取一张表的规格。唯一事实源是 registry，本模块不持有副本。

    :raises KeyError: 表名未在 registry 登记
    """
    return registry.by_name(name)


#: 四张表的规格。元素就是 registry 里的那几个对象本身——不是拷贝，也不是再定义一遍。
TABLES: tuple[TableSpec, ...] = tuple(spec(n) for n in TABLE_NAMES)


def columns(name: str) -> frozenset[str]:
    """一张表的全部列名（业务字段 + 该层系统字段）。

    落库行的键必须落在这个集合里，否则 INSERT 会因「列不存在」直接失败。
    """
    return frozenset(c.name for c in spec(name).all_columns())


def unknown_columns(name: str, keys: Iterable[str]) -> tuple[str, ...]:
    """``keys`` 里 registry 没有的列名，排序返回；全都认识时返回空元组。"""
    known = columns(name)
    return tuple(sorted(k for k in keys if k not in known))


def check_row(name: str, row: Mapping[str, Any]) -> Mapping[str, Any]:
    """校验一行的列名都在 registry 里，原样返回该行，便于内联使用。

    给 ``to_row()`` 的单测与排障用——两套结构并存时最难发现的就是「本地有、湖里没有」
    的那几列，这里把它变成一句能跑的断言。

    :raises ValueError: 行里带了 registry 没有的列
    """
    extra = unknown_columns(name, row.keys())
    if extra:
        raise ValueError(
            f"{name}: 这些列不在 registry 里 {list(extra)}——"
            "表结构以 catalog/tables/ 为唯一事实源，缺列请在那边补，不要在子系统本地加"
        )
    return row


def validate() -> dict[str, list[str]]:
    """四张表的规格自检，返回 {表名: 违规列表}，只含有问题的表。"""
    return {t.name: p for t in TABLES if (p := t.validate())}


def render_ddl(*, catalog: str | None = None, database: str | None = None) -> str:
    """渲染四张表的 Flink SQL 建表语句。

    :param catalog: Paimon catalog 名，默认取 config.settings().paimon.catalog
    :param database: 库名，默认取 config.settings().paimon.database
    """
    from ..config import settings  # 延迟 import：渲染时才需要读配置

    cfg = settings().paimon
    cat = catalog or cfg.catalog
    db = database or cfg.database
    return "\n".join(t.render_ddl(catalog=cat, database=db) for t in TABLES)
