# -*- coding: utf-8 -*-
"""网闸（Guardrail）差距修复回归：死闸门、WARNING 出口、审计落盘、规则外置与灰度、密级执法。

每个用例都构造独立的 Guardrail 实例（独立规则配置 + 独立审计目录），不碰
app.service.guardrail.guardrail 单例，避免污染其它测试。
"""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.service.guardrail import (
    DEFAULT_RULES_PATH,
    ERROR,
    WARNING,
    Guardrail,
    GuardrailException,
    load_rules_file,
    parse_engine_ontology,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
ENGINE_ONTOLOGY = BACKEND_ROOT.parent / "apps" / "data-agent-engine" / "backend" / "ontology" / "objects.yaml"


class _Metric:
    def __init__(self, name, dimensions=(), roles=("admin", "analyst", "user")):
        self.name = name
        self.aliases = [name]
        self.available_dimensions = list(dimensions)
        self.authorized_roles = list(roles)
        self.source_table = "dws_trade_order_daily"


class _Dimension:
    def __init__(self, name, security_level=None):
        self.name = name
        self.aliases = [name]
        if security_level:
            self.security_level = security_level


class _Layer:
    """最小语义层替身：只提供网闸真正用到的解析接口。"""

    def __init__(self, metrics=None, dimensions=None, tables=None, column_levels=None):
        self._metrics = {m.name: m for m in (metrics or [])}
        self._dimensions = {d.name: d for d in (dimensions or [])}
        self.discovered_table_columns = dict(tables or {})
        self.join_paths = []
        if column_levels is not None:
            self.column_security_levels = dict(column_levels)

    def resolve_metric(self, name):
        return self._metrics.get(name)

    def resolve_dimension(self, name, table_context=None):
        return self._dimensions.get(name)


def _merge(base, override):
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


class GuardrailGapTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.audit_dir = Path(self._tmp.name) / "audit"
        # 网闸的方言预检会在有物理连接时真的去库上跑 EXPLAIN。单测必须与开发机上
        # 实际配置的数据源解耦，否则结果随环境漂移。
        engine_patch = patch("app.service.db_service.db_service.real_engine", None)
        engine_patch.start()
        self.addCleanup(engine_patch.stop)
        env_patch = patch.dict(os.environ, {"DB_TYPE": "sqlite"})
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def build(self, **overrides):
        config = _merge({"audit": {"enabled": True, "dir": str(self.audit_dir), "log_pass": True}},
                        overrides)
        return Guardrail(config=config)

    def audit_records(self):
        path = self.audit_dir / "guardrail_audit.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def warehouse_layer(self):
        return _Layer(tables={
            "dws_trade_order_daily": [("dt", "TEXT"), ("region_id", "TEXT"), ("gmv", "REAL")],
            "dim_region": [("region_id", "TEXT"), ("region_name", "TEXT")],
        })


# ---------------------------------------------------------------------------
# ★#1 两条已死的成本闸门
# ---------------------------------------------------------------------------
class DeadCostGatesTest(GuardrailGapTestCase):
    def test_large_tables_is_populated_from_schema_discovery(self):
        """large_tables 此前全仓没有写入点；现在由 Schema 发现填充，维表不误入闸门。"""
        guard = self.build()
        guard.set_schema_provider(self.warehouse_layer())
        guard.refresh_large_tables()
        self.assertEqual(guard.large_tables.get("dws_trade_order_daily"), "dt")
        self.assertNotIn("dim_region", guard.large_tables)

    def test_explicit_registration_and_config_partition_keys_also_feed_the_gate(self):
        guard = self.build(partition_discovery={"partition_keys": {"ods_click_log": "log_date"}})
        guard.set_schema_provider(_Layer(tables={}))
        guard.refresh_large_tables()
        self.assertEqual(guard.large_tables.get("ods_click_log"), "log_date")
        guard.register_large_table("dwd_pay_detail", "ds")
        self.assertEqual(guard.large_tables.get("dwd_pay_detail"), "ds")

    def test_runtime_row_statistics_keep_small_tables_out_of_the_gate(self):
        guard = self.build(partition_discovery={"min_rows": 1_000_000})
        guard.set_schema_provider(self.warehouse_layer())
        guard.record_table_row_count("dws_trade_order_daily", 42)
        guard.refresh_large_tables()
        self.assertNotIn("dws_trade_order_daily", guard.large_tables)
        guard.record_table_row_count("dws_trade_order_daily", 9_000_000)
        guard.refresh_large_tables()
        self.assertEqual(guard.large_tables.get("dws_trade_order_daily"), "dt")

    def test_full_table_scan_is_detected_instead_of_silently_passing(self):
        guard = self.build()
        guard.set_schema_provider(self.warehouse_layer())
        result = guard.check_sql("SELECT SUM(gmv) FROM dws_trade_order_daily", dialect="mysql")
        rules = [w["rule"] for w in result["warnings"]]
        self.assertIn("sql.partition_pruning", rules)
        self.assertTrue(result["ok"])  # 默认灰度：标记但放行

    def test_partition_filter_present_produces_no_finding(self):
        guard = self.build()
        guard.set_schema_provider(self.warehouse_layer())
        result = guard.check_sql(
            "SELECT SUM(gmv) FROM dws_trade_order_daily WHERE dt = '2026-01-01'", dialect="mysql")
        self.assertEqual(result["warnings"], [])

    def test_promoted_table_actually_blocks_the_full_table_scan(self):
        guard = self.build(rules={"sql.partition_pruning": {
            "severity": WARNING, "tables": {"dws_trade_order_daily": ERROR}}})
        guard.set_schema_provider(self.warehouse_layer())
        with self.assertRaises(GuardrailException) as caught:
            guard.check_sql("SELECT SUM(gmv) FROM dws_trade_order_daily", dialect="mysql")
        self.assertIn("分区剪裁", caught.exception.message)

    def test_scan_row_limit_is_externalized_and_still_blocks(self):
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.execute("CREATE TABLE dws_trade_order_daily (dt TEXT, gmv REAL)")
        sql = ("SELECT SUM(gmv) FROM dws_trade_order_daily "
               "WHERE dt BETWEEN '2024-01-01' AND '2024-12-01'")

        guard = self.build()
        guard.set_schema_provider(_Layer(tables={}))
        with patch("app.service.db_service.db_service.real_engine", None):
            with self.assertRaises(GuardrailException) as caught:
                guard.check_sql(sql, dialect="mysql", conn=connection)
            self.assertIn("性能熔断拦截", caught.exception.message)

            relaxed = self.build(rules={"sql.scan_rows": {"limit": 10_000_000}})
            relaxed.set_schema_provider(_Layer(tables={}))
            result = relaxed.check_sql(sql, dialect="mysql", conn=connection)
            self.assertTrue(result["ok"])
            self.assertGreater(result["estimated_rows"], 0)

    def test_physical_explain_estimate_is_no_longer_discarded(self):
        """物理库模式下 EXPLAIN 的计划此前被丢弃，扫描量闸门形同虚设。"""
        guard = self.build()
        self.assertEqual(guard._estimated_rows_from_plan(
            _FakePlan([{"QUERY PLAN": "Seq Scan on dws (cost=0.00..9.9 rows=123456 width=8)"}])), 123456)
        self.assertEqual(guard._estimated_rows_from_plan(
            _FakePlan([{"id": 1, "table": "dws", "rows": 90000}])), 90000)
        self.assertIsNone(guard._estimated_rows_from_plan(_FakePlan([{"note": "no estimate"}])))

    def test_physical_scan_estimate_reaches_the_return_contract(self):
        guard = self.build()
        guard.set_schema_provider(_Layer(tables={}))
        engine = _FakeEngine([{"QUERY PLAN": "Seq Scan on dws_trade_order_daily (rows=880000 width=8)"}])
        with patch.dict(os.environ, {"DB_TYPE": "postgresql"}), \
                patch("app.service.db_service.db_service.real_engine", engine), \
                patch("app.service.db_service.db_service.active_db_type", "postgresql"), \
                patch("app.service.db_service.db_service.get_active_db_name", lambda: "warehouse"):
            result = guard.check_sql("SELECT SUM(gmv) FROM dws_trade_order_daily WHERE dt='2026-01-01'",
                                     dialect="mysql")
        self.assertEqual(result["estimated_rows"], 880000)
        self.assertIn("sql.scan_rows_physical", [w["rule"] for w in result["warnings"]])


class _FakePlan:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeConnection:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args, **kwargs):
        return _FakePlan(self._rows)


