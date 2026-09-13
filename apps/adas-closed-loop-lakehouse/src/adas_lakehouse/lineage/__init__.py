"""Paimon + Neo4j 湖图双引擎数据血缘追溯。

一句话：**湖仓管事实、图库管关系**。
Paimon 是唯一事实源，Neo4j 只是关系视图，属性永不双存（[a13] 结语第一句）。

来源
----
``[a13]``
    系列二 · 湖仓实战 第 5 篇《智驾数据闭环湖仓实战：Paimon + Neo4j 湖图双引擎
    数据血缘追溯系统》 https://mp.weixin.qq.com/s/Yfrk2Z_izzzGSC_BQMsinQ
``[a11]``
    系列一 第 4 篇《数据闭环全局 data_id 设计：贯穿智驾全链路的三级 ID 体系》
    https://mp.weixin.qq.com/s/bxyDkxNgLg7qhkJLqCjd9A

子系统全貌
----------
======================= ==========================================================
模块                     职责
======================= ==========================================================
:mod:`.constants`        原文出现的每一个数字与字面量，逐字落地并标注出处
:mod:`.model`            五类节点 / 七类关系 / 三级血缘 / 三套状态机 / 属性护栏
:mod:`.events`           湖仓四张事实表的行 → 幂等图变更集（链路二/三共用的转换）
:mod:`.graph`            Neo4j 访问层：MERGE 建图、唯一约束、受控遍历、深度闸门
:mod:`.resolver`         按节点 ID 批量回湖仓补属性（走 StarRocks External Catalog）
:mod:`.sync`             链路二实时同步 + 链路三 T+1 对账 UPSERT 补齐
:mod:`.query`            四大查询方向，结果同时带血缘路径与湖仓来源（表名 + ID）
======================= ==========================================================

配套 SQL
--------
* ``flink/sql/lineage_realtime_sync.sql``   链路二：Paimon changelog → Kafka 变更消息
* ``flink/sql/lineage_reconcile_t1.sql``    链路三：T+1 增量扫描与湖-图差异体检
* ``ddl/starrocks_lineage.sql``             属性回取视图 + 大批量影响分析的离线统计链路

三句话带走（[a13] 结语逐字）
--------------------------
1. **湖仓管事实、图库管关系** —— Paimon 是唯一事实源，Neo4j 只是关系视图，属性永不双存；
2. **三级 ID 是统一语言** —— data_id / artifact_id / run_id 在湖仓是主键、在图库是节点，
   跨库协同零翻译成本；
3. **实时保速度、对账保兜底** —— binlog 实时 MERGE 失败不阻塞湖仓，T+1 对账 UPSERT
   幂等补齐，「湖-图」最终一致。

快速上手
--------
::

    from adas_lakehouse.lineage import (
        LineageQueryService, Neo4jGraphStore, LakehouseResolver,
        RealtimeLineageSync, ReconciliationJob,
    )

    graph = Neo4jGraphStore()
    graph.init_schema()                                   # 五类节点的 id 唯一约束

    RealtimeLineageSync(graph).handle(                    # 链路二：一条变更进图
        "dwd_production_artifact_detail", row)

    ReconciliationJob(graph, LakehouseResolver()).run()   # 链路三：T+1 对账补齐

    svc = LineageQueryService(graph, LakehouseResolver())
    svc.forward_trace("COLLECT_BP_20240115143022_a3f8")   # 正向追踪
    svc.backward_trace("BC_20240120_001")                 # 反向追溯
    svc.compare_versions("COLLECT_BP_20240115143022_a3f8", "slam")   # 版本分支对比
    svc.impact_analysis("slam", "v3")                     # 影响分析

外部客户端库（neo4j / pymysql / kafka-python）全部延迟 import——
没装也不影响 ``import adas_lakehouse.lineage``，只在真正建连接时报错。
"""

from __future__ import annotations

