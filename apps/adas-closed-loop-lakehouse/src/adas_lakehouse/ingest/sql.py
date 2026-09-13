"""三通道入湖的 SQL 生成：Flink SQL 片段 + StarRocks 入湖侧 DDL。

生成物落在仓库里，作为可直接提交的生产形态：
  · ``flink/sql/ingest_00_catalog.sql``     Paimon Catalog 与会话参数
  · ``flink/sql/ingest_cdc_mysql.sql``      通道一：CDC（全量快照 → 增量 binlog → 断点续传）
  · ``flink/sql/ingest_kafka_event.sql``    通道二：Kafka 事件流（保留回放能力）
  · ``flink/sql/ingest_oss_file_meta.sql``  通道三：OSS 合规上传的元信息入湖 + 四项门禁 + 隔离分支
  · ``ddl/starrocks_ingest.sql``            入湖侧双路查询（外部表即席查 + 内表物化）

重新生成::

    python -m adas_lakehouse.ingest.sql --write

字段清单优先取共享契约 ``catalog.registry``；尚未登记的表用本模块的
``FALLBACK_SCHEMAS``（⚠️ 本项目推断，见各自注释），注册后自动切换到契约口径。
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..config import settings
from .channels import (
    DEFAULT_CDC_BINDINGS,
    CdcBinding,
    CdcSourceConfig,
    FileMetaBinding,
    KafkaBinding,
    default_file_meta_binding,
    default_kafka_bindings,
)
from .compliance import CONTRACT_REDACTION_FLAGS, RedactionStage
from .constants import (
    ARCHIVE_TIER_COST_FACTOR,
    CDC_SYNC_LATENCY_TEXT,
    COLD_TIER_COST_FACTOR,
    KAFKA_FUTURE_TOLERANCE_SEC,
    KAFKA_LAG_TOLERANCE_DAYS,
    OSS_CHANNEL_GATE_CHECKS,
    OSS_CHANNEL_P0_CHECKS,
)
from .gate import DIMENSION_KEYS, OSS_CHANNEL_CHECKS, QUALITY_ISSUE_TABLE
from .oss import FILE_META_TABLE
from .rows import SYS_INGEST_TIME, SYS_SOURCE_SYSTEM

__all__ = [
    "render_catalog_script",
    "render_cdc_pipeline",
    "render_kafka_pipeline",
    "render_file_meta_pipeline",
    "render_starrocks_ingest",
    "write_sql_files",
    "FALLBACK_SCHEMAS",
    "FILE_META_EXTRA_FIELDS",
    "file_meta_message_schema",
]

_SYSTEM_FIELDS = (SYS_INGEST_TIME, SYS_SOURCE_SYSTEM, "update_time")

#: 过渡期用的推断字段清单：一张 ODS 表在进入 ``catalog.registry`` 之前，本生成器
#: 靠它渲染 SQL。**现在是空的**——本模块用到的三张表（ods_vehicle_trigger_event /
#: ods_production_kafka_event / ods_quality_issue）都已登记进契约，过渡期结束。
#:
#: 为什么清空而不是留着「以防万一」：手写副本一旦与契约分叉，生成的就是一份看着
#: 完整、提交即失败（列不存在 / 列数对不上）的 SQL，而且没人会发现——真出现查不到
#: 表的情况，``_columns_of`` 抛 KeyError 指名道姓地喊一声，比悄悄拿旧列清单顶上安全得多。
FALLBACK_SCHEMAS: dict[str, tuple[tuple[str, str, str, bool], ...]] = {}

#: OSS 合规上传通道的 Kafka 元信息消息里，**比湖表多出来的**那些字段。
#: 它们是门禁判据与合规审计留痕（[a8] 强调、但共享契约 ods_data_file_meta 未设列），
#: 只参与过滤与隔离分支，不写进湖表。
FILE_META_EXTRA_FIELDS: tuple[tuple[str, str, str, bool], ...] = (
    ("file_path", "STRING", "文件本体完整路径，按需读取", True),
    ("storage_class", "STRING", "存储级别：standard/infrequent/archive，对接生命周期管理", True),
    ("project_code", "STRING", "所属项目", True),
    ("vehicle_code", "STRING", "车辆编码", True),
    ("distributed_at", "TIMESTAMP(3)", "合规数据副本分发至智驾云 OSS 的时间", True),
    ("redaction_vehicle_applied", "BOOLEAN", "车端脱敏标记：人脸/车牌个性脱敏 + 地信脱敏", True),
    ("redaction_vehicle_operator", "STRING", "车端脱敏执行方", True),
    ("redaction_vehicle_rules", "STRING", "车端脱敏生效规则", True),
    ("redaction_vehicle_time", "TIMESTAMP(3)", "车端脱敏时间", True),
    ("redaction_cloud_applied", "BOOLEAN", "合规云脱密标记：删除敏感 POI + 模糊桥梁限高", True),
    ("redaction_cloud_operator", "STRING", "合规云脱密执行方（具备资质的合规公司）", True),
    ("redaction_cloud_rules", "STRING", "合规云脱密生效规则", True),
    ("redaction_cloud_time", "TIMESTAMP(3)", "合规云脱密时间", True),
)


def file_meta_message_schema() -> tuple[tuple[str, str, str, bool], ...]:
    """OSS 通道 Kafka 源表的完整字段清单 = 湖表业务列 + 上面那组门禁判据列。

    **必须**由湖表字段清单派生，不能手写一份：分支一的 INSERT 是
    ``SELECT <ods_data_file_meta 的全部业务列> FROM kafka_src_collect_file_meta``，
    源表少声明一列，生成的就是一条引用不存在字段的 SQL——作业提交即报错。
    契约侧后来补的门禁判据列（frame_group_modalities / time_sync_error_ms /
    vehicle_desensitized_flag …）正是这样漏掉的，改成派生后不会再漏。

    源表列一律渲染成**可空**（见 ``_render_source_column``）：脏消息要能被门禁看见
    并进隔离表，而不是让作业在反序列化阶段就崩掉——与 DDL 里
    ``'json.ignore-parse-errors' = 'false'`` 是同一个取向。
    """
    lake_cols = _business_columns(FILE_META_TABLE)
    seen = {c[0] for c in lake_cols}
    extras = tuple(c for c in FILE_META_EXTRA_FIELDS if c[0] not in seen)
    return lake_cols + extras


# --------------------------------------------------------------------------- 工具


def _columns_of(table: str) -> tuple[tuple[str, str, str, bool], ...]:
    """取一张 ODS 表的字段清单：优先契约注册表，其次本模块推断表。

    Returns:
        (列名, 类型, 注释, 可空) 四元组序列。
    """
    try:
        from ..catalog.registry import by_name

        spec = by_name(table)
        return tuple((c.name, c.type, c.comment, c.nullable) for c in spec.all_columns())
    except Exception:
        cols = FALLBACK_SCHEMAS.get(table)
        if cols is None:
            raise KeyError(
                f"表 {table!r} 既未在 catalog.registry 登记，也不在 ingest.sql.FALLBACK_SCHEMAS 中"
            ) from None
        # 推断表不含系统字段，此处按 ODS 层规范补齐
        return cols + (
            (SYS_INGEST_TIME, "TIMESTAMP(3)", "入湖时间", False),
            (SYS_SOURCE_SYSTEM, "STRING", "来源系统标识", False),
        )


def _business_columns(table: str) -> tuple[tuple[str, str, str, bool], ...]:
    """只要业务字段（系统字段由入湖 SQL 显式盖章，不从源表读）。"""
    return tuple(c for c in _columns_of(table) if c[0] not in _SYSTEM_FIELDS)


def _render_column(col: tuple[str, str, str, bool]) -> str:
    name, type_, comment, nullable = col
    null = "" if nullable else " NOT NULL"
    cmt = f" COMMENT '{comment}'" if comment else ""
    return f"  `{name}` {type_}{null}{cmt}"


def _render_source_column(col: tuple[str, str, str, bool]) -> str:
    """渲染**源表**的一列：强制可空。

    源表是 Kafka/CDC 侧的读取结构，不是湖表。把湖表的 NOT NULL 照搬到源表上，
    会让一条缺 file_id 的脏消息在反序列化阶段直接把作业打挂，门禁根本没机会拦它、
    隔离表也就没有这条记录——[a5] 第八章要的是「异常数据进隔离表」，不是作业崩。
    """
    name, type_, comment, _nullable = col
    return _render_column((name, type_, comment, True))


def _fq(table: str) -> str:
    cfg = settings().paimon
    return f"`{cfg.catalog}`.`{cfg.database}`.`{table}`"


def _with(options: dict[str, str]) -> str:
    return "WITH (\n" + ",\n".join(f"  '{k}' = '{v}'" for k, v in options.items()) + "\n)"


def _header(title: str, lines: Iterable[str]) -> str:
    body = "\n".join(f"-- {line}" for line in lines)
    return f"-- {'=' * 72}\n-- {title}\n{body}\n-- {'=' * 72}\n"


# --------------------------------------------------------------------------- Catalog


def render_catalog_script() -> str:
    """Paimon Catalog 与会话参数（三条通道的公共前置）。"""
    minio = settings().minio
    paimon = settings().paimon
    flink = settings().flink
    opts = {
        "type": "paimon",
        "warehouse": minio.warehouse_path,
        "metastore": paimon.metastore,
        "s3.endpoint": minio.endpoint,
        "s3.access-key": minio.access_key,
        # 口令不落盘：部署时用 Flink 的 secret 机制或环境变量注入
        "s3.secret-key": "${MINIO_SECRET_KEY}",
        "s3.path.style.access": "true",
    }
    return (
        _header(
            "三通道入湖 · 公共前置：Paimon Catalog 与会话参数",
            [
                "由 adas_lakehouse.ingest.sql 生成，请勿手工编辑；重新生成：",
                "  python -m adas_lakehouse.ingest.sql --write",
                "连接信息取自 config.settings()（MinIO / Paimon / Flink 三段）。",
            ],
        )
        + f"\nCREATE CATALOG `{paimon.catalog}` {_with(opts)};\n"
        + f"\nUSE CATALOG `{paimon.catalog}`;\n"
        + f"CREATE DATABASE IF NOT EXISTS `{paimon.database}`;\n"
        + f"USE `{paimon.database}`;\n\n"
        + f"SET 'parallelism.default' = '{flink.parallelism}';\n"
        + "-- 断点续传：CDC 三阶段的第三阶段由 checkpoint 承载，作业重启自动从上次位点继续\n"
        + f"SET 'execution.checkpointing.interval' = '{flink.checkpoint_interval_ms} ms';\n"
        + "SET 'execution.checkpointing.mode' = 'EXACTLY_ONCE';\n"
        + "SET 'table.exec.sink.upsert-materialize' = 'NONE';\n"
    )


# --------------------------------------------------------------------------- 通道一 CDC


def render_cdc_pipeline(binding: CdcBinding, source: CdcSourceConfig | None = None) -> list[str]:
    """通道一 · CDC：源表 DDL + INSERT INTO ODS。

    三阶段（[a5] 第六章）在 SQL 里的对应：
      · 全量快照 + 增量 binlog —— ``'scan.startup.mode' = 'initial'``；
      · 断点续传 —— Flink checkpoint（会话参数里设，见 ingest_00_catalog.sql）；
      · 零侵入 —— 只读 binlog，不在业务库建触发器、不改业务代码。

    Args:
        binding: 业务库表 → ODS 表绑定。
        source: MySQL 连接信息，缺省从环境变量读取。

    Returns:
        两条语句：源表 DDL、INSERT INTO。
    """
    src = source or CdcSourceConfig()
    cols = _business_columns(binding.target_table)
    src_table = f"cdc_src_{binding.target_table}"

    pk = binding.primary_key or tuple(c[0] for c in cols if not c[3])[:1]
    pk_sql = ", ".join(f"`{k}`" for k in pk)
    ddl_cols = [_render_column(c) for c in cols]
    if pk_sql:
        ddl_cols.append(f"  PRIMARY KEY ({pk_sql}) NOT ENFORCED")

    options = {
        "connector": "mysql-cdc",
        "hostname": src.hostname,
        "port": str(src.port),
        "username": src.username,
        # 口令不落盘
        "password": "${CDC_MYSQL_PASSWORD}",
        "database-name": binding.database,
        "table-name": binding.table,
        "server-id": src.server_id,
        # 阶段一 → 阶段二：先全量快照，再自动转增量 binlog
        "scan.startup.mode": "initial",
        # 增量快照框架：支持并行快照与无锁读取，业务库零侵入
        "scan.incremental.snapshot.enabled": "true",
    }

    ddl = (
        f"-- 通道一 · Flink CDC：{binding.database}.{binding.table} → {binding.target_table}\n"
        f"-- 承接数据：各平台 MySQL 业务库（产线 / 标注 / 训练等）；入湖方式：读 binlog 实时同步\n"
        f"-- 三阶段：全量快照 → 增量 binlog → 断点续传；同步延迟{CDC_SYNC_LATENCY_TEXT}，业务库零侵入不改代码\n"
        f"CREATE TEMPORARY TABLE `{src_table}` (\n"
        + ",\n".join(ddl_cols)
        + f"\n) {_with(options)};"
    )

    select_cols = ",\n  ".join(f"`{c[0]}`" for c in cols)
    insert = (
        f"-- 统一盖章：ODS 层系统字段 = _ingest_time + _source_system（domains.Layer.ODS.system_fields）\n"
        f"INSERT INTO {_fq(binding.target_table)}\n"
        f"SELECT\n  {select_cols},\n"
        f"  CURRENT_TIMESTAMP AS `{SYS_INGEST_TIME}`,\n"
        f"  '{binding.source_system}' AS `{SYS_SOURCE_SYSTEM}`\n"
        f"FROM `{src_table}`;"
    )
    return [ddl, insert]


# --------------------------------------------------------------------------- 通道二 Kafka


def render_kafka_pipeline(binding: KafkaBinding) -> list[str]:
    """通道二 · Kafka：源表 DDL + 带时空合理性过滤的 INSERT INTO ODS。

    回放能力（[a5] 第六章）：``'scan.startup.mode' = 'group-offsets'``——
    消费失败可从上次位点重新消费，不丢事件；需要重放历史时改
    ``'specific-offsets'`` 或 ``'timestamp'``，DDL 注释里给了现成写法。
    """
    cols = _business_columns(binding.target_table)
    src_table = f"kafka_src_{binding.target_table}"
    cfg = settings().kafka

    options = {
        "connector": "kafka",
        "topic": binding.topic,
        "properties.bootstrap.servers": cfg.bootstrap_servers,
        "properties.group.id": binding.group_id or f"{cfg.group_id}-{binding.target_table}",
        "scan.startup.mode": "group-offsets",
        "properties.auto.offset.reset": "earliest",
        "format": "json",
        # 解析失败不静默跳过：脏消息要能被门禁看见并进隔离表
        "json.ignore-parse-errors": "false",
        "json.fail-on-missing-field": "false",
    }

    ddl = (
        f"-- 通道二 · Kafka：{binding.topic} → {binding.target_table}\n"
        f"-- 承接数据：事件流（产线埋点 / 训练指标 / 车端触发）；入湖方式：Flink 实时消费\n"
        f"-- 回放：各自独立 Topic，消费失败可从上次位点重新消费，不丢事件\n"
        f"--   重放历史 → 'scan.startup.mode' = 'timestamp' + 'scan.startup.timestamp-millis' = '...'\n"
        f"--   精确回放 → 'scan.startup.mode' = 'specific-offsets' + 'scan.startup.specific-offsets' = 'partition:0,offset:42'\n"
        f"-- ODS 只做原样入湖、不做窗口聚合，因此不声明 WATERMARK\n"
        f"CREATE TEMPORARY TABLE `{src_table}` (\n"
        + ",\n".join(_render_column(c) for c in cols)
        + f"\n) {_with(options)};"
    )

    # 分源门禁：Kafka 通道查「时空合理」。阈值与 channels.KafkaChannel 的默认值一致。
    # 注意：条件表达式里不写 `--` 行注释——行注释会把后面的右括号与分号一起吃掉。
    # 所有说明统一放在 INSERT 上方的注释块里。
    conditions: list[str] = []
    notes: list[str] = ["分源门禁：Kafka 通道查「时空合理」"]
    if binding.event_time_field:
        conditions.append(
            f"`{binding.event_time_field}` IS NOT NULL\n"
            f"  AND `{binding.event_time_field}` <= "
            f"TIMESTAMPADD(SECOND, {KAFKA_FUTURE_TOLERANCE_SEC}, CURRENT_TIMESTAMP)\n"
            f"  AND `{binding.event_time_field}` >= "
            f"TIMESTAMPADD(DAY, -{KAFKA_LAG_TOLERANCE_DAYS}, CURRENT_TIMESTAMP)"
        )
        notes.append(
            f"⚠️ 原文未明确，本项目设计：未来容忍 {KAFKA_FUTURE_TOLERANCE_SEC} 秒"
            f"（车端与云端时钟漂移）、滞后容忍 {KAFKA_LAG_TOLERANCE_DAYS} 天"
            "（车端离线缓存后补传）；两个数取自 constants，与 channels.KafkaChannel 同源"
        )
    if binding.lat_field:
        conditions.append(
            f"(`{binding.lat_field}` IS NULL OR `{binding.lat_field}` BETWEEN -90 AND 90)"
        )
    if binding.lon_field:
        conditions.append(
            f"(`{binding.lon_field}` IS NULL OR `{binding.lon_field}` BETWEEN -180 AND 180)"
        )
    if binding.partition_field:
        conditions.append(
            f"`{binding.partition_field}` IS NOT NULL AND `{binding.partition_field}` <> ''"
        )
        notes.append(
            f"分区字段 {binding.partition_field} 非空：分区表主键必须包含分区字段，"
            "空值会落进 __DEFAULT_PARTITION__"
        )
    notes.append("不满足的事件走隔离分支，写法见 ingest_oss_file_meta.sql 的 STATEMENT SET")

    where = "\nWHERE " + "\n  AND ".join(conditions) if conditions else ""
    select_cols = ",\n  ".join(f"`{c[0]}`" for c in cols)
    insert = (
        "\n".join(f"-- {n}" for n in notes) + f"\nINSERT INTO {_fq(binding.target_table)}\n"
        f"SELECT\n  {select_cols},\n"
        f"  CURRENT_TIMESTAMP AS `{SYS_INGEST_TIME}`,\n"
        f"  '{binding.source_system}' AS `{SYS_SOURCE_SYSTEM}`\n"
        f"FROM `{src_table}`{where};"
    )
    return [ddl, insert]


# --------------------------------------------------------------------------- 通道三 OSS


@dataclass(frozen=True, slots=True)
class _SqlCheck:
    """一项门禁检查在 SQL 侧的表达。

    ``condition`` 里**绝不能**出现 ``--`` 行注释：条件会被嵌进 CASE WHEN NOT (...) 与
    WHERE 里，行注释会把后面的右括号和分号一起注释掉，生成的 SQL 直接语法错误。
    说明一律放 ``note``，只在注释块里渲染。
    """

    name: str
    level: str
    code: str
    condition: str
    note: str = ""
    dimension: str = ""


def _file_meta_gate_conditions() -> list[_SqlCheck]:
    """四项专属检查在 SQL 侧的表达。

    检查项的**名字 / 级别 / 编码 / 维度一律取自 ``gate.OSS_CHANNEL_CHECKS``**，本函数只补
    「这一项在 SQL 里怎么写」。以前这四样在这里各抄了一遍，Python 侧改个编码、SQL 侧
    照旧——隔离表里同一项检查就会出现两个 rule_id。
    """
    bucket = settings().minio.raw_bucket
    conditions = [
        (
            "`redaction_vehicle_applied` IS TRUE"
            " AND `redaction_cloud_applied` IS TRUE"
            " AND `redaction_vehicle_operator` IS NOT NULL AND `redaction_vehicle_operator` <> ''"
            " AND `redaction_cloud_operator` IS NOT NULL AND `redaction_cloud_operator` <> ''"
            f" AND `{CONTRACT_REDACTION_FLAGS[RedactionStage.VEHICLE_SIMPLE]}` IS TRUE"
            f" AND `{CONTRACT_REDACTION_FLAGS[RedactionStage.CLOUD_COMPLEX]}` IS TRUE",
            "文件需携带「车端脱敏 + 合规云脱密」双合规标记，缺失即合规风险 → P0 拒绝入湖；"
            "落表的两个 flag 列必须与审计字段一致，否则湖里会留下一个「说自己脱过敏」的假标记",
        ),
        (
            "`data_id` IS NOT NULL"
            " AND REGEXP(`data_id`, '^COLLECT_[A-Z0-9]+_[0-9]{14}_[0-9a-f]{4,}$')",
            "全局数据 ID 格式与来源前缀合法性（血缘追溯起点）→ P0 拒绝入湖",
        ),
        (
            "`file_size_bytes` IS NOT NULL AND `file_size_bytes` > 0"
            " AND (`decodable_flag` IS NULL OR `decodable_flag` IS TRUE)",
            "图像 / 点云文件完整性与可解码性校验 → P1 拒绝入湖；SQL 侧查完整性与上游探针"
            "回填的 decodable_flag（探针未跑过时该列为 NULL，按「查不了 ≠ 查出问题」放行），"
            "魔数级探针在 Python 侧 ingest.oss.probe_decodable",
        ),
        (
            f"`file_path` IS NOT NULL AND `file_path` LIKE 's3://{bucket}/%'"
            " AND `checksum_md5` IS NOT NULL AND REGEXP(`checksum_md5`, '^[0-9a-fA-F]{32}$')",
            f"file_path 合法且指向智驾云 OSS（bucket={bucket}），checksum 可校验 → P1 拒绝入湖",
        ),
    ]
    if len(conditions) != len(OSS_CHANNEL_CHECKS):  # pragma: no cover - 结构性断言
        raise AssertionError(
            f"SQL 侧写了 {len(conditions)} 项检查，gate.OSS_CHANNEL_CHECKS 有 "
            f"{len(OSS_CHANNEL_CHECKS)} 项——四项专属检查必须两侧一一对应"
        )
    return [
        _SqlCheck(
            name=check.name,
            level=check.severity.value,
            code=check.code,
            condition=condition,
            note=note,
            dimension=DIMENSION_KEYS[check.dimension],
        )
        for check, (condition, note) in zip(OSS_CHANNEL_CHECKS, conditions, strict=True)
    ]


def _chk(check: _SqlCheck) -> str:
    """门禁视图里那一项检查的判定列名。每个条件在整份 SQL 里只写一次，其余地方引用它。"""
    return f"chk_{check.code}"


def _isolation_projection(
    checks: Sequence[_SqlCheck], binding: FileMetaBinding
) -> tuple[list[str], list[str]]:
    """隔离分支的 (列清单, 表达式清单)，**只保留 ods_quality_issue 真实存在的列**。

    自造列名是这条分支最容易翻车的地方：``subject_id`` / ``check_codes`` /
    ``issue_reason`` / ``closed_loop_step`` 听起来都对，但契约里一个都没有——
    INSERT 会直接失败，退一步说就算写成不带列名的 SELECT，也会按位错到别的列上。
    所以列清单在这里与 ``catalog.registry`` 对一次账，对不上的直接不生成。
    """
    registered = {c[0] for c in _columns_of(QUALITY_ISSUE_TABLE)}

    def _hit(code_expr: str, sep: str) -> str:
        body = ",\n".join(
            f"      CASE WHEN NOT `{_chk(c)}` THEN {code_expr.format(c=c)} ELSE NULL END"
            for c in checks
        )
        return f"CONCAT_WS('{sep}',\n{body}\n    )"

    rule_ids = _hit("'{c.code}'", ",")
    dimensions = _hit("'{c.dimension}'", ",")
    reason = _hit("'[{c.level}] {c.name}'", " | ")
    names = _hit("'{c.name}'", "；")
    # P0 项（脱敏标记完整性 / data_id 格式合法）命中就是 P0，否则 P1——
    # 级别取自 gate.OSS_CHANNEL_CHECKS，SQL 里不写死。
    level_branches = "".join(
        f"      WHEN NOT `{_chk(c)}` THEN '{c.level}'\n"
        for c in sorted(checks, key=lambda c: c.level)
    )
    issue_level = f"CASE\n{level_branches}      ELSE '{max(c.level for c in checks)}'\n    END"
    # 整条报文原样留存：列清单由源表派生，加列自动跟上
    payload = (
        "JSON_OBJECT(\n"
        + ",\n".join(f"      KEY '{c[0]}' VALUE `{c[0]}`" for c in file_meta_message_schema())
        + "\n    )"
    )

    candidates: list[tuple[str, str]] = [
        (
            "issue_id",
            "CONCAT('oss_file_', `file_id`, '_', DATE_FORMAT(CURRENT_TIMESTAMP, 'yyyyMMddHHmmss'))",
        ),
        ("dt", "DATE_FORMAT(CURRENT_TIMESTAMP, 'yyyy-MM-dd')"),
        ("data_id", "`data_id`"),
        ("project_code", "`project_code`"),
        ("vehicle_code", "`vehicle_code`"),
        # 通道取值与 gate.ISSUE_CHANNEL_CODES / quality.severity.Channel 同口径
        ("source_channel", "'oss_file'"),
        ("target_table", f"'{FILE_META_TABLE}'"),
        ("source_table", f"'{FILE_META_TABLE}'"),
        ("source_system", f"'{binding.source_system}'"),
        ("source_record_key", "CAST(`file_id` AS STRING)"),
        ("record_key", "CAST(`file_id` AS STRING)"),
        ("rule_id", rule_ids),
        ("rule_ids", rule_ids),
        ("rule_dimension", dimensions),
        ("dimension", dimensions),
        # severity 是检查器严重度（ERROR/WARNING），P0~P3 在 issue_level 列
        ("severity", "'ERROR'"),
        ("issue_level", issue_level),
        ("issue_detail", reason),
        ("detail", reason),
        ("message", names),
        ("raw_payload", payload),
        ("replayable", "TRUE"),
        ("isolate_time", "CURRENT_TIMESTAMP"),
        ("detected_at", "CURRENT_TIMESTAMP"),
        ("handle_status", "'isolated'"),
        ("issue_status", "'isolated'"),
        ("recheck_round", "0"),
        ("recheck_count", "0"),
        ("escalated", "FALSE"),
        (SYS_INGEST_TIME, "CURRENT_TIMESTAMP"),
        (SYS_SOURCE_SYSTEM, f"'{binding.source_system}'"),
    ]
    cols = [c for c, _ in candidates if c in registered]
    exprs = [e for c, e in candidates if c in registered]
    return cols, exprs


def render_file_meta_pipeline(binding: FileMetaBinding | None = None) -> list[str]:
    """通道三 · OSS 合规上传：文件元信息入湖 + 四项门禁 + 隔离分支。

    生产形态是一个 STATEMENT SET：一条 INSERT 写 ``ods_data_file_meta``（通过门禁的），
    一条 INSERT 写 ``ods_quality_issue``（被拦截的，进五步异常闭环）。两条共用一次
    Kafka 消费，事件不会被读两遍。

    四项检查的判定先落在一个临时视图的 ``chk_*`` 布尔列上，两条分支都引用这些列——
    每个条件表达式在整份 SQL 里只出现一次，改条件不会漏改第二处。

    Returns:
        四条语句：源表 DDL、门禁判定视图、STATEMENT SET、查询示例（元信息写入即可用）。
    """
    binding = binding or default_file_meta_binding()
    cfg = settings().kafka
    src_table = "kafka_src_collect_file_meta"

    options = {
        "connector": "kafka",
        "topic": binding.topic,
        "properties.bootstrap.servers": cfg.bootstrap_servers,
        "properties.group.id": binding.group_id or f"{cfg.group_id}-{FILE_META_TABLE}",
        "scan.startup.mode": "group-offsets",
        "properties.auto.offset.reset": "earliest",
        "format": "json",
        "json.ignore-parse-errors": "false",
        "json.fail-on-missing-field": "false",
    }

    ddl = (
        "-- 通道三 · OSS 合规上传：采集大文件（图像 / 点云 / 传感器数据）\n"
        "-- 入湖方式：文件本体存 OSS · 元信息经 Kafka 入湖（大文件外置，湖仓只存元信息）\n"
        "-- 五步合规链路：① 车端脱敏 → ② 合规室上传 → ③ 合规脱密 → ④ 合规数据分发 → ⑤ 实时入湖\n"
        "-- 本脚本落地第 ⑤ 步；第 ①~④ 步在车端 / 合规室 / 合规云 / 智驾云完成（见 ingest.compliance）\n"
        "-- 消息结构比 ods_data_file_meta 宽：双合规标记与 storage_class 是门禁判据，\n"
        "-- 共享契约表未设对应列，故只作为过滤条件参与，不写入湖表\n"
        "-- 字段清单由 ods_data_file_meta 的业务列派生（见 file_meta_message_schema），\n"
        "-- 保证下面 SELECT 的每一列在源表里都存在；源表列一律可空，脏消息交给门禁拦\n"
        f"CREATE TEMPORARY TABLE `{src_table}` (\n"
        + ",\n".join(_render_source_column(c) for c in file_meta_message_schema())
        + f"\n) {_with(options)};"
    )

    checks = _file_meta_gate_conditions()
    gate_view = "gated_collect_file_meta"
    pass_expr = "\n    AND ".join(f"`{_chk(c)}`" for c in checks)
    check_comment = "\n".join(f"--   [{c.level}] {c.name}：{c.note}" for c in checks)

    view = (
        "-- 门禁判定：四项专属检查各出一个布尔列，两条分支共用。\n"
        "-- 条件表达式只在这里写一次——写两遍迟早会改漏一处，让「通过」与「被拦」的口径打架。\n"
        f"CREATE TEMPORARY VIEW `{gate_view}` AS\nSELECT\n  *,\n"
        + ",\n".join(f"  ({c.condition}) AS `{_chk(c)}`" for c in checks)
        + f"\nFROM `{src_table}`;"
    )

    target_cols = [c[0] for c in _business_columns(FILE_META_TABLE)]
    select_cols = ",\n    ".join(f"`{c}`" for c in target_cols)

    issue_cols, issue_exprs = _isolation_projection(checks, binding)
    issue_col_sql = ", ".join(f"`{c}`" for c in issue_cols)
    issue_select = ",\n".join(
        f"    {expr} AS `{col}`" for col, expr in zip(issue_cols, issue_exprs, strict=True)
    )

    statement_set = (
        f"-- 合规最后一道闸：本通道有 {OSS_CHANNEL_GATE_CHECKS} 项专属检查，其中 {OSS_CHANNEL_P0_CHECKS} 项是 P0 级\n"
        f"{check_comment}\n"
        "-- 命中拒绝规则的数据进入五步异常闭环（拦截 → 隔离 → 告警 → 分流处置 → 复验）\n"
        "-- 隔离分支的列清单由 catalog.registry 的 ods_quality_issue 派生，并显式写出列名：\n"
        "--   契约里两套同义列名并存（source_record_key≈record_key、rule_id≈rule_ids、\n"
        "--   issue_detail≈detail、isolate_time≈detected_at …），两套都填，读哪套都不取 NULL；\n"
        "--   不显式写列名的 INSERT 一旦契约加列就按位错位，作业提交即失败。\n"
        "EXECUTE STATEMENT SET\nBEGIN\n\n"
        f"  -- 分支一 · 通过门禁 → 元信息写入即可用，下游 DWD 立即可引用，本体按 file_path 按需读取\n"
        f"  INSERT INTO {_fq(FILE_META_TABLE)}\n"
        f"  SELECT\n    {select_cols},\n"
        f"    CURRENT_TIMESTAMP AS `{SYS_INGEST_TIME}`,\n"
        f"    '{binding.source_system}' AS `{SYS_SOURCE_SYSTEM}`\n"
        f"  FROM `{gate_view}`\n"
        f"  WHERE {pass_expr};\n\n"
        f"  -- 分支二 · 命中拒绝规则 → 进隔离表，等待分流处置与复验\n"
        f"  -- raw_payload 存整条报文的 JSON：原始数据不丢失才谈得上复验重放\n"
        f"  INSERT INTO {_fq(QUALITY_ISSUE_TABLE)} ({issue_col_sql})\n"
        "  SELECT\n"
        f"{issue_select}\n"
        f"  FROM `{gate_view}`\n"
        f"  WHERE NOT ({pass_expr});\n\n"
        "END;"
    )

    example = (
        "-- 元信息写入即可用：下游 DWD 加工与查询立即可以引用，文件本体通过 file_path 按需读取\n"
        f"-- SELECT `data_id`, `file_id`, `object_key`, `file_size_bytes`, `checksum_md5`\n"
        f"-- FROM {_fq(FILE_META_TABLE)}\n"
        "-- WHERE `file_type` = 'pointcloud' AND `data_id` = 'COLLECT_BP_20260301123045_b7e2';"
    )
    return [ddl, view, statement_set, example]


# --------------------------------------------------------------------------- StarRocks


def render_starrocks_ingest() -> str:
    """入湖侧的 StarRocks DDL：外部表即席查 + 内表物化的入湖监控。

    [a5] 第七章双路查询：探索式分析走 External Catalog 直查 Paimon（零冗余零搬运），
    监控大屏这类高频固定报表物化成内表（毫秒级、不受湖端 Compaction 影响）。
    本文件只覆盖**入湖侧**的两张监控表，ADS 主题表归 ads 子系统。
    """
    sr = settings().starrocks
    minio = settings().minio
    paimon = settings().paimon

    return f"""-- ========================================================================