class _FakeEngine:
    def __init__(self, rows):
        self._rows = rows

    def connect(self):
        return _FakeConnection(self._rows)


# ---------------------------------------------------------------------------
# ★ WARNING 出口 + 审计落盘
# ---------------------------------------------------------------------------
class WarningExitAndAuditTest(GuardrailGapTestCase):
    def test_return_contract_keeps_legacy_keys_and_adds_ok_and_warnings(self):
        guard = self.build()
        guard.set_schema_provider(_Layer(tables={}))
        layer = _Layer(metrics=[_Metric("gmv", ["region_name"])],
                       dimensions=[_Dimension("region_name")])
        dsl = guard.check_dsl({"metrics": [{"name": "gmv"}], "dimensions": [{"name": "region_name"}]},
                              layer, user_role="admin")
        self.assertEqual(dsl["status"], "PASS")
        self.assertEqual(dsl["message"], "语义审计通过")
        self.assertTrue(dsl["ok"])
        self.assertEqual(dsl["warnings"], [])

        sql = guard.check_sql("SELECT 1 FROM dim_region", dialect="mysql")
        self.assertEqual(sql["status"], "PASS")
        self.assertEqual(sql["message"], "审计通过")
        self.assertEqual(sql["estimated_rows"], 1000)
        self.assertTrue(sql["ok"])
        self.assertEqual(sql["warnings"], [])

    def test_blocked_query_is_written_to_the_jsonl_audit_trail(self):
        guard = self.build()
        guard.set_schema_provider(_Layer(tables={}))
        with self.assertRaises(GuardrailException):
            guard.check_sql("DELETE FROM dws_trade_order_daily", dialect="mysql")
        blocks = [r for r in self.audit_records() if r.get("outcome") == "block"]
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["rule"], "sql.ddl_dml")
        self.assertEqual(blocks[0]["severity"], ERROR)
        self.assertIn("sql_sha256", blocks[0])
        self.assertNotIn("sql", blocks[0])  # 默认不落 SQL 原文

    def test_warning_is_written_to_the_audit_trail_and_returned(self):
        guard = self.build()
        guard.set_schema_provider(self.warehouse_layer())
        result = guard.check_sql("SELECT SUM(gmv) FROM dws_trade_order_daily", dialect="mysql")
        warns = [r for r in self.audit_records() if r.get("outcome") == "warn" and r.get("rule")]
        self.assertEqual([w["rule"] for w in warns], ["sql.partition_pruning"])
        self.assertEqual(warns[0]["table"], "dws_trade_order_daily")
        self.assertEqual(result["warnings"][0]["severity"], WARNING)

    def test_raw_sql_is_only_recorded_when_explicitly_enabled(self):
        guard = self.build(audit={"log_sql": True})
        guard.set_schema_provider(_Layer(tables={}))
        guard.check_sql("SELECT 1 FROM dim_region", dialect="mysql")
        self.assertTrue(any("SELECT 1 FROM dim_region" == r.get("sql") for r in self.audit_records()))

    def test_audit_uses_rotating_jsonl_files(self):
        guard = self.build(audit={"max_bytes": 400, "backup_count": 2})
        guard.set_schema_provider(_Layer(tables={}))
        for _ in range(50):
            guard.check_sql("SELECT 1 FROM dim_region", dialect="mysql")
        files = sorted(p.name for p in self.audit_dir.iterdir())
        self.assertIn("guardrail_audit.jsonl", files)
        self.assertIn("guardrail_audit.jsonl.1", files)
        self.assertLessEqual(len(files), 3)  # 主文件 + backup_count 份

    def test_audit_failure_never_breaks_the_query_path(self):
        blocked = Path(self._tmp.name) / "not-a-directory"
        blocked.write_text("occupied", encoding="utf-8")
        guard = Guardrail(config={"audit": {"enabled": True, "dir": str(blocked / "sub")}})
        guard.set_schema_provider(_Layer(tables={}))
        result = guard.check_sql("SELECT 1 FROM dim_region", dialect="mysql")
        self.assertTrue(result["ok"])

    def test_disabled_audit_writes_nothing(self):
        guard = self.build(audit={"enabled": False})
        guard.set_schema_provider(_Layer(tables={}))
        guard.check_sql("SELECT 1 FROM dim_region", dialect="mysql")
        self.assertEqual(self.audit_records(), [])


