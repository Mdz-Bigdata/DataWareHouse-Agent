"""控制面状态存储：MySQL（持久运行态）+ Redis（热缓存）。

原文第四章：
  「MySQL 存规则配置、任务配置与执行状态、审核流状态；Redis 存检索热点与字典热缓存
    ——全部是「平台自身运行态」，体量小、可随时重建」

「可随时重建」在代码里的体现是：本模块的默认实现是
:class:`InMemoryControlPlaneStore`——一个进程内存储。它能跑通全部功能，正说明控制面
状态没有一点是不可再生的。生产上换成 :class:`MySqlControlPlaneStore` 也只是换个持久
介质，语义完全一致。

⚠️ 连接配置的说明：``adas_lakehouse.config.settings()`` 覆盖的是 minio / paimon /
flink / starrocks / neo4j / kafka —— **全部是数据面**外部依赖，它按设计就不该知道平台
本地的 MySQL / Redis。因此控制面在本模块内自带 :class:`ControlPlaneStoreConfig`，
读环境变量的风格与 config.py 保持一致。数据面的连接信息一律仍走 ``settings()``。

依赖安全：pymysql / redis 都是**延迟 import**，没装也能 import 本模块。
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from . import constants as K
from .contracts import TaskEvent, TaskRecord, TaskState

__all__ = [
    "ControlPlaneStoreConfig",
    "ControlPlaneStore",
    "InMemoryControlPlaneStore",
    "MySqlControlPlaneStore",
    "HotCache",
    "InMemoryHotCache",
    "RedisHotCache",
    "CONTROL_PLANE_MYSQL_DDL",
    "store_config",
]


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    return int(raw) if raw else default


@dataclass(frozen=True, slots=True)
class ControlPlaneStoreConfig:
    """平台本地存储连接配置（控制面独有，不进 config.settings()）。"""

    mysql_host: str = field(default_factory=lambda: _env("CONTROL_PLANE_MYSQL_HOST", "localhost"))
    #: 宿主机发布端口（容器内仍是 3306，见 config.py 顶部端口口径说明）
    mysql_port: int = field(default_factory=lambda: _env_int("CONTROL_PLANE_MYSQL_PORT", 18606))
    mysql_user: str = field(default_factory=lambda: _env("CONTROL_PLANE_MYSQL_USER", "adas"))
    mysql_password: str = field(default_factory=lambda: _env("CONTROL_PLANE_MYSQL_PASSWORD", ""))
    mysql_database: str = field(
        default_factory=lambda: _env("CONTROL_PLANE_MYSQL_DATABASE", "adas_mining_platform")
    )
    redis_host: str = field(default_factory=lambda: _env("CONTROL_PLANE_REDIS_HOST", "localhost"))
    #: 宿主机发布端口（容器内仍是 6379，见 config.py 顶部端口口径说明）
    redis_port: int = field(default_factory=lambda: _env_int("CONTROL_PLANE_REDIS_PORT", 18679))
    redis_db: int = field(default_factory=lambda: _env_int("CONTROL_PLANE_REDIS_DB", 0))


def store_config() -> ControlPlaneStoreConfig:
    """读一份控制面存储配置（每次读环境变量，测试里改完即刻生效）。"""
    return ControlPlaneStoreConfig()


# --------------------------------------------------------------------------- MySQL DDL

#: 控制面 MySQL 建表语句。
#:
#: ⚠️ 原文未明确，本项目设计：原文只说 MySQL 存「规则配置、任务配置与执行状态、审核流状态」，
#: 没给 DDL。这四张表就是那三类状态 + 审计事件流的最小落地。
#: 注意所有表都带 ``cp_`` 前缀——控制面的表与湖仓四段式命名是两套体系，
#: 刻意不混用，避免有人误以为它们是数仓表。
CONTROL_PLANE_MYSQL_DDL: str = """\
-- 控制面本地库：清空重建不影响任何业务数据（原文第四章健康判据）
CREATE TABLE IF NOT EXISTS `cp_rule_config` (
  `rule_id`        VARCHAR(64)  NOT NULL COMMENT '规则 ID',
  `rule_version`   INT          NOT NULL COMMENT '规则版本，每次下发 +1',
  `rule_name`      VARCHAR(200) NOT NULL COMMENT '规则名称',
  `scene_expression` TEXT       NOT NULL COMMENT '场景表达式（SQL 谓词）',
  `tag_code`       VARCHAR(128)          COMMENT '命中后打的标签编码',
  `engine`         VARCHAR(32)  NOT NULL COMMENT 'spark_batch / flink_stream',
  `enabled`        TINYINT(1)   NOT NULL DEFAULT 1,
  `priority`       INT          NOT NULL DEFAULT 5,
  `owner`          VARCHAR(64),
  `created_at`     DATETIME(3)  NOT NULL,
  `updated_at`     DATETIME(3)  NOT NULL,
  PRIMARY KEY (`rule_id`, `rule_version`),
  KEY `idx_rule_updated` (`updated_at`)
) ENGINE=InnoDB COMMENT='规则配置；经 Flink CDC 同步入湖 ods_mining_rule_config';