-- 三通道入湖 · StarRocks 侧 DDL
-- 由 adas_lakehouse.ingest.sql 生成，请勿手工编辑；重新生成：
--   python -m adas_lakehouse.ingest.sql --write
-- 双路查询（[a5] 第七章）：外部表即席查 Paimon + 内表物化服务监控大屏
-- ========================================================================

-- 路径一 · External Catalog 直查 Paimon：零冗余、零搬运，Paimon 保持单一事实源
CREATE EXTERNAL CATALOG IF NOT EXISTS {sr.external_catalog}
PROPERTIES (
    "type" = "paimon",
    "paimon.catalog.type" = "{paimon.metastore}",
    "paimon.catalog.warehouse" = "{minio.warehouse_path}",
    "aws.s3.endpoint" = "{minio.endpoint}",
    "aws.s3.access_key" = "{minio.access_key}",
    "aws.s3.secret_key" = "${{MINIO_SECRET_KEY}}",
    "aws.s3.enable_path_style_access" = "true"
);

CREATE DATABASE IF NOT EXISTS {sr.internal_database};
USE {sr.internal_database};

-- ------------------------------------------------------------------------
-- 即席查 1：采集文件元信息（大文件外置——湖仓只存路径与元信息）
-- 「找回文件 / 验证文件 / 管理文件」三类问题在这里都能回答
-- ------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS v_collect_file_meta AS
SELECT
    `data_id`,
    `file_id`,
    `file_type`,
    `object_key`,
    `file_size_bytes`,
    `checksum_md5`,
    `sensor_id`,
    `duration_sec`,
    `_ingest_time`,
    `_source_system`
