"""深度对账：五步合规物理链路、双脱敏、三通道入湖、被拒数据的五步异常闭环。

对账基准是原文：

    [a8] 系列二 · 湖仓实战 第 8 篇（收官）《采集数据的合规入湖链路》 2026-09-07
         https://mp.weixin.qq.com/s/qhddTZf_P_g81s5z1RkPPA
    [a5] 全景综述特辑《智驾数据闭环的湖仓架构全景》第六 / 八章 2026-09-08
         https://mp.weixin.qq.com/s/UmHoxjBwRtZT0PgwjkL9DQ
    [a6] 系列二第 6 篇《数据质量门禁设计》第五章（五步异常闭环）2026-09-05
         https://mp.weixin.qq.com/s/e7lf3LrjX9JMHMvVnu4Anw

本文件的断言纪律：**打在原文给的具体数字与原句上**，不满足于「函数能跑通」。
原文原话逐条对应到下面的测试名：

    [a8] 二   五步链路    ① 车端脱敏 → ② 合规室上传 → ③ 合规脱密 → ④ 合规数据分发 → ⑤ 实时入湖
                          前三步「合规处理段」，后两步「智驾云内的分发与入湖」
    [a8] 二   跨域分界    进入智驾云的只有「合规数据副本 + 文件元信息」
    [a8] 三   双脱敏      车端简单脱敏 + 合规云复杂脱敏，两段缺一不可
    [a8] 四   合规云      独立云 · 同一云端 VPC · 不对外暴露，三条同时满足
    [a8] 五   文件外置    本体存 OSS，湖仓只存元信息；元信息写入即可用
    [a8] 六   四项门禁    2×P0（脱敏标记完整性 / data_id 格式合法）+ 2×P1
    [a6] 五   五步闭环    拦截 → 隔离 → 告警 → 分流处置 → 复验；不通过退回隔离，超 3 轮升级 P0
    [a6] 4.2  重复率      事件 ID 幂等去重，重复率 > 5% 告警
    [a5] 六   三通道      CDC / Kafka / OSS 合规上传，统一门禁、统一 ODS 系统字段
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from adas_lakehouse.ingest import (
    DEFAULT_CDC_BINDINGS,
    DIMENSION_KEYS,
    ISSUE_CHANNEL_CODES,
    OSS_CHANNEL_CHECKS,
    QUALITY_DIMENSIONS,
    QUALITY_ISSUE_TABLE,
    REDACTION_DEFINITIONS,
    STEP_DEFINITIONS,
    AnomalyClosedLoop,
    AnomalyStep,
    CdcBinding,
    CdcChannel,
    CdcPhase,
    ChannelKind,
    CheckStatus,
    ComplianceChain,
    ComplianceCloudTopology,
    ComplianceIngestPipeline,
    ComplianceMarks,
    ComplianceSite,
    ComplianceStep,
    CrossBoundaryPayload,
    Decision,
    FileMeta,
    FileType,
    InMemoryObjectStore,
    InMemoryOdsSink,
    IssueStatus,
    KafkaBinding,
    KafkaChannel,
    OssComplianceGate,
    OssFileChannel,
    RedactionMark,
    RedactionStage,
    Severity,
    TriageBranch,
    build_default_channels,
    constants,
    decide,
    dump_payload,
    load_payload,
    md5_of,
    merge_outcomes,
    probe_decodable,
    stamp_system_fields,
    unified_ingest,
    validate_data_id,
    validate_object_key,
    verify_checksum,
)
from adas_lakehouse.ingest.errors import ComplianceViolation, SinkError
from adas_lakehouse.ingest.quality_bridge import CHANNEL_MAP, unified_gate_hook
from adas_lakehouse.ingest.rows import dropped_fields, project_to_table, registered_columns
from adas_lakehouse.ingest.sql import (
    file_meta_message_schema,
    render_cdc_pipeline,
    render_file_meta_pipeline,
    render_kafka_pipeline,
)

DATA_ID = "COLLECT_BP_20260301123045_b7e2"
FILE_TABLE = "ods_data_file_meta"
NOW = datetime(2026, 3, 1, 12, 0, 0)
#: Kafka 通道的「时空合理」窗口是相对**当前时间**算的（未来 300 秒 / 滞后 30 天），
#: 所以事件时间必须贴着现在造；固定的历史时刻跑到第 31 天就会被门禁合理地拦下来。
RECENT = datetime.now() - timedelta(minutes=1)

#: 合规云与智驾云：同一云端、同一 VPC、合规云对象存储不对外暴露（[a8] 第四章三条约束）
TOPOLOGY = ComplianceCloudTopology(
    compliance_cloud_region="cn-shanghai",
    adas_cloud_region="cn-shanghai",
    compliance_cloud_vpc_id="vpc-adas-01",
    adas_cloud_vpc_id="vpc-adas-01",
    compliance_cloud_bucket="compliance-cloud-raw",
)


def _marks(moment: datetime | None = None) -> ComplianceMarks:
    return ComplianceMarks.both_applied(
        vehicle_operator="采集软件 v2.3",
        cloud_operator="XX 合规科技",
        moment=moment or NOW,
    )


def _meta(**overrides) -> FileMeta:
    payload = {
        "file_id": "F-0001",
        "file_type": FileType.POINTCLOUD,
        "data_id": DATA_ID,
        # [a8]「单帧点云几十 MB」：40 MiB
        "file_size_bytes": 41_943_040,
        "object_key": "collect/2026/03/01/F-0001.pcd",
        "checksum_md5": "0" * 32,
        "marks": _marks(),
        "project_code": "BP",
        "vehicle_code": "BP01",
    }
    payload.update(overrides)
    return FileMeta(**payload)


def _pipeline(**kwargs):
    """默认装配：目标表与隔离表各一个 sink，被拦数据真的落 ods_quality_issue。"""
    kwargs.setdefault("sink", InMemoryOdsSink())
    kwargs.setdefault("issue_sink", InMemoryOdsSink())
    return ComplianceIngestPipeline(topology=TOPOLOGY, **kwargs)


def _full_chain(**kwargs) -> ComplianceChain:
    """走完前四步、停在入湖前的链路。"""
    marks = kwargs.pop("marks", None) or _marks()
    chain = ComplianceChain(data_id=DATA_ID)
    chain.advance(ComplianceStep.VEHICLE_REDACTION, marks=marks, operator="采集软件 v2.3")
    chain.advance(ComplianceStep.COMPLIANCE_ROOM_UPLOAD, topology=TOPOLOGY, operator="合规员 A")
    chain.advance(
        ComplianceStep.COMPLIANCE_CLOUD_DESENSITIZATION, marks=marks, operator="XX 合规科技"
    )
    chain.advance(ComplianceStep.COMPLIANT_DATA_DISTRIBUTION, topology=TOPOLOGY)
    return chain


# ===========================================================================
# 一、原文数字逐字对账
# ===========================================================================


def test_source_numbers_are_transcribed_verbatim():
    """原文每个计数都在 constants 里逐字落地，且被真正的实现结构兑现。"""
    # [a8] 「采集大文件的五步合规入湖链路」
    assert constants.COMPLIANCE_CHAIN_STEPS == 5
    assert len(ComplianceStep) == 5 == len(STEP_DEFINITIONS)
    # [a8] 「前三步是『合规处理段』」「后两步是『智驾云内的分发与入湖』」
    assert constants.COMPLIANCE_PROCESSING_SEGMENT_STEPS == 3
    assert constants.ADAS_CLOUD_SEGMENT_STEPS == 2
    assert constants.COMPLIANCE_PROCESSING_SEGMENT_STEPS + constants.ADAS_CLOUD_SEGMENT_STEPS == 5
    # [a8] 「五步链路里有两次脱敏」
    assert constants.REDACTION_STAGE_COUNT == 2 == len(RedactionStage) == len(REDACTION_DEFINITIONS)
    # [a8] 「本通道有四项专属检查——其中两项是 P0 级」
    assert constants.OSS_CHANNEL_GATE_CHECKS == 4
    assert constants.OSS_CHANNEL_P0_CHECKS == 2
    assert constants.OSS_CHANNEL_P1_CHECKS == 2
    # [a8]/[a5] 「五步异常闭环（拦截 → 隔离 → 告警 → 分流处置 → 复验）」
    assert constants.ANOMALY_CLOSED_LOOP_STEPS == 5 == len(AnomalyStep)
    # [a8] 「三通道入湖架构」/「原因有三」/「三条原则」/ 合规云三条约束
    assert constants.INGEST_CHANNEL_COUNT == 3 == len(ChannelKind)
    assert constants.OSS_CHANNEL_RATIONALE_COUNT == 3
    assert constants.LAKE_ENTRY_PRINCIPLES == 3
    assert constants.COMPLIANCE_CLOUD_CONSTRAINTS == 3
    # [a5] 六维检查框架 / 三分支处置 / P0~P3
    assert constants.QUALITY_DIMENSION_COUNT == 6 == len(QUALITY_DIMENSIONS)
    assert constants.QUALITY_BRANCH_COUNT == 3 == len(Decision)
    assert constants.ALERT_LEVELS == ("P0", "P1", "P2", "P3")
    assert tuple(s.value for s in Severity) == constants.ALERT_LEVELS
    # [a5] 「8 类数据源」「CDC 三阶段」「同步延迟秒级」
    assert constants.SOURCE_SYSTEM_CATEGORIES == 8 == constants.ODS_ONE_TO_ONE_SOURCES
    assert constants.CDC_PHASE_COUNT == 3 == len(CdcPhase)
    assert constants.CDC_SYNC_LATENCY_TEXT == "秒级"
    # [a8] 「标准 / 低频 / 归档」在「五级分层」里的文件域投影
    assert constants.FILE_STORAGE_CLASSES == ("标准", "低频", "归档")
    assert constants.STORAGE_TIER_COUNT == 5
    # [a6] 第五章 ④ 三分支、⑤ 超 3 轮升级 P0；4.2 重复率 > 5%
    assert constants.TRIAGE_BRANCH_COUNT == 3 == len(TriageBranch)
    assert constants.MAX_RECHECK_ROUNDS == 3
    assert constants.DUPLICATE_RATE_ALERT_THRESHOLD == 0.05


def test_a6_numbers_do_not_drift_from_the_quality_subsystem():
    """[a6] 的数字在 quality 子系统里也登记了一份，两处必须逐字相同。

    ingest 对 quality 一律延迟 import（quality 在 import 期装载整套规则集），
    所以取值只能各存一份；这条测试就是那根防漂移的绳子。
    """
    from adas_lakehouse.quality import thresholds

    assert constants.MAX_RECHECK_ROUNDS == thresholds.MAX_RECHECK_ROUNDS == 3
    assert (
        constants.DUPLICATE_RATE_ALERT_THRESHOLD
        == thresholds.DUPLICATE_RATE_ALERT_THRESHOLD
        == 0.05
    )
    # 写 ODS 的重试次数与「复验超 3 轮」同口径，避免同一条数据在两处按不同轮次对待
    assert constants.ODS_WRITE_MAX_ATTEMPTS == thresholds.MAX_RECHECK_ROUNDS


@pytest.mark.parametrize(
    ("step", "label", "site", "fragment"),
    [
        (
            ComplianceStep.VEHICLE_REDACTION,
            "车端脱敏",
            ComplianceSite.VEHICLE,
            "个性脱敏（人脸 / 车牌）+ 地信脱敏",
        ),
        (
            ComplianceStep.COMPLIANCE_ROOM_UPLOAD,
            "合规室上传",
            ComplianceSite.COMPLIANCE_ROOM,
            "上传至合规云对象存储（不对外暴露）",
        ),
        (
            ComplianceStep.COMPLIANCE_CLOUD_DESENSITIZATION,
            "合规脱密",
            ComplianceSite.COMPLIANCE_CLOUD,
            "删除敏感 POI（军事设施等）、模糊桥梁限高等具体数值",
        ),
        (
            ComplianceStep.COMPLIANT_DATA_DISTRIBUTION,
            "合规数据分发",
            ComplianceSite.ADAS_CLOUD,
            "合规数据副本复制至智驾云 OSS，同时向业务方 Kafka 发送文件元信息（同一 VPC 流转）",
        ),
        (
            ComplianceStep.REALTIME_INGEST,
            "实时入湖",
            ComplianceSite.LAKEHOUSE,
            "元信息经质量门禁写入 ods_data_file_meta",
        ),
    ],
)
def test_each_step_quotes_the_source_table(step, label, site, fragment):
    """[a8] 第二章表格三列（步骤 / 发生地 / 关键动作）逐字落库。"""
    definition = STEP_DEFINITIONS[step]
    assert definition.label == label
    assert definition.site is site
    assert fragment in definition.key_action


def test_segments_split_three_plus_two():
    """前三步「合规处理段」，后两步「智驾云内的分发与入湖」。"""
    processing = [s for s in ComplianceStep if s.segment == "合规处理段"]
    adas_cloud = [s for s in ComplianceStep if s.segment == "智驾云内的分发与入湖"]
    assert [s.value for s in processing] == [1, 2, 3]
    assert [s.value for s in adas_cloud] == [4, 5]
    # 合规处理段发生在车端 / 合规室 / 合规云——智驾云一步都不沾
    assert {s.site for s in processing} == {
        ComplianceSite.VEHICLE,
        ComplianceSite.COMPLIANCE_ROOM,
        ComplianceSite.COMPLIANCE_CLOUD,
    }


def test_the_two_redaction_stages_quote_their_reasons():
    """[a8] 第三章：两段脱敏「缺一不可，理由互为补充」——两条理由都要在代码里。"""
    vehicle = REDACTION_DEFINITIONS[RedactionStage.VEHICLE_SIMPLE]
    cloud = REDACTION_DEFINITIONS[RedactionStage.CLOUD_COMPLEX]
    assert vehicle.label == "车端简单脱敏"
    assert vehicle.solves == "数据离车即合规——硬盘离开车的那一刻就不含个人信息"
    assert "车端实时算力做不到" in vehicle.gap_if_alone
    assert cloud.label == "合规云复杂脱敏"
    assert cloud.solves == "车端算力与资质做不了的云端复杂脱敏"
    assert "全程「裸奔」" in cloud.gap_if_alone


def test_the_four_oss_checks_quote_the_gate_table():
    """[a8] 第六章四项专属检查：名字 / 说明 / 级别逐字，且两 P0 在前。"""
    expected = [
        ("脱敏标记完整性", "P0", "文件需携带「车端脱敏 + 合规云脱密」双合规标记，缺失即合规风险"),
        ("data_id 格式合法", "P0", "全局数据 ID 格式与来源前缀合法性（血缘追溯起点）"),
        ("文件本体可解码", "P1", "图像 / 点云文件完整性与可解码性校验"),
        ("元信息与 OSS 路径一致", "P1", "file_path 合法且指向智驾云 OSS，checksum 可校验"),
    ]
    assert [(c.name, c.severity.value, c.description) for c in OSS_CHANNEL_CHECKS] == expected
    # 「脱敏标记缺失……这不是数据质量问题，而是合规问题」——只有第一项是合规问题
    assert [c.is_compliance for c in OSS_CHANNEL_CHECKS] == [True, False, False, False]
    # 每项都落在六维检查框架的某一维上
    assert {c.dimension for c in OSS_CHANNEL_CHECKS} <= set(QUALITY_DIMENSIONS)


def test_kafka_time_windows_are_shared_by_python_and_flink_sql():
    """同一个容忍窗口不能 Python 一份、SQL 一份——两处分叉等于两套门禁。"""
    assert KafkaChannel.DEFAULT_FUTURE_TOLERANCE_SEC == constants.KAFKA_FUTURE_TOLERANCE_SEC == 300
    assert KafkaChannel.DEFAULT_LAG_TOLERANCE_DAYS == constants.KAFKA_LAG_TOLERANCE_DAYS == 30
    sql = "\n".join(
        render_kafka_pipeline(
            KafkaBinding(
                topic="t",
                target_table="ods_vehicle_trigger_event",
                source_system="车云平台",
                event_time_field="trigger_time",
                partition_field="trigger_type",
            )
        )
    )
    assert f"TIMESTAMPADD(SECOND, {constants.KAFKA_FUTURE_TOLERANCE_SEC}, CURRENT_TIMESTAMP)" in sql
    assert f"TIMESTAMPADD(DAY, -{constants.KAFKA_LAG_TOLERANCE_DAYS}, CURRENT_TIMESTAMP)" in sql


# ===========================================================================
# 二、五步物理链路：顺序、准入条件、跨域边界
# ===========================================================================


@pytest.mark.parametrize("step", list(ComplianceStep)[1:])
def test_no_step_may_be_skipped(step):
    """[a8]：五步链路顺序不能乱、步骤不能跳——直接从第 N 步开始一律抛。"""
    chain = ComplianceChain(data_id=DATA_ID)
    with pytest.raises(ComplianceViolation, match="顺序不能乱"):
        chain.advance(step, marks=_marks(), topology=TOPOLOGY)


def test_a_step_may_not_be_advanced_twice():
    chain = ComplianceChain(data_id=DATA_ID)
    chain.advance(ComplianceStep.VEHICLE_REDACTION, marks=_marks())
    with pytest.raises(ComplianceViolation, match="不可重复推进"):
        chain.advance(ComplianceStep.VEHICLE_REDACTION, marks=_marks())


def test_step1_requires_the_vehicle_redaction_mark():
    """① 车端脱敏：没有车端脱敏标记，这一步就没发生过。"""
    chain = ComplianceChain(data_id=DATA_ID)
    only_cloud = ComplianceMarks(cloud=_marks().cloud)
    with pytest.raises(ComplianceViolation, match="车端简单脱敏"):
        chain.advance(ComplianceStep.VEHICLE_REDACTION, marks=only_cloud)


def test_step3_requires_the_cloud_desensitization_mark():
    """③ 合规脱密：车端脱敏过了也不能顶替合规云脱密。"""
    chain = ComplianceChain(data_id=DATA_ID)
    only_vehicle = ComplianceMarks(vehicle=_marks().vehicle)
    chain.advance(ComplianceStep.VEHICLE_REDACTION, marks=only_vehicle)
    chain.advance(ComplianceStep.COMPLIANCE_ROOM_UPLOAD)
    with pytest.raises(ComplianceViolation, match="合规云复杂脱敏"):
        chain.advance(ComplianceStep.COMPLIANCE_CLOUD_DESENSITIZATION, marks=only_vehicle)


@pytest.mark.parametrize("missing", ["operator", "applied_at", "rules"])
def test_a_mark_that_only_claims_to_be_applied_is_not_enough(missing):
    """标记说自己「做过」还不够：执行方 / 时间 / 生效规则缺一项就不是可追责的标记。"""
    kwargs = {
        "operator": "采集软件 v2.3",
        "applied_at": NOW,
        "rules": ("face_blur", "plate_blur"),
    }
    kwargs[missing] = "" if missing == "operator" else (None if missing == "applied_at" else ())
    mark = RedactionMark(RedactionStage.VEHICLE_SIMPLE, True, **kwargs)
    assert mark.validate(), "标记不完整却校验通过"
    chain = ComplianceChain(data_id=DATA_ID)
    with pytest.raises(ComplianceViolation, match="标记不完整"):
        chain.advance(ComplianceStep.VEHICLE_REDACTION, marks=ComplianceMarks(vehicle=mark))


def test_step2_refuses_a_publicly_exposed_compliance_cloud():
    """② 合规室上传：目的地必须是「不对外暴露」的合规云对象存储。

    这条约束在第 ② 步就生效，而不是等到第 ④ 步分发时才发现桶是敞开的——
    数据已经在那个桶里躺过了。
    """
    exposed = ComplianceCloudTopology(
        compliance_cloud_region="cn-shanghai",
        adas_cloud_region="cn-shanghai",
        compliance_cloud_vpc_id="vpc-adas-01",
        adas_cloud_vpc_id="vpc-adas-01",
        object_store_public_endpoint=True,
    )
    chain = ComplianceChain(data_id=DATA_ID)
    chain.advance(ComplianceStep.VEHICLE_REDACTION, marks=_marks())
    with pytest.raises(ComplianceViolation, match="不对外暴露的合规云对象存储"):
        chain.advance(ComplianceStep.COMPLIANCE_ROOM_UPLOAD, topology=exposed)


def test_step4_requires_a_declared_topology():
    """④ 合规数据分发：不声明合规云拓扑就不许分发——合规靠架构边界，不靠流程承诺。"""
    chain = ComplianceChain(data_id=DATA_ID)
    marks = _marks()
    chain.advance(ComplianceStep.VEHICLE_REDACTION, marks=marks)
    chain.advance(ComplianceStep.COMPLIANCE_ROOM_UPLOAD)
    chain.advance(ComplianceStep.COMPLIANCE_CLOUD_DESENSITIZATION, marks=marks)
    with pytest.raises(ComplianceViolation, match="必须声明合规云架构拓扑"):
        chain.advance(ComplianceStep.COMPLIANT_DATA_DISTRIBUTION)


@pytest.mark.parametrize(
    ("field", "value", "fragment"),
    [
        ("adas_cloud_region", "cn-beijing", "同一云端"),
        ("adas_cloud_vpc_id", "vpc-other", "同一 VPC"),
        ("object_store_public_endpoint", True, "不对外暴露"),
        ("intranet_only", False, "不出公网、不落第三方"),
        ("provider_qualified", False, "不具备合规资质"),
        ("managed_by_compliance_provider", False, "必须由合规方管理"),
    ],
)
def test_each_cloud_constraint_is_enforced(field, value, fragment):
    """[a8] 第四章三条架构约束（独立云 / 同一云端 VPC / 不对外暴露）逐条有牙齿。"""
    import dataclasses

    broken = dataclasses.replace(TOPOLOGY, **{field: value})
    problems = broken.validate()
    assert any(fragment in p for p in problems), problems
    with pytest.raises(ComplianceViolation):
        broken.assert_valid()
    # 完好的拓扑一条都不违反
    assert TOPOLOGY.validate() == []


def test_compliance_and_adas_cloud_may_not_share_a_bucket():
    """物理上隔离：两朵云共用一个桶，原始数据就没有「止步于合规云」的落点。"""
    import dataclasses

    same_bucket = dataclasses.replace(TOPOLOGY, adas_cloud_bucket="compliance-cloud-raw")
    assert any("物理上隔离" in p for p in same_bucket.validate())


@pytest.mark.parametrize(
    "payload",
    [CrossBoundaryPayload.RAW_DISK_DATA, CrossBoundaryPayload.UNREDACTED_DATA],
)
def test_raw_and_unredacted_data_may_not_leave_the_compliance_cloud(payload):
    """[a8] 第二章分界：原始硬盘数据的完整生命周期止步于合规云。

    拦截点在第 ④ 步的真实闸门上，不只是一个供外部调用的断言函数。
    """
    chain = ComplianceChain(data_id=DATA_ID)
    marks = _marks()
    chain.advance(ComplianceStep.VEHICLE_REDACTION, marks=marks)
    chain.advance(ComplianceStep.COMPLIANCE_ROOM_UPLOAD, topology=TOPOLOGY)
    chain.advance(ComplianceStep.COMPLIANCE_CLOUD_DESENSITIZATION, marks=marks)
    with pytest.raises(ComplianceViolation, match="不得离开合规云"):
        chain.advance(
            ComplianceStep.COMPLIANT_DATA_DISTRIBUTION,
            topology=TOPOLOGY,
            payloads=[CrossBoundaryPayload.COMPLIANT_DATA_COPY, payload],
        )
    assert ComplianceStep.COMPLIANT_DATA_DISTRIBUTION not in chain.completed


def test_distribution_carries_both_payloads_by_default():
    """[a8] 第 ④ 行：「副本复制至智驾云 OSS，**同时**向业务方 Kafka 发送文件元信息」。"""
    chain = _full_chain()
    assert set(chain.crossed_payloads) == {
        CrossBoundaryPayload.COMPLIANT_DATA_COPY,
        CrossBoundaryPayload.FILE_META,
    }
    # 一类都不过境的「分发」不是分发
    empty = ComplianceChain(data_id="COLLECT_BP_20260301123045_0001")
    marks = _marks()
    empty.advance(ComplianceStep.VEHICLE_REDACTION, marks=marks)
    empty.advance(ComplianceStep.COMPLIANCE_ROOM_UPLOAD, topology=TOPOLOGY)
    empty.advance(ComplianceStep.COMPLIANCE_CLOUD_DESENSITIZATION, marks=marks)
    with pytest.raises(ComplianceViolation, match="至少要过境一类载荷"):
        empty.advance(ComplianceStep.COMPLIANT_DATA_DISTRIBUTION, topology=TOPOLOGY, payloads=[])


def test_ingest_gate_lists_every_unfinished_step():
    """⑤ 实时入湖前的硬闸门：没走完的步骤要一条条说出来，而不是一句「不合规」。"""
    chain = ComplianceChain(data_id=DATA_ID)
    chain.advance(ComplianceStep.VEHICLE_REDACTION, marks=_marks())
    problems = chain.ready_for_ingest()
    assert any("第 2 步「合规室上传」（合规室）" in p for p in problems)
    assert any("第 3 步「合规脱密」（合规云）" in p for p in problems)
    assert any("第 4 步「合规数据分发」（智驾云）" in p for p in problems)
    with pytest.raises(ComplianceViolation, match="不得进入第 5 步"):
        chain.assert_ready_for_ingest()
    assert _full_chain().ready_for_ingest() == []


def test_audit_row_keeps_every_step_operator_and_the_boundary_trace():
    """一条链路 = 一行可追责的审计记录：每步的时间、执行方、过境载荷都在。"""
    chain = _full_chain()
    chain.advance(ComplianceStep.REALTIME_INGEST)
    row = chain.to_audit_row()
    assert row["data_id"] == DATA_ID
    assert row["compliance_chain_step"] == 5
    assert row["compliance_chain_complete"] is True
    assert row["compliance_status"] == "compliant"
    assert row["step1_operator"] == "采集软件 v2.3"
    assert row["step2_operator"] == "合规员 A"
    assert row["step3_operator"] == "XX 合规科技"
    assert row["crossed_payloads"] == "合规数据副本,文件元信息"
    for step in ComplianceStep:
        assert row[f"step{step.value}_time"] is not None
    assert row["chain_elapsed_sec"] is not None
    # 双脱敏标记随审计行一起走
    assert row["vehicle_desensitized_flag"] is True
    assert row["cloud_compliance_decrypted_flag"] is True


# ===========================================================================
# 三、双脱敏：缺一不可，且标记必须真的落进湖表
# ===========================================================================


@pytest.mark.parametrize("dropped", list(RedactionStage))
def test_missing_either_stage_blocks_the_file(dropped):
    """[a8]：双合规标记缺一即 P0 合规风险，两段谁缺都一样拦。"""
    both = _marks()
    kept = {
        RedactionStage.VEHICLE_SIMPLE: ComplianceMarks(cloud=both.cloud),
        RedactionStage.CLOUD_COMPLEX: ComplianceMarks(vehicle=both.vehicle),
    }[dropped]
    assert kept.missing_stages() == (dropped,)
    assert not kept.is_complete

    pipeline = _pipeline()
    outcome = pipeline.submit(_meta(marks=kept))
    assert not outcome.accepted
    assert pipeline.sink.rows(FILE_TABLE) == []
    # 不是静默失败：进了隔离表、发了 P0 告警、留了可复核的原始报文
    assert outcome.anomaly is not None
    assert outcome.anomaly.severity is Severity.P0
    assert outcome.anomaly.outcome.has_compliance_failure()
    assert pipeline.issue_sink.rows(QUALITY_ISSUE_TABLE)


def test_both_marks_land_on_the_contract_columns_not_only_the_audit_fields():
    """脱敏标记必须落在契约列名上，否则投影到湖表时会被整组丢掉。

    [a5] 第六章要求大文件外置后湖仓保留「文件大小、**脱敏标记**、校验和、归属 data_id」
    四项——标记掉了就是这条要求没落地，而且掉得无声无息。
    """
    row = _meta().to_row()
    assert row["vehicle_desensitized_flag"] is True
    assert row["cloud_compliance_decrypted_flag"] is True
    projected = project_to_table(FILE_TABLE, row)
    assert projected["vehicle_desensitized_flag"] is True
    assert projected["cloud_compliance_decrypted_flag"] is True
    # 四项外置元信息全部进湖
    assert projected["file_size_bytes"] == 41_943_040
    assert projected["checksum_md5"] == "0" * 32
    assert projected["data_id"] == DATA_ID


def test_marks_round_trip_through_the_contract_flags_alone():
    """从湖表回读一行（只有两个 flag 列）也能重建标记——不能只认入湖侧的键名。"""
    restored = ComplianceMarks.from_meta(
        {"vehicle_desensitized_flag": True, "cloud_compliance_decrypted_flag": True}
    )
    assert restored.missing_stages() == ()
    # 但缺执行方与时间，仍不算「可追责的完整标记」
    assert restored.validate()


def test_marks_to_meta_keeps_operator_time_and_rules_for_audit():
    meta = _marks().to_meta()
    assert meta["redaction_vehicle_operator"] == "采集软件 v2.3"
    assert meta["redaction_cloud_operator"] == "XX 合规科技"
    # 默认规则取原文措辞：车端人脸/车牌+地信，合规云删 POI+模糊桥梁限高
    assert meta["redaction_vehicle_rules"] == "face_blur,plate_blur,geo_desensitize"
    assert meta["redaction_cloud_rules"] == "sensitive_poi_delete,bridge_height_blur"
    assert meta["redaction_vehicle_time"] == NOW


# ===========================================================================
# 四、被拒数据的完整去向：拦截 → 隔离 → 告警 → 分流处置 → 复验
# ===========================================================================


def _reject(pipeline=None, **overrides):
    """造一条必定被门禁拒的文件（data_id 非法，P0），返回 (pipeline, outcome)。"""
    pipeline = pipeline or _pipeline()
    overrides.setdefault("data_id", "NOT-A-DATA-ID")
    outcome = pipeline.submit(_meta(**overrides))
    return pipeline, outcome


def test_rejected_data_never_reaches_the_target_table():
    """① 门禁拦截：阻断写入 ODS。门禁在盖章与落表之前，不存在「先写进去再补检查」。"""
    pipeline, outcome = _reject()
    assert not outcome.accepted
    assert pipeline.sink.rows(FILE_TABLE) == []
    assert outcome.gate is not None and outcome.gate.decision is Decision.REJECT


def test_isolated_row_uses_the_contract_columns_and_keeps_the_hit_rules():
    """② 异常隔离：被拒数据**连同命中规则**落隔离表，而不是打日志了事。

    最容易翻车的地方是列名：自造的列名会被 ``project_to_table`` 整列丢掉，
    隔离表里只剩一个 ID 和一段报文，「找得回、说得清」全落空。
    """
    pipeline, outcome = _reject()
    rows = pipeline.issue_sink.rows(QUALITY_ISSUE_TABLE)
    assert rows, "被拒数据没有进隔离表"
    row = rows[-1]

    registered = set(registered_columns(QUALITY_ISSUE_TABLE) or ())
    assert set(row) <= registered, f"隔离行里有契约没有的列: {set(row) - registered}"
    assert dropped_fields(QUALITY_ISSUE_TABLE, outcome.anomaly.to_issue_row()) == ()

    # 说得清：命中规则、维度、等级、原因，一个都不能少
    assert "data_id_format_valid" in row["rule_ids"] == row["rule_id"]
    assert row["dimension"] == DIMENSION_KEYS["有效性"] == "validity"
    assert row["rule_dimension"] == row["dimension"]
    assert "data_id 格式合法" in row["issue_detail"]
    assert row["issue_detail"] == row["detail"]
    # 找得回：目标表 + 记录键，两套同义列名都填
    assert row["target_table"] == row["source_table"] == FILE_TABLE
    assert row["source_record_key"] == row["record_key"] == "F-0001"
    assert row["source_channel"] == ChannelKind.OSS.issue_code == "oss_file"
    # 隔离表也是 ODS 表，系统字段照盖
    assert row["_ingest_time"] is not None and row["_source_system"]


def test_severity_and_issue_level_are_two_different_columns():
    """契约 severity = ERROR/WARNING（检查器严重度），issue_level = P0~P3（告警等级）。

    把 P0 写进 severity 列，下游按 severity 统计「ERROR 多少条」就永远是 0。
    """
    pipeline, _ = _reject()
    row = pipeline.issue_sink.rows(QUALITY_ISSUE_TABLE)[-1]
    assert row["severity"] == "ERROR"
    assert row["issue_level"] in {s.value for s in Severity}
    assert row["issue_level"] == "P0"


def test_raw_payload_is_json_and_actually_replayable():
    """「原始数据不丢失是整套门禁可重放、可审计的根基」——那它就必须能被解析回来。

    报文里有 datetime（脱敏时间 / 分发时间），``repr()`` 出来的东西既不是 JSON
    也过不了 literal_eval，等于只能用眼睛看。
    """
    pipeline, outcome = _reject(distributed_at=NOW)
    row = pipeline.issue_sink.rows(QUALITY_ISSUE_TABLE)[-1]
    restored = json.loads(row["raw_payload"])
    assert restored["file_id"] == "F-0001"
    assert restored["file_size_bytes"] == 41_943_040
    assert restored["redaction_vehicle_time"].startswith("2026-03-01")
    assert row["replayable"] is True
    assert row["payload_hash"]
    # 闭环记录自己也能把报文还原成可重跑门禁的一行
    assert outcome.anomaly.replay_payload()["data_id"] == "NOT-A-DATA-ID"


def test_payload_dump_load_round_trip_keeps_every_value():
    payload = {"a": 1, "t": NOW, "tup": ("x", "y"), "n": None}
    restored = load_payload(dump_payload(payload))
    assert restored["a"] == 1
    assert restored["t"] == "2026-03-01 12:00:00.000"
    assert restored["tup"] == ["x", "y"]
    assert restored["n"] is None
    with pytest.raises(ValueError, match="不是合法 JSON"):
        load_payload("{'not': 'json'}")


def test_alerts_are_recorded_even_without_an_alert_sink():
    """③ 分级告警：没接告警通道时告警也不能凭空消失，至少要留在可复核的位置。"""
    pipeline, _ = _reject()
    assert pipeline.closed_loop.alerts
    notice = pipeline.closed_loop.alerts[-1]
    assert notice.severity is Severity.P0
    assert notice.message.startswith("[P0]")
    assert "F-0001" in notice.message


def test_compliance_issue_is_flagged_as_compliance_not_quality():
    """[a8]：脱敏标记缺失「不是数据质量问题，而是合规问题」。"""
    pipeline = _pipeline()
    outcome = pipeline.submit(_meta(marks=ComplianceMarks()))
    assert outcome.anomaly.outcome.has_compliance_failure()
    assert pipeline.closed_loop.alerts[-1].is_compliance is True
    assert "（合规问题）" in pipeline.closed_loop.alerts[-1].message
    assert pipeline.stats()["compliance_issues"] == 1


def test_step4_has_all_three_branches():
    """④ 分流处置：A 自动修复 / B 人工修复 / C 弃置归档，三条都能走。"""
    assert [b.value for b in TriageBranch] == ["auto_repair", "manual_repair", "discard_archive"]
    assert TriageBranch.AUTO.label.startswith("A 自动修复")
    assert TriageBranch.MANUAL.label.startswith("B 人工修复")
    assert TriageBranch.DISCARD.label.startswith("C 弃置归档")

    pipeline, outcome = _reject()
    record = outcome.anomaly
    pipeline.triage(record, TriageBranch.AUTO, "源端重发元信息")
    assert record.branch is TriageBranch.AUTO
    assert record.status is IssueStatus.DISPATCHED
    assert record.current_step is AnomalyStep.TRIAGE
    row = pipeline.issue_sink.rows(QUALITY_ISSUE_TABLE)[-1]
    assert row["repair_action"] == row["handle_strategy"] == "auto_repair"


def test_discard_must_state_a_reason_and_may_not_swallow_a_compliance_issue():
    """C 弃置归档：「无法修复，**标记原因后**归档保留审计」；合规问题不许一弃了之。"""
    pipeline, outcome = _reject()
    with pytest.raises(ValueError, match="必须标记原因"):
        pipeline.triage(outcome.anomaly, TriageBranch.DISCARD)
    pipeline.triage(outcome.anomaly, TriageBranch.DISCARD, "源端已删除，无法补数")
    assert outcome.anomaly.discard_reason == "源端已删除，无法补数"
    assert outcome.anomaly.status is IssueStatus.DISCARDED

    compliance = _pipeline()
    bad = compliance.submit(_meta(marks=ComplianceMarks()))
    with pytest.raises(ValueError, match="不得弃置归档"):
        compliance.triage(bad.anomaly, TriageBranch.DISCARD, "懒得补脱敏")


def test_step5_reruns_the_whole_gate_and_reingests_on_pass():
    """⑤ 复验重入湖：「重新执行全部门禁规则，通过则写入 ODS 并回填处理状态」。

    复验不是调用方说了算——门禁重跑一遍，通过才写。
    """
    pipeline, outcome = _reject()
    record = outcome.anomaly
    pipeline.triage(record, TriageBranch.MANUAL, "源端订正 data_id")

    # 源端把 data_id 补正了：直接改隔离记录里的报文，模拟补数后重放
    record.payload["data_id"] = DATA_ID
    pipeline.recheck(record, note="源端已订正")

    assert record.status is IssueStatus.REINGESTED
    assert record.recheck_rounds == 1
    assert record.reingested_at is not None
    assert record.current_step is AnomalyStep.RECHECK
    # 真的落进了目标表
    rows = pipeline.sink.rows(FILE_TABLE)
    assert [r["data_id"] for r in rows] == [DATA_ID]
    assert pipeline.pending_recheck() == []


def test_step5_failed_recheck_returns_to_isolation():
    """不通过 → 退回隔离，且命中规则按这一轮的结论刷新。"""
    pipeline, outcome = _reject()
    record = outcome.anomaly
    pipeline.recheck(record, note="没改就重验")
    assert record.status is IssueStatus.ISOLATED
    assert record.recheck_rounds == 1
    assert record.escalated is False
    assert pipeline.sink.rows(FILE_TABLE) == []
    assert record in pipeline.pending_recheck()


def test_step5_escalates_to_p0_strictly_after_three_rounds():
    """[a6]：「不通过退回隔离，**超 3 轮升级 P0**」——第 3 轮还不升，第 4 轮才升。"""
    pipeline = _pipeline()
    # 造一条 P1（而不是 P0）的拒绝，升级才看得出来：file_path 指向非白名单 bucket
    outcome = pipeline.submit(_meta(object_key="s3://someone-else/collect/a.pcd"))
    record = outcome.anomaly
    assert record.severity is Severity.P1

    for round_no in range(1, constants.MAX_RECHECK_ROUNDS + 1):
        pipeline.recheck(record)
        assert record.recheck_rounds == round_no
        assert record.escalated is False, f"第 {round_no} 轮就升级了，原文是「超 3 轮」"
        assert record.severity is Severity.P1

    pipeline.recheck(record)
    assert record.recheck_rounds == constants.MAX_RECHECK_ROUNDS + 1 == 4
    assert record.escalated is True
    assert record.severity is Severity.P0
    assert pipeline.closed_loop.alerts[-1].severity is Severity.P0
    assert "超 3 轮" in pipeline.closed_loop.alerts[-1].message
    row = pipeline.issue_sink.rows(QUALITY_ISSUE_TABLE)[-1]
    assert row["escalated"] is True
    assert row["recheck_count"] == row["recheck_round"] == 4
    assert row["issue_level"] == "P0"


def test_a_compliance_issue_can_never_be_rechecked_through():
    """[a8]：脱敏标记缺失必须退回合规云重做双脱敏，门禁不接受「放行」结论。"""
    loop = AnomalyClosedLoop()
    pipeline = _pipeline(closed_loop=loop)
    outcome = pipeline.submit(_meta(marks=ComplianceMarks()))
    record = outcome.anomaly
    with pytest.raises(ValueError, match="不得复验放行"):
        loop.recheck(record, passed=True)
    # 就算真去重跑门禁，报文里照样没有脱敏标记，重跑结论仍是拒绝
    pipeline.recheck(record)
    assert record.status is IssueStatus.ISOLATED
    assert pipeline.sink.rows(FILE_TABLE) == []


def test_recheck_demands_a_verdict():
    loop = AnomalyClosedLoop()
    pipeline = _pipeline(closed_loop=loop)
    record = pipeline.submit(_meta(data_id="bad")).anomaly
    with pytest.raises(ValueError, match="必须给出结论"):
        loop.recheck(record)


def test_history_records_every_closed_loop_step_for_accountability():
    """隔离表的 history 列：五步闭环每次状态推进追加一条，可追责。"""
    pipeline, outcome = _reject()
    record = outcome.anomaly
    pipeline.triage(record, TriageBranch.MANUAL, "工单 #42")
    record.payload["data_id"] = DATA_ID
    pipeline.recheck(record)
    steps = [e["step"] for e in json.loads(record.history_json())]
    assert steps == ["拦截", "隔离", "告警", "分流处置", "复验"]
    assert [s.value for s in AnomalyStep] == steps


def test_isolation_is_keyed_by_a_stable_issue_id():
    """同一条被拦数据反复回写状态，issue_id 不变——隔离表是主键表，不是流水账。"""
    pipeline, outcome = _reject()
    rows = pipeline.issue_sink.rows(QUALITY_ISSUE_TABLE)
    assert len({r["issue_id"] for r in rows}) == 1
    assert rows[0]["issue_status"] == "isolated"
    assert rows[-1]["issue_status"] == "alerted"


def test_an_unparsable_message_is_isolated_not_skipped():
    """转换失败（Schema 不可解析）是 [a6] 点名的 P0 典型场景，不能 continue 掉。

    生成的 Kafka 源表写死 ``json.ignore-parse-errors=false`` 就是这个取向：
    脏消息要能被门禁看见并进隔离表。Python 侧的补数路径必须同款。
    """
    sink, issues = InMemoryOdsSink(), InMemoryOdsSink()
    from adas_lakehouse.ingest.channels import isolating_closed_loop

    loop = isolating_closed_loop(issues)
    channel = OssFileChannel(sink=sink, closed_loop=loop)
    report = channel.run([{"file_id": "F-BAD"}])  # 缺 file_type / data_id / object_key…

    assert report.total == 1 and report.rejected == 1 and report.accepted == 0
    assert sink.rows(FILE_TABLE) == []
    rows = issues.rows(QUALITY_ISSUE_TABLE)
    assert rows, "转换失败的记录被静默丢弃了"
    assert rows[-1]["issue_level"] == "P0"
    assert "schema_unparsable" in rows[-1]["rule_ids"]
    assert json.loads(rows[-1]["raw_payload"])["file_id"] == "F-BAD"
    assert report.rejections and report.rejections[0].decision is Decision.REJECT


def test_truncating_a_batch_is_reported_not_silent():
    """limit 之外的记录不能无声消失——报告里要说清楚被截断了。"""
    channel = OssFileChannel(sink=InMemoryOdsSink())
    metas = [
        _meta(file_id=f"F-{i:04d}", data_id=f"COLLECT_BP_2026030112304{i}_b7e2") for i in range(3)
    ]
    report = channel.run(metas, limit=2)
    assert report.total == 2
    assert report.truncated is True
    assert any("超过单批上限 2 条" in e for e in report.errors)
    assert "输入被 limit 截断" in report.summary()

    full = OssFileChannel(sink=InMemoryOdsSink()).run(metas, limit=None)
    assert full.total == 3 and full.truncated is False


# ===========================================================================
# 五、三通道入湖：统一系统字段、统一门禁、幂等重放
# ===========================================================================


def test_three_channels_quote_the_source_table():
    """[a8] 第一章 / [a5] 第六章表格：通道 / 承接数据 / 入湖方式三列逐字。"""
    expected = {
        ChannelKind.CDC: (
            "Flink CDC",
            "各平台 MySQL 业务库（产线 / 标注 / 训练等）",
            "读 binlog 实时同步",
        ),
        ChannelKind.KAFKA: (
            "Kafka",
            "事件流（产线埋点 / 训练指标 / 车端触发）",
            "Flink 实时消费",
        ),
        ChannelKind.OSS: (
            "OSS 合规上传",
            "采集大文件（图像 / 点云 / 传感器数据）",
            "文件本体存 OSS · 元信息经 Kafka 入湖",
        ),
    }
    assert {k: (k.label, k.payload, k.mechanism) for k in ChannelKind} == expected


def test_channel_codes_are_shared_with_the_quality_subsystem():
    """隔离表 source_channel 只能有一套字面量——两个写入方写同一列。"""
    from adas_lakehouse.quality import Channel

    assert set(ISSUE_CHANNEL_CODES.values()) == {"mysql_cdc", "kafka", "oss_file"}
    for kind, code in CHANNEL_MAP.items():
        assert kind.issue_code == code
        assert Channel(code).value == code


@pytest.mark.parametrize("kind", list(ChannelKind))
def test_every_channel_stamps_the_two_ods_system_fields(kind):
    """[a5]：ODS 层系统字段 = _ingest_time + _source_system，三条通道一视同仁。"""
    sink = InMemoryOdsSink()
    channel, records = _channel_with_records(kind, sink)
    report = channel.run(records, ingest_time=NOW)
    assert report.accepted == 1, report.summary()
    row = sink.rows(channel.target_table)[0]
    assert row["_ingest_time"] == NOW
    assert row["_source_system"] == channel.source_system


def _channel_with_records(kind: ChannelKind, sink):
    """三条通道各造一条合法记录（目标表都是契约里真实登记的 ODS 表）。"""
    if kind is ChannelKind.CDC:
        binding = DEFAULT_CDC_BINDINGS[0]
        return CdcChannel(binding, sink=sink), [
            {"collect_task_id": "T-1", "task_status": "running", "project_code": "BP"}
        ]
    if kind is ChannelKind.KAFKA:
        binding = KafkaBinding(
            topic="vehicle.trigger.event",
            target_table="ods_vehicle_trigger_event",
            source_system="车云平台",
            event_time_field="trigger_time",
            partition_field="trigger_type",
        )
        return KafkaChannel(binding, sink=sink), [
            {
                "event_id": "E-1",
                "trigger_type": "aeb",
                "data_id": DATA_ID,
                "trigger_time": RECENT,
            }
        ]
    return OssFileChannel(sink=sink), [_meta()]


def test_the_source_system_stamp_cannot_be_spoofed_by_the_message():
    """``_source_system`` 是通道的事实，不是报文的声明——源报文改不了它。"""
    sink = InMemoryOdsSink()
    channel = CdcChannel(DEFAULT_CDC_BINDINGS[0], sink=sink)
    channel.run(
        [
            {
                "collect_task_id": "T-1",
                "task_status": "running",
                "_source_system": "伪造的来源",
                "_ingest_time": datetime(2000, 1, 1),
            }
        ],
        ingest_time=NOW,
    )
    row = sink.rows("ods_collect_task")[0]
    assert row["_source_system"] == "采集管理系统"
    assert row["_ingest_time"] == NOW


def test_stamp_refuses_an_empty_source_system_on_ods():
    with pytest.raises(ValueError, match="必须带 _source_system"):
        stamp_system_fields({"a": 1}, source_system="")


def test_oss_channel_is_idempotent_on_the_table_primary_key():
    """同一批数据重放两次不该产生重复行。OSS 通道的幂等键 = (file_id, file_type)。"""
    sink = InMemoryOdsSink()
    channel = OssFileChannel(sink=sink)
    first = channel.run([_meta()])
    second = channel.run([_meta()])
    assert first.accepted == 1 and first.duplicates == 0
    assert second.accepted == 0 and second.duplicates == 1
    assert len(sink.rows(FILE_TABLE)) == 1
    # 同一个 file_id 换一种 file_type 就是另一行（主键是两列）
    third = channel.run([_meta(file_type=FileType.VIDEO, object_key="collect/a.mp4")])
    assert third.accepted == 1
    assert len(sink.rows(FILE_TABLE)) == 2


def test_kafka_replay_from_the_last_offset_does_not_duplicate_events():
    """[a5]：「消费失败可从上次位点重新消费，不丢事件」——不丢的同时也不能多写。"""
    sink = InMemoryOdsSink()
    binding = KafkaBinding(
        topic="vehicle.trigger.event",
        target_table="ods_vehicle_trigger_event",
        source_system="车云平台",
        event_time_field="trigger_time",
        partition_field="trigger_type",
    )
    channel = KafkaChannel(binding, sink=sink)
    event = {"event_id": "E-1", "trigger_type": "aeb", "data_id": DATA_ID, "trigger_time": RECENT}
    channel.run([event, dict(event)])  # 同一批里重复投递
    replay = channel.run([dict(event)])  # 从上次位点重放
    assert len(sink.rows("ods_vehicle_trigger_event")) == 1
    assert replay.duplicates == 1


def test_cdc_without_a_version_field_does_not_deduplicate_updates():
    """CDC 读的是 binlog：同一主键本来就有多次合法变更，只按主键去重会丢数据。"""
    sink = InMemoryOdsSink()
    binding = CdcBinding(
        database="collect_platform",
        table="collect_task",
        target_table="ods_collect_task",
        source_system="采集管理系统",
        primary_key=("collect_task_id",),
    )
    channel = CdcChannel(binding, sink=sink)
    report = channel.run(
        [
            {"collect_task_id": "T-1", "task_status": "created"},
            {"collect_task_id": "T-1", "task_status": "running"},
        ]
    )
    assert report.duplicates == 0
    assert len(sink.rows("ods_collect_task")) == 2

    versioned = CdcChannel(
        CdcBinding(
            database="collect_platform",
            table="collect_task",
            target_table="ods_collect_task",
            source_system="采集管理系统",
            primary_key=("collect_task_id",),
            version_field="task_status",
        ),
        sink=InMemoryOdsSink(),
    )
    again = versioned.run(
        [
            {"collect_task_id": "T-1", "task_status": "created"},
            {"collect_task_id": "T-1", "task_status": "created"},
            {"collect_task_id": "T-1", "task_status": "running"},
        ]
    )
    assert again.duplicates == 1 and again.accepted == 2


def test_duplicate_rate_alert_fires_strictly_above_five_percent():
    """[a6] 4.2：「自动去重 **+ 超限告警**」，重复率 > 5% 才报，等于 5% 不报。"""
    binding = KafkaBinding(
        topic="vehicle.trigger.event",
        target_table="ods_vehicle_trigger_event",
        source_system="车云平台",
        event_time_field="trigger_time",
        partition_field="trigger_type",
    )

    def _events(total: int, dupes: int):
        rows = [
            {
                "event_id": f"E-{i}",
                "trigger_type": "aeb",
                "data_id": DATA_ID,
                "trigger_time": RECENT,
            }
            for i in range(total - dupes)
        ]
        return rows + [dict(rows[0]) for _ in range(dupes)]

    # 20 条里 1 条重复 = 5.0%，不超线
    quiet = AnomalyClosedLoop()
    KafkaChannel(binding, sink=InMemoryOdsSink(), closed_loop=quiet).run(_events(20, 1))
    assert quiet.alerts == []

    # 20 条里 2 条重复 = 10%，超线
    loud = AnomalyClosedLoop()
    report = KafkaChannel(binding, sink=InMemoryOdsSink(), closed_loop=loud).run(_events(20, 2))
    assert report.duplicate_rate == pytest.approx(0.1)
    assert report.duplicate_rate_exceeded
    assert loud.alerts and loud.alerts[-1].severity is Severity.P3
    assert "重复率" in loud.alerts[-1].message


def test_ods_write_is_retried_then_surfaced_not_swallowed():
    """写 ODS 失败可重试（幂等重放），但耗尽重试后必须抛，不能假装写成功。"""

    class _FlakySink:
        def __init__(self, fail_times: int) -> None:
            self.calls = 0
            self.fail_times = fail_times
            self.rows: list = []

        def write(self, table, rows):
            self.calls += 1
            if self.calls <= self.fail_times:
                raise RuntimeError("paimon busy")
            self.rows.extend(rows)
            return len(rows)

    flaky = _FlakySink(fail_times=2)
    report = OssFileChannel(sink=flaky).run([_meta()])
    assert report.write_attempts == constants.ODS_WRITE_MAX_ATTEMPTS == 3
    assert len(flaky.rows) == 1

    dead = _FlakySink(fail_times=99)
    with pytest.raises(SinkError, match="连续 3 次写入失败"):
        OssFileChannel(sink=dead).run([_meta()])


def test_big_files_stay_outside_the_lake():
    """[a5]：「大文件外置」——湖表只存路径与元信息，本体一个字节都不进来。"""
    row = _meta().to_ods_row()
    assert row["object_key"] == "collect/2026/03/01/F-0001.pcd"
    assert row["file_size_bytes"] == 41_943_040
    assert not any(isinstance(v, (bytes, bytearray)) for v in row.values())
    # file_path 是派生出来的完整 URI，本体按需读取
    assert _meta().file_path == "s3://adas-raw/collect/2026/03/01/F-0001.pcd"


# ===========================================================================
# 六、门禁：四项专属检查 + 三分支处置 + 与通用六维门禁的衔接
# ===========================================================================


def test_gate_three_branches():
    """[a5]：ERROR → 拒绝，WARNING → 带标放行，全过 → 通过。"""
    gate = OssComplianceGate()  # 无对象存储探针：两项 P1 降级为带标放行
    warned = gate.check(_meta())
    assert warned.decision is Decision.ACCEPT_WITH_WARNING
    assert warned.severity is Severity.P2
    assert {r.check.code for r in warned.warnings} == {
        "file_body_decodable",
        "meta_oss_path_consistent",
    }

    store = InMemoryObjectStore()
    body = b"# .PCD v0.7 - Point Cloud Data" + b"\0" * 10
    store.put("collect/2026/03/01/F-0001.pcd", body)
    ok = OssComplianceGate(store).check(_meta(file_size_bytes=len(body), checksum_md5=md5_of(body)))
    assert ok.decision is Decision.ACCEPT
    assert ok.severity is None

    bad = gate.check(_meta(marks=ComplianceMarks()))
    assert bad.decision is Decision.REJECT
    assert bad.severity is Severity.P0


def test_a_skipped_probe_is_not_a_failure():
    """探针不可用 ≠ 查出问题。否则离线回放时门禁会变成误杀源。"""
    probe = probe_decodable(_meta(), InMemoryObjectStore())
    assert probe.known is False and probe.decodable is False
    gate = OssComplianceGate()
    outcome = gate.check(_meta())
    assert outcome.accepted
    assert all(r.status is not CheckStatus.FAIL for r in outcome.results)


def test_warning_rows_are_let_in_and_the_missing_flag_column_is_reported():
    """[a5]：「带标记放行的数据写入 ``_quality_flag``，供下游按质量筛选，不阻塞主链路」。

    带标放行这一半是真的：数据照常入湖。另一半落不了地——共享契约里**没有任何一张表**
    有 ``_quality_flag`` 列（见返回结果里的契约缺列清单），投影时它会被丢掉。
    丢可以，丢得无人知晓不行：这一批丢了哪些列必须出现在 ``report.dropped_columns`` 里。
    """
    sink = InMemoryOdsSink()
    channel = OssFileChannel(sink=sink)
    report = channel.run([_meta()])
    assert report.warned == 1 and report.accepted == 1
    assert len(sink.rows(FILE_TABLE)) == 1  # 带标放行不阻塞主链路

    assert "_quality_flag" not in sink.rows(FILE_TABLE)[0]
    assert "_quality_flag" in report.dropped_columns, "质量标记被静默丢弃了"
    assert {"storage_class", "file_path"} <= report.dropped_columns


def test_decide_picks_the_highest_severity_among_failures():
    from adas_lakehouse.ingest import CheckResult

    p1 = CheckResult(OSS_CHANNEL_CHECKS[2], CheckStatus.FAIL, ("坏了",))
    p0 = CheckResult(OSS_CHANNEL_CHECKS[0], CheckStatus.FAIL, ("没脱敏",))
    assert decide([p1, p0]).severity is Severity.P0
    assert decide([p1]).severity is Severity.P1
    assert (
        decide([CheckResult(OSS_CHANNEL_CHECKS[1], CheckStatus.PASS)]).decision is Decision.ACCEPT
    )


def test_merging_gates_never_relaxes_a_rejection():
    """「通道可以分，门禁不能分」：挂上通用门禁只会更严，不会把已拦的放行。"""
    from adas_lakehouse.ingest import CheckResult, GateOutcome

    rejected = decide([CheckResult(OSS_CHANNEL_CHECKS[0], CheckStatus.FAIL, ("没脱敏",))])
    accepted = GateOutcome(Decision.ACCEPT, [CheckResult(OSS_CHANNEL_CHECKS[1], CheckStatus.PASS)])
    assert merge_outcomes(rejected, accepted).decision is Decision.REJECT
    assert merge_outcomes(accepted, rejected).decision is Decision.REJECT
    assert merge_outcomes(accepted, accepted).decision is Decision.ACCEPT


def test_default_channels_share_one_generic_gate():
    """默认装配就把通用六维门禁挂上，且三条通道共享同一个 QualityGate 实例。

    「通道可以分，门禁不能分」如果只写在注释里，装配时 generic_gate=None，
    那三条通道跑的其实是各自的专属规则——通用门禁成了没人调用的孤岛。
    """
    channels = build_default_channels()
    assert {c.kind for c in channels} == set(ChannelKind)
    assert all(c.generic_gate is not None for c in channels)
    gates = {id(c.generic_gate.quality_gate) for c in channels}
    assert len(gates) == 1, "三条通道各建了一个 QualityGate，灰度状态与指标会分家"

    # 显式关掉时只跑通道专属规则
    off = build_default_channels(generic_gate=None)
    assert all(c.generic_gate is None for c in off)


def test_generic_gate_maps_error_to_reject_and_warning_to_flagged():
    """quality 侧 ERROR → 拒绝入湖，WARNING → 带标放行，映射不能反。"""
    hook = unified_gate_hook(ChannelKind.OSS, table=FILE_TABLE)
    clean = _meta().to_row()
    clean.update(
        {
            "frame_group_modalities": "camera,lidar,radar,imu,gnss",
            "decodable_flag": True,
            "object_key": "oss://adas-raw/collect/a.pcd",
        }
    )
    results = hook(clean)
    assert all(r.status is not CheckStatus.FAIL for r in results), [
        r.describe() for r in results if r.status is CheckStatus.FAIL
    ]

    dirty = dict(clean)
    dirty["vehicle_desensitized_flag"] = False
    hits = unified_gate_hook(ChannelKind.OSS, table=FILE_TABLE)(dirty)
    failed = [r for r in hits if r.status is CheckStatus.FAIL]
    assert failed, "quality 侧 P0 规则没有映射成 FAIL"
    assert any(r.check.is_compliance for r in failed)
    assert all(r.check.dimension in QUALITY_DIMENSIONS for r in hits)


def test_unified_ingest_runs_every_fed_channel():
    """统一入湖入口：喂哪条通道跑哪条，不主动连外部系统。"""
    sink = InMemoryOdsSink()
    channels = build_default_channels(sink=sink, generic_gate=None)
    reports = unified_ingest(
        channels,
        feeds={
            "ods_collect_task": [{"collect_task_id": "T-1", "task_status": "running"}],
            FILE_TABLE: [_meta()],
        },
    )
    assert {r.target_table for r in reports} == {"ods_collect_task", FILE_TABLE}
    assert all(r.accepted == 1 for r in reports)


# ===========================================================================
# 七、OSS 侧零件：路径边界、校验和、可解码性
# ===========================================================================


def test_a_file_path_pointing_at_the_compliance_cloud_is_rejected_by_name():
    """跨域边界：file_path 指向合规云 bucket 不是「桶写错了」，是边界被越过。"""
    problems = validate_object_key(
        "s3://compliance-cloud-raw/collect/a.pcd",
        allowed_buckets=("adas-raw",),
        forbidden_buckets=("compliance-cloud-raw",),
    )
    assert any("合规云对象存储不对外暴露" in p for p in problems)

    pipeline = _pipeline()
    outcome = pipeline.submit(_meta(object_key="s3://compliance-cloud-raw/collect/a.pcd"))
    assert not outcome.accepted
    assert "合规云" in outcome.gate.reason_text()


@pytest.mark.parametrize(
    "uri",
    [
        "https://adas-raw.oss-cn-shanghai.aliyuncs.com/a.pcd",  # 公网直链
        "s3://adas-raw/../etc/passwd",  # 路径穿越
        "s3://another-bucket/a.pcd",  # 不在白名单
    ],
)
def test_illegal_file_paths_are_caught(uri):
    assert validate_object_key(uri, allowed_buckets=("adas-raw",))


def test_checksum_is_verifiable_three_ways():
    """[a8]：「checksum 让文件完整性随时可验证」。"""
    body = b"point-cloud-bytes"
    assert verify_checksum(md5_of(body), payload=body) == []
    assert verify_checksum(md5_of(body), payload=b"tampered")
    assert verify_checksum("") == ["checksum 缺失，文件完整性无法验证"]
    assert verify_checksum("not-an-md5")


@pytest.mark.parametrize(
    ("file_type", "body", "decodable"),
    [
        (FileType.POINTCLOUD, b"# .PCD v0.7\n" + b"\0" * 24, True),
        (FileType.POINTCLOUD, b"LASF" + b"\0" * 32, True),
        (FileType.POINTCLOUD, b"garbage-not-a-point-cloud-header", False),
        (FileType.VIDEO, b"\xff\xd8\xff\xe0" + b"\0" * 28, True),
        # ISO BMFF：前 4 字节是 box size，第 5~8 字节才是 ftyp
        (FileType.VIDEO, b"\x00\x00\x00\x20ftypisom" + b"\0" * 20, True),
        # 被截断成全零的坏文件不能因为「以三个零字节开头」就算可解码
        (FileType.VIDEO, b"\x00" * 32, False),
        # 原文未要求对传感器数据做解码校验，只查非空与大小一致
        (FileType.IMU, b"anything", True),
    ],
)
def test_decode_probe_judges_by_real_magic(file_type, body, decodable):
    store = InMemoryObjectStore()
    store.put("collect/probe.bin", body)
    meta = _meta(
        file_type=file_type,
        object_key="collect/probe.bin",
        file_size_bytes=len(body),
        checksum_md5=md5_of(body),
    )
    probe = probe_decodable(meta, store)
    assert probe.known is True
    assert probe.decodable is decodable, probe.reason


def test_decode_probe_catches_a_size_mismatch():
    store = InMemoryObjectStore()
    store.put("collect/probe.bin", b"# .PCD v0.7\n")
    probe = probe_decodable(_meta(object_key="collect/probe.bin", file_size_bytes=999), store)
    assert probe.known and not probe.decodable
    assert "文件大小不一致" in probe.reason


def test_probe_result_is_written_back_to_the_contract_column():
    """探针结论要落 ``decodable_flag`` 列，否则湖里这一列永远是 NULL。"""
    store = InMemoryObjectStore()
    body = b"# .PCD v0.7\n" + b"\0" * 8
    store.put("collect/2026/03/01/F-0001.pcd", body)
    sink = InMemoryOdsSink()
    channel = OssFileChannel(gate=OssComplianceGate(store), sink=sink)
    channel.run([_meta(file_size_bytes=len(body), checksum_md5=md5_of(body))])
    assert sink.rows(FILE_TABLE)[0]["decodable_flag"] is True


@pytest.mark.parametrize(
    "data_id",
    ["", "NOT-A-DATA-ID", "TRAIN_BP_20260301123045_b7e2", "COLLECT_BP_2026_b7e2"],
)
def test_data_id_gate_is_the_lineage_entry_point(data_id):
    """[a8]：「不符合 ID 规范的数据根本没有血缘追溯的起点」。"""
    assert validate_data_id(data_id)
    assert validate_data_id(DATA_ID) == []


# ===========================================================================
# 八、生产形态的 SQL：与 Python 侧同一套口径
# ===========================================================================


def test_generated_sql_stamps_the_system_fields_on_every_insert():
    """三条通道的 INSERT 都要盖 _ingest_time + _source_system。"""
    statements = [
        *render_cdc_pipeline(DEFAULT_CDC_BINDINGS[0]),
        *render_kafka_pipeline(
            KafkaBinding(
                topic="t",
                target_table="ods_production_kafka_event",
                source_system="产线埋点",
                event_time_field="event_time",
                partition_field="event_type",
            )
        ),
        *render_file_meta_pipeline(),
    ]
    inserts = [s for s in statements if "INSERT INTO" in s]
    assert inserts
    for statement in inserts:
        for block in statement.split("INSERT INTO")[1:]:
            assert "`_ingest_time`" in block, statement
            assert "`_source_system`" in block, statement


def test_cdc_sql_expresses_the_three_phases():
    """[a5]：「全量快照 → 增量 binlog → 断点续传」三阶段 + 业务库零侵入。"""
    ddl, insert = render_cdc_pipeline(DEFAULT_CDC_BINDINGS[0])
    assert "'scan.startup.mode' = 'initial'" in ddl  # 快照→增量
    assert "'scan.incremental.snapshot.enabled' = 'true'" in ddl  # 无锁读、零侵入
    assert "'connector' = 'mysql-cdc'" in ddl
    assert "${CDC_MYSQL_PASSWORD}" in ddl  # 口令不落盘
    from adas_lakehouse.ingest.sql import render_catalog_script

    assert "execution.checkpointing.interval" in render_catalog_script()  # 断点续传


def test_kafka_sql_keeps_the_replay_capability():
    """[a5]：「消费失败可从上次位点重新消费，不丢事件」。"""
    ddl, _ = render_kafka_pipeline(
        KafkaBinding(
            topic="vehicle.trigger.event",
            target_table="ods_vehicle_trigger_event",
            source_system="车云平台",
            event_time_field="trigger_time",
            partition_field="trigger_type",
        )
    )
    assert "'scan.startup.mode' = 'group-offsets'" in ddl
    assert "'json.ignore-parse-errors' = 'false'" in ddl  # 脏消息不静默跳过
    assert "specific-offsets" in ddl and "timestamp" in ddl  # 重放历史的写法


def test_oss_sql_checks_match_the_python_gate_one_for_one():
    """SQL 侧四项检查的编码 / 级别必须与 gate.OSS_CHANNEL_CHECKS 完全一致。"""
    _, view, statement_set, _ = render_file_meta_pipeline()
    for check in OSS_CHANNEL_CHECKS:
        assert f"`chk_{check.code}`" in view
        assert f"'{check.code}'" in statement_set
        assert f"[{check.severity.value}] {check.name}" in statement_set
        assert f"'{DIMENSION_KEYS[check.dimension]}'" in statement_set
    # 条件表达式只写一次：判定都在视图里
    assert view.count("REGEXP(`data_id`") == 1
    assert statement_set.count("REGEXP(`data_id`") == 0


def test_oss_sql_isolation_branch_only_writes_registered_columns():
    """隔离分支的列清单必须来自契约，且 INSERT 显式写列名。

    自造列名（subject_id / check_codes / issue_reason / closed_loop_step）在
    ods_quality_issue 里一个都没有——作业提交即失败，被拦的数据一条都进不了隔离表。
    """
    _, _, statement_set, _ = render_file_meta_pipeline()
    head = statement_set.split(f"INSERT INTO `paimon`.`adas_lakehouse`.`{QUALITY_ISSUE_TABLE}`")[1]
    col_list = head[head.index("(") + 1 : head.index(")")]
    columns = [c.strip().strip("`") for c in col_list.split(",")]
    registered = set(registered_columns(QUALITY_ISSUE_TABLE) or ())
    assert columns, "隔离分支没有显式列清单"
    assert set(columns) <= registered, f"契约里没有这些列: {set(columns) - registered}"
    for invented in ("subject_id", "check_codes", "issue_reason", "closed_loop_step"):
        assert invented not in statement_set
    # 两套同义列名都填上了
    for pair in (
        ("source_record_key", "record_key"),
        ("rule_id", "rule_ids"),
        ("issue_detail", "detail"),
        ("isolate_time", "detected_at"),
    ):
        assert set(pair) <= set(columns), pair


def test_oss_sql_isolates_the_whole_payload_not_just_an_id():
    """「携带原始报文」是原文原话——隔离表里存的必须是整条报文。"""
    _, _, statement_set, _ = render_file_meta_pipeline()
    payload_block = statement_set[
        statement_set.index("JSON_OBJECT(") : statement_set.index("AS `raw_payload`")
    ]
    for column, *_ in file_meta_message_schema():
        assert f"KEY '{column}' VALUE `{column}`" in payload_block, column


def test_oss_sql_source_table_is_derived_from_the_lake_table():
    """源表字段由湖表业务列派生：契约加列，源表自动跟上，SELECT 不会引用空气。"""
    ddl, _, statement_set, _ = render_file_meta_pipeline()
    lake_cols = [c for c in (registered_columns(FILE_TABLE) or ()) if not c.startswith("_")]
    for column in lake_cols:
        assert f"`{column}`" in ddl, column
        assert f"`{column}`" in statement_set, column
    # 源表列一律可空：脏消息交给门禁拦，不在反序列化阶段把作业打挂
    assert "NOT NULL" not in ddl


def test_no_line_comment_leaks_into_a_sql_condition():
    """条件表达式里出现 ``--`` 会把后面的右括号和分号一起吃掉。"""
    for statement in render_file_meta_pipeline():
        for line in statement.splitlines():
            stripped = line.strip()
            if stripped.startswith("--"):
                continue
            assert "--" not in line, line


# ===========================================================================
# 九、契约边界：进不了湖的字段必须被说出来
# ===========================================================================


def test_fields_without_a_contract_column_are_listed_not_silently_dropped():
    """[a8] 点名的 storage_class 在共享契约里没有列——不能悄悄丢，要报出来。"""
    off_table = _meta().off_table_fields()
    assert "storage_class" in off_table
    assert "file_path" in off_table
    # 但门禁真正依赖的判据列都在契约里，不会被投影掉
    projected = _meta().to_ods_row()
    for column in (
        "vehicle_desensitized_flag",
        "cloud_compliance_decrypted_flag",
        "decodable_flag",
    ):
        assert column in projected


def test_pipeline_reports_off_table_fields_on_every_outcome():
    pipeline = _pipeline()
    outcome = pipeline.submit(_meta())
    assert outcome.accepted
    assert "storage_class" in outcome.off_table_fields


def test_the_generic_gate_can_still_stop_a_row_the_channel_rules_let_through():
    """「通道可以分，门禁不能分」的端到端形态：通道专属四项过了，通用六维照样能拦。

    这条挂的是真的 ``adas_lakehouse.quality`` 规则集，不是替身——桥接接不上的话，
    通用门禁就是个没人调用的孤岛。
    """
    sink, issues = InMemoryOdsSink(), InMemoryOdsSink()
    from adas_lakehouse.ingest.channels import isolating_closed_loop

    channel = OssFileChannel(
        sink=sink,
        closed_loop=isolating_closed_loop(issues),
        generic_gate=unified_gate_hook(ChannelKind.OSS, table=FILE_TABLE),
    )
    # 四项专属检查全过（双标记齐、data_id 合法、探针不可用降级带标放行），
    # 但帧组里少了激光雷达——通用规则 QG-OSS-002 多模态完整性判 P0 拒绝。
    report = channel.run(
        [_meta(frame_group_modalities="camera,radar,imu,gnss", decodable_flag=True)]
    )
    assert report.rejected == 1 and report.accepted == 0
    assert sink.rows(FILE_TABLE) == []
    row = issues.rows(QUALITY_ISSUE_TABLE)[-1]
    assert "QG-OSS-002" in row["rule_ids"]
    assert row["issue_level"] == "P0"

    # 同一条数据补齐模态就放行——拦的是缺帧，不是这条通道
    ok = channel.run(
        [
            _meta(
                file_id="F-0002",
                frame_group_modalities="camera,lidar,radar,imu,gnss",
                decodable_flag=True,
            )
        ]
    )
    assert ok.accepted == 1


def test_a_missing_decodable_flag_is_a_missing_criterion_not_a_free_pass():
    """通用门禁的 QG-OSS-006 在没有探针、上游也没给 ``decodable_flag`` 时判 P1 拒绝。

    这不是误杀：契约把该列写成「上游解码探针的结论，随元信息落表」，列是空的就等于
    「文件能不能解码」这件事**没人回答过**。本通道的责任是把探针结论回填到这一列
    （见 ``OssFileChannel.post_gate``），而不是让下游按 NULL 猜。
    """
    hook = unified_gate_hook(ChannelKind.OSS, table=FILE_TABLE)
    blank = _meta(frame_group_modalities="camera,lidar,radar,imu,gnss").to_row()
    assert blank["decodable_flag"] is None
    failed = [r for r in hook(blank) if r.status is CheckStatus.FAIL]
    assert [r.check.code for r in failed] == ["QG-OSS-006-file-decodable"]
    assert failed[0].check.severity is Severity.P1

    blank["decodable_flag"] = True
    assert [r for r in hook(blank) if r.status is CheckStatus.FAIL] == []


def test_one_clip_one_chain_for_the_hundreds_of_files_in_a_collect_task():
    """[a8]「一个采集任务数百个文件」：一个 clip 的多个文件共享同一条五步链路。

    硬盘只被脱敏、上传、脱密、分发一次，链路不该因为第二个文件被要求重走一遍。
    """
    pipeline = _pipeline()
    metas = [
        _meta(file_id="F-CAM", file_type=FileType.VIDEO, object_key="collect/a.mp4"),
        _meta(file_id="F-LID", file_type=FileType.POINTCLOUD, object_key="collect/a.pcd"),
        _meta(file_id="F-IMU", file_type=FileType.IMU, object_key="collect/a.bin"),
    ]
    outcomes = pipeline.submit_batch(metas)
    assert all(o.accepted for o in outcomes), [o.summary() for o in outcomes]
    assert pipeline.stats()["chains"] == 1
    assert pipeline.stats()["completed"] == 1
    assert len(pipeline.sink.rows(FILE_TABLE)) == 3

    # 逐条 submit 的默认口径仍然保守：链路走完后不许再推第 ⑤ 步
    with pytest.raises(ComplianceViolation, match="不可重复推进"):
        pipeline.submit(_meta(file_id="F-GPS", file_type=FileType.GPS))


def test_one_shared_sink_puts_both_tables_in_the_same_lake():
    """生产接线：目标表与隔离表同属一个 Paimon catalog，传同一个 sink 即可。"""
    lake = InMemoryOdsSink()
    pipeline = ComplianceIngestPipeline(topology=TOPOLOGY, sink=lake, issue_sink=lake)
    pipeline.submit(_meta())
    pipeline.submit(_meta(file_id="F-BAD", data_id="not-a-data-id"))
    assert len(lake.rows(FILE_TABLE)) == 1
    assert lake.rows(QUALITY_ISSUE_TABLE)
    assert set(lake.tables) == {FILE_TABLE, QUALITY_ISSUE_TABLE}


def test_stats_separate_compliance_issues_from_quality_issues():
    pipeline = _pipeline()
    pipeline.submit(_meta())
    pipeline.submit(_meta(file_id="F-2", data_id="bad-id"))
    pipeline.submit(
        _meta(file_id="F-3", data_id="COLLECT_BP_20260301123045_0002", marks=ComplianceMarks())
    )
    stats = pipeline.stats()
    assert stats["chains"] == 3
    assert stats["completed"] == 1
    assert stats["intercepted"] == 2
    assert stats["compliance_issues"] == 1  # 只有缺脱敏那条算合规问题


def test_source_wording_is_transcribed_verbatim():
    """量级措辞也是原文的一部分：改成「约 50TB」这种「合理值」就不是原文了。"""
    assert constants.COLLECT_PROJECT_VOLUME_TEXT == "数十 TB"  # [a8] 一个采集项目动辄数十 TB
    assert constants.LIDAR_FRAME_SIZE_TEXT == "几十 MB"  # [a8] 单帧点云几十 MB
    assert constants.FILES_PER_COLLECT_TASK_TEXT == "数百个文件"  # [a8] 一个采集任务数百个文件
    assert constants.COLLECT_FILE_VOLUME_TEXT == "几十 TB"  # [a5] 几十 TB 的采集大文件
    assert constants.BIG_FILE_SIZE_TEXT == "几十 GB"  # [a5] 图像、点云这类几十 GB 的文件
    # [a8] 第五章「三个字段值得注意」
    assert constants.OSS_META_HIGHLIGHT_FIELDS == ("data_id", "checksum", "storage_class")
    # [a5] 第六章大文件外置后湖仓保留的四项
    assert constants.EXTERNALIZED_FILE_META_FIELDS == (
        "文件大小",
        "脱敏标记",
        "校验和",
        "归属 data_id",
    )
    # [a6] 第五章 ③ 分级告警的通知对象
    assert constants.ALERT_NOTIFY_TARGETS == ("数据 owner", "平台值班")


@pytest.mark.parametrize("kind", list(ChannelKind))
def test_all_three_channels_reject_into_the_same_isolation_table(kind):
    """「通道可以分，门禁不能分」在被拒一侧同样成立：三条通道进同一张隔离表。"""
    from adas_lakehouse.ingest.channels import isolating_closed_loop

    issues = InMemoryOdsSink()
    loop = isolating_closed_loop(issues)
    sink = InMemoryOdsSink()
    if kind is ChannelKind.CDC:
        channel = CdcChannel(DEFAULT_CDC_BINDINGS[0], sink=sink, closed_loop=loop)
        bad = [{"collect_task_id": "", "task_status": "running"}]  # 主键为空
    elif kind is ChannelKind.KAFKA:
        channel = KafkaChannel(
            KafkaBinding(
                topic="vehicle.trigger.event",
                target_table="ods_vehicle_trigger_event",
                source_system="车云平台",
                event_time_field="trigger_time",
                partition_field="trigger_type",
            ),
            sink=sink,
            closed_loop=loop,
        )
        bad = [
            {"event_id": "E-1", "trigger_type": "aeb", "trigger_time": RECENT + timedelta(days=2)}
        ]
    else:
        channel = OssFileChannel(sink=sink, closed_loop=loop)
        bad = [_meta(data_id="not-a-data-id")]

    report = channel.run(bad)
    assert report.rejected == 1 and sink.rows(channel.target_table) == []
    rows = issues.rows(QUALITY_ISSUE_TABLE)
    assert len(rows) >= 1
    assert rows[-1]["source_channel"] == kind.issue_code
    assert rows[-1]["target_table"] == channel.target_table
    assert rows[-1]["raw_payload"]


def test_a_broken_cloud_boundary_also_lands_in_the_isolation_table():
    """第 ② / ④ 步的架构边界被越过时，数据同样要进隔离表，而不是只抛一个异常给调用方。

    编排入口 ``submit()`` 是唯一会被业务调用的路径；它吞掉 ``ComplianceViolation``
    之后如果不补隔离，这条数据就只活在返回值里——没人告警、没处可查、没法复验。
    """
    import dataclasses

    exposed = dataclasses.replace(TOPOLOGY, object_store_public_endpoint=True)
    sink, issues = InMemoryOdsSink(), InMemoryOdsSink()
    pipeline = ComplianceIngestPipeline(topology=exposed, sink=sink, issue_sink=issues)

    outcome = pipeline.submit(_meta())
    assert not outcome.accepted
    assert "不对外暴露" in outcome.error
    assert sink.rows(FILE_TABLE) == []

    row = issues.rows(QUALITY_ISSUE_TABLE)[-1]
    assert row["rule_ids"] == "compliance_chain_incomplete"
    assert row["issue_level"] == "P0"
    assert json.loads(row["raw_payload"])["file_id"] == "F-0001"
    assert pipeline.closed_loop.alerts[-1].is_compliance is True
    # 合规问题不接受「复验放行」，只能退回合规云重做
    with pytest.raises(ValueError, match="不得复验放行"):
        pipeline.closed_loop.recheck(outcome.anomaly, passed=True)
