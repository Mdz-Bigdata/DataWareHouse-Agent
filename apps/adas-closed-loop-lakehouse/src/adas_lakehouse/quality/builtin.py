"""内置规则集：六维框架 × 三通道，全部以声明式 :class:`RuleSpec` 落地。

原文第四章：「六维框架是通用的，但三个通道的数据形态完全不同，规则侧重也完全不同」，
并给出规律——

    业务库查「流程合规」（质检、审核、防泄漏）
    事件流查「时空合理」（时间戳、截断、重复）
    文件查「物理完整」（脱敏、缺帧、同步）

每条规则的 ``source`` 字段标注它在原文的出处；原文没讲到、由本项目补齐的规则
在 ``notes`` 里以「⚠️ 原文未明确，本项目设计：」开头。

阈值一律引用 :mod:`.thresholds`，本模块不出现裸数字。
"""

from __future__ import annotations

from collections.abc import Iterable

from .rules import CheckType, RepairAction, RuleScope, RuleSpec
from .ruleset import RuleCenter
from .severity import (
    Channel,
    IssueLevel,
    Severity,
)
from .severity import (
    QualityDimension as QD,
)
from .severity import (
    QualityLayer as QL,
)
from .thresholds import (
    CLOCK_DRIFT_TOLERANCE_SECONDS,
    COLLECT_VEHICLE_SYNC_TOLERANCE_MS,
    CONTINUOUS_FRAME_LOSS_RATE_ESCALATE_THRESHOLD,
    DUPLICATE_RATE_ALERT_THRESHOLD,
    INGEST_LATENCY_ALERT_SECONDS,
    NEAR_DUPLICATE_SIMILARITY_THRESHOLD,
    PRODUCTION_VEHICLE_SYNC_TOLERANCE_MS,
    TRIGGER_POST_SECONDS_DEFAULT,
    TRIGGER_PRE_SECONDS_DEFAULT,
)

__all__ = [
    "DATA_ID_PATTERN",
    "ARTIFACT_ID_PATTERN",
    "RUN_ID_PATTERN",
    "MODALITIES",
    "VEHICLE_TYPE_COLLECT",
    "VEHICLE_TYPE_PRODUCTION",
    "VEHICLE_TYPE_VALUES",
    "BUILTIN_RULES",
    "default_rule_center",
    "verify_id_patterns",
    "unregistered_tables",
]

# --------------------------------------------------------------------------- 表名
# 原文点名的表只有两张：ods_qc_result（第二道防线质检结论回写）与
# ods_quality_issue（隔离表）。其余表名取自本项目共享契约 catalog/tables/。
# ⚠️ 原文未明确，本项目设计：带 (推断) 标记的表名是门禁规则挂载点的推断，
# 装配阶段应与 catalog.registry 对账，见 unregistered_tables()。

TBL_QC_RESULT = "ods_qc_result"  # 原文第一章「质检结论回写 ods_qc_result」
TBL_QUALITY_ISSUE = "ods_quality_issue"  # 原文第五章隔离表
TBL_DATA_FILE_META = "ods_data_file_meta"  # 共享契约（采集域参考实现）
TBL_COLLECT_TASK = "ods_collect_task"  # 共享契约
TBL_CLIP_DETAIL = "dwd_collect_clip_detail"  # 共享契约，data_id 血缘起点
TBL_TRIGGER_EVENT = "ods_vehicle_trigger_event"  # 共享契约（分区表清单）
TBL_PRODUCTION_KAFKA_EVENT = "ods_production_kafka_event"  # 共享契约（分区表清单）
TBL_ANNOTATION_RESULT = "ods_annotation_result"  # (推断) 标注结果表
# 数据集划分挂在数据集域已登记的成员表上——它本来就带 (data_id, split_type) 两列，
# 正是防泄漏与枚举校验需要的粒度；不另起一张 ods_dataset_split。
TBL_DATASET_SPLIT = "ods_dataset_data_list"  # 共享契约（数据集域成员表，含 split_type）

#: 五类模态。原文 4.3：「同一帧组内相机 / 激光雷达 / 毫米波 / IMU / GNSS 文件齐全」
MODALITIES: tuple[str, ...] = ("camera", "lidar", "radar", "imu", "gnss")

# 车型字典。原文 4.3「时间同步」把车分成两类且各配一档容差：
# 「采集车硬件同步 ≤ ±10ms，量产车软同步 ≤ ±50ms」。
# 这两个取值是 QG-OSS-004 / QG-OSS-005 的 when 前置条件，也是 QG-OSS-013 的枚举字典——
# 三条规则引用同一组常量，避免「规则 A 写 collect、规则 B 写 collector」这类静默漂移。
#: 采集车（硬件同步 ≤ ±10ms）
VEHICLE_TYPE_COLLECT = "collect"
#: 量产车（软同步 ≤ ±50ms）
VEHICLE_TYPE_PRODUCTION = "production"
#: ⚠️ 原文未明确，本项目设计：原文只给了「采集车 / 量产车」两个中文说法，
#: 列名与取值字典由本项目落地（与 catalog ods_data_file_meta.vehicle_type 的注释一致）。
VEHICLE_TYPE_VALUES: tuple[str, ...] = (VEHICLE_TYPE_COLLECT, VEHICLE_TYPE_PRODUCTION)

