"""数据质量门禁：智驾数据入湖的五步校验链路——拦得住、找得回、修得好。

来源：系列二 · 湖仓实战 第 6 篇《数据质量门禁设计：智驾数据入湖的五步校验链路》
（公众号「小周谈智驾数据闭环」，2026-09-05）。

一句话概括这个子系统做什么：**所有通道的数据在写入 ODS 之前，先过一遍规则化硬检查；
命中「拒绝入湖」的数据不直接丢弃，而是进入「拦截 → 隔离 → 告警 → 分流处置 → 复验重入湖」
五步闭环**。

模块地图
--------
:mod:`.thresholds`   原文全部数字的唯一出处（阈值 / SLA / 比例），业务代码里不写裸数字
:mod:`.severity`     五层质量问题、六维检查框架、三道防线、四档 SLA、三通道等分级体系
:mod:`.rules`        声明式规则模型 + 检查器实现（派发靠查表，不靠 if）
:mod:`.ruleset`      规则中心：注册、按表检索、灰度发布、误杀率统计
:mod:`.builtin`      内置规则集：通用 + MySQL CDC + Kafka + OSS 三通道
:mod:`.loader`       YAML 三级配置（检查类型 → 表 → 字段）的加载与导出
:mod:`.gate`         门禁执行器：检查器三分支 + ① 拦截 + ② 隔离（recordRejectedData）
:mod:`.isolation`    隔离表 ods_quality_issue 的记录模型与存储实现
:mod:`.alerting`     ③ 分级告警：P0 电话+钉钉 / P1 钉钉+工单 / P2 日报 / P3 周报
:mod:`.closed_loop`  ③④⑤ 编排：告警 → 分流处置 → 复验重入湖（超 3 轮升级 P0）
:mod:`.metrics`      门禁自监控两类指标 + 四个闭环度量指标
:mod:`.tables`       隔离表规格的取用入口——结构本身由 catalog.registry 持有，此处只转发
:mod:`.udf`          Flink 接入点 ``quality_gate_check``，flink/sql/quality_gate_pipeline.sql
                     按类名注册它。**故意不在这里 import**：它在 pyflink 装了的环境里
                     会把 pyflink 拉进来，而本包必须做到「没装客户端库也能 import」。
                     需要时显式 ``from adas_lakehouse.quality.udf import QualityGateUdf``。

三条不可动摇的设计约束
----------------------
1. **严重程度决定处置**：ERROR → REJECT，WARNING → ALLOW_WITH_FLAG，通过 → ACCEPTED。
   全门禁只有 :meth:`.gate.QualityGate._decide` 一处做这个判定。
2. **规则是数据不是代码**：新增规则 = 加一条 :class:`.rules.RuleSpec` 声明或一段 YAML，
   不改任何分支逻辑。
3. **被拦下的数据不丢弃**：命中即写隔离表并保留原始报文，可重放、可审计。

最小用法
--------
>>> from adas_lakehouse.quality import QualityGate, Channel
>>> gate = QualityGate(source_system="kafka:vehicle.trigger.event")
>>> decision = gate.check(
...     "ods_vehicle_trigger_event",
...     {"event_id": "EVT-1", "data_id": "COLLECT_BP_20240115143022_a1b2"},
...     channel=Channel.KAFKA,
... )
>>> decision.disposition.value in {"ACCEPTED", "ALLOW_WITH_FLAG", "REJECT"}
True

外部依赖（Flink / Paimon / StarRocks / Kafka 客户端）一律延迟 import，
连接信息取自 :func:`adas_lakehouse.config.settings`——没装客户端库也能 import 本包。
"""

from __future__ import annotations

from .alerting import (
    Alert,
    AlertRouter,
    AlertSink,
    CollectingAlertSink,
    LoggingAlertSink,
    WebhookAlertSink,
)
from .builtin import BUILTIN_RULES, default_rule_center, unregistered_tables, verify_id_patterns
from .closed_loop import ClosedLoop, RecheckResult, RepairOutcome, ReplayRepair
from .gate import BatchResult, GateDecision, QualityGate, format_quality_flag, iter_accepted
from .isolation import (
    QUALITY_ISSUE_TABLE,
    InMemoryIssueStore,
    IssueRecord,
    IssueStatus,
    IssueStore,
    JsonlIssueStore,
)
from .loader import DEFAULT_RULES_PATH, dump_yaml, load_rules, validate_config, write_rules
from .metrics import ClosedLoopMetrics, GateMetrics, MetricAlert
from .rules import CheckContext, CheckType, RepairAction, RuleHit, RuleScope, RuleSpec
from .ruleset import Rollout, RolloutMode, RuleCenter, RuleStats
from .severity import (
    LEVEL_POLICIES,
    QUALITY_FLAG_FIELD,
    Channel,
    DefenseLine,
    Disposition,
    IssueLevel,
    QualityDimension,
    QualityLayer,
    Severity,
)
from .tables import QUALITY_ISSUE, TABLES

__all__ = [
    # 分级体系
    "Severity",
    "Disposition",
    "QualityDimension",
    "QualityLayer",
    "DefenseLine",
    "Channel",
    "IssueLevel",
    "LEVEL_POLICIES",
    "QUALITY_FLAG_FIELD",
    # 规则
    "RuleSpec",
    "RuleHit",
    "CheckType",
    "CheckContext",
    "RuleScope",
    "RepairAction",
    "RuleCenter",
    "RuleStats",
    "Rollout",
    "RolloutMode",
    "BUILTIN_RULES",
    "default_rule_center",
    "verify_id_patterns",
    "unregistered_tables",
    # 配置
    "DEFAULT_RULES_PATH",
    "load_rules",
    "dump_yaml",
    "write_rules",
    "validate_config",
    # 门禁
    "QualityGate",
    "GateDecision",
    "BatchResult",
    "format_quality_flag",
    "iter_accepted",
    # 隔离
    "IssueRecord",
    "IssueStatus",
    "IssueStore",
    "InMemoryIssueStore",
    "JsonlIssueStore",
    "QUALITY_ISSUE_TABLE",
    # 告警
    "Alert",
    "AlertSink",
    "AlertRouter",
    "LoggingAlertSink",
    "CollectingAlertSink",
    "WebhookAlertSink",
    # 闭环
    "ClosedLoop",
    "RepairOutcome",
    "RecheckResult",
    "ReplayRepair",
    # 指标
    "GateMetrics",
    "MetricAlert",
    "ClosedLoopMetrics",
    # 表
    "QUALITY_ISSUE",
    "TABLES",
]

__version__ = "1.0.0"
