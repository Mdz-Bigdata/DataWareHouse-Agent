"""隔离表 ods_quality_issue 的取用入口——**表结构本身不在这里**。

表结构的唯一事实源是注册表 :mod:`adas_lakehouse.catalog.registry`
（定义落在 ``catalog/tables/_quality.py``）。本模块过去自带一份同名 TableSpec，
列比注册表丰富，于是同一张表在仓库里有两套结构：门禁按本地那份拼 INSERT、
注册表按自己那份渲染 DDL，真跑到 Paimon 上必然对不上。现在本地定义已删除，
门禁一律从注册表取规格，两套结构合并为一套。

原文第五章：「隔离表是这套闭环的物理基础，核心字段全部围绕『可重放、可追责』设计」。
这些字段（detected_at / source_table / record_key / rule_ids / raw_payload /
payload_hash / issue_status / repair_action / recheck_count / SLA 四列 …）已在
「并列」阶段并入注册表，能力一列未少；注册表里另有一组早期同义列
（target_table / source_record_key / rule_id / isolate_time / handle_status …）
与之并存，flink/sql/ 的隔离分支仍按那组旧列名写入，两组的对照表见
``catalog/tables/_quality.py`` 的文件头与 columns 里的分隔注释。

物理策略（由注册表持有，此处只复述，不再声明）：
  · 分区 ``dt``：全湖 6 张分区表之一，规则一「大体量 + 时间范围查询」——
    异常分析永远是「看最近 N 天」
  · 主键 ``(issue_id, dt)``：原则三要求分区表主键包含分区字段（Paimon 硬要求）；
    issue_id 由「表 + 记录键 + 报文哈希 + 命中规则」派生，重放幂等
  · bucket 4：中等体量 ODS · changelog-producer input：ODS 层默认

数据域归属：隔离表独立于 11 数据域之外，在注册表里登记为伪域 ``quality_``
（见 ``domains.QUALITY_GATE_PSEUDO_DOMAIN``），并置 ``name_omits_domain=True``。
注册工作由 ``catalog.registry._MODULES`` 里的 ``_quality`` 模块完成，本模块不参与注册。
"""

from __future__ import annotations

from ..catalog import registry
from ..catalog.spec import TableSpec

__all__ = ["QUALITY_ISSUE_TABLE_NAME", "QUALITY_ISSUE", "TABLES", "columns", "render_ddl"]

#: 隔离表表名（原文第五章点名）。
QUALITY_ISSUE_TABLE_NAME = "ods_quality_issue"

#: 隔离表规格——注册表持有的**同一个对象**，不是副本。
#: 取不到就直接在 import 期炸，好过让门禁按一份幻觉结构拼 SQL。
QUALITY_ISSUE: TableSpec = registry.by_name(QUALITY_ISSUE_TABLE_NAME)

#: 兼容旧用法的单元素序列。注册由 ``catalog/tables/_quality.py`` 负责，
#: 这里**不要**再登记进 ``catalog.registry._MODULES``——会触发表名重复校验。
TABLES: tuple[TableSpec, ...] = (QUALITY_ISSUE,)


def columns() -> frozenset[str]:
    """隔离表的全部列名（含 catalog.spec 按层级自动追加的系统字段）。

    写入侧（``quality.isolation`` 的各 IssueStore）用它判断「这一列能不能写」，
    不必自己再抄一份列清单。
    """
    return frozenset(c.name for c in QUALITY_ISSUE.all_columns())


def timestamp_columns() -> frozenset[str]:
    """隔离表里类型为 TIMESTAMP 的列名。

    渲染 INSERT 时这些列的字面量要带 ``TIMESTAMP`` 前缀、置空要 CAST 成
    ``TIMESTAMP(3)``——判据取自注册表的列类型，而不是手抄一份名单。
    """
    return frozenset(
        c.name for c in QUALITY_ISSUE.all_columns() if c.type.upper().startswith("TIMESTAMP")
    )


def render_ddl(*, catalog: str | None = None, database: str | None = None) -> str:
    """渲染隔离表的 Flink SQL 建表语句（字段清单来自注册表）。

    catalog / database 缺省取 :func:`adas_lakehouse.config.settings` 的 Paimon 配置。
    """
    from ..config import settings

    cfg = settings()
    return QUALITY_ISSUE.render_ddl(
        catalog=catalog or cfg.paimon.catalog,
        database=database or cfg.paimon.database,
    )
