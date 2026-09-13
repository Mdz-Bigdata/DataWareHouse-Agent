"""入湖子系统的异常类型。

分层原则：
  · ComplianceViolation —— 合规问题，永远不可重试、不可带标放行（[a8] 第六章：
    「脱敏标记缺失是最高优先级的 P0——这不是数据质量问题，而是合规问题」）；
  · GateRejected       —— 质量门禁拒绝，走五步异常闭环（拦截 → 隔离 → 告警 → 分流处置 → 复验）；
  · SinkError / ChannelError —— 工程故障，可重试。
"""

from __future__ import annotations


class IngestError(Exception):
    """入湖子系统的异常基类。"""


class ComplianceViolation(IngestError):
    """合规红线违规：五步链路顺序错误、脱敏标记缺失、合规云架构约束不满足等。

    [a8]「合规不是靠流程承诺，而是靠架构边界」——所以这类问题在代码里抛异常，
    而不是记一条 WARNING 放行。
    """

    def __init__(self, message: str, *, violations: list[str] | None = None) -> None:
        super().__init__(message)
        self.violations = violations or []

    def __str__(self) -> str:  # pragma: no cover - 仅用于日志
        base = super().__str__()
        if not self.violations:
            return base
        return base + "\n  - " + "\n  - ".join(self.violations)


class GateRejected(IngestError):
    """质量门禁判定拒绝入湖。携带门禁结论供隔离表落库。"""

    def __init__(self, message: str, outcome: object | None = None) -> None:
        super().__init__(message)
        self.outcome = outcome


class ChannelError(IngestError):
    """通道读取失败：CDC 连接、Kafka 消费、对象存储访问等。"""


class SinkError(IngestError):
    """写入 ODS 失败：Flink SQL Gateway 提交失败、本地落盘失败等。"""


class MissingDependency(IngestError):
    """可选客户端库未安装。

    外部客户端（kafka-python / boto3 等）一律延迟 import，保证
    ``import adas_lakehouse.ingest`` 在裸环境下不炸——只有真正用到时才要求安装。
    """

    def __init__(self, package: str, purpose: str) -> None:
        super().__init__(f"{purpose} 需要可选依赖 {package!r}，请先安装：pip install {package}")
        self.package = package
        self.purpose = purpose
