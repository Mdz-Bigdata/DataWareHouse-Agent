"""冒烟：规则挖掘——可视化规则 → SQL 编译 → 批/流执行。

主流程：RuleDefinition + 水位 → RuleCompiler 编译出 count/select/insert 三段 SQL。
编译是纯函数，不连任何引擎；执行器只在假后端上跑。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from adas_lakehouse.mining.compiler import CompileError, RuleCompiler
from adas_lakehouse.mining.rules import (
    SAMPLE_RULES,
    ConditionGroup,
    Dialect,
    ExecutionMode,
    RuleDefinition,
    RulePriority,
    RuleStatus,
    RuleType,
    TagCondition,
)
from adas_lakehouse.mining.watermark import InMemoryWatermarkStore, Watermark

pytestmark = pytest.mark.smoke

LOW = datetime(2026, 3, 1)
HIGH = datetime(2026, 3, 2)
RUN_ID = "run_mining_20260302000000_deadbeef"


def _watermark(rule_id: str) -> Watermark:
    return Watermark(
        rule_id=rule_id,
        table="dwd_collect_clip_detail",
        column="create_time",
        low=LOW,
        high=HIGH,
    )


def _compile(rule: RuleDefinition):
    return RuleCompiler().compile_batch(rule, _watermark(rule.rule_id), run_id=RUN_ID, now=HIGH)


# --------------------------------------------------------------------------- 编译主流程


def test_tag_combination_rule_compiles_to_runnable_sql():
    rule = next(r for r in SAMPLE_RULES if r.rule_type is RuleType.TAG_COMBINATION)
    query = _compile(rule)

    assert query.rule_id == rule.rule_id
    assert query.execution_mode is ExecutionMode.BATCH_T_PLUS_1
    assert query.dialect is Dialect.SPARK
    assert query.scan_tables
    assert "SELECT COUNT(1)" in query.count_sql
    assert "SELECT" in query.select_sql.upper()
    assert "INSERT INTO" in query.insert_sql.upper()


def test_compiled_sql_carries_the_incremental_watermark():
    """增量扫描靠水位，不是每天全表重扫——这是 4 小时 SLA 的前提。"""
    rule = next(r for r in SAMPLE_RULES if r.rule_type is RuleType.TAG_COMBINATION)
    query = _compile(rule)
    assert "2026-03-01" in query.count_sql
    assert "2026-03-02" in query.count_sql
    assert query.watermark.low == LOW and query.watermark.high == HIGH


def _compile_any(rule: RuleDefinition):
    """按规则自己的执行模式选编译入口：批的走 compile_batch，准实时的走 compile_stream。"""
    if rule.effective_mode is ExecutionMode.BATCH_T_PLUS_1:
        return _compile(rule)
    return RuleCompiler().compile_stream(rule, run_id=RUN_ID, now=HIGH)


def test_every_sample_rule_compiles():
    """六大规则种类各造一条示例，全部要能编译过——编译期报错胜过线上报错。"""
    assert len(SAMPLE_RULES) >= 6
    kinds = set()
    for rule in SAMPLE_RULES:
        query = _compile_any(rule)
        assert query.select_sql
        assert query.scan_tables
        kinds.add(rule.rule_type)
    assert len(kinds) >= 5


def test_batch_and_stream_entrypoints_do_not_cross():
    """准实时规则不许编成批查询，反之亦然——选错模式等于线上跑错引擎。"""
    stream_rule = next(
        r for r in SAMPLE_RULES if r.effective_mode is not ExecutionMode.BATCH_T_PLUS_1
    )
    with pytest.raises(CompileError, match="不能编译成批查询"):
        _compile(stream_rule)

    query = RuleCompiler().compile_stream(stream_rule, run_id=RUN_ID, now=HIGH)
    assert query.execution_mode is not ExecutionMode.BATCH_T_PLUS_1


def test_conditions_are_bound_not_interpolated_blindly():
    """标签取值出现在 IN 列表里，且被正确引号化——防 SQL 注入的第一道。"""
    rule = RuleDefinition(
        rule_id="RULE_TEST_QUOTE",
        rule_name="引号测试",
        rule_type=RuleType.TAG_COMBINATION,
        rule_status=RuleStatus.ENABLED,
        rule_priority=RulePriority.P2,
        scene_label="quote_test",
        target_clip_count=1,
        visual_config=ConditionGroup("AND", (TagCondition("weather", ("rain",)),)),
    )
    query = _compile(rule)
    assert "'rain'" in query.count_sql


def test_result_rows_carry_the_run_id_and_rule_version():
    rule = SAMPLE_RULES[0]
    query = _compile(rule)
    assert RUN_ID in query.insert_sql or RUN_ID in query.select_sql
    assert query.rule_version >= 1


def test_insert_targets_the_registered_mining_result_table():
    from adas_lakehouse.catalog import registry

    query = _compile(SAMPLE_RULES[0])
    target = "dwd_mining_result_detail"
    assert target in query.insert_sql
    assert registry.by_name(target)


def test_scan_sources_are_registered_tables_or_declared_streams():
    """扫描源只有两种合法身份：registry 登记的湖仓表，或 tables 里声明的流源。

    第三种——「随手编一个 dwd_* 表名当占位」——是被禁掉的那种：
    它看起来像湖仓表，实际不在 88 张里，SQL 打到 Paimon 上才报表不存在。
    """
    from adas_lakehouse.catalog import registry
    from adas_lakehouse.mining.tables import VEHICLE_SIGNAL_STREAM

    known = {t.name for t in registry.all_tables()} | {VEHICLE_SIGNAL_STREAM.resolve()}
    for rule in SAMPLE_RULES:
        for table in _compile_any(rule).scan_tables:
            assert table in known, f"{rule.rule_id} 扫描了未登记的源 {table!r}"


def test_signal_stream_is_not_faked_as_a_lakehouse_table():
    """信号流不许叫 ods_/dwd_/dws_/ads_ 开头的名字，也不许套 Paimon 三段式前缀。"""
    from adas_lakehouse.mining.rules import RuleType
    from adas_lakehouse.mining.tables import VEHICLE_SIGNAL_STREAM

    name = VEHICLE_SIGNAL_STREAM.resolve()
    assert not name.startswith(("ods_", "dwd_", "dws_", "ads_"))

    rule = next(r for r in SAMPLE_RULES if r.rule_id == "RULE_SIGNAL_HARSH_DECEL")
    sql = RuleCompiler().compile_stream(rule, run_id=RUN_ID, now=HIGH).select_sql
    assert f"FROM `{name}`" in sql
    assert rule.rule_type is RuleType.VEHICLE_SIGNAL


def test_harsh_decel_reference_sql_keeps_the_only_source_numbers():
    """原文唯一带数字的规则：CAN 减速度 < -4m/s² 持续 ≥ 0.5s。数字不许改。"""
    from adas_lakehouse.mining.compiler import harsh_decel_reference_sql
    from adas_lakehouse.mining.rules import (
        HARSH_DECEL_MIN_DURATION_SEC,
        HARSH_DECEL_THRESHOLD_MPS2,
    )

    assert HARSH_DECEL_THRESHOLD_MPS2 == -4.0
    assert HARSH_DECEL_MIN_DURATION_SEC == 0.5

    sql = harsh_decel_reference_sql()
    assert "-4" in sql
    assert "0.5" in sql


# --------------------------------------------------------------------------- 列契约锚定 registry


def test_column_contracts_are_derived_from_the_registry_not_hand_written():
    """四组列契约必须逐字等于 registry，一个字都不许自己写。

    这条如果松了，子系统就能悄悄多出一列 registry 没有的名字——
    SQL 照样拼得出来，打到真实 Paimon 上才炸；更坏的情况是列名近义异名，
    写进去一列没人读、读出来永远 NULL。
    """
    from adas_lakehouse.catalog import registry
    from adas_lakehouse.mining import tables as mt

    for const, table in (
        (mt.MINING_RESULT_COLUMNS, "dwd_mining_result_detail"),
        (mt.SCENE_GAP_COLUMNS, "dwd_scene_gap_detail"),
        (mt.MINING_TASK_COLUMNS, "dwd_mining_task_detail"),
        (mt.RULE_CONFIG_COLUMNS, "ods_mining_rule_config"),
    ):
        assert list(const) == [c.name for c in registry.by_name(table).all_columns()], table


def test_write_projections_cover_the_primary_key_and_nothing_unregistered():
    """引擎写入的列必须 ⊆ registry，且必须写全主键——少一段主键就 Upsert 不进去。"""
    from adas_lakehouse.catalog import registry
    from adas_lakehouse.mining import tables as mt

    for cols, table in (
        (mt.RESULT_WRITE_COLUMNS, "dwd_mining_result_detail"),
        (mt.SCENE_GAP_WRITE_COLUMNS, "dwd_scene_gap_detail"),
        (mt.MINING_TASK_WRITE_COLUMNS, "dwd_mining_task_detail"),
        (mt.RULE_CONFIG_CDC_COLUMNS, "ods_mining_rule_config"),
    ):
        spec = registry.by_name(table)
        known = {c.name for c in spec.all_columns()}
        assert set(cols) <= known, f"{table} 写了 registry 没有的列 {set(cols) - known}"
        assert set(spec.primary_key) <= set(cols), f"{table} 的写入投影缺主键段"


def test_projection_refuses_a_column_the_registry_does_not_have():
    """投影里写错一个列名要在 import 期就炸，而不是留到 SQL 执行期。"""
    from adas_lakehouse.mining.tables import DWD_MINING_RESULT_DETAIL, projection

    projection(DWD_MINING_RESULT_DETAIL, "data_id", "rule_id")  # 正常放行
    with pytest.raises(ValueError, match="没有这些列"):
        projection(DWD_MINING_RESULT_DETAIL, "data_id", "scene_label")


def test_row_writers_emit_registry_column_names_only():
    """三个 to_row() 产出的键必须全是 registry 的列名，顺序对齐写入投影。"""
    from adas_lakehouse.catalog import registry
    from adas_lakehouse.mining import tables as mt
    from adas_lakehouse.mining.executor import RuleRunRecord
    from adas_lakehouse.mining.gaps import evaluate_gap

    rule = SAMPLE_RULES[0]
    cases = [
        (rule.to_row(), "ods_mining_rule_config", None),
        (
            evaluate_gap(rule, 120, run_id=RUN_ID, now=HIGH).to_row(),
            "dwd_scene_gap_detail",
            mt.SCENE_GAP_WRITE_COLUMNS,
        ),
        (
            RuleRunRecord(
                task_id="t1",
                run_id=RUN_ID,
                rule_id=rule.rule_id,
                rule_version=rule.rule_version,
                execution_mode=ExecutionMode.BATCH_T_PLUS_1,
                executed_at=HIGH,
            ).to_row(),
            "dwd_mining_task_detail",
            mt.MINING_TASK_WRITE_COLUMNS,
        ),
    ]
    for row, table, order in cases:
        known = {c.name for c in registry.by_name(table).all_columns()}
        assert set(row) <= known, f"{table} 多出 registry 没有的键 {set(row) - known}"
        if order is not None:
            assert list(row) == list(order), table


def test_rule_config_row_round_trips_through_registry_column_names():
    """规则 -> registry 列名的行 -> 规则：一圈下来语义不掉。"""
    from adas_lakehouse.mining.config_sync import rule_from_row

    for rule in SAMPLE_RULES:
        back = rule_from_row(rule.to_row())
        assert back.rule_id == rule.rule_id
        assert back.rule_type is rule.rule_type
        assert back.rule_priority is rule.rule_priority  # INT 列 <-> P0-P3 枚举
        assert back.rule_version == rule.rule_version  # STRING 列 <-> int
        assert back.scene_label == rule.scene_label  # target_tag_id
        assert back.effective_mode is rule.effective_mode  # exec_mode
        assert back.expression_mode is rule.expression_mode  # express_mode


def test_backfill_dispatch_writes_only_registered_columns():
    """补抽帧交接走 registry 已有的那四列，不另起一张私有请求表。"""
    from adas_lakehouse.catalog import registry
    from adas_lakehouse.mining.backends import (
        BackfillRequest,
        DryRunBackend,
        LakehouseBackfillDispatcher,
    )

    captured: list[str] = []

    class _Capture(DryRunBackend):
        def execute(self, sql: str) -> int:
            captured.append(sql)
            return 1

    dispatcher = LakehouseBackfillDispatcher(backend=_Capture())
    n = dispatcher.dispatch(
        [
            BackfillRequest.for_event(
                data_id=f"clip_{i}", rule_id="RULE_X", run_id=RUN_ID, event_time=HIGH
            )
            for i in range(3)
        ]
    )
    assert n == 3
    assert len(captured) == 1  # 同窗口的请求合成一条语句

    sql = captured[0]
    known = {c.name for c in registry.by_name("dwd_mining_result_detail").all_columns()}
    for col in ("frame_supplement_status", "event_window_start_time", "event_window_end_time"):
        assert f"`{col}`" in sql and col in known
    assert "dwd_mining_result_detail" in sql
    assert "backfill_request" not in sql


def test_stream_hits_feed_the_execution_trace():
    """流命中的打标量与补抽帧量要回填进执行追溯，否则那几列永远是 0/false。"""
    from adas_lakehouse.mining.backends import (
        DryRunBackend,
        FrameBackfillDispatcher,
        InMemoryResultSink,
        InMemoryTagService,
    )
    from adas_lakehouse.mining.executor import RuleRunRecord, StreamRuleExecutor

    class _Counting(FrameBackfillDispatcher):
        def dispatch(self, requests) -> int:
            return len(requests)

    rule = next(r for r in SAMPLE_RULES if r.rule_id == "RULE_EVENT_DRIVER_TAKEOVER")
    executor = StreamRuleExecutor(
        backend=DryRunBackend(),
        task_sink=InMemoryResultSink(),
        tag_service=InMemoryTagService(),
        backfill=_Counting(),
    )
    record = RuleRunRecord(
        task_id="t1",
        run_id=RUN_ID,
        rule_id=rule.rule_id,
        rule_version=rule.rule_version,
        execution_mode=ExecutionMode.NEAR_REALTIME,
        executed_at=HIGH,
    )
    hits = [{"data_id": f"clip_{i}", "event_time": HIGH} for i in range(2)]
    tagged, dispatched = executor.handle_hits(rule, hits, run_id=RUN_ID, now=HIGH, record=record)

    assert tagged == 2 and dispatched == 2
    assert record.to_row()["tag_write_count"] == 2
    assert record.to_row()["frame_supplement_triggered"] is True


# --------------------------------------------------------------------------- 规则生命周期


def test_rule_status_transitions_produce_a_change_record():
    """规则改动要留痕，否则回头没人说得清「那天的口径是哪版」。"""
    rule = SAMPLE_RULES[0]
    disabled, change = rule.disable(by="tester", reason="冒烟测试", at=HIGH)
    assert disabled.rule_status is RuleStatus.DISABLED
    assert change is not None
    assert rule.rule_status is RuleStatus.ENABLED  # 原对象不被就地改

    enabled, _ = disabled.enable(by="tester", at=HIGH)
    assert enabled.rule_status is RuleStatus.ENABLED


def test_raw_sql_condition_rejects_dangerous_statements():
    from adas_lakehouse.mining.rules import RawSqlCondition, RuleValidationError

    RawSqlCondition("clip.weather = 'rain'")  # 正常谓词放行
    for danger in ("DROP TABLE clips", "1=1; DELETE FROM clips", "INSERT INTO x VALUES (1)"):
        with pytest.raises(RuleValidationError):
            RawSqlCondition(danger)


# --------------------------------------------------------------------------- 水位存储


def test_watermark_store_round_trip():
    """水位存控制面，不存湖仓；commit 必须在结果落表之后，否则会丢数据。"""
    store = InMemoryWatermarkStore()
    wm = _watermark("RULE_X")

    assert store.get("RULE_X", wm.table) is None  # 从未跑过
    store.commit(wm)
    loaded = store.get("RULE_X", wm.table)
    assert loaded is not None and loaded.high == HIGH

    assert store.reset("RULE_X") >= 1
    assert store.get("RULE_X", wm.table) is None  # 重置后触发全量回扫


def test_compile_error_is_raised_for_an_unresolvable_column():
    """strict 模式下引用不存在的列必须在编译期炸，不能带着错列上线。"""
    rule = RuleDefinition(
        rule_id="RULE_TEST_BAD_COLUMN",
        rule_name="坏列",
        rule_type=RuleType.TAG_COMBINATION,
        rule_status=RuleStatus.ENABLED,
        rule_priority=RulePriority.P2,
        scene_label="bad_column",
        target_clip_count=1,
        visual_config=ConditionGroup("AND", (TagCondition("no_such_column_at_all", ("x",)),)),
    )
    with pytest.raises(CompileError):
        RuleCompiler().compile_batch(
            rule, _watermark(rule.rule_id), run_id=RUN_ID, now=HIGH, strict=True
        )
