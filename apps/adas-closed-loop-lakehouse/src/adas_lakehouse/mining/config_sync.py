"""规则配置的控制面 ↔ 数据面同步：「规则即数据」的落地。

原文（[S3-04] 一、规则即数据）：

    规则配置存在挖掘平台的 MySQL，经 Flink CDC 实时同步入湖（ods_mining_rule_config）。
    规则不是散落在代码里的 if-else，而是与业务数据一样可查询、可追溯、可审计的
    湖仓资产——谁在什么时候改了什么规则，一查便知。

原文（[S3-01] 四、控制面回流数据面）：

    规则配置经 Flink CDC 同步入湖（ods_mining_rule_config），任务与审核动作定期回写
    dwd_mining_task_detail——平台的每一步操作都进血缘，与湖仓闭环。

方向必须记牢：**MySQL 是规则的写入端，湖仓是只读副本**。本模块因此只提供
「从湖仓读规则」和「渲染 CDC 作业」两件事，绝不提供「往 ods_mining_rule_config 写规则」——
那会把单一事实源劈成两半，正是原文 [S3-01] 二点名要避免的坑。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..config import settings
from ._sqlfmt import literal
from .backends import BackendError, SqlBackend
from .constants import EVENT_WINDOW_AFTER_SEC, EVENT_WINDOW_BEFORE_SEC
from .rules import (
    ExecutionMode,
    ExpressionMode,
    RuleDefinition,
    RulePriority,
    RuleStatus,
    RuleType,
    RuleValidationError,
    condition_from_dict,
)
from .tables import ODS_MINING_RULE_CONFIG, RULE_CONFIG_CDC_COLUMNS, qualified

logger = logging.getLogger(__name__)

__all__ = [
    "CDC_SOURCE_TABLE",
    "rule_from_row",
    "rows_to_rules",
    "RuleConfigLoader",
    "render_mysql_control_plane_ddl",
    "render_cdc_source_ddl",
    "render_cdc_sync_job",
    "RuleConfigCdcJob",
]

#: 控制面 MySQL 里规则配置表的表名。
#: ⚠️ 原文未明确，本项目设计：原文只说「规则配置存在挖掘平台的 MySQL」，没给 MySQL
#: 侧的表名。这里取与湖仓侧同名（去掉 ods_ 前缀），便于对账。
CDC_SOURCE_TABLE = "mining_rule_config"


# --------------------------------------------------------------------------- 行 → 规则


def _parse_dt(value: Any) -> datetime | None:
    """宽容地把各种时间表示解析成 datetime。CDC 过来的行类型五花八门。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _priority_from_db(value: Any) -> RulePriority:
    """把 registry 的 INT 型 rule_priority 还原成 :class:`RulePriority`。

    registry 里这一列是 INT（0/1/2/3），引擎侧是 ``P0``/``P1``/... 枚举。
    两种写法都认：历史行或人工导入可能还是 ``'P1'`` 字符串。
    """
    if value is None or value == "":
        return RulePriority.P2
    if isinstance(value, RulePriority):
        return value
    text = str(value).strip()
    try:
        # 两种写法都认，但**越界一律拒**：档位决定向量化队列分级（[S3-04] 一），
        # 悄悄兜底成 P2 会让一条本该优先向量化的规则静默降级。
        return RulePriority(text.upper() if text.upper().startswith("P") else f"P{int(text)}")
    except (TypeError, ValueError) as exc:
        raise RuleValidationError(f"rule_priority={value!r} 既不是 P0-P3 也不是 0-3") from exc


