"""原文里出现过的每一个具体数字与字面量，逐字收敛到这里，并标注出处。

这个模块不含逻辑，只含「事实」——把散落在两篇原文里的数字集中起来，
好处是：任何一个数字被改动都能被 code review 一眼看见，且能对回原文。

出处标记
--------
``[a13]``
    系列二 · 湖仓实战 第 5 篇《智驾数据闭环湖仓实战：Paimon + Neo4j 湖图双引擎
    数据血缘追溯系统》，公众号「小周」，2026-09-04，
    https://mp.weixin.qq.com/s/Yfrk2Z_izzzGSC_BQMsinQ
``[a11]``
    系列一 第 4 篇《数据闭环全局 data_id 设计：贯穿智驾全链路的三级 ID 体系》，
    公众号「小周的成长之路」，2026-08-27，
    https://mp.weixin.qq.com/s/bxyDkxNgLg7qhkJLqCjd9A

约定
----
* 常量名后缀 ``_SOURCE`` 表示「原文原样抄录，本项目不一定照此执行」（例如 bucket=32）。
* 凡 docstring / 注释里带「⚠️ 原文未明确，本项目设计：」的，是本项目补的设计，
  原文没给数字，不要当成原文方案引用。
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "SOURCE_A13",
    "SOURCE_A11",
    "CLIP_DURATION_MINUTES",
    "ID_LEVEL_COUNT",
    "ID_RULE_COUNT",
    "ID_TIMESTAMP_DIGITS",
    "CONTENT_HASH_HEX_LENGTH",
    "CLOSED_LOOP_SEGMENT_COUNT",
    "INDUSTRY_SEGMENT_COUNT",
    "NODE_LABEL_COUNT",
    "REL_TYPE_COUNT",
    "LINEAGE_LEVEL_COUNT",
    "FACT_TABLE_COUNT",
    "BUSINESS_GOAL_COUNT",
    "QUERY_DIRECTION_COUNT",
    "QUERY_STEP_COUNT",
    "WRITE_CHANNEL_COUNT",
    "GUARDRAIL_COUNT",
    "CONSISTENCY_REDLINE_COUNT",
    "BACKFILL_STEP_COUNT",
    "SOURCE_CYPHER_EXAMPLE_COUNT",
    "MIN_TRAVERSAL_DEPTH",
    "MAX_TRAVERSAL_DEPTH",
    "TRAVERSAL_DEPTH_RANGE_TEXT",
    "RECONCILE_LAG_DAYS",
    "RECONCILE_SCHEDULE_TEXT",
    "PIPELINE_STAGES",
    "SLAM_OLD_VERSION",
    "SLAM_NEW_VERSION",
    "RUN_PARAM_EXAMPLE",
    "RUN_PARAM_EXAMPLE_MAX_ITER",
    "BACKFILL_HISTORICAL_CLIP_SCALE",
    "ARTIFACT_TABLE_BUCKET_SOURCE",
    "ARTIFACT_TABLE_CHANGELOG_PRODUCER_SOURCE",
    "PROJECT_BUCKET_TIERS",
    "SOURCE_EXAMPLES",
    "MAX_PATHS_PER_QUERY",
    "ID_BATCH_SIZE",
    "OFFLINE_IMPACT_FANOUT_THRESHOLD",
    "REALTIME_MAX_RETRIES",
    "REALTIME_RETRY_BACKOFF_SECONDS",
    "GRAPH_QUERY_TIMEOUT_SECONDS",
]

SOURCE_A13: Final[str] = (
    "[a13] 系列二·湖仓实战 第 5 篇《Paimon + Neo4j 湖图双引擎数据血缘追溯系统》 "
    "https://mp.weixin.qq.com/s/Yfrk2Z_izzzGSC_BQMsinQ"
)
SOURCE_A11: Final[str] = (
    "[a11] 系列一 第 4 篇《数据闭环全局 data_id 设计：贯穿智驾全链路的三级 ID 体系》 "
    "https://mp.weixin.qq.com/s/bxyDkxNgLg7qhkJLqCjd9A"
)

# --------------------------------------------------------------------------- 数据单元与 ID

#: clip = 约 1 分钟连续采集片段，一个 clip 对应一个 data_id。
#: 出处 [a11] 一、「本方案的采集最小数据单元是 clip：约 1 分钟连续采集片段」
CLIP_DURATION_MINUTES: Final[int] = 1

#: 三级 ID 体系：data_id / artifact_id / run_id。出处 [a11] 二
ID_LEVEL_COUNT: Final[int] = 3

#: 四条核心生成规则（唯一性 / 时间戳格式 / 重刷处理 / 关联数据）。出处 [a11] 三
ID_RULE_COUNT: Final[int] = 4

#: 时间戳格式 yyyyMMddHHmmss，精确到秒 —— 14 位数字。出处 [a11] 三·规则 2
ID_TIMESTAMP_DIGITS: Final[int] = 14

#: content_hash 取 8 位十六进制。出处 [a11]/[a13] 全部示例 ID 的末段：
#: a3f8（4 位 seq）/ a1b2c3d4 / 9f3a21c7 / b7e8f9a0 / c4d5e6f7 均为 8 位 hex。
#: 与 adas_lakehouse.ids.content_hash(length=8) 默认值一致。
CONTENT_HASH_HEX_LENGTH: Final[int] = 8

#: 三级 ID 贯穿闭环全部 7 个环节。出处 [a11] 四
CLOSED_LOOP_SEGMENT_COUNT: Final[int] = 7

#: 行业共识的 8 环节模型（[a11] 开头引用的前序文章标题「8 环节模型与行业共识」）。
INDUSTRY_SEGMENT_COUNT: Final[int] = 8

# --------------------------------------------------------------------------- 图模型规模

#: 图库五类节点：Clip / Artifact / Run / DatasetVersion / Badcase。出处 [a13] 二
NODE_LABEL_COUNT: Final[int] = 5

#: 图库七类关系：CONTAINS / DERIVED_FROM / SUPERSEDED_BY / INPUT / PRODUCED /
#: REFERENCES / TRACED_TO。出处 [a13] 二
REL_TYPE_COUNT: Final[int] = 7

#: 三级血缘模型：实体血缘 / 版本血缘 / 运行血缘。出处 [a13] 三
LINEAGE_LEVEL_COUNT: Final[int] = 3

#: 湖仓四张血缘元信息表。出处 [a13] 3.1
FACT_TABLE_COUNT: Final[int] = 4

#: 血缘系统支撑的四个业务目标：可追溯 / 可重放 / 可对比 / 可演进。出处 [a13] 一
BUSINESS_GOAL_COUNT: Final[int] = 4

#: 四个查询方向：正向追踪 / 反向追溯 / 版本分支对比 / 影响分析。出处 [a13] 五 与 [a11] 六
QUERY_DIRECTION_COUNT: Final[int] = 4

#: 一次完整血缘查询三步走：图库遍历定范围 → 回湖仓补属性 → 组装带来源的结果。出处 [a13] 五
QUERY_STEP_COUNT: Final[int] = 3

#: 三条写入链路：事实源 / 实时 / 对账。出处 [a13] 四
WRITE_CHANNEL_COUNT: Final[int] = 3

#: 四条工程护栏：属性单一事实源 / 双链路可靠性 / 遍历边界 / 结果可审计。出处 [a13] 六
GUARDRAIL_COUNT: Final[int] = 4

#: 三条一致性红线。出处 [a13] 4.2
CONSISTENCY_REDLINE_COUNT: Final[int] = 3

#: 重刷流程四步走。出处 [a11] 五 与 [a13] 3.2
BACKFILL_STEP_COUNT: Final[int] = 4

#: 原文声明「七个典型 Cypher 查询」，正文仅展开 2 个（反向追溯 + 影响分析）。出处 [a13] 文末
SOURCE_CYPHER_EXAMPLE_COUNT: Final[int] = 7

# --------------------------------------------------------------------------- 遍历边界

#: 多跳遍历限定深度 3-5 跳防止扇出爆炸。出处 [a13] 六·遍历边界 与 [a11] 六·遍历边界：
#: 「3–5 跳是实践验证过的安全区间」
MIN_TRAVERSAL_DEPTH: Final[int] = 3
MAX_TRAVERSAL_DEPTH: Final[int] = 5
TRAVERSAL_DEPTH_RANGE_TEXT: Final[str] = "3-5 跳"

# --------------------------------------------------------------------------- 对账链路

#: T+1 对账：滞后 1 天扫描湖仓增量。出处 [a13] 四·链路三「T+1 / 定时扫描湖仓增量」
RECONCILE_LAG_DAYS: Final[int] = 1
RECONCILE_SCHEDULE_TEXT: Final[str] = "T+1"

# --------------------------------------------------------------------------- 产线与版本

#: 产线 5 个环节。出处 [a13] 3.1 DDL 注释「step STRING -- align / slam / ann / qc / post」
PIPELINE_STAGES: Final[tuple[str, ...]] = ("align", "slam", "ann", "qc", "post")

#: 重刷示例：SLAM 算法 v3 → v4。出处 [a13] 3.2 与 [a11] 五
SLAM_OLD_VERSION: Final[str] = "v3"
SLAM_NEW_VERSION: Final[str] = "v4"

#: 运行参数快照示例，逐字抄自 [a13] 4.1 建图语句 ``SET r.params='{"max_iter":200}'``
RUN_PARAM_EXAMPLE: Final[str] = '{"max_iter":200}'
RUN_PARAM_EXAMPLE_MAX_ITER: Final[int] = 200

#: 「历史百万级 clip 要不要重新处理」——重刷影响面量级。出处 [a11] 五
BACKFILL_HISTORICAL_CLIP_SCALE: Final[int] = 1_000_000

# --------------------------------------------------------------------------- 原文 DDL 字面量

#: 原文 dwd_production_artifact_detail 建表节选写的是 'bucket' = '32'。出处 [a13] 3.1
#:
#: ⚠️ 冲突登记：本项目的 Bucket 五档策略（见 catalog.spec.BUCKET_TIERS）只允许
#: 1 / 2 / 4 / 8 / 16，不含 32。血缘模块不建表（四张事实表归 catalog/tables/ 各域模块
#: 所有），此处仅原样保留原文数字供对账；真正落库时按五档策略取 16（超大表 / 高并发写入）。
ARTIFACT_TABLE_BUCKET_SOURCE: Final[int] = 32

#: 原文同一段 DDL 的 'changelog-producer' = 'input'。出处 [a13] 3.1
#:
#: ⚠️ 冲突登记：dwd_production_artifact_detail 是 DWD 层，本项目默认 lookup；
#: 原文写 input。同样只做登记，不在本模块建表。
ARTIFACT_TABLE_CHANGELOG_PRODUCER_SOURCE: Final[str] = "input"

#: 本项目 Bucket 五档（与 catalog.spec.BUCKET_TIERS 同源，此处复述用于上面的冲突说明）。
PROJECT_BUCKET_TIERS: Final[tuple[int, ...]] = (1, 2, 4, 8, 16)

#: 原文出现过的示例 ID / 示例值，逐字抄录。用于冒烟测试与文档示例，不要改写。
SOURCE_EXAMPLES: Final[dict[str, str]] = {
    # [a11] 二·一级示例
    "data_id": "COLLECT_BP_20240115143022_a3f8",
    # [a11] 二·二级示例
    "artifact_id": "COLLECT_BP_20240115143022_a3f8_slam_v3_a1b2c3d4",
    # [a11] 二·三级示例 / [a13] 4.1 建图语句
    "run_id": "run_slam_20240116103000_9f3a21c7",
    # [a13] 4.1 建图语句里的产物 ID 后缀
    "artifact_slam_v4_suffix": "_slam_v4_b7e8f9a0",
    "artifact_slam_v3_suffix": "_slam_v3_c4d5e6f7",
    "artifact_align_v1_suffix": "_align_v1_9f3a21c7",
    # [a13] 4.1 数据集版本节点 ID
    "dataset_version_id": "DS_0001_V2",
    # [a13] 5.1 反向追溯 Cypher 的 Badcase ID
    "badcase_id": "BC_20240120_001",
    # [a13] 4.1 运行参数快照
    "run_params": RUN_PARAM_EXAMPLE,
}

# --------------------------------------------------------------------------- 本项目补充的工程参数

#: ⚠️ 原文未明确，本项目设计：单次图查询返回的路径条数上限。
#: 原文只说「限定深度 3-5 跳防止扇出爆炸」，没给条数上限；但深度限制只挡住了链路长度，
#: 挡不住单跳的横向扇出（一个 clip 可能被上百个数据集版本引用），故补一道条数闸门。
MAX_PATHS_PER_QUERY: Final[int] = 1000

#: ⚠️ 原文未明确，本项目设计：回湖仓按 ID 批量补属性时，单条 SQL 的 IN 列表长度上限。
#: 取 500 是为了让 StarRocks 的 IN 谓词仍能走前缀索引裁剪，同时不把 SQL 撑到 MB 级。
ID_BATCH_SIZE: Final[int] = 500

#: ⚠️ 原文未明确，本项目设计：影响分析的下游规模阈值。
#: 原文护栏只定性说「大批量下游影响分析改走湖仓离线统计审计链路」，没给「多大算大批量」。
#: 本项目以「图库一跳命中的 Artifact 数」为准，超过该值即拒绝在图库硬算，
#: 改走 ddl/starrocks_lineage.sql 里的离线统计视图。
OFFLINE_IMPACT_FANOUT_THRESHOLD: Final[int] = 10_000

#: ⚠️ 原文未明确，本项目设计：实时链路（链路二）单条事件的 MERGE 重试次数与退避秒数。
#: 红线①「实时链路失败不阻塞湖仓写入」要求重试必须有限且不抛穿，故给定次数与退避。
REALTIME_MAX_RETRIES: Final[int] = 3
REALTIME_RETRY_BACKOFF_SECONDS: Final[tuple[float, ...]] = (0.5, 2.0, 5.0)

#: ⚠️ 原文未明确，本项目设计：图库读查询超时（秒）。防止漏网的扇出把连接池打满。
GRAPH_QUERY_TIMEOUT_SECONDS: Final[float] = 30.0