# ---------------------------------------------------------------------------
# ★ 规则外置 + 按表灰度
# ---------------------------------------------------------------------------
class ExternalizedRulesTest(GuardrailGapTestCase):
    def test_shipped_rules_file_loads_and_matches_code_defaults(self):
        config, path, errors = load_rules_file()
        self.assertEqual(path, DEFAULT_RULES_PATH)
        self.assertEqual(errors, [])
        guard = Guardrail(config=config)
        self.assertEqual(guard.ruleset.decide("sql.partition_pruning"), WARNING)
        self.assertEqual(guard.ruleset.decide("sql.scan_rows"), ERROR)
        self.assertEqual(guard.scan_row_limit, 50000)
        self.assertEqual(guard.ruleset.threshold("dsl.time_span", "max_days"), 365)

    def test_broken_rules_file_falls_back_to_defaults_instead_of_crashing(self):
        broken = Path(self._tmp.name) / "broken.json"
        broken.write_text("{ not json", encoding="utf-8")
        config, path, errors = load_rules_file(broken)
        self.assertIsNone(path)
        self.assertTrue(errors)
        self.assertEqual(Guardrail(config=config).scan_row_limit, 50000)

    def test_time_span_threshold_comes_from_configuration(self):
        guard = self.build(rules={"dsl.time_span": {"max_days": 30}})
        layer = _Layer(metrics=[_Metric("gmv", ["region_name"])])
        dsl = {"metrics": [{"name": "gmv"}], "dimensions": [],
               "time_range": {"start": "2026-01-01", "end": "2026-03-31"}}
        with self.assertRaises(GuardrailException) as caught:
            guard.check_dsl(dsl, layer, user_role="admin")
        self.assertIn("(30天)", caught.exception.message)

    def test_time_span_can_be_degraded_to_a_warning(self):
        guard = self.build(rules={"dsl.time_span": {"severity": WARNING}})
        layer = _Layer(metrics=[_Metric("gmv", ["region_name"])])
        dsl = {"metrics": [{"name": "gmv"}], "dimensions": [],
               "time_range": {"start": "2020-01-01", "end": "2026-01-01"}}
        result = guard.check_dsl(dsl, layer, user_role="admin")
        self.assertTrue(result["ok"])
        self.assertEqual(result["warnings"][0]["rule"], "dsl.time_span")

    def test_grey_release_only_checks_registered_tables(self):
        guard = self.build()
        guard.set_schema_provider(self.warehouse_layer())
        guard.ruleset.start_grey("sql.partition_pruning", "other_fact_table")
        result = guard.check_sql("SELECT SUM(gmv) FROM dws_trade_order_daily", dialect="mysql")
        self.assertEqual(result["warnings"], [])

    def test_promotion_requires_samples_unless_forced(self):
        guard = self.build(rules={"sql.partition_pruning": {"promote_min_samples": 5}})
        guard.set_schema_provider(self.warehouse_layer())
        with self.assertRaises(ValueError):
            guard.ruleset.promote("sql.partition_pruning", table="dws_trade_order_daily")
        guard.ruleset.promote("sql.partition_pruning", table="dws_trade_order_daily", force=True)
        with self.assertRaises(GuardrailException):
            guard.check_sql("SELECT SUM(gmv) FROM dws_trade_order_daily", dialect="mysql")

    def test_grey_hits_are_counted_so_promotion_can_be_evidence_based(self):
        guard = self.build(rules={"sql.partition_pruning": {"promote_min_samples": 2}})
        guard.set_schema_provider(self.warehouse_layer())
        for _ in range(2):
            guard.check_sql("SELECT SUM(gmv) FROM dws_trade_order_daily", dialect="mysql")
        stats = guard.ruleset.stats()["sql.partition_pruning"]
        self.assertEqual(stats["hits"], 2)
        self.assertEqual(stats["blocked"], 0)
        guard.ruleset.promote("sql.partition_pruning", table="dws_trade_order_daily")

    def test_environment_override_can_switch_partition_gate_to_enforcement(self):
        with patch.dict(os.environ, {"GUARDRAIL_PARTITION_MODE": "error"}):
            guard = self.build()
        guard.set_schema_provider(self.warehouse_layer())
        with self.assertRaises(GuardrailException):
            guard.check_sql("SELECT SUM(gmv) FROM dws_trade_order_daily", dialect="mysql")

    def test_p0_security_rules_cannot_be_weakened_by_configuration(self):
        guard = self.build(rules={
            "dsl.sensitive_column": {"severity": WARNING},
            "dsl.row_level_region": {"mode": "off"},
            "sql.ddl_dml": {"severity": "off"},
        })
        self.assertEqual(guard.ruleset.decide("dsl.sensitive_column"), ERROR)
        self.assertEqual(guard.ruleset.decide("dsl.row_level_region"), ERROR)
        self.assertEqual(guard.ruleset.decide("sql.ddl_dml"), ERROR)
        self.assertTrue(guard.ruleset.load_errors)
        with self.assertRaises(ValueError):
            guard.ruleset.disable("sql.ddl_dml")
        with self.assertRaises(ValueError):
            guard.ruleset.start_grey("dsl.sensitive_column", "articles")

    def test_saved_ruleset_round_trips_the_grey_state(self):
        guard = self.build()
        guard.ruleset.start_grey("sql.partition_pruning", "dws_trade_order_daily")
        target = Path(self._tmp.name) / "saved.json"
        guard.ruleset.save(target)
        config, path, errors = load_rules_file(target)
        self.assertEqual(path, target)
        self.assertEqual(errors, [])
        reloaded = Guardrail(config=config)
        self.assertEqual(reloaded.ruleset.rule("sql.partition_pruning")["mode"], "grey")
        self.assertEqual(reloaded.ruleset.decide("sql.partition_pruning", "dws_trade_order_daily"), WARNING)