# --------------------------------------------------------------------------- ID 正则
# 原文第三章：「注意 data_id 的正则——它就是系列一讲过的全局三级 ID 规范
# （来源前缀_项目码_时间戳_序列号），门禁在这里做了第一道格式兜底：
# 不符合 ID 规范的数据根本没有血缘追溯的起点」。
# 下面三条正则必须与共享契约 adas_lakehouse.ids 保持一致，用 verify_id_patterns() 自检。

DATA_ID_PATTERN = r"COLLECT_[A-Z0-9]+_\d{14}_[0-9a-f]{4,}"
ARTIFACT_ID_PATTERN = (
    r"COLLECT_[A-Z0-9]+_\d{14}_[0-9a-f]{4,}"
    r"_[a-z][a-z0-9]*_v[0-9][0-9a-z.]*_[0-9a-f]{8,}"
)
RUN_ID_PATTERN = r"run_[a-z][a-z0-9]*_\d{14}_[0-9a-f]{4,}"

#: 产物状态字典（共享契约 ids.ArtifactStatus）
ARTIFACT_STATUS_VALUES: tuple[str, ...] = ("active", "superseded", "invalid")
#: 文件类型字典（共享契约 ods_data_file_meta.file_type 注释）
FILE_TYPE_VALUES: tuple[str, ...] = ("video", "pointcloud", "radar", "imu", "gps", "can")
#: ⚠️ 原文未明确，本项目设计：触发类型字典，取自 ods_vehicle_trigger_event 的分区字段语义
TRIGGER_TYPE_VALUES: tuple[str, ...] = (
    "aeb",
    "takeover",
    "cut_in",
    "hard_brake",
    "shadow_mode",
    "manual_mark",
    "corner_case",
)
#: ⚠️ 原文未明确，本项目设计：数据集划分字典
DATASET_SPLIT_VALUES: tuple[str, ...] = ("train", "val", "test")

_SRC_CH3 = "原文三、规则引擎实现（YAML 配置化 + 检查器三分支）"
_SRC_CH4_1 = "原文四、4.1 MySQL CDC · 业务库数据"
_SRC_CH4_2 = "原文四、4.2 Kafka 消息流 · 事件数据"
_SRC_CH4_3 = "原文四、4.3 OSS 采集文件"
_SRC_CH1 = "原文一、五层质量问题全景"
_SRC_CH2 = "原文二、六维质量检查框架"
_SRC_CH5 = "原文五、四档异常等级 SLA 表"


