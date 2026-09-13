# -*- coding: utf-8 -*-
"""全链条安全网闸 Guardrail。

每个 NL2SQL 查询在真正执行前必须经过：
 1. DDL/DML 拦截 (仅允许 SELECT)
 2. 分区键过滤检查 (针对拥有 dt 分区列的大表，由 Schema 发现 + 运行时统计 + 配置填充)
 3. 方言语法预检 (使用 EXPLAIN)
 4. 扫描量预估 (EXPLAIN / EXPLAIN ESTIMATE 熔断)
 5. 超时控制

本模块的三条工程约束：

* **规则外置**：阈值、密级角色、按表灰度都来自 ``backend/config/guardrail_rules.json``
  （可用 ``GUARDRAIL_RULES_PATH`` 指向别处）。配置文件缺失时回退到 :data:`DEFAULT_CONFIG`，
  两者语义一致——删掉配置文件不改变系统行为。
  **敏感列清单**另有一份外置目录 ``backend/config/sensitive_columns.json``（也支持 yaml，
  ``GUARDRAIL_SENSITIVE_COLUMNS_PATH`` 可再追加一份）：具体数据源有哪些敏感列属于元数据，
  不属于代码，所以不写在 :data:`DEFAULT_CONFIG` 里。目录缺失时仍有 ``builtin_patterns``
  兜底（支付账户/凭据/证件号），拦截面只会更窄不会归零。
* **密级 L1/L2/L3 的执法路径**：engine（data-agent-engine）只在本体 ``objects.yaml`` 里
  *声明并存储* ``security_level``，本模块是 *执法端*。两侧取值同构，衔接有三条路且都不
  跨应用 import：①  ``security_levels.metadata_sources`` 读本体文件下沉列级密级；
  ② 适配器调用 :meth:`Guardrail.register_column_security_levels` 运行时注入（推荐）；
  ③ 语义层 ``Dimension.security_level`` / ``column_security_levels`` 回调。
  执法出口两处：``dsl.sensitive_column``（P0，不可降级）与 ``sql.sensitive_column``
  （物理 SQL 侧补位，默认 warning 观察、可按表转 error）。
* **两级出口**：``error`` 拦截（抛 :class:`GuardrailException`），``warning`` 放行但在返回值
  ``warnings[]`` 中标记。两者都落审计。P0 安全规则（越权/行级/列级/DDL/除零/笛卡尔积）
  不允许被配置降级。
* **审计落盘**：jsonl + RotatingFileHandler（默认 10MB × 7），写失败静默降级，绝不影响取数主链路。

返回值契约（向后兼容，只增字段不改字段）：
    check_dsl -> {"status": "PASS", "message": ..., "ok": True, "warnings": [...]}
    check_sql -> {"status": "PASS", "message": ..., "estimated_rows": N,
                  "ok": True, "warnings": [...]}
消费端判 ``result["ok"]`` 之外，应把 ``result["warnings"]`` 透传给前端/日志，否则
WARNING 级命中会被整体丢弃。
"""

import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import threading
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import sqlglot
from sqlglot import exp

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent
DEFAULT_RULES_PATH = BACKEND_ROOT / "config" / "guardrail_rules.json"
# .runtime/ 已在仓库 .gitignore 中，审计日志不会污染工作区。
DEFAULT_AUDIT_DIR = REPO_ROOT / ".runtime" / "guardrail"

# ---------------------------------------------------------------------------
# 严重级与灰度模式
# ---------------------------------------------------------------------------
ERROR = "error"
WARNING = "warning"
OFF = "off"

MODE_FULL = "full"
MODE_GREY = "grey"
MODE_OFF = "off"

_SEVERITY_ALIASES = {
    "error": ERROR, "err": ERROR, "block": ERROR, "deny": ERROR, "fatal": ERROR,
    "warning": WARNING, "warn": WARNING, "shadow": WARNING, "observe": WARNING,
    "off": OFF, "disabled": OFF, "none": OFF, "skip": OFF,
}


def normalize_severity(value: Any, default: Optional[str] = None) -> Optional[str]:
    """把配置里写的各种花名统一成 error / warning / off。无法识别时返回 default。"""
    if value is None:
        return default
    return _SEVERITY_ALIASES.get(str(value).strip().lower(), default)


class GuardrailException(Exception):
    """Guardrail 拦截异常"""
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# ---------------------------------------------------------------------------
# 默认配置（配置文件缺失时的兜底；与 backend/config/guardrail_rules.json 语义一致）
# ---------------------------------------------------------------------------
DEFAULT_CONFIG: Dict[str, Any] = {
    "version": 1,
    "audit": {
        "enabled": True,
        "dir": None,
        "filename": "guardrail_audit.jsonl",
        "max_bytes": 10 * 1024 * 1024,
        "backup_count": 7,
        "log_sql": False,
        "log_pass": True,
    },
    "partition_discovery": {
        "enabled": True,
        "partition_columns": ["dt", "ds", "pt", "p_date", "date", "data_date", "stat_date", "log_date"],
        "exclude_prefixes": ["sqlite_", "information_schema"],
        "exclude_tables": [],
        "min_rows": 0,
        "partition_keys": {},
    },
    "security_levels": {
        "roles": {"L1": ["*"], "L2": ["admin", "analyst"], "L3": ["admin"]},
        # 只有「这套代码在任何数据源上都成立」的列才写在这里；具体数据源的真实列清单
        # 走 catalog_files（backend/config/sensitive_columns.json），属于元数据不是代码。
        "columns": {"customer_phone": "L3", "customer_card_no": "L3"},
        "patterns": [
            {"match": "phone", "level": "L3"},
            {"match": "mobile", "level": "L3"},
            {"match": "card", "level": "L3"},
            {"match": "id_no", "level": "L3"},
            {"match": "idcard", "level": "L3"},
            {"match": "bank_account", "level": "L3"},
            {"match": "passwd", "level": "L3"},
            {"match": "password", "level": "L3"},
            {"match": "email", "level": "L2", "severity": WARNING},
            {"match": "address", "level": "L2", "severity": WARNING},
            {"match": "salary", "level": "L2", "severity": WARNING},
            {"match": "birthday", "level": "L2", "severity": WARNING},
        ],
        # builtin_patterns 与 patterns 的区别：外置规则文件里的 "patterns" 是一个列表，
        # 深合并时整体替换默认值——旧配置文件会把新增的默认模式一起冲掉。兜底模式因此
        # 单独放一个键，永远追加在最后，保证「支付账户/凭据」这类红线不会被旧配置清空。
        "builtin_patterns": [
            {"id": "pay.wallet", "match": r"(^|_)(alipay|zhifubao|wechat|weixin|wxpay|paypal|unionpay)(_|$)",
             "type": "regex", "level": "L3", "reason": "第三方支付账户",
             "examples": ["alipay_info", "wechat_info", "user_wechat_account"],
             "counter_examples": ["wechat_message_count", "alipay_sync_status"]},
            {"id": "pay.bank_account",
             "match": r"(^|_)(bank_card|bankcard|card_no|cardno|card_num|account_info|acct_no|iban|swift|payment_account)(_|$)",
             "type": "regex", "level": "L3", "reason": "银行卡/结算账户",
             "examples": ["bank_card_info", "account_info", "settle_account_info"],
             "counter_examples": ["bank_card_type", "account_info_updated_flag"]},
            {"id": "cred.secret", "match": r"(^|_)(secret|token|api_key|apikey|access_key|private_key)(_|$)",
             "type": "regex", "level": "L3", "reason": "凭据/密钥",
             "examples": ["access_token", "api_key"],
             "counter_examples": ["token_count", "secret_enabled"]},
            {"id": "pii.national_id", "match": r"(^|_)(id_card|idcard|id_number|ssn|passport_no)(_|$)",
             "type": "regex", "level": "L3", "reason": "证件号",
             "examples": ["id_card", "passport_no"], "counter_examples": ["id_card_type"]},
        ],
        # 排除项只作用于「模糊层」（legacy 子串 / pattern / 推断），不会推翻显式登记与元数据密级。
        # 解决的是 phone_type / card_type 这类「描述字段而非字段值」的误伤。
        "exclusions": [
            {"id": "meta.descriptor",
             "match": r"_(type|kind|flag|status|state|count|cnt|enabled|verified|visible|required|format|label|desc|description)$",
             "type": "regex", "reason": "描述字段的元信息而非敏感值本身"},
            {"id": "meta.boolean_prefix", "match": r"^(is|has|need|allow|can)_", "type": "regex",
             "reason": "布尔标记位，不承载敏感值"},
            {"id": "meta.timestamp", "match": r"_(at|time)$", "type": "regex",
             "reason": "时间戳列，不承载敏感值"},
        ],
        "legacy_substring_match": True,
        "metadata_sources": [],
        # 外置敏感列目录（json / yaml）。相对路径以 backend/ 为根；缺失时静默跳过。
        # 环境变量 GUARDRAIL_SENSITIVE_COLUMNS_PATH 可追加一份（优先级最高）。
        "catalog_files": ["config/sensitive_columns.json"],
        # 物理 schema 推断：只产出候选（propose_column_security_levels），需要人工审阅后
        # 用 approve_column_security_candidates 才生效。auto_apply 为安全起见不被支持。
        "inference": {
            "enabled": True,
            "auto_register_as_warning": False,
            "name_rules": [
                {"id": "infer.pay", "match": r"(^|_)(pay|payment|payout|withdraw|withdrawal|settle|refund)(_|$)",
                 "type": "regex", "level": "L3", "reason": "资金/支付相关"},
                {"id": "infer.account", "match": r"(^|_)(account|acct|wallet|balance_detail)(_|$)",
                 "type": "regex", "level": "L3", "reason": "账户标识"},
                {"id": "infer.contact", "match": r"(^|_)(contact|mail|tel|im|qq)(_|$)",
                 "type": "regex", "level": "L2", "reason": "联系方式"},
                {"id": "infer.identity", "match": r"(^|_)(real_name|realname|nickname|username|user_name|birth)(_|$)",
                 "type": "regex", "level": "L2", "reason": "身份信息"},
            ],
            # 半结构化列（jsonb/json/text 等）天然可能塞进任意 PII，命名再带上 info/detail/extra
            # 时列为候选——这正是活库里 bank_card_info / wechat_info / alipay_info 的形态。
            "semi_structured_types": ["json", "jsonb", "jsonb[]", "map", "struct", "variant", "object"],
            "semi_structured_name_hint": r"(^|_)(info|detail|details|extra|extra_data|payload|profile|meta)(_|$)",
            "semi_structured_level": "L2",
        },
    },
    "rules": {
        "dsl.metric_missing": {"mode": MODE_FULL, "severity": ERROR},
        "dsl.metric_unregistered": {"mode": MODE_FULL, "severity": ERROR},
        "dsl.metric_role": {"mode": MODE_FULL, "severity": ERROR},
        "dsl.dimension_unregistered": {"mode": MODE_FULL, "severity": ERROR},
        "dsl.sensitive_column": {"mode": MODE_FULL, "severity": ERROR},
        "dsl.row_level_region": {"mode": MODE_FULL, "severity": ERROR},
        "dsl.metric_dimension_compat": {"mode": MODE_FULL, "severity": ERROR},
        "dsl.time_span": {"mode": MODE_FULL, "severity": ERROR, "max_days": 365},
        "sql.parse": {"mode": MODE_FULL, "severity": ERROR},
        "sql.ddl_dml": {"mode": MODE_FULL, "severity": ERROR},
        # 物理 SQL 侧的列级密级执法。DSL 侧（dsl.sensitive_column）只看得见语义层注册过的
        # 维度，凡是绕过语义层直接生成/手写的 SQL 一律无人看管——这条补的就是那个口子。
        # 默认 warning：先观察不拦截（既有链路里 metadata_enricher 等系统调用也会经过这里），
        # 确认某张表没有误伤后用 tables 登记成 error 执法，或整体提 severity。
        "sql.sensitive_column": {"mode": MODE_FULL, "severity": WARNING, "star_severity": WARNING},
        "sql.division_by_zero": {"mode": MODE_FULL, "severity": ERROR},
        "sql.join_no_equality": {"mode": MODE_FULL, "severity": ERROR},
        "sql.join_cartesian": {"mode": MODE_FULL, "severity": ERROR},
        "sql.dialect_precheck": {"mode": MODE_FULL, "severity": ERROR},
        "sql.partition_pruning": {"mode": MODE_FULL, "severity": WARNING,
                                  "promote_min_samples": 20, "tables": {}},
        "sql.scan_rows": {"mode": MODE_FULL, "severity": ERROR, "limit": 50000},
        "sql.scan_rows_physical": {"mode": MODE_FULL, "severity": WARNING, "limit": 50000},
    },
}

