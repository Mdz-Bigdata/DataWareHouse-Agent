"""三通道统一入湖：CDC / Kafka / OSS 合规上传的统一抽象。

来源：[a5] 第六章「数据进得来：三通道统一入湖」+ [a8] 第一章「第三条通道」。

| 通道 | 承接数据 | 入湖方式 |
|---|---|---|
| Flink CDC | 各平台 MySQL 业务库（产线 / 标注 / 训练等） | 读 binlog 实时同步 |
| Kafka | 事件流（产线埋点 / 训练指标 / 车端触发） | Flink 实时消费 |
| OSS 合规上传 | 采集大文件（图像 / 点云 / 传感器数据） | 文件本体存 OSS · 元信息经 Kafka 入湖 |

统一抽象的三条不变式（三条通道一视同仁）：
  1. **统一终点**：所有入湖数据统一经数据质量门禁校验后写入 Paimon ODS 层——
     「通道可以分，门禁不能分」；
  2. **统一盖章**：每一行都带 ``_ingest_time`` 与 ``_source_system``（见 rows.py）；
  3. **统一异常闭环**：被拒数据一律走五步异常闭环（拦截 → 隔离 → 告警 → 分流处置 → 复验）。

每条通道两种执行形态：
  · ``plan()``  —— 产出 Flink SQL 语句（生产形态，实时流作业，见 sql.py 与 flink/sql/）；
  · ``run()``   —— Python 侧逐条执行（补数 / 回放 / 演练 / 单测），走同一套门禁与盖章逻辑。
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, ClassVar

from ..config import settings
from ..domains import Layer
from .constants import (
    CDC_PHASE_COUNT,
    CDC_SYNC_LATENCY_TEXT,
    INGEST_CHANNEL_COUNT,
    LOCAL_REPLAY_BATCH_SIZE,
    ODS_WRITE_MAX_ATTEMPTS,
)
from .errors import ChannelError, MissingDependency, SinkError
from .gate import (
    AnomalyClosedLoop,
    CheckResult,
    CheckStatus,
    Decision,
    GateCheck,
    GateOutcome,
    OssComplianceGate,
    Severity,
    merge_outcomes,
)
from .oss import FILE_META_TABLE, FileMeta
from .rows import project_to_table, stamp_system_fields
from .sinks import InMemoryOdsSink, OdsSink

__all__ = [
    "ChannelKind",
    "CdcPhase",
    "IngestReport",
    "IngestChannel",
    "CdcBinding",
    "CdcChannel",
    "KafkaBinding",
    "KafkaChannel",
    "FileMetaBinding",
    "OssFileChannel",
    "CdcSourceConfig",
    "DEFAULT_CDC_BINDINGS",
    "default_kafka_bindings",
    "default_file_meta_binding",
    "build_default_channels",
    "unified_ingest",
]


class ChannelKind(Enum):
    """三条入湖通道。value 三元组取 [a8] 第一章 / [a5] 第六章表格原文。"""

    CDC = ("Flink CDC", "各平台 MySQL 业务库（产线 / 标注 / 训练等）", "读 binlog 实时同步")
    KAFKA = ("Kafka", "事件流（产线埋点 / 训练指标 / 车端触发）", "Flink 实时消费")
    OSS = (
        "OSS 合规上传",
        "采集大文件（图像 / 点云 / 传感器数据）",
        "文件本体存 OSS · 元信息经 Kafka 入湖",
    )

    @property
    def label(self) -> str:
        return self.value[0]

    @property
    def payload(self) -> str:
        return self.value[1]

    @property
    def mechanism(self) -> str:
        return self.value[2]


assert len(ChannelKind) == INGEST_CHANNEL_COUNT


class CdcPhase(str, Enum):
    """CDC 通道三阶段（[a5] 第六章：「按『全量快照 → 增量 binlog → 断点续传』三阶段运行」）。"""

    FULL_SNAPSHOT = "全量快照"
    INCREMENTAL_BINLOG = "增量 binlog"
    RESUME = "断点续传"


assert len(CdcPhase) == CDC_PHASE_COUNT


# --------------------------------------------------------------------------- 报告


@dataclass(slots=True)
class IngestReport:
    """一次入湖执行的结果。"""

    channel: ChannelKind
    target_table: str
    source_system: str
    total: int = 0
    accepted: int = 0
    warned: int = 0
    rejected: int = 0
    #: 幂等去重跳过的条数（同一主键在本通道实例里已经写过）
    duplicates: int = 0
    #: 落 ODS 实际尝试的次数（1 = 一次成功；> 1 说明发生了重试）
    write_attempts: int = 0
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: datetime | None = None
    rejections: list[GateOutcome] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def elapsed_seconds(self) -> float:
        return ((self.finished_at or datetime.now()) - self.started_at).total_seconds()

    def summary(self) -> str:
        return (
            f"[{self.channel.label}] → {self.target_table}："
            f"共 {self.total} 条，入湖 {self.accepted} 条（带标放行 {self.warned} 条），"
            f"拒绝 {self.rejected} 条，幂等去重 {self.duplicates} 条，"
            f"写入尝试 {self.write_attempts} 次，耗时 {self.elapsed_seconds:.3f}s"
        )


# --------------------------------------------------------------------------- 基类


class IngestChannel(ABC):
    """入湖通道基类：读 → 转换 → 门禁 → 盖章 → 投影 → 落 ODS → 异常闭环。

    子类必须实现 ``read()``（数据来源）与 ``plan()``（Flink SQL 生产形态），
    可选覆写 ``transform()``（源结构 → ODS 结构）、``gate_check()``（分源门禁规则）、
    ``idempotency_key()``（幂等主键）与 ``post_gate()``（把门禁派生结论写回行）。

    门禁在链路上的位置是固定的：**盖章与落表之前**。[a5] 第六章——
    「所有入湖数据统一经数据质量门禁校验后写入 Paimon ODS 层」，
    所以 ``run()`` 里不存在「先写进去再补检查」的分支。
    """

    kind: ClassVar[ChannelKind]

    def __init__(
        self,
        *,
        target_table: str,
        source_system: str,
        sink: OdsSink | None = None,
        closed_loop: AnomalyClosedLoop | None = None,
        layer: Layer = Layer.ODS,
        generic_gate: Callable[[Mapping[str, Any]], Sequence[CheckResult]] | None = None,
        max_write_attempts: int = ODS_WRITE_MAX_ATTEMPTS,
        dedupe: bool = True,
    ) -> None:
        """
        Args:
            target_table: 目标 ODS 表。
            source_system: ``_source_system`` 取值，ODS 层必填。
            sink: ODS 写出口，缺省内存 sink。
            closed_loop: 五步异常闭环执行器。
            layer: 目标层（决定盖哪组系统字段）。
            generic_gate: **三通道共用**的通用六维门禁钩子（由 ``adas_lakehouse.quality``
                子系统提供，见 ``ingest.quality_bridge.unified_gate_hook``）。挂上之后，
                通道专属规则与通用规则的结论按 ``gate.merge_outcomes`` 合并——
                「通道可以分，门禁不能分」。
            max_write_attempts: 落 ODS 的最大尝试次数（首次 + 重试）。
            dedupe: 是否按 ``idempotency_key()`` 做幂等去重。
        """
        if not source_system:
            raise ValueError("ODS 通道必须声明 source_system（_source_system 字段的取值来源）")
        if max_write_attempts < 1:
            raise ValueError("max_write_attempts 至少为 1（1 = 不重试）")
        self.target_table = target_table
        self.source_system = source_system
        self.sink: OdsSink = sink if sink is not None else InMemoryOdsSink()
        self.closed_loop = closed_loop if closed_loop is not None else AnomalyClosedLoop()
        self.layer = layer
        self.generic_gate = generic_gate
        self.max_write_attempts = max_write_attempts
        self.dedupe = dedupe
        #: 已写过的幂等键（本通道实例内），支撑「重放不重复入湖」
        self._written_keys: set[str] = set()

    # ---- 子类实现 ----

    @abstractmethod
    def read(self) -> Iterator[Mapping[str, Any]]:
        """从通道源读原始记录。"""

    @abstractmethod
    def plan(self) -> list[str]:
        """产出该通道的 Flink SQL 语句（生产形态）。"""

    def transform(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        """源结构 → ODS 结构。ODS 是「原样入湖」层，缺省只做浅拷贝。"""
        return dict(raw)

    def gate_check(self, row: Mapping[str, Any]) -> GateOutcome:
        """分源门禁规则。缺省全通过，由子类按通道特性覆写。

        [a5] 第八章：「六维检查框架 + 分源规则——CDC 通道查流程合规、
        Kafka 通道查时空合理、OSS 通道查物理完整与脱敏标记」。
        """
        return GateOutcome(Decision.ACCEPT, [], None, subject=str(row.get("data_id", "")))

    def post_gate(self, row: dict[str, Any], outcome: GateOutcome) -> None:  # noqa: B027
        """门禁跑完、盖章之前的回写钩子。缺省 no-op（**不是**抽象方法，多数通道不需要它）。

        用于把门禁过程中派生出来的结论写回行（例如 OSS 通道的可解码探针结论要落
        契约列 ``decodable_flag``）——这类值只有跑过门禁才知道，不能在 transform 阶段确定。
        """

    def idempotency_key(self, row: Mapping[str, Any]) -> str | None:
        """这一行的幂等主键；返回 None 表示本通道不做去重。

        口径与目标 Paimon 表的主键一致——主键表天然 upsert，通道侧再去一次重，
        是为了让「重放 / 补数 / 重试」这三件事在 Python 侧也不会把同一条数据
        写两遍（[a5] 第六章 Kafka 通道「消费失败可从上次位点重新消费，不丢事件」，
        不丢的前提是重复消费不产生重复数据）。
        """
        return None

    def _key_of(self, row: Mapping[str, Any], fields: Sequence[str]) -> str | None:
        """按字段元组拼幂等键；任一字段为空则返回 None（拼不出键就不去重）。"""
        if not fields:
            return None
        values: list[str] = []
        for name in fields:
            value = row.get(name)
            if value in (None, ""):
                return None
            values.append(str(value))
        return "|".join(values)

    # ---- 统一执行 ----

    def run(
        self,
        records: Iterable[Mapping[str, Any]] | None = None,
        *,
        limit: int | None = LOCAL_REPLAY_BATCH_SIZE,
        ingest_time: datetime | None = None,
    ) -> IngestReport:
        """本地执行一批入湖（补数 / 回放 / 演练 / 单测）。

        Args:
            records: 显式给定的记录；不给则调用 ``read()`` 从通道源读。
            limit: 单批最大条数，None 表示不限。
            ingest_time: 覆盖 ``_ingest_time``（回放历史数据时按事件时间盖章）。

        Returns:
            IngestReport：总数 / 入湖数 / 带标放行数 / 拒绝数 / 幂等去重数与被拒明细。
        """
        report = IngestReport(self.kind, self.target_table, self.source_system)
        source = records if records is not None else self.read()
        buffer: list[dict[str, Any]] = []
        batch_keys: list[str] = []

        for i, raw in enumerate(source):
            if limit is not None and i >= limit:
                break
            report.total += 1
            try:
                row = self.transform(raw)
            except Exception as exc:
                report.errors.append(f"转换失败: {exc}")
                report.rejected += 1
                continue

            # 门禁先于落表：通道专属规则 + （挂上时）三通道通用的六维门禁
            outcome = self.gate_check(row)
            if self.generic_gate is not None:
                outcome = merge_outcomes(
                    outcome,
                    GateOutcome(
                        Decision.ACCEPT,
                        list(self.generic_gate(row)),
                        None,
                        subject=outcome.subject,
                    ),
                )
            self.post_gate(row, outcome)

            if outcome.decision is Decision.REJECT:
                report.rejected += 1
                report.rejections.append(outcome)
                self.closed_loop.handle(
                    subject=outcome.subject or str(i),
                    outcome=outcome,
                    channel=self.kind.label,
                    target_table=self.target_table,
                    payload=row,
                )
                continue

            # 幂等：门禁之后、盖章之前去重。放在门禁之后是刻意的——
            # 重复数据也要先过门禁，否则「第一条脏数据被拦，重放的同一条被当成重复跳过」
            # 会让隔离表漏记；放在盖章之前则保证 _ingest_time 只对真正写入的行生成。
            key = self.idempotency_key(row) if self.dedupe else None
            if key is not None and (key in self._written_keys or key in batch_keys):
                report.duplicates += 1
                continue

            if outcome.decision is Decision.ACCEPT_WITH_WARNING:
                report.warned += 1
                # WARNING 带标放行：把门禁结论写进行内，下游可据此过滤
                row["_quality_warning"] = outcome.reason_text()

            stamped = stamp_system_fields(
                row,
                source_system=self.source_system,
                ingest_time=ingest_time,
                layer=self.layer,
            )
            buffer.append(project_to_table(self.target_table, stamped))
            if key is not None:
                batch_keys.append(key)
            report.accepted += 1

        if buffer:
            self._write_with_retry(buffer, report)
            self._written_keys.update(batch_keys)
        report.finished_at = datetime.now()
        return report

    def _write_with_retry(self, buffer: Sequence[Mapping[str, Any]], report: IngestReport) -> None:
        """落 ODS，失败按 ``max_write_attempts`` 重试；耗尽仍失败则抛 ``SinkError``。

        重试是安全的：目标表是 Paimon 主键表（upsert 语义），同一批重复写入不会
        产生重复行；批内也已按 ``idempotency_key()`` 去过重。最后一次仍失败时
        抛出的是 ``SinkError``，由调用方决定是整批进隔离还是等下一轮补数——
        ⚠️ 原文未明确，本项目设计：原文只给了「重传 / 幂等重放 / 断点续传」的语义，
        没有规定重试次数与退避策略，本实现不做 sleep 退避（入湖链路上的阻塞式
        sleep 会把反压传导给 Kafka 消费位点），退避交给外部调度。
        """
        last_error: Exception | None = None
        for attempt in range(1, self.max_write_attempts + 1):
            report.write_attempts = attempt
            try:
                self.sink.write(self.target_table, list(buffer))
                return
            except Exception as exc:  # noqa: BLE001 - sink 实现五花八门，统一转 SinkError
                last_error = exc
                report.errors.append(
                    f"落 {self.target_table} 第 {attempt}/{self.max_write_attempts} 次尝试失败: {exc}"
                )
        report.finished_at = datetime.now()
        raise SinkError(
            f"落 {self.target_table} 连续 {self.max_write_attempts} 次写入失败，"
            f"{len(buffer)} 行未入湖：{last_error}"
        ) from last_error


# --------------------------------------------------------------------------- 通道一：CDC


@dataclass(frozen=True, slots=True)
class CdcSourceConfig:
    """MySQL CDC 源连接信息。

    ⚠️ 原文未明确，本项目设计：共享契约 ``config.settings()`` 只覆盖
    minio / paimon / flink / starrocks / neo4j / kafka 六段，没有 MySQL 段，
    而契约是只读的。因此 CDC 源的连接信息从环境变量读取，变量名与 config.py
    的风格保持一致（全大写下划线）。
    """

    hostname: str = field(default_factory=lambda: os.environ.get("CDC_MYSQL_HOSTNAME", "localhost"))
    #: 宿主机发布端口（186xx 独占号段，避开同仓平台）；容器内仍是 MySQL 原生 3306，
    #: 在 compose 网络里跑请用 CDC_MYSQL_HOSTNAME=mysql / CDC_MYSQL_PORT=3306 覆盖
    port: int = field(default_factory=lambda: int(os.environ.get("CDC_MYSQL_PORT", "18606")))
    username: str = field(default_factory=lambda: os.environ.get("CDC_MYSQL_USERNAME", "adas"))
    password: str = field(default_factory=lambda: os.environ.get("CDC_MYSQL_PASSWORD", ""))
    #: Flink CDC 要求每个 source 有唯一 server-id（或区间）
    server_id: str = field(
        default_factory=lambda: os.environ.get("CDC_MYSQL_SERVER_ID", "5400-5404")
    )


@dataclass(frozen=True, slots=True)
class CdcBinding:
    """一张业务库表 → 一张 ODS 表的 CDC 绑定。"""

    database: str
    table: str
    target_table: str
    source_system: str
    #: 业务主键，用于 Paimon upsert 与流程合规检查
    primary_key: tuple[str, ...] = ()
    #: 该表的状态字段与合法取值（流程合规检查用）。空表示不检查。
    status_field: str = ""
    status_values: tuple[str, ...] = ()
    #: 变更版本字段（如 update_time / version）。与主键一起构成幂等键；
    #: 空表示本表不在 Python 侧去重，幂等完全交给 Paimon 主键表的 upsert。
    #: 见 ``CdcChannel.idempotency_key``。
    version_field: str = ""


class CdcChannel(IngestChannel):
    """通道一 · Flink CDC：读 binlog 实时同步各平台 MySQL 业务库。

    工程要点（[a5] 第六章）：
      · 三阶段运行：全量快照 → 增量 binlog → 断点续传；
      · 业务库零侵入、不改代码；
      · 同步延迟秒级。

    断点续传由 Flink checkpoint 承载（``execution.checkpointing.interval``
    取自 ``settings().flink.checkpoint_interval_ms``），不需要在 SQL 里额外表达；
    ``scan.startup.mode='initial'`` 即「先全量快照再转增量 binlog」。
    """

    kind: ClassVar[ChannelKind] = ChannelKind.CDC

    def __init__(
        self,
        binding: CdcBinding,
        *,
        source: CdcSourceConfig | None = None,
        sink: OdsSink | None = None,
        closed_loop: AnomalyClosedLoop | None = None,
        generic_gate: Callable[[Mapping[str, Any]], Sequence[CheckResult]] | None = None,
    ) -> None:
        super().__init__(
            target_table=binding.target_table,
            source_system=binding.source_system,
            sink=sink,
            closed_loop=closed_loop,
            generic_gate=generic_gate,
        )
        self.binding = binding
        self.source = source or CdcSourceConfig()
        self.phase = CdcPhase.FULL_SNAPSHOT
        self.sync_latency_text = CDC_SYNC_LATENCY_TEXT

    def idempotency_key(self, row: Mapping[str, Any]) -> str | None:
        """幂等键 = 业务主键 + 变更版本字段；**没有版本字段就不去重**。

        这里刻意不只用主键：CDC 读的是 binlog，同一主键本来就会有多次合法变更
        （INSERT 之后跟着若干 UPDATE），只按主键去重会把后续更新当成重复丢掉——
        那不是幂等，是丢数据。断点续传重放同一段 binlog 时的去重，靠的是
        「主键 + 版本」这对组合：同一版本重放多少次都只写一行，新版本照常写入。

        没有声明 ``version_field`` 时返回 None，把幂等完全交给 Paimon 主键表的
        upsert 与 Flink checkpoint——宁可不去重，也不能去错重。
        """
        if not self.binding.version_field:
            return None
        return self._key_of(row, (*self.binding.primary_key, self.binding.version_field))

    # ---- 分源门禁：CDC 通道查「流程合规」 ----

    _PROCESS_CHECK = GateCheck(
        "cdc_process_compliance",
        "流程合规",
        "CDC 通道分源规则：主键非空、变更类型合法、状态取值在枚举内",
        Severity.P1,
        "唯一性",
    )
    #: binlog 变更类型。Flink CDC 的 RowKind 四态。
    _VALID_OPS = ("+I", "-U", "+U", "-D", "c", "u", "d", "r")

    def gate_check(self, row: Mapping[str, Any]) -> GateOutcome:
        """流程合规检查。

        ⚠️ 原文未明确，本项目设计：原文只给出「CDC 通道查流程合规」七个字，
        没有规则明细。本项目把它落成三条最小规则：业务主键非空（唯一性维度）、
        变更类型合法、状态字段取值在声明枚举内——三条都是「流程」层面的自洽性，
        不涉及业务语义。
        """
        problems: list[str] = []
        for pk in self.binding.primary_key:
            if row.get(pk) in (None, ""):
                problems.append(f"主键字段 {pk} 为空，upsert 无法定位行")
        op = row.get("_op") or row.get("op")
        if op is not None and str(op) not in self._VALID_OPS:
            problems.append(f"非法的变更类型 {op!r}，合法值 {self._VALID_OPS}")
        if self.binding.status_field and self.binding.status_values:
            value = row.get(self.binding.status_field)
            if value is not None and str(value) not in self.binding.status_values:
                problems.append(
                    f"{self.binding.status_field}={value!r} 不在合法状态枚举 "
                    f"{self.binding.status_values} 内"
                )
        subject = str(row.get(self.binding.primary_key[0], "")) if self.binding.primary_key else ""
        if problems:
            return GateOutcome(
                Decision.REJECT,
                [CheckResult(self._PROCESS_CHECK, CheckStatus.FAIL, tuple(problems))],
                Severity.P1,
                subject=subject,
            )
        return GateOutcome(
            Decision.ACCEPT,
            [CheckResult(self._PROCESS_CHECK, CheckStatus.PASS)],
            None,
            subject=subject,
        )

    def transform(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        """剥掉 binlog 元字段，只留业务列——ODS 原样入湖。"""
        return {k: v for k, v in raw.items() if not k.startswith("_op") and k != "op"}

    def read(self) -> Iterator[Mapping[str, Any]]:
        """Python 侧不直连 binlog。

        Raises:
            ChannelError: 始终抛出。CDC 是 Flink 作业的职责（见 ``plan()``），
                Python 侧只在补数场景用 ``run(records=...)`` 显式喂数据。
        """
        raise ChannelError(
            "CDC 通道不在 Python 侧读 binlog：生产形态请用 plan() 产出的 Flink SQL 作业；"
            "补数 / 回放请用 run(records=[...]) 显式传入记录"
        )

    def plan(self) -> list[str]:
        """产出 CDC 源表 DDL + INSERT INTO ODS 两条语句。"""
        from .sql import render_cdc_pipeline  # 局部 import 避免与 sql.py 循环依赖

        return render_cdc_pipeline(self.binding, self.source)


# --------------------------------------------------------------------------- 通道二：Kafka


@dataclass(frozen=True, slots=True)
class KafkaBinding:
    """一个 Topic → 一张 ODS 表的绑定。

    [a5] 第六章：「产线埋点、训练指标、车端触发事件各自独立 Topic，
    消费失败可从上次位点重新消费，不丢事件」。
    """

    topic: str
    target_table: str
    source_system: str
    #: 事件时间字段，时空合理性检查用
    event_time_field: str = "event_time"
    #: 经纬度字段（车端触发事件有，产线埋点没有）
    lat_field: str = ""
    lon_field: str = ""
    #: 目标表的分区字段（如 trigger_type / event_type），非空校验用
    partition_field: str = ""
    group_id: str = ""
    #: 事件唯一键，幂等去重用。Kafka 是 at-least-once，同一事件可能被投递多次；
    #: 「消费失败可从上次位点重新消费，不丢事件」的前提是重复消费不产生重复数据。
    key_fields: tuple[str, ...] = ("event_id",)


class KafkaChannel(IngestChannel):
    """通道二 · Kafka：Flink 实时消费事件流落表，保留回放能力。

    回放能力的工程表达（[a5] 第六章）：
      · 独立 Topic + 独立 group，互不干扰；
      · ``scan.startup.mode='group-offsets'``——消费失败可从上次位点重新消费，不丢事件；
      · 需要重放历史时切 ``specific-offsets`` / ``timestamp``（见 sql.py 生成的注释）。
    """

    kind: ClassVar[ChannelKind] = ChannelKind.KAFKA

    #: ⚠️ 原文未明确，本项目设计：原文只说「Kafka 通道查时空合理」，未给容忍窗口。
    #: 这里给出可覆盖的默认值——未来时间 300 秒（覆盖车端与云端的常见时钟漂移），
    #: 滞后 30 天（覆盖车端离线缓存后补传的场景）。两者都可在构造时按业务调整。
    DEFAULT_FUTURE_TOLERANCE_SEC = 300
    DEFAULT_LAG_TOLERANCE_DAYS = 30

    _SPACETIME_CHECK = GateCheck(
        "kafka_spacetime_sane",
        "时空合理",
        "Kafka 通道分源规则：事件时间在容忍窗口内、经纬度在合法值域内、分区字段非空",
        Severity.P1,
        "及时性",
    )

    def __init__(
        self,
        binding: KafkaBinding,
        *,
        sink: OdsSink | None = None,
        closed_loop: AnomalyClosedLoop | None = None,
        future_tolerance_sec: int | None = None,
        lag_tolerance_days: int | None = None,
        generic_gate: Callable[[Mapping[str, Any]], Sequence[CheckResult]] | None = None,
    ) -> None:
        super().__init__(
            target_table=binding.target_table,
            source_system=binding.source_system,
            sink=sink,
            closed_loop=closed_loop,
            generic_gate=generic_gate,
        )
        self.binding = binding
        self.future_tolerance = timedelta(
            seconds=future_tolerance_sec
            if future_tolerance_sec is not None
            else self.DEFAULT_FUTURE_TOLERANCE_SEC
        )
        self.lag_tolerance = timedelta(
            days=lag_tolerance_days
            if lag_tolerance_days is not None
            else self.DEFAULT_LAG_TOLERANCE_DAYS
        )

    # ---- 分源门禁：Kafka 通道查「时空合理」 ----

    @staticmethod
    def _as_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            # 毫秒时间戳 > 1e11，秒级时间戳在 1e9 量级
            seconds = value / 1000 if value > 1e11 else value
            try:
                return datetime.fromtimestamp(seconds)
            except (OverflowError, OSError, ValueError):
                return None
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return None
        return None

    def gate_check(self, row: Mapping[str, Any]) -> GateOutcome:
        """时空合理性检查：事件时间窗口 + 经纬度值域 + 分区字段非空。"""
        problems: list[str] = []
        now = datetime.now()

        raw_ts = row.get(self.binding.event_time_field)
        if raw_ts in (None, ""):
            problems.append(f"事件时间字段 {self.binding.event_time_field} 缺失")
        else:
            moment = self._as_datetime(raw_ts)
            if moment is None:
                problems.append(f"事件时间 {raw_ts!r} 无法解析")
            elif moment > now + self.future_tolerance:
                problems.append(
                    f"事件时间 {moment.isoformat()} 超出未来容忍窗口 "
                    f"{int(self.future_tolerance.total_seconds())} 秒（车端时钟异常）"
                )
            elif moment < now - self.lag_tolerance:
                problems.append(
                    f"事件时间 {moment.isoformat()} 滞后超过 {self.lag_tolerance.days} 天，"
                    "疑似重复投递历史事件"
                )

        if self.binding.lat_field and self.binding.lon_field:
            lat, lon = row.get(self.binding.lat_field), row.get(self.binding.lon_field)
            if lat is not None and not -90 <= float(lat) <= 90:
                problems.append(f"纬度 {lat} 超出 [-90, 90]")
            if lon is not None and not -180 <= float(lon) <= 180:
                problems.append(f"经度 {lon} 超出 [-180, 180]")

        if self.binding.partition_field and row.get(self.binding.partition_field) in (None, ""):
            problems.append(
                f"分区字段 {self.binding.partition_field} 为空——"
                "Paimon 分区表主键必须包含分区字段，空值会落进 __DEFAULT_PARTITION__"
            )

        subject = str(row.get("data_id") or row.get("event_id") or "")
        if problems:
            return GateOutcome(
                Decision.REJECT,
                [CheckResult(self._SPACETIME_CHECK, CheckStatus.FAIL, tuple(problems))],
                Severity.P1,
                subject=subject,
            )
        return GateOutcome(
            Decision.ACCEPT,
            [CheckResult(self._SPACETIME_CHECK, CheckStatus.PASS)],
            None,
            subject=subject,
        )

    def idempotency_key(self, row: Mapping[str, Any]) -> str | None:
        """事件唯一键即幂等键（缺省 ``event_id``）。

        [a5] 第六章：「消费失败可从上次位点重新消费，不丢事件」——从上次位点重放
        必然带来重复投递，不丢事件的同时也不能多写事件，这两件事一起才叫「不丢不重」。
        """
        return self._key_of(row, self.binding.key_fields)

    def read(self) -> Iterator[Mapping[str, Any]]:
        """从 Kafka 消费 JSON 事件（补数 / 回放用）。

        kafka-python 延迟 import：裸环境下 import 本模块不应失败。

        Raises:
            MissingDependency: 未安装 kafka-python。
            ChannelError: 消费失败。
        """
        try:
            from kafka import KafkaConsumer  # type: ignore[import-not-found]
        except ImportError as exc:
            raise MissingDependency("kafka-python", f"消费 Topic {self.binding.topic}") from exc

        import json

        cfg = settings().kafka
        try:
            consumer = KafkaConsumer(
                self.binding.topic,
                bootstrap_servers=cfg.bootstrap_servers.split(","),
                group_id=self.binding.group_id or cfg.group_id,
                # 消费失败可从上次位点重新消费，不丢事件
                enable_auto_commit=False,
                auto_offset_reset="earliest",
                consumer_timeout_ms=10_000,
                value_deserializer=lambda b: json.loads(b.decode("utf-8")),
            )
        except Exception as exc:  # pragma: no cover - 取决于环境
            raise ChannelError(f"连接 Kafka 失败: {exc}") from exc

        try:
            for message in consumer:
                yield message.value
        finally:
            consumer.close()

    def plan(self) -> list[str]:
        from .sql import render_kafka_pipeline

        return render_kafka_pipeline(self.binding)


# --------------------------------------------------------------------------- 通道三：OSS


@dataclass(frozen=True, slots=True)
class FileMetaBinding:
    """OSS 合规上传通道的元信息 Topic → ``ods_data_file_meta`` 绑定。

    ⚠️ 原文未明确，本项目设计：``config.settings().kafka`` 只登记了
    trigger_topic 与 production_topic，没有文件元信息 Topic。本项目从环境变量
    ``KAFKA_FILE_META_TOPIC`` 读取，缺省 ``collect.file.meta``。
    """

    topic: str = field(
        default_factory=lambda: os.environ.get("KAFKA_FILE_META_TOPIC", "collect.file.meta")
    )
    target_table: str = FILE_META_TABLE
    #: 与共享契约 ods_data_file_meta.source_system 保持一致
    source_system: str = "文件管理系统"
    group_id: str = ""


class OssFileChannel(IngestChannel):
    """通道三 · OSS 合规上传：文件本体存 OSS，元信息经 Kafka 实时写入 ODS。

    [a8] 第五章三条入湖原则：
      1. 文件本体存 OSS——湖仓保存的是文件元信息和解析后的关键信息；
      2. 元信息经 Kafka 实时写入 ``ods_data_file_meta``；
      3. 元信息写入即可用——下游 DWD 加工与查询立即可以引用，
         文件本体通过 ``file_path`` 按需读取。

    门禁用 OSS 通道专属的四项检查（见 gate.OssComplianceGate），其中脱敏标记
    完整性与 data_id 格式合法是 P0——「门禁在这里承担了合规的最后核验职责」。
    """

    kind: ClassVar[ChannelKind] = ChannelKind.OSS

    def __init__(
        self,
        binding: FileMetaBinding | None = None,
        *,
        gate: OssComplianceGate | None = None,
        sink: OdsSink | None = None,
        closed_loop: AnomalyClosedLoop | None = None,
        generic_gate: Callable[[Mapping[str, Any]], Sequence[CheckResult]] | None = None,
    ) -> None:
        binding = binding or FileMetaBinding()
        super().__init__(
            target_table=binding.target_table,
            source_system=binding.source_system,
            sink=sink,
            closed_loop=closed_loop,
            generic_gate=generic_gate,
        )
        self.binding = binding
        self.gate = gate if gate is not None else OssComplianceGate()
        self._metas: dict[str, FileMeta] = {}

    #: 幂等键字段：与共享契约 ods_data_file_meta 的主键 (file_id, file_type) 同口径
    KEY_FIELDS: ClassVar[tuple[str, str]] = ("file_id", "file_type")

    def transform(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        """Kafka 元信息消息 → 入湖行，并缓存 FileMeta 供门禁使用。"""
        meta = raw if isinstance(raw, FileMeta) else FileMeta.from_kafka_message(raw)
        row = meta.to_row()
        self._metas[meta.file_id] = meta
        return row

    def gate_check(self, row: Mapping[str, Any]) -> GateOutcome:
        """跑 OSS 通道四项专属检查。"""
        meta = self._metas.get(str(row.get("file_id", "")))
        if meta is None:  # pragma: no cover - transform 必定先于 gate_check 执行
            meta = FileMeta.from_kafka_message(row)
        return self.gate.check(meta, row=row)

    def post_gate(self, row: dict[str, Any], outcome: GateOutcome) -> None:
        """把可解码探针的结论回写进行，落契约列 ``decodable_flag``。

        探针结果只有跑完 P1 检查才知道，而 ``transform()`` 早于门禁执行——
        不在这里回写，湖表里这一列永远是 NULL。
        """
        meta = self._metas.get(str(row.get("file_id", "")))
        if meta is not None:
            row["decodable_flag"] = meta.decodable_flag

    def idempotency_key(self, row: Mapping[str, Any]) -> str | None:
        """幂等键 = (file_id, file_type)，与目标表主键一致。

        一个文件的元信息被重复投递（合规分发侧重试、Kafka 重放、补数脚本重跑）
        时只写一行；[a8] 的「一个采集任务数百个文件」在重跑时不会翻倍。
        """
        return self._key_of(row, self.KEY_FIELDS)

    def read(self) -> Iterator[Mapping[str, Any]]:
        """消费文件元信息 Topic（复用 Kafka 通道的消费实现）。"""
        proxy = KafkaChannel(
            KafkaBinding(
                topic=self.binding.topic,
                target_table=self.binding.target_table,
                source_system=self.binding.source_system,
                group_id=self.binding.group_id,
            ),
            sink=self.sink,
        )
        yield from proxy.read()

    def ingest_meta(self, meta: FileMeta, *, ingest_time: datetime | None = None) -> IngestReport:
        """单条元信息入湖（合规链路第 ⑤ 步的直接入口）。"""
        return self.run([meta], limit=1, ingest_time=ingest_time)

    def plan(self) -> list[str]:
        from .sql import render_file_meta_pipeline

        return render_file_meta_pipeline(self.binding)


# --------------------------------------------------------------------------- 默认绑定


#: CDC 通道默认绑定：目前只覆盖已在共享契约里登记的采集域 ODS 表。
#: 其余业务域的 ODS 表由各自子系统登记后按同样结构追加即可。
DEFAULT_CDC_BINDINGS: tuple[CdcBinding, ...] = (
    CdcBinding(
        database="collect_platform",
        table="collect_task",
        target_table="ods_collect_task",
        source_system="采集管理系统",
        primary_key=("collect_task_id",),
        status_field="task_status",
        # ⚠️ 原文未明确，本项目设计：原文未给采集任务的状态枚举
        status_values=("created", "running", "finished", "failed", "canceled"),
    ),
    CdcBinding(
        database="vehicle_platform",
        table="vehicle_info",
        target_table="ods_vehicle_info",
        source_system="车辆管理系统",
        primary_key=("vehicle_code",),
    ),
    CdcBinding(
        database="config_platform",
        table="sensor_config",
        target_table="ods_sensor_config",
        source_system="配置管理系统",
        primary_key=("vehicle_code", "sensor_id"),
    ),
)


def default_kafka_bindings() -> tuple[KafkaBinding, ...]:
    """Kafka 通道默认绑定：Topic 取自 ``settings().kafka``。

    写成函数而不是模块常量，是为了不在 import 期就固化环境变量——
    测试里改完 env 调 ``settings.cache_clear()`` 后重新调用即可生效。

    两张目标表都是全湖 6 张分区表之一（分别按 trigger_type / event_type 分区），
    因此 ``partition_field`` 必须非空，见 ``KafkaChannel.gate_check``。
    """
    cfg = settings().kafka
    return (
        KafkaBinding(
            topic=cfg.trigger_topic,
            target_table="ods_vehicle_trigger_event",
            source_system="车云平台",
            event_time_field="trigger_time",
            lat_field="gps_lat",
            lon_field="gps_lon",
            partition_field="trigger_type",
        ),
        KafkaBinding(
            topic=cfg.production_topic,
            target_table="ods_production_kafka_event",
            source_system="产线埋点",
            event_time_field="event_time",
            partition_field="event_type",
        ),
    )


def default_file_meta_binding() -> FileMetaBinding:
    """OSS 通道默认绑定（全湖只有一张文件元信息表 ods_data_file_meta）。"""
    return FileMetaBinding()


def build_default_channels(
    *,
    sink: OdsSink | None = None,
    closed_loop: AnomalyClosedLoop | None = None,
    gate: OssComplianceGate | None = None,
    generic_gate: Callable[[Mapping[str, Any]], Sequence[CheckResult]] | None = None,
) -> list[IngestChannel]:
    """按默认绑定构造三条通道的全部 channel 实例，共享同一个 sink 与异常闭环。

    Args:
        sink: 三条通道共用的 ODS 写出口。
        closed_loop: 三条通道共用的五步异常闭环——被拒数据不分通道，一律进同一个隔离表。
        gate: OSS 通道的四项专属门禁。
        generic_gate: **三条通道共用**的通用六维门禁钩子。三条通道拿到的是同一个
            可调用对象，这就是「通道可以分，门禁不能分」在装配层面的落点；
            要接 ``adas_lakehouse.quality`` 的规则集，用
            ``ingest.quality_bridge.unified_gate_hook(kind)`` 构造。
    """
    shared_sink = sink if sink is not None else InMemoryOdsSink()
    loop = closed_loop if closed_loop is not None else AnomalyClosedLoop()
    channels: list[IngestChannel] = [
        CdcChannel(b, sink=shared_sink, closed_loop=loop, generic_gate=generic_gate)
        for b in DEFAULT_CDC_BINDINGS
    ]
    channels.extend(
        KafkaChannel(b, sink=shared_sink, closed_loop=loop, generic_gate=generic_gate)
        for b in default_kafka_bindings()
    )
    channels.append(
        OssFileChannel(
            default_file_meta_binding(),
            gate=gate,
            sink=shared_sink,
            closed_loop=loop,
            generic_gate=generic_gate,
        )
    )
    return channels


def unified_ingest(
    channels: Sequence[IngestChannel],
    feeds: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
    *,
    on_report: Callable[[IngestReport], None] | None = None,
) -> list[IngestReport]:
    """统一入湖入口：逐条通道执行，返回各自的报告。

    Args:
        channels: 通道实例列表（通常来自 ``build_default_channels()``）。
        feeds: ``{target_table: 记录序列}``；给定则用它替代通道的 ``read()``，
            用于补数与演练。未在 feeds 里出现的通道会跳过（不主动连外部系统）。
        on_report: 每条通道执行完的回调，便于接监控。

    Returns:
        每条通道一份 IngestReport。
    """
    reports: list[IngestReport] = []
    for channel in channels:
        records = None if feeds is None else feeds.get(channel.target_table)
        if feeds is not None and records is None:
            continue
        report = channel.run(records)
        reports.append(report)
        if on_report is not None:
            on_report(report)
    return reports