def rule_from_row(row: dict[str, Any]) -> RuleDefinition:
    """把 ods_mining_rule_config 的一行还原成 :class:`RuleDefinition`。

    双模式表达在这里收口：``express_mode='sql'`` 读 ``rule_sql``，
    ``'visual'`` 读 ``rule_condition_json`` 并反序列化成条件树。两条路产出的
    RuleDefinition 对下游完全等价（原文：「产出的规则等价」）。

    列名一律用 registry 的权威名（``rule_category`` / ``exec_mode`` / ``express_mode`` /
    ``rule_sql`` / ``rule_condition_json`` / ``target_tag_id`` / ``create_time`` …），
    引擎侧的领域名只活在 :class:`RuleDefinition` 的字段上——归一映射表见
    catalog/tables/_mining.py 的模块 docstring。

    Args:
        row: 一行规则配置，列名见
            :data:`~adas_lakehouse.mining.tables.RULE_CONFIG_CDC_COLUMNS`。

    Returns:
        RuleDefinition。

    Raises:
        RuleValidationError: 必填列缺失、枚举值非法、或可视化配置 JSON 解析失败。
    """
    missing = [c for c in ("rule_id", "rule_name", "rule_category") if not row.get(c)]
    if missing:
        raise RuleValidationError(f"规则配置行缺少必填列 {missing}: {row!r}")

    try:
        rule_type = RuleType(row["rule_category"])
    except ValueError as exc:
        raise RuleValidationError(
            f"规则 {row['rule_id']} 的 rule_category={row['rule_category']!r} 不在六大种类内"
        ) from exc

    expr_mode = ExpressionMode(row.get("express_mode") or ExpressionMode.VISUAL.value)
    visual = None
    if expr_mode is ExpressionMode.VISUAL:
        raw = row.get("rule_condition_json") or ""
        if not raw.strip():
            raise RuleValidationError(
                f"规则 {row['rule_id']} 声明为可视化模式但 rule_condition_json 为空"
            )
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuleValidationError(
                f"规则 {row['rule_id']} 的 rule_condition_json 不是合法 JSON: {exc}"
            ) from exc
        visual = condition_from_dict(payload)

    exec_mode = row.get("exec_mode")
    return RuleDefinition(
        rule_id=str(row["rule_id"]),
        rule_name=str(row["rule_name"]),
        rule_type=rule_type,
        expression_mode=expr_mode,
        sql_condition=str(row.get("rule_sql") or ""),
        visual_config=visual,
        rule_status=RuleStatus(row.get("rule_status") or RuleStatus.DRAFT.value),
        rule_priority=_priority_from_db(row.get("rule_priority")),
        rule_version=int(row.get("rule_version") or 1),
        execution_mode=ExecutionMode(exec_mode) if exec_mode else None,
        scene_label=str(row.get("target_tag_id") or ""),
        target_clip_count=int(row.get("target_clip_count") or 0),
        project_code=str(row.get("project_code") or ""),
        owner=str(row.get("owner") or ""),
        created_at=_parse_dt(row.get("create_time")),
        updated_at=_parse_dt(row.get("last_modify_time")),
        disabled_at=_parse_dt(row.get("disable_time")),
    )


def rows_to_rules(
    rows: Sequence[dict[str, Any]],
) -> tuple[list[RuleDefinition], list[tuple[str, str]]]:
    """批量还原，坏行不拖垮整批。

    一条规则配置写坏了（比如业务同学拖出一个空条件组），不该让当天所有规则都跑不了。

    Returns:
        ``(解析成功的规则, [(rule_id, 错误原因)])``。
    """
    ok: list[RuleDefinition] = []
    bad: list[tuple[str, str]] = []
    for row in rows:
        rid = str(row.get("rule_id", "<unknown>"))
        try:
            ok.append(rule_from_row(row))
        except (RuleValidationError, ValueError, TypeError) as exc:
            bad.append((rid, str(exc)))
            logger.warning("规则 %s 解析失败，本轮跳过: %s", rid, exc)
    return ok, bad


# --------------------------------------------------------------------------- 从湖仓读规则


