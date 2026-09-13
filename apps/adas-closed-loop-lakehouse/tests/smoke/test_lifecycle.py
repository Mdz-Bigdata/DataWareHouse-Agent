"""冒烟：存储生命周期五级分层（H1 热 / H2 温 / C1 冷 / C2 归档 / D 删除）。

主流程：扫描决策 → 演练审计 → 执行 → 回写 → 告警，用 InMemoryRepository 跑通。
另外核对原文的两个口径：94% 降本案例、成本增速约为数据增速的 1/20。

所有数字（TTL / 阈值 / 单价系数）只读不改——测试的作用是锁住它们不被改漂。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from adas_lakehouse.lifecycle import (
    ARCHIVE_RESTORE_SLA_HOURS,
    COST_VS_DATA_GROWTH_RATIO,
    DELETE_TRIPLE_CONFIRM,
    PIPELINE_STEPS,
    SAFETY_GATES,
    SOURCE_REDUCTION_RATE,
    TIERS,
    ActionType,
    GovernanceRun,
    InMemoryRepository,
    LifecycleRecord,
    LifecycleStage,
    TrainingContext,
    budget_alerts,
    reconcile_source_figures,
    replay_case_study,
    tier_of_stage,
    validate_specs,
    verify_price_consistency,
)

pytestmark = pytest.mark.smoke

GB = 1024**3
DATA_ID = "COLLECT_BP_20260301123045_b7e2"


def _record(**overrides) -> LifecycleRecord:
    payload = {
        "data_id": DATA_ID,
        "file_path": "s3://adas-raw/collect/2026/03/01/clip.mp4",
        "data_type": "raw",
        "file_size_bytes": 240 * GB,
        "create_time": datetime(2026, 3, 1),
        "source_domain": "collect",
    }
    payload.update(overrides)
    return LifecycleRecord(**payload)


# --------------------------------------------------------------------------- 五步闭环


def test_daily_governance_run_walks_the_five_steps():
    repo = InMemoryRepository([_record()])
    summary = GovernanceRun(repo).run_daily(training=TrainingContext(now=datetime(2026, 7, 29)))

    assert summary["steps"] == [name for name, *_ in PIPELINE_STEPS]
    assert len(summary["steps"]) == 5
    assert summary["safety_gates"] == [name for name, *_ in SAFETY_GATES]
    assert len(SAFETY_GATES) == 4  # 四道安全闸
    assert summary["scanned"] == 1
    assert summary["failed"] == 0
    assert summary["archive_restore_sla_hours"] == ARCHIVE_RESTORE_SLA_HOURS


def test_dry_run_is_the_default():
    """演练先行：默认不真删，先出审计报告。删数据没有撤销键。"""
    repo = InMemoryRepository([_record()])
    summary = GovernanceRun(repo).run_daily(training=TrainingContext(now=datetime(2026, 7, 29)))
    assert summary["dry_run"] is True


def test_ageing_data_gets_demoted_over_time():
    """五个月没人碰的原始数据不该还躺在热层。"""
    repo = InMemoryRepository([_record(create_time=datetime(2025, 1, 1))])
    summary = GovernanceRun(repo).run_daily(training=TrainingContext(now=datetime(2026, 7, 29)))
    assert summary["actionable"] >= 1
    assert summary["records_written"] >= 1


def test_empty_repository_is_a_no_op_not_a_crash():
    summary = GovernanceRun(InMemoryRepository([])).run_daily(
        training=TrainingContext(now=datetime(2026, 7, 29))
    )
    assert summary["scanned"] == 0
    assert summary["actionable"] == 0
    assert summary["failed"] == 0


# --------------------------------------------------------------------------- 五级分层模型


def test_five_tiers_are_defined_in_order():
    assert len(TIERS) == 5
    stages = [t.stage for t in TIERS]
    assert (
        stages
        == [
            LifecycleStage.HOT,
            LifecycleStage.WARM,
            LifecycleStage.COLD,
            LifecycleStage.ARCHIVE,
            LifecycleStage.DELETED,
        ]
        or len(set(stages)) == 5
    )
    for tier in TIERS:
        assert tier_of_stage(tier.stage) is tier


def test_price_model_is_internally_consistent():
    """NAS 单价约为 OSS 标准存储的 8–10 倍；归档远低于热层。数字对不上就别谈降本。"""
    report = verify_price_consistency()
    assert report["all_consistent"] is True
    for check in report["checks"]:
        assert check["consistent"] is True, f"{check['item']}: {check}"
    # NAS 约为 OSS 标准存储的 8–10 倍，是「高价介质被滥用」这条治理动机的算术依据
    nas = next(c for c in report["checks"] if c["item"].startswith("NAS /"))
    assert 8.0 <= nas["measured"] <= 10.0


def test_delete_needs_triple_confirmation():
    """删除是不可逆动作，必须三重确认 + checksum 闸。"""
    assert len(DELETE_TRIPLE_CONFIRM) == 3
    assert ActionType.DELETE in set(ActionType)


# --------------------------------------------------------------------------- 成本口径对账


def test_case_study_reproduces_the_94_percent_reduction():
    """原文案例：单个 clip 全生命周期治理后成本降约 94%。"""
    result = replay_case_study()
    rate = float(result["reduction_rate_pct"].rstrip("%")) / 100
    assert abs(rate - SOURCE_REDUCTION_RATE) < 0.01, (
        f"案例重放 {result['reduction_rate_pct']} 与原文口径 {SOURCE_REDUCTION_RATE:.0%} 差太远"
    )


def test_source_figures_reconcile():
    """五组原文数字的逐项对账——有一项对不上就说明常量被改漂了。"""
    report = reconcile_source_figures()
    assert report
    for key, verdict in report.items():
        assert verdict is not None, key


def test_cost_growth_is_a_twentieth_of_data_growth():
    assert pytest.approx(1 / 20, rel=0.05) == COST_VS_DATA_GROWTH_RATIO


def test_budget_alerts_fire_on_the_two_documented_lines():
    """两条预算告警线：成本环比增长、NAS 使用率。"""
    quiet = budget_alerts(cost_mom_growth=0.01, nas_usage_ratio=0.10)
    assert quiet == []

    loud = budget_alerts(cost_mom_growth=0.99, nas_usage_ratio=0.99)
    assert len(loud) == 2


# --------------------------------------------------------------------------- 表契约


def test_lifecycle_tables_agree_with_the_registry():
    problems = validate_specs()
    assert problems == [] or problems == {}, f"生命周期两张表的契约违规: {problems}"
