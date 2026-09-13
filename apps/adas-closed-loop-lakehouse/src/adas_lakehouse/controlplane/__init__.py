"""控制面：任务编排 / 规则下发 / 状态管理。

子系统「控制面/数据面分离」的控制面一半。数据面一半在
:mod:`adas_lakehouse.dataplane`。

来源：系列三「数据挖掘与 AI」第 1 篇《数据闭环数据挖掘平台架构设计：控制面/数据面
分离的工程实践》（2026-09-09，https://mp.weixin.qq.com/s/bSekzM_WjtGtAd_1WdbBAQ）。

======================================================================
一、为什么要分离（原文给的三条理由，一条不加）
======================================================================

1. **反面教训**（原文第二章）：很多团队做挖掘平台踩的最大的坑是，平台顺手建了一套
   自己的「数据副本」——抽帧结果存一份、标签存一份、向量再存一份，时间一长，平台里
   的数据与湖仓对不上，**口径分裂、血缘断裂**。

2. **正面收益**（原文第四章）：**平台可以整体重建与迁移，主数据不受影响**——
   MySQL 丢了，重建配置即可；服务挂了，无状态重启即可。

3. **对账基准**（原文第四章）：湖仓是唯一对账基准，不存在双写导致的口径分裂。

判断健康与否的标准，原文给得很干脆：「把平台的数据库清空重建，业务数据是否完好？」
—— :meth:`~.scheduler.ControlPlane.rebuild_check` 就是这句话的可执行版本。

======================================================================
二、两面的职责边界
======================================================================

============  =====================================  =========================
面            持有什么                               存储底座
============  =====================================  =========================
控制面        规则配置 / 任务配置与执行状态 /        MySQL + Redis（平台本地）
              审核流状态 / 检索热点 / 字典热缓存
数据面        clip / 图片 / 标签 / 向量（主数据）    Paimon 湖仓 + StarRocks
============  =====================================  =========================

边界由 :func:`~.contracts.asset_plane` 写死，由 :func:`~.contracts.assert_no_master_data`
在运行时守卫：主数据一旦试图进入控制面载荷，直接抛 :class:`~.contracts.MasterDataLeak`。

======================================================================
三、交互协议（只有两种信封，方向单一）
======================================================================

  控制面 --:class:`~.contracts.TaskEnvelope`--> 数据面
      装「怎么算」：规则版本、算法参数、输入选择器（SQL 谓词）、run_id、优先级。
      **不装数据。**

  数据面 --:class:`~.contracts.RunReport`--> 控制面
      回「算完了，产物在哪」：artifact_id、落点表、行数、状态。
      **不回数据。**

  控制面 --回流--> 数据面（:mod:`.reconcile`）
      规则配置经 Flink CDC 入湖 ``ods_mining_rule_config``；
      任务与审核动作定期回写 ``dwd_mining_task_detail``——每一步操作都进血缘。

======================================================================
四、任务生命周期
======================================================================

  DRAFT → SUBMITTED → QUEUED → DISPATCHED → RUNNING
                                     ├→ AWAITING_REVIEW → SUCCEEDED / REJECTED
                                     ├→ SUCCEEDED
                                     └→ FAILED →（重试）→ QUEUED
  任意非终态 → CANCELLED

数据面只能把任务推到 RUNNING / AWAITING_REVIEW / SUCCEEDED / FAILED；排队、下发、
取消、审核裁决是控制面的权力。⚠️ 状态机本身为本项目设计，原文未给（见 :mod:`.lifecycle`）。

======================================================================
五、怎么调度 mining / sampling / tags
======================================================================

靠 :class:`~.subsystems.SubsystemAdapter` 这个 Protocol，以及**字符串形式**的模块路径
延迟绑定——控制面代码里没有一行 ``import adas_lakehouse.mining``。
子系统各自提供 ``<pkg>.plane_adapter.build_plane_adapter()``。任何一个子系统缺席，
只让它那条链路报 :class:`~.subsystems.SubsystemUnavailable`，其他链路照跑，
对应原文「任何一个引擎故障或扩容，都不影响其他链路」。

快速上手::

    from adas_lakehouse.controlplane import ControlPlane, SubmitRequest, TaskKind
    from adas_lakehouse.controlplane.rules import EXAMPLE_RULE_RAIN_NIGHT_UNLIT_INTERSECTION

    cp = ControlPlane()
    cp.rules.upsert(EXAMPLE_RULE_RAIN_NIGHT_UNLIT_INTERSECTION)
    task = cp.submit(SubmitRequest(kind=TaskKind.RULE_MINING,
                                   rule_id="RULE_RAIN_NIGHT_UNLIT_INTERSECTION"))
    cp.dispatch_once()      # 下发给 mining 子系统（需其提供 plane_adapter）
    cp.progress(task.task_id)
"""

from __future__ import annotations

