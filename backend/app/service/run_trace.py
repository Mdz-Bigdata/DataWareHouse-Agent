# -*- coding: utf-8 -*-
"""全链路追溯 ID 贯通 + 分阶段耗时度量。

一次问数 = 一条可追溯的运行记录。三级 ID 语义（借鉴智驾数据闭环湖仓的三级 ID
思想，但不依赖其任何代码）：

* ``trace_id`` —— 一次外部请求。由 platform_gateway 签发并通过 ``x-trace-id``
  透传，跨子系统保持不变；下游没有拿到就地补签一个。
* ``run_id``   —— 一次执行。``ask_agent.ask()`` 每被调用一次就产出一条运行记录，
  即使同一个 trace 上重试也各自成条。
* ``stage``    —— 运行记录内的一个阶段（Qdrant 召回 / LLM 生成 / 网闸校验 /
  SQL 编译 / 物理执行 / 纠错重试……），各自独立计时。

运行记录只保存在进程内存里，条数有界，不落盘、不外发；字符串字段一律截断，
避免把整份 SQL/问句无限堆在内存里。
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Deque, Dict, Iterator, List, Optional

# 追溯 ID 来自 HTTP 头，属于外部输入：只接受这个字符集与长度，其余一律重新签发。
_TRACE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")

# platform_gateway 透传 trace_id 所用的请求头，两侧必须保持一致
# （见 platform_gateway/tracing.py:TRACE_HEADER）。HTTP 头名大小写不敏感，
# Starlette 的 request.headers 已按小写归一。
TRACE_HEADER = "x-trace-id"

# 运行记录里任何单个字符串字段的上限（问句、SQL、报错信息）。
MAX_FIELD_CHARS = 2000

# 进程内保留的最近运行记录条数。
DEFAULT_MAX_RECORDS = 200

_current_trace_id: ContextVar[Optional[str]] = ContextVar("dwh_current_trace_id", default=None)


def new_trace_id() -> str:
    return uuid.uuid4().hex


def new_run_id() -> str:
    return f"run-{uuid.uuid4().hex[:16]}"


def normalize_trace_id(value: Any) -> Optional[str]:
    """把外部传入的追溯 ID 规整为可安全回显的值；不合规返回 ``None``。

    不合规包括：非字符串、超长、含空白或控制字符（防 header 注入与日志污染）。
    """

    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or not _TRACE_ID_RE.match(candidate):
        return None
    return candidate


def resolve_trace_id(value: Any = None) -> str:
    """取得本次调用要用的 trace_id：显式入参 > 上下文变量 > 新签发。"""

    resolved = normalize_trace_id(value)
    if resolved:
        return resolved
    resolved = normalize_trace_id(_current_trace_id.get())
    if resolved:
        return resolved
    return new_trace_id()


def current_trace_id() -> Optional[str]:
    """当前上下文里绑定的 trace_id（没有绑定则为 ``None``）。"""

    return _current_trace_id.get()


@contextmanager
def trace_scope(trace_id: Any) -> Iterator[str]:
    """把 trace_id 绑定到当前上下文。

    供中间件/入口层使用：绑定之后，链路里任何没有显式接收 trace_id 的环节都能
    通过 :func:`resolve_trace_id` 取到同一个值，无需改动函数签名。
    """

    resolved = resolve_trace_id(trace_id)
    token = _current_trace_id.set(resolved)
    try:
        yield resolved
    finally:
        _current_trace_id.reset(token)


def _clip(value: Any, limit: int = MAX_FIELD_CHARS) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…[truncated]"
    return value


class StageTiming:
    """运行记录里的一个阶段计时条目。"""

    __slots__ = ("seq", "name", "started_at", "duration_ms", "ok", "error", "meta")

    def __init__(self, seq: int, name: str, meta: Optional[Dict[str, Any]] = None):
        self.seq = seq
        self.name = name
        self.started_at = time.time()
        self.duration_ms: float = 0.0
        self.ok: bool = True
        self.error: Optional[str] = None
        self.meta: Dict[str, Any] = dict(meta or {})

    def to_dict(self) -> Dict[str, Any]:
        item: Dict[str, Any] = {
            "seq": self.seq,
            "stage": self.name,
            "duration_ms": round(self.duration_ms, 3),
            "ok": self.ok,
        }
        if self.error:
            item["error"] = _clip(self.error, 300)
        if self.meta:
            item["meta"] = self.meta
        return item


class RunRecord:
    """一次问数执行的可追溯运行记录：问题、DSL、SQL、分阶段耗时、网闸、状态。"""

    def __init__(
        self,
        *,
        trace_id: Optional[str] = None,
        run_id: Optional[str] = None,
        question: str = "",
        user: str = "anonymous",
        role: str = "user",
        dialect: str = "",
    ):
        self.trace_id = resolve_trace_id(trace_id)
        self.run_id = run_id or new_run_id()
        self.question = _clip(question)
        self.user = _clip(user, 120)
        self.role = _clip(role, 60)
        self.dialect = _clip(dialect, 60)
        self.started_at = time.time()
        self.started_perf = time.perf_counter()
        self.total_ms: float = 0.0
        self.status: str = "running"
        self.error: Optional[str] = None
        self.dsl: Optional[Dict[str, Any]] = None
        self.sql: Optional[str] = None
        self.rewritten_question: Optional[str] = None
        self.row_count: Optional[int] = None
        self.retry_count: int = 0
        self.cache_hit: Optional[str] = None
        self.stages: List[StageTiming] = []
        self.guardrails: List[Dict[str, Any]] = []
        self.notes: Dict[str, Any] = {}
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- 计时
    @contextmanager
    def stage(self, name: str, **meta: Any) -> Iterator[StageTiming]:
        """给一个阶段计时。异常照常向外抛，但耗时与失败状态一定会被记下来。"""

        entry = self._open_stage(name, meta)
        start = time.perf_counter()
        try:
            yield entry
        except BaseException as exc:  # noqa: BLE001 - 记录后原样抛出
            entry.ok = False
            entry.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            entry.duration_ms = (time.perf_counter() - start) * 1000.0

    def _open_stage(self, name: str, meta: Optional[Dict[str, Any]] = None) -> StageTiming:
        with self._lock:
            entry = StageTiming(seq=len(self.stages) + 1, name=name, meta=meta)
            self.stages.append(entry)
            return entry

    def add_stage(self, name: str, duration_ms: float, ok: bool = True, **meta: Any) -> StageTiming:
        """登记一个已经量好的阶段耗时（用于无法包成上下文管理器的场景）。"""

        entry = self._open_stage(name, meta)
        entry.duration_ms = float(duration_ms)
        entry.ok = ok
        return entry

    def stage_ms(self, name: str) -> float:
        """同名阶段的耗时合计（重试会产生多条同名阶段）。"""

        return round(sum(item.duration_ms for item in self.stages if item.name == name), 3)

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.started_perf) * 1000.0

    # ---------------------------------------------------------------- 记录
    def set_dsl(self, dsl: Optional[Dict[str, Any]]) -> None:
        # 存浅拷贝：记录是执行现场的快照，调用方后续改写 DSL 不该倒灌进来。
        self.dsl = dict(dsl) if isinstance(dsl, dict) else None

    def set_sql(self, sql: Optional[str]) -> None:
        self.sql = _clip(sql) if isinstance(sql, str) else None

    def add_guardrail(self, layer: str, outcome: str, message: str = "", **meta: Any) -> None:
        """记录一次网闸判定。``outcome``: pass / block / error。"""

        item: Dict[str, Any] = {"layer": layer, "outcome": outcome}
        if message:
            item["message"] = _clip(message, 500)
        if meta:
            item.update(meta)
        with self._lock:
            self.guardrails.append(item)

    def note(self, key: str, value: Any) -> None:
        self.notes[key] = _clip(value, 500)

    def finish(self, status: str, error: Optional[str] = None) -> "RunRecord":
        self.status = status
        self.error = _clip(error, 500) if error else None
        self.total_ms = self.elapsed_ms()
        return self

    # ---------------------------------------------------------------- 输出
    def to_dict(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "question": self.question,
            "rewritten_question": self.rewritten_question,
            "user": self.user,
            "role": self.role,
            "dialect": self.dialect,
            "status": self.status,
            "error": self.error,
            "started_at": self.started_at,
            "total_ms": round(self.total_ms or self.elapsed_ms(), 3),
            "retry_count": self.retry_count,
            "row_count": self.row_count,
            "cache_hit": self.cache_hit,
            "dsl": self.dsl,
            "sql": self.sql,
            "guardrails": list(self.guardrails),
            "stages": [item.to_dict() for item in self.stages],
            "notes": dict(self.notes),
        }

    def summary(self) -> Dict[str, Any]:
        """给 API 响应用的精简视图：追溯 ID + 分阶段耗时 + 状态。"""

        return {
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "status": self.status,
            "total_ms": round(self.total_ms or self.elapsed_ms(), 3),
            "retry_count": self.retry_count,
            "stages": [item.to_dict() for item in self.stages],
            "guardrails": list(self.guardrails),
        }


class RunTraceRegistry:
    """进程内有界的运行记录表，可按 run_id / trace_id 回查。"""

    def __init__(self, max_records: int = DEFAULT_MAX_RECORDS):
        self.max_records = max_records
        self._records: Deque[RunRecord] = deque(maxlen=max_records)
        self._lock = threading.Lock()

    def start_run(self, **kwargs: Any) -> RunRecord:
        """新开一条运行记录并立刻登记（未完成的运行也查得到）。"""

        record = RunRecord(**kwargs)
        with self._lock:
            self._records.append(record)
        return record

    def record(self, record: RunRecord) -> RunRecord:
        with self._lock:
            if record not in self._records:
                self._records.append(record)
        return record

    def get(self, run_id: str) -> Optional[RunRecord]:
        with self._lock:
            for item in reversed(self._records):
                if item.run_id == run_id:
                    return item
        return None

    def for_trace(self, trace_id: str) -> List[RunRecord]:
        with self._lock:
            return [item for item in self._records if item.trace_id == trace_id]

    def recent(self, limit: int = 20) -> List[RunRecord]:
        bounded = max(1, min(int(limit), self.max_records))
        with self._lock:
            return list(self._records)[-bounded:][::-1]

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)


run_trace_registry = RunTraceRegistry()