FROM {sr.external_catalog}.{paimon.database}.{FILE_META_TABLE};

-- ------------------------------------------------------------------------
-- 即席查 2：入湖门禁拦截明细（五步异常闭环的可视化入口）
-- ⚠️ 依赖 quality 子系统登记的 {QUALITY_ISSUE_TABLE}；该表按 dt 分区
-- ------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS v_ingest_gate_rejection AS
SELECT
    `dt`,
    `source_channel`,
    `severity`,
    `is_compliance_issue`,
    `check_codes`,
    `issue_reason`,
    `subject_id`,
    `data_id`,
    `intercepted_at`
FROM {sr.external_catalog}.{paimon.database}.{QUALITY_ISSUE_TABLE};

-- ------------------------------------------------------------------------
-- 路径二 · 内表物化：入湖通道日级监控（高频、毫秒级，服务监控大屏）
-- 注意：这是 StarRocks 内表，不是 Paimon 表，不参与全湖 88 张表的口径与四段式命名
-- ------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingest_channel_daily (
    `dt`                 DATE         NOT NULL COMMENT "统计日期",
    `source_channel`     VARCHAR(64)  NOT NULL COMMENT "通道：Flink CDC / Kafka / OSS 合规上传",
    `target_table`       VARCHAR(128) NOT NULL COMMENT "目标 ODS 表",
    `ingest_cnt`         BIGINT       COMMENT "入湖行数",
    `reject_cnt`         BIGINT       COMMENT "门禁拒绝行数",
    `p0_reject_cnt`      BIGINT       COMMENT "P0 拒绝行数（含合规问题）",
    `compliance_reject_cnt` BIGINT    COMMENT "脱敏标记缺失等合规问题行数",
    `reject_rate`        DECIMAL(9,6) COMMENT "拒绝率",
    `bytes_externalized` BIGINT       COMMENT "外置到 OSS 的文件字节数（不进湖）",
    `updated_at`         DATETIME     COMMENT "刷新时间"
)
ENGINE = OLAP
PRIMARY KEY (`dt`, `source_channel`, `target_table`)
PARTITION BY RANGE (`dt`) ()
DISTRIBUTED BY HASH (`source_channel`) BUCKETS 2
PROPERTIES (
    "replication_num" = "1",
    "dynamic_partition.enable" = "true",
    "dynamic_partition.time_unit" = "DAY",
    "dynamic_partition.start" = "-90",
    "dynamic_partition.end" = "3",
    "dynamic_partition.prefix" = "p",
    "dynamic_partition.buckets" = "2"
);
-- 说明：dynamic_partition.start = -90 与 [a5] 第八章「连续 90 天无访问」降冷口径对齐，
-- 监控内表只保留最近 90 天，更早的数据回湖里查（Paimon 是单一事实源）。

