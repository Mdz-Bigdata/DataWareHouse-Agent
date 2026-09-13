"""三通道入湖 + 合规入湖链路。

来源：[a5]《湖仓架构全景》第六章「数据进得来：三通道统一入湖」 +
[a8]《采集数据的合规入湖链路》全文。

| 通道 | 承接数据 | 入湖方式 | 实现 |
|---|---|---|---|
| Flink CDC | 各平台 MySQL 业务库（产线 / 标注 / 训练等） | 读 binlog 实时同步 | ``CdcChannel`` |
| Kafka | 事件流（产线埋点 / 训练指标 / 车端触发） | Flink 实时消费 | ``KafkaChannel`` |
| OSS 合规上传 | 采集大文件（图像 / 点云 / 传感器数据） | 文件本体存 OSS · 元信息经 Kafka 入湖 | ``OssFileChannel`` |

三条通道共用同一套不变式：统一门禁、统一系统字段（``_ingest_time`` + ``_source_system``）、
统一五步异常闭环——「通道可以分，门禁不能分」。

模块地图::

    constants.py       原文数字逐字登记（阈值 / 比例 / TTL / 成本）
    compliance.py      五步合规链路状态机 + 双脱敏标记 + 合规云架构约束
    oss.py             文件元信息、对象存储探针、路径 / 校验和 / 可解码性校验
    gate.py            OSS 通道四项专属门禁（2×P0 + 2×P1）+ 五步异常闭环
    quality_bridge.py  把 quality 子系统的通用六维门禁挂进三条通道（门禁不能分）
    channels.py        三通道统一抽象与默认绑定
    sinks.py       ODS 写出口（内存 / JSONL / Flink SQL Gateway）
    sql.py         Flink SQL 与 StarRocks DDL 生成（写入 flink/sql/ 与 ddl/）
    pipeline.py    端到端编排：五步链路 → 门禁 → 落 ods_data_file_meta
    rows.py        系统字段盖章与契约表投影
    errors.py      异常类型

快速上手::

    from adas_lakehouse.ingest import (
        ComplianceIngestPipeline, ComplianceCloudTopology, ComplianceMarks,
        FileMeta, FileType, InMemoryOdsSink,
    )

    pipeline = ComplianceIngestPipeline(
        topology=ComplianceCloudTopology(
            compliance_cloud_region="cn-shanghai", adas_cloud_region="cn-shanghai",
            compliance_cloud_vpc_id="vpc-adas-01", adas_cloud_vpc_id="vpc-adas-01",
        ),
        sink=InMemoryOdsSink(),
    )
    outcome = pipeline.submit(
        FileMeta(
            file_id="F-0001", file_type=FileType.POINTCLOUD,
            data_id="COLLECT_BP_20260301123045_b7e2",
            object_key="collect/2026/03/01/F-0001.pcd",
            file_size_bytes=41_943_040,
            checksum_md5="0" * 32,
            marks=ComplianceMarks.both_applied(
                vehicle_operator="采集软件 v2.3", cloud_operator="XX 合规科技",
            ),
        ),
    )

所有外部客户端库（boto3 / kafka-python）均延迟 import，裸环境下
``import adas_lakehouse.ingest`` 不会失败。
"""

from __future__ import annotations

