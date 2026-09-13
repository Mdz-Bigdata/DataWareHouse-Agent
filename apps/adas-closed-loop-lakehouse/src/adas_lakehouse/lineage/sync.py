"""双链路更新：链路二实时同步 + 链路三 T+1 对账 UPSERT 补齐。

[a13] 四的三条链路（表格逐字）：

===================== ============================================================== =========================
链路                   机制                                                            定位
===================== ============================================================== =========================
链路一（事实源）        run / 产物 / 数据集版本写入湖仓四张表，关系字段冗余落表               一切血缘的出发点
链路二（实时）          监听湖仓 binlog / 变更消息，MERGE 图库节点与关系                    低延迟；失败不阻塞湖仓写入
链路三（对账）          T+1 / 定时扫描湖仓增量（_ingest_time），按关系字段 UPSERT 补齐        兜底；幂等可重复执行
===================== ============================================================== =========================

红线①（[a13] 4.2）在本模块是硬约束：**实时链路失败不阻塞湖仓写入——湖仓永远是事实源**。
因此 :meth:`RealtimeLineageSync.handle` 永远不向调用方抛异常，失败只记账、投死信，
等 T+1 对账（:class:`ReconciliationJob`）把它补回来。

链路二的物理形态
    ⚠️ 原文只说「监听湖仓 binlog / 变更消息」，没指定中间件。本项目设计：
    Flink 读 Paimon 四张表的 changelog（flink/sql/lineage_realtime_sync.sql）
    写入 Kafka topic，本模块的消费者拿到消息后 MERGE 进 Neo4j。
    Neo4j 没有官方 Flink 连接器，中间过一层 Kafka 也让「图库挂了不影响湖仓」变成物理事实。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from ..config import KafkaConfig, settings
from .constants import (
    ID_BATCH_SIZE,
    REALTIME_MAX_RETRIES,
    RECONCILE_LAG_DAYS,
    RECONCILE_SCHEDULE_TEXT,
)
from .events import FACT_BY_TABLE, LineageFact, fact_from_row
from .graph import LineageGraphError, Neo4jGraphStore, Neo4jUnavailable
from .model import (
    CONSISTENCY_REDLINES,
    GraphMutation,
    GraphPropertyPolicy,
    LakehouseSource,
    LineageModelError,
)
from .resolver import LakehouseResolver, LakehouseUnavailable

__all__ = [
    "SyncOutcome",
    "DeadLetter",
    "FailureSink",
    "InMemoryFailureSink",
    "RealtimeLineageSync",
    "ReconcileReport",
    "ReconciliationJob",
    "LINEAGE_CHANGE_TOPIC",
    "reconcile_window",
]

_log = logging.getLogger(__name__)

#: 链路二的中间 topic。
#: ⚠️ 原文未明确，本项目设计：与 KafkaConfig 里既有的 trigger/production topic 同风格命名。
LINEAGE_CHANGE_TOPIC: str = "lineage.change.event"


# --------------------------------------------------------------------------- 死信


@dataclass(frozen=True, slots=True)
class DeadLetter:
    """一条同步失败的记录，供人工排查与重投。

    对账链路本身就能把它补回来（这才是「兜底」的含义），死信的价值是**可观测**：
    连续大量死信意味着图库出问题了，而不是等 T+1 才发现。
    """

    table: str
    node_id: str
    reason: str
    payload: Mapping[str, Any]
    failed_at: datetime = field(default_factory=datetime.now)

    def as_json(self) -> str:
        return json.dumps(
            {
                "table": self.table,
                "node_id": self.node_id,
                "reason": self.reason,
                "failed_at": self.failed_at.isoformat(),
                "payload": {k: str(v) for k, v in self.payload.items()},
            },
            ensure_ascii=False,
        )


#: 死信落地接口：拿到一条死信做什么（写 Kafka DLQ / 写文件 / 打日志）由部署方决定。
FailureSink = Callable[[DeadLetter], None]


class InMemoryFailureSink:
    """内存死信收集器。默认实现，兼作单测断言点。

    生产上换成写 Kafka DLQ 或写湖仓 ods_quality_issue 都只是换一个 callable。
    """

    def __init__(self, capacity: int = 10_000) -> None:
        self._items: list[DeadLetter] = []
        self._capacity = capacity

    def __call__(self, letter: DeadLetter) -> None:
        if len(self._items) >= self._capacity:
            self._items.pop(0)  # 环形丢最旧的，死信本身不该把进程撑爆
        self._items.append(letter)
        _log.error("血缘实时同步失败进死信: %s", letter.as_json())

    @property
    def items(self) -> tuple[DeadLetter, ...]:
        return tuple(self._items)

    def __len__(self) -> int:
        return len(self._items)


# --------------------------------------------------------------------------- 链路二


@dataclass(slots=True)
class SyncOutcome:
    """一次实时同步的结果。``ok=False`` 不代表调用方要做什么——红线①要求它别抛。"""

    ok: bool
    table: str
    node_id: str
    statements: int = 0
    skipped: tuple[str, ...] = ()
    error: str | None = None

    def __bool__(self) -> bool:
        return self.ok


class RealtimeLineageSync:
    """链路二：监听湖仓变更消息，MERGE 图库节点与关系。

    核心契约（红线①）::

        实时链路失败不阻塞湖仓写入——湖仓永远是事实源

    因此 :meth:`handle` 的所有失败路径都返回 ``SyncOutcome(ok=False)`` 并投死信，
    绝不抛异常。唯一会抛的是调用方自己传了非法参数（编程错误，不是运行时故障）。

    用法::

        sync = RealtimeLineageSync(Neo4jGraphStore())
        sync.handle("dwd_production_artifact_detail", row)        # 单条
        sync.consume_kafka(max_messages=1000)                      # 从 Kafka 持续消费
    """

    def __init__(
        self,
        graph: Neo4jGraphStore | None = None,
        *,
        policy: GraphPropertyPolicy = GraphPropertyPolicy.TRAVERSAL_KEYS,
        failure_sink: FailureSink | None = None,
        max_retries: int = REALTIME_MAX_RETRIES,
        kafka: KafkaConfig | None = None,
        topic: str = LINEAGE_CHANGE_TOPIC,
    ) -> None:
        self._graph = graph if graph is not None else Neo4jGraphStore()
        self._policy = policy
        # 注意用 is None 判断而不是 or：InMemoryFailureSink 定义了 __len__，
        # 空的死信收集器是 falsy，用 or 会把调用方注入的 sink 悄悄换掉。
        self._sink: FailureSink = (
            failure_sink if failure_sink is not None else InMemoryFailureSink()
        )
        self._max_retries = max_retries
        self._kafka = kafka if kafka is not None else settings().kafka
        self._topic = topic
        self._stats = {"ok": 0, "failed": 0, "skipped": 0}

    @property
    def stats(self) -> dict[str, int]:
        """累计计数，供监控上报。"""
        return dict(self._stats)

    @property
    def failure_sink(self) -> FailureSink:
        return self._sink

    def handle(self, table: str, row: Mapping[str, Any]) -> SyncOutcome:
        """处理一条湖仓变更（changelog 一行）。**永不抛异常。**

        :param table: 湖仓表名，见 :data:`events.FACT_BY_TABLE`
        :param row: 该表的一行
        :returns: :class:`SyncOutcome`；失败时 ``ok=False`` 且已投死信
        """
        node_id = ""
        try:
            fact: LineageFact = fact_from_row(table, row)
            node_id = fact.node_id
            mutation = fact.to_mutation(policy=self._policy, channel="realtime")
            written = self._graph.apply(mutation, max_retries=self._max_retries)
        except LineageModelError as exc:
            # 脏数据：重试没有意义，直接进死信等人工处置
            self._stats["failed"] += 1
            self._sink(DeadLetter(table, node_id, f"数据不合法: {exc}", row))
            return SyncOutcome(False, table, node_id, error=str(exc))
        except (Neo4jUnavailable, LineageGraphError) as exc:
            # 图库故障：红线① —— 吞掉，等 T+1 对账补齐
            self._stats["failed"] += 1
            self._sink(DeadLetter(table, node_id, f"图库不可用: {exc}", row))
            _log.warning(
                "图库写入失败，已吞掉不阻塞湖仓（%s），等 %s 对账补齐: %s",
                CONSISTENCY_REDLINES[0],
                RECONCILE_SCHEDULE_TEXT,
                exc,
            )
            return SyncOutcome(False, table, node_id, error=str(exc))
        except Exception as exc:  # 兜底：任何未预期异常都不许穿到湖仓写入侧
            self._stats["failed"] += 1
            self._sink(DeadLetter(table, node_id, f"未预期异常: {exc!r}", row))
            _log.exception("血缘实时同步未预期异常 table=%s", table)
            return SyncOutcome(False, table, node_id, error=repr(exc))
        self._stats["ok"] += 1
        self._stats["skipped"] += len(mutation.skipped)
        for note in mutation.skipped:
            _log.info("实时同步按红线跳过: %s", note)
        return SyncOutcome(
            True, table, node_id, statements=written, skipped=tuple(mutation.skipped)
        )

    def handle_message(self, message: Mapping[str, Any]) -> SyncOutcome:
        """处理一条 changelog 消息。

        消息约定（⚠️ 原文未明确，本项目设计，与 flink/sql/lineage_realtime_sync.sql 对齐）::

            {"table": "dwd_production_artifact_detail",
             "op": "+I" | "+U" | "-U" | "-D",
             "row": {...}}

        ``-U``（更新前镜像）直接丢弃——MERGE 用的是更新后的值；
        ``-D``（删除）同样不处理：血缘节点**永不删除**，
        「可演进」的前提是历史可查（[a13] 3.2 实践提醒：重刷绝不是删旧写新）。
        """
        op = str(message.get("op", "+I"))
        table = str(message.get("table", ""))
        row = message.get("row") or {}
        if op in ("-U", "-D"):
            self._stats["skipped"] += 1
            return SyncOutcome(
                True, table, "", skipped=(f"op={op} 不入图：血缘节点只增不删（[a13] 3.2）",)
            )
        if not isinstance(row, Mapping):
            self._stats["failed"] += 1
            self._sink(DeadLetter(table, "", "消息缺少 row 字段", dict(message)))
            return SyncOutcome(False, table, "", error="消息缺少 row 字段")
        return self.handle(table, row)

    def consume_kafka(
        self,
        *,
        max_messages: int | None = None,
        poll_timeout: float = 1.0,
        consumer: Any | None = None,
    ) -> dict[str, int]:
        """从 Kafka 消费 changelog 消息并同步进图库。

        ``kafka-python`` / ``confluent_kafka`` 均为延迟 import，两者都没装时抛
        :class:`RuntimeError`（模块本身仍可 import）。也可以直接注入 ``consumer``——
        任何支持迭代且产出带 ``.value`` 的对象即可。

        :param max_messages: 处理这么多条后返回；None 表示一直消费（生产常驻进程）
        :param poll_timeout: 轮询超时（秒），仅对内建消费者生效
        :param consumer: 自定义消费者（单测注入）
        :returns: 本次消费的统计
        """
        consumer = consumer if consumer is not None else self._build_consumer(poll_timeout)
        processed = 0
        for record in consumer:
            raw = getattr(record, "value", record)
            try:
                message = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
            except json.JSONDecodeError as exc:
                self._stats["failed"] += 1
                self._sink(DeadLetter("", "", f"消息不是合法 JSON: {exc}", {"raw": str(raw)[:512]}))
                continue
            if isinstance(message, Mapping):
                self.handle_message(message)
            else:
                self._stats["failed"] += 1
                self._sink(DeadLetter("", "", "消息不是 JSON 对象", {"raw": str(raw)[:512]}))
            processed += 1
            if max_messages is not None and processed >= max_messages:
                break
        return self.stats

    def _build_consumer(self, poll_timeout: float) -> Any:
        """延迟 import Kafka 客户端。两个常见库都试一遍，都没有才报错。"""
        cfg = self._kafka
        try:
            from kafka import KafkaConsumer  # type: ignore[import-not-found]
        except ImportError:
            pass
        else:
            return KafkaConsumer(
                self._topic,
                bootstrap_servers=cfg.bootstrap_servers.split(","),
                group_id=f"{cfg.group_id}-lineage",
                enable_auto_commit=True,
                auto_offset_reset="latest",
                consumer_timeout_ms=int(poll_timeout * 1000),
            )
        raise RuntimeError(
            "未安装 Kafka 客户端（pip install kafka-python），"
            "无法启动血缘实时同步链路；也可以给 consume_kafka 传自定义 consumer"
        )


# --------------------------------------------------------------------------- 链路三


def reconcile_window(
    as_of: date | datetime | None = None, *, lag_days: int = RECONCILE_LAG_DAYS
) -> tuple[datetime, datetime]:
    """算 T+1 对账的增量窗口 ``[start, end)``。

    出处 [a13] 四·链路三：「T+1 / 定时扫描湖仓增量（_ingest_time）」。
    T+1 即滞后 :data:`constants.RECONCILE_LAG_DAYS` = 1 天：今天跑的作业扫的是昨天入湖的数据。

    :param as_of: 作业运行日，默认今天
    :param lag_days: 滞后天数，默认 1（T+1）
    :returns: (窗口起, 窗口止)，左闭右开

    >>> reconcile_window(date(2024, 1, 16))
    (datetime.datetime(2024, 1, 15, 0, 0), datetime.datetime(2024, 1, 16, 0, 0))
    """
    if lag_days < 1:
        raise ValueError(f"对账滞后天数至少 1 天（T+1），收到 {lag_days}")
    if as_of is None:
        as_of = date.today()
    if isinstance(as_of, datetime):
        as_of = as_of.date()
    end = datetime.combine(as_of, time.min)
    start = end - timedelta(days=lag_days)
    return start, end


@dataclass(slots=True)
class ReconcileReport:
    """一次对账的结果报告。"""

    window_start: datetime
    window_end: datetime
    scanned_rows: int = 0
    facts: int = 0
    nodes_upserted: int = 0
    edges_upserted: int = 0
    statements: int = 0
    failures: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    per_table: dict[str, int] = field(default_factory=dict)
    sources: list[LakehouseSource] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def summary(self) -> str:
        return (
            f"[{RECONCILE_SCHEDULE_TEXT} 对账 {self.window_start:%Y-%m-%d}] "
            f"扫描 {self.scanned_rows} 行 / 事实 {self.facts} 条 / "
            f"节点 {self.nodes_upserted} / 边 {self.edges_upserted} / "
            f"语句 {self.statements} / 失败 {len(self.failures)} / 跳过 {len(self.skipped)}"
        )


class ReconciliationJob:
    """链路三：T+1 定时扫描湖仓增量，按关系字段 UPSERT 补齐图库。

    幂等是硬要求（[a13] 四·链路三「兜底；幂等可重复执行」）——本作业只用 MERGE，
    同一天重跑任意次，图的状态不变。

    与链路二共用 :func:`events.fact_from_row` 的转换，所以补齐的结果与实时链路
    写出来的完全一致，不会出现「两条链路写出两种图」。

    用法::

        job = ReconciliationJob(Neo4jGraphStore(), LakehouseResolver())
        report = job.run()                      # 补昨天的
        report = job.run(as_of=date(2024, 1, 16))
    """

    def __init__(
        self,
        graph: Neo4jGraphStore | None = None,
        resolver: LakehouseResolver | None = None,
        *,
        policy: GraphPropertyPolicy = GraphPropertyPolicy.TRAVERSAL_KEYS,
        tables: Sequence[str] | None = None,
        batch_size: int = ID_BATCH_SIZE,
    ) -> None:
        """
        :param tables: 要对账的表，默认 :data:`events.FACT_BY_TABLE` 的全部五张
            （原文四张事实表 + 本项目补的 Badcase 表）
        """
        self._graph = graph if graph is not None else Neo4jGraphStore()
        self._resolver = resolver if resolver is not None else LakehouseResolver()
        self._policy = policy
        self._tables = tuple(tables) if tables else tuple(FACT_BY_TABLE)
        self._batch_size = batch_size

    # ---- 扫描 SQL ----

    def scan_sql(self, table: str) -> str:
        """渲染某张表的增量扫描 SQL（按 ``_ingest_time`` 切窗口）。

        ``_ingest_time`` 是全湖统一的系统字段（见 domains.Layer.system_fields），
        [a13] 四·链路三点名用它切增量。
        """
        if table not in FACT_BY_TABLE:
            raise ValueError(f"{table!r} 不是血缘事实表；可选 {sorted(FACT_BY_TABLE)}")
        qualified = self._resolver.qualified(table)
        return (
            f"SELECT *\nFROM {qualified}\n"
            "WHERE `_ingest_time` >= %s AND `_ingest_time` < %s\n"
            "ORDER BY `_ingest_time`"
        )

    # ---- 执行 ----

    def run(
        self,
        as_of: date | datetime | None = None,
        *,
        lag_days: int = RECONCILE_LAG_DAYS,
        dry_run: bool = False,
    ) -> ReconcileReport:
        """跑一次 T+1 对账。

        :param as_of: 作业运行日，默认今天（扫的是昨天入湖的增量）
        :param dry_run: True 时只扫描与生成变更，不写图库——用于容量评估与演练
        :returns: :class:`ReconcileReport`；单表失败不会中断其他表（兜底链路自己要够皮实）
        """
        start, end = reconcile_window(as_of, lag_days=lag_days)
        report = ReconcileReport(window_start=start, window_end=end)
        for table in self._tables:
            try:
                rows = self._scan(table, start, end)
            except (LakehouseUnavailable, ValueError) as exc:
                report.failures.append(f"{table} 扫描失败: {exc}")
                _log.error("对账扫描失败 table=%s: %s", table, exc)
                continue
            report.scanned_rows += len(rows)
            report.per_table[table] = len(rows)
            merged = GraphMutation(channel="reconcile")
            for row in rows:
                try:
                    fact = fact_from_row(table, row)
                except LineageModelError as exc:
                    report.skipped.append(f"{table}: {exc}")
                    continue
                merged.extend(fact.to_mutation(policy=self._policy, channel="reconcile"))
                report.facts += 1
            report.nodes_upserted += len(merged.nodes)
            report.edges_upserted += len(merged.edges)
            report.skipped.extend(merged.skipped)
            report.sources.extend(merged.sources)
            if dry_run or merged.is_empty():
                continue
            try:
                report.statements += self._graph.apply(merged)
            except (Neo4jUnavailable, LineageGraphError) as exc:
                # 对账也失败说明图库确实挂了——记账，等下一次定时对账
                report.failures.append(f"{table} UPSERT 失败: {exc}")
                _log.error("对账写图失败 table=%s: %s", table, exc)
        _log.info(report.summary())
        return report

    def _scan(self, table: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
        sql = self.scan_sql(table)
        return self._resolver.fetch(sql, (start, end))

    # ---- 一致性体检 ----

    def audit_missing_edges(
        self,
        table: str,
        as_of: date | datetime | None = None,
        *,
        lag_days: int = RECONCILE_LAG_DAYS,
    ) -> list[str]:
        """体检：湖仓冗余关系字段里有、图库里却没有的边。

        这是「湖仓冗余血缘字段为对账源」（护栏二）的直接兑现——
        以湖仓为准，逐条核对图库是否缺边，返回缺失清单（人可读）。
        只读不写；要补齐直接跑 :meth:`run`。

        :returns: 缺失边的描述列表；空列表表示该表在该窗口内湖-图一致
        """
        start, end = reconcile_window(as_of, lag_days=lag_days)
        rows = self._scan(table, start, end)
        missing: list[str] = []
        for row in rows:
            try:
                fact = fact_from_row(table, row)
            except LineageModelError:
                continue
            for edge in fact.to_mutation(policy=self._policy, channel="audit").edges:
                cypher = (
                    f"MATCH (s:`{edge.spec.src.value}` {{id: $src}})"
                    f"-[r:`{edge.rel.value}`]->"
                    f"(d:`{edge.spec.dst.value}` {{id: $dst}})\n"
                    "RETURN count(r) AS cnt"
                )
                try:
                    res = self._graph.run_read(
                        cypher, {"src": edge.src_id, "dst": edge.dst_id}, limit_guard=1
                    )
                except Neo4jUnavailable as exc:
                    missing.append(f"图库不可用，体检中止: {exc}")
                    return missing
                if not res or int(res[0].get("cnt", 0)) == 0:
                    missing.append(
                        f"缺边 {edge}（对账源 {edge.spec.reconcile_table}.{edge.spec.reconcile_column}）"
                    )
        return missing