-- 日级刷新（调度器每日跑一次；OSS 通道的拦截明细来自隔离表）
INSERT OVERWRITE ingest_channel_daily
SELECT
    CAST(m.`dt` AS DATE)                                   AS `dt`,
    m.`source_channel`                                     AS `source_channel`,
    m.`target_table`                                       AS `target_table`,
    m.`ingest_cnt`                                         AS `ingest_cnt`,
    m.`reject_cnt`                                         AS `reject_cnt`,
    m.`p0_reject_cnt`                                      AS `p0_reject_cnt`,
    m.`compliance_reject_cnt`                              AS `compliance_reject_cnt`,
    CASE WHEN m.`ingest_cnt` + m.`reject_cnt` = 0 THEN 0
         ELSE m.`reject_cnt` / (m.`ingest_cnt` + m.`reject_cnt`) END AS `reject_rate`,
    m.`bytes_externalized`                                 AS `bytes_externalized`,
    NOW()                                                  AS `updated_at`
FROM (
    SELECT
        DATE_FORMAT(`_ingest_time`, '%Y-%m-%d')  AS `dt`,
        'OSS 合规上传'                            AS `source_channel`,
        '{FILE_META_TABLE}'                       AS `target_table`,
        COUNT(*)                                  AS `ingest_cnt`,
        0                                         AS `reject_cnt`,
        0                                         AS `p0_reject_cnt`,
        0                                         AS `compliance_reject_cnt`,
        SUM(`file_size_bytes`)                    AS `bytes_externalized`
    FROM {sr.external_catalog}.{paimon.database}.{FILE_META_TABLE}
    GROUP BY DATE_FORMAT(`_ingest_time`, '%Y-%m-%d')
    UNION ALL
    SELECT
        `dt`,
        `source_channel`,
        `target_table`,
        0,
        COUNT(*),
        SUM(CASE WHEN `severity` = 'P0' THEN 1 ELSE 0 END),
        SUM(CASE WHEN `is_compliance_issue` THEN 1 ELSE 0 END),
        0
    FROM {sr.external_catalog}.{paimon.database}.{QUALITY_ISSUE_TABLE}
    GROUP BY `dt`, `source_channel`, `target_table`
) m;

