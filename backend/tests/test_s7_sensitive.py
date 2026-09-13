# -*- coding: utf-8 -*-
"""§4/§7.2 敏感列元数据驱动的回归测试。

覆盖三件事：

1. **活库真实列被保护**：``users.bank_card_info`` / ``users.wechat_info`` /
   ``users.alipay_info`` 这三个支付账户列（活库 blog_converter 的实际 jsonb 列）
   在 DSL 与物理 SQL 两条路径上都按密级执法；改造前的清单里一个都没有。
2. **不误伤**：``phone_type`` / ``card_type`` 这类「描述字段而非字段值」不再被
   子串模糊匹配拦下；同时 ``customer_phone`` / ``card_no`` 等既有拦截面不缩小。
3. **元数据驱动**：外置目录（json/yaml）、运行时注册、物理 schema 推断（只出候选、
   需审阅才生效）、pattern 自带用例的自检。

每个用例都自建 Guardrail 实例（独立审计目录），不碰 app.service.guardrail.guardrail 单例。
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.service.guardrail import (
    ERROR,
    WARNING,
    ColumnRule,
    ColumnSecurityCatalog,
    Guardrail,
    GuardrailException,
    normalize_level,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SHIPPED_CATALOG = BACKEND_ROOT / "config" / "sensitive_columns.json"

#: 活库 postgresql://…/blog_converter 里真实存在的三个支付账户列（information_schema 实查）。
LIVE_PAYMENT_COLUMNS = ("bank_card_info", "wechat_info", "alipay_info")


class _Metric:
    def __init__(self, name, dimensions=(), roles=("admin", "analyst", "user")):
        self.name = name
        self.aliases = [name]
        self.available_dimensions = list(dimensions)
        self.authorized_roles = list(roles)
        self.source_table = "users"


class _Dimension:
    def __init__(self, name, security_level=None):
        self.name = name
        self.aliases = [name]
        if security_level:
            self.security_level = security_level


class _Layer:
    def __init__(self, metrics=None, dimensions=None, tables=None):
        self._metrics = {m.name: m for m in (metrics or [])}
        self._dimensions = {d.name: d for d in (dimensions or [])}
        self.discovered_table_columns = dict(tables or {})
        self.join_paths = []

    def resolve_metric(self, name):
        return self._metrics.get(name)

    def resolve_dimension(self, name, table_context=None):
        return self._dimensions.get(name)


class SensitiveColumnTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.audit_dir = self.tmp / "audit"
        # 方言预检在有物理连接时会真的去库上跑 EXPLAIN，单测必须与开发机数据源解耦。
        engine_patch = patch("app.service.db_service.db_service.real_engine", None)
        engine_patch.start()
        self.addCleanup(engine_patch.stop)
        env_patch = patch.dict(os.environ, {"DB_TYPE": "sqlite"})
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def build(self, **overrides):
        config = {"audit": {"enabled": True, "dir": str(self.audit_dir), "log_pass": True}}
        for key, value in overrides.items():
            config[key] = value
        return Guardrail(config=config)

    def audit_records(self):
        path = self.audit_dir / "guardrail_audit.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def dsl_for(self, dimension):
        return {"metrics": [{"name": "user_count"}], "dimensions": [{"name": dimension}], "filters": []}

    def layer_for(self, dimension):
        return _Layer(metrics=[_Metric("user_count", [dimension])],
                      dimensions=[_Dimension(dimension)])

    def write_catalog(self, payload: dict, name: str = "catalog.json") -> Path:
        path = self.tmp / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path


# ---------------------------------------------------------------------------
# ★ 活库真实支付账户列
# ---------------------------------------------------------------------------
class LivePaymentColumnsTest(SensitiveColumnTestCase):
    def test_shipped_catalog_covers_every_live_payment_account_column(self):
        """随仓发布的目录必须实际覆盖活库那三个支付账户列（有人删条目就会红）。"""
        self.assertTrue(SHIPPED_CATALOG.exists(), f"缺少敏感列目录 {SHIPPED_CATALOG}")
        catalog = json.loads(SHIPPED_CATALOG.read_text(encoding="utf-8"))
        declared = {str(entry["column"]).lower() for entry in catalog["columns"]}
        for column in LIVE_PAYMENT_COLUMNS:
            self.assertIn(f"users.{column}", declared)

    def test_payment_columns_are_blocked_for_non_admin_on_the_dsl_path(self):
        guard = self.build()
        for column in LIVE_PAYMENT_COLUMNS:
            layer = self.layer_for(column)
            for role in ("user", "analyst"):
                with self.assertRaises(GuardrailException, msg=column) as caught:
                    guard.check_dsl(self.dsl_for(column), layer, user_role=role)
                self.assertIn("金融级列级安全拦截", caught.exception.message)
            # admin 仍然拿得到——密级控制的是「谁能看」，不是一刀切。
            self.assertTrue(guard.check_dsl(self.dsl_for(column), layer, user_role="admin")["ok"])

    def test_qualified_name_and_bare_name_are_both_classified_as_L3(self):
        guard = self.build()
        for column in LIVE_PAYMENT_COLUMNS:
            qualified = guard.security.classify(f"users.{column}")
            bare = guard.security.classify(column)
            self.assertEqual(qualified["level"], "L3", column)
            self.assertEqual(bare["level"], "L3", column)
            self.assertEqual(bare["roles"], ["admin"])
            # 裸列名走的是限定列回退，来源要能看出是回退而不是精确命中。
            self.assertIn("qualified-fallback", bare["source"])

    def test_payment_columns_stay_covered_even_without_the_catalog_file(self):
        """删掉外置目录只该让拦截面变窄，不该让支付账户列裸奔——builtin_patterns 兜底。"""
        guard = self.build(security_levels={"catalog_files": []})
        self.assertNotIn("users.bank_card_info", guard.security.columns)
        for column in LIVE_PAYMENT_COLUMNS:
            finding = guard.security.classify(column)
            self.assertIsNotNone(finding, column)
            self.assertEqual(finding["level"], "L3", column)
            self.assertTrue(finding["source"].startswith("pattern:"), finding["source"])

    def test_old_hardcoded_columns_do_not_exist_in_the_live_schema_but_still_match(self):
        """customer_phone / customer_card_no 活库里没有，留着也没坏处——别的数据源可能有。"""
        guard = self.build()
        for column in ("customer_phone", "customer_card_no"):
            self.assertEqual(guard.security.classify(column)["level"], "L3")


# ---------------------------------------------------------------------------
# ★ 匹配方式：精确列名 + 显式 pattern，不再靠双向子串一把梭
# ---------------------------------------------------------------------------
class MatchingPrecisionTest(SensitiveColumnTestCase):
    #: 形似敏感、实则只描述字段本身的列——改造前会被 `phone`/`card` 子串误伤。
    FALSE_POSITIVES = ("phone_type", "card_type", "email_type", "phone_area",
                       "bank_card_info_updated_at", "is_bank_card_bound",
                       "wechat_message_count", "alipay_sync_status", "card_level")
    #: 既有拦截面，一个都不许丢。
    MUST_STAY_BLOCKED = ("customer_phone", "phone", "card_no", "customer_card_no",
                         "mobile", "user_password", "hashed_password", "id_no")

    def test_descriptor_columns_are_not_misclassified(self):
        guard = self.build()
        for column in self.FALSE_POSITIVES:
            self.assertIsNone(guard.security.classify(column), column)

    def test_descriptor_columns_pass_the_dsl_gate_for_a_plain_user(self):
        guard = self.build()
        layer = self.layer_for("phone_type")
        result = guard.check_dsl(self.dsl_for("phone_type"), layer, user_role="user")
        self.assertTrue(result["ok"])
        self.assertEqual([w for w in result["warnings"] if "sensitive" in w["rule"]], [])

    def test_existing_interception_surface_does_not_shrink(self):
        guard = self.build()
        for column in self.MUST_STAY_BLOCKED:
            finding = guard.security.classify(column)
            self.assertIsNotNone(finding, column)
            self.assertEqual(finding["level"], "L3", column)

    def test_exclusions_never_override_an_explicit_registration(self):
        """排除项只挡模糊层：显式登记过的列即使长得像描述字段也照样执法。"""
        guard = self.build()
        guard.register_column_security_levels({"users.phone_type": "L3"}, source="dpo-review")
        finding = guard.security.classify("phone_type", table="users")
        self.assertEqual(finding["level"], "L3")
        self.assertEqual(finding["source"], "dpo-review")

    def test_every_match_type_behaves_as_declared(self):
        cases = [
            ({"match": "phone", "type": "exact"}, "phone", "customer_phone"),
            ({"match": "cust_", "type": "prefix"}, "cust_card", "my_cust_card"),
            ({"match": "_info", "type": "suffix"}, "alipay_info", "info_column"),
            ({"match": "card", "type": "substring"}, "bank_card_info", "phone"),
            ({"match": r"^(alipay|wechat)_info$", "type": "regex"}, "wechat_info", "wechat_info_id"),
            ({"match": "*_card_*", "type": "glob"}, "bank_card_info", "bank_card"),
        ]
        for spec, hit, miss in cases:
            rule = ColumnRule(spec)
            self.assertTrue(rule.matches(hit), spec)
            self.assertFalse(rule.matches(miss), spec)

    def test_a_rule_can_be_scoped_to_specific_tables(self):
        rule = ColumnRule({"match": "amount", "type": "exact", "tables": ["transactions"]})
        self.assertTrue(rule.matches("amount", "transactions"))
        self.assertFalse(rule.matches("amount", "articles"))
        # 表未知时从严：宁可多标记，也不因为解析不出表名就漏过。
        self.assertTrue(rule.matches("amount", None))

    def test_pattern_self_check_reports_broken_rules_instead_of_failing_silently(self):
        guard = self.build(security_levels={
            "catalog_files": [],
            "patterns": [
                {"id": "broken.example", "match": "alipay", "level": "L3",
                 "examples": ["wechat_info"]},
                {"id": "broken.counter", "match": "phone", "level": "L3",
                 "counter_examples": ["customer_phone"]},
                {"id": "broken.regex", "match": "([unclosed", "type": "regex", "level": "L3"},
            ],
        })
        errors = " | ".join(guard.security.load_errors)
        self.assertIn("broken.example", errors)
        self.assertIn("broken.counter", errors)
        self.assertIn("正则非法", errors)

    def test_shipped_rules_pass_their_own_self_check(self):
        guard = self.build()
        self.assertEqual(guard.security.validate_patterns(), [])
        self.assertEqual(guard.security.load_errors, [])

    def test_explain_shows_why_a_column_was_let_through(self):
        guard = self.build()
        explained = guard.security.explain("phone_type")
        self.assertIsNone(explained["finding"])
        self.assertTrue(explained["excluded_by"])
        self.assertEqual(guard.security.explain("users.bank_card_info")["finding"]["level"], "L3")


# ---------------------------------------------------------------------------
# ★ 元数据驱动：外置目录 / 运行时注册 / 密级别名
# ---------------------------------------------------------------------------
class MetadataDrivenCatalogTest(SensitiveColumnTestCase):
    def test_external_json_catalog_is_loaded(self):
        path = self.write_catalog({"columns": [
            {"column": "withdrawal_requests.account_info", "level": "L3", "reason": "提现账户"}]})
        guard = self.build(security_levels={"catalog_files": [str(path)]})
        finding = guard.security.classify("account_info", table="withdrawal_requests")
        self.assertEqual(finding["level"], "L3")
        self.assertEqual(finding["reason"], "提现账户")

    @unittest.skipUnless(__import__("importlib").util.find_spec("yaml"), "需要 PyYAML")
    def test_external_yaml_catalog_is_loaded(self):
        path = self.tmp / "catalog.yaml"
        path.write_text("columns:\n  - column: users.alipay_info\n    level: L3\n", encoding="utf-8")
        guard = self.build(security_levels={"catalog_files": [str(path)]})
        self.assertEqual(guard.security.classify("users.alipay_info")["level"], "L3")

    def test_yaml_catalog_without_pyyaml_degrades_loudly_instead_of_silently(self):
        """backend venv 目前没装 PyYAML：yaml 目录要报一条明确的装载错误，而不是静默放行。"""
        path = self.tmp / "catalog.yaml"
        path.write_text("columns:\n  - column: users.alipay_info\n    level: L3\n", encoding="utf-8")
        with patch.dict("sys.modules", {"yaml": None}):
            guard = self.build(security_levels={"catalog_files": [str(path)]})
        self.assertTrue(any("PyYAML" in e for e in guard.security.load_errors),
                        guard.security.load_errors)

    def test_missing_or_broken_catalog_degrades_instead_of_breaking_the_system(self):
        broken = self.tmp / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        guard = self.build(security_levels={
            "catalog_files": [str(self.tmp / "absent.json"), str(broken)]})
        self.assertTrue(any("broken.json" in e for e in guard.security.load_errors))
        self.assertFalse(any("absent.json" in e for e in guard.security.load_errors))
        # 目录坏了，兜底模式仍在，支付账户列不裸奔。
        self.assertEqual(guard.security.classify("alipay_info")["level"], "L3")

    def test_environment_variable_can_point_at_another_catalog(self):
        path = self.write_catalog({"columns": [{"column": "orders.buyer_note", "level": "L3"}]})
        with patch.dict(os.environ, {"GUARDRAIL_SENSITIVE_COLUMNS_PATH": str(path)}):
            guard = self.build()
        self.assertEqual(guard.security.classify("orders.buyer_note")["level"], "L3")
        # 随仓目录仍然生效，环境变量是「追加一份」而不是「替换掉」。
        self.assertEqual(guard.security.classify("users.bank_card_info")["level"], "L3")

    def test_runtime_registration_accepts_qualified_names_and_is_audited(self):
        guard = self.build()
        count = guard.register_column_security_levels(
            {"users.wx_openid": "L3", "orders.receiver_addr": {"level": "L2", "severity": WARNING}},
            source="metadata-service")
        self.assertEqual(count, 2)
        self.assertEqual(guard.security.classify("users.wx_openid")["source"], "metadata-service")
        self.assertEqual(guard.security.classify("orders.receiver_addr")["level"], "L2")
        sources = [r for r in self.audit_records() if r.get("event") == "guardrail.metadata_source"]
        self.assertTrue(sources)

    def test_configuration_wins_over_runtime_injection(self):
        guard = self.build(security_levels={"columns": {"customer_phone": "L3"}})
        guard.register_column_security_levels({"customer_phone": "L1"}, source="rogue")
        self.assertEqual(guard.security.classify("customer_phone")["level"], "L3")

    def test_runtime_injection_can_tighten_but_never_weaken_a_catalog_entry(self):
        """上游元数据源出错时，不能把支付账户列悄悄降成公开列。"""
        guard = self.build()
        guard.register_column_security_levels({"users.bank_card_info": "L1"}, source="rogue")
        self.assertEqual(guard.security.classify("users.bank_card_info")["level"], "L3")
        # 收紧的方向是允许的：L2 的邮箱列可以被上游提到 L3。
        guard.register_column_security_levels({"users.email": "L3"}, source="dpo-review")
        self.assertEqual(guard.security.classify("users.email")["level"], "L3")

    def test_security_level_aliases_are_normalized(self):
        self.assertEqual(normalize_level("confidential"), "L3")
        self.assertEqual(normalize_level("internal"), "L2")
        self.assertEqual(normalize_level("public"), "L1")
        self.assertEqual(normalize_level(None, "L2"), "L2")
        guard = self.build()
        guard.register_column_security_levels({"vault_ref": "confidential"}, source="engine")
        self.assertEqual(guard.security.classify("vault_ref")["roles"], ["admin"])

    def test_unknown_level_falls_back_to_the_strictest_role_set(self):
        guard = self.build()
        guard.register_column_security_levels({"weird_col": "L9"}, source="engine")
        finding = guard.security.classify("weird_col")
        self.assertEqual(finding["roles"], guard.security.roles_for("L3"))


# ---------------------------------------------------------------------------
# ★ 物理 schema 推断：只出候选，审阅后才生效
# ---------------------------------------------------------------------------
class SchemaInferenceTest(SensitiveColumnTestCase):
    #: 贴着活库形状的 schema 片段（jsonb 半结构化列 + 资金相关列名）。
    LIVE_SHAPED_SCHEMA = {
        "users": [("id", "uuid"), ("username", "character varying"), ("balance", "integer"),
                  ("wallet_info", "jsonb"), ("created_at", "timestamp")],
        "orders": [("payment_method", "character varying"), ("qr_code_url", "character varying")],
    }

    def test_inference_produces_reviewable_candidates(self):
        guard = self.build()
        proposals = guard.propose_column_security_levels(table_columns=self.LIVE_SHAPED_SCHEMA)
        qualified = {p["qualified"] for p in proposals}
        self.assertIn("users.wallet_info", qualified)
        self.assertIn("orders.payment_method", qualified)
        self.assertNotIn("users.created_at", qualified)
        self.assertNotIn("users.id", qualified)
        candidate = next(p for p in proposals if p["qualified"] == "users.wallet_info")
        self.assertEqual(candidate["status"], "candidate")
        self.assertTrue(candidate["signals"])
        self.assertTrue(candidate["reason"])

    def test_candidates_do_not_take_effect_until_they_are_approved(self):
        guard = self.build()
        proposals = guard.propose_column_security_levels(table_columns=self.LIVE_SHAPED_SCHEMA)
        self.assertIsNone(guard.security.classify("wallet_info", table="users"))

        approved = [p for p in proposals if p["qualified"] == "users.wallet_info"]
        self.assertEqual(guard.approve_column_security_candidates(approved, source="dpo-review"), 1)
        finding = guard.security.classify("wallet_info", table="users")
        self.assertEqual(finding["level"], "L3")
        self.assertEqual(finding["source"], "dpo-review")
        # 没被批准的那条仍然不执法。
        self.assertIsNone(guard.security.classify("payment_method", table="orders"))

    def test_review_and_approval_are_written_to_the_audit_trail(self):
        guard = self.build()
        guard.propose_column_security_levels(table_columns=self.LIVE_SHAPED_SCHEMA)
        guard.approve_column_security_candidates(["users.wallet_info"], source="dpo-review")
        events = [r.get("event") for r in self.audit_records()]
        self.assertIn("guardrail.security_candidates", events)
        self.assertIn("guardrail.security_approved", events)

    def test_auto_apply_is_refused_so_inference_can_never_self_enforce(self):
        guard = self.build(security_levels={"inference": {"auto_apply": True}})
        self.assertTrue(any("auto_apply" in e for e in guard.security.load_errors))
        self.assertIsNone(guard.security.classify("wallet_info", table="users"))

    def test_inference_reads_the_schema_provider_when_no_schema_is_passed(self):
        guard = self.build()
        guard.set_schema_provider(_Layer(tables=self.LIVE_SHAPED_SCHEMA))
        proposals = guard.propose_column_security_levels()
        self.assertIn("users.wallet_info", {p["qualified"] for p in proposals})

    def test_inference_can_be_disabled(self):
        guard = self.build(security_levels={"inference": {"enabled": False}})
        self.assertEqual(guard.propose_column_security_levels(
            table_columns=self.LIVE_SHAPED_SCHEMA), [])


# ---------------------------------------------------------------------------
# ★ 密级执法路径：DSL 侧 P0 + 物理 SQL 侧补位
# ---------------------------------------------------------------------------
class SecurityLevelEnforcementTest(SensitiveColumnTestCase):
    SQL = "SELECT bank_card_info FROM users WHERE id = 1"

    def test_sql_path_marks_the_payment_column_by_default(self):
        """默认观察期：标记但放行（系统内部的画像/剖析调用也会经过这里）。"""
        guard = self.build()
        result = guard.check_sql(self.SQL, dialect="mysql", user_role="user")
        self.assertTrue(result["ok"])
        finding = next(w for w in result["warnings"] if w["rule"] == "sql.sensitive_column")
        self.assertEqual(finding["detail"]["column"], "bank_card_info")
        self.assertEqual(finding["detail"]["table"], "users")
        self.assertEqual(finding["detail"]["level"], "L3")

    def test_sql_path_blocks_once_the_table_is_promoted_to_enforcement(self):
        guard = self.build(rules={"sql.sensitive_column": {"severity": WARNING,
                                                           "tables": {"users": ERROR}}})
        with self.assertRaises(GuardrailException) as caught:
            guard.check_sql(self.SQL, dialect="mysql", user_role="user")
        self.assertIn("bank_card_info", caught.exception.message)
        blocked = [r for r in self.audit_records()
                   if r.get("rule") == "sql.sensitive_column" and r.get("outcome") == "block"]
        self.assertEqual(blocked[0]["detail"]["level"], "L3")

    def test_sql_path_resolves_table_aliases(self):
        guard = self.build(rules={"sql.sensitive_column": {"tables": {"users": ERROR}}})
        with self.assertRaises(GuardrailException):
            guard.check_sql("SELECT u.bank_card_info FROM users u", dialect="mysql", user_role="user")

    def test_sql_path_lets_an_authorized_role_through(self):
        guard = self.build(rules={"sql.sensitive_column": {"tables": {"users": ERROR}}})
        result = guard.check_sql(self.SQL, dialect="mysql", user_role="admin")
        self.assertTrue(result["ok"])
        self.assertEqual([w for w in result["warnings"] if w["rule"] == "sql.sensitive_column"], [])

    def test_sql_path_does_not_fire_on_descriptor_columns(self):
        guard = self.build(rules={"sql.sensitive_column": {"tables": {"users": ERROR}}})
        result = guard.check_sql("SELECT phone_type, card_type FROM users",
                                 dialect="mysql", user_role="user")
        self.assertTrue(result["ok"])
        self.assertEqual(result["warnings"], [])

    def test_select_star_warns_about_the_sensitive_columns_it_would_expose(self):
        guard = self.build()
        result = guard.check_sql("SELECT * FROM users", dialect="mysql", user_role="user")
        star = next(w for w in result["warnings"]
                    if w["rule"] == "sql.sensitive_column" and w["detail"]["column"] == "*")
        for column in LIVE_PAYMENT_COLUMNS:
            self.assertIn(column, star["detail"]["exposed"])

    def test_count_star_is_not_treated_as_a_column_exposure(self):
        guard = self.build()
        result = guard.check_sql("SELECT COUNT(*) FROM users", dialect="mysql", user_role="user")
        self.assertEqual(result["warnings"], [])

    def test_sql_rule_can_be_switched_off_entirely(self):
        guard = self.build(rules={"sql.sensitive_column": {"mode": "off"}})
        result = guard.check_sql(self.SQL, dialect="mysql", user_role="user")
        self.assertEqual(result["warnings"], [])

    def test_dsl_rule_remains_a_p0_red_line(self):
        """物理 SQL 侧可以灰度，DSL 侧不行——配置想降级会被强制打回。"""
        guard = self.build(rules={"dsl.sensitive_column": {"mode": "off", "severity": WARNING}})
        self.assertEqual(guard.ruleset.decide("dsl.sensitive_column"), ERROR)
        with self.assertRaises(GuardrailException):
            guard.check_dsl(self.dsl_for("bank_card_info"),
                            self.layer_for("bank_card_info"), user_role="user")

    def test_engine_declared_levels_are_enforced_on_the_backend_side(self):
        """engine 只声明/存储 security_level，执法在 backend：注入即生效，零跨应用 import。"""
        guard = self.build()
        # 形如 engine ontology 下沉出来的列级密级（L1/L2/L3 与 engine 同构取值）。
        guard.register_column_security_levels(
            {"users.wechat_openid": "L3", "columns.price": "L1"}, source="ontology:objects.yaml")
        with self.assertRaises(GuardrailException):
            guard.check_dsl(self.dsl_for("wechat_openid"),
                            self.layer_for("wechat_openid"), user_role="analyst")
        # L1 公开列对所有角色开放，不该被顺手连坐。
        self.assertTrue(guard.check_dsl(self.dsl_for("price"), self.layer_for("price"),
                                        user_role="user")["ok"])

    def test_semantic_layer_column_levels_still_drive_enforcement(self):
        guard = self.build()
        layer = _Layer(metrics=[_Metric("user_count", ["vip_ref"])],
                       dimensions=[_Dimension("vip_ref", security_level="L3")])
        with self.assertRaises(GuardrailException):
            guard.check_dsl(self.dsl_for("vip_ref"), layer, user_role="analyst")

    def test_snapshot_exposes_the_catalog_for_operations(self):
        guard = self.build()
        levels = guard.snapshot()["security_levels"]
        self.assertIn("users.bank_card_info", levels["columns"])
        self.assertTrue(levels["patterns"])
        self.assertTrue(levels["exclusions"])
        self.assertEqual(levels["load_errors"], [])

    def test_role_allowed_helper_keeps_its_contract(self):
        self.assertTrue(ColumnSecurityCatalog.role_allowed("user", ["*"]))
        self.assertTrue(ColumnSecurityCatalog.role_allowed("analyst", ["admin", "analyst"]))
        self.assertFalse(ColumnSecurityCatalog.role_allowed("user", ["admin"]))


if __name__ == "__main__":
    unittest.main()