# --------------------------------------------------------------------------- 通用规则
_COMMON_RULES: tuple[RuleSpec, ...] = (
    RuleSpec(
        rule_id="QG-COM-001-data-id-not-null",
        table="*",
        field="data_id",
        check=CheckType.NOT_NULL,
        severity=Severity.ERROR,
        dimension=QD.COMPLETENESS,
        issue_level=IssueLevel.P0,
        quality_layer=QL.L5_ENGINEERING,
        message="主键 data_id 为空，无血缘追溯起点",
        repair_action=RepairAction.MANUAL_REPAIR,
        source=f"{_SRC_CH5}（P0 典型场景「主键为空」）",
    ),
    RuleSpec(
        rule_id="QG-COM-002-data-id-format",
        table="*",
        field="data_id",
        check=CheckType.REGEX,
        params={"pattern": DATA_ID_PATTERN},
        severity=Severity.ERROR,
        dimension=QD.VALIDITY,
        issue_level=IssueLevel.P1,
        quality_layer=QL.L5_ENGINEERING,
        message="data_id 不符合全局三级 ID 规范（来源前缀_项目码_时间戳_序列号）",
        repair_action=RepairAction.MANUAL_REPAIR,
        source=f"{_SRC_CH3}；{_SRC_CH5}（P1 典型场景「ID 格式非法」）",
    ),
    RuleSpec(
        rule_id="QG-COM-003-artifact-id-format",
        table="*",
        field="artifact_id",
        check=CheckType.REGEX,
        params={"pattern": ARTIFACT_ID_PATTERN, "allow_null": True},
        severity=Severity.ERROR,
        dimension=QD.VALIDITY,
        issue_level=IssueLevel.P1,
        quality_layer=QL.L5_ENGINEERING,
        message="artifact_id 不符合二级 ID 规范（{data_id}_{stage}_{algo_version}_{content_hash}）",
        repair_action=RepairAction.MANUAL_REPAIR,
        source=f"{_SRC_CH3}（ID 格式兜底）",
    ),
    RuleSpec(
        rule_id="QG-COM-004-run-id-format",
        table="*",
        field="run_id",
        check=CheckType.REGEX,
        params={"pattern": RUN_ID_PATTERN, "allow_null": True},
        severity=Severity.WARNING,
        dimension=QD.VALIDITY,
        issue_level=IssueLevel.P3,
        quality_layer=QL.L5_ENGINEERING,
        message="run_id 不符合三级 ID 规范（run_{stage}_{时间戳}_{序列号}）",
        repair_action=RepairAction.AUTO_REPAIR,
        source=f"{_SRC_CH3}（ID 格式兜底）",
    ),
    RuleSpec(
        rule_id="QG-COM-005-artifact-status-enum",
        table="*",
        field="artifact_status",
        check=CheckType.ENUM,
        params={"values": list(ARTIFACT_STATUS_VALUES), "allow_null": True},
        severity=Severity.WARNING,
        dimension=QD.VALIDITY,
        issue_level=IssueLevel.P2,
        message="artifact_status 不在字典 active/superseded/invalid 内",
        source=f"{_SRC_CH2}（有效性：枚举值在字典内）",
    ),
    RuleSpec(
        rule_id="QG-COM-006-schema-parsable",
        table="*",
        field="_raw_payload",
        check=CheckType.SCHEMA_PARSABLE,
        params={"allow_null": True},
        severity=Severity.ERROR,
        dimension=QD.VALIDITY,
        issue_level=IssueLevel.P0,
        message="原始报文 Schema 不可解析",
        repair_action=RepairAction.DISCARD_ARCHIVE,
        source=f"{_SRC_CH5}（P0 典型场景「Schema 不可解析」）",
        notes="allow_null=True：只有携带原始报文的通道（Kafka/OSS 元信息）才做这项检查",
    ),
    RuleSpec(
        rule_id="QG-COM-007-lineage-parent-exists",
        table="*",
        field="parent_artifact_id",
        check=CheckType.REFERENCE_EXISTS,
        params={
            "target_table": TBL_CLIP_DETAIL,
            "target_field": "artifact_id",
            "allow_null": True,
        },
        severity=Severity.WARNING,
        dimension=QD.CONSISTENCY,
        issue_level=IssueLevel.P2,
        quality_layer=QL.L5_ENGINEERING,
        message="父产物血缘缺失，图库对账兜底字段 parent_artifact_id 找不到对应产物",
        source=f"{_SRC_CH1}（L5 门禁动作「血缘缺失告警」）",
    ),
    RuleSpec(
        rule_id="QG-COM-008-clip-anchor-exists",
        table="*",
        field="data_id",
        check=CheckType.REFERENCE_EXISTS,
        params={"target_table": TBL_CLIP_DETAIL, "target_field": "data_id"},
        severity=Severity.WARNING,
        dimension=QD.CONSISTENCY,
        issue_level=IssueLevel.P2,
        message="跨源关联缺失：data_id 在 clip 明细表中不存在",
        source=f"{_SRC_CH2}（一致性：跨源关联存在性 → 告警标记，允许入湖）",
    ),
    RuleSpec(
        rule_id="QG-COM-009-ingest-latency",
        table="*",
        field="event_time",
        check=CheckType.FRESHNESS,
        params={
            "max_delay_seconds": INGEST_LATENCY_ALERT_SECONDS,
            "allow_null": True,
        },
        severity=Severity.WARNING,
        dimension=QD.TIMELINESS,
        issue_level=IssueLevel.P3,
        message="入湖延迟超阈值",
        repair_action=RepairAction.AUTO_REPAIR,
        source=f"{_SRC_CH2}（及时性：监控告警，不拦截）；{_SRC_CH5}（P3 典型场景「入湖延迟」）",
        notes="⚠️ 原文未明确，本项目设计：原文未给入湖延迟阈值，默认值见 thresholds",
    ),
)