#: P0 安全红线：配置不允许把它们降级为 warning/off。
P0_RULES = frozenset({
    "dsl.metric_role",
    "dsl.sensitive_column",
    "dsl.row_level_region",
    "sql.parse",
    "sql.ddl_dml",
    "sql.division_by_zero",
    "sql.join_no_equality",
    "sql.join_cartesian",
})


def _deep_merge(base: dict, override: dict) -> dict:
    """把 override 深合并进 base 的副本；下划线开头的注释键直接丢弃。"""
    result = dict(base)
    for key, value in (override or {}).items():
        if isinstance(key, str) and key.startswith("_"):
            continue
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        elif isinstance(value, dict):
            result[key] = _deep_merge({}, value)
        else:
            result[key] = value
    return result


def load_rules_file(path: Optional[Path] = None) -> Tuple[dict, Optional[Path], List[str]]:
    """读取外置规则文件。返回 (配置, 实际路径, 错误列表)；任何失败都回退到默认配置。"""
    errors: List[str] = []
    candidate = path or os.getenv("GUARDRAIL_RULES_PATH") or DEFAULT_RULES_PATH
    candidate = Path(candidate)
    if not candidate.is_absolute():
        candidate = (BACKEND_ROOT / candidate).resolve()
    if not candidate.exists():
        return dict(DEFAULT_CONFIG), None, errors
    try:
        raw = candidate.read_text(encoding="utf-8")
        if candidate.suffix.lower() in (".yaml", ".yml"):
            try:
                import yaml  # type: ignore
            except ImportError:
                errors.append(f"规则文件 {candidate} 是 YAML 但当前环境没有 PyYAML，已回退默认规则")
                return dict(DEFAULT_CONFIG), None, errors
            data = yaml.safe_load(raw) or {}
        else:
            data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("规则文件顶层必须是对象")
        return _deep_merge(DEFAULT_CONFIG, data), candidate, errors
    except Exception as exc:  # 配置坏了不能把问数系统带下水
        errors.append(f"规则文件 {candidate} 解析失败，已回退默认规则: {exc}")
        return dict(DEFAULT_CONFIG), None, errors


# ---------------------------------------------------------------------------
# 审计落盘（jsonl + RotatingFileHandler）
# ---------------------------------------------------------------------------
_AUDIT_LOGGER_SEQ = 0