@dataclass(slots=True)
class RuleConfigLoader:
    """从 ods_mining_rule_config 读规则。

    读的是湖仓副本而不是 MySQL——控制面可以整体重建（[S3-01] 四），
    执行引擎只依赖湖仓，平台挂了不影响已经入湖的规则照常跑批。
    """

    backend: SqlBackend

    def load(
        self,
        *,
        only_enabled: bool = True,
        execution_mode: ExecutionMode | None = None,
        project_code: str = "",
        rule_ids: Sequence[str] = (),
    ) -> tuple[list[RuleDefinition], list[tuple[str, str]]]:
        """按条件加载规则。

        Args:
            only_enabled: 只要 ENABLED 的规则（调度器的默认行为）。
            execution_mode: 只要某一执行模式的规则，批流双模各取各的。
            project_code: 按项目过滤。
            rule_ids: 指定规则 ID 列表（OpenAPI 手工触发某几条规则时用）。

        Returns:
            ``(规则列表, 解析失败列表)``。

        Raises:
            BackendError: 查询失败。
        """
        preds: list[str] = []
        if only_enabled:
            preds.append(f"rule_status = {literal(RuleStatus.ENABLED.value)}")
        if execution_mode is not None:
            preds.append(f"exec_mode = {literal(execution_mode.value)}")
        if project_code:
            preds.append(f"project_code = {literal(project_code)}")
        if rule_ids:
            preds.append("rule_id IN (" + ", ".join(literal(r) for r in rule_ids) + ")")

        cols = ", ".join(f"`{c}`" for c in RULE_CONFIG_CDC_COLUMNS)
        where = " AND ".join(preds) if preds else "TRUE"
        sql = (
            f"SELECT {cols}\nFROM {qualified(ODS_MINING_RULE_CONFIG)}\n"
            f"WHERE {where}\nORDER BY `rule_priority`, `rule_id`"
        )
        rows = self.backend.query(sql)
        rules, bad = rows_to_rules(rows)
        logger.info(
            "从 %s 载入规则 %d 条（解析失败 %d 条）",
            ODS_MINING_RULE_CONFIG.resolve(),
            len(rules),
            len(bad),
        )
        return rules, bad

    def load_one(self, rule_id: str) -> RuleDefinition:
        """按 ID 取一条规则。

        Raises:
            BackendError: 规则不存在或解析失败。
        """
        rules, bad = self.load(only_enabled=False, rule_ids=(rule_id,))
        if bad:
            raise BackendError(f"规则 {rule_id} 解析失败: {bad[0][1]}")
        if not rules:
            raise BackendError(f"规则 {rule_id} 在 {ODS_MINING_RULE_CONFIG.resolve()} 中不存在")
        return rules[0]


# --------------------------------------------------------------------------- CDC 作业渲染