# ---------------------------------------------------------------------------
# ★#9 敏感列元数据驱动
# ---------------------------------------------------------------------------
class SensitiveColumnMetadataTest(GuardrailGapTestCase):
    def dsl_for(self, dimension):
        return {"metrics": [{"name": "gmv"}], "dimensions": [{"name": dimension}], "filters": []}

    def layer_for(self, dimension, security_level=None, column_levels=None):
        return _Layer(metrics=[_Metric("gmv", [dimension])],
                      dimensions=[_Dimension(dimension, security_level=security_level)],
                      column_levels=column_levels)

    def test_legacy_hardcoded_behaviour_is_preserved(self):
        """改造前写死的 customer_phone/customer_card_no 与模糊匹配，拦截面不缩小。"""
        guard = self.build()
        for dimension in ("customer_phone", "phone", "card_no", "customer_card_no"):
            layer = self.layer_for(dimension)
            with self.assertRaises(GuardrailException) as caught:
                guard.check_dsl(self.dsl_for(dimension), layer, user_role="user")
            self.assertIn("金融级列级安全拦截", caught.exception.message)
            self.assertTrue(guard.check_dsl(self.dsl_for(dimension), layer, user_role="admin")["ok"])

    def test_sensitive_filter_field_is_still_blocked(self):
        guard = self.build()
        layer = self.layer_for("region_name")
        dsl = {"metrics": [{"name": "gmv"}], "dimensions": [],
               "filters": [{"field": "customer_phone", "op": "eq", "value": "138"}]}
        with self.assertRaises(GuardrailException) as caught:
            guard.check_dsl(dsl, layer, user_role="analyst")
        self.assertIn("敏感过滤条件字段", caught.exception.message)

    def test_security_level_drives_which_roles_may_read_a_column(self):
        guard = self.build(security_levels={"columns": {"net_salary_amt": "L2"},
                                            "roles": {"L2": ["admin", "analyst"]}})
        layer = self.layer_for("net_salary_amt")
        with self.assertRaises(GuardrailException) as caught:
            guard.check_dsl(self.dsl_for("net_salary_amt"), layer, user_role="user")
        self.assertIn("admin, analyst", caught.exception.message)
        self.assertTrue(guard.check_dsl(self.dsl_for("net_salary_amt"), layer, user_role="analyst")["ok"])

    def test_semantic_layer_dimension_security_level_is_enforced(self):
        guard = self.build()
        layer = self.layer_for("vip_contact_ref", security_level="L3")
        with self.assertRaises(GuardrailException):
            guard.check_dsl(self.dsl_for("vip_contact_ref"), layer, user_role="analyst")
        records = [r for r in self.audit_records() if r.get("rule") == "dsl.sensitive_column"]
        self.assertEqual(records[0]["detail"]["source"], "semantic_layer.Dimension.security_level")
        self.assertEqual(records[0]["detail"]["level"], "L3")

    def test_layer_provided_column_level_map_is_enforced(self):
        guard = self.build()
        layer = self.layer_for("member_doc_ref", column_levels={"member_doc_ref": "L3"})
        with self.assertRaises(GuardrailException):
            guard.check_dsl(self.dsl_for("member_doc_ref"), layer, user_role="user")

    def test_runtime_injection_from_engine_metadata_is_enforced(self):
        guard = self.build()
        self.assertEqual(guard.register_column_security_levels(
            {"cust_mobile_ref": "L3"}, source="engine-ontology"), 1)
        layer = self.layer_for("cust_mobile_ref")
        with self.assertRaises(GuardrailException):
            guard.check_dsl(self.dsl_for("cust_mobile_ref"), layer, user_role="analyst")
        detail = [r for r in self.audit_records() if r.get("rule") == "dsl.sensitive_column"][0]["detail"]
        self.assertEqual(detail["source"], "engine-ontology")

    def test_newly_classified_columns_only_warn_so_nothing_newly_breaks(self):
        """模式库新增的密级（邮箱/地址等）默认 warning：标记但不改变既有放行行为。"""
        guard = self.build()
        layer = self.layer_for("contact_email")
        result = guard.check_dsl(self.dsl_for("contact_email"), layer, user_role="user")
        self.assertTrue(result["ok"])
        self.assertEqual(result["warnings"][0]["rule"], "dsl.sensitive_column")
        self.assertEqual(result["warnings"][0]["detail"]["level"], "L2")

    def test_engine_ontology_file_is_translated_into_column_levels(self):
        ontology = Path(self._tmp.name) / "objects.yaml"
        ontology.write_text(
            "objects:\n"
            "  - name: Customer\n"
            "    domain: usr\n"
            "    security_level: L3\n"
            "    properties:\n"
            "      - {name: cust_contact_ref, type: string, description: 联系方式}\n"
            "      - {name: cust_name, type: string, description: 姓名}\n"
            "    source_tables:\n"
            "      - {table: dwd_usr_customer_di, layer: DWD,\n"
            "         field_mapping: {cust_contact_ref: contact_ref_col}}\n"
            "  - name: Order\n"
            "    security_level: L1\n"
            "    properties:\n"
            "      - {name: order_public_no, type: string}\n",
            encoding="utf-8")
        mapping, degraded = parse_engine_ontology(ontology)
        self.assertEqual(mapping["cust_contact_ref"], "L3")
        self.assertEqual(mapping["contact_ref_col"], "L3")
        self.assertEqual(mapping["order_public_no"], "L1")
        self.assertIsInstance(degraded, bool)

        guard = self.build(security_levels={"metadata_sources": [
            {"enabled": True, "format": "engine_ontology", "path": str(ontology), "severity": ERROR}]})
        layer = self.layer_for("cust_contact_ref")
        with self.assertRaises(GuardrailException):
            guard.check_dsl(self.dsl_for("cust_contact_ref"), layer, user_role="analyst")
        # L1 公开列对所有角色开放
        public_layer = self.layer_for("order_public_no")
        self.assertTrue(guard.check_dsl(self.dsl_for("order_public_no"), public_layer, user_role="user")["ok"])

    @unittest.skipUnless(ENGINE_ONTOLOGY.exists(), "data-agent-engine 本体文件不存在")
    def test_real_engine_ontology_parses_into_valid_levels(self):
        mapping, _ = parse_engine_ontology(ENGINE_ONTOLOGY)
        self.assertTrue(mapping)
        self.assertTrue(all(value in {"L1", "L2", "L3"} for value in mapping.values()))

    def test_unreadable_metadata_source_is_recorded_and_does_not_crash(self):
        guard = self.build(security_levels={"metadata_sources": [
            {"enabled": True, "path": str(Path(self._tmp.name) / "missing.yaml")}]})
        failures = [r for r in self.audit_records()
                    if r.get("event") == "guardrail.metadata_source" and r.get("outcome") == "failed"]
        self.assertTrue(failures)
        self.assertTrue(guard.check_sql("SELECT 1 FROM dim_region", dialect="mysql")["ok"])