# --------------------------------------------------------------------------- 4.1 MySQL CDC
_CDC_RULES: tuple[RuleSpec, ...] = (
    RuleSpec(
        rule_id="QG-CDC-001-annotation-qc-linked",
        table=TBL_ANNOTATION_RESULT,
        field="qc_result_id",
        check=CheckType.REFERENCE_EXISTS,
        params={"target_table": TBL_QC_RESULT, "target_field": "qc_result_id"},
        severity=Severity.ERROR,
        dimension=QD.CONSISTENCY,
        issue_level=IssueLevel.P1,
        channel=Channel.MYSQL_CDC,
        quality_layer=QL.L3_ANNOTATION,
        message="标注质量准入：标注结果未关联质检结论，质检缺失拒绝入湖",
        repair_action=RepairAction.MANUAL_REPAIR,
        source=f"{_SRC_CH4_1}（标注质量准入）",
    ),
    RuleSpec(
        rule_id="QG-CDC-002-annotation-qc-passed",
        table=TBL_ANNOTATION_RESULT,
        field="qc_conclusion",
        check=CheckType.ENUM,
        params={"values": ["passed"], "case_insensitive": True},
        severity=Severity.ERROR,
        dimension=QD.VALIDITY,
        issue_level=IssueLevel.P1,
        channel=Channel.MYSQL_CDC,
        quality_layer=QL.L3_ANNOTATION,
        message="标注质量准入：质检结论不为通过，未过质检不入湖",
        repair_action=RepairAction.MANUAL_REPAIR,
        source=f"{_SRC_CH4_1}（标注质量准入）；{_SRC_CH1}（L3 门禁动作「未过质检不入湖」）",
    ),
    RuleSpec(
        rule_id="QG-CDC-003-auto-label-human-reviewed",
        table=TBL_ANNOTATION_RESULT,
        field="human_review_status",
        check=CheckType.ENUM,
        params={"values": ["reviewed", "approved"], "case_insensitive": True},
        when={"field": "annotation_source", "in": ["auto_label", "pretrain_model", "llm_prelabel"]},
        severity=Severity.ERROR,
        dimension=QD.VALIDITY,
        issue_level=IssueLevel.P1,
        channel=Channel.MYSQL_CDC,
        quality_layer=QL.L3_ANNOTATION,
        message="自动标注准入：预标注（大模型/离线模型）结果未经人工审核，不得入训练数据集",
        repair_action=RepairAction.MANUAL_REPAIR,
        source=f"{_SRC_CH4_1}（自动标注准入）",
        notes="⚠️ 原文未明确，本项目设计：annotation_source 的取值字典由本项目枚举",
    ),
    RuleSpec(
        rule_id="QG-CDC-004-dataset-split-leakage",
        table=TBL_DATASET_SPLIT,
        field="split_type",
        check=CheckType.DISJOINT_SPLIT,
        params={"group_field": "data_id"},
        severity=Severity.ERROR,
        dimension=QD.CONSISTENCY,
        issue_level=IssueLevel.P1,
        channel=Channel.MYSQL_CDC,
        quality_layer=QL.L5_ENGINEERING,
        message="数据集划分防泄漏：训练/验证/测试集按 clip 级划分，相邻帧不得跨集",
        repair_action=RepairAction.MANUAL_REPAIR,
        source=f"{_SRC_CH4_1}（数据集划分防泄漏，跨集拒绝 L5）；{_SRC_CH5}（P1「跨集泄漏」）",
    ),
    RuleSpec(
        rule_id="QG-CDC-005-dataset-split-enum",
        table=TBL_DATASET_SPLIT,
        field="split_type",
        check=CheckType.ENUM,
        params={"values": list(DATASET_SPLIT_VALUES)},
        severity=Severity.ERROR,
        dimension=QD.VALIDITY,
        issue_level=IssueLevel.P1,
        channel=Channel.MYSQL_CDC,
        quality_layer=QL.L5_ENGINEERING,
        message="数据集划分取值不在字典 train/val/test 内",
        source=f"{_SRC_CH2}（有效性：枚举值在字典内）",
        notes="⚠️ 原文未明确，本项目设计：划分字典由本项目给定",
    ),
    RuleSpec(
        rule_id="QG-CDC-006-annotation-status-transition",
        table=TBL_ANNOTATION_RESULT,
        field="task_status",
        check=CheckType.STATUS_TRANSITION,
        params={
            "from_field": "prev_task_status",
            "transitions": {
                "created": ["annotating", "cancelled"],
                "annotating": ["qc_pending", "cancelled"],
                "qc_pending": ["qc_passed", "qc_rejected"],
                "qc_rejected": ["annotating", "cancelled"],
                "qc_passed": ["delivered"],
                "delivered": [],
                "cancelled": [],
            },
        },
        severity=Severity.WARNING,
        dimension=QD.VALIDITY,
        issue_level=IssueLevel.P2,
        channel=Channel.MYSQL_CDC,
        quality_layer=QL.L3_ANNOTATION,
        message="标注任务状态流转非法",
        source=f"{_SRC_CH2}（有效性：状态流转合法）；{_SRC_CH5}（P2「状态流转异常」）",
        notes="⚠️ 原文未明确，本项目设计：状态机取值与迁移表由本项目给定",
    ),
    RuleSpec(
        rule_id="QG-CDC-007-cdc-sync-latency",
        table="*",
        field="update_time",
        check=CheckType.FRESHNESS,
        params={"max_delay_seconds": INGEST_LATENCY_ALERT_SECONDS, "allow_null": True},
        severity=Severity.WARNING,
        dimension=QD.TIMELINESS,
        issue_level=IssueLevel.P3,
        channel=Channel.MYSQL_CDC,
        message="CDC 同步延迟超阈值",
        repair_action=RepairAction.AUTO_REPAIR,
        source=f"{_SRC_CH2}（及时性：CDC 同步延迟）",
        notes="⚠️ 原文未明确，本项目设计：原文未给 CDC 延迟阈值，复用入湖延迟默认值",
    ),
)

