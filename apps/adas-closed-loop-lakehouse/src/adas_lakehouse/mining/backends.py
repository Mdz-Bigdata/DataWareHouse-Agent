"""外部依赖的适配层：SQL 执行、统一标签服务、补抽帧派发。

设计约束有两条，都是硬的：

1. **导入本模块永远不能炸。** flink/paimon/starrocks/kafka 的客户端库一律延迟导入，
   驱动没装时只有「构造那个具体实现」才会报错，``import`` 本身不受影响。
2. **连接信息一律从** :func:`adas_lakehouse.config.settings` **取**，不在本模块写死。

职责边界来自原文（[S3-04] 三、结果双写）：

    命中结果一律经统一标签服务写入标签表（完成字典映射与去重），
    同时写 dwd_mining_result_detail 供回补闭环消费。

以及（[S3-04] 二）：

    所有命中统一经标签服务打标，携带 rule_id 血缘——规则挖掘的产出自动继承
    字典映射、去重与审核体系，不需要规则引擎自建一套标签写入逻辑。

所以本模块里 **没有任何直接写标签表的代码**：标签那一路只有 :class:`TagService` 接口。
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from ..config import settings
from .constants import (
    EVENT_WINDOW_AFTER_SEC,
    EVENT_WINDOW_BEFORE_SEC,
    HIGH_VALUE_SOURCES,
    RULE_JOB_PROGRESS_API_PATH,
    RULE_JOBS_API_PATH,
)
from .tables import DWD_MINING_RESULT_DETAIL, TableRef, qualified

logger = logging.getLogger(__name__)

__all__ = [
    "BackendError",
    "FRAME_SUPPLEMENT_PENDING",
    "SqlBackend",
    "DryRunBackend",
    "StarRocksBackend",
    "FlinkSqlGatewayBackend",
    "TAG_SOURCE_RULE",
    "RULE_TAG_CONFIDENCE",
    "TagWriteRequest",
    "TagService",
    "InMemoryTagService",
    "HttpTagService",
    "BackfillRequest",
    "FrameBackfillDispatcher",
    "LakehouseBackfillDispatcher",
    "KafkaBackfillDispatcher",
    "ResultSink",
    "InMemoryResultSink",
    "SqlResultSink",
    "rule_job_endpoint",
    "rule_job_progress_endpoint",
]


class BackendError(RuntimeError):
    """外部依赖调用失败。执行器捕获它并把任务标成 failed，而不是整条链路崩掉。"""


# --------------------------------------------------------------------------- SQL 执行


@runtime_checkable
class SqlBackend(Protocol):
    """SQL 执行后端。批走 Spark/StarRocks，流走 Flink SQL Gateway。"""

    name: str

    def query(self, sql: str) -> list[dict[str, Any]]:
        """执行查询并取回全部结果行。"""
        ...

    def execute(self, sql: str) -> int:
        """执行 INSERT / 提交作业，返回受影响行数（流作业返回 -1 表示已提交、行数未知）。"""
        ...


@dataclass(slots=True)
class DryRunBackend:
    """空跑后端：只记录 SQL，不连任何外部系统。

    这不是玩具——它承担两件正事：一是单测里断言编译产物；二是生产上线前的
    「规则体检」：把当天要跑的全部规则编译一遍、收集 SQL 与告警，确认没有坏规则会
    白白占掉 4 小时批处理窗口（constants.BATCH_SLA_HOURS）。
    """

    name: str = "dry-run"
    rows: list[dict[str, Any]] = field(default_factory=list)
    executed: list[str] = field(default_factory=list)

    def query(self, sql: str) -> list[dict[str, Any]]:
        self.executed.append(sql)
        return list(self.rows)

    def execute(self, sql: str) -> int:
        self.executed.append(sql)
        return len(self.rows)


class StarRocksBackend:
    """StarRocks 后端。

    原文（[S3-01] 二、对齐原则表「双路查询出口」）：
    「检索明细与向量走 External Catalog 查 Paimon；看板经 ADS 物化至 StarRocks
    内表毫秒级直查」。规则挖掘的批路命中统计走前者——External Catalog 直查 Paimon，
    不搬数据。

    依赖 ``pymysql``（StarRocks 兼容 MySQL 协议）。驱动未安装时构造实例才报错。
    """

    name = "starrocks"

    def __init__(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        user: str | None = None,
        password: str | None = None,
        database: str | None = None,
        connect_timeout: int = 10,
    ) -> None:
        try:
            import pymysql  # noqa: F401
        except ImportError as exc:  # pragma: no cover - 取决于环境
            raise BackendError(
                "StarRocksBackend 需要 pymysql（StarRocks 走 MySQL 协议）；"
                "未安装时可改用 DryRunBackend 或 FlinkSqlGatewayBackend"
            ) from exc
        cfg = settings().starrocks
        self._kwargs = {
            "host": host or cfg.fe_host,
            "port": port or cfg.query_port,
            "user": user or cfg.user,
            "password": password if password is not None else cfg.password,
            "database": database or cfg.internal_database,
            "connect_timeout": connect_timeout,
            "autocommit": True,
        }

    def _connect(self):  # pragma: no cover - 需要真实 StarRocks
        import pymysql

        try:
            return pymysql.connect(**self._kwargs)
        except Exception as exc:
            raise BackendError(f"连接 StarRocks 失败: {exc}") from exc

    def query(self, sql: str) -> list[dict[str, Any]]:  # pragma: no cover
        import pymysql.cursors

        with self._connect() as conn, conn.cursor(pymysql.cursors.DictCursor) as cur:
            try:
                cur.execute(sql)
                return list(cur.fetchall())
            except Exception as exc:
                raise BackendError(f"StarRocks 查询失败: {exc}\nSQL: {sql[:400]}") from exc

    def execute(self, sql: str) -> int:  # pragma: no cover
        with self._connect() as conn, conn.cursor() as cur:
            try:
                return int(cur.execute(sql))
            except Exception as exc:
                raise BackendError(f"StarRocks 执行失败: {exc}\nSQL: {sql[:400]}") from exc


class FlinkSqlGatewayBackend:
    """Flink SQL Gateway 后端，用于提交准实时流作业。

    只用标准库 ``urllib``，不引入 HTTP 依赖——本模块的第一条硬约束是「导入不能炸」。

    ⚠️ 原文未明确，本项目设计：原文只说「流处理选 Flink」「事件类规则以 Flink 消费
    触发事件流」（[S3-01] 五 / [S3-04] 三），没说通过什么方式提交作业。
    这里选 SQL Gateway 的 REST v1 接口，因为编译产物本来就是 SQL 文本。
    """

    name = "flink-sql-gateway"

    def __init__(self, *, base_url: str | None = None, timeout: int = 30) -> None:
        cfg = settings().flink
        self.base_url = (base_url or cfg.sql_gateway_url).rstrip("/")
        self.timeout = timeout
        self._session_handle: str | None = None

    def _post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        body = json.dumps(payload or {}).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:  # pragma: no cover - 需要真实网关
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise BackendError(f"Flink SQL Gateway {path} 返回 {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:  # pragma: no cover
            raise BackendError(f"连接 Flink SQL Gateway 失败（{url}）: {exc.reason}") from exc
        return json.loads(raw) if raw else {}

    def _session(self) -> str:  # pragma: no cover - 需要真实网关
        if self._session_handle is None:
            cfg = settings().flink
            resp = self._post(
                "/v1/sessions",
                {
                    "properties": {
                        "parallelism.default": str(cfg.parallelism),
                        "execution.checkpointing.interval": f"{cfg.checkpoint_interval_ms} ms",
                    }
                },
            )
            handle = resp.get("sessionHandle")
            if not handle:
                raise BackendError(f"Flink SQL Gateway 未返回 sessionHandle: {resp}")
            self._session_handle = str(handle)
        return self._session_handle

    def query(self, sql: str) -> list[dict[str, Any]]:  # pragma: no cover
        raise BackendError(
            "Flink SQL Gateway 后端只用于提交流作业，拉取结果请走 StarRocksBackend "
            "（原文的双路查询出口：External Catalog 直查 Paimon）"
        )

    def execute(self, sql: str) -> int:  # pragma: no cover
        sess = self._session()
        resp = self._post(f"/v1/sessions/{sess}/statements", {"statement": sql})
        handle = resp.get("operationHandle")
        if not handle:
            raise BackendError(f"提交 Flink 作业失败，未返回 operationHandle: {resp}")
        logger.info("已提交 Flink 流作业 operationHandle=%s", handle)
        return -1  # 流作业无界，行数未知


# --------------------------------------------------------------------------- 统一标签服务


#: 规则标签的来源码。registry 里 dwd_mining_data_tag_detail.tag_source 的注释写死了
#: 取值域「collect 采集/rule 规则/vlm 模型」；统一标签服务侧的三来源枚举
#: （``tags.sources.TagSource``）取值也是这三个。
#:
#: 它与 [S3-01] 五那三类「高价值数据来源」（「规则命中 / 事件抽帧 / VLM 标签」）
#: **不是同一件事**，以前混用了：前者是标签事实表的列取值域，后者是向量化的成本分级口径。
#: 把「规则命中」写进 tag_source 的后果是双份的——统一标签服务按枚举校验会直接拒收，
#: 就算收了也在湖仓里落下一个取值域外的字符串，按 tag_source 做的一切统计从此少一块。
TAG_SOURCE_RULE = "rule"

#: 规则命中的置信度。registry 该列注释：「置信度（模型标签必填，人工/规则标签为 1.0）」——
#: 规则是确定性判定，要么中要么不中。
RULE_TAG_CONFIDENCE = 1.0


@dataclass(frozen=True, slots=True)
class TagWriteRequest:
    """一次打标请求。

    原文（[S3-04] 二）要求命中「携带 rule_id 血缘」，所以 rule_id / rule_version
    是必填项，不是可选的元数据。

    :meth:`to_payload` 的键名刻意与统一标签服务的公开入参
    （``tags.records.RawTag`` 的字段名）对齐：本模块**不 import tags**
    （子系统之间零耦合，见 controlplane/subsystems.py），但键名对齐之后，
    把这个 payload 翻成 RawTag 就是一次机械的同名映射，中间不需要再来一张翻译表——
    翻译表正是「改了一边忘了另一边」的高发地。两边字段名的一致性由
    tests/deep/test_mining.py 的 ``test_tag_payload_speaks_the_tag_services_vocabulary`` 钉住。
    """

    data_id: str
    scene_label: str
    rule_id: str
    rule_version: int
    run_id: str
    value_score: float
    #: 标签来源码，落 tag_source 列。默认恒为 ``rule``——本引擎产出的都是规则标签。
    tag_source: str = TAG_SOURCE_RULE
    #: 成本分级口径的来源标记（[S3-01] 五的三类高价值数据来源之一）。
    #: 它不落标签表，是给向量化队列分级看的，所以与 tag_source 分开两个字段。
    high_value_source: str = HIGH_VALUE_SOURCES[0]
    event_time: datetime | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            # 归一前的原始写法：字典映射与别名归一是服务侧的职权（[S3-03] 二①）
            "raw_tag": self.scene_label,
            "source": self.tag_source,
            "data_id": self.data_id,
            "rule_id": self.rule_id,
            # registry 里 rule_version 是 STRING，类型口径在这里对齐
            "rule_version": str(self.rule_version),
            "confidence": RULE_TAG_CONFIDENCE,
            "run_id": self.run_id,
            "value_score": round(self.value_score, 4),
            "high_value_source": self.high_value_source,
            "event_time": self.event_time.isoformat() if self.event_time else None,
        }


class TagService(ABC):
    """统一标签服务接口。

    **规则引擎不写标签表。** 原文（[S3-04] 二）把话说死了：「所有命中统一经标签服务
    打标 …… 规则挖掘的产出自动继承上篇讲的字典映射、去重与审核体系，
    不需要规则引擎自建一套标签写入逻辑」。字典映射与去重都发生在服务侧，
    本接口只负责把命中递过去，并拿回「实际写入了多少条」——那个数字要回写
    dwd_mining_task_detail 的「写入标签量」（[S3-04] 一）。
    """

    @abstractmethod
    def write_tags(self, requests: Sequence[TagWriteRequest]) -> int:
        """批量打标，返回服务侧实际写入的标签条数（去重后）。"""


@dataclass(slots=True)
class InMemoryTagService(TagService):
    """进程内标签服务，做 dry-run 与单测。

    自带一份去重逻辑，模拟真实服务的去重语义：同一 (data_id, scene_label) 只记一次。
    真实服务的去重口径以标签服务为准，这里只是形似。
    """

    written: dict[tuple[str, str], TagWriteRequest] = field(default_factory=dict)

    def write_tags(self, requests: Sequence[TagWriteRequest]) -> int:
        new = 0
        for req in requests:
            key = (req.data_id, req.scene_label)
            if key not in self.written:
                self.written[key] = req
                new += 1
        return new


class HttpTagService(TagService):
    """经 OpenAPI 网关调用统一标签服务。

    原文（[S3-01] 六）：「OpenAPI 为唯一对外通道」，标签治理类接口「一律经统一标签
    服务收口」；任务类接口「幂等键防重复提交」——这里把幂等键一并带上，
    规则重跑时服务侧可据此丢重。

    ⚠️ 原文未明确，本项目设计：原文给了检索类/任务类/标签治理类/数据集类四组接口的
    代表路径，但没给「批量写入命中标签」的具体路径。这里用
    ``/api/v1/tags/bulk-write`` 作默认值，可用构造参数覆盖。
    """

    name = "http-tag-service"

    #: ⚠️ 本项目拟定的默认路径，原文未给。
    DEFAULT_PATH = "/api/v1/tags/bulk-write"

    def __init__(
        self,
        base_url: str,
        *,
        path: str | None = None,
        token: str = "",
        timeout: int = 30,
        batch_size: int = 500,
    ) -> None:
        if not base_url:
            raise ValueError("统一标签服务的 base_url 不能为空")
        if batch_size < 1:
            raise ValueError("batch_size 必须为正")
        self.base_url = base_url.rstrip("/")
        self.path = path or self.DEFAULT_PATH
        self.token = token
        self.timeout = timeout
        self.batch_size = batch_size

    def write_tags(
        self, requests: Sequence[TagWriteRequest]
    ) -> int:  # pragma: no cover - 需要真实服务
        total = 0
        for chunk in _chunks(requests, self.batch_size):
            total += self._post_chunk(chunk)
        return total

    def _post_chunk(self, chunk: Sequence[TagWriteRequest]) -> int:  # pragma: no cover
        # 幂等键：同一 run + 同一批内容 → 同一个键，重试不产生重复标签
        first = chunk[0]
        idem = f"{first.run_id}:{first.rule_id}:{len(chunk)}:{chunk[0].data_id}:{chunk[-1].data_id}"
        payload = {"items": [r.to_payload() for r in chunk]}
        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": idem,  # [S3-01] 六：幂等键防重复提交
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(
            f"{self.base_url}{self.path}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise BackendError(f"统一标签服务返回 {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise BackendError(f"调用统一标签服务失败: {exc.reason}") from exc
        # 服务侧去重后实际写入量；拿不到就退化为请求量
        return int(body.get("written_count", len(chunk)))


# --------------------------------------------------------------------------- 补抽帧派发


@dataclass(frozen=True, slots=True)
class BackfillRequest:
    """一次补抽帧请求：对事件窗口做加密采样。

    原文（[S3-04] 三、隐藏联动）：

        事件命中会异步触发补抽帧——规则识别出接管事件，事件抽帧引擎立刻回头对
        前 15 后 5 秒窗口加密采样，两个引擎经湖仓表解耦协作，谁也不阻塞谁。

    窗口的 15 / 5 秒不可配，由 :meth:`for_event` 从 constants 取。
    """

    data_id: str
    rule_id: str
    run_id: str
    event_time: datetime
    window_start_time: datetime
    window_end_time: datetime
    window_before_sec: int = EVENT_WINDOW_BEFORE_SEC
    window_after_sec: int = EVENT_WINDOW_AFTER_SEC
    reason: str = ""

    @classmethod
    def for_event(
        cls, *, data_id: str, rule_id: str, run_id: str, event_time: datetime, reason: str = ""
    ) -> BackfillRequest:
        """按原文的前 15 后 5 秒窗口构造请求。"""
        from datetime import timedelta

        return cls(
            data_id=data_id,
            rule_id=rule_id,
            run_id=run_id,
            event_time=event_time,
            window_start_time=event_time - timedelta(seconds=EVENT_WINDOW_BEFORE_SEC),
            window_end_time=event_time + timedelta(seconds=EVENT_WINDOW_AFTER_SEC),
            reason=reason,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "data_id": self.data_id,
            "rule_id": self.rule_id,
            "run_id": self.run_id,
            "event_time": self.event_time.isoformat(),
            "window_start_time": self.window_start_time.isoformat(),
            "window_end_time": self.window_end_time.isoformat(),
            "window_before_sec": self.window_before_sec,
            "window_after_sec": self.window_after_sec,
            "sample_mode": "dense",  # 加密采样
            "reason": self.reason,
        }


class FrameBackfillDispatcher(ABC):
    """补抽帧派发接口。派发必须是异步的——「谁也不阻塞谁」。"""

    @abstractmethod
    def dispatch(self, requests: Sequence[BackfillRequest]) -> int:
        """派发补抽帧请求，返回成功派发的条数。"""


#: 补抽帧的交接状态。抽帧引擎轮询 ``frame_supplement_status = 'pending'`` 取活，
#: 做完自己推进到 running/done。取值域见 registry 里该列的注释。
FRAME_SUPPLEMENT_PENDING = "pending"


@dataclass(slots=True)
class LakehouseBackfillDispatcher(FrameBackfillDispatcher):
    """默认实现：把请求写进湖仓表，抽帧引擎自己来取。

    这是最贴原文的做法——原文说「两个引擎**经湖仓表**解耦协作」，
    而不是规则引擎去调抽帧引擎的接口。规则引擎写完就走，不等抽帧做完。

    交接**不另起私有请求表**：registry 早就为这件事在 dwd_mining_result_detail 上
    留好了四列——``frame_supplement_status``（pending/running/done/skipped）与
    ``event_time`` / ``event_window_start_time`` / ``event_window_end_time``。
    抽帧引擎扫 ``frame_supplement_status='pending'`` 就拿到了它需要的全部信息：
    哪条 clip、哪段窗口。另建一张 ``*_backfill_request`` 表只会让同一件事有两份事实，
    而且那张表不在 registry 的 88 张里——SQL 一打到真实 Paimon 就是「表不存在」。

    窗口的前 15 后 5 秒与「加密采样」是原文写死的口径（见 constants.py），
    不是每条请求各自携带的参数，因此不需要落成列。
    """

    backend: SqlBackend
    table: TableRef = DWD_MINING_RESULT_DETAIL

    def dispatch(self, requests: Sequence[BackfillRequest]) -> int:
        if not requests:
            return 0
        from ._sqlfmt import literal

        target = qualified(self.table)
        dispatched = 0
        # 同一 run + 同一窗口的请求合成一条 UPDATE，避免一条命中一条语句
        for (run_id, event_time, start, end), data_ids in _group_requests(requests).items():
            id_list = ", ".join(literal(d) for d in data_ids)
            sql = (
                f"UPDATE {target}\n"
                f"SET `frame_supplement_status` = {literal(FRAME_SUPPLEMENT_PENDING)},\n"
                f"    `event_time` = {literal(event_time)},\n"
                f"    `event_window_start_time` = {literal(start)},\n"
                f"    `event_window_end_time` = {literal(end)}\n"
                f"WHERE `run_id` = {literal(run_id)}\n"
                f"  AND `data_id` IN ({id_list})"
            )
            self.backend.execute(sql)
            dispatched += len(data_ids)
        logger.info(
            "已派发 %d 条补抽帧请求（前 %d 后 %d 秒窗口加密采样）",
            dispatched,
            EVENT_WINDOW_BEFORE_SEC,
            EVENT_WINDOW_AFTER_SEC,
        )
        return dispatched


def _group_requests(
    requests: Sequence[BackfillRequest],
) -> dict[tuple[str, datetime, datetime, datetime], list[str]]:
    """按 ``(run_id, 事件时刻, 窗口起, 窗口止)`` 归组，组内是 data_id 列表。"""
    grouped: dict[tuple[str, datetime, datetime, datetime], list[str]] = {}
    for r in requests:
        key = (r.run_id, r.event_time, r.window_start_time, r.window_end_time)
        ids = grouped.setdefault(key, [])
        if r.data_id not in ids:
            ids.append(r.data_id)
    return grouped


@dataclass(slots=True)
class KafkaBackfillDispatcher(FrameBackfillDispatcher):
    """备选实现：经 Kafka 派发补抽帧请求。

    ``kafka-python`` 是可选依赖，延迟导入。topic 默认取
    :func:`adas_lakehouse.config.settings` 里的 trigger_topic 同族命名。

    ⚠️ 原文未明确，本项目设计：原文明确写的是「经湖仓表解耦协作」，
    Kafka 这条路是本项目补的备选，延迟敏感时用。默认实现请用
    :class:`LakehouseBackfillDispatcher`。
    """

    topic: str = "mining.frame.backfill"
    bootstrap_servers: str | None = None
    _producer: Any = field(default=None, init=False, repr=False)

    def _get_producer(self) -> Any:
        if self._producer is None:
            try:
                from kafka import KafkaProducer  # type: ignore[import-not-found]
            except ImportError as exc:  # pragma: no cover
                raise BackendError(
                    "KafkaBackfillDispatcher 需要 kafka-python；"
                    "未安装时请改用 LakehouseBackfillDispatcher（也更贴原文）"
                ) from exc
            servers = self.bootstrap_servers or settings().kafka.bootstrap_servers
            self._producer = KafkaProducer(
                bootstrap_servers=servers,
                value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
            )
        return self._producer

    def dispatch(
        self, requests: Sequence[BackfillRequest]
    ) -> int:  # pragma: no cover - 需要真实 Kafka
        if not requests:
            return 0
        producer = self._get_producer()
        for r in requests:
            producer.send(self.topic, r.to_payload())
        producer.flush()
        return len(requests)


# --------------------------------------------------------------------------- 结果落表


class ResultSink(ABC):
    """dwd_mining_result_detail 的写入口——原文「结果双写」的湖仓那一路。"""

    @abstractmethod
    def write_results(self, rows: Sequence[dict[str, Any]]) -> int:
        """写入命中明细，返回写入行数。"""


@dataclass(slots=True)
class InMemoryResultSink(ResultSink):
    """进程内结果表，供 dry-run 与单测断言。"""

    rows: list[dict[str, Any]] = field(default_factory=list)

    def write_results(self, rows: Sequence[dict[str, Any]]) -> int:
        self.rows.extend(rows)
        return len(rows)


@dataclass(slots=True)
class SqlResultSink(ResultSink):
    """走 SQL 后端写 dwd_mining_result_detail。

    两种用法：
    * 编译器已产出 ``INSERT INTO ... SELECT`` 时，直接把那条 SQL 交给 backend；
    * Python 侧算完分再落库时，用本类逐批 INSERT VALUES。
    """

    backend: SqlBackend
    table: TableRef = DWD_MINING_RESULT_DETAIL
    batch_size: int = 1000

    def write_results(self, rows: Sequence[dict[str, Any]]) -> int:
        if not rows:
            return 0
        from ._sqlfmt import literal

        cols = list(rows[0].keys())
        target = qualified(self.table)
        written = 0
        for chunk in _chunks(rows, self.batch_size):
            values = [
                "  (" + ", ".join(literal(_coerce(r.get(c), c)) for c in cols) + ")" for r in chunk
            ]
            sql = (
                f"INSERT INTO {target}\n  ({', '.join(f'`{c}`' for c in cols)})\nVALUES\n"
                + ",\n".join(values)
            )
            self.backend.execute(sql)
            written += len(chunk)
        return written


# --------------------------------------------------------------------------- 工具


def _chunks(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    """按 size 切片。"""
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _coerce(value: Any, column: str) -> Any:
    """把 payload 里的 ISO 字符串还原成 datetime，好让 literal() 渲染成 TIMESTAMP。"""
    if isinstance(value, str) and column.endswith("_time") and "T" in value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return value
    return value


def rule_job_endpoint(base_url: str) -> str:
    """规则任务创建的 OpenAPI 端点。

    原文（[S3-01] 六、接口表「任务类」行）：``POST /api/v1/mining/rule-jobs；
    GET /jobs/{jobId}/progress，任务创建与进度查询，幂等键防重复提交``。

    本引擎自己不调这个接口——**它是出口不是入口**：外部编排（控制面 / 网关 / 运维脚本）
    按这个路径把规则任务提交进来，本函数只负责把原文那条路径与部署的 base_url 拼一起，
    保证「对外暴露的路径」全仓库只有 constants 一处定义。
    """
    return f"{base_url.rstrip('/')}{RULE_JOBS_API_PATH}"


def rule_job_progress_endpoint(base_url: str, job_id: str) -> str:
    """规则任务进度查询的 OpenAPI 端点。

    原文出处同 :func:`rule_job_endpoint`：「GET /jobs/{jobId}/progress，
    任务创建与进度查询」。同样是供外部编排调用的公开出口。

    Args:
        base_url: 网关地址。
        job_id: 任务 ID，填进路径里的 ``{jobId}`` 占位符。

    Raises:
        ValueError: job_id 为空——拼出一个带 ``{jobId}`` 字面量的 URL 去请求，
            会得到一个谁也看不懂的 404。
    """
    if not job_id:
        raise ValueError("job_id 不能为空，否则路径里的 {jobId} 占位符会被原样发出去")
    return f"{base_url.rstrip('/')}{RULE_JOB_PROGRESS_API_PATH.format(jobId=job_id)}"