CREATE TABLE IF NOT EXISTS `cp_task` (
  `task_id`        VARCHAR(96)  NOT NULL,
  `run_id`         VARCHAR(64)  NOT NULL COMMENT '三级 ID：处理运行级',
  `task_kind`      VARCHAR(32)  NOT NULL,
  `subsystem`      VARCHAR(32)  NOT NULL,
  `task_state`     VARCHAR(24)  NOT NULL,
  `rule_id`        VARCHAR(64),
  `rule_version`   INT,
  `priority`       INT          NOT NULL DEFAULT 5,
  `requested_by`   VARCHAR(64)  NOT NULL,
  `idempotency_key` VARCHAR(128)         COMMENT '幂等键防重复提交（原文第六章）',
  `external_handle` VARCHAR(200)         COMMENT '数据面作业句柄（Spark/Flink/K8s/Ray）',
  `attempt`        INT          NOT NULL DEFAULT 0,
  `envelope_json`  TEXT         NOT NULL COMMENT 'TaskEnvelope 快照',
  `artifacts_json` TEXT                  COMMENT '产物指针列表（只有 ID，没有数据本体）',
  `rows_written`   BIGINT       NOT NULL DEFAULT 0,
  `review_decision` VARCHAR(16),
  `reviewer`       VARCHAR(64),
  `message`        VARCHAR(500),
  `written_back`   TINYINT(1)   NOT NULL DEFAULT 0,
  `started_at`     DATETIME(3)           COMMENT '数据面确认开跑的时刻，回写 dwd_mining_task_detail.start_time',
  `finished_at`    DATETIME(3)           COMMENT '进终态的时刻，回写 end_time；与 started_at 相减得 duration_sec',
  `created_at`     DATETIME(3)  NOT NULL,
  `updated_at`     DATETIME(3)  NOT NULL,
  PRIMARY KEY (`task_id`),
  UNIQUE KEY `uk_idempotency` (`idempotency_key`),
  KEY `idx_state_priority` (`task_state`, `priority`, `created_at`),
  KEY `idx_writeback` (`written_back`, `updated_at`)
) ENGINE=InnoDB COMMENT='任务配置与执行状态 + 审核流状态';

CREATE TABLE IF NOT EXISTS `cp_task_event` (
  `task_id`    VARCHAR(96) NOT NULL,
  `event_seq`  INT         NOT NULL,
  `from_state` VARCHAR(24),
  `to_state`   VARCHAR(24) NOT NULL,
  `event_time` DATETIME(3) NOT NULL,
  `actor`      VARCHAR(64) NOT NULL,
  `detail`     VARCHAR(500),
  PRIMARY KEY (`task_id`, `event_seq`)
) ENGINE=InnoDB COMMENT='状态迁移审计流；定期回写 dwd_mining_task_detail 进血缘';