from .constants import (
    MAX_TRAVERSAL_DEPTH,
    MIN_TRAVERSAL_DEPTH,
    OFFLINE_IMPACT_FANOUT_THRESHOLD,
    RECONCILE_LAG_DAYS,
    SOURCE_A11,
    SOURCE_A13,
    SOURCE_EXAMPLES,
    TRAVERSAL_DEPTH_RANGE_TEXT,
)
from .events import (
    ArtifactFact,
    BadcaseFact,
    ClipFact,
    DatasetVersionFact,
    LineageFact,
    RunFact,
    fact_from_row,
    mutation_from_rows,
    parse_id_list,
)
from .graph import (
    CONSTRAINT_STATEMENTS,
    LineageGraphError,
    Neo4jGraphStore,
    Neo4jUnavailable,
    Statement,
    TraversalDepthError,
    resolve_depth,
)
from .model import (
    CONSISTENCY_REDLINES,
    GUARDRAILS,
    NODE_SOURCE_TABLES,
    REL_SPECS,
    TRAVERSAL_PROPERTIES,
    ArtifactStatus,
    DatasetVersionStatus,
    GraphEdge,
    GraphMutation,
    GraphNode,
    GraphPropertyPolicy,
    InvalidTransitionError,
    LakehouseSource,
    LineageLevel,
    LineageModelError,
    NodeLabel,
    NodeRef,
    RelSpec,
    RelType,
    RunStatus,
    check_transition,
    lakehouse_source_for,
    sanitize_properties,
    validate_edges,
)
from .query import (
    SOURCE_CYPHER_BACKWARD_TRACE,
    SOURCE_CYPHER_IMPACT_ANALYSIS,
    LineagePath,
    LineageQueryService,
    LineageResult,
    PathHop,
    QueryDirection,
)
from .resolver import (
    ATTRIBUTE_COLUMNS,
    LakehouseResolver,
    LakehouseUnavailable,
    ResolveResult,
)
from .sync import (
    LINEAGE_CHANGE_TOPIC,
    DeadLetter,
    InMemoryFailureSink,
    RealtimeLineageSync,
    ReconcileReport,
    ReconciliationJob,
    SyncOutcome,
    reconcile_window,
)

__all__ = [
    # 出处与常量
    "SOURCE_A13",
    "SOURCE_A11",
    "SOURCE_EXAMPLES",
    "MIN_TRAVERSAL_DEPTH",
    "MAX_TRAVERSAL_DEPTH",
    "TRAVERSAL_DEPTH_RANGE_TEXT",
    "RECONCILE_LAG_DAYS",
    "OFFLINE_IMPACT_FANOUT_THRESHOLD",
    # 图模型
    "NodeLabel",
    "RelType",
    "RelSpec",
    "REL_SPECS",
    "LineageLevel",
    "GraphPropertyPolicy",
    "ArtifactStatus",
    "RunStatus",
    "DatasetVersionStatus",
    "NodeRef",
    "GraphNode",
    "GraphEdge",
    "GraphMutation",
    "LakehouseSource",
    "NODE_SOURCE_TABLES",
    "TRAVERSAL_PROPERTIES",
    "CONSISTENCY_REDLINES",
    "GUARDRAILS",
    "LineageModelError",
    "InvalidTransitionError",
    "check_transition",
    "sanitize_properties",
    "lakehouse_source_for",
    "validate_edges",
    # 事实 → 图变更
    "LineageFact",
    "ClipFact",
    "ArtifactFact",
    "RunFact",
    "DatasetVersionFact",
    "BadcaseFact",
    "fact_from_row",
    "mutation_from_rows",
    "parse_id_list",
    # 图库
    "Neo4jGraphStore",
    "Statement",
    "CONSTRAINT_STATEMENTS",
    "resolve_depth",
    "LineageGraphError",
    "Neo4jUnavailable",
    "TraversalDepthError",
    # 湖仓回取
    "LakehouseResolver",
    "LakehouseUnavailable",
    "ResolveResult",
    "ATTRIBUTE_COLUMNS",
    # 双链路
    "RealtimeLineageSync",
    "SyncOutcome",
    "DeadLetter",
    "InMemoryFailureSink",
    "ReconciliationJob",
    "ReconcileReport",
    "reconcile_window",
    "LINEAGE_CHANGE_TOPIC",
    # 查询
    "LineageQueryService",
    "QueryDirection",
    "LineageResult",
    "LineagePath",
    "PathHop",
    "SOURCE_CYPHER_BACKWARD_TRACE",
    "SOURCE_CYPHER_IMPACT_ANALYSIS",
]