class GuardrailAudit:
    """网闸审计落盘器：一条命中一行 JSON，写失败静默降级。"""

    def __init__(self, config: Optional[dict] = None):
        self._lock = threading.RLock()
        self._logger: Optional[logging.Logger] = None
        self._warned = False
        self.path: Optional[Path] = None
        self.configure(config or DEFAULT_CONFIG["audit"])

    # -- 配置 ---------------------------------------------------------------
    def configure(self, config: dict) -> None:
        with self._lock:
            self.config = dict(DEFAULT_CONFIG["audit"])
            self.config.update(config or {})
            env_enabled = os.getenv("GUARDRAIL_AUDIT_ENABLED")
            if env_enabled is not None:
                self.config["enabled"] = env_enabled.strip().lower() not in ("0", "false", "no", "off")
            env_dir = os.getenv("GUARDRAIL_AUDIT_DIR")
            if env_dir:
                self.config["dir"] = env_dir
            self._close()
            self.path = None
            self._warned = False

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", True))

    @property
    def log_pass(self) -> bool:
        return bool(self.config.get("log_pass", True))

    @property
    def log_sql(self) -> bool:
        return bool(self.config.get("log_sql", False))

    @property
    def target_path(self) -> Path:
        """审计文件的落点（日志器是懒加载的，未写过也能看到配置指向哪里）。"""
        if self.path is not None:
            return self.path
        directory = Path(self.config.get("dir") or DEFAULT_AUDIT_DIR)
        if not directory.is_absolute():
            directory = (BACKEND_ROOT / directory).resolve()
        return directory / str(self.config.get("filename") or "guardrail_audit.jsonl")

    def _close(self) -> None:
        if self._logger is not None:
            for handler in list(self._logger.handlers):
                try:
                    self._logger.removeHandler(handler)
                    handler.close()
                except Exception:
                    pass
        self._logger = None

    def _ensure_logger(self) -> Optional[logging.Logger]:
        if self._logger is not None:
            return self._logger
        with self._lock:
            if self._logger is not None:
                return self._logger
            try:
                directory = Path(self.config.get("dir") or DEFAULT_AUDIT_DIR)
                if not directory.is_absolute():
                    directory = (BACKEND_ROOT / directory).resolve()
                directory.mkdir(parents=True, exist_ok=True)
                target = directory / str(self.config.get("filename") or "guardrail_audit.jsonl")
                global _AUDIT_LOGGER_SEQ
                _AUDIT_LOGGER_SEQ += 1
                logger = logging.getLogger(f"guardrail.audit.{_AUDIT_LOGGER_SEQ}")
                logger.setLevel(logging.INFO)
                logger.propagate = False
                handler = RotatingFileHandler(
                    target,
                    maxBytes=int(self.config.get("max_bytes") or 10 * 1024 * 1024),
                    backupCount=int(self.config.get("backup_count") or 7),
                    encoding="utf-8",
                )
                handler.setFormatter(logging.Formatter("%(message)s"))
                logger.addHandler(handler)
                self._logger = logger
                self.path = target
                return logger
            except Exception as exc:
                if not self._warned:
                    self._warned = True
                    print(f"[Guardrail Audit] 审计日志初始化失败，已降级为不落盘: {exc}", file=sys.stderr)
                return None

    # -- 写入 ---------------------------------------------------------------
    def emit(self, record: dict) -> None:
        if not self.enabled:
            return
        logger = self._ensure_logger()
        if logger is None:
            return
        payload = {"ts": datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")}
        payload.update(record)
        try:
            logger.info(json.dumps(payload, ensure_ascii=False, default=str))
        except Exception as exc:  # 审计绝不能影响主链路
            if not self._warned:
                self._warned = True
                print(f"[Guardrail Audit] 审计写入失败: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# 规则中心（外置规则 + 按表灰度）
# ---------------------------------------------------------------------------
class GuardrailRuleset:
    """规则注册、按表灰度发布与命中统计。

    灰度语义（对齐「先单表试运行、再全量执法」的做法，未 import 任何外部实现）：

    ``off``   规则不跑。
    ``grey``  只在 ``tables`` 里显式登记的表上跑；未登记的表一律不检查。
    ``full``  所有表都跑；未登记的表用规则级 ``severity``，登记的表用表级 severity。
    """

    def __init__(self, config: Optional[dict] = None, path: Optional[Path] = None,
                 load_errors: Optional[List[str]] = None):
        self._lock = threading.RLock()
        self.path = path
        self.load_errors: List[str] = list(load_errors or [])
        self.config = config or dict(DEFAULT_CONFIG)
        self.rules: Dict[str, dict] = {}
        self._stats: Dict[str, Dict[str, int]] = {}
        self._normalize()

    # -- 规范化 -------------------------------------------------------------
    def _normalize(self) -> None:
        merged = _deep_merge(DEFAULT_CONFIG["rules"], self.config.get("rules") or {})
        rules: Dict[str, dict] = {}
        for rule_id, spec in merged.items():
            spec = {k: v for k, v in (spec or {}).items() if not str(k).startswith("_")}
            mode = str(spec.get("mode", MODE_FULL)).strip().lower()
            if mode not in (MODE_FULL, MODE_GREY, MODE_OFF):
                self.load_errors.append(f"规则 {rule_id} 的 mode='{mode}' 非法，按 full 处理")
                mode = MODE_FULL
            severity = normalize_severity(spec.get("severity"), ERROR)
            tables = {}
            for table, value in (spec.get("tables") or {}).items():
                table_sev = normalize_severity(value, None)
                if table_sev is None:
                    self.load_errors.append(f"规则 {rule_id} 表 {table} 的 severity='{value}' 非法，已忽略")
                    continue
                tables[str(table).lower()] = table_sev
            if rule_id in P0_RULES and (mode != MODE_FULL or severity != ERROR or
                                        any(v != ERROR for v in tables.values())):
                self.load_errors.append(
                    f"规则 {rule_id} 属于 P0 安全红线，不接受降级配置，已强制恢复为 full/error")
                mode, severity, tables = MODE_FULL, ERROR, {}
            spec.update({"mode": mode, "severity": severity, "tables": tables})
            rules[rule_id] = spec
        self.rules = rules
        env_partition = normalize_severity(os.getenv("GUARDRAIL_PARTITION_MODE"), None)
        if env_partition is not None:
            self.rules["sql.partition_pruning"]["severity"] = env_partition
            self.rules["sql.partition_pruning"]["mode"] = MODE_FULL

    # -- 查询 ---------------------------------------------------------------
    def rule(self, rule_id: str) -> dict:
        return self.rules.get(rule_id, {"mode": MODE_FULL, "severity": ERROR, "tables": {}})

    def threshold(self, rule_id: str, key: str, default: Any = None) -> Any:
        value = self.rule(rule_id).get(key, default)
        return default if value is None else value

    def decide(self, rule_id: str, table: Optional[str] = None) -> Optional[str]:
        """返回该规则在这张表上的处置级别：error / warning；None 表示不检查。"""
        if rule_id in P0_RULES:
            return ERROR
        spec = self.rule(rule_id)
        tables = spec.get("tables") or {}
        key = (table or "*").lower()
        if key in tables:
            severity = tables[key]
        elif "*" in tables:
            severity = tables["*"]
        elif spec.get("mode") == MODE_OFF:
            return None
        elif spec.get("mode") == MODE_GREY:
            # 灰度必须显式指定「先在哪张表试」，未登记的表不参与检查。
            return None
        else:
            severity = spec.get("severity", ERROR)
        return None if severity == OFF else severity

    def enforced_tables(self, rule_id: str) -> Dict[str, str]:
        return dict(self.rule(rule_id).get("tables") or {})

    # -- 灰度操作 -----------------------------------------------------------
    def start_grey(self, rule_id: str, table: str, severity: str = WARNING) -> dict:
        """按表灰度：规则先在单表上试运行（默认 warning，只标记不拦截）。"""
        severity = normalize_severity(severity, WARNING)
        with self._lock:
            if rule_id in P0_RULES:
                raise ValueError(f"规则 {rule_id} 属于 P0 安全红线，不接受灰度试运行")
            spec = self.rules.setdefault(rule_id, {"mode": MODE_FULL, "severity": ERROR, "tables": {}})
            spec.setdefault("tables", {})[str(table).lower()] = severity
            spec["mode"] = MODE_GREY
            return dict(spec)

    def promote(self, rule_id: str, table: Optional[str] = None, force: bool = False) -> dict:
        """灰度转正：某张表（或整条规则）从 warning 提升为 error 执法。

        未达样本量门槛（``promote_min_samples``）时拒绝转正，``force=True`` 可强推。
        """
        with self._lock:
            spec = self.rules.setdefault(rule_id, {"mode": MODE_FULL, "severity": ERROR, "tables": {}})
            min_samples = int(spec.get("promote_min_samples") or 0)
            observed = self._stats.get(rule_id, {}).get("evaluated", 0)
            if not force and min_samples and observed < min_samples:
                raise ValueError(
                    f"规则 {rule_id} 灰度样本不足: {observed} < {min_samples}，不允许转全量执法")
            if table:
                spec.setdefault("tables", {})[str(table).lower()] = ERROR
            else:
                spec["mode"] = MODE_FULL
                spec["severity"] = ERROR
            return dict(spec)

    def disable(self, rule_id: str, table: Optional[str] = None) -> dict:
        with self._lock:
            if rule_id in P0_RULES:
                raise ValueError(f"规则 {rule_id} 属于 P0 安全红线，不允许下线")
            spec = self.rules.setdefault(rule_id, {"mode": MODE_FULL, "severity": ERROR, "tables": {}})
            if table:
                spec.setdefault("tables", {})[str(table).lower()] = OFF
            else:
                spec["mode"] = MODE_OFF
            return dict(spec)

    # -- 统计 ---------------------------------------------------------------
    def observe(self, rule_id: str, hit: bool, enforced: bool = False) -> None:
        with self._lock:
            stats = self._stats.setdefault(rule_id, {"evaluated": 0, "hits": 0, "blocked": 0})
            stats["evaluated"] += 1
            if hit:
                stats["hits"] += 1
                if enforced:
                    stats["blocked"] += 1

    def stats(self) -> Dict[str, Dict[str, int]]:
        return {k: dict(v) for k, v in self._stats.items()}

    def snapshot(self) -> dict:
        return {
            "path": str(self.path) if self.path else None,
            "load_errors": list(self.load_errors),
            "rules": {k: {"mode": v.get("mode"), "severity": v.get("severity"),
                          "tables": dict(v.get("tables") or {})} for k, v in self.rules.items()},
            "stats": self.stats(),
        }

    def save(self, path: Optional[Path] = None) -> Optional[Path]:
        """把当前灰度状态写回规则文件（显式调用才写，运行期不自动落盘）。"""
        target = Path(path or self.path or DEFAULT_RULES_PATH)
        payload = dict(self.config)
        payload["rules"] = {k: {kk: vv for kk, vv in v.items() if not str(kk).startswith("_")}
                            for k, v in self.rules.items()}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return target


# ---------------------------------------------------------------------------
# 敏感列匹配规则：精确列名 + 显式 pattern 两种规则并存
# ---------------------------------------------------------------------------
MATCH_EXACT = "exact"
MATCH_PREFIX = "prefix"
MATCH_SUFFIX = "suffix"
MATCH_SUBSTRING = "substring"
MATCH_REGEX = "regex"
MATCH_GLOB = "glob"
MATCH_TYPES = (MATCH_EXACT, MATCH_PREFIX, MATCH_SUFFIX, MATCH_SUBSTRING, MATCH_REGEX, MATCH_GLOB)

#: 密级别名：engine 侧用 L1/L2/L3（backend/ontology/objects.yaml），其它元数据系统常写成
#: public/internal/confidential。统一收敛成 L1/L2/L3，避免把 "confidential" 当成未知值放行。
LEVEL_ALIASES = {
    "l1": "L1", "1": "L1", "public": "L1", "open": "L1", "公开": "L1",
    "l2": "L2", "2": "L2", "internal": "L2", "restricted": "L2", "内部": "L2",
    "l3": "L3", "3": "L3", "confidential": "L3", "secret": "L3", "private": "L3",
    "sensitive": "L3", "机密": "L3",
}


def normalize_level(value: Any, default: str = "L3") -> str:
    """把各种写法的密级统一成 L1/L2/L3。

    认不出来的自定义密级按原样大写保留（``roles_for`` 对未知密级本来就退到 L3 的角色名单，
    从严不放松），空值返回 default。
    """
    text = str(value or "").strip()
    if not text:
        return default
    return LEVEL_ALIASES.get(text.lower(), text.upper())


def _level_rank(level: str) -> int:
    """密级的严格程度排序：L1 < L2 < L3 < 任何认不出来的自定义密级（认不出的从严）。"""
    match = re.fullmatch(r"L(\d+)", str(level or "").strip().upper())
    return int(match.group(1)) if match else 99


class ColumnRule:
    """一条敏感列规则。``exact`` 是精确列名，其余是显式声明的 pattern。

    支持的 ``type``：exact / prefix / suffix / substring / regex / glob。
    ``tables`` 限定规则只在某些物理表上生效（表未知时从严按命中处理）。
    ``examples`` / ``counter_examples`` 是规则自带的测试用例，装载时校验，
    写错的 pattern 会进 ``load_errors`` 而不是默默失效或默默扩大拦截面。
    """

    __slots__ = ("id", "match", "match_type", "level", "severity", "reason", "source",
                 "tables", "examples", "counter_examples", "_matcher")

    def __init__(self, spec: Any, *, source: str = "config", default_level: str = "L3",
                 default_type: str = MATCH_SUBSTRING):
        if not isinstance(spec, dict):
            raise ValueError(f"规则必须是对象: {spec!r}")
        raw_match = spec.get("match") or spec.get("column") or spec.get("name") or spec.get("pattern")
        if not raw_match:
            raise ValueError(f"规则缺少 match/column 字段: {spec!r}")
        self.match = str(raw_match).strip().lower()
        match_type = str(spec.get("type") or spec.get("match_type") or default_type).strip().lower()
        if match_type not in MATCH_TYPES:
            raise ValueError(f"规则 {self.match} 的 type='{match_type}' 非法，可选 {list(MATCH_TYPES)}")
        self.match_type = match_type
        self.level = normalize_level(spec.get("level"), default_level)
        self.severity = normalize_severity(spec.get("severity"), None)
        self.reason = str(spec.get("reason") or "")
        self.source = str(spec.get("source") or source)
        self.id = str(spec.get("id") or f"{self.match_type}:{self.match}")
        self.tables = {str(t).strip().lower() for t in (spec.get("tables") or []) if str(t).strip()}
        self.examples = [str(x).lower() for x in (spec.get("examples") or [])]
        self.counter_examples = [str(x).lower() for x in (spec.get("counter_examples") or [])]
        self._matcher = self._compile()

    def _compile(self):
        needle = self.match
        if self.match_type == MATCH_EXACT:
            return lambda column: column == needle
        if self.match_type == MATCH_PREFIX:
            return lambda column: column.startswith(needle)
        if self.match_type == MATCH_SUFFIX:
            return lambda column: column.endswith(needle)
        if self.match_type == MATCH_SUBSTRING:
            return lambda column: needle in column
        if self.match_type == MATCH_GLOB:
            from fnmatch import fnmatchcase
            return lambda column: fnmatchcase(column, needle)
        try:
            compiled = re.compile(needle)
        except re.error as exc:
            raise ValueError(f"规则 {self.id} 的正则非法: {exc}") from exc
        return lambda column: compiled.search(column) is not None

    def matches(self, column: str, table: Optional[str] = None) -> bool:
        # 表限定：表已知且不在名单里才跳过；表未知时从严（宁可多标记也不漏）。
        if self.tables and table and table.lower() not in self.tables:
            return False
        return bool(self._matcher(column or ""))

    def describe(self) -> dict:
        return {"id": self.id, "match": self.match, "type": self.match_type, "level": self.level,
                "severity": self.severity, "reason": self.reason, "source": self.source,
                "tables": sorted(self.tables)}


def _build_rules(specs: Any, *, source: str, default_level: str = "L3",
                 default_type: str = MATCH_SUBSTRING,
                 errors: Optional[List[str]] = None) -> List[ColumnRule]:
    rules: List[ColumnRule] = []
    for spec in specs or []:
        if isinstance(spec, str):
            spec = {"match": spec}
        try:
            rules.append(ColumnRule(spec, source=source, default_level=default_level,
                                    default_type=default_type))
        except Exception as exc:
            if errors is not None:
                errors.append(f"敏感列规则装载失败（{source}）：{exc}")
    return rules


def load_sensitive_catalog_file(path: Path) -> Tuple[dict, List[str]]:
    """读取外置敏感列目录（.json / .yaml / .yml）。返回 (内容, 错误列表)。"""
    errors: List[str] = []
    path = Path(path)
    if not path.exists():
        return {}, errors
    try:
        raw = path.read_text(encoding="utf-8")
        if path.suffix.lower() in (".yaml", ".yml"):
            try:
                import yaml  # type: ignore
            except ImportError:
                errors.append(f"敏感列目录 {path} 是 YAML 但当前环境没有 PyYAML，已跳过该文件")
                return {}, errors
            data = yaml.safe_load(raw) or {}
        else:
            data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("敏感列目录顶层必须是对象")
        return data, errors
    except Exception as exc:
        errors.append(f"敏感列目录 {path} 解析失败，已跳过该文件: {exc}")
        return {}, errors


# ---------------------------------------------------------------------------
# 敏感列密级目录（元数据驱动）
# ---------------------------------------------------------------------------
class ColumnSecurityCatalog:
    """列级密级（L1 公开 / L2 内部 / L3 机密）目录——元数据驱动，不是写死的列名字面量。

    密级来源（从高到低，先命中先返回）：

    1. ``table.column`` 精确登记（外置目录 / 运行时注入的限定列）
    2. ``column`` 精确登记（配置 ``columns`` / 外置目录 / 运行时注入）
    3. 限定列的裸列名回退：登记过 ``users.bank_card_info``、查询里只看得到
       ``bank_card_info``（DSL 维度没有表上下文）时，按最严的那一级从严处理
    4. 外部元数据回调（语义层 ``Dimension.security_level`` / ``column_security_levels``）
    5. 排除项（``exclusions``）——只挡住下面两层模糊匹配，不会推翻 1~4 的显式结论
    6. 兼容层：配置 ``columns`` 的双向子串模糊匹配（保持改造前的拦截面不缩小）
    7. 显式 pattern（``patterns`` + 永不被旧配置冲掉的 ``builtin_patterns``）

    与 data-agent-engine 的衔接：engine 侧 ``backend/ontology/objects.yaml`` 的
    ``security_level``（同一套 L1/L2/L3 取值）可通过 ``metadata_sources`` 读文件下沉，
    或由适配器调用 :meth:`Guardrail.register_column_security_levels` 运行时注入
    （推荐：零跨应用 import）。物理 schema 推断只产出候选，不自动生效。
    """

    def __init__(self, config: Optional[dict] = None):
        self.load_errors: List[str] = []
        self.configure(config or DEFAULT_CONFIG["security_levels"])

    # -- 配置 ---------------------------------------------------------------
    def configure(self, config: dict) -> None:
        merged = _deep_merge(DEFAULT_CONFIG["security_levels"], config or {})
        self.config = merged
        self.load_errors = []
        self.roles = {str(k).upper(): list(v) for k, v in (merged.get("roles") or {}).items()}

        #: 列键 -> 密级。键可以是裸列名，也可以是 ``table.column`` 限定名。
        self.columns: Dict[str, str] = {}
        self.column_sources: Dict[str, str] = {}
        self.column_severity: Dict[str, str] = {}
        self.column_reasons: Dict[str, str] = {}
        self._by_bare: Dict[str, List[str]] = {}

        for key, value in (merged.get("columns") or {}).items():
            level, severity, reason = value, None, ""
            if isinstance(value, dict):
                level = value.get("level")
                severity = normalize_severity(value.get("severity"), None)
                reason = str(value.get("reason") or "")
            self._put(str(key), level, "config.columns", severity, reason)
        # 配置块里显式登记的列：运行时注入不允许覆盖它们（配置是权威）。
        self._config_columns = set(self.columns)
        # 兼容层只在「配置 columns 的裸列名」上做双向子串匹配，保序以复现改造前的命中顺序。
        self._legacy_order = [k for k in self.columns if "." not in k]

        # 外置敏感列目录（元数据，不是代码）——真实数据源的列清单住在这里。
        # 重新 configure 时必须清空，否则同一实例反复装载会把规则越堆越多。
        self._catalog_patterns: List[ColumnRule] = []
        self._catalog_exclusions: List[ColumnRule] = []
        self._catalog_columns: set = set()
        for spec in self._catalog_paths(merged):
            self._load_catalog_file(spec)

        errors: List[str] = []
        self.patterns: List[ColumnRule] = (
            _build_rules(merged.get("patterns"), source="config.patterns", errors=errors)
            + list(self._catalog_patterns)
            + _build_rules(merged.get("builtin_patterns"), source="builtin", errors=errors))
        self.exclusions: List[ColumnRule] = (
            _build_rules(merged.get("exclusions"), source="config.exclusions",
                         default_level="L1", errors=errors)
            + list(self._catalog_exclusions))
        self.legacy_substring_match = bool(merged.get("legacy_substring_match", True))

        inference = dict(merged.get("inference") or {})
        if inference.get("auto_apply"):
            errors.append("security_levels.inference.auto_apply 不被支持：schema 推断结果必须经过"
                          "审阅（approve_column_security_candidates）才能生效，已忽略该开关")
            inference.pop("auto_apply", None)
        self.inference = inference
        self.inference_rules: List[ColumnRule] = _build_rules(
            inference.get("name_rules"), source="inference", errors=errors)
        self.load_errors.extend(errors)
        self.load_errors.extend(self.validate_patterns())

    def _catalog_paths(self, merged: dict) -> List[Any]:
        specs: List[Any] = list(merged.get("catalog_files") or [])
        env_path = os.getenv("GUARDRAIL_SENSITIVE_COLUMNS_PATH")
        if env_path:
            specs.append(env_path)
        return specs

    def _load_catalog_file(self, spec: Any) -> None:
        """装载一份外置敏感列目录；文件缺失是正常情况（静默跳过），解析失败只记错不抛。"""
        enabled, raw_path = True, spec
        if isinstance(spec, dict):
            enabled = bool(spec.get("enabled", True))
            raw_path = spec.get("path")
        if not enabled or not raw_path:
            return
        path = Path(str(raw_path))
        if not path.is_absolute():
            path = (BACKEND_ROOT / path).resolve()
        data, errors = load_sensitive_catalog_file(path)
        self.load_errors.extend(errors)
        if not data:
            return
        source = f"catalog:{path.name}"
        entries = data.get("columns")
        if isinstance(entries, dict):
            entries = [{"column": k, "level": v} for k, v in entries.items()]
        for entry in entries or []:
            if isinstance(entry, str):
                entry = {"column": entry}
            if not isinstance(entry, dict):
                self.load_errors.append(f"敏感列目录 {path.name} 里的条目必须是对象或字符串: {entry!r}")
                continue
            key = entry.get("column") or entry.get("name")
            table = entry.get("table")
            if key and table and "." not in str(key):
                key = f"{table}.{key}"
            if not key:
                self.load_errors.append(f"敏感列目录 {path.name} 有条目缺少 column 字段: {entry!r}")
                continue
            if self._put(str(key), entry.get("level"), f"{source}:{entry.get('owner') or 'catalog'}",
                         normalize_severity(entry.get("severity"), None), str(entry.get("reason") or "")):
                self._catalog_columns.add(str(key).strip().lower().lstrip("."))
        self._catalog_patterns.extend(
            _build_rules(data.get("patterns"), source=source, errors=self.load_errors))
        self._catalog_exclusions.extend(
            _build_rules(data.get("exclusions"), source=source, default_level="L1",
                         errors=self.load_errors))
        for key, value in (data.get("roles") or {}).items():
            self.roles[str(key).upper()] = list(value)

    def _put(self, key: str, level: Any, source: str, severity: Optional[str] = None,
             reason: str = "") -> bool:
        key = str(key).strip().lower().lstrip(".")
        if not key:
            return False
        self.columns[key] = normalize_level(level, "L3")
        self.column_sources[key] = source
        if severity:
            self.column_severity[key] = severity
        else:
            self.column_severity.pop(key, None)
        if reason:
            self.column_reasons[key] = reason
        if "." in key:
            bare = key.rsplit(".", 1)[-1]
            bucket = self._by_bare.setdefault(bare, [])
            if key not in bucket:
                bucket.append(key)
        return True

    # -- 注入 ---------------------------------------------------------------
    def register(self, mapping: Dict[str, str], source: str = "runtime",
                 severity: Optional[str] = None, reason: str = "") -> int:
        """注入列 -> 密级映射（配置里显式登记的列不会被覆盖）。返回生效条数。

        键可以是裸列名 ``bank_card_info``，也可以是限定名 ``users.bank_card_info``；
        值可以是 ``"L3"``，也可以是 ``{"level": "L3", "severity": "warning", "reason": ...}``。

        注入只能收紧不能放松：配置块登记的列不接受覆盖，外置目录登记的列只接受更高密级。
        否则任何一个上游元数据源出错，都能把支付账户列悄悄降级成公开列。
        """
        count = 0
        for column, value in (mapping or {}).items():
            key = str(column).strip().lower()
            if not key or key in self._config_columns:
                continue
            level, entry_severity, entry_reason = value, severity, reason
            if isinstance(value, dict):
                level = value.get("level")
                entry_severity = normalize_severity(value.get("severity"), severity)
                entry_reason = str(value.get("reason") or reason)
            if key in self._catalog_columns and \
                    _level_rank(normalize_level(level, "L3")) < _level_rank(self.columns.get(key, "L3")):
                continue
            if self._put(key, level, source, entry_severity, entry_reason):
                count += 1
        return count

    def roles_for(self, level: str) -> List[str]:
        return list(self.roles.get(str(level).upper(), self.roles.get("L3", ["admin"])))

    def columns_for_table(self, table: str) -> Dict[str, str]:
        """某张物理表上已登记的敏感列 -> 密级（用于 SELECT * 这类整表暴露的判定）。"""
        prefix = f"{str(table or '').strip().lower()}."
        return {k.split(".", 1)[1]: v for k, v in self.columns.items() if k.startswith(prefix)}

    # -- 判级 ---------------------------------------------------------------
    @staticmethod
    def split_name(name: str, table: Optional[str] = None) -> Tuple[Optional[str], str]:
        """把 ``db.schema.table.column`` / ``table.column`` / ``column`` 拆成 (表, 列)。"""
        lowered = (name or "").strip().lower()
        if "." in lowered:
            parts = [p for p in lowered.split(".") if p]
            if len(parts) >= 2:
                return parts[-2], parts[-1]
        return (str(table).strip().lower() if table else None), lowered

    def is_excluded(self, column: str, table: Optional[str] = None) -> Optional[ColumnRule]:
        for rule in self.exclusions:
            if rule.matches(column, table):
                return rule
        return None

    def classify(self, name: str, metadata_lookup=None, table: Optional[str] = None) -> Optional[dict]:
        raw = (name or "").strip()
        if not raw:
            return None
        lowered = raw.lower()
        tbl, col = self.split_name(lowered, table)

        # 1) table.column 精确登记
        if tbl:
            qualified = f"{tbl}.{col}"
            if qualified in self.columns:
                return self._finding(col, self.columns[qualified],
                                     self.column_sources.get(qualified, "config.columns"),
                                     self.column_severity.get(qualified), qualified, table=tbl,
                                     rule_id=qualified)

        # 2) 裸列名精确登记（配置 / 外置目录 / 运行时注入）
        if col in self.columns:
            return self._finding(col, self.columns[col],
                                 self.column_sources.get(col, "config.columns"),
                                 self.column_severity.get(col), col, table=tbl, rule_id=col)

        # 3) 限定列的裸列名回退：DSL 维度没有表上下文，同名列一律按最严的那一级从严处理
        candidates = self._by_bare.get(col) or []
        if candidates:
            strictest = max(candidates, key=lambda k: (_level_rank(self.columns.get(k, "L1")), k))
            return self._finding(col, self.columns[strictest],
                                 f"{self.column_sources.get(strictest, 'catalog')}[qualified-fallback]",
                                 self.column_severity.get(strictest), strictest, table=tbl,
                                 rule_id=strictest)

        # 4) 外部元数据（语义层 Dimension.security_level 等）
        if metadata_lookup is not None:
            try:
                found = metadata_lookup(raw)
            except Exception:
                found = None
            if found:
                level, source = found
                return self._finding(col, level, source, None, col, table=tbl, rule_id=source)

        # 5) 排除项：只挡模糊层。phone_type / card_type 这类「描述字段而非字段值」到此为止。
        excluded = self.is_excluded(col, tbl)
        if excluded is not None:
            return None

        # 6) 兼容层：配置 columns 的双向子串模糊匹配（改造前的行为，拦截面不缩小）
        if self.legacy_substring_match:
            for key in self._legacy_order:
                if key in col or col in key:
                    return self._finding(col, self.columns[key], "config.columns[legacy-substring]",
                                         self.column_severity.get(key), key, table=tbl, rule_id=key)

        # 7) 显式 pattern（手机号/卡号/支付账户/凭据…）
        for rule in self.patterns:
            if rule.matches(col, tbl):
                return self._finding(col, rule.level, f"pattern:{rule.match}", rule.severity,
                                     rule.match, table=tbl, rule_id=rule.id, reason=rule.reason)
        return None

    def explain(self, name: str, table: Optional[str] = None) -> dict:
        """给运维/审阅用的判级说明：命中了哪条规则、为什么没命中。"""
        tbl, col = self.split_name((name or "").strip().lower(), table)
        excluded = self.is_excluded(col, tbl)
        finding = self.classify(name, table=table)
        return {
            "input": name, "table": tbl, "column": col,
            "excluded_by": excluded.id if excluded is not None else None,
            "finding": finding,
        }

    def _finding(self, column: str, level: str, source: str, severity: Optional[str],
                 matched: str, table: Optional[str] = None, rule_id: Optional[str] = None,
                 reason: str = "") -> dict:
        level = normalize_level(level, "L3")
        finding = {
            "column": column,
            "level": level,
            "roles": self.roles_for(level),
            "source": source,
            "severity_cap": severity,
            "matched": matched,
        }
        if table:
            finding["table"] = table
        if rule_id:
            finding["rule_id"] = rule_id
        reason = reason or self.column_reasons.get(matched, "")
        if reason:
            finding["reason"] = reason
        return finding

    # -- 规则自检（pattern 可测试） -----------------------------------------
    def validate_patterns(self) -> List[str]:
        """跑每条规则自带的 examples / counter_examples。

        examples 必须被该条规则命中；counter_examples 必须「命中不了、或被排除项挡掉」——
        后者正是 ``phone`` 模式与 ``phone_type`` 误伤的那条边界。
        """
        failures: List[str] = []
        for rule in list(self.patterns) + list(getattr(self, "inference_rules", [])):
            for sample in rule.examples:
                if not rule.matches(sample):
                    failures.append(f"敏感列规则自检失败：{rule.id} 应命中 '{sample}' 却没命中")
            for sample in rule.counter_examples:
                if rule.matches(sample) and self.is_excluded(sample) is None:
                    failures.append(f"敏感列规则自检失败：{rule.id} 误伤了 '{sample}'（也没有任何排除项兜住）")
        return failures

    # -- 物理 schema 推断：只出候选，不自动生效 ------------------------------
    def propose(self, table_columns: Dict[str, Any], include_covered: bool = False) -> List[dict]:
        """从物理 schema 的列名/类型推断敏感列候选。

        返回的是**待审阅候选**，不写进目录、不参与执法。审阅后调用
        :meth:`Guardrail.approve_column_security_candidates` 才真正生效。
        """
        if not self.inference.get("enabled", True):
            return []
        semi_types = {str(t).lower() for t in (self.inference.get("semi_structured_types") or [])}
        hint = self.inference.get("semi_structured_name_hint")
        hint_re = re.compile(str(hint)) if hint else None
        semi_level = normalize_level(self.inference.get("semi_structured_level"), "L2")

        proposals: List[dict] = []
        for table, columns in (table_columns or {}).items():
            table_key = str(table).strip().lower()
            for column in columns or []:
                name, dtype = _column_name_and_type(column)
                if not name:
                    continue
                qualified = f"{table_key}.{name}"
                covered = self.classify(name, table=table_key)
                if covered and not include_covered:
                    continue
                signals: List[dict] = []
                for rule in self.inference_rules:
                    if rule.matches(name, table_key):
                        signals.append({"rule": rule.id, "level": rule.level,
                                        "reason": rule.reason or "列名规则命中"})
                if dtype and str(dtype).lower() in semi_types and (hint_re is None or hint_re.search(name)):
                    signals.append({"rule": "inference.semi_structured", "level": semi_level,
                                    "reason": f"半结构化列（{dtype}）可能塞进任意 PII"})
                if not signals:
                    continue
                if self.is_excluded(name, table_key) is not None:
                    continue
                level = max((s["level"] for s in signals), key=_level_rank)
                proposals.append({
                    "table": table_key, "column": name, "qualified": qualified,
                    "level": level, "type": dtype,
                    "confidence": "high" if len(signals) > 1 else "medium",
                    "signals": signals,
                    "reason": "；".join(dict.fromkeys(s["reason"] for s in signals)),
                    "status": "covered" if covered else "candidate",
                    "current_level": covered["level"] if covered else None,
                })
        proposals.sort(key=lambda p: (-_level_rank(p["level"]), p["qualified"]))
        return proposals

    def snapshot(self) -> dict:
        return {
            "columns": dict(self.columns),
            "sources": dict(self.column_sources),
            "roles": dict(self.roles),
            "patterns": [r.describe() for r in self.patterns],
            "exclusions": [r.describe() for r in self.exclusions],
            "load_errors": list(self.load_errors),
        }

    @staticmethod
    def role_allowed(user_role: str, roles: List[str]) -> bool:
        return "*" in roles or user_role in roles


def _column_name_and_type(column: Any) -> Tuple[Optional[str], Optional[str]]:
    """把 Schema 发现里的各种列表示统一成 (列名, 类型)。"""
    if isinstance(column, (list, tuple)) and column:
        name = str(column[0]).strip().lower()
        dtype = str(column[1]).strip().lower() if len(column) > 1 and column[1] is not None else None
        return name or None, dtype
    if isinstance(column, dict):
        name = column.get("name") or column.get("column")
        dtype = column.get("type") or column.get("data_type") or column.get("dtype")
        return (str(name).strip().lower() if name else None,
                str(dtype).strip().lower() if dtype else None)
    if isinstance(column, str):
        return column.strip().lower() or None, None
    return None, None


def parse_engine_ontology(path: Path) -> Tuple[Dict[str, str], bool]:
    """把 engine 侧 ontology 的对象级 security_level 下沉成列级密级。

    返回 (列 -> 密级, degraded)。``degraded=True`` 表示没有 PyYAML、走了受限正则解析。
    """
    text = Path(path).read_text(encoding="utf-8")
    mapping: Dict[str, str] = {}
    try:
        import yaml  # type: ignore
    except ImportError:
        yaml = None  # type: ignore

    if yaml is not None:
        data = yaml.safe_load(text) or {}
        for obj in data.get("objects") or []:
            level = str(obj.get("security_level") or "").upper()
            if not level:
                continue
            for prop in obj.get("properties") or []:
                name = prop.get("name") if isinstance(prop, dict) else None
                if name:
                    mapping[str(name).lower()] = str(
                        (prop.get("security_level") if isinstance(prop, dict) else None) or level).upper()
            for table in obj.get("source_tables") or []:
                for physical in (table.get("field_mapping") or {}).values():
                    mapping.setdefault(str(physical).lower(), level)
        return mapping, False

    # 受限降级解析：只抓 objects[].security_level / properties[].name / field_mapping 的物理列。
    blocks = re.split(r"^  - name:", text, flags=re.MULTILINE)[1:]
    for block in blocks:
        level_match = re.search(r"security_level:\s*(L\d)", block)
        if not level_match:
            continue
        level = level_match.group(1).upper()
        props = re.search(r"properties:\s*(.*?)(?:\n    \w|\Z)", block, flags=re.DOTALL)
        if props:
            for name in re.findall(r"\{\s*name:\s*([A-Za-z_][\w]*)", props.group(1)):
                mapping[name.lower()] = level
        for body in re.findall(r"field_mapping:\s*\{(.*?)\}", block, flags=re.DOTALL):
            for _, physical in re.findall(r"([A-Za-z_]\w*)\s*:\s*([A-Za-z_]\w*)", body):
                mapping.setdefault(physical.lower(), level)
    return mapping, True


# ---------------------------------------------------------------------------
# 一次检查的上下文（收集 WARNING 出口 + 审计字段）
# ---------------------------------------------------------------------------
class _CheckContext:
    def __init__(self, event: str, *, user_role: str = "user", dialect: Optional[str] = None,
                 sql: Optional[str] = None):
        self.event = event
        self.user_role = user_role
        self.dialect = dialect
        self.sql = sql
        self.sql_fingerprint = (hashlib.sha256(sql.encode("utf-8")).hexdigest()[:16] if sql else None)
        self.started = time.perf_counter()
        self.warnings: List[dict] = []
        self.tables: List[str] = []

    @property
    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self.started) * 1000)

    def base_record(self) -> dict:
        record = {"event": self.event, "user_role": self.user_role, "elapsed_ms": self.elapsed_ms}
        if self.dialect:
            record["dialect"] = self.dialect
        if self.sql_fingerprint:
            record["sql_sha256"] = self.sql_fingerprint
        if self.tables:
            record["tables"] = list(self.tables)
        return record