CREATE TABLE IF NOT EXISTS `cp_audit_log` (
  `audit_id`   BIGINT       NOT NULL AUTO_INCREMENT,
  `api_path`   VARCHAR(200) NOT NULL,
  `http_method` VARCHAR(8)  NOT NULL,
  `caller`     VARCHAR(64)  NOT NULL,
  `at`         DATETIME(3)  NOT NULL,
  `outcome`    VARCHAR(24)  NOT NULL,
  `detail`     VARCHAR(500),
  PRIMARY KEY (`audit_id`),
  KEY `idx_audit_at` (`at`)
) ENGINE=InnoDB COMMENT='OpenAPI 网关审计（原文第三章接入层：认证/限流/审计）';
"""


# --------------------------------------------------------------------------- 存储协议


@runtime_checkable
class ControlPlaneStore(Protocol):
    """控制面状态存储协议。实现它就能替换底座，语义不变。"""

    def save_task(self, record: TaskRecord) -> None: ...

    def get_task(self, task_id: str) -> TaskRecord | None: ...

    def find_by_idempotency_key(self, key: str) -> TaskRecord | None: ...

    def list_tasks(
        self, *, states: Iterable[TaskState] | None = None, limit: int = 100
    ) -> list[TaskRecord]: ...

    def next_event_seq(self, task_id: str) -> int: ...

    def append_event(self, event: TaskEvent) -> None: ...

    def list_events(self, task_id: str) -> list[TaskEvent]: ...

    def pending_writeback(self, limit: int = K.WRITEBACK_BATCH_ROWS) -> list[TaskRecord]: ...

    def mark_written_back(self, task_ids: Iterable[str]) -> int: ...

    def purge(self) -> None: ...


# --------------------------------------------------------------------------- 内存实现


class InMemoryControlPlaneStore:
    """进程内实现：默认存储，也是「控制面可随时重建」的活证明。

    线程安全（RLock）。功能上与 MySQL 实现等价，测试与本地跑批直接用它。
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, TaskRecord] = {}
        self._idempotency: dict[str, str] = {}
        self._events: dict[str, list[TaskEvent]] = {}

    def save_task(self, record: TaskRecord) -> None:
        with self._lock:
            self._tasks[record.task_id] = record
            key = record.envelope.idempotency_key
            if key:
                self._idempotency[key] = record.task_id

    def get_task(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            return self._tasks.get(task_id)

    def find_by_idempotency_key(self, key: str) -> TaskRecord | None:
        with self._lock:
            task_id = self._idempotency.get(key)
            return self._tasks.get(task_id) if task_id else None

    def list_tasks(
        self, *, states: Iterable[TaskState] | None = None, limit: int = 100
    ) -> list[TaskRecord]:
        wanted = set(states) if states is not None else None
        with self._lock:
            rows = [r for r in self._tasks.values() if wanted is None or r.state in wanted]
        # 调度顺序：优先级升序（数值越小越优先），同优先级按创建时间先到先得
        rows.sort(key=lambda r: (r.envelope.priority, r.created_at))
        return rows[:limit]

    def next_event_seq(self, task_id: str) -> int:
        with self._lock:
            return len(self._events.get(task_id, ())) + 1

    def append_event(self, event: TaskEvent) -> None:
        with self._lock:
            self._events.setdefault(event.task_id, []).append(event)

    def list_events(self, task_id: str) -> list[TaskEvent]:
        with self._lock:
            return list(self._events.get(task_id, ()))

    def pending_writeback(self, limit: int = K.WRITEBACK_BATCH_ROWS) -> list[TaskRecord]:
        with self._lock:
            rows = [r for r in self._tasks.values() if not r.written_back]
        rows.sort(key=lambda r: r.updated_at)
        return rows[:limit]

    def mark_written_back(self, task_ids: Iterable[str]) -> int:
        count = 0
        with self._lock:
            for tid in task_ids:
                rec = self._tasks.get(tid)
                if rec is not None and not rec.written_back:
                    self._tasks[tid] = rec.evolve(written_back=True)
                    count += 1
        return count

    def purge(self) -> None:
        """清空控制面——原文第四章健康判据的那把「清空键」。

        调用它之后业务数据必须完好无损（全在湖仓），这是本子系统的核心不变量。
        """
        with self._lock:
            self._tasks.clear()
            self._idempotency.clear()
            self._events.clear()


# --------------------------------------------------------------------------- MySQL 实现


class MySqlControlPlaneStore:
    """MySQL 实现。pymysql 延迟 import——没装也不影响本模块被 import。

    :param config: 连接配置，默认读环境变量
    :param connection_factory: 可注入的连接工厂（测试/连接池场景）
    """

    def __init__(
        self,
        config: ControlPlaneStoreConfig | None = None,
        *,
        connection_factory: Any = None,
    ) -> None:
        self._config = config or store_config()
        self._factory = connection_factory
        self._conn: Any = None

    # ---- 连接 ----

    def _connect(self) -> Any:
        if self._conn is not None:
            return self._conn
        if self._factory is not None:
            self._conn = self._factory()
            return self._conn
        try:
            import pymysql  # 延迟 import：验收阶段的全量 import 检查不应因缺包而失败
        except ImportError as exc:  # pragma: no cover - 取决于环境
            raise RuntimeError(
                "MySqlControlPlaneStore 需要 pymysql；请 `pip install pymysql`，"
                "或改用 InMemoryControlPlaneStore（控制面状态本就可随时重建）"
            ) from exc
        cfg = self._config
        self._conn = pymysql.connect(
            host=cfg.mysql_host,
            port=cfg.mysql_port,
            user=cfg.mysql_user,
            password=cfg.mysql_password,
            database=cfg.mysql_database,
            charset="utf8mb4",
            autocommit=True,
        )
        return self._conn

    def _execute(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        conn = self._connect()
        with conn.cursor() as cur:
            cur.execute(sql, args)
            if cur.description is None:
                return []
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]

    def init_schema(self) -> None:
        """建表。DDL 见 :data:`CONTROL_PLANE_MYSQL_DDL`。"""
        for stmt in (s.strip() for s in CONTROL_PLANE_MYSQL_DDL.split(";")):
            if stmt and not stmt.startswith("--"):
                self._execute(stmt)

    # ---- 序列化 ----

    @staticmethod
    def _dump(record: TaskRecord) -> tuple[Any, ...]:
        import json

        env = record.envelope
        return (
            record.task_id,
            env.run_id,
            env.kind.value,
            env.subsystem,
            record.state.value,
            env.rule_id,
            env.rule_version,
            env.priority,
            env.requested_by,
            env.idempotency_key,
            record.external_handle,
            record.attempt,
            json.dumps(env.to_dict(), ensure_ascii=False),
            json.dumps([a.as_row() for a in record.artifacts], ensure_ascii=False),
            record.rows_written,
            record.review_decision.value if record.review_decision else None,
            record.reviewer,
            record.message[:500],
            1 if record.written_back else 0,
            record.started_at,
            record.finished_at,
            record.created_at,
            record.updated_at,
        )

    @staticmethod
    def _load(row: dict[str, Any]) -> TaskRecord:
        import json

        from ..ids import ArtifactStatus
        from .contracts import ArtifactRef, ReviewDecision, TaskEnvelope, TaskKind

        env_raw = json.loads(row["envelope_json"])
        envelope = TaskEnvelope(
            task_id=env_raw["task_id"],
            kind=TaskKind(env_raw["kind"]),
            subsystem=env_raw["subsystem"],
            run_id=env_raw["run_id"],
            input_selector=env_raw.get("input_selector", ""),
            input_tables=tuple(env_raw.get("input_tables", ())),
            params=env_raw.get("params", {}),
            rule_id=env_raw.get("rule_id"),
            rule_version=env_raw.get("rule_version"),
            priority=env_raw.get("priority", K.PRIORITY_DEFAULT),
            requested_by=env_raw.get("requested_by", "system"),
            created_at=datetime.fromisoformat(env_raw["created_at"]),
            idempotency_key=env_raw.get("idempotency_key"),
        )
        artifacts = tuple(
            ArtifactRef(
                artifact_id=a["artifact_id"],
                table=a["target_table"],
                row_count=a.get("row_count", 0),
                parent_artifact_id=a.get("parent_artifact_id"),
                status=ArtifactStatus(a.get("artifact_status", "active")),
            )
            for a in json.loads(row.get("artifacts_json") or "[]")
        )
        return TaskRecord(
            envelope=envelope,
            state=TaskState(row["task_state"]),
            external_handle=row.get("external_handle"),
            attempt=row.get("attempt", 0),
            artifacts=artifacts,
            rows_written=row.get("rows_written", 0),
            review_decision=ReviewDecision(row["review_decision"])
            if row.get("review_decision")
            else None,
            reviewer=row.get("reviewer"),
            message=row.get("message") or "",
            started_at=row.get("started_at"),
            finished_at=row.get("finished_at"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            written_back=bool(row.get("written_back", 0)),
        )

    # ---- 协议实现 ----

    _UPSERT = """\
INSERT INTO `cp_task` (task_id, run_id, task_kind, subsystem, task_state, rule_id, rule_version,
  priority, requested_by, idempotency_key, external_handle, attempt, envelope_json, artifacts_json,
  rows_written, review_decision, reviewer, message, written_back, started_at, finished_at,
  created_at, updated_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE task_state=VALUES(task_state), external_handle=VALUES(external_handle),
  attempt=VALUES(attempt), artifacts_json=VALUES(artifacts_json), rows_written=VALUES(rows_written),
  review_decision=VALUES(review_decision), reviewer=VALUES(reviewer), message=VALUES(message),
  written_back=VALUES(written_back), started_at=VALUES(started_at),
  finished_at=VALUES(finished_at), updated_at=VALUES(updated_at)"""

    def save_task(self, record: TaskRecord) -> None:
        self._execute(self._UPSERT, self._dump(record))

    def get_task(self, task_id: str) -> TaskRecord | None:
        rows = self._execute("SELECT * FROM `cp_task` WHERE task_id=%s", (task_id,))
        return self._load(rows[0]) if rows else None

    def find_by_idempotency_key(self, key: str) -> TaskRecord | None:
        rows = self._execute("SELECT * FROM `cp_task` WHERE idempotency_key=%s", (key,))
        return self._load(rows[0]) if rows else None

    def list_tasks(
        self, *, states: Iterable[TaskState] | None = None, limit: int = 100
    ) -> list[TaskRecord]:
        if states is None:
            rows = self._execute(
                "SELECT * FROM `cp_task` ORDER BY priority, created_at LIMIT %s", (limit,)
            )
        else:
            values = [s.value for s in states]
            if not values:
                return []
            holes = ", ".join(["%s"] * len(values))
            rows = self._execute(
                f"SELECT * FROM `cp_task` WHERE task_state IN ({holes}) "
                f"ORDER BY priority, created_at LIMIT %s",
                (*values, limit),
            )
        return [self._load(r) for r in rows]

    def next_event_seq(self, task_id: str) -> int:
        rows = self._execute(
            "SELECT COALESCE(MAX(event_seq), 0) AS m FROM `cp_task_event` WHERE task_id=%s",
            (task_id,),
        )
        return int(rows[0]["m"]) + 1 if rows else 1

    def append_event(self, event: TaskEvent) -> None:
        self._execute(
            "INSERT IGNORE INTO `cp_task_event` "
            "(task_id, event_seq, from_state, to_state, event_time, actor, detail) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                event.task_id,
                event.seq,
                event.from_state.value if event.from_state else None,
                event.to_state.value,
                event.at,
                event.actor,
                event.detail[:500],
            ),
        )

    def list_events(self, task_id: str) -> list[TaskEvent]:
        rows = self._execute(
            "SELECT * FROM `cp_task_event` WHERE task_id=%s ORDER BY event_seq", (task_id,)
        )
        return [
            TaskEvent(
                task_id=r["task_id"],
                seq=r["event_seq"],
                from_state=TaskState(r["from_state"]) if r["from_state"] else None,
                to_state=TaskState(r["to_state"]),
                at=r["event_time"],
                actor=r["actor"],
                detail=r.get("detail") or "",
            )
            for r in rows
        ]

    def pending_writeback(self, limit: int = K.WRITEBACK_BATCH_ROWS) -> list[TaskRecord]:
        rows = self._execute(
            "SELECT * FROM `cp_task` WHERE written_back=0 ORDER BY updated_at LIMIT %s", (limit,)
        )
        return [self._load(r) for r in rows]

    def mark_written_back(self, task_ids: Iterable[str]) -> int:
        ids = list(task_ids)
        if not ids:
            return 0
        holes = ", ".join(["%s"] * len(ids))
        self._execute(f"UPDATE `cp_task` SET written_back=1 WHERE task_id IN ({holes})", tuple(ids))
        return len(ids)

    def purge(self) -> None:
        """清库。原文健康判据：清空重建后业务数据必须完好（主数据全在湖仓）。"""
        for table in ("cp_task_event", "cp_task", "cp_audit_log", "cp_rule_config"):
            self._execute(f"TRUNCATE TABLE `{table}`")


# --------------------------------------------------------------------------- 热缓存


@runtime_checkable
class HotCache(Protocol):
    """Redis 热缓存协议：检索热点 + 字典热缓存 + 幂等键。"""

    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str, ttl_seconds: int) -> None: ...

    def set_if_absent(self, key: str, value: str, ttl_seconds: int) -> bool: ...

    def delete(self, key: str) -> None: ...