# ---------------------------------------------------------------------------
# 既有安全校验不得被本次改造放宽
# ---------------------------------------------------------------------------
class ExistingSecurityStillEnforcedTest(GuardrailGapTestCase):
    def setUp(self):
        super().setUp()
        self.guard = self.build()
        self.guard.set_schema_provider(_Layer(tables={}))

    def test_ddl_dml_is_rejected(self):
        for sql in ("DROP TABLE dws_trade_order_daily",
                    "INSERT INTO dws_trade_order_daily VALUES (1)",
                    "UPDATE dws_trade_order_daily SET gmv = 0"):
            with self.assertRaises(GuardrailException):
                self.guard.check_sql(sql, dialect="mysql")

    def test_division_without_nullif_is_rejected(self):
        with self.assertRaises(GuardrailException) as caught:
            self.guard.check_sql("SELECT SUM(refund_amount) / SUM(gmv) FROM dws_trade_order_daily "
                                 "WHERE dt = '2026-01-01'", dialect="mysql")
        self.assertIn("NULLIF", caught.exception.message)

    def test_unregistered_metric_and_role_are_still_blocked(self):
        layer = _Layer(metrics=[_Metric("net_refunds", ["region_name"], roles=["admin"])])
        with self.assertRaises(GuardrailException):
            self.guard.check_dsl({"metrics": [{"name": "unknown_metric"}]}, layer, user_role="admin")
        with self.assertRaises(GuardrailException) as caught:
            self.guard.check_dsl({"metrics": [{"name": "net_refunds"}]}, layer, user_role="user")
        self.assertIn("权限审计拦截", caught.exception.message)

    def test_row_level_region_isolation_is_intact(self):
        layer = _Layer(metrics=[_Metric("gmv", ["region_name"])],
                       dimensions=[_Dimension("region_name")])
        dsl = {"metrics": [{"name": "gmv"}], "dimensions": [],
               "filters": [{"field": "region_name", "op": "eq", "value": "华北"}]}
        with self.assertRaises(GuardrailException):
            self.guard.check_dsl(dsl, layer, user_role="user")

        injected = {"metrics": [{"name": "gmv"}], "dimensions": [], "filters": []}
        self.guard.check_dsl(injected, layer, user_role="user")
        self.assertEqual(injected["filters"],
                         [{"field": "region_name", "op": "eq", "value": "华东"}])

    def test_missing_metric_is_still_blocked(self):
        with self.assertRaises(GuardrailException):
            self.guard.check_dsl({"metrics": []}, _Layer(), user_role="admin")


if __name__ == "__main__":
    unittest.main()
