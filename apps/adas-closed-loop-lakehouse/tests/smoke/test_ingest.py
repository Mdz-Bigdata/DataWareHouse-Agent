"""冒烟：三通道入湖 + 合规入湖链路。

主流程：五步合规链路 → OSS 四项门禁 → 落 ods_data_file_meta。
全程用 InMemoryOdsSink / InMemoryObjectStore 顶替外部组件，不装 boto3 / kafka-python。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from adas_lakehouse.ingest import (
    ChannelKind,
    ComplianceCloudTopology,
    ComplianceIngestPipeline,
    ComplianceMarks,
    CrossBoundaryPayload,
    FileMeta,
    FileType,
    InMemoryObjectStore,
    InMemoryOdsSink,
    RedactionStage,
    assert_may_cross_boundary,
    build_default_channels,
    md5_of,
    normalize_file_type,
    parse_object_uri,
    validate_object_key,
    verify_checksum,
)
from adas_lakehouse.ingest.errors import ComplianceViolation

pytestmark = pytest.mark.smoke

DATA_ID = "COLLECT_BP_20260301123045_b7e2"
SAME_REGION = ComplianceCloudTopology(
    compliance_cloud_region="cn-shanghai",
    adas_cloud_region="cn-shanghai",
    compliance_cloud_vpc_id="vpc-adas-01",
    adas_cloud_vpc_id="vpc-adas-01",
)


def _meta(**overrides) -> FileMeta:
    payload = {
        "file_id": "F-0001",
        "file_type": FileType.POINTCLOUD,
        "data_id": DATA_ID,
        "object_key": "collect/2026/03/01/F-0001.pcd",
        "file_size_bytes": 41_943_040,
        "checksum_md5": "0" * 32,
        "marks": ComplianceMarks.both_applied(
            vehicle_operator="采集软件 v2.3", cloud_operator="XX 合规科技"
        ),
    }
    payload.update(overrides)
    return FileMeta(**payload)


# --------------------------------------------------------------------------- 主流程


def test_compliance_pipeline_accepts_a_desensitized_file():
    sink = InMemoryOdsSink()
    pipeline = ComplianceIngestPipeline(topology=SAME_REGION, sink=sink)

    outcome = pipeline.submit(_meta())

    assert outcome.accepted
    assert DATA_ID in outcome.summary()
    assert sink.total() == 1

    stats = pipeline.stats()
    assert stats["chains"] == 1
    assert stats["completed"] == 1
    assert stats["intercepted"] == 0
    assert stats["compliance_issues"] == 0


def test_ingested_row_carries_the_ods_system_fields():
    sink = InMemoryOdsSink()
    pipeline = ComplianceIngestPipeline(topology=SAME_REGION, sink=sink)
    pipeline.submit(_meta(), ingest_time=datetime(2026, 3, 1, 13, 0, 0))

    rows = sink.rows("ods_data_file_meta")
    assert len(rows) == 1
    row = rows[0]
    assert row["data_id"] == DATA_ID
    # 三通道共用同一套系统字段：ODS 层是 _ingest_time + _source_system
    assert row["_ingest_time"] is not None
    assert row["_source_system"]


def test_chain_is_keyed_by_data_id_and_refuses_replay():
    """一个 data_id 一条合规链路；链路走完后不许重放第 5 步（可重放≠可重复入湖）。"""
    pipeline = ComplianceIngestPipeline(topology=SAME_REGION, sink=InMemoryOdsSink())
    first = pipeline.submit(_meta(file_id="F-0001"))
    assert first.accepted
    assert pipeline.stats()["chains"] == 1

    chain = pipeline.chain_for(DATA_ID)
    assert chain.is_complete
    with pytest.raises(ComplianceViolation, match="不可重复推进"):
        pipeline.submit(_meta(file_id="F-0002"))
    assert pipeline.stats()["chains"] == 1


# --------------------------------------------------------------------------- 合规红线


def test_undesensitized_file_is_intercepted():
    """双脱敏缺一不可：车端脱敏 + 合规云脱密，缺任何一个都不许入湖。"""
    sink = InMemoryOdsSink()
    pipeline = ComplianceIngestPipeline(topology=SAME_REGION, sink=sink)

    outcome = pipeline.submit(_meta(marks=ComplianceMarks()))

    assert not outcome.accepted
    assert sink.total() == 0
    assert "车端脱敏" in outcome.summary()
    # 链路卡在第 1 步，没有走完——留痕在审计行里，等人工补脱敏后重放
    stats = pipeline.stats()
    assert stats["completed"] == 0
    assert stats["in_flight"] == 1
    audit = pipeline.audit_rows()
    assert audit[0]["compliance_chain_complete"] is False
    assert audit[0]["redaction_vehicle_applied"] is False


def test_marks_report_the_missing_stage():
    marks = ComplianceMarks()
    missing = set(marks.missing_stages())
    assert missing == set(RedactionStage)
    assert not marks.is_complete
    assert marks.validate()

    both = ComplianceMarks.both_applied(vehicle_operator="车端", cloud_operator="合规云")
    assert both.is_complete
    assert both.missing_stages() == ()
    assert both.validate() == []


def test_cross_region_topology_is_rejected():
    """合规云与智驾云必须同地域同 VPC，否则跨境/跨域传输不合规。"""
    split = ComplianceCloudTopology(
        compliance_cloud_region="cn-shanghai",
        adas_cloud_region="cn-beijing",
        compliance_cloud_vpc_id="vpc-a",
        adas_cloud_vpc_id="vpc-b",
    )
    assert split.validate()
    with pytest.raises(ComplianceViolation):
        split.assert_valid()


def test_raw_payload_may_not_cross_the_boundary():
    """只有脱敏后的产物能过境；原始数据本体一律不许。"""
    crossed, blocked = [], []
    for payload in CrossBoundaryPayload:
        (crossed if payload.may_cross else blocked).append(payload)
    assert crossed and blocked

    for payload in crossed:
        assert_may_cross_boundary(payload)
    for payload in blocked:
        with pytest.raises(ComplianceViolation):
            assert_may_cross_boundary(payload)


# --------------------------------------------------------------------------- OSS 门禁零件


def test_object_uri_parsing_and_key_validation():
    uri = parse_object_uri("s3://adas-raw/collect/2026/03/01/F-0001.pcd")
    assert uri.bucket == "adas-raw"
    assert uri.key.endswith("F-0001.pcd")
    assert validate_object_key("s3://adas-raw/collect/2026/03/01/F-0001.pcd") == []
    assert validate_object_key("s3://adas-raw/../etc/passwd")


def test_checksum_gate_catches_a_corrupted_payload():
    payload = b"point-cloud-bytes"
    assert verify_checksum(md5_of(payload), payload=payload) == []
    assert verify_checksum(md5_of(payload), payload=b"tampered")


def test_in_memory_object_store_round_trip():
    store = InMemoryObjectStore()
    stat = store.put("collect/x.pcd", b"0123456789")
    assert stat.size_bytes == 10
    assert store.head("collect/x.pcd") is not None
    assert store.head("collect/missing.pcd") is None
    assert store.read_range("collect/x.pcd", 4) == b"0123"


def test_file_type_aliases_normalize():
    assert normalize_file_type("pcd") is FileType.POINTCLOUD
    with pytest.raises(ValueError):
        normalize_file_type("no-such-type")


# --------------------------------------------------------------------------- 三通道


def test_default_channels_cover_cdc_kafka_and_oss():
    """三条通道各有承接数据与入湖方式，但共用同一套门禁——通道可以分，门禁不能分。"""
    channels = build_default_channels()
    kinds = {c.kind for c in channels}
    assert kinds == set(ChannelKind)
    for kind in ChannelKind:
        assert kind.label and kind.payload and kind.mechanism