def render_mysql_control_plane_ddl(table: str = CDC_SOURCE_TABLE) -> str:
    """渲染控制面 MySQL 的规则配置表 DDL。

    ⚠️ 原文未明确，本项目设计：原文只说「规则配置存在挖掘平台的 MySQL」，
    没给这张表的 DDL。**列名一律照 registry 的 ods_mining_rule_config 取**
    （见 :data:`~adas_lakehouse.mining.tables.RULE_CONFIG_CDC_COLUMNS`）——
    CDC 是按列名对位同步的，两端叫法不一致等于一路改名映射，改漏一个就静默同步成 NULL。
    多出的 ``change_log`` 是 MySQL 侧独有的变更留痕（[S3-04] 一），不入湖。
    """
    return f"""
-- 控制面 MySQL：规则配置的写入端（唯一写入端）
-- [S3-04] 一：「规则配置存在挖掘平台的 MySQL，经 Flink CDC 实时同步入湖」
-- 列名与湖仓侧 ods_mining_rule_config 逐列同名，CDC 才能按名对位
CREATE TABLE IF NOT EXISTS `{table}` (
  `rule_id`                 VARCHAR(64)   NOT NULL COMMENT '规则 ID，命中结果携带它作血缘',
  `rule_name`               VARCHAR(128)  NOT NULL COMMENT '规则名，如「夜间雨天急刹」',
  `rule_category`           VARCHAR(32)   NOT NULL COMMENT '六大种类：tag_combination/spatiotemporal/vehicle_signal/model_output/event_trigger/composite',
  `rule_version`            VARCHAR(32)   NOT NULL DEFAULT '1' COMMENT '版本，语义变更 +1',
  `rule_priority`           INT           NOT NULL DEFAULT 2 COMMENT '优先级 0-3（0 最高），直接决定 Embedding 与存储分级',
  `express_mode`            VARCHAR(8)    NOT NULL DEFAULT 'visual' COMMENT '双模式表达：sql / visual',
  `rule_sql`                TEXT                   COMMENT '工程师写的 SQL 条件',
  `rule_condition_json`     MEDIUMTEXT             COMMENT '业务同学拖的可视化配置（条件树 JSON）',
  `exec_mode`               VARCHAR(24)   NOT NULL COMMENT 'batch_t_plus_1 / near_realtime，跟着条件来源走',
  `schedule_cron`           VARCHAR(64)            COMMENT '批模式调度表达式',
  `event_window_before_sec` INT           NOT NULL DEFAULT {EVENT_WINDOW_BEFORE_SEC} COMMENT '事件窗口前置秒数（原文固定 15 秒，引擎不读该列，见 constants.py）',
  `event_window_after_sec`  INT           NOT NULL DEFAULT {EVENT_WINDOW_AFTER_SEC} COMMENT '事件窗口后置秒数（原文固定 5 秒，同上）',
  `target_tag_id`           VARCHAR(128)           COMMENT '命中后要打的场景标签（引擎侧的 scene_label）',
  `target_clip_count`       INT           NOT NULL DEFAULT 0 COMMENT '该场景的需求目标量，场景缺口识别用',
  `project_code`            VARCHAR(64)            COMMENT '所属项目',
  `rule_status`             VARCHAR(16)   NOT NULL DEFAULT 'draft' COMMENT '生命周期：draft/enabled/disabled/archived',
  `create_user`             VARCHAR(64)            COMMENT '创建人',
  `last_modify_user`        VARCHAR(64)            COMMENT '最后变更人——「谁在什么时候改了什么规则」',
  `owner`                   VARCHAR(64)            COMMENT '责任人',
  `create_time`             DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时刻',
  `last_modify_time`        DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '修改时刻',
  `disable_time`            DATETIME(3)            COMMENT '禁用时刻',
  `change_log`              MEDIUMTEXT             COMMENT '变更留痕（RuleChange 列表 JSON）。MySQL 侧独有，不入湖',
  PRIMARY KEY (`rule_id`),
  KEY `idx_status_mode` (`rule_status`, `exec_mode`),
  KEY `idx_priority` (`rule_priority`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='规则挖掘引擎的规则配置（控制面主数据）';
""".strip()


def render_cdc_source_ddl(
    *,
    hostname: str = "localhost",
    # 宿主机口径默认值（容器内是 mysql:3306，见 config.py 顶部端口口径说明）
    port: int = 18606,
    username: str = "adas",
    password: str = "${MYSQL_PASSWORD}",
    database: str = "mining_platform",
    table: str = CDC_SOURCE_TABLE,
    server_id: str = "5400-5404",
) -> str:
    """渲染 Flink MySQL CDC 源表 DDL。

    ⚠️ 原文未明确，本项目设计：原文只说「经 Flink CDC 实时同步入湖」，
    未给 connector 参数。这里用 ``mysql-cdc`` connector 的标准写法，
    ``scan.startup.mode=initial`` 保证首次启动先做一次全量快照再转增量。

    列名与类型对齐 registry 的 ods_mining_rule_config（见
    :data:`~adas_lakehouse.mining.tables.RULE_CONFIG_CDC_COLUMNS`）：
    源表、目标表、:func:`render_cdc_sync_job` 的 SELECT 三处同名，才能按名对位。

    Args:
        password: 不要把明文口令写进 SQL 文件——默认值是环境变量占位符。
    """
    return f"""
-- Flink CDC 源表：控制面 MySQL 的规则配置
-- [S3-01] 四：「控制面回流数据面」——平台的每一步操作都进血缘，与湖仓闭环
CREATE TEMPORARY TABLE `mining_rule_config_cdc_source` (
  `rule_id`                 STRING,
  `rule_name`               STRING,
  `rule_category`           STRING,
  `rule_version`            STRING,
  `rule_priority`           INT,
  `express_mode`            STRING,
  `rule_sql`                STRING,
  `rule_condition_json`     STRING,
  `exec_mode`               STRING,
  `schedule_cron`           STRING,
  `event_window_before_sec` INT,
  `event_window_after_sec`  INT,
  `target_tag_id`           STRING,
  `target_clip_count`       INT,
  `project_code`            STRING,
  `rule_status`             STRING,
  `create_user`             STRING,
  `last_modify_user`        STRING,
  `owner`                   STRING,
  `create_time`             TIMESTAMP(3),
  `last_modify_time`        TIMESTAMP(3),
  `disable_time`            TIMESTAMP(3),
  PRIMARY KEY (`rule_id`) NOT ENFORCED
) WITH (
  'connector' = 'mysql-cdc',
  'hostname' = '{hostname}',
  'port' = '{port}',
  'username' = '{username}',
  'password' = '{password}',
  'database-name' = '{database}',
  'table-name' = '{table}',
  'server-id' = '{server_id}',
  'scan.startup.mode' = 'initial'
);
""".strip()