class Guardrail:
    def __init__(self, config: Optional[dict] = None, rules_path: Optional[Path] = None):
        # NOTE: 大表分区键检查由「Schema 发现 + 运行时统计 + 外置配置」三方填充，
        # 不再硬编码任何特定仿真表名，也不再是一个永远为空的死字典。
        self._manual_large_tables: Dict[str, str] = {}
        self._auto_large_tables: Dict[str, str] = {}
        self._discovery_signature: Optional[tuple] = None
        self._row_counts: Dict[str, int] = {}
        self._layer_provider = None
        self._scan_row_limit_override: Optional[int] = None
        self.reload_rules(config=config, path=rules_path)

    # ------------------------------------------------------------------
    # 规则装载
    # ------------------------------------------------------------------
    def reload_rules(self, config: Optional[dict] = None, path: Optional[Path] = None) -> dict:
        """重新加载外置规则（配置文件 / 传入 dict）。返回加载快照。"""
        if config is not None:
            merged, source, errors = _deep_merge(DEFAULT_CONFIG, config), None, []
        else:
            merged, source, errors = load_rules_file(path)
        self.config = merged
        self.ruleset = GuardrailRuleset(merged, source, errors)
        existing_audit = getattr(self, "audit", None)
        if existing_audit is None:
            self.audit = GuardrailAudit(merged.get("audit"))
        else:
            # 复用同一个落盘器，旧的 RotatingFileHandler 会被正常关闭，不泄漏文件句柄。
            existing_audit.configure(merged.get("audit"))
        self.security = ColumnSecurityCatalog(merged.get("security_levels"))
        self._discovery_signature = None
        self._auto_large_tables = {}
        self._load_security_metadata_sources()
        for message in list(self.ruleset.load_errors) + list(self.security.load_errors):
            print(f"[Guardrail Config] {message}", file=sys.stderr)
            self.audit.emit({"event": "guardrail.config", "outcome": "degraded", "message": message})
        return self.ruleset.snapshot()

    def _load_security_metadata_sources(self) -> None:
        for source in (self.config.get("security_levels") or {}).get("metadata_sources") or []:
            if not isinstance(source, dict) or not source.get("enabled"):
                continue
            raw_path = source.get("path")
            if not raw_path:
                continue
            path = Path(raw_path)
            if not path.is_absolute():
                path = (BACKEND_ROOT / path).resolve()
            severity = normalize_severity(source.get("severity"), WARNING)
            try:
                mapping, degraded = parse_engine_ontology(path)
                count = self.security.register(mapping, source=f"ontology:{path.name}", severity=severity)
                self.audit.emit({"event": "guardrail.metadata_source",
                                 "outcome": "degraded" if degraded else "loaded",
                                 "path": str(path), "columns": count,
                                 "message": ("没有 PyYAML，已用受限正则解析本体密级" if degraded
                                             else "本体密级已载入")})
            except Exception as exc:
                self.audit.emit({"event": "guardrail.metadata_source", "outcome": "failed",
                                 "path": str(path), "message": str(exc)})

    def register_column_security_levels(self, mapping: Dict[str, str], source: str = "runtime",
                                        severity: Optional[str] = None) -> int:
        """运行时注入列级密级（engine / 元数据服务的推荐衔接方式）。

        键支持 ``column`` 与 ``table.column`` 两种写法；engine 侧密级取值 L1/L2/L3 与本模块
        同构，``public``/``internal``/``confidential`` 之类的别名会被 :func:`normalize_level`
        归一。调用方是元数据服务或适配器，本模块不 import engine 的任何实现。
        """
        count = self.security.register(mapping, source=source, severity=normalize_severity(severity, None))
        self.audit.emit({"event": "guardrail.metadata_source", "outcome": "loaded",
                         "path": source, "columns": count})
        return count

    # ------------------------------------------------------------------
    # 物理 schema 推断：产候选 -> 人工审阅 -> 批准生效
    # ------------------------------------------------------------------
    def propose_column_security_levels(self, layer=None, table_columns: Optional[dict] = None,
                                       include_covered: bool = False) -> List[dict]:
        """按物理 schema 的列名/类型推断敏感列候选，**只出清单不生效**。

        用法：``guardrail.propose_column_security_levels()`` 看候选 → 人工筛掉误报 →
        ``guardrail.approve_column_security_candidates(选中的候选)`` 才进目录参与执法。
        审阅动作本身会落审计（guardrail.security_candidates / guardrail.security_approved）。
        """
        if table_columns is None:
            layer = layer if layer is not None else self._schema_layer()
            table_columns = dict(getattr(layer, "discovered_table_columns", None) or {})
        proposals = self.security.propose(table_columns, include_covered=include_covered)
        self.audit.emit({"event": "guardrail.security_candidates", "outcome": "review",
                         "columns": len(proposals),
                         "detail": [p["qualified"] for p in proposals[:50]]})
        return proposals

    def approve_column_security_candidates(self, candidates: Any, source: str = "schema-review",
                                           severity: Optional[str] = None) -> int:
        """把审阅通过的推断候选登记成正式密级。接受候选 dict、``table.column`` 字符串或两者混列。"""
        mapping: Dict[str, Any] = {}
        for item in candidates or []:
            if isinstance(item, str):
                mapping[item] = "L3"
                continue
            if not isinstance(item, dict):
                continue
            key = item.get("qualified") or item.get("column")
            if item.get("table") and key and "." not in str(key):
                key = f"{item['table']}.{key}"
            if not key:
                continue
            mapping[str(key)] = {"level": item.get("level") or "L3",
                                 "severity": item.get("severity"),
                                 "reason": item.get("reason") or ""}
        count = self.security.register(mapping, source=source,
                                       severity=normalize_severity(severity, None))
        self.audit.emit({"event": "guardrail.security_approved", "outcome": "loaded",
                         "path": source, "columns": count, "detail": sorted(mapping)[:50]})
        return count

    # ------------------------------------------------------------------
    # 阈值 / 大表登记
    # ------------------------------------------------------------------
    @property
    def scan_row_limit(self) -> int:
        if self._scan_row_limit_override is not None:
            return self._scan_row_limit_override
        return int(self.ruleset.threshold("sql.scan_rows", "limit", 50000))

    @scan_row_limit.setter
    def scan_row_limit(self, value: int) -> None:
        self._scan_row_limit_override = int(value)

    @property
    def large_tables(self) -> Dict[str, str]:
        """表名 -> 分区键。自动发现结果与显式登记的并集。"""
        merged = dict(self._auto_large_tables)
        merged.update(self._manual_large_tables)
        return merged

    @large_tables.setter
    def large_tables(self, value: Dict[str, str]) -> None:
        self._manual_large_tables = {str(k).lower(): str(v).lower() for k, v in (value or {}).items()}

    def register_large_table(self, table: str, partition_key: str = "dt") -> None:
        """显式登记一张必须做分区裁剪的大表（配置/元数据之外的运行时入口）。"""
        self._manual_large_tables[str(table).lower()] = str(partition_key).lower()

    def record_table_row_count(self, table: str, rows: int) -> None:
        """登记运行时行数统计，配合 partition_discovery.min_rows 决定哪些表算大表。"""
        self._row_counts[str(table).lower()] = int(rows)
        self._discovery_signature = None

    def set_schema_provider(self, layer) -> None:
        """指定 Schema 发现来源（默认取 semantic_layer 单例）。"""
        self._layer_provider = layer
        self._discovery_signature = None

    def _schema_layer(self):
        if self._layer_provider is not None:
            return self._layer_provider
        try:
            from app.service.semantic_layer import semantic_layer
            return semantic_layer
        except Exception:
            return None

    def refresh_large_tables(self, layer=None, force: bool = False) -> Dict[str, str]:
        """从 Schema 发现 / 运行时统计 / 外置配置三处填充 large_tables。"""
        cfg = self.config.get("partition_discovery") or {}
        explicit = {str(k).lower(): str(v).lower() for k, v in (cfg.get("partition_keys") or {}).items()}
        candidates = [str(c).lower() for c in (cfg.get("partition_columns") or ["dt"])]
        graded = {t for t in self.ruleset.enforced_tables("sql.partition_pruning")}

        layer = layer if layer is not None else self._schema_layer()
        discovered: Dict[str, List[str]] = {}
        if cfg.get("enabled", True) and layer is not None:
            table_columns = getattr(layer, "discovered_table_columns", None) or {}
            for table, columns in table_columns.items():
                names = []
                for column in columns or []:
                    if isinstance(column, (list, tuple)) and column:
                        names.append(str(column[0]).lower())
                    elif isinstance(column, dict) and column.get("name"):
                        names.append(str(column["name"]).lower())
                    elif isinstance(column, str):
                        names.append(column.lower())
                discovered[str(table).lower()] = names

        signature = (tuple(sorted((t, tuple(c)) for t, c in discovered.items())),
                     tuple(sorted(explicit.items())), tuple(sorted(graded)),
                     tuple(sorted(self._row_counts.items())), int(cfg.get("min_rows") or 0),
                     tuple(candidates), bool(cfg.get("enabled", True)))
        if signature == self._discovery_signature and not force:
            return self.large_tables

        exclude_prefixes = tuple(str(p).lower() for p in (cfg.get("exclude_prefixes") or []))
        exclude_tables = {str(t).lower() for t in (cfg.get("exclude_tables") or [])}
        min_rows = int(cfg.get("min_rows") or 0)

        auto: Dict[str, str] = {}
        for table, names in discovered.items():
            if table in exclude_tables or (exclude_prefixes and table.startswith(exclude_prefixes)):
                continue
            partition_key = explicit.get(table) or next((c for c in candidates if c in names), None)
            if not partition_key:
                continue
            # 运行时统计门槛：有统计且明显小于门槛的表不进闸门；没有统计的表按大表从严处理。
            if min_rows and table in self._row_counts and self._row_counts[table] < min_rows:
                continue
            auto[table] = partition_key
        # 配置里显式登记过分区键、或已在灰度名单里的表，即使 Schema 发现没覆盖也纳入。
        for table, partition_key in explicit.items():
            auto[table] = partition_key
        for table in graded:
            if table != "*" and table not in auto:
                auto[table] = explicit.get(table) or (candidates[0] if candidates else "dt")

        self._auto_large_tables = auto
        self._discovery_signature = signature
        return self.large_tables

    # ------------------------------------------------------------------
    # 出口：ERROR 拦截 / WARNING 放行标记，两者都落审计
    # ------------------------------------------------------------------
    def _enforce(self, ctx: _CheckContext, rule_id: str, message: str, *,
                 table: Optional[str] = None, severity_cap: Optional[str] = None,
                 detail: Optional[dict] = None) -> Optional[str]:
        """命中一条规则。返回实际处置级别；ERROR 直接抛出，WARNING 记录后放行。"""
        severity = self.ruleset.decide(rule_id, table)
        if severity is None:
            self.ruleset.observe(rule_id, hit=False)
            return None
        # 分级目录给出的 severity 只能减弱不能加强：新识别出的密级不应凭空变成拦截。
        if severity_cap == WARNING:
            severity = WARNING
        record = ctx.base_record()
        record.update({"rule": rule_id, "severity": severity, "message": message})
        if table:
            record["table"] = table
        if detail:
            record["detail"] = detail
        if self.audit.log_sql and ctx.sql:
            record["sql"] = ctx.sql
        if severity == ERROR:
            self.ruleset.observe(rule_id, hit=True, enforced=True)
            record["outcome"] = "block"
            self.audit.emit(record)
            raise GuardrailException(message)
        self.ruleset.observe(rule_id, hit=True, enforced=False)
        record["outcome"] = "warn"
        self.audit.emit(record)
        warning = {"rule": rule_id, "severity": WARNING, "message": message}
        if table:
            warning["table"] = table
        if detail:
            warning["detail"] = detail
        ctx.warnings.append(warning)
        return WARNING

    def _finish(self, ctx: _CheckContext, payload: dict) -> dict:
        payload = dict(payload)
        payload["ok"] = True
        payload["warnings"] = list(ctx.warnings)
        if self.audit.log_pass or ctx.warnings:
            record = ctx.base_record()
            record.update({"outcome": "warn" if ctx.warnings else "pass",
                           "warnings": len(ctx.warnings), "status": payload.get("status")})
            if "estimated_rows" in payload:
                record["estimated_rows"] = payload["estimated_rows"]
            if self.audit.log_sql and ctx.sql:
                record["sql"] = ctx.sql
            self.audit.emit(record)
        return payload

    # ------------------------------------------------------------------
    # 第一层网闸：DSL 语义审计
    # ------------------------------------------------------------------
    def _metadata_lookup(self, layer):
        def lookup(name: str):
            if layer is None:
                return None
            mapping = getattr(layer, "column_security_levels", None)
            if isinstance(mapping, dict):
                level = mapping.get(name) or mapping.get(name.lower())
                if level:
                    return str(level).upper(), "semantic_layer.column_security_levels"
            try:
                dimension = layer.resolve_dimension(name)
            except Exception:
                dimension = None
            level = getattr(dimension, "security_level", None) if dimension is not None else None
            if level:
                return str(level).upper(), "semantic_layer.Dimension.security_level"
            return None
        return lookup

    def _check_sensitive_column(self, ctx: _CheckContext, layer, name: str,
                                user_role: str, message_builder, table: Optional[str] = None,
                                rule_id: str = "dsl.sensitive_column") -> Optional[dict]:
        finding = self.security.classify(name, metadata_lookup=self._metadata_lookup(layer),
                                         table=table)
        if not finding:
            return None
        if ColumnSecurityCatalog.role_allowed(user_role, finding["roles"]):
            return None
        detail = {"column": name, "level": finding["level"], "source": finding["source"],
                  "allowed_roles": finding["roles"]}
        if finding.get("table"):
            detail["table"] = finding["table"]
        if finding.get("reason"):
            detail["reason"] = finding["reason"]
        self._enforce(
            ctx, rule_id, message_builder(finding["roles"]),
            table=finding.get("table") or table,
            severity_cap=finding.get("severity_cap"),
            detail=detail,
        )
        return finding

    def check_dsl(self, dsl: dict, layer, user_role: str = "user") -> dict:
        """
        DSL 编译前语义网闸审计：
        1. 指标存在性校验
        2. 指标权限校验
        3. 维度与指标的兼容性校验
        4. 查询时间跨度校验 (默认拒绝超过 365 天的查询，阈值外置)
        """
        ctx = _CheckContext("check_dsl", user_role=user_role)

        # 1. 检查 metrics 存在性及权限
        metrics = dsl.get("metrics", [])
        if not metrics:
            self._enforce(ctx, "dsl.metric_missing",
                          "语义审计拦截: 未包含任何查询指标，请提供您想查询的业务指标（如GMV、订单数等）！")

        for m_item in metrics:
            m_name = m_item.get("name")
            metric = layer.resolve_metric(m_name)
            if not metric:
                self._enforce(ctx, "dsl.metric_unregistered",
                              f"语义审计拦截: 发现未注册的指标名 '{m_name}'，请检查输入或在语义层完成指标治理注册！",
                              detail={"metric": m_name})
                continue

            # 2. 检查权限
            if user_role not in metric.authorized_roles:
                self._enforce(ctx, "dsl.metric_role",
                              f"权限审计拦截: 用户角色 '{user_role}' 无权访问受限指标 '{m_name}'，该指标仅限管理员/分析师使用！",
                              detail={"metric": m_name, "authorized_roles": list(metric.authorized_roles)})

        # 维度存在性校验
        dimensions = dsl.get("dimensions", [])
        for d_item in dimensions:
            dim_name = d_item.get("name")
            if not dim_name or dim_name == "month":
                continue
            dim = layer.resolve_dimension(dim_name)
            if not dim:
                self._enforce(ctx, "dsl.dimension_unregistered",
                              f"语义审计拦截: 维度 '{dim_name}' 未在语义层注册，请检查输入！",
                              detail={"dimension": dim_name})

        # =====================================================================
        # 金融级行级与列级权限控制 (最高优先级安全壁垒)
        # =====================================================================
        # 1. 列级权限控制：密级由元数据/本体驱动（L1 公开 / L2 内部 / L3 机密），
        #    配置缺省时退化为改造前的敏感列名模糊匹配，拦截面不缩小。
        filters = dsl.get("filters", [])

        # 检查维度 (dimensions)
        for d_item in dimensions:
            dim_name = d_item.get("name") or ""
            self._check_sensitive_column(
                ctx, layer, dim_name, user_role,
                lambda roles, dim_name=dim_name: (
                    f"金融级列级安全拦截: 发现敏感维度列 '{dim_name}' 越权访问！"
                    f"该敏感列仅对拥有 {', '.join(roles)} 权限的账户开放。"),
            )

        # 检查过滤器条件 (filters)
        for f_item in filters:
            field_name = f_item.get("field") or ""
            self._check_sensitive_column(
                ctx, layer, field_name, user_role,
                lambda roles, field_name=field_name: (
                    f"金融级列级安全拦截: 发现敏感过滤条件字段 '{field_name}' 越权访问！"),
            )

        # 2. 行级权限控制：实现同一指标在不同辖区角色下的行级隔离。
        if user_role == "user":
            # 只有在主表指标支持大区维度 region_name 时，才进行行级隔离自动注入
            supports_region = False
            for m_item in metrics:
                m_name = m_item.get("name")
                metric = layer.resolve_metric(m_name)
                if metric and "region_name" in metric.available_dimensions:
                    supports_region = True
                    break

            if supports_region:
                region_found = False
                for f_item in filters:
                    if f_item.get("field") == "region_name":
                        region_found = True
                        if f_item.get("value") != "华东":
                            self._enforce(
                                ctx, "dsl.row_level_region",
                                f"金融级行级安全拦截: 普通用户角色 '{user_role}' 数据辖区仅限于 '华东'，"
                                f"无权越权拉取其他行级数据（当前请求拉取: '{f_item.get('value')}'）！",
                                detail={"requested": f_item.get("value"), "allowed": "华东"})

                if not region_found:
                    print(f"[Guardrail Row-Level Injection]: Automatically injected region_name = '华东' filter for user role: {user_role}")
                    filters.append({
                        "field": "region_name",
                        "op": "eq",
                        "value": "华东"
                    })
                    self.audit.emit({"event": "check_dsl", "outcome": "inject",
                                     "rule": "dsl.row_level_region", "user_role": user_role,
                                     "detail": {"field": "region_name", "value": "华东"}})

        # 3. 维度与指标兼容性校验 (安全通过后的业务限制校验)
        for d_item in dimensions:
            dim_name = d_item.get("name")
            if not dim_name or dim_name == "month":
                continue
            for m_item in metrics:
                m_name = m_item.get("name")
                metric = layer.resolve_metric(m_name)
                if metric and dim_name not in metric.available_dimensions:
                    self._enforce(
                        ctx, "dsl.metric_dimension_compat",
                        f"语义审计拦截: 指标 '{m_name}' 与维度 '{dim_name}' 不兼容！"
                        f"该指标可用的分解维度为：{', '.join(metric.available_dimensions)}",
                        detail={"metric": m_name, "dimension": dim_name})

        # 4. 时间范围长度校验
        time_range = dsl.get("time_range")
        start_date, end_date = None, None
        if time_range and isinstance(time_range, dict):
            start_date = time_range.get("start")
            end_date = time_range.get("end")
        else:
            for f in filters:
                if f.get("field") == "dt" and f.get("op") == "between":
                    val = f.get("value")
                    if isinstance(val, list) and len(val) == 2:
                        start_date, end_date = val[0], val[1]
                        break

        if start_date and end_date:
            max_days = int(self.ruleset.threshold("dsl.time_span", "max_days", 365))
            try:
                d1 = datetime.strptime(start_date[:10], "%Y-%m-%d")
                d2 = datetime.strptime(end_date[:10], "%Y-%m-%d")
                days = abs((d2 - d1).days)
                if days > max_days:
                    self._enforce(
                        ctx, "dsl.time_span",
                        f"性能审计拦截: 发现查询的时间跨度为 {days} 天，超过了系统允许的最大跨度限制 ({max_days}天)！"
                        f"请缩短时间范围以减轻物理数据库负担。",
                        detail={"days": days, "max_days": max_days})
            except Exception as e:
                if isinstance(e, GuardrailException):
                    raise e

        return self._finish(ctx, {"status": "PASS", "message": "语义审计通过"})

    # ------------------------------------------------------------------
    # 第二层网闸：物理 SQL 审计
    # ------------------------------------------------------------------
    def check_sql(self, sql: str, dialect: str = "mysql", conn=None, user_role: str = "user") -> dict:
        """
        全链路安全与性能审计，通过所有检查返回 dict 信息，否则抛出 GuardrailException
        """
        # 1. 净化并解析 SQL
        cleaned_sql = sql.strip().rstrip(";")
        ctx = _CheckContext("check_sql", user_role=user_role, dialect=dialect, sql=cleaned_sql)

        try:
            expression = sqlglot.parse_one(cleaned_sql, read=dialect)
        except Exception as e:
            self._enforce(ctx, "sql.parse", f"SQL 语法解析失败，无法进行安全审计: {e}")
            return self._finish(ctx, {"status": "PASS", "message": "审计通过", "estimated_rows": 0})

        # 2. DDL / DML 拦截（仅允许查询操作）
        # 遍历 AST 检查节点类型
        for node in expression.walk():
            # 如果包含创建、删除、插入、更新、修改等节点类型则拦截
            if isinstance(node, (exp.Create, exp.Drop, exp.Insert, exp.Update, exp.Delete, exp.Alter)):
                self._enforce(ctx, "sql.ddl_dml",
                              "安全审计拦截: 拒绝执行非 SELECT 查询的操作（拦截 DDL/DML 修改操作）！",
                              detail={"node": type(node).__name__})
                break

        # 2.3 列级密级执法：DSL 侧只覆盖语义层注册过的维度，物理 SQL 侧才是最后一道闸。
        self._check_sql_sensitive_columns(expression, ctx, user_role)

        # 2.5 运行时安全校验：除零保护与多对多关联检验
        self._verify_runtime_safety(expression, dialect, ctx)

        # 3. 分区过滤检查
        # 提取 SQL 中查询的所有表
        tables = [t.name.lower() for t in expression.find_all(exp.Table)]
        ctx.tables = list(dict.fromkeys(tables))
        try:
            # large_tables 的真实写入点：Schema 发现 + 运行时统计 + 外置配置。
            self.refresh_large_tables()
        except Exception as exc:  # 发现失败不能把查询带下水
            self.audit.emit({"event": "check_sql", "outcome": "degraded",
                             "rule": "sql.partition_pruning", "message": f"大表发现失败: {exc}"})
        large_tables = self.large_tables
        for table in tables:
            if table in large_tables:
                partition_key = large_tables[table]
                # 检查 WHERE 条件中是否引用了分区键
                has_partition_filter = False

                # 遍历 WHERE 节点
                for where_node in expression.find_all(exp.Where):
                    # 在 WHERE 的子树中寻找分区键标识符
                    for column in where_node.find_all(exp.Column):
                        if column.name.lower() == partition_key:
                            has_partition_filter = True
                            break

                # 遍历 JOIN 节点，如果是 JOIN ON 关联分区键也可以
                for join_node in expression.find_all(exp.Join):
                    for column in join_node.find_all(exp.Column):
                        if column.name.lower() == partition_key:
                            has_partition_filter = True
                            break

                if not has_partition_filter:
                    self._enforce(
                        ctx, "sql.partition_pruning",
                        f"性能审计拦截: 发现大表 `{table}` 查询未设置分区键 `{partition_key}` 过滤条件！"
                        f"大表必须进行分区剪裁，请带上日期过滤条件（例如: dt BETWEEN ... 或 dt = ...），防止大表全表扫描。",
                        table=table, detail={"partition_key": partition_key})
                else:
                    self.ruleset.observe("sql.partition_pruning", hit=False)

        # 4. 语法预检与扫描量预估
        estimated_rows = 1000  # 默认预估行数
        physical_rows: Optional[int] = None

        from app.service.db_service import db_service
        if os.getenv("DB_TYPE") != "sqlite" and db_service.real_engine:
            try:
                db_type_lower = db_service.active_db_type.lower()
                if "postgre" in db_type_lower:
                    target_dialect = "postgres"
                elif "clickhouse" in db_type_lower:
                    target_dialect = "clickhouse"
                elif "doris" in db_type_lower:
                    target_dialect = "doris"
                elif "starrocks" in db_type_lower:
                    target_dialect = "starrocks"
                else:
                    target_dialect = "mysql"

                translated_sqls = sqlglot.transpile(cleaned_sql, read=dialect, write=target_dialect)
                target_sql = translated_sqls[0]

                # PostgreSQL 不支持 database.table 语法，去掉 db_name 前缀
                if "postgres" in target_dialect:
                    db_name = db_service.get_active_db_name()
                    target_sql = target_sql.replace(f"{db_name}.", "")

                # 直接在真实物理库执行 EXPLAIN
                explain_sql = f"EXPLAIN {target_sql}"
                from sqlalchemy import text
                with db_service.real_engine.connect() as connection:
                    plan = connection.execute(text(explain_sql))
                    # 计划不再被丢弃：解析出估算行数，让扫描量闸门在物理库模式下也有数可判。
                    physical_rows = self._estimated_rows_from_plan(plan)
            except Exception as pe:
                self._enforce(ctx, "sql.dialect_precheck", f"物理数据库方言语法预检报错: {pe}")
            if physical_rows is not None:
                estimated_rows = physical_rows
                limit = int(self.ruleset.threshold("sql.scan_rows_physical", "limit", self.scan_row_limit))
                if physical_rows > limit:
                    self._enforce(
                        ctx, "sql.scan_rows_physical",
                        f"性能熔断拦截: 物理执行计划预估扫描行数 {physical_rows} 超过了安全阈值 {limit} 行！"
                        f"请缩短查询时间范围或加入更窄的维度过滤条件以减少扫描量。",
                        detail={"estimated_rows": physical_rows, "limit": limit})
                else:
                    self.ruleset.observe("sql.scan_rows_physical", hit=False)
        elif conn:
            over_limit = False
            try:
                # 尝试将 SQL 转换成 SQLite 的 EXPLAIN 运行
                sqlite_sqls = sqlglot.transpile(cleaned_sql, read=dialect, write="sqlite")
                sqlite_sql = sqlite_sqls[0]
                sqlite_sql = re.sub(r"TIMESTAMP_TRUNC\(([^,]+),\s*MONTH\)", r"strftime('%Y-%m-01', \1)", sqlite_sql, flags=re.IGNORECASE)
                sqlite_sql = re.sub(r"date_trunc\('month',\s*([^)]+)\)", r"strftime('%Y-%m-01', \1)", sqlite_sql, flags=re.IGNORECASE)

                cursor = conn.cursor()
                explain_sql = f"EXPLAIN QUERY PLAN {sqlite_sql}"
                cursor.execute(explain_sql)
                plan_rows = cursor.fetchall()

                has_scan = False
                for step in plan_rows:
                    detail = step[3].lower() if len(step) > 3 else ""
                    if "scan" in detail:
                        has_scan = True

                date_range_days = 200
                match_dates = re.findall(r"'\d{4}-\d{2}-\d{2}'", cleaned_sql)
                if len(match_dates) >= 2:
                    try:
                        d1 = datetime.strptime(match_dates[0].replace("'", ""), "%Y-%m-%d")
                        d2 = datetime.strptime(match_dates[1].replace("'", ""), "%Y-%m-%d")
                        date_range_days = abs((d2 - d1).days)
                    except Exception:
                        pass

                if has_scan and date_range_days > 150:
                    estimated_rows = date_range_days * 5 * 4 * 10
                else:
                    estimated_rows = date_range_days * 5 * 4

                over_limit = estimated_rows > self.scan_row_limit
            except sqlite3.Error as se:
                self._enforce(ctx, "sql.dialect_precheck", f"方言语法预检报错: {se}")

            if over_limit:
                self._enforce(
                    ctx, "sql.scan_rows",
                    f"性能熔断拦截: 预估扫描行数 {estimated_rows} 超过了安全阈值 {self.scan_row_limit} 行！"
                    f"请缩短查询时间范围（例如限制在 30 天内）或加入更窄的维度过滤条件以减少扫描量。",
                    detail={"estimated_rows": estimated_rows, "limit": self.scan_row_limit})
            else:
                self.ruleset.observe("sql.scan_rows", hit=False)

        return self._finish(ctx, {
            "status": "PASS",
            "message": "审计通过",
            "estimated_rows": estimated_rows,
        })

    #: EXPLAIN 输出里代表「估算行数」的列名（MySQL / Doris / StarRocks / ClickHouse）
    ROW_ESTIMATE_KEYS = frozenset({"rows", "row", "estimate rows", "estimated rows",
                                   "est_rows", "estrows", "cardinality"})

    @classmethod
    def _estimated_rows_from_plan(cls, plan) -> Optional[int]:
        """从物理库 EXPLAIN 结果里解析估算行数；解析不出来返回 None（绝不抛错）。"""
        rows: Any = None
        try:
            rows = plan.mappings().all()
        except Exception:
            try:
                rows = plan.fetchall()
            except Exception:
                return None
        best: Optional[int] = None

        def offer(value: Any) -> None:
            nonlocal best
            if isinstance(value, int) and not isinstance(value, bool):
                best = value if best is None else max(best, value)

        for row in rows or []:
            items = None
            if hasattr(row, "items"):
                try:
                    items = list(row.items())
                except Exception:
                    items = None
            if items is not None:
                for key, value in items:
                    # MySQL / Doris / StarRocks：EXPLAIN 带独立的估算行数列
                    if str(key).strip().lower() in cls.ROW_ESTIMATE_KEYS:
                        offer(value)
                    # PostgreSQL：计划文本里的 rows=N
                    if isinstance(value, str):
                        for match in re.finditer(r"rows=(\d+)", value):
                            offer(int(match.group(1)))
                continue
            text_row = " ".join(str(v) for v in (row if isinstance(row, (list, tuple)) else [row]))
            for match in re.finditer(r"rows=(\d+)", text_row):
                offer(int(match.group(1)))
        return best

    # ------------------------------------------------------------------
    # 物理 SQL 侧的列级密级执法
    # ------------------------------------------------------------------
    @staticmethod
    def _table_aliases(expression) -> Dict[str, str]:
        """别名 -> 物理表名。``FROM users u`` 之后 ``u.bank_card_info`` 才认得出是 users 的列。"""
        aliases: Dict[str, str] = {}
        for table_node in expression.find_all(exp.Table):
            name = (table_node.name or "").lower()
            if not name:
                continue
            alias = (table_node.alias or "").lower()
            if alias:
                aliases[alias] = name
            aliases.setdefault(name, name)
        return aliases

    @staticmethod
    def _starred_tables(expression) -> bool:
        """投影里是否直接出现 ``*``（``COUNT(*)`` 不算——它不吐出列值）。"""
        for select in expression.find_all(exp.Select):
            for projection in select.expressions or []:
                if isinstance(projection, exp.Star):
                    return True
                if isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star):
                    return True
        return False

    def _check_sql_sensitive_columns(self, expression, ctx: _CheckContext, user_role: str) -> None:
        """对物理 SQL 里引用到的列做密级执法。

        默认 severity=warning（观察期，只标记不拦截），把某张表登记成 error 即开始执法：
        ``guardrail.ruleset.rules["sql.sensitive_column"]["tables"]["users"] = "error"``。
        DSL 侧的 dsl.sensitive_column 是 P0 红线不可降级，这条是它在 SQL 层的补位，
        允许按表灰度——因为系统内部调用（画像/剖析）也会经过这里。
        """
        if self.ruleset.decide("sql.sensitive_column") is None and \
                not self.ruleset.enforced_tables("sql.sensitive_column"):
            return
        aliases = self._table_aliases(expression)
        seen: set = set()
        for column_node in expression.find_all(exp.Column):
            column = (column_node.name or "").lower()
            if not column:
                continue
            qualifier = (column_node.table or "").lower()
            table = aliases.get(qualifier, qualifier or None)
            if table is None and len(set(aliases.values())) == 1:
                # 单表查询里不带前缀的列，归属是确定的。
                table = next(iter(aliases.values()))
            key = (table, column)
            if key in seen:
                continue
            seen.add(key)
            self._check_sensitive_column(
                ctx, None, column, user_role,
                lambda roles, column=column, table=table: (
                    f"金融级列级安全拦截: SQL 引用了敏感列 "
                    f"`{(table + '.' + column) if table else column}`，"
                    f"该列仅对拥有 {', '.join(roles)} 权限的账户开放。"),
                table=table, rule_id="sql.sensitive_column")

        # SELECT * 会把整表的敏感列一并带出来，按表已登记的敏感列给出提示。
        if not self._starred_tables(expression):
            return
        star_severity = normalize_severity(
            self.ruleset.threshold("sql.sensitive_column", "star_severity", WARNING), WARNING)
        for table in dict.fromkeys(aliases.values()):
            exposed = {c: level for c, level in self.security.columns_for_table(table).items()
                       if not ColumnSecurityCatalog.role_allowed(user_role, self.security.roles_for(level))}
            if not exposed:
                continue
            self._enforce(
                ctx, "sql.sensitive_column",
                f"金融级列级安全拦截: `SELECT *` 会把表 `{table}` 的敏感列 "
                f"{', '.join(sorted(exposed))} 一并带出，请显式列出需要的列。",
                table=table, severity_cap=star_severity,
                detail={"table": table, "column": "*", "exposed": exposed})

    def _verify_runtime_safety(self, expression, dialect: str, ctx: Optional[_CheckContext] = None):
        """
        运行时校验机制：
        1. 自动审计所有除法表达式，拦截没有任何 NULLIF 除零保护的分母列。
        2. 自动审计 JOIN 条件拓扑结构，拦截没有利用主外键对齐的多对多扇出笛卡尔积。
        """
        from app.service.semantic_layer import semantic_layer

        if ctx is None:
            ctx = _CheckContext("check_sql", dialect=dialect)

        # 1. 扫描除法操作，防护 Division-by-Zero 运行时崩溃
        for div_node in expression.find_all(exp.Div):
            denominator = div_node.expression

            # 如果分母是个数字字面量且非零，那就是安全的
            if isinstance(denominator, exp.Literal):
                try:
                    val = float(denominator.this)
                    if val != 0.0:
                        continue
                except Exception:
                    pass

            # 检查分母子树中是否包含 nullif 保护
            has_nullif = False
            # 包含分母自身以应对分母就是 NULLIF 的情况
            for parent_or_child in [denominator] + list(denominator.walk()):
                if isinstance(parent_or_child, exp.Nullif) or parent_or_child.__class__.__name__.lower() == "nullif":
                    has_nullif = True
                    break
                if isinstance(parent_or_child, (exp.Anonymous, exp.Func)):
                    f_name = parent_or_child.name.lower() if hasattr(parent_or_child, 'name') else str(parent_or_child.this).lower()
                    if "nullif" in f_name:
                        has_nullif = True
                        break

            if not has_nullif:
                self._enforce(
                    ctx, "sql.division_by_zero",
                    "安全审计拦截: 检测到 SQL 除法表达式的分母列未进行 NULLIF(..., 0) 除零安全保护，"
                    "在数据为空或零时极易触发运行时崩溃！")

        # 2. 扫描 JOIN 拓扑关联关系，防护多对多 (Many-to-Many) 笛卡尔积扇出风险
        for join_node in expression.find_all(exp.Join):
            join_table_node = join_node.this
            if not isinstance(join_table_node, exp.Table):
                continue
            join_table_name = join_table_node.name.lower()

            from_table_node = expression.find(exp.Table)
            if not from_table_node:
                continue
            from_table_name = from_table_node.name.lower()

            if join_table_name == from_table_name:
                continue

            on_expr = join_node.args.get("on")
            if not on_expr:
                continue

            has_valid_key_join = False
            eq_conditions = list(on_expr.find_all(exp.EQ))

            if not eq_conditions:
                self._enforce(
                    ctx, "sql.join_no_equality",
                    f"安全审计拦截: 检测到表 `{join_table_name}` 与主表在 JOIN 时缺少等值关联条件，"
                    f"存在笛卡尔积多对多爆表风险！", table=join_table_name)
                continue

            for eq_node in eq_conditions:
                left_col = eq_node.left
                right_col = eq_node.right

                if isinstance(left_col, exp.Column) and isinstance(right_col, exp.Column):
                    left_name = left_col.name.lower()
                    right_name = right_col.name.lower()

                    # 检查是否包含主键或特定外键关联
                    if left_name == "id" or right_name == "id" or left_name.endswith("_id") or right_name.endswith("_id") or "id" in left_name or "id" in right_name:
                        has_valid_key_join = True
                        break

                    # 从语义层 join_paths 寻找匹配
                    for jp in semantic_layer.join_paths:
                        cond_lower = jp.condition.lower()
                        if left_name in cond_lower and right_name in cond_lower:
                            has_valid_key_join = True
                            break

            if not has_valid_key_join:
                self._enforce(
                    ctx, "sql.join_cartesian",
                    f"安全审计拦截: 检测到表 `{join_table_name}` 关联条件非唯一主键/外键对齐，"
                    f"存在多对多(Many-to-Many)扇出风险，会导致指标被成倍放大计算错误！",
                    table=join_table_name)

    # ------------------------------------------------------------------
    # 运维自省
    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        """当前网闸状态：规则、灰度、大表、审计路径——供运维接口与排障使用。"""
        return {
            "rules": self.ruleset.snapshot(),
            "large_tables": self.large_tables,
            "row_counts": dict(self._row_counts),
            "audit_path": str(self.audit.target_path),
            "audit_enabled": self.audit.enabled,
            "security_levels": {
                "columns": dict(self.security.columns),
                "sources": dict(self.security.column_sources),
                "roles": dict(self.security.roles),
                "patterns": [r.describe() for r in self.security.patterns],
                "exclusions": [r.describe() for r in self.security.exclusions],
                "load_errors": list(self.security.load_errors),
            },
        }


guardrail = Guardrail()