from . import constants
from .channels import (
    DEFAULT_CDC_BINDINGS,
    CdcBinding,
    CdcChannel,
    CdcPhase,
    CdcSourceConfig,
    ChannelKind,
    FileMetaBinding,
    IngestChannel,
    IngestReport,
    KafkaBinding,
    KafkaChannel,
    OssFileChannel,
    build_default_channels,
    default_file_meta_binding,
    default_kafka_bindings,
    unified_ingest,
)
from .compliance import (
    CONTRACT_REDACTION_FLAGS,
    DISTRIBUTION_PAYLOADS,
    REDACTION_DEFINITIONS,
    STEP_DEFINITIONS,
    ComplianceChain,
    ComplianceCloudTopology,
    ComplianceMarks,
    ComplianceSite,
    ComplianceStep,
    CrossBoundaryPayload,
    RedactionMark,
    RedactionStage,
    assert_may_cross_boundary,
)
from .errors import (
    ChannelError,
    ComplianceViolation,
    GateRejected,
    IngestError,
    MissingDependency,
    SinkError,
)
from .gate import (
    OSS_CHANNEL_CHECKS,
    QUALITY_DIMENSIONS,
    QUALITY_ISSUE_TABLE,
    AnomalyClosedLoop,
    AnomalyRecord,
    AnomalyStep,
    CheckResult,
    CheckStatus,
    Decision,
    GateCheck,
    GateOutcome,
    OssComplianceGate,
    Severity,
    decide,
    merge_outcomes,
)
from .oss import (
    FILE_META_TABLE,
    FileMeta,
    FileType,
    InMemoryObjectStore,
    NullObjectStore,
    ObjectStat,
    ObjectStore,
    S3CompatibleObjectStore,
    StorageClass,
    md5_of,
    normalize_file_type,
    parse_object_uri,
    probe_decodable,
    validate_data_id,
    validate_object_key,
    verify_checksum,
)
from .pipeline import ComplianceIngestPipeline, IngestOutcome
from .quality_bridge import unified_gate_hook
from .rows import dropped_fields, project_to_table, stamp_system_fields
from .sinks import (
    CompositeOdsSink,
    FlinkSqlGatewayClient,
    FlinkSqlGatewaySink,
    InMemoryOdsSink,
    JsonlOdsSink,
    OdsSink,
    render_insert,
)
from .sql import (
    render_catalog_script,
    render_cdc_pipeline,
    render_file_meta_pipeline,
    render_kafka_pipeline,
    render_starrocks_ingest,
    write_sql_files,
)

__all__ = [
    # 通道
    "ChannelKind",
    "CdcPhase",
    "IngestChannel",
    "IngestReport",
    "CdcBinding",
    "CdcSourceConfig",
    "CdcChannel",
    "KafkaBinding",
    "KafkaChannel",
    "FileMetaBinding",
    "OssFileChannel",
    "DEFAULT_CDC_BINDINGS",
    "default_kafka_bindings",
    "default_file_meta_binding",
    "build_default_channels",
    "unified_ingest",
    # 合规链路
    "ComplianceSite",
    "ComplianceStep",
    "STEP_DEFINITIONS",
    "RedactionStage",
    "REDACTION_DEFINITIONS",
    "RedactionMark",
    "ComplianceMarks",
    "CONTRACT_REDACTION_FLAGS",
    "ComplianceCloudTopology",
    "CrossBoundaryPayload",
    "assert_may_cross_boundary",
    "DISTRIBUTION_PAYLOADS",
    "ComplianceChain",
    # OSS
    "FILE_META_TABLE",
    "StorageClass",
    "FileType",
    "normalize_file_type",
    "FileMeta",
    "ObjectStat",
    "ObjectStore",
    "NullObjectStore",
    "InMemoryObjectStore",
    "S3CompatibleObjectStore",
    "parse_object_uri",
    "validate_object_key",
    "validate_data_id",
    "verify_checksum",
    "probe_decodable",
    "md5_of",
    # 门禁与异常闭环
    "Severity",
    "CheckStatus",
    "Decision",
    "GateCheck",
    "CheckResult",
    "GateOutcome",
    "decide",
    "merge_outcomes",
    "OSS_CHANNEL_CHECKS",
    "QUALITY_DIMENSIONS",
    "QUALITY_ISSUE_TABLE",
    "OssComplianceGate",
    "unified_gate_hook",
    "AnomalyStep",
    "AnomalyRecord",
    "AnomalyClosedLoop",
    # 落地
    "OdsSink",
    "InMemoryOdsSink",
    "JsonlOdsSink",
    "CompositeOdsSink",
    "FlinkSqlGatewayClient",
    "FlinkSqlGatewaySink",
    "render_insert",
    "stamp_system_fields",
    "project_to_table",
    "dropped_fields",
    # SQL 生成
    "render_catalog_script",
    "render_cdc_pipeline",
    "render_kafka_pipeline",
    "render_file_meta_pipeline",
    "render_starrocks_ingest",
    "write_sql_files",
    # 编排
    "ComplianceIngestPipeline",
    "IngestOutcome",
    # 异常
    "IngestError",
    "ComplianceViolation",
    "GateRejected",
    "ChannelError",
    "SinkError",
    "MissingDependency",
    # 常量表
    "constants",
]
