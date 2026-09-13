"""增量扫描水位——规则天天跑而成本不爆炸的那把钥匙。

原文（[S3-04] 三、批流双模）：

    增量扫描：基于 _ingest_time / update_time 水位做增量，避免每次全表回扫——
    规则天天跑，成本不爆炸的关键；

两个水位列的选择不是随便挑的，而是跟着共享契约的系统字段规范走
（见 :class:`adas_lakehouse.domains.Layer.system_fields`）：ODS 层只有 ``_ingest_time``，
DWD/DWS/ADS 层有 ``update_time``。规则扫的表横跨两类，所以 :func:`watermark_column`
按层选列，不让调用方猜。

水位本身属于「平台自身运行态」，按 [S3-01] 四的控制面/数据面分离，它存在控制面
（MySQL / 本地文件），不占湖仓——「MySQL 丢了，重建配置即可」，水位丢了最多多扫一次。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..domains import Layer
from ._sqlfmt import ident, literal

__all__ = [
    "DEFAULT_LOOKBACK_MINUTES",
    "EPOCH_WATERMARK",
    "Watermark",
    "WatermarkStore",
    "InMemoryWatermarkStore",
    "JsonFileWatermarkStore",
    "ControlPlaneWatermarkStore",
    "watermark_column",
    "incremental_predicate",
]

#: 水位回退安全边界，分钟。
#: ⚠️ 原文未明确，本项目设计：原文只说「基于 _ingest_time / update_time 水位做增量」，
#: 没提迟到数据。Paimon 写入与 CDC 同步都存在秒级到分钟级的乱序，如果每次严格从
#: 上次高水位接着扫，边界上的迟到记录会被永久跳过。因此每轮把下界往回挪 5 分钟，
#: 代价是边界数据可能被重复命中——而命中结果按 artifact_id 幂等（见 ids 模块规则一），
#: 重复扫描不会产生重复产物，所以这个交换是划算的。
DEFAULT_LOOKBACK_MINUTES = 5

#: 从未跑过的规则的初始下界。选 1970-01-01 而不是 None，是为了让 SQL 谓词形状恒定，
#: 首轮就是一次全表扫描——这正是原文说的「每次全表回扫」，只应该发生一次。
EPOCH_WATERMARK = datetime(1970, 1, 1, 0, 0, 0)


def watermark_column(layer: Layer) -> str:
    """按数仓层级选水位列。

    Args:
        layer: 被扫描表所在层级。

    Returns:
        ``_ingest_time``（ODS）或 ``update_time``（DWD/DWS/ADS）。

    原文并列写了 ``_ingest_time / update_time`` 两个列名，没说什么时候用哪个；
    这里按共享契约 :attr:`adas_lakehouse.domains.Layer.system_fields` 的定义来分——
    ODS 层压根没有 update_time 列，用错了就是执行期报错。
    """
    fields = layer.system_fields
    return "update_time" if "update_time" in fields else "_ingest_time"


@dataclass(frozen=True, slots=True)
class Watermark:
    """一条规则在一张表上的扫描水位。

    Attributes:
        rule_id: 规则 ID。水位按规则隔离——两条规则扫同一张表互不影响，
            某条规则回刷历史不会拖累其他规则。
        table: 被扫描的表名。
        column: 水位列（``_ingest_time`` 或 ``update_time``）。
        low: 本轮扫描下界（含）。
        high: 本轮扫描上界（不含）。
        updated_at: 水位提交时间。
    """

    rule_id: str
    table: str
    column: str
    low: datetime
    high: datetime
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.low > self.high:
            raise ValueError(f"水位区间非法: low={self.low} > high={self.high}")

    @property
    def span_seconds(self) -> float:
        """本轮扫描覆盖的时间跨度，秒。写进 dwd_mining_task_detail 的「扫描范围」。"""
        return (self.high - self.low).total_seconds()

    def key(self) -> str:
        return f"{self.rule_id}::{self.table}"

    def to_dict(self) -> dict[str, str]:
        return {
            "rule_id": self.rule_id,
            "table": self.table,
            "column": self.column,
            "low": self.low.isoformat(),
            "high": self.high.isoformat(),
            "updated_at": (self.updated_at or datetime.now()).isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> Watermark:
        return cls(
            rule_id=data["rule_id"],
            table=data["table"],
            column=data["column"],
            low=datetime.fromisoformat(data["low"]),
            high=datetime.fromisoformat(data["high"]),
            updated_at=datetime.fromisoformat(data["updated_at"])
            if data.get("updated_at")
            else None,
        )


def incremental_predicate(wm: Watermark, *, alias: str = "") -> str:
    """渲染增量扫描谓词：``col >= low AND col < high``。

    上界取「不含」，是为了让相邻两轮的区间严丝合缝地拼接，不重不漏
    （回退边界另由 DEFAULT_LOOKBACK_MINUTES 处理）。
    """
    col = f"{ident(alias)}.{ident(wm.column)}" if alias else ident(wm.column)
    return f"({col} >= {literal(wm.low)} AND {col} < {literal(wm.high)})"


@runtime_checkable
class WatermarkStore(Protocol):
    """水位存储接口。控制面实现，湖仓不存。"""

    def get(self, rule_id: str, table: str) -> Watermark | None:
        """读取上一轮提交的水位；从未跑过返回 None。"""
        ...

    def commit(self, wm: Watermark) -> None:
        """提交本轮水位。必须在结果成功落表之后调用，否则会丢数据。"""
        ...

    def reset(self, rule_id: str, table: str | None = None) -> int:
        """清空水位，触发下一轮全量回扫。返回被清掉的条数。"""
        ...


class _BaseStore:
    """三种实现共用的「下一轮扫什么区间」推导逻辑。"""

    def next_window(
        self,
        rule_id: str,
        table: str,
        layer: Layer,
        *,
        now: datetime | None = None,
        lookback_minutes: int = DEFAULT_LOOKBACK_MINUTES,
        initial_low: datetime | None = None,
    ) -> Watermark:
        """算出本轮该扫的 [low, high) 区间。

        Args:
            rule_id: 规则 ID。
            table: 被扫描表。
            layer: 该表所在层级，决定水位列。
            now: 扫描上界，默认当前时刻。
            lookback_minutes: 下界回退分钟数，见 DEFAULT_LOOKBACK_MINUTES 的说明。
            initial_low: 首轮下界。不给则从 EPOCH_WATERMARK 起（即一次全表回扫）。

        Returns:
            本轮的 Watermark（尚未提交）。
        """
        high = now or datetime.now()
        previous = self.get(rule_id, table)  # type: ignore[attr-defined]
        if previous is None:
            low = initial_low or EPOCH_WATERMARK
        else:
            low = previous.high - timedelta(minutes=lookback_minutes)
            if low < EPOCH_WATERMARK:
                low = EPOCH_WATERMARK
        if low > high:
            # 时钟回拨或人为传了个更早的 now：退化成空区间，不报错，本轮扫 0 行
            low = high
        return Watermark(
            rule_id=rule_id,
            table=table,
            column=watermark_column(layer),
            low=low,
            high=high,
        )


class InMemoryWatermarkStore(_BaseStore):
    """进程内水位，仅用于测试与 dry-run。进程退出即丢。"""

    def __init__(self) -> None:
        self._data: dict[str, Watermark] = {}
        self._lock = threading.Lock()

    def get(self, rule_id: str, table: str) -> Watermark | None:
        with self._lock:
            return self._data.get(f"{rule_id}::{table}")

    def commit(self, wm: Watermark) -> None:
        with self._lock:
            self._data[wm.key()] = replace(wm, updated_at=wm.updated_at or datetime.now())

    def reset(self, rule_id: str, table: str | None = None) -> int:
        with self._lock:
            victims = [
                k
                for k in self._data
                if k.startswith(f"{rule_id}::") and (table is None or k == f"{rule_id}::{table}")
            ]
            for k in victims:
                del self._data[k]
            return len(victims)


class JsonFileWatermarkStore(_BaseStore):
    """本地 JSON 文件水位——单机跑批、本地 compose 调试的默认实现。

    写入走「临时文件 + 原子 rename」，避免进程在写一半时被杀导致水位文件损坏。
    水位文件损坏的后果是下一轮全表回扫，虽然不丢数据，但会白烧掉 4 小时批处理窗口
    （constants.BATCH_SLA_HOURS），值得多写这几行。
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load(self) -> dict[str, dict[str, str]]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # 文件损坏时当作空——退化为全量回扫，比崩掉整条链路好
            return {}
        return raw if isinstance(raw, dict) else {}

    def _dump(self, data: dict[str, dict[str, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".wm-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def get(self, rule_id: str, table: str) -> Watermark | None:
        with self._lock:
            entry = self._load().get(f"{rule_id}::{table}")
        if entry is None:
            return None
        try:
            return Watermark.from_dict(entry)
        except (KeyError, ValueError):
            return None

    def commit(self, wm: Watermark) -> None:
        with self._lock:
            data = self._load()
            data[wm.key()] = replace(wm, updated_at=wm.updated_at or datetime.now()).to_dict()
            self._dump(data)

    def reset(self, rule_id: str, table: str | None = None) -> int:
        with self._lock:
            data = self._load()
            victims = [
                k
                for k in data
                if k.startswith(f"{rule_id}::") and (table is None or k == f"{rule_id}::{table}")
            ]
            for k in victims:
                del data[k]
            if victims:
                self._dump(data)
            return len(victims)


class ControlPlaneWatermarkStore(_BaseStore):
    """控制面 MySQL 水位——生产实现，对齐 [S3-01] 四「控制面（平台本地）MySQL 存
    规则配置、任务配置与执行状态」。

    ``pymysql`` 是可选依赖：驱动没装时构造实例才报错，**导入本模块永远不会炸**
    （验收阶段的全量 import 检查因此不受影响）。

    ⚠️ 原文未明确，本项目设计：原文没给水位表的 DDL，:attr:`DDL` 是本项目拟的。
    """

    #: 控制面水位表 DDL，首次使用时手工建一次即可。
    DDL = """
CREATE TABLE IF NOT EXISTS mining_rule_watermark (
  rule_id      VARCHAR(64)  NOT NULL COMMENT '规则 ID',
  table_name   VARCHAR(128) NOT NULL COMMENT '被扫描表',
  wm_column    VARCHAR(64)  NOT NULL COMMENT '水位列：_ingest_time 或 update_time',
  low_bound    DATETIME(3)  NOT NULL COMMENT '扫描下界（含）',
  high_bound   DATETIME(3)  NOT NULL COMMENT '扫描上界（不含）',
  updated_at   DATETIME(3)  NOT NULL,
  PRIMARY KEY (rule_id, table_name)
) COMMENT '规则挖掘增量扫描水位（平台运行态，不入湖）';
""".strip()

    def __init__(
        self,
        *,
        host: str,
        # 宿主机口径默认值（容器内是 mysql:3306，见 config.py 顶部端口口径说明）；
        # 与 controlplane/store.py 的 CONTROL_PLANE_MYSQL_PORT 同一个 MySQL 实例
        port: int = 18606,
        user: str,
        password: str,
        database: str,
        table: str = "mining_rule_watermark",
    ) -> None:
        try:
            import pymysql  # noqa: F401  # 延迟导入：驱动缺失不应影响模块导入
        except ImportError as exc:  # pragma: no cover - 取决于环境
            raise RuntimeError(
                "ControlPlaneWatermarkStore 需要 pymysql；未安装时请改用 "
                "JsonFileWatermarkStore 或 InMemoryWatermarkStore"
            ) from exc
        self._conn_kwargs = {
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "database": database,
            "autocommit": True,
        }
        self._table = table

    def _connect(self):  # pragma: no cover - 需要真实 MySQL
        import pymysql

        return pymysql.connect(**self._conn_kwargs)

    def get(self, rule_id: str, table: str) -> Watermark | None:  # pragma: no cover
        sql = (
            f"SELECT rule_id, table_name, wm_column, low_bound, high_bound, updated_at "
            f"FROM `{self._table}` WHERE rule_id=%s AND table_name=%s"
        )
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, (rule_id, table))
            row = cur.fetchone()
        if not row:
            return None
        return Watermark(row[0], row[1], row[2], row[3], row[4], row[5])

    def commit(self, wm: Watermark) -> None:  # pragma: no cover
        sql = (
            f"INSERT INTO `{self._table}` "
            f"(rule_id, table_name, wm_column, low_bound, high_bound, updated_at) "
            f"VALUES (%s,%s,%s,%s,%s,%s) "
            f"ON DUPLICATE KEY UPDATE wm_column=VALUES(wm_column), low_bound=VALUES(low_bound), "
            f"high_bound=VALUES(high_bound), updated_at=VALUES(updated_at)"
        )
        params = (wm.rule_id, wm.table, wm.column, wm.low, wm.high, wm.updated_at or datetime.now())
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)

    def reset(self, rule_id: str, table: str | None = None) -> int:  # pragma: no cover
        if table is None:
            sql, params = f"DELETE FROM `{self._table}` WHERE rule_id=%s", (rule_id,)
        else:
            sql, params = (
                f"DELETE FROM `{self._table}` WHERE rule_id=%s AND table_name=%s",
                (rule_id, table),
            )
        with self._connect() as conn, conn.cursor() as cur:
            return int(cur.execute(sql, params))
