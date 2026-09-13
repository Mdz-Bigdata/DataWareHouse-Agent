"""深度核验：存储生命周期五级分层 / storage_class / 成本系数 / PB 级大文件外置。

对标原文：系列一第 7 篇《存储生命周期五级分层：智驾 PB 级数据成本治理实战》
（公众号「小周」2026-08-30，仓库快照 ``scratchpad/wx/a14.md``）。

与 ``tests/smoke/test_lifecycle.py`` 的分工：冒烟测试证明「跑得通」，
本文件证明「跟原文一个字不差」——每条断言都钉在原文给出的具体数字上，
常量被谁改漂一个小数点，这里立刻红。

覆盖的原文数字清单（每节开头列出该节锁住的值）::

    介质成本系数   8–10x / 1x / 0.5x / 0.15x / 0.05x，归档是热层的 1/50
    示例单价       NAS 1.0 / 标准 0.12 / 低频 0.06 / 归档 0.018 元/GB·月
    保留期表       30-90-365 / 90-180-365 / 180-365-永久 / 90-180-永久 / 7
    分层阈值       温 30 天 / 冷 90 天 / 归档 180 天
    NAS 淘汰纪律   7 天缓冲 / 最新 3 个版本 / 80% 水位 / 近 30 天 LRU
    案例成本账     240GB，¥3,226 → ¥203，降幅约 94%
    月末复盘       1.8PB / ¥58 万 / 6-24-38-32 / 210TB / ¥9.6 万 / 1.8% vs 35%
    告警线         成本环比 > 10%、NAS 使用率 > 80%
    取回 SLA       标准恢复 ≤ 4 小时
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from adas_lakehouse.catalog import registry
from adas_lakehouse.lifecycle import (
    ARCHIVE_RESTORE_SLA_HOURS,
    ARCHIVE_VS_HOT_PRICE_DIVISOR,
    COLD_HISTORY_SHARE,
    COLD_TO_ARCHIVE_NO_ACCESS_DAYS,
    COST_DAILY_COLUMNS,
    COST_VS_DATA_GROWTH_RATIO,
    DEEP_ARCHIVE_NO_ACCESS_DAYS,
    DELETE_TRIPLE_CONFIRM,
    EXTERNALIZED_META_FIELDS,
    FILE_STORAGE_CLASS_VALUES,
    FLOW_CYCLE,
    GOVERNANCE_PRINCIPLES,
    GROWTH_SOURCES,
    LAKEHOUSE_METADATA_CONTENT,
    LIFECYCLE_COLUMNS,
    LINEAGE_BUMP_FLOOR_STAGE,
    LOSS_OF_CONTROL,
    MEDIA_ROLES,
    NAS_CHECKPOINT_KEEP_VERSIONS,
    NAS_LRU_NO_ACCESS_DAYS,
    NAS_RELATIVE_PRICE_RANGE,
    NAS_TRAINING_DONE_BUFFER_DAYS,
    NAS_WATERMARK_USAGE,
    PIPELINE_STEPS,
    RELATIVE_PRICE,
    REUSABLE_LESSONS,
    SAFETY_GATES,
    SAMPLE_PRICE_YUAN_PER_GB_MONTH,
    SOURCE_MONTHLY_REVIEW,
    SOURCE_REDUCTION_RATE,
    STAGE_OF_MEDIA,
    TIER_MODEL_THRESHOLDS,
    TIERS,
    ActionType,
    CostDailyRow,
    CostModel,
    DataType,
    EvictStatus,
    GovernanceRun,
    InMemoryRepository,
    LifecycleRecord,
    LifecycleStage,
    NasContext,
    StorageMedia,
    TrainingContext,
    aggregate_cost_daily,
    budget_alerts,
    check_externalized,
    check_source_of_truth,
    decide,
    delete_triple_confirm,
    externalization_leverage,
    lru_evict_order,
    media_of_storage_class,
    replay_case_study,
    retention_for,
    scan,
    storage_class_of,
    target_stage_by_age,
    verify_price_consistency,
    verify_storage_class_bridge,
)

GB = 1024**3

#: 原文第四章案例的数据 ID 与体积
CASE_ID = "COLLECT_BP_20260301123045_b7e2"
CASE_GB = 240


def _rec(**overrides) -> LifecycleRecord:
    """案例那份 240GB 雨夜城区采集数据，按需覆盖字段。"""
    payload: dict = {
        "data_id": CASE_ID,
        "file_path": "s3://adas-raw/collect/2026/03/01/clip.mp4",
        "data_type": "raw",
        "file_size_bytes": CASE_GB * GB,
        "create_time": datetime(2026, 3, 1),
        "source_domain": "collect",
    }
    payload.update(overrides)
    return LifecycleRecord(**payload)


# ===========================================================================
# 一、两个维度：生命周期档位 ≠ 介质档位
# ===========================================================================


def test_stage_and_media_are_two_independent_axes():
    """分层状态六值、介质五值，两套取值域各自独立（原文第四章①字段说明）。

    这是整套方案最容易被做塌的地方——把「冷」直接当成「OSS 低频」，
    归档级那两档介质就没处放了。
    """
    assert [m.value for m in StorageMedia] == [
        "nas",
        "oss_standard",
        "oss_ia",
        "oss_archive",
        "oss_deep_archive",
    ]
    assert [s.value for s in LifecycleStage] == [
        "hot",
        "warm",
        "cold",
        "archive",
        "pending_delete",
        "deleted",
    ]
    # 五个介质映射到四个分层——不是一一对应，归档级吃掉了两档介质
    assert len(STAGE_OF_MEDIA) == 5
    assert len(set(STAGE_OF_MEDIA.values())) == 4
    assert STAGE_OF_MEDIA[StorageMedia.OSS_ARCHIVE] is LifecycleStage.ARCHIVE
    assert STAGE_OF_MEDIA[StorageMedia.OSS_DEEP_ARCHIVE] is LifecycleStage.ARCHIVE


def test_five_tier_model_matches_the_source_table_row_by_row():
    """五级分层模型：热 H1 / 温 H2 / 冷 C1 / 归档 C2 / 删除 D（原文第二章表格）。"""
    assert [t.code for t in TIERS] == ["H1", "H2", "C1", "C2", "D"]
    assert [t.label for t in TIERS] == ["热", "温", "冷", "归档", "删除"]

    by_code = {t.code: t for t in TIERS}
    assert by_code["H1"].media == (StorageMedia.NAS,)
    assert by_code["H2"].media == (StorageMedia.OSS_STANDARD,)
    assert by_code["C1"].media == (StorageMedia.OSS_IA,)
    # 归档级 C2 原文写的是「OSS 归档 / 深度归档」——一个分层挂两档介质
    assert by_code["C2"].media == (StorageMedia.OSS_ARCHIVE, StorageMedia.OSS_DEEP_ARCHIVE)
    # 删除档没有介质：D 是生命周期的终点，不是一种存储介质
    assert by_code["D"].media == ()

    assert by_code["H2"].entry_condition == "创建 30 天内或 30 天内有访问"
    assert by_code["C1"].entry_condition == "连续 90 天无访问"
    assert by_code["C2"].entry_condition == "连续 180 天无访问且过保留策略阈值"
    assert by_code["D"].entry_condition == "过保留期 + 下游血缘引用数为 0"


def test_lineage_bump_never_promotes_data_into_the_hot_tier():
    """血缘提档是分层动作，不是介质动作——被引用的温层数据不会自己跳上 NAS。

    热 H1 的进入条件是「数据集关联活跃训练任务并**预热**」，
    只能由预热动作产生。提档到 hot 等于把两个维度混成一个。
    """
    assert LINEAGE_BUMP_FLOOR_STAGE is LifecycleStage.WARM

    # 案例 03-10：入数据集，血缘引用 +1，触发「被引用提档保留」
    decision = decide(
        _rec(lineage_ref_count=1, last_access_time=datetime(2026, 3, 10)),
        training=TrainingContext(now=datetime(2026, 3, 10)),
    )
    assert decision.action is ActionType.HOLD
    assert "hot" not in decision.reason, f"提档把温层数据抬进了热层: {decision.reason}"
    assert "warm" in decision.reason


def test_governance_principles_are_the_four_from_the_source():
    """四条治理原则（原文第二章原话）。"""
    assert [name for name, _ in GOVERNANCE_PRINCIPLES] == [
        "元信息驱动",
        "分层存储",
        "血缘保护",
        "成本稳态",
    ]
    assert dict(GOVERNANCE_PRINCIPLES)["血缘保护"] == (
        "被活跃数据集或训练任务引用的数据禁止删除、暂缓降冷"
    )


# ===========================================================================
# 二、介质相对成本系数五档
# ===========================================================================


@pytest.mark.parametrize(
    ("media", "factor", "source_text"),
    [
        (StorageMedia.NAS, 8.0, "约 8–10x"),
        (StorageMedia.OSS_STANDARD, 1.0, "1x（基准）"),
        (StorageMedia.OSS_IA, 0.5, "约 0.5x"),
        (StorageMedia.OSS_ARCHIVE, 0.15, "约 0.15x"),
        (StorageMedia.OSS_DEEP_ARCHIVE, 0.05, "约 0.05x"),
    ],
)
def test_relative_price_is_verbatim(media, factor, source_text):
    """相对 OSS 标准存储的单价量级五档（原文第二章第二张表，逐字）。"""
    assert RELATIVE_PRICE[media] == factor, f"{media.value} 应为 {source_text}"


def test_nas_price_band_and_archive_divisor():
    """NAS「约 8–10 倍」是区间不是点；归档「只有热层的 1/50 甚至更低」。"""
    assert NAS_RELATIVE_PRICE_RANGE == (8.0, 10.0)
    assert ARCHIVE_VS_HOT_PRICE_DIVISOR == 50.0
    # 引言：「超过 80% 的历史数据……几乎不再被访问」
    assert COLD_HISTORY_SHARE == 0.80


@pytest.mark.parametrize(
    ("media", "price"),
    [
        (StorageMedia.NAS, 1.0),
        (StorageMedia.OSS_STANDARD, 0.12),
        (StorageMedia.OSS_IA, 0.06),
        (StorageMedia.OSS_ARCHIVE, 0.018),
    ],
)
def test_sample_unit_prices_are_verbatim(media, price):
    """案例示例单价（原文第四章：NAS 1.0 元/GB·月，OSS 标准 0.12 / 低频 0.06 / 归档 0.018）。"""
    assert SAMPLE_PRICE_YUAN_PER_GB_MONTH[media] == price


def test_two_price_calibers_reconcile_exactly():
    """第二章相对量级与第四章示例单价必须互相自洽，否则降本账是假的。"""
    report = verify_price_consistency()
    assert report["all_consistent"] is True
    measured = {c["item"]: c["measured"] for c in report["checks"]}
    assert measured["NAS / OSS标准"] == pytest.approx(1.0 / 0.12)  # 8.33x，落在 8–10
    assert 8.0 <= measured["NAS / OSS标准"] <= 10.0
    assert measured["OSS低频 / OSS标准"] == pytest.approx(0.5)
    assert measured["OSS归档 / OSS标准"] == pytest.approx(0.15)
    assert measured["OSS归档 / NAS热层"] == pytest.approx(1 / 0.018)  # 55.6 ≥ 50


# ===========================================================================
# 三、storage_class：五级分层在文件域的落点
# ===========================================================================


def test_storage_class_has_exactly_the_three_file_domain_values():
    """[a8]「storage_class 对接存储生命周期管理——标准 / 低频 / 归档的降冷策略」。"""
    assert FILE_STORAGE_CLASS_VALUES == ("standard", "infrequent", "archive")


@pytest.mark.parametrize(
    ("media", "storage_class"),
    [
        (StorageMedia.OSS_STANDARD, "standard"),
        (StorageMedia.OSS_IA, "infrequent"),
        (StorageMedia.OSS_ARCHIVE, "archive"),
        (StorageMedia.OSS_DEEP_ARCHIVE, "archive"),
        (StorageMedia.NAS, None),
    ],
)
def test_media_maps_to_file_domain_storage_class(media, storage_class):
    """介质 → storage_class。NAS 映射为 None——热层是介质，不是 OSS 的降冷档位。"""
    assert storage_class_of(media) == storage_class


def test_storage_class_round_trips_through_the_bridge():
    for value in FILE_STORAGE_CLASS_VALUES:
        assert storage_class_of(media_of_storage_class(value)) == value
    with pytest.raises(ValueError, match="未知 storage_class"):
        media_of_storage_class("glacier")


def test_bridge_agrees_with_the_ingest_subsystem():
    """两个子系统对同一档介质的成本系数必须一致，否则成本看板整体失真。"""
    report = verify_storage_class_bridge()
    assert report["available"] is True, "入湖子系统不可用，storage_class 桥无法核对"
    assert report["values_match"] is True
    assert report["all_consistent"] is True
    factors = {c["storage_class"]: c["upstream_cost_factor"] for c in report["checks"]}
    assert factors["infrequent"] == 0.5  # [a5]/[a14] 低频约 0.5x
    assert factors["archive"] == 0.15  # [a5]/[a14] 归档约 0.15x


def test_record_exposes_storage_class_but_does_not_store_it():
    """storage_class 是 storage_media 的函数，存两份必然漂移。"""
    record = _rec(storage_media="oss_ia", lifecycle_stage="cold")
    assert record.storage_class == "infrequent"
    assert "storage_class" not in record.to_row(), "明细表不该自带一列 storage_class"


def test_media_parse_accepts_the_cpfs_spelling():
    """原文热层介质写作「CPFS / NAS」，字段取值只有 nas——上游写 cpfs 要能归一。"""
    assert StorageMedia.parse("cpfs") is StorageMedia.NAS
    assert StorageMedia.parse("CPFS / NAS") is StorageMedia.NAS
    assert StorageMedia.parse("infrequent") is StorageMedia.OSS_IA
    with pytest.raises(ValueError, match="未知存储介质"):
        StorageMedia.parse("tape")


# ===========================================================================
# 四、OSS 保留期表（原文第三章五行逐字）
# ===========================================================================


@pytest.mark.parametrize(
    ("code", "standard", "ia_until", "archive_until", "delete_after"),
    [
        ("raw", 30, 90, 365, 365),  # 原始数据 | 30 天 | 30–90 | 90–365 | 365 天后
        ("intermediate", 90, 180, 365, 365),  # 中间过程产物 | 90 | 90–180 | 180–365 | 365
        ("dataset", 180, 365, None, None),  # 数据集文件 | 180 | 180–365 | 365+ | 永久
        ("model", 90, 180, None, None),  # 模型文件 | 90 | 90–180 | 180+ | 永久
        ("temp", 7, None, None, 7),  # 临时文件 | 7 天 | — | — | 7 天后删除
    ],
)
def test_retention_schedule_is_verbatim(code, standard, ia_until, archive_until, delete_after):
    rule = retention_for(code)
    assert rule.standard_days == standard
    assert rule.ia_until_days == ia_until
    assert rule.archive_until_days == archive_until
    assert rule.delete_after_days == delete_after


def test_tier_model_thresholds_are_verbatim():
    """第二章按访问温度的通用阈值：温 30 / 冷 90 / 归档 180（天）。"""
    assert TIER_MODEL_THRESHOLDS[LifecycleStage.WARM] == 30
    assert TIER_MODEL_THRESHOLDS[LifecycleStage.COLD] == 90
    assert TIER_MODEL_THRESHOLDS[LifecycleStage.ARCHIVE] == 180


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        (0, LifecycleStage.WARM),
        (29, LifecycleStage.WARM),  # 标准存储 30 天内
        (30, LifecycleStage.COLD),  # 低频 30–90 天
        (89, LifecycleStage.COLD),
        (90, LifecycleStage.ARCHIVE),  # 归档 90–365 天
        (364, LifecycleStage.ARCHIVE),
        (365, LifecycleStage.PENDING_DELETE),  # 365 天后且血缘零引用
    ],
)
def test_raw_data_walks_the_retention_ladder(days, expected):
    """原始数据 30 / 90 / 365 三道坎，一天都不许错位。"""
    assert target_stage_by_age("raw", days_since_create=days, days_since_access=days) is expected


def test_temp_files_are_deleted_after_seven_days_without_tiering():
    """临时文件没有低频/归档段（原文两列都是「—」），7 天后直接进删除候选。"""
    assert target_stage_by_age("temp", days_since_create=6, days_since_access=6) is (
        LifecycleStage.WARM
    )
    assert target_stage_by_age("temp", days_since_create=7, days_since_access=7) is (
        LifecycleStage.PENDING_DELETE
    )


def test_dataset_and_model_are_kept_forever():
    """数据集「永久保留（非最新版本转低频/归档）」、模型「永久保留（仅最新 N 版本留标准）」。"""
    for code in ("dataset", "model"):
        assert retention_for(code).delete_after_days is None
        assert retention_for(code).archive_open_ended is True
        # 再老也只到归档，不进删除候选
        assert target_stage_by_age(code, days_since_create=3650, days_since_access=3650) is (
            LifecycleStage.ARCHIVE
        )


def test_unknown_data_type_is_rejected_not_silently_treated_as_raw():
    with pytest.raises(ValueError, match="未知数据类型"):
        DataType.parse("pointcloud")


# ===========================================================================
# 五、降冷 / 归档 / 删除的触发条件
# ===========================================================================


def test_cold_to_archive_uses_the_ninety_day_case_caliber():
    """案例 04-30 转冷、07-29 转归档，间隔正好 90 天（「连续 90 天无访问，归档流转」）。"""
    assert COLD_TO_ARCHIVE_NO_ACCESS_DAYS == 90
    decision = decide(
        _rec(
            storage_media="oss_ia",
            lifecycle_stage="cold",
            lineage_ref_count=1,
            last_access_time=datetime(2026, 3, 20),
        ),
        training=TrainingContext(now=datetime(2026, 7, 29)),
    )
    assert decision.action is ActionType.TIER_DOWN
    assert decision.target_stage is LifecycleStage.ARCHIVE
    assert decision.target_media is StorageMedia.OSS_ARCHIVE


def test_deep_archive_tier_is_actually_reachable():
    """归档级 C2 的第二档介质（0.05x）必须有人能走到，否则那个系数是死常量。

    口径：连续 180 天无访问（第二章「连续 180 天无访问且过保留策略阈值」）。
    """
    assert DEEP_ARCHIVE_NO_ACCESS_DAYS == 180

    # 179 天不动
    decision = decide(
        _rec(
            storage_media="oss_archive",
            lifecycle_stage="archive",
            last_access_time=datetime(2026, 3, 20),
        ),
        training=TrainingContext(now=datetime(2026, 3, 20 + 0).replace(month=9, day=14)),
    )
    assert decision.target_media is not StorageMedia.OSS_DEEP_ARCHIVE

    # 184 天 → 深度归档
    decision = decide(
        _rec(
            storage_media="oss_archive",
            lifecycle_stage="archive",
            last_access_time=datetime(2026, 3, 20),
        ),
        training=TrainingContext(now=datetime(2026, 9, 20)),
    )
    assert decision.action is ActionType.TIER_DOWN
    assert decision.target_media is StorageMedia.OSS_DEEP_ARCHIVE
    # 分层没变，变的只是介质——两个维度各走各的
    assert decision.target_stage is LifecycleStage.ARCHIVE
    assert RELATIVE_PRICE[StorageMedia.OSS_DEEP_ARCHIVE] == 0.05


def test_delete_needs_all_three_confirmations():
    """「过保留期 + 血缘零引用 + 白名单校验，三者同时满足才允许删除」。"""
    assert DELETE_TRIPLE_CONFIRM == ("past_retention", "zero_lineage_ref", "not_whitelisted")

    now = datetime(2027, 3, 2)  # 落湖 366 天后
    ok, failed = delete_triple_confirm(_rec(lineage_ref_count=0), now=now)
    assert ok is True and failed == ()

    ok, failed = delete_triple_confirm(_rec(lineage_ref_count=1), now=now)
    assert ok is False and failed == ("zero_lineage_ref",)

    ok, failed = delete_triple_confirm(_rec(whitelist_flag=True), now=now)
    assert ok is False and "not_whitelisted" in failed

    # 没到保留期：365 天是下限，第 364 天不许删
    ok, failed = delete_triple_confirm(_rec(), now=datetime(2027, 2, 28))
    assert ok is False and "past_retention" in failed


def test_lineage_blocks_deletion_exactly_like_the_case_snapshot_seven():
    """案例第 7 个快照点：「保留期满但血缘引用 > 0 → 删除三重确认拦截，继续留存」。"""
    decision = decide(
        _rec(
            storage_media="oss_archive",
            lifecycle_stage="archive",
            lineage_ref_count=1,
            last_access_time=datetime(2026, 3, 20),
        ),
        training=TrainingContext(now=datetime(2027, 3, 2)),
    )
    assert decision.action is ActionType.BLOCKED
    assert decision.blocked_by == ("删除三重确认",)
    assert "zero_lineage_ref" in decision.reason


def test_whitelist_skips_tiering_entirely():
    """「合规留存与长期回归测试数据可打白名单标签跳过分层流转」。"""
    decision = decide(
        _rec(whitelist_flag=True, last_access_time=datetime(2026, 3, 1)),
        training=TrainingContext(now=datetime(2028, 1, 1)),
    )
    assert decision.action is ActionType.HOLD
    assert decision.rule == "whitelist"


# ===========================================================================
# 六、NAS 四条淘汰纪律（原文第三章）
# ===========================================================================


def _nas_rec(**overrides) -> LifecycleRecord:
    payload = {
        "storage_media": "nas",
        "lifecycle_stage": "hot",
        "checksum_md5": "d41d8cd98f00b204e9800998ecf8427e",
    }
    payload.update(overrides)
    return _rec(**payload)


def test_training_done_waits_exactly_seven_days():
    """「任务已结束且副本未被下一任务引用，7 天缓冲期后淘汰」。"""
    assert NAS_TRAINING_DONE_BUFFER_DAYS == 7
    record = _nas_rec()
    nas = NasContext(checksum_verified=frozenset({record.pk}))
    finished = {CASE_ID: datetime(2026, 3, 13)}

    sixth = decide(
        record,
        training=TrainingContext(now=datetime(2026, 3, 19), training_finished_at=finished),
        nas=nas,
    )
    assert sixth.action is ActionType.HOLD, "第 6 天就淘汰 = 缓冲期被吃掉了"

    seventh = decide(
        record,
        training=TrainingContext(now=datetime(2026, 3, 20), training_finished_at=finished),
        nas=nas,
    )
    assert seventh.action is ActionType.EVICT
    assert seventh.target_media is StorageMedia.OSS_STANDARD


def test_next_task_reference_holds_the_copy_on_nas():
    """「副本未被下一任务引用」是淘汰的前置条件——被引用就不淘汰。"""
    record = _nas_rec()
    decision = decide(
        record,
        training=TrainingContext(
            now=datetime(2026, 4, 1),
            training_finished_at={CASE_ID: datetime(2026, 3, 13)},
            next_task_referenced=frozenset({CASE_ID}),
        ),
        nas=NasContext(checksum_verified=frozenset({record.pk})),
    )
    assert decision.action is not ActionType.EVICT


def test_checkpoint_keeps_the_latest_three_versions():
    """「NAS 仅保留最新 N 个版本（默认 3），历史版本转存 OSS 归档」。"""
    assert NAS_CHECKPOINT_KEEP_VERSIONS == 3
    record = _nas_rec(data_type="model", file_path="/nas/ckpt/epoch_07.pt")
    nas = NasContext(checksum_verified=frozenset({record.pk}))

    third = decide(
        record,
        training=TrainingContext(now=datetime(2026, 4, 1), checkpoint_rank={record.pk: 3}),
        nas=nas,
    )
    assert third.action is not ActionType.EVICT, "第 3 个版本还在保留名额内"

    fourth = decide(
        record,
        training=TrainingContext(now=datetime(2026, 4, 1), checkpoint_rank={record.pk: 4}),
        nas=nas,
    )
    assert fourth.action is ActionType.EVICT
    assert fourth.target_media is StorageMedia.OSS_ARCHIVE  # 历史版本转存 OSS 归档


def test_capacity_watermark_fires_strictly_above_eighty_percent():
    """「NAS 使用率 > 80% 触发水位淘汰，按 LRU 优先淘汰近 30 天无访问数据」。"""
    assert NAS_WATERMARK_USAGE == 0.80
    assert NAS_LRU_NO_ACCESS_DAYS == 30

    record = _nas_rec(access_count_30d=0)
    verified = frozenset({record.pk})
    training = TrainingContext(now=datetime(2026, 4, 1))

    at_line = decide(record, training=training, nas=NasContext(0.80, verified))
    assert at_line.action is not ActionType.EVICT, "80% 是「> 80%」的边界，等于不触发"

    above = decide(record, training=training, nas=NasContext(0.81, verified))
    assert above.action is ActionType.EVICT

    # 近 30 天有访问的不在 LRU 命中范围内
    warm_copy = _nas_rec(access_count_30d=5, file_path="/nas/other.mp4")
    kept = decide(
        warm_copy,
        training=training,
        nas=NasContext(0.95, frozenset({warm_copy.pk})),
    )
    assert kept.action is not ActionType.EVICT


def test_watermark_candidates_are_ordered_by_lru():
    """「按 LRU **优先**淘汰」——「优先」是排序要求，不排序等于没实现。"""
    old = _nas_rec(file_path="/nas/old.mp4", last_access_time=datetime(2026, 3, 2))
    recent = _nas_rec(file_path="/nas/recent.mp4", last_access_time=datetime(2026, 3, 25))
    never = _nas_rec(file_path="/nas/never.mp4", last_access_time=None)
    verified = frozenset({old.pk, recent.pk, never.pk})

    decisions = scan(
        [recent, old, never],
        training=TrainingContext(now=datetime(2026, 4, 1)),
        nas=NasContext(0.95, verified),
    )
    assert all(d.action is ActionType.EVICT for d in decisions)
    assert [d.record.file_path for d in decisions] == [
        "/nas/never.mp4",  # 从未访问 = 最久没被碰
        "/nas/old.mp4",
        "/nas/recent.mp4",
    ]
    # 只重排水位淘汰，其它动作保持输入顺序
    assert lru_evict_order([]) == []


def test_whitelisted_nas_copy_is_exempt_from_eviction():
    """第四条淘汰场景：「活跃调试 / 在研迭代数据打白名单标签，不参与自动淘汰」。"""
    record = _nas_rec(whitelist_flag=True, access_count_30d=0)
    decision = decide(
        record,
        training=TrainingContext(
            now=datetime(2026, 4, 1), training_finished_at={CASE_ID: datetime(2026, 3, 1)}
        ),
        nas=NasContext(0.99, frozenset({record.pk})),
    )
    assert decision.action is ActionType.HOLD


def test_checksum_mismatch_keeps_the_copy_and_still_points_at_oss():
    """铁律：「淘汰前必须校验 checksum 一致，不一致则告警并保留副本」。"""
    record = _nas_rec()
    decision = decide(
        record,
        training=TrainingContext(
            now=datetime(2026, 3, 20), training_finished_at={CASE_ID: datetime(2026, 3, 13)}
        ),
        nas=NasContext(checksum_verified=frozenset()),  # 未校验
    )
    assert decision.action is ActionType.BLOCKED
    assert decision.blocked_by == ("淘汰校验",)
    # 淘汰 ≠ 删除：即便被拦下，落点写的也还是 OSS 事实源
    assert decision.target_media is StorageMedia.OSS_STANDARD
    assert (
        check_source_of_truth(
            from_media=StorageMedia.NAS, to_media=decision.target_media, action="evict"
        )
        == []
    )


def test_evict_status_values_are_verbatim():
    """evict_status 取值：none / pending / done / skipped（原文第四章①）。"""
    assert [s.value for s in EvictStatus] == ["none", "pending", "done", "skipped"]


# ===========================================================================
# 七、案例逐行重放：240GB，¥3,226 → ¥203，降幅约 94%
# ===========================================================================


def test_case_cost_book_matches_the_source_yuan_by_yuan():
    """原文第四章成本账表格的每一个数字都要能算出来。"""
    result = replay_case_study()
    assert result["size_gb"] == 240.0
    assert result["data_id"] == CASE_ID

    baseline = result["baseline"]
    assert baseline["nas_yuan"] == 2880.0  # 240GB × 12 月 × 1.0
    assert baseline["oss_standard_yuan"] == 345.6  # 原文取整写 ¥346
    assert baseline["total_yuan"] == 3225.6  # 原文取整写 ¥3,226
    assert round(baseline["total_yuan"]) == 3226

    segments = {s["media"]: s["cost_yuan"] for s in result["governed"]["segments"]}
    assert segments["nas"] == 72.0  # 占用 9 天 ¥72
    assert segments["oss_standard"] == 57.6  # 2 个月，原文写 ¥58
    assert segments["oss_ia"] == 43.2  # 3 个月，原文写 ¥43
    assert segments["oss_archive"] == 30.24  # 7 个月，原文写 ¥30
    assert result["governed"]["total_yuan"] == 203.04  # 原文写 ¥203
    assert round(result["governed"]["total_yuan"]) == 203


def test_case_reduction_rate_is_ninety_four_percent():
    """降幅约 94%：精确值 93.71%，原文取整。差过 1 个百分点就说明常量漂了。"""
    result = replay_case_study()
    assert SOURCE_REDUCTION_RATE == 0.94
    assert result["reduction_rate"] == pytest.approx(0.9371, abs=1e-4)
    assert result["claim_verified"] is True
    # 基线口径必须连着说：只对 OSS 标准常驻的话降幅只有 41.25%
    assert result["reduction_vs_oss_only"] == pytest.approx(0.4125, abs=1e-4)


def test_nine_days_on_nas_costs_seventy_two_yuan():
    """「占用 9 天（预热 → 淘汰）¥72」——按 30 天/月折算才对得上。"""
    model = CostModel()
    assert model.cost_for_days(StorageMedia.NAS, 240, 9) == pytest.approx(72.0)


def test_case_timeline_replays_through_the_decision_engine():
    """七次状态流转不是文档插图——扫表算出来就该是这几个动作。"""
    training_only = TrainingContext(
        now=datetime(2026, 3, 11), active_preheat_data_ids=frozenset({CASE_ID})
    )

    # 03-11 训练任务创建，预热至 NAS
    preheat = decide(
        _rec(lineage_ref_count=1, last_access_time=datetime(2026, 3, 10)), training=training_only
    )
    assert preheat.action is ActionType.PREHEAT
    assert (preheat.target_stage, preheat.target_media) == (
        LifecycleStage.HOT,
        StorageMedia.NAS,
    )

    # 03-20 7 天缓冲期满且 checksum 校验通过，NAS 副本淘汰
    on_nas = _nas_rec(lineage_ref_count=1, last_access_time=datetime(2026, 3, 13))
    evict = decide(
        on_nas,
        training=TrainingContext(
            now=datetime(2026, 3, 20), training_finished_at={CASE_ID: datetime(2026, 3, 13)}
        ),
        nas=NasContext(checksum_verified=frozenset({on_nas.pk})),
    )
    assert evict.action is ActionType.EVICT
    assert evict.target_media is StorageMedia.OSS_STANDARD  # 淘汰 ≠ 删除

    # 04-30 30 天无访问且提档保留期满，自动降冷（成本降至 0.5x）
    tier_down = decide(
        _rec(lineage_ref_count=1, last_access_time=datetime(2026, 3, 20)),
        training=TrainingContext(now=datetime(2026, 4, 30)),
    )
    assert tier_down.action is ActionType.TIER_DOWN
    assert tier_down.target_media is StorageMedia.OSS_IA
    assert RELATIVE_PRICE[StorageMedia.OSS_IA] == 0.5


def test_preheating_archived_data_restores_it_first():
    """归档对象不可直读——预热命中归档态要先走取回（标准恢复 ≤ 4 小时）。"""
    decision = decide(
        _rec(
            storage_media="oss_archive",
            lifecycle_stage="archive",
            last_access_time=datetime(2026, 3, 20),
        ),
        training=TrainingContext(
            now=datetime(2026, 7, 29), active_preheat_data_ids=frozenset({CASE_ID})
        ),
    )
    assert decision.action is ActionType.RESTORE
    assert decision.target_media is StorageMedia.OSS_STANDARD
    assert f"≤ {ARCHIVE_RESTORE_SLA_HOURS} 小时" in decision.reason


def test_restore_resets_the_access_clock_so_data_stops_ping_ponging():
    """第四道闸：「取回后自动回升温层并**重置访问计时**」。

    只把访问次数清零是不够的——降冷判据看的是「连续多少天无访问」，
    取回的第二天就会被判成长期无访问再降回归档，来回弹一次就是一笔取回费。
    """
    archived = _rec(
        storage_media="oss_archive",
        lifecycle_stage="archive",
        last_access_time=datetime(2026, 3, 20),
        access_count_30d=3,
    )
    repo = InMemoryRepository([archived])
    run = GovernanceRun(repo)

    run.run_daily(training=TrainingContext(now=datetime(2027, 3, 1)), dry_run=False)
    restored = repo.load_records()[0]
    assert restored.lifecycle_stage is LifecycleStage.WARM
    assert restored.storage_media is StorageMedia.OSS_STANDARD
    assert restored.last_access_time == datetime(2027, 3, 1)  # 访问计时重置
    assert restored.access_count_30d == 0
    assert restored.stage_entered_at == datetime(2027, 3, 1)

    summary = run.run_daily(training=TrainingContext(now=datetime(2027, 3, 2)), dry_run=False)
    assert summary["actionable"] == 0, "取回第二天又被降冷 = 数据在两档之间来回弹"


# ===========================================================================
# 八、PB 级大文件外置：湖仓只存元信息
# ===========================================================================


def test_three_media_each_mind_their_own_segment():
    """原文第一章：OSS 是唯一事实源、数据湖是元信息中枢、NAS 只是训练加速缓存层。"""
    assert len(MEDIA_ROLES) == 3
    by_media = {r.media: r for r in MEDIA_ROLES}
    assert by_media["OSS 对象存储"].positioning == "海量低成本 · 数据归宿"
    assert by_media["数据湖（DLF + Paimon）"].positioning == "元信息中枢 · 治理大脑"
    assert by_media["CPFS / NAS 高性能存储"].positioning == "高吞吐 · 训练加速"

    # 全篇只有 OSS 是事实源，一个都不能多
    sources = [r.media for r in MEDIA_ROLES if r.is_source_of_truth]
    assert sources == ["OSS 对象存储"]
    # 数据湖不是介质——它一份文件本体都不存
    assert by_media["数据湖（DLF + Paimon）"].storage_media == ()


def test_lakehouse_stores_only_metadata():
    """数据湖存的五类内容全是元信息，没有一项是文件本体（原文第一章）。"""
    assert LAKEHOUSE_METADATA_CONTENT == (
        "文件元信息",
        "任务状态",
        "数据集版本与血缘",
        "生命周期状态",
        "访问记录",
    )
    # [a5] 第六章：大文件外置时湖仓保留的元信息四项
    assert len(EXTERNALIZED_META_FIELDS) == 4
    assert "checksum_md5" in EXTERNALIZED_META_FIELDS
    assert "file_size_bytes" in EXTERNALIZED_META_FIELDS


def test_flow_cycle_is_six_steps_and_one_way():
    """「文件落 OSS → 元信息入湖 → 预热上 NAS → 训练读写 → 淘汰回 OSS → 分层降冷」。"""
    assert len(FLOW_CYCLE) == 6
    assert FLOW_CYCLE[0] == "文件落 OSS（唯一事实源）"
    assert FLOW_CYCLE[-1] == "OSS 按规则分层降冷"


def test_externalization_check_catches_a_body_that_leaked_into_the_lake():
    """湖仓行必须是指针 + 元信息，指不回对象存储就是外置没做干净。"""
    assert check_externalized(_rec()) == []

    bad_path = check_externalized(_rec(file_path="clip.mp4"))
    assert any("对象存储 URI" in p for p in bad_path)

    no_size = check_externalized(_rec(file_size_bytes=0))
    assert any("file_size_bytes" in p for p in no_size)

    nas_no_checksum = check_externalized(
        _rec(storage_media="nas", lifecycle_stage="hot", file_path="/nas/clip.mp4")
    )
    assert any("checksum_md5" in p for p in nas_no_checksum)


def test_source_of_truth_rules_reject_an_eviction_that_loses_the_oss_copy():
    """淘汰只能把副本还回 OSS，落点为空或落回 NAS 都是在毁事实源。"""
    assert (
        check_source_of_truth(
            from_media=StorageMedia.NAS, to_media=StorageMedia.OSS_STANDARD, action="evict"
        )
        == []
    )
    assert check_source_of_truth(from_media=StorageMedia.NAS, to_media=None, action="evict")
    assert check_source_of_truth(
        from_media=StorageMedia.NAS, to_media=StorageMedia.NAS, action="evict"
    )


def test_externalization_leverage_scales_to_pb():
    """PB 级下外置的意义：湖仓拿 KB 级元信息管住 PB 级本体。"""
    # 4096 份 240GB 的 clip ≈ 0.9375 PB
    records = [_rec(file_path=f"s3://adas-raw/clip_{i}.mp4") for i in range(4096)]
    report = externalization_leverage(records)
    assert report["row_count"] == 4096
    assert report["externalized_tb"] == pytest.approx(4096 * 240 / 1024, rel=1e-6)
    assert report["externalized_pb"] == pytest.approx(0.9375, rel=1e-6)
    assert report["leverage_x"] > 100_000_000
    assert externalization_leverage([])["leverage_x"] == 0.0


def test_growth_sources_and_loss_of_control_are_registered():
    """引言的六大增长源与三重失控——治理的立项依据。"""
    assert len(GROWTH_SOURCES) == 6
    assert len(LOSS_OF_CONTROL) == 3
    assert [name for name, _ in LOSS_OF_CONTROL] == [
        "高价介质被滥用",
        "冷数据占据热层",
        "成本指数级增长",
    ]
    assert len(REUSABLE_LESSONS) == 3
    assert [name for name, _ in REUSABLE_LESSONS] == [
        "快照即决策",
        "血缘定生死",
        "个体稳态即全局稳态",
    ]


# ===========================================================================
# 九、月末复盘、告警线与五步闭环
# ===========================================================================


def test_monthly_review_numbers_are_verbatim():
    """月末复盘场景：1.8PB / ¥58 万 / 6-24-38-32 / 210TB / ¥9.6 万 / 1.8% vs 35%。"""
    r = SOURCE_MONTHLY_REVIEW
    assert r.total_capacity_pb == 1.8
    assert r.monthly_cost_yuan == 580_000.0
    assert r.tier_mix[StorageMedia.NAS] == 0.06
    assert r.tier_mix[StorageMedia.OSS_STANDARD] == 0.24
    assert r.tier_mix[StorageMedia.OSS_IA] == 0.38
    assert r.tier_mix[StorageMedia.OSS_ARCHIVE] == 0.32
    assert r.released_tb == 210.0
    assert r.saved_yuan == 96_000.0
    assert r.ingested_tb == 190.0
    assert r.cost_mom_growth == 0.018
    assert r.data_growth == 0.35
    assert r.preheat_hit_rate == 0.91
    assert r.archive_restore_per_day == 23

    check = r.self_check()
    assert check["tier_mix_sums_to_one"] is True
    assert check["matches_one_twentieth"] is True  # 1.8% / 35% ≈ 1/19.4 ≈ 1/20
    assert COST_VS_DATA_GROWTH_RATIO == 1 / 20
    # 释放 210TB 与新增 190TB 对冲后净增 −20TB
    assert r.net_capacity_growth_tb == -20.0


def test_budget_alert_lines_fire_strictly_above_the_threshold():
    """两条预算告警线：成本环比 > 10%、NAS 使用率持续 > 80%。"""
    assert budget_alerts(cost_mom_growth=0.10, nas_usage_ratio=0.80) == []
    alerts = budget_alerts(cost_mom_growth=0.11, nas_usage_ratio=0.81)
    assert {a["code"] for a in alerts} == {"cost_mom_growth", "nas_usage"}
    assert [a["threshold"] for a in alerts] == [0.10, 0.80]
    # 「持续」> 80% 才告警，瞬时尖峰不告警
    assert budget_alerts(cost_mom_growth=0.0, nas_usage_ratio=0.95, sustained=False) == []


def test_archive_restore_sla_is_four_hours():
    assert ARCHIVE_RESTORE_SLA_HOURS == 4


def test_pipeline_is_five_steps_and_gates_are_four():
    """五步日级调度闭环 + 四道安全闸（原文第五章两张表）。"""
    assert [name for name, *_ in PIPELINE_STEPS] == [
        "扫描决策",
        "演练审计",
        "执行",
        "回写",
        "告警",
    ]
    assert PIPELINE_STEPS[0][1] == "StarRocks 定时任务（T+1）"
    assert [name for name, _ in SAFETY_GATES] == [
        "删除三重确认",
        "淘汰校验",
        "审计留痕",
        "冷数据取回",
    ]


# ===========================================================================
# 十、回写：成本日表不能少列，否则看板上就是一片 NULL
# ===========================================================================


def test_cost_daily_carries_every_column_the_registry_declares():
    """注册表有、本模块不写的列 = 落表就是 NULL，下游 ADS 看板直接读空。"""
    spec = registry.by_name("dws_closed_loop_storage_cost_daily")
    # 系统字段由 spec 层落表时自动追加，不由业务模块写，故不计入
    declared = {c.name for c in spec.columns}
    missing = declared - set(COST_DAILY_COLUMNS)
    assert missing == set(), f"成本日表少写这些列: {sorted(missing)}"


def test_lifecycle_detail_carries_every_column_the_registry_declares():
    spec = registry.by_name("dwd_closed_loop_storage_lifecycle")
    # 系统字段由 spec 层落表时自动追加，不由业务模块写，故不计入
    declared = {c.name for c in spec.columns}
    missing = declared - set(LIFECYCLE_COLUMNS)
    assert missing == set(), f"明细表少写这些列: {sorted(missing)}"


def test_row_serialisation_covers_the_declared_column_order():
    assert set(_rec().to_row()) == set(LIFECYCLE_COLUMNS)
    row = CostDailyRow(
        stat_date=date(2026, 7, 29),
        storage_media=StorageMedia.OSS_IA,
        lifecycle_stage=LifecycleStage.COLD,
        data_type="raw",
        source_domain="collect",
    )
    assert set(row.to_row()) == set(COST_DAILY_COLUMNS)


def test_cost_saving_is_computed_not_left_null():
    """原文第六章看板指标：「成本节省额 = 治理释放成本 = 无治理基线成本 − 实际成本」。

    240GB 躺在低频存储上，日成本 = 240 × 0.06 / 30 = ¥0.48，
    基线（全放标准存储）= 240 × 0.12 / 30 = ¥0.96，省下 ¥0.48。
    """
    rows = aggregate_cost_daily(
        [_rec(storage_media="oss_ia", lifecycle_stage="cold")],
        stat_date=date(2026, 7, 29),
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.file_count == 1
    assert row.daily_cost_yuan == pytest.approx(0.48)
    assert row.baseline_cost_yuan == pytest.approx(0.96)
    assert row.saved_cost_yuan == pytest.approx(0.48)
    assert row.cost_mom_rate == 0.0  # 没有昨天可比，写 0 比写假值诚实


def test_cost_mom_rate_compares_against_yesterday():
    """成本环比要有昨天才算得出来——它是第一条预算告警线的输入。"""
    yesterday = aggregate_cost_daily(
        [_rec(storage_media="oss_standard", lifecycle_stage="warm")],
        stat_date=date(2026, 4, 29),
    )
    today = aggregate_cost_daily(
        [
            _rec(
                storage_media="oss_standard",
                lifecycle_stage="warm",
                file_size_bytes=2 * CASE_GB * GB,
            )
        ],
        stat_date=date(2026, 4, 30),
        previous_day=yesterday,
    )
    assert today[0].cost_mom_rate == pytest.approx(1.0)  # 容量翻倍 → 环比 +100%
    assert budget_alerts(cost_mom_growth=today[0].cost_mom_rate, nas_usage_ratio=0.0)


def test_deleted_rows_stop_costing_money():
    rows = aggregate_cost_daily(
        [_rec(storage_media="oss_standard", lifecycle_stage="deleted")],
        stat_date=date(2026, 7, 29),
    )
    assert rows == []
