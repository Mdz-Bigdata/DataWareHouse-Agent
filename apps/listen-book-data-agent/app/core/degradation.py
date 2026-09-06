"""Phase 4.2：基础设施降级判断与提示。

区分"SQL 语法/业务错误"（可重试修复）与"基础设施故障"（不可恢复，需降级）。
基础设施故障包括：连接超时、连接拒绝、连接池耗尽、网络不可达等。
"""

from __future__ import annotations

# 判定为基础设施故障的异常类型名片段（按异常类名或模块名匹配）
# 覆盖 asyncmy/asyncpg/SQLAlchemy/通用 socket 的连接类异常
_INFRA_FAILURE_MARKERS = (
    "OperationalError",
    "InterfaceError",
    "ConnectionError",
    "ConnectionRefusedError",
    "ConnectionResetError",
    "ConnectionAbortedError",
    "TimeoutError",
    "TimeoutErrorError",
    "PoolError",
    "PoolExhaustedError",
    "CannotConnectError",
)

# 错误消息中判定为基础设施故障的关键词
_INFRA_MESSAGE_KEYWORDS = (
    "can't connect",
    "cannot connect",
    "connection refused",
    "connection reset",
    "connection closed",
    "connection timed out",
    "too many connections",
    "server gone away",
    "lost connection",
    "server has gone away",
    "unreachable",
    "econnrefused",
    "econnreset",
    "etimedout",
    "host is unreachable",
    "no route to host",
    "pool exhausted",
    "access denied",
    "authentication failed",
    "permission denied",
)

_SQL_SEMANTIC_MESSAGE_KEYWORDS = (
    "syntax error",
    "you have an error in your sql syntax",
    "unknown column",
    "unknown table",
    "doesn't exist",
    "does not exist",
    "no such column",
    "no such table",
    "ambiguous column",
    "invalid identifier",
    "not in group by",
    "must appear in the group by",
    "function does not exist",
    "operator does not exist",
    "division by zero",
)

_SQL_SEMANTIC_ERROR_CODES = {
    1052,  # ambiguous column
    1054,  # unknown column
    1055,  # invalid GROUP BY
    1064,  # syntax error
    1146,  # table does not exist
    1305,  # function does not exist
    1366,  # invalid value/type
}


class InfrastructureFailure(RuntimeError):
    """A sanitized non-retryable pipeline failure caused by external infrastructure."""

    def __init__(self, message: str, *, stage: str, reason: str):
        super().__init__(message)
        self.stage = stage
        self.reason = reason


def is_infra_failure(exc: BaseException) -> bool:
    """判断异常是否为基础设施故障（不可通过 SQL 修复恢复）。

    判定逻辑：
    1. 异常类名匹配已知的基础设施异常类型
    2. 异常消息包含连接类故障关键词
    命中任一即判定为基础设施故障。
    """

    if is_sql_semantic_failure(exc):
        return False
    for current in _exception_chain(exc):
        exc_name = type(current).__name__
        if any(marker in exc_name for marker in _INFRA_FAILURE_MARKERS):
            return True
        message = str(current).lower()
        if any(keyword in message for keyword in _INFRA_MESSAGE_KEYWORDS):
            return True
    return False


def is_sql_semantic_failure(exc: BaseException) -> bool:
    """Recognize database/SQL errors that can be repaired by the SQL Refiner."""

    for current in _exception_chain(exc):
        code = _exception_error_code(current)
        if code in _SQL_SEMANTIC_ERROR_CODES:
            return True
        message = str(current).lower()
        if any(keyword in message for keyword in _SQL_SEMANTIC_MESSAGE_KEYWORDS):
            return True
    return False


def _exception_chain(exc: BaseException):
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        original = getattr(current, "orig", None)
        current = original if isinstance(original, BaseException) else current.__cause__


def _exception_error_code(exc: BaseException) -> int | None:
    args = getattr(exc, "args", ())
    if args and isinstance(args[0], int):
        return args[0]
    return None


def degradation_message(exc: BaseException) -> str:
    """生成给用户看的降级提示（不泄露内部细节）。"""

    return "数据仓库暂时不可用，请稍后重试。如问题持续，请联系管理员检查数据源连接。"