class InMemoryHotCache:
    """进程内 TTL 缓存。默认实现——缓存丢了只是变慢，不丢任何数据。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data: dict[str, tuple[str, float]] = {}

    def _alive(self, key: str) -> str | None:
        item = self._data.get(key)
        if item is None:
            return None
        value, expire_at = item
        if expire_at < time.monotonic():
            self._data.pop(key, None)
            return None
        return value

    def get(self, key: str) -> str | None:
        with self._lock:
            return self._alive(key)

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        with self._lock:
            self._data[key] = (value, time.monotonic() + ttl_seconds)

    def set_if_absent(self, key: str, value: str, ttl_seconds: int) -> bool:
        """SETNX 语义。幂等键防重复提交就靠它。"""
        with self._lock:
            if self._alive(key) is not None:
                return False
            self._data[key] = (value, time.monotonic() + ttl_seconds)
            return True

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)


class RedisHotCache:
    """Redis 实现。redis-py 延迟 import。"""

    def __init__(
        self, config: ControlPlaneStoreConfig | None = None, *, client: Any = None
    ) -> None:
        self._config = config or store_config()
        self._client = client

    def _redis(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import redis  # 延迟 import
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "RedisHotCache 需要 redis-py；请 `pip install redis`，或改用 InMemoryHotCache"
            ) from exc
        cfg = self._config
        self._client = redis.Redis(
            host=cfg.redis_host, port=cfg.redis_port, db=cfg.redis_db, decode_responses=True
        )
        return self._client

    def get(self, key: str) -> str | None:
        return self._redis().get(key)

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self._redis().set(key, value, ex=ttl_seconds)

    def set_if_absent(self, key: str, value: str, ttl_seconds: int) -> bool:
        return bool(self._redis().set(key, value, ex=ttl_seconds, nx=True))

    def delete(self, key: str) -> None:
        self._redis().delete(key)