def render_cdc_sync_job(
    *,
    catalog: str | None = None,
    database: str | None = None,
    source_system: str = "挖掘平台 MySQL",
) -> str:
    """渲染 CDC → ods_mining_rule_config 的同步 INSERT。

    ODS 层的系统字段由本作业补齐：``_ingest_time`` 取处理时间，
    ``_source_system`` 写死为控制面标识——对齐共享契约
    :attr:`adas_lakehouse.domains.Layer.system_fields`（ODS 层用 _source_system，
    不用 update_time）。
    """
    cfg = settings().paimon
    target = qualified(
        ODS_MINING_RULE_CONFIG, catalog=catalog or cfg.catalog, database=database or cfg.database
    )
    business_cols = list(RULE_CONFIG_CDC_COLUMNS)
    select_cols = ",\n  ".join(f"`{c}`" for c in business_cols)
    insert_cols = ", ".join(f"`{c}`" for c in business_cols + ["_ingest_time", "_source_system"])
    return f"""
-- 规则配置入湖：控制面 MySQL --(Flink CDC)--> ods_mining_rule_config
-- [S3-04] 一：「规则不是散落在代码里的 if-else，而是与业务数据一样可查询、可追溯、可审计的湖仓资产」
INSERT INTO {target}
  ({insert_cols})
SELECT
  {select_cols},
  CURRENT_TIMESTAMP AS `_ingest_time`,
  {literal(source_system)} AS `_source_system`
FROM `mining_rule_config_cdc_source`;
""".strip()


@dataclass(slots=True)
class RuleConfigCdcJob:
    """规则配置 CDC 同步作业：渲染完整脚本并提交给 Flink。

    一个作业三段：源表 DDL（MySQL CDC）+ 目标表已由 catalog 建好 + 同步 INSERT。
    目标表 **不在这里建**——挖掘域的表定义归 catalog/tables/ 管，本引擎不越界。
    """

    hostname: str = "localhost"
    #: 宿主机口径默认值（容器内是 mysql:3306，见 config.py 顶部端口口径说明）
    port: int = 18606
    username: str = "adas"
    password: str = "${MYSQL_PASSWORD}"
    database: str = "mining_platform"
    table: str = CDC_SOURCE_TABLE
    server_id: str = "5400-5404"
    source_system: str = "挖掘平台 MySQL"

    def render(self) -> str:
        """渲染完整的 Flink SQL 脚本。"""
        return (
            render_cdc_source_ddl(
                hostname=self.hostname,
                port=self.port,
                username=self.username,
                password=self.password,
                database=self.database,
                table=self.table,
                server_id=self.server_id,
            )
            + "\n\n"
            + render_cdc_sync_job(source_system=self.source_system)
        )

    def submit(self, backend: SqlBackend) -> None:
        """把脚本按语句逐条提交给 Flink SQL Gateway。

        Raises:
            BackendError: 任一语句提交失败。
        """
        statements = [s.strip() for s in self.render().split(";") if s.strip()]
        for stmt in statements:
            backend.execute(stmt + ";")
        logger.info("规则配置 CDC 作业已提交（%d 条语句）", len(statements))