-- ------------------------------------------------------------------------
-- 存储成本口径备注（治理归 lifecycle 子系统，这里只登记换算系数）
--   OSS 低频存储约 {COLD_TIER_COST_FACTOR}x 标准存储成本
--   OSS 归档存储约 {ARCHIVE_TIER_COST_FACTOR}x 标准存储成本
-- ------------------------------------------------------------------------
"""


# --------------------------------------------------------------------------- 写文件


def _project_root() -> Path:
    """仓库根：src/adas_lakehouse/ingest/sql.py → 上溯三层。"""
    return Path(__file__).resolve().parents[3]


def write_sql_files(root: str | Path | None = None) -> list[Path]:
    """生成并写出全部 SQL 文件，返回写出的路径列表。"""
    base = Path(root) if root else _project_root()
    flink_dir = base / "flink" / "sql"
    ddl_dir = base / "ddl"
    flink_dir.mkdir(parents=True, exist_ok=True)
    ddl_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []

    catalog_path = flink_dir / "ingest_00_catalog.sql"
    catalog_path.write_text(render_catalog_script(), encoding="utf-8")
    written.append(catalog_path)

    cdc_parts = [
        _header(
            "通道一 · Flink CDC：各平台 MySQL 业务库 → ODS",
            [
                "由 adas_lakehouse.ingest.sql 生成，请勿手工编辑。",
                "先执行 ingest_00_catalog.sql 建 Catalog 与会话参数。",
                "三阶段：全量快照 → 增量 binlog → 断点续传；业务库零侵入、不改代码。",
            ],
        )
    ]
    for binding in DEFAULT_CDC_BINDINGS:
        cdc_parts.extend(render_cdc_pipeline(binding))
    cdc_path = flink_dir / "ingest_cdc_mysql.sql"
    cdc_path.write_text("\n\n".join(cdc_parts) + "\n", encoding="utf-8")
    written.append(cdc_path)

    kafka_parts = [
        _header(
            "通道二 · Kafka：事件流 → ODS",
            [
                "由 adas_lakehouse.ingest.sql 生成，请勿手工编辑。",
                "产线埋点、训练指标、车端触发事件各自独立 Topic，保留回放能力。",
            ],
        )
    ]
    for kbinding in default_kafka_bindings():
        kafka_parts.extend(render_kafka_pipeline(kbinding))
    kafka_path = flink_dir / "ingest_kafka_event.sql"
    kafka_path.write_text("\n\n".join(kafka_parts) + "\n", encoding="utf-8")
    written.append(kafka_path)

    oss_parts = [
        _header(
            "通道三 · OSS 合规上传：采集文件元信息 → ODS",
            [
                "由 adas_lakehouse.ingest.sql 生成，请勿手工编辑。",
                "文件本体存 OSS（大文件外置），湖仓只存元信息与解析后的关键信息。",
                "合规靠架构边界：合规云与智驾云同一 VPC 内网流转、不对外暴露。",
            ],
        )
    ]
    oss_parts.extend(render_file_meta_pipeline())
    oss_path = flink_dir / "ingest_oss_file_meta.sql"
    oss_path.write_text("\n\n".join(oss_parts) + "\n", encoding="utf-8")
    written.append(oss_path)

    sr_path = ddl_dir / "starrocks_ingest.sql"
    sr_path.write_text(render_starrocks_ingest(), encoding="utf-8")
    written.append(sr_path)

    return written


def _main(argv: Sequence[str]) -> int:
    if "--write" in argv:
        for path in write_sql_files():
            print(f"written: {path}")
        return 0
    print(render_catalog_script())
    for binding in DEFAULT_CDC_BINDINGS:
        print("\n\n".join(render_cdc_pipeline(binding)))
    for kbinding in default_kafka_bindings():
        print("\n\n".join(render_kafka_pipeline(kbinding)))
    print("\n\n".join(render_file_meta_pipeline()))
    print(render_starrocks_ingest())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main(sys.argv[1:]))
