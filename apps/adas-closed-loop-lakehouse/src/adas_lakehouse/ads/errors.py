"""ADS 服务层异常体系。

分三类，对应三种排障路径：
  · 调用方错误（AdsQueryError 家族）—— 参数、表名、字段、算子不合法，改调用即可
  · 依赖不可用（BackendUnavailableError 家族）—— StarRocks / 图库 / 向量子系统没接上
  · 网关拒绝（GatewayError 家族）—— 认证、限流、路由

全部继承自 AdsError，业务平台可以只 catch 一个基类。
"""

from __future__ import annotations

__all__ = [
    "AdsError",
    "AdsQueryError",
    "UnknownTableError",
    "UnknownColumnError",
    "InvalidFilterError",
    "BackendUnavailableError",
    "StarRocksUnavailableError",
    "ServiceUnavailableError",
    "GatewayError",
    "AuthenticationError",
    "AuthorizationError",
    "RateLimitExceededError",
    "RouteNotFoundError",
]


class AdsError(Exception):
    """ADS 服务层所有异常的基类。"""


# --------------------------------------------------------------------------- 调用方错误


class AdsQueryError(AdsError):
    """查询构造或执行阶段的错误。"""


class UnknownTableError(AdsQueryError):
    """请求了一张不在 11 张 ADS 数据产品矩阵内的表。

    ADS 层「零 JOIN、开箱即用」的前提是出口收敛：服务层只允许查这 11 张表，
    其余表一律走服务层之外的即席查询路径（Paimon 外部表）。
    """


class UnknownColumnError(AdsQueryError):
    """请求的字段不在该 ADS 表的列清单里。

    字段白名单同时是 SQL 注入防线——标识符永远不拼用户输入，只从白名单里取。
    """


class InvalidFilterError(AdsQueryError):
    """过滤条件非法：算子不在白名单，或 IN/BETWEEN 的取值形态不对。"""


# --------------------------------------------------------------------------- 依赖不可用


class BackendUnavailableError(AdsError):
    """底层存储/服务依赖不可用。"""


class StarRocksUnavailableError(BackendUnavailableError):
    """连不上 StarRocks，或本机没装 MySQL 协议驱动。

    ADS 内表查询走 StarRocks 的 MySQL 协议端口（宿主机默认 18630，见 config.StarRocksConfig），
    需要 pymysql 或 mysql-connector-python 之一。驱动缺失不应该让 import 本模块炸掉，
    因此驱动是延迟 import 的，只有真正发起查询时才会抛出本异常。
    """


class ServiceUnavailableError(BackendUnavailableError):
    """依赖的兄弟子系统没有注入。

    典型是血缘图库（Neo4j，lineage 子系统）与向量检索（vector 子系统）——
    ADS 服务层只定义端口（Protocol），不自己实现，未注入时给出明确指引而不是静默降级。
    """


# --------------------------------------------------------------------------- 网关


class GatewayError(AdsError):
    """统一 API 网关层的错误。"""


class AuthenticationError(GatewayError):
    """令牌缺失或无效。"""


class AuthorizationError(GatewayError):
    """令牌有效，但该业务平台没有这个接口的访问范围。"""


class RateLimitExceededError(GatewayError):
    """超出该调用方的 QPS 配额。"""


class RouteNotFoundError(GatewayError):
    """请求路径没有匹配到任何已注册的业务服务接口。"""
