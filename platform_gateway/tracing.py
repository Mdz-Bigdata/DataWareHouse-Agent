from __future__ import annotations

"""网关侧的全链路追溯：签发/校验 trace_id，并记录每一段代理调用的耗时。

trace_id 是链路的第一级 ID：网关签发后通过 ``x-trace-id`` 透传给子系统，
子系统（如 backend 的 ask_agent）在同一个 trace 下再签发自己的 run_id。

这里只记最小必要信息（子系统、方法、路径、状态码、耗时），不记请求体、
不记查询串——查询串里可能带用户问句等敏感内容。记录有界且只在内存里。
"""

import re
import time
import uuid
from collections import deque
from typing import Deque, Mapping

TRACE_HEADER = "x-trace-id"

# 入站 trace_id 是外部输入：只接受这个字符集与长度，其余一律重新签发，
# 避免把空白/控制字符/超长值回显进响应头或日志。
_TRACE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")

MAX_PATH_CHARS = 200
DEFAULT_MAX_RECORDS = 200


def new_trace_id() -> str:
    return uuid.uuid4().hex


def normalize_trace_id(value: object) -> str | None:
    """把外部传入的 trace_id 规整为可安全透传的值；不合规返回 ``None``。"""

    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or not _TRACE_ID_RE.match(candidate):
        return None
    return candidate


def resolve_trace_id(headers: Mapping[str, str]) -> str:
    """取入站 ``x-trace-id``；缺失或不合规则就地签发一个新的。"""

    return normalize_trace_id(headers.get(TRACE_HEADER)) or new_trace_id()


class GatewayTraceLog:
    """进程内有界的网关调用记录，可按 trace_id 回查这条链路的网关段。"""

    def __init__(self, max_records: int = DEFAULT_MAX_RECORDS) -> None:
        self.max_records = max_records
        self._records: Deque[dict[str, object]] = deque(maxlen=max_records)

    def record(
        self,
        *,
        trace_id: str,
        subsystem: str,
        method: str,
        path: str,
        status_code: int | None,
        elapsed_ms: float,
        error: str = "",
    ) -> dict[str, object]:
        item: dict[str, object] = {
            "trace_id": trace_id,
            "subsystem": subsystem,
            "method": method,
            # 只留路径，查询串可能带用户问句等敏感内容，不入记录。
            "path": path[:MAX_PATH_CHARS],
            "status_code": status_code,
            "elapsed_ms": round(elapsed_ms, 3),
            "at": time.time(),
        }
        if error:
            item["error"] = error[:120]
        self._records.append(item)
        return item

    def recent(self, limit: int = 50) -> list[dict[str, object]]:
        bounded = max(1, min(int(limit), self.max_records))
        return list(self._records)[-bounded:][::-1]

    def for_trace(self, trace_id: str) -> list[dict[str, object]]:
        return [item for item in self._records if item["trace_id"] == trace_id]

    def clear(self) -> None:
        self._records.clear()

    def __len__(self) -> int:
        return len(self._records)


trace_log = GatewayTraceLog()
