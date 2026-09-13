"""深度对账：传感器时间同步（±10ms / ±50ms）、整帧完整性、五步质量门禁。

对账基准是原文——系列二 · 湖仓实战 第 6 篇《数据质量门禁设计：智驾数据入湖的
五步校验链路》（公众号「小周谈智驾数据闭环」，2026-09-05）。

本文件的断言纪律：**打在原文给的具体数字上**，不满足于「函数能跑通」。
原文原话逐条对应到下面的测试名：

    4.3 时间同步    采集车硬件同步 ≤ ±10ms，量产车软同步 ≤ ±50ms，超限标记
                    → 超限告警放行（L1）
    4.3 多模态完整性 同一帧组内相机 / 激光雷达 / 毫米波 / IMU / GNSS 文件齐全，
                    连续丢帧率 > 1% 升级告警 → 缺帧拒绝（L1）
    五   五步闭环    拦截 → 隔离 → 告警 → 分流处置 → 复验；不通过退回隔离，超 3 轮升级 P0
    五   四档 SLA    P0 电话+钉钉 30 分钟 / P1 钉钉+工单 2 小时当日修复 /
                    P2 日报 3 个工作日 / P3 周报 连续两周超标升级 P2
    六   门禁自监控  rejected_records_count > 100 WARNING；quality_check_duration > 1000 WARNING
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from adas_lakehouse.quality import (
    BUILTIN_RULES,
    Channel,
    ClosedLoop,
    CollectingAlertSink,
    Disposition,
    GateMetrics,
    InMemoryIssueStore,
    IssueLevel,
    IssueStatus,
    QualityGate,
    RepairAction,
    Rollout,
    RolloutMode,
    RuleCenter,
    Severity,
    default_rule_center,
)
from adas_lakehouse.quality.alerting import AlertRouter
from adas_lakehouse.quality.builtin import (
    MODALITIES,
    VEHICLE_TYPE_COLLECT,
    VEHICLE_TYPE_PRODUCTION,
)
from adas_lakehouse.quality.metrics import (
    METRIC_QUALITY_CHECK_DURATION,
    METRIC_REJECTED_RECORDS_COUNT,
)
from adas_lakehouse.quality.severity import QualityLayer
from adas_lakehouse.quality.thresholds import (
    COLLECT_VEHICLE_SYNC_TOLERANCE_MS,
    CONTINUOUS_FRAME_LOSS_RATE_ESCALATE_THRESHOLD,
    DUPLICATE_RATE_ALERT_THRESHOLD,
    MAX_RECHECK_ROUNDS,
    P0_RESPONSE_MINUTES,
    P1_RESPONSE_HOURS,
    P2_CLOSE_BUSINESS_DAYS,
    P3_CONSECUTIVE_WEEKS_TO_ESCALATE,
    PRODUCTION_VEHICLE_SYNC_TOLERANCE_MS,
    QUALITY_CHECK_DURATION_MS_THRESHOLD,
    REJECTED_RECORDS_COUNT_THRESHOLD,
)

FILE_TABLE = "ods_data_file_meta"
EVENT_TABLE = "ods_vehicle_trigger_event"
NOW = datetime(2026, 3, 1, 12, 0, 0)
DATA_ID = "COLLECT_BP_20260301120000_ab12"

#: 一条各项体检全过的采集文件元信息。测哪一项就只改哪一项，避免连带命中别的规则。
_CLEAN_FILE = {
    "data_id": DATA_ID,
    "file_id": "FILE-0001",
    "file_type": "video",
    "object_key": "oss://adas-raw/clip/0001.mp4",
    "file_size_bytes": 1024,
    "checksum_md5": "d41d8cd98f00b204e9800998ecf8427e",
    "decodable_flag": True,
    "frame_group_modalities": "camera,lidar,radar,imu,gnss",
    "vehicle_desensitized_flag": True,
    "cloud_compliance_decrypted_flag": True,
}


def _file(**overrides) -> dict:
    row = dict(_CLEAN_FILE)
    row.update(overrides)
    return row


def _gate(store: InMemoryIssueStore | None = None, **kwargs) -> QualityGate:
    return QualityGate(
        store=store if store is not None else InMemoryIssueStore(),
        source_system="oss:adas-raw",
        clock=lambda: NOW,
        **kwargs,
    )


def _check_file(**overrides):
    return _gate().check(FILE_TABLE, _file(**overrides), channel=Channel.OSS_FILE)


def _hit_ids(decision) -> set[str]:
    return {h.rule_id for h in decision.hits}


# =========================================================================== A 参数逐字
# 原文出现过的每一个数字，先在常量层对一遍，再在规则层对一遍——
# 常量对了但规则没引用它，等于没落地。


def test_source_numbers_are_transcribed_verbatim():
    """原文第四 / 五 / 六章的全部数字，一个不改地落在 thresholds 里。"""
    # 4.3 时间同步
    assert COLLECT_VEHICLE_SYNC_TOLERANCE_MS == 10  # 采集车硬件同步 ≤ ±10ms
    assert PRODUCTION_VEHICLE_SYNC_TOLERANCE_MS == 50  # 量产车软同步 ≤ ±50ms
    # 4.3 多模态完整性 / 4.2 重复率监控
    assert CONTINUOUS_FRAME_LOSS_RATE_ESCALATE_THRESHOLD == 0.01  # 连续丢帧率 > 1%
    assert DUPLICATE_RATE_ALERT_THRESHOLD == 0.05  # 重复率 > 5%
    # 五、⑤ 复验重入湖
    assert MAX_RECHECK_ROUNDS == 3  # 超 3 轮升级 P0
    # 五、四档响应 SLA
    assert P0_RESPONSE_MINUTES == 30  # 电话 + 钉钉，30 分钟内响应
    assert P1_RESPONSE_HOURS == 2  # 钉钉 + 工单，2 小时内响应
    assert P2_CLOSE_BUSINESS_DAYS == 3  # 日报汇总，3 个工作日内闭环
    assert P3_CONSECUTIVE_WEEKS_TO_ESCALATE == 2  # 周报汇总，连续两周超标升级 P2
    # 六、门禁运维两类核心指标
    assert REJECTED_RECORDS_COUNT_THRESHOLD == 100
    assert QUALITY_CHECK_DURATION_MS_THRESHOLD == 1000


def test_rules_carry_the_literal_thresholds_not_rounded_copies():
    """规则的 params 必须就是原文那个数，不是「差不多的合理值」。"""
    rules = {r.rule_id: r for r in BUILTIN_RULES}
    assert rules["QG-OSS-004-time-sync-collect-vehicle"].params["max_abs"] == 10
    assert rules["QG-OSS-005-time-sync-production-vehicle"].params["max_abs"] == 50
    assert rules["QG-OSS-003-continuous-frame-loss-rate"].params["max_ratio"] == 0.01
    assert rules["QG-OSS-012-batch-frame-loss"].params["max_ratio"] == 0.01
    assert rules["QG-KFK-006-duplicate-rate"].params["max_ratio"] == 0.05
    # 五类模态逐字：相机 / 激光雷达 / 毫米波 / IMU / GNSS
    assert MODALITIES == ("camera", "lidar", "radar", "imu", "gnss")
    assert rules["QG-OSS-002-multimodal-completeness"].params["members"] == list(MODALITIES)


def test_sla_due_times_follow_the_four_tier_table():
    """四档 SLA 的到期时间按原文表格算，不是「随便给个时限」。"""
    p0 = IssueLevel.P0.policy
    assert p0.notify_channels == ("电话", "钉钉")
    assert p0.response_due_at(NOW) == NOW + timedelta(minutes=30)
    assert p0.closure_due_at(NOW) is None  # 原文只给了响应时限

    p1 = IssueLevel.P1.policy
    assert p1.notify_channels == ("钉钉", "工单")
    assert p1.response_due_at(NOW) == NOW + timedelta(hours=2)
    # 「当日修复」= 当天 23:59:59 之前，而不是 +24 小时
    assert p1.closure_due_at(NOW) == datetime(2026, 3, 1, 23, 59, 59)

    p2 = IssueLevel.P2.policy
    assert p2.notify_channels == ("日报",)
    assert p2.closure_due_at(NOW) == NOW + timedelta(days=3)

    p3 = IssueLevel.P3.policy
    assert p3.notify_channels == ("周报",)
    assert "连续两周超标升级 P2" in p3.response_requirement_cn


# =================================================== B 传感器时间同步误差 ±10ms / ±50ms


@pytest.mark.parametrize("error_ms", [0, 10, -10, 9.9])
def test_collect_vehicle_within_10ms_passes(error_ms):
    """采集车硬件同步 ≤ ±10ms：边界值 ±10ms 本身算通过（原文写的是「≤」）。"""
    decision = _check_file(vehicle_type=VEHICLE_TYPE_COLLECT, time_sync_error_ms=error_ms)
    assert decision.disposition is Disposition.ACCEPTED
    assert decision.hits == ()


@pytest.mark.parametrize("error_ms", [11, -11, 10.5, 5000])
def test_collect_vehicle_beyond_10ms_is_flagged_but_let_in(error_ms):
    """超 ±10ms → 超限标记放行（L1），不是拒绝入湖。"""
    decision = _check_file(vehicle_type=VEHICLE_TYPE_COLLECT, time_sync_error_ms=error_ms)
    assert decision.disposition is Disposition.ALLOW_WITH_FLAG
    assert decision.accepted and not decision.rejected
    assert "QG-OSS-004-time-sync-collect-vehicle" in _hit_ids(decision)
    hit = next(h for h in decision.hits if h.rule_id.startswith("QG-OSS-004"))
    assert hit.severity is Severity.WARNING
    assert hit.issue_level is IssueLevel.P2  # 五章 SLA 表：「时间同步超限」属 P2 一般
    assert hit.quality_layer is QualityLayer.L1_SENSOR
    assert "QG-OSS-004-time-sync-collect-vehicle" in decision.quality_flag


@pytest.mark.parametrize("error_ms", [0, 50, -50, 49.9])
def test_production_vehicle_within_50ms_passes(error_ms):
    decision = _check_file(vehicle_type=VEHICLE_TYPE_PRODUCTION, time_sync_error_ms=error_ms)
    assert decision.disposition is Disposition.ACCEPTED


@pytest.mark.parametrize("error_ms", [51, -51, 50.5])
def test_production_vehicle_beyond_50ms_is_flagged(error_ms):
    decision = _check_file(vehicle_type=VEHICLE_TYPE_PRODUCTION, time_sync_error_ms=error_ms)
    assert decision.disposition is Disposition.ALLOW_WITH_FLAG
    assert "QG-OSS-005-time-sync-production-vehicle" in _hit_ids(decision)


def test_the_two_tolerances_are_not_interchangeable():
    """30ms 这个值把两档容差分开：采集车超限、量产车合格。

    两条规则要是共用一个阈值（或者 when 写串了），这条就会红——
    「分车型判定」是原文的原话，不是可以合并掉的实现细节。
    """
    collect = _check_file(vehicle_type=VEHICLE_TYPE_COLLECT, time_sync_error_ms=30)
    production = _check_file(vehicle_type=VEHICLE_TYPE_PRODUCTION, time_sync_error_ms=30)
    assert _hit_ids(collect) == {"QG-OSS-004-time-sync-collect-vehicle"}
    assert production.hits == ()


def test_collect_vehicle_rule_does_not_fire_on_production_rows_and_vice_versa():
    """when 前置条件必须真的按 vehicle_type 分流，不能两条一起命中。"""
    collect = _check_file(vehicle_type=VEHICLE_TYPE_COLLECT, time_sync_error_ms=999)
    production = _check_file(vehicle_type=VEHICLE_TYPE_PRODUCTION, time_sync_error_ms=999)
    assert "QG-OSS-005-time-sync-production-vehicle" not in _hit_ids(collect)
    assert "QG-OSS-004-time-sync-collect-vehicle" not in _hit_ids(production)


@pytest.mark.parametrize("vehicle_type", [None, "", "test_mule", "COLLECT"])
def test_undecidable_vehicle_type_cannot_silently_skip_the_sync_check(vehicle_type):
    """回归：车型判不出来时，同步误差不允许一路放行到底。

    QG-OSS-004 / QG-OSS-005 的 when 都挂在 vehicle_type 上——该列缺失或写错时
    两条一起落空，5000ms 的同步误差会被判成 ACCEPTED（无命中）。
    这正是「规则挂在判据列上却静默失效」那一类问题，由 QG-OSS-013 兜住。
    """
    decision = _check_file(vehicle_type=vehicle_type, time_sync_error_ms=5000)
    assert decision.hits != (), "车型不可判定 + 同步误差 5000ms 竟然一条规则都没命中"
    assert "QG-OSS-013-vehicle-type-decidable" in _hit_ids(decision)
    assert decision.disposition is Disposition.ALLOW_WITH_FLAG  # 带标记放行，不误伤主链路


def test_vehicle_type_gate_only_fires_when_a_sync_error_is_present():
    """没有同步误差可判的行不该被这条兜底规则打扰（否则等于给全表加了一条必填）。"""
    decision = _check_file()  # 既没有 vehicle_type 也没有 time_sync_error_ms
    assert "QG-OSS-013-vehicle-type-decidable" not in _hit_ids(decision)
    assert decision.disposition is Disposition.ACCEPTED


def test_a_collect_vehicle_row_missing_the_sync_column_is_not_silently_ok():
    """声明了自己是采集车却不带同步误差列 → 命中，而不是当作合格。"""
    decision = _check_file(vehicle_type=VEHICLE_TYPE_COLLECT)
    assert "QG-OSS-004-time-sync-collect-vehicle" in _hit_ids(decision)


# =========================================================================== C 整帧完整性


def test_a_complete_frame_group_passes():
    assert _check_file(frame_group_modalities=",".join(MODALITIES)).hits == ()


@pytest.mark.parametrize("dropped", MODALITIES)
def test_missing_any_single_modality_is_a_p0_rejection(dropped):
    """五个模态少一个就拒——原文 P0 典型场景「多模态整帧缺失」。"""
    kept = [m for m in MODALITIES if m != dropped]
    decision = _check_file(frame_group_modalities=",".join(kept))
    assert decision.rejected
    assert "QG-OSS-002-multimodal-completeness" in _hit_ids(decision)
    hit = next(h for h in decision.hits if h.rule_id.startswith("QG-OSS-002"))
    assert hit.severity is Severity.ERROR and hit.issue_level is IssueLevel.P0
    assert hit.quality_layer is QualityLayer.L1_SENSOR
    assert dropped in hit.detail  # 报文里说清楚缺的是哪一个模态，才谈得上「修得好」


@pytest.mark.parametrize("empty", [None, "", []])
def test_an_empty_frame_group_is_rejected_too(empty):
    """整帧缺失（清单为空）与缺单个模态同判 P0，不给 allow_null 的口子。"""
    decision = _check_file(frame_group_modalities=empty)
    assert decision.rejected
    assert "QG-OSS-002-multimodal-completeness" in _hit_ids(decision)


def test_frame_group_accepts_list_and_mapping_shapes():
    """上游用逗号串、列表还是 {模态: 文件} 字典表达帧组，判定结果必须一致。"""
    as_list = _check_file(frame_group_modalities=list(MODALITIES))
    as_map = _check_file(frame_group_modalities={m: f"oss://x/{m}" for m in MODALITIES})
    half_map = _check_file(frame_group_modalities=dict.fromkeys(MODALITIES))
    assert as_list.hits == () and as_map.hits == ()
    assert half_map.rejected  # 值为空 = 该模态的文件没到


@pytest.mark.parametrize("rate", [0.0, 0.005, 0.01])
def test_continuous_frame_loss_at_or_below_one_percent_is_quiet(rate):
    """原文写的是「> 1%」——恰好 1% 不告警。"""
    assert _check_file(continuous_frame_loss_rate=rate).hits == ()


@pytest.mark.parametrize("rate", [0.0101, 0.02, 1.0])
def test_continuous_frame_loss_above_one_percent_escalates_to_a_flagged_alert(rate):
    """连续丢帧率 > 1% 升级告警：升的是等级（P2），不是升成拒绝入湖。"""
    decision = _check_file(continuous_frame_loss_rate=rate)
    assert decision.disposition is Disposition.ALLOW_WITH_FLAG
    hit = next(h for h in decision.hits if h.rule_id.startswith("QG-OSS-003"))
    assert hit.issue_level is IssueLevel.P2
    assert hit.severity is Severity.WARNING


def test_batch_missing_ratio_uses_the_same_one_percent_line():
    """批次缺片是批级判定，喂的是 batch_stats 不是记录列。"""
    gate = _gate()
    assert (
        gate.check_batch_stats(FILE_TABLE, {"batch_missing_ratio": 0.01}, channel=Channel.OSS_FILE)
        == []
    )
    hits = gate.check_batch_stats(
        FILE_TABLE, {"batch_missing_ratio": 0.02}, channel=Channel.OSS_FILE
    )
    assert [h.rule_id for h in hits] == ["QG-OSS-012-batch-frame-loss"]
    assert hits[0].issue_level is IssueLevel.P3  # 原文 P3 典型场景「批次轻微缺片」


def test_duplicate_rate_alert_fires_strictly_above_five_percent():
    gate = _gate()
    assert (
        gate.check_batch_stats(EVENT_TABLE, {"duplicate_rate": 0.05}, channel=Channel.KAFKA) == []
    )
    hits = gate.check_batch_stats(EVENT_TABLE, {"duplicate_rate": 0.0501}, channel=Channel.KAFKA)
    assert [h.rule_id for h in hits] == ["QG-KFK-006-duplicate-rate"]


# ======================================================================= D 五步异常闭环


def _loop(sink: CollectingAlertSink | None = None) -> tuple[ClosedLoop, InMemoryIssueStore, list]:
    store = InMemoryIssueStore()
    collected = sink or CollectingAlertSink()
    loop = ClosedLoop(_gate(store), router=AlertRouter(default_sink=collected))
    return loop, store, collected.alerts


def test_step1_and_2_intercept_and_isolate_with_the_original_payload():
    """① 拦截携带命中规则 ID；② 原始报文随隔离记录落表，可重放。"""
    loop, store, _ = _loop()
    bad = _file(file_id="FILE-BAD", vehicle_desensitized_flag=False)

    result = loop.ingest(FILE_TABLE, [bad], channel=Channel.OSS_FILE)

    assert result.rejected and not result.accepted
    (issue,) = store.list()
    assert issue.rule_ids == ("QG-OSS-001-desensitization-flags",)
    assert issue.issue_level is IssueLevel.P0  # 脱敏标记缺失 = 合规红线
    assert issue.replayable and issue.replay_payload() == bad
    assert any("① 门禁拦截" in h for h in issue.history)
    assert any("② 异常隔离" in h for h in issue.history)


def test_isolation_is_idempotent_for_the_same_bad_record():
    """同一条坏数据重复进门禁只更新一行，不刷出一堆隔离记录。"""
    loop, store, _ = _loop()
    bad = _file(file_id="FILE-BAD", vehicle_desensitized_flag=False)
    loop.ingest(FILE_TABLE, [bad, dict(bad)], channel=Channel.OSS_FILE)
    assert store.count() == 1


def test_step3_p0_is_paged_immediately_and_leaves_a_trace_on_the_issue():
    """③ 分级告警：P0 电话 + 钉钉即时触达，并把这一步写回隔离记录。"""
    loop, store, alerts = _loop()
    loop.ingest(
        FILE_TABLE,
        [_file(vehicle_desensitized_flag=False)],
        channel=Channel.OSS_FILE,
    )

    assert [a.level for a in alerts] == [IssueLevel.P0]
    assert alerts[0].channels == ("电话", "钉钉")
    assert alerts[0].response_due_at == NOW + timedelta(minutes=P0_RESPONSE_MINUTES)
    (issue,) = store.list()
    assert issue.issue_status is IssueStatus.ALERTED
    assert any("③ 分级告警" in h for h in issue.history)


def test_step3_flagged_rows_still_reach_the_p2_daily_digest():
    """回归：带标记放行的 P2 也要进日报，否则「3 个工作日内闭环」没有对象。

    原文第五章 SLA 表把「时间同步超限」列在 P2 一般档；这类命中不进隔离表
    （数据已经入湖了），但必须进汇总队列，否则日报永远是空的。
    """
    loop, store, alerts = _loop()
    loop.ingest(
        FILE_TABLE,
        [_file(vehicle_type=VEHICLE_TYPE_COLLECT, time_sync_error_ms=99)],
        channel=Channel.OSS_FILE,
    )

    assert store.count() == 0, "带标记放行的数据不该占隔离表（会把入湖拦截率口径搞脏）"
    assert alerts == [], "P2 不即时触达，先进日报队列"
    assert loop.router.pending_digest_counts()["daily_pending"] == 1

    digest = loop.router.flush_daily(NOW)
    assert digest is not None and digest.digest
    assert digest.channels == ("日报",)
    assert "QG-OSS-004-time-sync-collect-vehicle" in digest.render()


def test_step3_batch_hits_are_routed_not_just_logged():
    """批级命中（批次缺片 / 重复率超限）也要进汇总，不能只落一行日志。"""
    loop, _, _ = _loop()
    result = loop.ingest(
        FILE_TABLE,
        [_file()],
        channel=Channel.OSS_FILE,
        batch_stats={"batch_missing_ratio": 0.5},
    )
    assert [h.rule_id for h in result.batch_hits] == ["QG-OSS-012-batch-frame-loss"]
    assert loop.router.pending_digest_counts()["weekly_pending"] == 1  # P3 → 周报


def test_step4_has_all_three_branches():
    """④ 分流处置：A 自动修复 / B 人工修复 / C 弃置归档，一条都不能少。"""
    assert {a.value for a in RepairAction} == {"A", "B", "C"}

    # C 弃置归档：必须留下原因，且原始报文仍在（归档保留审计）
    loop, store, _ = _loop()
    loop.ingest(FILE_TABLE, [_file(vehicle_desensitized_flag=False)], channel=Channel.OSS_FILE)
    (issue,) = store.list()
    outcome = loop.dispatch(issue, RepairAction.DISCARD_ARCHIVE, reason="源端已删除，无法补数")
    assert outcome.action is RepairAction.DISCARD_ARCHIVE
    assert issue.issue_status is IssueStatus.DISCARDED
    assert issue.discard_reason == "源端已删除，无法补数"
    assert issue.raw_payload  # 报文不因为弃置就丢掉

    # A 自动修复：幂等重放，报文原样取回后进复验队列
    loop_a, store_a, _ = _loop()
    loop_a.ingest(FILE_TABLE, [_file(file_type="hologram")], channel=Channel.OSS_FILE)
    (issue_a,) = store_a.list()
    auto = loop_a.dispatch(issue_a, RepairAction.AUTO_REPAIR)
    assert auto.action is RepairAction.AUTO_REPAIR and auto.repaired is True
    assert issue_a.issue_status is IssueStatus.REPAIRED

    # B 人工修复：补数回填后进复验队列
    loop2, store2, _ = _loop()
    loop2.ingest(FILE_TABLE, [_file(file_type="hologram")], channel=Channel.OSS_FILE)
    (issue2,) = store2.list()
    assert loop2.dispatch(issue2, RepairAction.MANUAL_REPAIR).repaired is False  # 等工单
    assert (
        loop2.dispatch(issue2, RepairAction.MANUAL_REPAIR, repaired_record=_file()).repaired is True
    )
    assert issue2.issue_status is IssueStatus.REPAIRED
    assert store2.pending_recheck() == [issue2]


def test_step4_auto_repair_failure_actually_routes_to_manual():
    """A 分支修不动时要真的改挂 B，否则下一次 dispatch 还会去跑必然失败的自动修复。"""

    class _NeverRepairs:
        def repair(self, issue):
            return None

    loop, store, _ = _loop()
    loop.gate.isolate_warnings = False
    loop.auto_repair = _NeverRepairs()
    loop.ingest(FILE_TABLE, [_file(frame_group_modalities="camera")], channel=Channel.OSS_FILE)
    (issue,) = store.list()

    outcome = loop.dispatch(issue, RepairAction.AUTO_REPAIR)
    assert outcome.repaired is False
    assert issue.repair_action is RepairAction.MANUAL_REPAIR
    assert issue.issue_status is IssueStatus.DISPATCHED


def test_step5_recheck_reruns_every_rule_and_reingests_on_pass():
    """⑤ 复验重入湖：通过则写 ODS 并回填处理状态。"""
    written: list[tuple[str, dict]] = []
    store = InMemoryIssueStore()
    loop = ClosedLoop(
        _gate(store),
        router=AlertRouter(default_sink=CollectingAlertSink()),
        ods_sink=lambda table, row: written.append((table, dict(row))),
    )
    loop.ingest(FILE_TABLE, [_file(file_type="hologram")], channel=Channel.OSS_FILE)
    (issue,) = store.list()
    loop.dispatch(issue, RepairAction.MANUAL_REPAIR, repaired_record=_file())

    result = loop.recheck(issue)

    assert result.passed and result.round_no == 1
    assert issue.issue_status is IssueStatus.REINGESTED
    assert issue.reingested_at == NOW and issue.sla_met is True
    assert [t for t, _ in written] == [FILE_TABLE]


def test_step5_escalates_to_p0_only_after_more_than_three_rounds():
    """不通过退回隔离，**超 3 轮**升级 P0：第 1/2/3 轮不升，第 4 轮才升。"""
    loop, store, alerts = _loop()
    # file_type 不在字典内 → P1 拒绝，复验多少次都还是拒绝
    loop.ingest(FILE_TABLE, [_file(file_type="hologram")], channel=Channel.OSS_FILE)
    (issue,) = store.list()
    assert issue.issue_level is IssueLevel.P1

    for expected_round in range(1, MAX_RECHECK_ROUNDS + 1):
        result = loop.recheck(issue)
        assert result.passed is False
        assert result.round_no == expected_round
        assert result.escalated is False, f"第 {expected_round} 轮就升级了，原文说的是「超 3 轮」"
        assert issue.issue_level is IssueLevel.P1
        assert issue.issue_status is IssueStatus.ISOLATED

    final = loop.recheck(issue)
    assert final.round_no == MAX_RECHECK_ROUNDS + 1
    assert final.escalated is True
    assert issue.issue_level is IssueLevel.P0
    assert issue.escalated is True
    assert issue.response_due_at == NOW + timedelta(minutes=P0_RESPONSE_MINUTES)
    assert any(a.escalated_from is IssueLevel.P1 for a in alerts)


def test_step5_unreplayable_payload_is_counted_as_a_failed_round():
    """报文取不回来也算一轮复验失败，不能悄悄卡死在队列里。"""
    loop, store, _ = _loop()
    loop.ingest(FILE_TABLE, [_file(file_type="hologram")], channel=Channel.OSS_FILE)
    (issue,) = store.list()
    issue.replayable = False

    result = loop.recheck(issue)

    assert result.passed is False and result.round_no == 1
    assert issue.issue_status is IssueStatus.ISOLATED


def test_p3_escalates_to_p2_after_two_consecutive_weeks():
    """P3 周报：连续两周超标升级 P2（原文第五章 P3 行）。

    用真实的 P3 命中喂周报队列：事件 ID 重复（QG-KFK-005）是原文 4.2
    「重复率监控」那一路，等级 P3、带标记放行。
    """
    loop, _, alerts = _loop()
    dup = {
        "data_id": DATA_ID,
        "event_id": "EVT-DUP",
        "trigger_type": "aeb",
        "trigger_time": NOW,
        "vehicle_manufacture_time": datetime(2024, 1, 1),
        "pre_trigger_seconds": 10.0,
        "post_trigger_seconds": 10.0,
    }

    for week in range(P3_CONSECUTIVE_WEEKS_TO_ESCALATE):
        result = loop.ingest(EVENT_TABLE, [dict(dup), dict(dup)], channel=Channel.KAFKA)
        flagged = [d for d in result.decisions if d.flagged]
        assert flagged, "重复事件 ID 没有被判成 P3 带标记放行"
        assert "QG-KFK-005-event-id-dedup" in _hit_ids(flagged[-1])
        assert loop.router.pending_digest_counts()["weekly_pending"] >= 1
        digest = loop.router.flush_weekly(NOW + timedelta(weeks=week))
        assert digest is not None and digest.channels == ("周报",)

    escalated = [a for a in alerts if a.escalated_from is IssueLevel.P3]
    assert escalated, f"连续 {P3_CONSECUTIVE_WEEKS_TO_ESCALATE} 周超标却没有升级告警"
    assert escalated[-1].level is IssueLevel.P2
    assert "QG-KFK-005-event-id-dedup" in escalated[-1].render()


# ===================================================================== E 规则分级与门禁自监控


def test_every_p0_rule_is_a_hard_block():
    """P0 合规/安全级只能是 ERROR 硬拦截——这是门禁的牙齿。"""
    p0 = [r for r in BUILTIN_RULES if r.issue_level is IssueLevel.P0]
    assert p0
    for rule in p0:
        assert rule.severity is Severity.ERROR, rule.rule_id
        assert rule.is_hard_block, rule.rule_id
        assert rule.disposition_hint == Disposition.REJECT.value


def test_p0_typical_cases_from_the_sla_table_all_have_a_rule():
    """原文 P0 典型场景四项：脱敏标记缺失、主键为空、Schema 不可解析、多模态整帧缺失。"""
    p0_ids = {r.rule_id for r in BUILTIN_RULES if r.issue_level is IssueLevel.P0}
    assert "QG-OSS-001-desensitization-flags" in p0_ids  # 脱敏标记缺失
    assert "QG-COM-001-data-id-not-null" in p0_ids  # 主键为空
    assert "QG-COM-006-schema-parsable" in p0_ids  # Schema 不可解析
    assert "QG-OSS-002-multimodal-completeness" in p0_ids  # 多模态整帧缺失


def test_p0_rules_cannot_be_greyed_off_or_registered_disabled():
    """合规红线不接受灰度、下线，也不接受「注册时就是关着的」。"""
    center = default_rule_center()
    p0_id = "QG-OSS-002-multimodal-completeness"
    with pytest.raises(ValueError, match="P0"):
        center.start_grey(p0_id, FILE_TABLE)
    with pytest.raises(ValueError, match="P0"):
        center.disable(p0_id)

    rule = center.get(p0_id)
    fresh = RuleCenter()
    with pytest.raises(ValueError, match="P0"):
        fresh.register(rule, Rollout(mode=RolloutMode.OFF))
    with pytest.raises(ValueError, match="P0"):
        RuleCenter().register(
            rule, Rollout(mode=RolloutMode.GREY, grey_tables=frozenset({FILE_TABLE}))
        )


def test_gate_self_monitoring_thresholds_are_strictly_greater_than():
    """六、门禁运维：> 100 条拒绝、> 1000ms 耗时才告警，等于阈值不告警。"""
    metrics = GateMetrics(window_started_at=NOW)
    for _ in range(REJECTED_RECORDS_COUNT_THRESHOLD):
        metrics.observe(disposition=Disposition.REJECT, duration_ms=1.0, table=FILE_TABLE)
    assert metrics.evaluate(NOW) == []

    metrics.observe(disposition=Disposition.REJECT, duration_ms=1.0, table=FILE_TABLE)
    alerts = metrics.evaluate(NOW)
    assert [a.metric for a in alerts] == [METRIC_REJECTED_RECORDS_COUNT]
    assert alerts[0].threshold == 100 and alerts[0].value == 101
    assert alerts[0].severity is Severity.WARNING

    slow = GateMetrics(window_started_at=NOW)
    slow.observe(
        disposition=Disposition.ACCEPTED,
        duration_ms=QUALITY_CHECK_DURATION_MS_THRESHOLD,
        table=FILE_TABLE,
    )
    assert slow.evaluate(NOW) == []
    slow.observe(disposition=Disposition.ACCEPTED, duration_ms=1000.5, table=FILE_TABLE)
    assert [a.metric for a in slow.evaluate(NOW)] == [METRIC_QUALITY_CHECK_DURATION]
    assert slow.snapshot()[METRIC_REJECTED_RECORDS_COUNT] == 0


# ============================================================== F 判据列必须真的存在


def _judged_columns(rule) -> set[tuple[str, str]]:
    """一条规则实际会去读的 (表, 列)：field + when.field + params 里的列名引用。

    列不存在 = 规则恒取 NULL = 阈值静默不生效，是最难发现的一类失效。
    """
    single = ("not_before_field", "from_field", "checksum_field", "payload_field", "flag_field")
    plural = ("key_fields", "flags")
    params = dict(rule.params or {})
    refs: set[tuple[str, str]] = set()

    def add(table: str, col) -> None:
        if table and table != "*" and col:
            refs.add((table, str(col)))

    add(rule.table, rule.field)
    if rule.when:
        add(rule.table, rule.when.get("field"))
    for key in single:
        add(rule.table, params.get(key))
    for key in plural:
        for col in params.get(key) or ():
            add(rule.table, col)
    if params.get("group_field"):
        add(rule.table, params["group_field"])
    if params.get("target_table") and params.get("target_field"):
        add(str(params["target_table"]), params["target_field"])
    return refs


def test_no_rule_hangs_on_a_column_that_does_not_exist():
    """判据列（含 when.field 与 params 里的 from_field / flag_field 等）必须在注册表上。"""
    from adas_lakehouse.catalog import registry

    columns = {t.name: {c.name for c in t.all_columns()} for t in registry.all_tables()}
    missing = sorted(
        f"{table}.{col}"
        for rule in default_rule_center()
        for table, col in _judged_columns(rule)
        if col not in columns.get(table, set())
    )
    assert missing == [], f"规则挂在不存在的列上，会静默不生效: {missing}"


def test_the_time_sync_and_frame_rules_hang_on_real_columns():
    """把本次审计重点的五条规则单独点名一遍，防止上面那条被整体放宽。"""
    from adas_lakehouse.catalog import registry

    cols = {c.name for c in registry.by_name(FILE_TABLE).all_columns()}
    assert {"vehicle_type", "time_sync_error_ms"} <= cols
    assert {"frame_group_modalities", "continuous_frame_loss_rate", "batch_missing_ratio"} <= cols

    center = default_rule_center()
    for rule_id in (
        "QG-OSS-004-time-sync-collect-vehicle",
        "QG-OSS-005-time-sync-production-vehicle",
        "QG-OSS-013-vehicle-type-decidable",
    ):
        rule = center.get(rule_id)
        assert rule.field in cols
        assert rule.when["field"] in cols


def test_generated_yaml_snapshot_matches_the_builtin_rules():
    """rules/quality_rules.yaml 是内置规则的快照——加了规则不重新导出就会漂。"""
    pytest.importorskip("yaml")
    from adas_lakehouse.quality.loader import DEFAULT_RULES_PATH, load_rules

    from_yaml = load_rules(DEFAULT_RULES_PATH)
    assert {r.rule_id for r in from_yaml} == {r.rule_id for r in BUILTIN_RULES}
    yaml_rule = from_yaml.get("QG-OSS-004-time-sync-collect-vehicle")
    assert yaml_rule.params["max_abs"] == COLLECT_VEHICLE_SYNC_TOLERANCE_MS
    assert from_yaml.get("QG-OSS-005-time-sync-production-vehicle").params["max_abs"] == (
        PRODUCTION_VEHICLE_SYNC_TOLERANCE_MS
    )


# ============================================ G Flink 接入点：SQL 点名的 UDF 必须真的存在


def test_the_udf_named_by_the_flink_pipeline_exists_and_matches_its_row_contract():
    """flink/sql/quality_gate_pipeline.sql 按类名注册门禁，那个类必须在。

    SQL 里写的是 ``AS 'adas_lakehouse.quality.udf.QualityGateUdf' LANGUAGE PYTHON``，
    并按 ``gate.disposition / quality_flag / rule_ids / issue_json / duration_ms``
    取五个字段——名字对不上，整条门禁作业 submit 就失败。
    """
    from adas_lakehouse.quality.udf import RESULT_FIELDS, QualityGateUdf

    pipeline = (_repo_root() / "flink" / "sql" / "quality_gate_pipeline.sql").read_text(
        encoding="utf-8"
    )
    assert "adas_lakehouse.quality.udf.QualityGateUdf" in pipeline
    assert QualityGateUdf is not None
    for field, _type in RESULT_FIELDS:
        assert f"gate.{field}" in pipeline, f"SQL 没有取用 UDF 的 {field} 字段"


def _repo_root():
    from pathlib import Path

    return Path(__file__).resolve().parents[2]


def _udf_eval(record: dict, *, table: str = FILE_TABLE, channel: str = "oss_file"):
    import json

    from adas_lakehouse.quality.udf import QualityGateUdf

    (row,) = list(
        QualityGateUdf(source_system="oss:adas-raw").eval(table, channel, json.dumps(record))
    )
    return row


def test_udf_returns_the_three_dispositions():
    assert _udf_eval(_file())[0] == Disposition.ACCEPTED.value
    flagged = _udf_eval(_file(vehicle_type=VEHICLE_TYPE_COLLECT, time_sync_error_ms=99))
    assert flagged[0] == Disposition.ALLOW_WITH_FLAG.value
    assert "QG-OSS-004-time-sync-collect-vehicle" in flagged[1]  # _quality_flag
    rejected = _udf_eval(_file(vehicle_desensitized_flag=False))
    assert rejected[0] == Disposition.REJECT.value
    assert rejected[2] == "QG-OSS-001-desensitization-flags"  # rule_ids


def test_udf_issue_json_carries_every_path_the_sql_reads():
    """SQL 用 JSON_VALUE / JSON_QUERY 逐字段取隔离行，少一个键就写出一列 NULL。"""
    import json

    row = _udf_eval(_file(vehicle_desensitized_flag=False))
    issue = json.loads(row[3])
    for path in (
        "issue_id",
        "severity",
        "issue_level",
        "dimension",
        "quality_layer",
        "message",
        "detail",
        "hits",
        "raw_payload",
        "payload_hash",
        "repair_action",
        "owner",
        "response_due_at",
        "closure_due_at",
        "gate_version",
        "history",
    ):
        assert path in issue, f"issue_json 缺少 SQL 要读的 $.{path}"
    assert isinstance(issue["hits"], list) and issue["hits"]  # JSON_QUERY 取的是数组
    assert isinstance(issue["history"], list)
    assert issue["issue_level"] == IssueLevel.P0.value
    assert json.loads(issue["raw_payload"])["file_id"] == "FILE-0001"  # 可重放


def test_udf_lets_the_rules_judge_an_unparsable_payload():
    """报文不是 JSON 时判定权仍在规则中心：Schema 不可解析是原文的 P0 典型场景。"""
    import json

    from adas_lakehouse.quality.udf import QualityGateUdf

    (row,) = list(QualityGateUdf().eval(FILE_TABLE, "oss_file", "{ 这不是 JSON"))
    assert row[0] == Disposition.REJECT.value
    assert "QG-COM-006-schema-parsable" in row[2]
    assert json.loads(row[3])["issue_level"] == IssueLevel.P0.value


def test_udf_does_not_double_write_the_isolation_table():
    """隔离行由 SQL 的 INSERT 负责，UDF 只序列化——两边都写会写重。"""
    from adas_lakehouse.quality.udf import QualityGateUdf

    udf = QualityGateUdf()
    list(udf.eval(FILE_TABLE, "oss_file", "{}"))
    assert udf.gate.store.count() == 0