from . import constants
from .constants import (
    API_PREFIX,
    API_VERSION,
    EMBEDDING_WINDOW_DEADLINE_HOUR,
    FIRST_PRINCIPLE,
    HEALTH_CRITERION,
    MAX_AUTO_RETRY,
    ONLINE_SERVICE_CPU_CORES,
    ONLINE_SERVICE_MEMORY_GB,
    ONLINE_SERVICE_MIN_REPLICAS,
    OPENAPI_GROUP_COUNT,
)
from .contracts import (
    CONTROL_PLANE_ASSETS,
    CONTROL_STATE_FIELD_HINTS,
    DATA_PLANE_ASSETS,
    REVIEW_REQUIRED_KINDS,
    SUBSYSTEM_ROUTING,
    WRITEBACK_COLUMN_MAP,
    WRITEBACK_TABLES,
    WRITEBACK_UNMAPPED_FIELDS,
    ArtifactRef,
    ControlStateLeak,
    MasterDataLeak,
    Plane,
    PlaneBoundaryError,
    ReviewDecision,
    RunReport,
    TaskEnvelope,
    TaskEvent,
    TaskKind,
    TaskRecord,
    TaskState,
    assert_no_control_state,
    assert_no_master_data,
    asset_plane,
    writeback_column_gap,
)
from .lifecycle import TRANSITIONS, IllegalTransition, RetryExhausted
from .openapi import API_ENDPOINTS, API_GROUPS, ApiError, OpenApiGateway, openapi_document
from .reconcile import (
    CdcJobPlan,
    DryRunWriteback,
    FlinkSqlGatewayWriteback,
    LakeWriteback,
    ReconcileReport,
    WritebackService,
    cdc_job_plan,
    reconcile_tasks,
)
from .rules import RuleConfig, RuleEngine, RuleRegistry, RuleValidationError
from .scheduler import ControlPlane, DispatchOutcome, RebuildCheck, SubmitRequest
from .store import (
    ControlPlaneStore,
    ControlPlaneStoreConfig,
    HotCache,
    InMemoryControlPlaneStore,
    InMemoryHotCache,
    MySqlControlPlaneStore,
    RedisHotCache,
)
from .subsystems import (
    OPTIONAL_SUBSYSTEMS,
    REQUIRED_SUBSYSTEMS,
    SUBSYSTEM_MODULE_PATHS,
    SubsystemAdapter,
    SubsystemBinding,
    SubsystemRegistry,
    SubsystemUnavailable,
)

__all__ = [
    # 门面
    "ControlPlane",
    "SubmitRequest",
    "DispatchOutcome",
    "RebuildCheck",
    # 契约
    "Plane",
    "TaskKind",
    "TaskState",
    "ReviewDecision",
    "TaskEnvelope",
    "RunReport",
    "TaskRecord",
    "TaskEvent",
    "ArtifactRef",
    "MasterDataLeak",
    "ControlStateLeak",
    "PlaneBoundaryError",
    "assert_no_master_data",
    "assert_no_control_state",
    "asset_plane",
    "CONTROL_PLANE_ASSETS",
    "DATA_PLANE_ASSETS",
    "CONTROL_STATE_FIELD_HINTS",
    "WRITEBACK_TABLES",
    "WRITEBACK_COLUMN_MAP",
    "WRITEBACK_UNMAPPED_FIELDS",
    "writeback_column_gap",
    "SUBSYSTEM_ROUTING",
    "REVIEW_REQUIRED_KINDS",
    # 生命周期
    "TRANSITIONS",
    "IllegalTransition",
    "RetryExhausted",
    # 规则
    "RuleConfig",
    "RuleEngine",
    "RuleRegistry",
    "RuleValidationError",
    # 子系统接入
    "SubsystemAdapter",
    "SubsystemRegistry",
    "SubsystemBinding",
    "SubsystemUnavailable",
    "SUBSYSTEM_MODULE_PATHS",
    "REQUIRED_SUBSYSTEMS",
    "OPTIONAL_SUBSYSTEMS",
    # 存储
    "ControlPlaneStore",
    "ControlPlaneStoreConfig",
    "InMemoryControlPlaneStore",
    "MySqlControlPlaneStore",
    "HotCache",
    "InMemoryHotCache",
    "RedisHotCache",
    # 回流与对账
    "CdcJobPlan",
    "cdc_job_plan",
    "LakeWriteback",
    "DryRunWriteback",
    "FlinkSqlGatewayWriteback",
    "WritebackService",
    "ReconcileReport",
    "reconcile_tasks",
    # OpenAPI
    "OpenApiGateway",
    "ApiError",
    "API_ENDPOINTS",
    "API_GROUPS",
    "openapi_document",
    # 常量
    "constants",
    "FIRST_PRINCIPLE",
    "HEALTH_CRITERION",
    "API_VERSION",
    "API_PREFIX",
    "OPENAPI_GROUP_COUNT",
    "MAX_AUTO_RETRY",
    "EMBEDDING_WINDOW_DEADLINE_HOUR",
    "ONLINE_SERVICE_CPU_CORES",
    "ONLINE_SERVICE_MEMORY_GB",
    "ONLINE_SERVICE_MIN_REPLICAS",
]