# --------------------------------------------------------------------------- 4.2 Kafka
_KAFKA_RULES: tuple[RuleSpec, ...] = (
    RuleSpec(
        rule_id="QG-KFK-001-event-id-not-null",
        table=TBL_TRIGGER_EVENT,
        field="event_id",
        check=CheckType.NOT_NULL,
        severity=Severity.ERROR,
        dimension=QD.COMPLETENESS,
        issue_level=IssueLevel.P0,
        channel=Channel.KAFKA,
        quality_layer=QL.L2_FLEET_RETURN,
        message="事件主键 event_id 为空",
        repair_action=RepairAction.DISCARD_ARCHIVE,
        source=f"{_SRC_CH5}（P0 典型场景「主键为空」）",
    ),
    RuleSpec(
        rule_id="QG-KFK-002-event-time-window",
        table=TBL_TRIGGER_EVENT,
        # 回传域该表的时间列，catalog 登记名是 trigger_time（不是 event_time）
        field="trigger_time",
        check=CheckType.TIMESTAMP_WINDOW,
        params={
            "not_before_field": "vehicle_manufacture_time",
            "future_tolerance_seconds": CLOCK_DRIFT_TOLERANCE_SECONDS,
        },
        severity=Severity.WARNING,
        dimension=QD.ACCURACY,
        issue_level=IssueLevel.P2,
        channel=Channel.KAFKA,
        quality_layer=QL.L2_FLEET_RETURN,
        message="时间戳不合理：早于车辆出厂时间或晚于服务器时间 + 容忍窗口（车端时钟漂移）",
        repair_action=RepairAction.AUTO_REPAIR,
        source=f"{_SRC_CH4_2}（时间戳合理性 → 告警标记放行）",
        notes="⚠️ 原文未明确，本项目设计：容忍窗口大小原文未给，默认见 thresholds",
    ),
    RuleSpec(
        rule_id="QG-KFK-003-clip-truncated-pre",
        table=TBL_TRIGGER_EVENT,
        field="pre_trigger_seconds",
        check=CheckType.MIN_VALUE,
        params={"min": TRIGGER_PRE_SECONDS_DEFAULT},
        severity=Severity.WARNING,
        dimension=QD.COMPLETENESS,
        issue_level=IssueLevel.P2,
        channel=Channel.KAFKA,
        quality_layer=QL.L2_FLEET_RETURN,
        message="片段截断：回传片段覆盖触发前不足 N 秒，标记 truncated_flag",
        repair_action=RepairAction.AUTO_REPAIR,
        source=f"{_SRC_CH4_2}（片段截断 → 告警标记放行）",
        notes="⚠️ 原文未明确，本项目设计：原文写「触发前 ≥ N 秒」，N 是占位符，默认值见 thresholds",
    ),
    RuleSpec(
        rule_id="QG-KFK-004-clip-truncated-post",
        table=TBL_TRIGGER_EVENT,
        field="post_trigger_seconds",
        check=CheckType.MIN_VALUE,
        params={"min": TRIGGER_POST_SECONDS_DEFAULT},
        severity=Severity.WARNING,
        dimension=QD.COMPLETENESS,
        issue_level=IssueLevel.P2,
        channel=Channel.KAFKA,
        quality_layer=QL.L2_FLEET_RETURN,
        message="片段截断：回传片段覆盖触发后不足 M 秒，标记 truncated_flag",
        repair_action=RepairAction.AUTO_REPAIR,
        source=f"{_SRC_CH4_2}（片段截断 → 告警标记放行）",
        notes="⚠️ 原文未明确，本项目设计：原文写「触发后 ≥ M 秒」，M 是占位符，默认值见 thresholds",
    ),
    RuleSpec(
        rule_id="QG-KFK-005-event-id-dedup",
        table=TBL_TRIGGER_EVENT,
        field="event_id",
        check=CheckType.UNIQUE_KEY,
        params={"allow_null": True},
        severity=Severity.WARNING,
        dimension=QD.UNIQUENESS,
        issue_level=IssueLevel.P3,
        channel=Channel.KAFKA,
        quality_layer=QL.L2_FLEET_RETURN,
        message="事件 ID 重复（物理去重由 Paimon 主键 Upsert 幂等保证，此处只做观测）",
        repair_action=RepairAction.AUTO_REPAIR,
        source=f"{_SRC_CH4_2}（重复率监控 → 自动去重 + 超限告警）",
    ),
    RuleSpec(
        rule_id="QG-KFK-006-duplicate-rate",
        table=TBL_TRIGGER_EVENT,
        field="duplicate_rate",
        check=CheckType.RATIO_MAX,
        params={"max_ratio": DUPLICATE_RATE_ALERT_THRESHOLD},
        scope=RuleScope.BATCH,
        severity=Severity.WARNING,
        dimension=QD.UNIQUENESS,
        issue_level=IssueLevel.P3,
        channel=Channel.KAFKA,
        quality_layer=QL.L2_FLEET_RETURN,
        message="事件重复率 > 5% 告警",
        repair_action=RepairAction.AUTO_REPAIR,
        source=f"{_SRC_CH4_2}（重复率监控：重复率 > 5% 告警）；{_SRC_CH5}（P3「重复率波动」）",
    ),
    RuleSpec(
        rule_id="QG-KFK-007-trigger-type-enum",
        table=TBL_TRIGGER_EVENT,
        field="trigger_type",
        check=CheckType.ENUM,
        params={"values": list(TRIGGER_TYPE_VALUES)},
        severity=Severity.ERROR,
        dimension=QD.VALIDITY,
        issue_level=IssueLevel.P1,
        channel=Channel.KAFKA,
        message="trigger_type 不在字典内（该字段是 ods_vehicle_trigger_event 的分区字段，"
        "非法值会产生脏分区）",
        repair_action=RepairAction.MANUAL_REPAIR,
        source=f"{_SRC_CH2}（有效性：枚举值在字典内）",
        notes="⚠️ 原文未明确，本项目设计：触发类型字典由本项目枚举",
    ),
    RuleSpec(
        rule_id="QG-KFK-008-gps-jump",
        table=TBL_TRIGGER_EVENT,
        field="gps_jump_meters",
        check=CheckType.RANGE,
        params={"min": 0, "max": 100, "allow_null": True},
        severity=Severity.WARNING,
        dimension=QD.ACCURACY,
        issue_level=IssueLevel.P2,
        channel=Channel.KAFKA,
        quality_layer=QL.L1_SENSOR,
        message="定位跳变超限",
        source=f"{_SRC_CH2}（准确性：定位无跳变）；{_SRC_CH5}（P2「定位跳变」）",
        notes="⚠️ 原文未明确，本项目设计：原文只说「定位无跳变」未给米数，"
        "本项目取相邻采样点跳变 100 米为上限",
    ),
    RuleSpec(
        rule_id="QG-KFK-009-production-event-id-not-null",
        table=TBL_PRODUCTION_KAFKA_EVENT,
        field="event_id",
        check=CheckType.NOT_NULL,
        severity=Severity.ERROR,
        dimension=QD.COMPLETENESS,
        issue_level=IssueLevel.P0,
        channel=Channel.KAFKA,
        message="产线事件主键 event_id 为空",
        repair_action=RepairAction.DISCARD_ARCHIVE,
        source=f"{_SRC_CH5}（P0 典型场景「主键为空」）",
    ),
)

