"""数据资产治理三件套的测试：分区体检 / 本体↔物理 schema 漂移 / 数据新鲜度。

对应报告 §5.2 的三条差距：
  #14 Doris 分区已过期（init/doris 的静态分区末档写满，跑批每天失败）
  #13 本体声明与物理 schema 无任何校验，漂移只在问数失败时暴露
  #5  查询结果没有「数据截止到什么时候」的声明

运行：cd backend && PYTHONPATH=. ./venv/bin/python -m pytest tests/test_gap_assets.py -q
"""
from __future__ import annotations

import io
import contextlib
import os
import sqlite3
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from app.service.freshness import FreshnessService, extract_tables
from app.service.partition_guard import (
    main as partition_main,
    parse_partitioned_tables,
    scan_paths,
    scan_sql_text,
)
from app.service.schema_drift import (
    DictSchemaReader,
    SqliteSchemaReader,
    check_schema_drift,
    declarations_from_objects,
    type_family,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DORIS_INIT_DIR = REPO_ROOT / "init" / "doris"
TRADE_SUMMARY_DDL = DORIS_INIT_DIR / "dws_trade_order_summary_daily.sql"
PARTITION_MIGRATION = (DORIS_INIT_DIR / "migrations"
                       / "20260913_dws_trade_order_summary_daily_partitions.sql")
SAMPLE_DB = REPO_ROOT / "apps" / "data-agent-engine" / "backend" / "seed" / "sample.db"

TODAY = date(2026, 9, 13)

# 修复之前 init/doris/dws_trade_order_summary_daily.sql 的原文（末档 2026-08-01，
# 无 dynamic_partition）。保留在这里，是为了锁住「体检器认得出这种表」这件事。
EXPIRED_DDL = """
CREATE TABLE IF NOT EXISTS dw_store.dws_trade_order_summary_daily (
    dt DATE COMMENT "分区日期 (YYYY-MM-DD)",
    region_id INT COMMENT "区域 ID",
    gmv DOUBLE COMMENT "总交易额 (GMV)"
)
UNIQUE KEY(dt, region_id)
PARTITION BY RANGE(dt) (
    PARTITION p_202605 VALUES LESS THAN ("2026-06-01"),
    PARTITION p_202606 VALUES LESS THAN ("2026-07-01"),
    PARTITION p_202607 VALUES LESS THAN ("2026-08-01")
)
DISTRIBUTED BY HASH(region_id) BUCKETS 8
PROPERTIES (
    "replication_allocation" = "tag.location.default: 1",
    "compression" = "zstd"
);
"""


def _kinds(findings) -> set[str]:
    return {f.kind for f in findings}


# ══════════════════════════════════════════════════════════════════════
# #14 Doris 分区体检
# ══════════════════════════════════════════════════════════════════════
class PartitionGuardTests(unittest.TestCase):
    def test_expired_static_range_is_reported_as_error(self):
        findings = scan_sql_text(EXPIRED_DDL, source="old.sql", today=TODAY)
        self.assertEqual(_kinds(findings), {"partition_range_expired"})
        self.assertEqual(findings[0].severity, "error")
        self.assertEqual(findings[0].last_boundary, "2026-08-01")
        self.assertFalse(findings[0].dynamic_enabled)
        self.assertIn("dws_trade_order_summary_daily", findings[0].table)

    def test_range_about_to_fill_up_is_warned_before_it_breaks(self):
        # 末档 2026-08-01，基准日提前到 7 月中：还没坏，但必须先叫一声
        findings = scan_sql_text(EXPIRED_DDL, source="old.sql",
                                 today=date(2026, 7, 15), warn_days=31)
        self.assertEqual(_kinds(findings), {"partition_range_expiring"})
        self.assertEqual(findings[0].severity, "warn")

    def test_range_with_plenty_of_headroom_is_silent(self):
        findings = scan_sql_text(EXPIRED_DDL, source="old.sql",
                                 today=date(2026, 1, 1), warn_days=31)
        self.assertEqual(findings, [])

    def test_shipped_trade_summary_ddl_is_healthy_now_and_later(self):
        """修好的建表脚本：今天干净，两年后也干净（因为开了动态分区）。"""
        text = TRADE_SUMMARY_DDL.read_text(encoding="utf-8")
        for day in (TODAY, date(2027, 12, 31), date(2030, 6, 1)):
            self.assertEqual(scan_sql_text(text, source=str(TRADE_SUMMARY_DDL), today=day), [],
                             f"{TRADE_SUMMARY_DDL} 在 {day} 出现分区体检发现")

    def test_shipped_ddl_backfills_the_gap_and_enables_rolling_partitions(self):
        """跑批从 2026-08-01 起失败：缺口分区必须补上，且此后自动滚动。"""
        specs = parse_partitioned_tables(TRADE_SUMMARY_DDL.read_text(encoding="utf-8"))
        self.assertEqual(len(specs), 1)
        spec = specs[0]
        self.assertIn("2026-09-01", spec.boundaries)   # 覆盖 2026-08 整月
        self.assertIn("2026-10-01", spec.boundaries)   # 覆盖 2026-09 整月
        self.assertTrue(spec.dynamic_enabled)
        self.assertEqual(spec.dynamic.get("time_unit"), "MONTH")
        self.assertGreaterEqual(int(spec.dynamic["end"]), 1)
        # 既有分区命名是 p_YYYYMM，动态分区前缀必须一致，否则新旧两套命名
        self.assertEqual(spec.dynamic.get("prefix"), "p_")

    def test_shipped_ddl_never_auto_drops_history(self):
        """dynamic_partition.start 是不可逆删数据的开关，本表刻意不设置。"""
        spec = parse_partitioned_tables(TRADE_SUMMARY_DDL.read_text(encoding="utf-8"))[0]
        self.assertNotIn("start", spec.dynamic)

    def test_dynamic_partition_that_drops_history_is_flagged(self):
        ddl = EXPIRED_DDL.replace(
            '"compression" = "zstd"',
            '"compression" = "zstd",\n    "dynamic_partition.enable" = "true",\n'
            '    "dynamic_partition.end" = "3",\n    "dynamic_partition.start" = "-90"')
        findings = scan_sql_text(ddl, source="drop.sql", today=TODAY)
        self.assertEqual(_kinds(findings), {"dynamic_partition_drops_history"})
        self.assertEqual(findings[0].severity, "warn")

    def test_dynamic_partition_without_forward_window_still_fills_up(self):
        """enable=true 但 end<=0 = 不预建未来分区，照样会写满——不能算修好。"""
        ddl = EXPIRED_DDL.replace(
            '"compression" = "zstd"',
            '"compression" = "zstd",\n    "dynamic_partition.enable" = "true",\n'
            '    "dynamic_partition.end" = "0"')
        findings = scan_sql_text(ddl, source="noend.sql", today=TODAY)
        self.assertIn("dynamic_partition_no_end", _kinds(findings))
        self.assertEqual([f.severity for f in findings], ["error"])

    def test_maxvalue_catch_all_partition_is_accepted(self):
        ddl = EXPIRED_DDL.replace(
            'PARTITION p_202607 VALUES LESS THAN ("2026-08-01")',
            'PARTITION p_202607 VALUES LESS THAN ("2026-08-01"),\n'
            '    PARTITION p_max VALUES LESS THAN MAXVALUE')
        self.assertEqual(scan_sql_text(ddl, source="maxvalue.sql", today=TODAY), [])

    def test_doris_fixed_range_syntax_is_understood(self):
        """Doris 的 VALUES [("a"), ("b")) 写法与 LESS THAN 语义相同，必须一起认。"""
        ddl = EXPIRED_DDL.replace(
            'PARTITION p_202605 VALUES LESS THAN ("2026-06-01"),\n'
            '    PARTITION p_202606 VALUES LESS THAN ("2026-07-01"),\n'
            '    PARTITION p_202607 VALUES LESS THAN ("2026-08-01")',
            'PARTITION p_202607 VALUES [("2026-07-01"), ("2026-08-01"))')
        specs = parse_partitioned_tables(ddl)
        self.assertEqual(specs[0].boundaries, ("2026-08-01",))
        findings = scan_sql_text(ddl, source="range.sql", today=TODAY)
        self.assertEqual(_kinds(findings), {"partition_range_expired"})

    def test_comments_never_produce_findings(self):
        """注释里出现的日期/属性不能被当成真的分区定义。"""
        commented = (
            '-- 历史包袱：以前末档是 "2026-08-01"，"dynamic_partition.start" = "-90"\n'
            "/* PARTITION p_old VALUES LESS THAN (\"2020-01-01\") */\n"
            + TRADE_SUMMARY_DDL.read_text(encoding="utf-8"))
        self.assertEqual(scan_sql_text(commented, source="c.sql", today=TODAY), [])

    def test_alter_add_partition_script_is_not_a_table_definition(self):
        """补分区的 ALTER 脚本不是建表声明，不能被当成一张分区已过期的表。"""
        self.assertTrue(PARTITION_MIGRATION.is_file(), "缺少线上表的补分区迁移脚本")
        text = PARTITION_MIGRATION.read_text(encoding="utf-8")
        self.assertEqual(parse_partitioned_tables(text), [])
        self.assertEqual(scan_sql_text(text, source=str(PARTITION_MIGRATION), today=TODAY), [])
        # 迁移脚本必须真的补上这两个缺口月份，并打开动态分区
        self.assertIn("p_202608", text)
        self.assertIn("p_202609", text)
        self.assertIn("dynamic_partition.enable", text)

    def test_init_doris_directory_is_clean(self):
        """整个 init/doris 目录（含 test_write.sql 这类非分区表）不应有 error。"""
        findings = scan_paths([DORIS_INIT_DIR], today=TODAY)
        self.assertEqual([f for f in findings if f.severity == "error"], [])

    def test_cli_exit_codes(self):
        with contextlib.redirect_stdout(io.StringIO()):
            clean = partition_main([str(DORIS_INIT_DIR), "--today", TODAY.isoformat()])
        self.assertEqual(clean, 0)
        with contextlib.redirect_stdout(io.StringIO()):
            bad_date = partition_main([str(DORIS_INIT_DIR), "--today", "not-a-date"])
        self.assertEqual(bad_date, 2)

    def test_cli_fails_on_a_directory_holding_an_expired_table(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "broken.sql").write_text(EXPIRED_DDL, encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()) as out:
                code = partition_main([tmp, "--today", TODAY.isoformat()])
        self.assertEqual(code, 1)
        self.assertIn("partition", out.getvalue().lower() + "partition")


# ══════════════════════════════════════════════════════════════════════
# #13 本体 ↔ 物理 schema 漂移
# ══════════════════════════════════════════════════════════════════════
ONTOLOGY_FIXTURE = [
    {
        "name": "Order",
        "status": "active",
        "required_filters": ["is_valid = 1"],
        "properties": [
            {"name": "order_amount", "type": "decimal"},
            {"name": "order_status", "type": "string"},
            {"name": "order_date", "type": "date"},
        ],
        "source_tables": [
            {"table": "dwd_ord_order_di", "layer": "DWD", "perm_column": "region_id",
             "field_mapping": {"order_amount": "order_amt", "order_status": "order_status",
                               "order_date": "dt"}},
            {"table": "dws_ord_order_1d", "layer": "DWS", "perm_column": "region_id",
             "field_mapping": {"order_date": "dt"},
             "pre_aggregated": {"order_count": "order_cnt"}},
        ],
    },
]

PHYSICAL_FIXTURE = {
    "dwd_ord_order_di": {"order_amt": "REAL", "order_status": "TEXT", "dt": "TEXT",
                         "region_id": "TEXT", "is_valid": "INTEGER", "user_id": "TEXT"},
    "dws_ord_order_1d": {"dt": "TEXT", "region_id": "TEXT", "order_cnt": "INTEGER"},
}


class SchemaDriftTests(unittest.TestCase):
    def test_matching_ontology_and_physical_schema_is_clean(self):
        report = check_schema_drift(ONTOLOGY_FIXTURE, DictSchemaReader(PHYSICAL_FIXTURE))
        self.assertTrue(report["ok"])
        self.assertEqual(report["counts"]["error"], 0)
        self.assertEqual(report["counts"]["warn"], 0)
        self.assertEqual(report["checked_tables"], 2)

    def test_storage_conventions_are_not_reported_as_type_drift(self):
        """dt 用 TEXT 存 'YYYYMMDD'、金额用 REAL 是本仓库既有约定，不是漂移。"""
        report = check_schema_drift(ONTOLOGY_FIXTURE, DictSchemaReader(PHYSICAL_FIXTURE))
        self.assertEqual([i for i in report["issues"] if i["kind"] == "type_mismatch"], [])

    def test_renamed_physical_column_is_an_error(self):
        physical = {**PHYSICAL_FIXTURE}
        physical["dwd_ord_order_di"] = {k: v for k, v in physical["dwd_ord_order_di"].items()
                                        if k != "order_amt"}
        physical["dwd_ord_order_di"]["order_amount_yuan"] = "REAL"
        report = check_schema_drift(ONTOLOGY_FIXTURE, DictSchemaReader(physical))
        self.assertFalse(report["ok"])
        missing = [i for i in report["issues"] if i["kind"] == "missing_column"]
        self.assertEqual([i["column"] for i in missing], ["order_amt"])
        self.assertIn("order_amount_yuan", missing[0]["detail"])

    def test_dropped_physical_table_is_an_error(self):
        report = check_schema_drift(
            ONTOLOGY_FIXTURE, DictSchemaReader({"dws_ord_order_1d": PHYSICAL_FIXTURE["dws_ord_order_1d"]}))
        missing = [i for i in report["issues"] if i["kind"] == "missing_table"]
        self.assertEqual([i["table"] for i in missing], ["dwd_ord_order_di"])

    def test_missing_row_level_permission_column_is_an_error(self):
        """行级权限列没了，权限过滤会静默失效——这条必须是 error，不能降级成提示。"""
        physical = {t: dict(c) for t, c in PHYSICAL_FIXTURE.items()}
        physical["dwd_ord_order_di"].pop("region_id")
        report = check_schema_drift(ONTOLOGY_FIXTURE, DictSchemaReader(physical))
        perm = [i for i in report["issues"] if i["kind"] == "missing_perm_column"]
        self.assertEqual(len(perm), 1)
        self.assertEqual(perm[0]["severity"], "error")

    def test_missing_pre_aggregated_measure_column_is_an_error(self):
        physical = {t: dict(c) for t, c in PHYSICAL_FIXTURE.items()}
        physical["dws_ord_order_1d"].pop("order_cnt")
        report = check_schema_drift(ONTOLOGY_FIXTURE, DictSchemaReader(physical))
        measures = [i for i in report["issues"] if i["kind"] == "missing_measure_column"]
        self.assertEqual([i["column"] for i in measures], ["order_cnt"])

    def test_measure_column_turned_into_text_is_warned(self):
        physical = {t: dict(c) for t, c in PHYSICAL_FIXTURE.items()}
        physical["dws_ord_order_1d"]["order_cnt"] = "VARCHAR(32)"
        report = check_schema_drift(ONTOLOGY_FIXTURE, DictSchemaReader(physical))
        self.assertEqual([i["kind"] for i in report["issues"] if i["severity"] == "warn"],
                         ["measure_not_numeric"])

    def test_amount_column_turned_into_text_is_warned(self):
        physical = {t: dict(c) for t, c in PHYSICAL_FIXTURE.items()}
        physical["dwd_ord_order_di"]["order_amt"] = "TEXT"
        report = check_schema_drift(ONTOLOGY_FIXTURE, DictSchemaReader(physical))
        mismatch = [i for i in report["issues"] if i["kind"] == "type_mismatch"]
        self.assertEqual([i["column"] for i in mismatch], ["order_amt"])
        self.assertTrue(report["ok"], "类型不符是 warn，不该把 CI 直接判死")

    def test_required_filter_column_checked_only_on_detail_tables(self):
        """DWS 预聚合表不会走明细模式，不该因为没有 is_valid 被误报。"""
        physical = {t: dict(c) for t, c in PHYSICAL_FIXTURE.items()}
        physical["dwd_ord_order_di"].pop("is_valid")
        report = check_schema_drift(ONTOLOGY_FIXTURE, DictSchemaReader(physical))
        flagged = [i for i in report["issues"] if i["kind"] == "missing_required_filter_column"]
        self.assertEqual([i["table"] for i in flagged], ["dwd_ord_order_di"])

    def test_deprecated_declarations_are_skipped(self):
        objects = [{**ONTOLOGY_FIXTURE[0], "source_tables": [
            {**ONTOLOGY_FIXTURE[0]["source_tables"][0], "status": "deprecated"}]}]
        report = check_schema_drift(objects, DictSchemaReader({}))
        self.assertTrue(report["ok"])
        self.assertEqual([i["kind"] for i in report["issues"] if i["severity"] != "info"], [])

    def test_undeclared_physical_table_is_informational_only(self):
        physical = {**PHYSICAL_FIXTURE, "ads_ord_user_1d": {"dt": "TEXT"}}
        report = check_schema_drift(ONTOLOGY_FIXTURE, DictSchemaReader(physical))
        self.assertTrue(report["ok"])
        undeclared = [i for i in report["issues"] if i["kind"] == "undeclared_table"]
        self.assertEqual([i["table"] for i in undeclared], ["ads_ord_user_1d"])

    def test_type_family_normalizes_engine_specific_spellings(self):
        for physical, family in (("VARCHAR(50)", "text"), ("TEXT", "text"), ("String", "text"),
                                 ("DOUBLE", "float"), ("REAL", "float"), ("Float64", "float"),
                                 ("DECIMAL(18,2)", "decimal"), ("INT", "int"),
                                 ("BIGINT", "int"), ("Int32", "int"), ("DATE", "date"),
                                 ("DATETIME", "datetime"), ("", "unknown")):
            self.assertEqual(type_family(physical), family, physical)

    def test_declarations_extract_required_filter_columns(self):
        decls = {d.table: d for d in declarations_from_objects(ONTOLOGY_FIXTURE)}
        self.assertEqual(decls["dwd_ord_order_di"].required_filter_columns, ("is_valid",))
        self.assertTrue(decls["dwd_ord_order_di"].detail_capable)
        self.assertFalse(decls["dws_ord_order_1d"].detail_capable)

    def test_real_ads_ord_gmv_1m_declaration_drifts_from_the_sample_warehouse(self):
        """真实回归：本体把 order_date 映射到 dt，而 ads_ord_gmv_1m 物理列叫 month_dt。

        这条漂移是真的——`ads_ord_gmv_1d` 被下线后，月粒度 GMV 会路由到
        `ads_ord_gmv_1m` 并以 `no such column: F.dt` 执行失败。
        下面的声明片段与 apps/data-agent-engine/backend/ontology/objects.yaml
        里 Payment 对象的同名条目一致（该目录是带 provenance 校验的上游快照）。
        """
        self.assertTrue(SAMPLE_DB.is_file(), f"缺少样例数仓 {SAMPLE_DB}")
        with sqlite3.connect(f"file:{SAMPLE_DB}?mode=ro", uri=True) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(ads_ord_gmv_1m)")}
        self.assertIn("month_dt", columns)
        self.assertNotIn("dt", columns, "物理表已改名，本用例的前提需要重新确认")

        payment_slice = [{
            "name": "Payment",
            "properties": [{"name": "order_date", "type": "date"}],
            "source_tables": [{
                "table": "ads_ord_gmv_1m", "layer": "ADS",
                "field_mapping": {"order_date": "dt"},
                "pre_aggregated": {"gmv": "gmv_amt", "order_count": "order_cnt"},
            }],
        }]
        report = check_schema_drift(payment_slice, SqliteSchemaReader(SAMPLE_DB),
                                    report_undeclared_tables=False)
        self.assertFalse(report["ok"])
        missing = [i for i in report["issues"] if i["kind"] == "missing_column"]
        self.assertEqual([(i["table"], i["column"]) for i in missing],
                         [("ads_ord_gmv_1m", "dt")])

    def test_sqlite_reader_rejects_unsafe_table_names(self):
        reader = SqliteSchemaReader(SAMPLE_DB)
        self.assertEqual(reader.columns("ads_ord_gmv_1m); DROP TABLE x;--"), {})
        self.assertNotEqual(reader.columns("ads_ord_gmv_1m"), {})


# ══════════════════════════════════════════════════════════════════════
# #5 数据新鲜度声明
# ══════════════════════════════════════════════════════════════════════
KNOWN_TABLES = ("dws_trade_order_daily", "dws_audio_album_daily", "dim_region", "articles")
TABLE_COLUMNS = {
    "dws_trade_order_daily": ("dt", "region_id", "gmv"),
    "dws_audio_album_daily": ("dt", "album_id", "play_count"),
    "dim_region": ("region_id", "region_name"),      # 维表没有时间列
    "articles": ("id", "title", "publish_date"),
}


class _RecordingRunner:
    """记下每一条探测 SQL，并按预设值应答。"""

    def __init__(self, values: dict[str, str | None], fail: Exception | None = None):
        self.values = values
        self.fail = fail
        self.calls: list[str] = []

    def __call__(self, sql: str):
        self.calls.append(sql)
        if self.fail is not None:
            raise self.fail
        rows = []
        for name, value in self.values.items():
            if f"FROM {name}" in sql:
                rows.append({"source_table": name, "latest_value": value})
        return rows


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _service(runner, **kwargs):
    kwargs.setdefault("known_tables", KNOWN_TABLES)
    kwargs.setdefault("table_columns", TABLE_COLUMNS)
    kwargs.setdefault("today", lambda: TODAY)
    return FreshnessService(runner, **kwargs)


class FreshnessTests(unittest.TestCase):
    def setUp(self):
        # 测试不受宿主环境里的总开关影响
        patcher = patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("DATA_FRESHNESS_MODE", None)

    def test_result_declares_the_data_cutoff(self):
        runner = _RecordingRunner({"dws_trade_order_daily": "2026-09-12"})
        snapshot = _service(runner).snapshot(["dws_trade_order_daily"])
        self.assertTrue(snapshot["enabled"])
        self.assertEqual(snapshot["as_of"], "2026-09-12")
        self.assertFalse(snapshot["stale"])
        self.assertIn("2026-09-12", snapshot["statement"])

    def test_as_of_is_the_oldest_source_table_not_the_newest(self):
        """两张表一张到 09-12 一张到 09-05，结论只能信到 09-05。"""
        runner = _RecordingRunner({"dws_trade_order_daily": "2026-09-12",
                                   "dws_audio_album_daily": "2026-09-05"})
        snapshot = _service(runner).snapshot(["dws_trade_order_daily", "dws_audio_album_daily"])
        self.assertEqual(snapshot["as_of"], "2026-09-05")
        self.assertIn("dws_audio_album_daily", snapshot["statement"])

    def test_stale_data_is_called_out(self):
        """跑批停了一个月，数字照样出得来——必须告诉用户它是旧的。"""
        runner = _RecordingRunner({"dws_trade_order_daily": "2026-08-01"})
        snapshot = _service(runner).snapshot(["dws_trade_order_daily"])
        self.assertTrue(snapshot["stale"])
        self.assertIn("落后", snapshot["statement"])
        self.assertIn("43", snapshot["statement"])  # 2026-08-01 → 2026-09-13

    def test_compact_date_format_is_understood(self):
        runner = _RecordingRunner({"dws_trade_order_daily": "20260801"})
        snapshot = _service(runner).snapshot(["dws_trade_order_daily"])
        self.assertEqual(snapshot["as_of"], "20260801")
        self.assertTrue(snapshot["stale"])

    # ── 成本控制（报告 §7.6 点名的风险）──────────────────────────────
    def test_all_tables_are_probed_in_one_round_trip(self):
        runner = _RecordingRunner({"dws_trade_order_daily": "2026-09-12",
                                   "dws_audio_album_daily": "2026-09-12"})
        _service(runner).snapshot(["dws_trade_order_daily", "dws_audio_album_daily"])
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(runner.calls[0].count("UNION ALL"), 1)

    def test_repeat_questions_hit_the_cache_instead_of_the_warehouse(self):
        runner = _RecordingRunner({"dws_trade_order_daily": "2026-09-12"})
        service = _service(runner)
        for _ in range(5):
            snapshot = service.snapshot(["dws_trade_order_daily"])
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(service.probe_calls, 1)
        self.assertEqual(snapshot["tables"][0]["source"], "cache")
        self.assertEqual(snapshot["as_of"], "2026-09-12")

    def test_cache_expires_after_its_ttl(self):
        clock = _FakeClock()
        runner = _RecordingRunner({"dws_trade_order_daily": "2026-09-12"})
        service = _service(runner, clock=clock, ttl_s=900)
        service.snapshot(["dws_trade_order_daily"])
        clock.advance(899)
        service.snapshot(["dws_trade_order_daily"])
        self.assertEqual(len(runner.calls), 1)
        clock.advance(2)
        service.snapshot(["dws_trade_order_daily"])
        self.assertEqual(len(runner.calls), 2)

    def test_probe_failure_degrades_instead_of_breaking_the_query(self):
        runner = _RecordingRunner({}, fail=RuntimeError("warehouse unreachable"))
        snapshot = _service(runner).snapshot(["dws_trade_order_daily"])
        self.assertIsNone(snapshot["as_of"])
        self.assertTrue(snapshot["degraded"])
        self.assertIn("warehouse unreachable", snapshot["note"])

    def test_user_facing_statement_never_echoes_the_raw_connection_error(self):
        """note 给日志，statement 给用户：连接异常里可能夹着主机名甚至凭据。"""
        runner = _RecordingRunner(
            {}, fail=RuntimeError("could not connect to doris://etl:s3cr3t@fe-prod:9030/dw"))
        snapshot = _service(runner).snapshot(["dws_trade_order_daily"])
        self.assertEqual(snapshot["statement"], "数据截止时间未知（新鲜度探测失败）")
        for secret in ("s3cr3t", "fe-prod", "doris://"):
            self.assertNotIn(secret, snapshot["statement"])
        self.assertIn("s3cr3t", snapshot["note"])  # 运维仍拿得到完整线索

    def test_a_failing_probe_is_negative_cached_so_the_failure_is_not_amplified(self):
        clock = _FakeClock()
        runner = _RecordingRunner({}, fail=RuntimeError("timeout"))
        service = _service(runner, clock=clock, negative_ttl_s=120)
        for _ in range(4):
            snapshot = service.snapshot(["dws_trade_order_daily"])
        self.assertEqual(len(runner.calls), 1)
        # 负缓存期内的后续问数同样要报降级，不能显示成「新鲜度没问题」
        self.assertTrue(snapshot["degraded"])
        self.assertIsNone(snapshot["as_of"])
        self.assertEqual(snapshot["tables"][0]["source"], "error")
        clock.advance(121)
        service.snapshot(["dws_trade_order_daily"])
        self.assertEqual(len(runner.calls), 2)

    def test_too_many_tables_skips_probing_entirely(self):
        runner = _RecordingRunner({name: "2026-09-12" for name in KNOWN_TABLES})
        snapshot = _service(runner, max_tables=2).snapshot(
            ["dws_trade_order_daily", "dws_audio_album_daily", "articles"])
        self.assertEqual(runner.calls, [])
        self.assertTrue(snapshot["degraded"])
        self.assertIn("max_tables", snapshot["note"])
        self.assertEqual({t["source"] for t in snapshot["tables"]}, {"skipped"})

    def test_dimension_tables_without_a_time_column_are_never_probed(self):
        runner = _RecordingRunner({"dws_trade_order_daily": "2026-09-12"})
        snapshot = _service(runner).snapshot(["dws_trade_order_daily", "dim_region"])
        self.assertEqual(len(runner.calls), 1)
        self.assertNotIn("dim_region", runner.calls[0])
        by_table = {t["table"]: t for t in snapshot["tables"]}
        self.assertEqual(by_table["dim_region"]["source"], "no_time_column")

    def test_kill_switch_disables_all_probing(self):
        runner = _RecordingRunner({"dws_trade_order_daily": "2026-09-12"})
        with patch.dict(os.environ, {"DATA_FRESHNESS_MODE": "off"}):
            snapshot = _service(runner).snapshot(["dws_trade_order_daily"])
        self.assertFalse(snapshot["enabled"])
        self.assertEqual(runner.calls, [])

    def test_cache_only_mode_serves_cache_but_never_probes(self):
        runner = _RecordingRunner({"dws_trade_order_daily": "2026-09-12"})
        service = _service(runner)
        service.snapshot(["dws_trade_order_daily"])          # 预热
        with patch.dict(os.environ, {"DATA_FRESHNESS_MODE": "cache_only"}):
            cached = service.snapshot(["dws_trade_order_daily"])
            service.invalidate()
            cold = service.snapshot(["dws_trade_order_daily"])
        self.assertEqual(cached["as_of"], "2026-09-12")
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(cold["tables"][0]["source"], "skipped")
        # 缓存落空时快照必须自曝降级，不能读成「新鲜度没问题」
        self.assertTrue(cold["degraded"])
        self.assertIsNone(cold["as_of"])
        self.assertFalse(cached["degraded"])

    def test_switching_data_source_does_not_reuse_the_other_sources_cache(self):
        runner_a = _RecordingRunner({"dws_trade_order_daily": "2026-09-12"})
        runner_b = _RecordingRunner({"dws_trade_order_daily": "2026-07-01"})
        _service(runner_a, scope="pg-prod").snapshot(["dws_trade_order_daily"])
        snapshot = _service(runner_b, scope="doris-prod").snapshot(["dws_trade_order_daily"])
        self.assertEqual(snapshot["as_of"], "2026-07-01")

    # ── 安全 ──────────────────────────────────────────────────────────
    def test_only_whitelisted_tables_ever_reach_the_probe_sql(self):
        runner = _RecordingRunner({"dws_trade_order_daily": "2026-09-12"})
        snapshot = _service(runner).snapshot(
            ["dws_trade_order_daily", "secret_payroll", 'x"; DROP TABLE users; --'])
        self.assertEqual(len(runner.calls), 1)
        self.assertNotIn("secret_payroll", runner.calls[0])
        self.assertNotIn("DROP", runner.calls[0].upper())
        self.assertEqual([t["table"] for t in snapshot["tables"]], ["dws_trade_order_daily"])

    def test_extract_tables_keeps_only_known_tables(self):
        sql = ("SELECT * FROM dws_trade_order_daily t "
               "JOIN dim_region r ON t.region_id = r.region_id "
               "LEFT JOIN some_other_schema.secret_payroll p ON 1=1")
        self.assertEqual(extract_tables(sql, KNOWN_TABLES),
                         ["dws_trade_order_daily", "dim_region"])

    def test_for_sql_derives_the_source_tables_from_the_executed_sql(self):
        runner = _RecordingRunner({"dws_trade_order_daily": "2026-09-12"})
        snapshot = _service(runner).for_sql(
            "SELECT SUM(gmv) FROM dws_trade_order_daily WHERE dt >= '2026-09-01'")
        self.assertEqual(snapshot["as_of"], "2026-09-12")

    def test_unrecognised_sql_degrades_quietly(self):
        runner = _RecordingRunner({})
        snapshot = _service(runner).for_sql("SELECT 1")
        self.assertEqual(runner.calls, [])
        self.assertIsNone(snapshot["as_of"])
        self.assertTrue(snapshot["degraded"])


class FreshnessAgainstFixtureWarehouseTests(unittest.TestCase):
    """跑在真实的 DBService 演示数仓上，确认接线是通的。"""

    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, {"DB_TYPE": "sqlite"}):
            from app.service.db_service import DBService
            cls.db = DBService()

    def test_snapshot_reads_the_real_max_partition(self):
        from app.service.warehouse_fixture import FIXTURE_TABLES

        os.environ.pop("DATA_FRESHNESS_MODE", None)
        service = FreshnessService.from_db_service(self.db, known_tables=FIXTURE_TABLES)
        snapshot = service.for_sql(
            "SELECT category_name, SUM(gmv) FROM dws_trade_order_daily "
            "JOIN dim_region USING (region_id) GROUP BY 1")
        self.assertTrue(snapshot["enabled"])
        self.assertIsNotNone(snapshot["as_of"])
        self.assertEqual(service.probe_calls, 1)
        by_table = {t["table"]: t for t in snapshot["tables"]}
        self.assertEqual(by_table["dws_trade_order_daily"]["time_column"], "dt")
        self.assertEqual(by_table["dim_region"]["source"], "no_time_column")
        # 演示数据每天补到昨天，所以不该被判成 stale
        self.assertFalse(snapshot["stale"])
        # 第二次问同一批表不再打库
        service.for_sql("SELECT SUM(gmv) FROM dws_trade_order_daily")
        self.assertEqual(service.probe_calls, 1)


if __name__ == "__main__":
    unittest.main()