# --------------------------------------------------------------------------- 4.3 OSS
_OSS_RULES: tuple[RuleSpec, ...] = (
    RuleSpec(
        rule_id="QG-OSS-001-desensitization-flags",
        table=TBL_DATA_FILE_META,
        check=CheckType.REQUIRED_FLAGS,
        params={
            "flags": ["vehicle_desensitized_flag", "cloud_compliance_decrypted_flag"],
        },
        severity=Severity.ERROR,
        dimension=QD.COMPLETENESS,
        issue_level=IssueLevel.P0,
        channel=Channel.OSS_FILE,
        quality_layer=QL.L2_FLEET_RETURN,
        message="脱敏标记合规：必须携带「车端脱敏 + 合规云脱密」双合规标记，缺失即合规风险",
        repair_action=RepairAction.MANUAL_REPAIR,
        source=f"{_SRC_CH4_3}（脱敏标记合规 → P0 拒绝入湖）；{_SRC_CH5}（P0「脱敏标记缺失」）",
    ),
    RuleSpec(
        rule_id="QG-OSS-002-multimodal-completeness",
        table=TBL_DATA_FILE_META,
        field="frame_group_modalities",
        check=CheckType.REQUIRED_MEMBERS,
        params={"members": list(MODALITIES)},
        severity=Severity.ERROR,
        dimension=QD.COMPLETENESS,
        issue_level=IssueLevel.P0,
        channel=Channel.OSS_FILE,
        quality_layer=QL.L1_SENSOR,
        message="多模态完整性：同一帧组内相机/激光雷达/毫米波/IMU/GNSS 文件不齐全，缺帧拒绝",
        repair_action=RepairAction.AUTO_REPAIR,
        source=f"{_SRC_CH4_3}（多模态完整性，缺帧拒绝 L1）；{_SRC_CH5}（P0「多模态整帧缺失」）",
        notes="整帧完整性 = 五个模态一个都不能少：缺任意一个模态与整帧缺失（字段为空）"
        "同判 P0 硬拒绝，不设 allow_null——「帧组模态清单为空」本身就是原文 P0 典型场景"
        "「多模态整帧缺失」。丢帧的**比例**是另一回事，走 QG-OSS-003（连续丢帧率 > 1%）"
        "与 QG-OSS-012（批次缺片），那两条是带标记放行。",
    ),
    RuleSpec(
        rule_id="QG-OSS-003-continuous-frame-loss-rate",
        table=TBL_DATA_FILE_META,
        field="continuous_frame_loss_rate",
        check=CheckType.RATIO_MAX,
        params={"max_ratio": CONTINUOUS_FRAME_LOSS_RATE_ESCALATE_THRESHOLD, "allow_null": True},
        severity=Severity.WARNING,
        dimension=QD.COMPLETENESS,
        issue_level=IssueLevel.P2,
        channel=Channel.OSS_FILE,
        quality_layer=QL.L1_SENSOR,
        message="连续丢帧率 > 1%，升级告警",
        repair_action=RepairAction.AUTO_REPAIR,
        source=f"{_SRC_CH4_3}（多模态完整性：连续丢帧率 > 1% 升级告警）",
        notes="原文写「升级告警」：本项目落地为异常等级由 P3 升一档到 P2，仍属带标记放行",
    ),
    RuleSpec(
        rule_id="QG-OSS-004-time-sync-collect-vehicle",
        table=TBL_DATA_FILE_META,
        field="time_sync_error_ms",
        check=CheckType.ABS_MAX,
        params={"max_abs": COLLECT_VEHICLE_SYNC_TOLERANCE_MS},
        when={"field": "vehicle_type", "equals": VEHICLE_TYPE_COLLECT},
        severity=Severity.WARNING,
        dimension=QD.ACCURACY,
        issue_level=IssueLevel.P2,
        channel=Channel.OSS_FILE,
        quality_layer=QL.L1_SENSOR,
        message="采集车硬件同步误差超 ±10ms，超限标记",
        source=f"{_SRC_CH4_3}（时间同步：采集车硬件同步 ≤ ±10ms → 超限告警放行 L1）",
        notes="判定用「绝对值严格大于」：原文写 ≤ ±10ms，误差恰为 ±10ms 通过、±10.1ms 命中。"
        "处置取 WARNING/P2 而非硬拒绝——原文三处说法不一致，按「就近且两处互证」取："
        "4.3 该行处置列写「超限告警放行（L1）」，第五章 SLA 表把「时间同步超限」列在 P2 一般；"
        "第一章 L1 行的「同步超限硬拒绝」是层级级别的粗描述，不采纳。",
    ),
    RuleSpec(
        rule_id="QG-OSS-005-time-sync-production-vehicle",
        table=TBL_DATA_FILE_META,
        field="time_sync_error_ms",
        check=CheckType.ABS_MAX,
        params={"max_abs": PRODUCTION_VEHICLE_SYNC_TOLERANCE_MS},
        when={"field": "vehicle_type", "equals": VEHICLE_TYPE_PRODUCTION},
        severity=Severity.WARNING,
        dimension=QD.ACCURACY,
        issue_level=IssueLevel.P2,
        channel=Channel.OSS_FILE,
        quality_layer=QL.L1_SENSOR,
        message="量产车软同步误差超 ±50ms，超限标记",
        source=f"{_SRC_CH4_3}（时间同步：量产车软同步 ≤ ±50ms → 超限告警放行 L1）",
        notes="量产车是软同步，容差比采集车宽 5 倍（±50ms vs ±10ms），两档不可互换；"
        "处置口径与 QG-OSS-004 同源，见该条 notes。",
    ),
    RuleSpec(
        rule_id="QG-OSS-013-vehicle-type-decidable",
        table=TBL_DATA_FILE_META,
        field="vehicle_type",
        check=CheckType.ENUM,
        params={"values": list(VEHICLE_TYPE_VALUES)},
        when={"field": "time_sync_error_ms", "exists": True},
        severity=Severity.WARNING,
        dimension=QD.VALIDITY,
        issue_level=IssueLevel.P2,
        channel=Channel.OSS_FILE,
        quality_layer=QL.L1_SENSOR,
        message="车型无法判定（vehicle_type 不在字典 collect/production 内或缺失），"
        "±10ms / ±50ms 两档同步容差都选不出来，该记录的时间同步检查被跳过",
        repair_action=RepairAction.MANUAL_REPAIR,
        source=f"{_SRC_CH4_3}（时间同步：分车型两档容差）；{_SRC_CH2}（有效性：枚举值在字典内）",
        notes="⚠️ 原文未明确，本项目设计：原文只给了两类车各自的容差，没说车型判不出来怎么办。"
        "不补这条的话，vehicle_type 缺失或写错的记录会让 QG-OSS-004 与 QG-OSS-005 的 when "
        "同时落空，5000ms 的同步误差也会一路 ACCEPTED——正是「规则挂在判据列上却静默失效」"
        "那一类问题。这里不替它选一档容差（那等于替原文编阈值），只把「判不出车型」本身"
        "标成 P2 带标记放行，让下游能筛掉这批同步状态不明的数据。",
    ),
    RuleSpec(
        rule_id="QG-OSS-006-file-decodable",
        table=TBL_DATA_FILE_META,
        field="object_key",
        check=CheckType.DECODABLE,
        params={"flag_field": "decodable_flag", "algorithm": "md5"},
        severity=Severity.ERROR,
        dimension=QD.VALIDITY,
        issue_level=IssueLevel.P1,
        channel=Channel.OSS_FILE,
        quality_layer=QL.L2_FLEET_RETURN,
        message="文件损坏不可解码（压缩损伤 / 传输截断）",
        repair_action=RepairAction.AUTO_REPAIR,
        source=f"{_SRC_CH2}（有效性：文件可解码）；{_SRC_CH5}（P1「文件损坏不可解码」）",
    ),
    RuleSpec(
        rule_id="QG-OSS-007-file-not-empty",
        table=TBL_DATA_FILE_META,
        field="file_size_bytes",
        check=CheckType.RANGE,
        params={"min": 1},
        severity=Severity.ERROR,
        dimension=QD.COMPLETENESS,
        issue_level=IssueLevel.P1,
        channel=Channel.OSS_FILE,
        quality_layer=QL.L1_SENSOR,
        message="空帧/空文件拒绝入湖",
        repair_action=RepairAction.AUTO_REPAIR,
        source=f"{_SRC_CH1}（L1 门禁动作「空帧拒绝」）",
    ),
    RuleSpec(
        rule_id="QG-OSS-008-file-type-enum",
        table=TBL_DATA_FILE_META,
        field="file_type",
        check=CheckType.ENUM,
        params={"values": list(FILE_TYPE_VALUES)},
        severity=Severity.ERROR,
        dimension=QD.VALIDITY,
        issue_level=IssueLevel.P1,
        channel=Channel.OSS_FILE,
        message="file_type 不在字典内（该字段是 ods_data_file_meta 的分区字段）",
        repair_action=RepairAction.MANUAL_REPAIR,
        source=f"{_SRC_CH2}（有效性：枚举值在字典内）",
        notes="字典取自共享契约 catalog/tables/_collect.py 的 file_type 注释",
    ),
    RuleSpec(
        rule_id="QG-OSS-009-metadata-required",
        table=TBL_DATA_FILE_META,
        field="object_key",
        check=CheckType.NOT_NULL,
        severity=Severity.ERROR,
        dimension=QD.COMPLETENESS,
        issue_level=IssueLevel.P1,
        channel=Channel.OSS_FILE,
        quality_layer=QL.L2_FLEET_RETURN,
        message="关键元数据缺失：对象存储 key 为空",
        repair_action=RepairAction.MANUAL_REPAIR,
        source=f"{_SRC_CH1}（L2 门禁动作「损坏文件与关键元数据缺失拒绝」）",
    ),
    RuleSpec(
        rule_id="QG-OSS-010-meta-file-consistency",
        table=TBL_DATA_FILE_META,
        field="checksum_md5",
        check=CheckType.CUSTOM,
        params={"name": "meta_matches_object"},
        severity=Severity.WARNING,
        dimension=QD.CONSISTENCY,
        issue_level=IssueLevel.P2,
        channel=Channel.OSS_FILE,
        quality_layer=QL.L2_FLEET_RETURN,
        message="元信息与文件本体不一致（大小 / 校验和对不上）",
        repair_action=RepairAction.AUTO_REPAIR,
        source=f"{_SRC_CH2}（一致性：元信息与文件本体一致）",
        notes="谓词 meta_matches_object 由入湖作业注入（HEAD 对象拿 size/etag 比对）；"
        "未注入时该检查自动跳过，不阻塞主链路",
    ),
    RuleSpec(
        rule_id="QG-OSS-011-near-duplicate",
        table=TBL_DATA_FILE_META,
        field="near_duplicate_similarity",
        check=CheckType.SIMILARITY_MAX,
        params={
            "max_similarity": NEAR_DUPLICATE_SIMILARITY_THRESHOLD,
            "allow_null": True,
        },
        severity=Severity.WARNING,
        dimension=QD.UNIQUENESS,
        issue_level=IssueLevel.P3,
        channel=Channel.OSS_FILE,
        quality_layer=QL.L2_FLEET_RETURN,
        message="近重复场景，标记抑制",
        repair_action=RepairAction.DISCARD_ARCHIVE,
        source=f"{_SRC_CH1}（L2「近重复抑制」/ L5「近重复」）；{_SRC_CH2}（唯一性：近重复场景标记）",
        notes="⚠️ 原文未明确，本项目设计：相似度阈值原文未给，默认见 thresholds",
    ),
    RuleSpec(
        rule_id="QG-OSS-012-batch-frame-loss",
        table=TBL_DATA_FILE_META,
        field="batch_missing_ratio",
        check=CheckType.RATIO_MAX,
        params={"max_ratio": CONTINUOUS_FRAME_LOSS_RATE_ESCALATE_THRESHOLD},
        scope=RuleScope.BATCH,
        severity=Severity.WARNING,
        dimension=QD.TIMELINESS,
        issue_level=IssueLevel.P3,
        channel=Channel.OSS_FILE,
        quality_layer=QL.L2_FLEET_RETURN,
        message="批次到达完整性：批次轻微缺片",
        repair_action=RepairAction.AUTO_REPAIR,
        source=f"{_SRC_CH2}（及时性：批次到达完整性）；{_SRC_CH5}（P3「批次轻微缺片」）",
        notes="⚠️ 原文未明确，本项目设计：缺片比例阈值复用连续丢帧率的 1%",
    ),
)


#: 内置规则全集：通用 9 条 + CDC 7 条 + Kafka 9 条 + OSS 13 条。
BUILTIN_RULES: tuple[RuleSpec, ...] = _COMMON_RULES + _CDC_RULES + _KAFKA_RULES + _OSS_RULES


def default_rule_center(extra: Iterable[RuleSpec] = ()) -> RuleCenter:
    """构造装好内置规则的规则中心。

    :param extra: 追加的项目级规则（会与内置规则做 rule_id 唯一性校验）
    """
    center = RuleCenter(BUILTIN_RULES)
    center.register_many(extra)
    return center


def verify_id_patterns() -> dict[str, bool]:
    """自检：本模块的三条 ID 正则与共享契约 :mod:`adas_lakehouse.ids` 是否仍然一致。

    共享契约里的正则是私有的，这里不直接引用，而是拿契约生成的真实 ID 回测，
    避免两边悄悄漂移。返回 ``{"data_id": True, ...}``，全 True 才算对齐。
    """
    import re
    from datetime import datetime

    from ..ids import derive_artifact_id, new_data_id, new_run_id

    moment = datetime(2024, 1, 15, 14, 30, 22)
    did = new_data_id("BP", moment)
    aid = derive_artifact_id(did, "mining", "v3", b"payload")
    rid = new_run_id("mining", moment)
    return {
        "data_id": re.fullmatch(DATA_ID_PATTERN, str(did)) is not None,
        "artifact_id": re.fullmatch(ARTIFACT_ID_PATTERN, str(aid)) is not None,
        "run_id": re.fullmatch(RUN_ID_PATTERN, str(rid)) is not None,
    }


def unregistered_tables(center: RuleCenter | None = None) -> list[str]:
    """列出规则里引用、但尚未登记进 :mod:`adas_lakehouse.catalog.registry` 的表名。

    装配阶段用它对账（本模块推断的表名见文件头「表名」小节）。
    catalog 尚未装配完成时返回的清单会偏长，这是预期行为，不是错误。
    """
    from ..catalog import registry

    known = {t.name for t in registry.all_tables()} | {TBL_QUALITY_ISSUE}
    rules = center or default_rule_center()
    return sorted({r.table for r in rules if r.table != "*" and r.table not in known})
