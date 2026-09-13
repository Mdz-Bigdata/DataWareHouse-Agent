"""分级告警：P0 电话 + 钉钉 · P1 钉钉 + 工单 · P2 日报 · P3 周报。

原文第五章第 ③ 步：「分级告警：P0 电话 + 钉钉 · P1 钉钉 + 工单 · P2 日报 · P3 周报，
通知数据 owner + 平台值班」，配套第五章的四档响应 SLA 表（见 severity.LEVEL_POLICIES）。

设计要点：**避免告警疲劳**——P0/P1 即时触达，P2/P3 进汇总队列，
分别由 :meth:`AlertRouter.flush_daily` / :meth:`AlertRouter.flush_weekly` 批量发出；
P3 连续两周超标自动升级 P2（原文 P3 行「连续两周超标升级 P2」）。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from .isolation import IssueRecord
from .severity import LEVEL_POLICIES, IssueLevel
from .thresholds import P3_CONSECUTIVE_WEEKS_TO_ESCALATE

__all__ = [
    "Alert",
    "AlertSink",
    "LoggingAlertSink",
    "CollectingAlertSink",
    "WebhookAlertSink",
    "AlertRouter",
    "PLATFORM_ON_DUTY",
]

_log = logging.getLogger("adas.quality.alerting")

#: ⚠️ 原文未明确，本项目设计：原文说「通知数据 owner + 平台值班」，
#: 平台值班的具体值班组名由部署方配置，这里给一个可覆盖的默认标识。
PLATFORM_ON_DUTY = "platform-oncall"


@dataclass(frozen=True, slots=True)
class Alert:
    """一条告警。digest=True 表示它是日报/周报的汇总条目而非即时告警。"""

    level: IssueLevel
    title: str
    body: str
    channels: tuple[str, ...]
    targets: tuple[str, ...]
    issue_ids: tuple[str, ...]
    created_at: datetime
    response_due_at: datetime | None = None
    digest: bool = False
    escalated_from: IssueLevel | None = None

    def render(self) -> str:
        """渲染成可直接丢进钉钉/工单的纯文本。"""
        lines = [
            f"【{self.level.value} {self.level.policy.name_cn}】{self.title}",
            f"触达: {' + '.join(self.channels)}｜通知: {', '.join(self.targets)}",
            f"响应要求: {self.level.policy.response_requirement_cn}",
        ]
        if self.response_due_at:
            lines.append(f"响应截止: {self.response_due_at.isoformat(sep=' ', timespec='seconds')}")
        if self.escalated_from:
            lines.append(f"⚠️ 由 {self.escalated_from.value} 升级而来")
        lines.append(self.body)
        if self.issue_ids:
            head = ", ".join(self.issue_ids[:10])
            more = f" 等 {len(self.issue_ids)} 条" if len(self.issue_ids) > 10 else ""
            lines.append(f"隔离记录: {head}{more}")
        return "\n".join(lines)


class AlertSink(Protocol):
    """告警出口。实现方负责真正触达（钉钉机器人、工单系统、电话网关）。"""

    def send(self, alert: Alert) -> bool:  # pragma: no cover - 协议声明
        ...


class LoggingAlertSink:
    """默认出口：打日志。

    生产必须换成真实通道；留它做默认值是为了「没配告警通道时门禁依然能跑」，
    而不是悄悄把告警吞掉。
    """

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or _log

    def send(self, alert: Alert) -> bool:
        level = (
            logging.CRITICAL
            if alert.level is IssueLevel.P0
            else (logging.ERROR if alert.level is IssueLevel.P1 else logging.WARNING)
        )
        self._logger.log(level, "%s", alert.render())
        return True


class CollectingAlertSink:
    """把告警收进内存列表，供测试与干跑校验。"""

    def __init__(self) -> None:
        self.alerts: list[Alert] = []

    def send(self, alert: Alert) -> bool:
        self.alerts.append(alert)
        return True

    def clear(self) -> None:
        self.alerts.clear()


class WebhookAlertSink:
    """通用 Webhook 出口（钉钉机器人 / 工单系统均可）。

    只用标准库 urllib，保证没装任何第三方 HTTP 客户端时本模块也能 import。
    发送失败不抛异常，返回 False 并记日志——告警通道故障不能反过来打挂入湖链路。
    """

    def __init__(self, webhook_url: str, *, timeout_seconds: float = 5.0) -> None:
        self.webhook_url = webhook_url
        self.timeout_seconds = timeout_seconds

    def send(self, alert: Alert) -> bool:
        import json
        import urllib.error
        import urllib.request

        payload = json.dumps(
            {"msgtype": "text", "text": {"content": alert.render()}}, ensure_ascii=False
        ).encode("utf-8")
        req = urllib.request.Request(
            self.webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                return 200 <= resp.status < 300
        except (urllib.error.URLError, OSError) as exc:
            _log.warning("告警 Webhook 发送失败（%s）: %s", self.webhook_url, exc)
            return False


@dataclass(slots=True)
class AlertRouter:
    """按异常等级路由告警。

    :param sinks: 等级 → 出口列表。缺省全部走 default_sink。
    :param default_sink: 兜底出口，默认打日志。
    :param on_duty: 平台值班标识，与数据 owner 一起作为通知对象。
    """

    default_sink: Any = field(default_factory=LoggingAlertSink)
    sinks: dict[IssueLevel, list[Any]] = field(default_factory=dict)
    on_duty: str = PLATFORM_ON_DUTY
    #: P2/P3 的汇总队列（原文：P2 日报、P3 周报）
    _daily_queue: list[IssueRecord] = field(default_factory=list, init=False)
    _weekly_queue: list[IssueRecord] = field(default_factory=list, init=False)
    #: 「连续两周超标升级 P2」的周计数，键是规则 ID
    _weekly_breach_streak: dict[str, int] = field(default_factory=dict, init=False)

    # ---- 即时告警 ----

    def notify(
        self, issue: IssueRecord, *, escalated_from: IssueLevel | None = None
    ) -> Alert | None:
        """按等级发一条告警。

        P0/P1 即时触达；P2 进日报队列、P3 进周报队列，返回 None 表示「已入队，未即时发」。
        """
        level = issue.issue_level
        if level in (IssueLevel.P2, IssueLevel.P3) and escalated_from is None:
            (self._daily_queue if level is IssueLevel.P2 else self._weekly_queue).append(issue)
            return None
        alert = self._build(level, [issue], escalated_from=escalated_from)
        self._emit(alert)
        return alert

    def notify_many(self, issues: Sequence[IssueRecord]) -> list[Alert]:
        """批量告警：同等级合并成一条，避免同一批次刷屏。"""
        grouped: dict[IssueLevel, list[IssueRecord]] = {}
        for issue in issues:
            grouped.setdefault(issue.issue_level, []).append(issue)
        alerts: list[Alert] = []
        for level, group in sorted(grouped.items(), key=lambda kv: kv[0].value):
            if level in (IssueLevel.P2, IssueLevel.P3):
                (self._daily_queue if level is IssueLevel.P2 else self._weekly_queue).extend(group)
                continue
            alert = self._build(level, group)
            self._emit(alert)
            alerts.append(alert)
        return alerts

    # ---- 汇总告警 ----

    def flush_daily(self, at: datetime | None = None) -> Alert | None:
        """发 P2 日报（原文：P2 日报汇总，3 个工作日内闭环）。"""
        if not self._daily_queue:
            return None
        alert = self._build(IssueLevel.P2, self._daily_queue, digest=True, at=at)
        self._emit(alert)
        self._daily_queue.clear()
        return alert

    def flush_weekly(self, at: datetime | None = None) -> Alert | None:
        """发 P3 周报，并推进「连续两周超标升级 P2」的计数。"""
        if not self._weekly_queue:
            # 本周无超标：所有规则的连续计数清零
            self._weekly_breach_streak.clear()
            return None
        breached: set[str] = set()
        for issue in self._weekly_queue:
            breached.update(issue.rule_ids)
        for rule_id in list(self._weekly_breach_streak):
            if rule_id not in breached:
                del self._weekly_breach_streak[rule_id]
        escalating: list[str] = []
        for rule_id in breached:
            streak = self._weekly_breach_streak.get(rule_id, 0) + 1
            self._weekly_breach_streak[rule_id] = streak
            if streak >= P3_CONSECUTIVE_WEEKS_TO_ESCALATE:
                escalating.append(rule_id)

        alert = self._build(IssueLevel.P3, self._weekly_queue, digest=True, at=at)
        self._emit(alert)
        self._weekly_queue.clear()

        if escalating:
            # 连续两周超标 → 升级 P2，并重置计数避免每周重复升级。
            # 目标等级取 IssueLevel.escalate()（P3 的上一档恰好是 P2），而不是写死 P2——
            # 「升一档」这件事只有等级枚举自己说了算，散在各处手写会各判各的。
            target = IssueLevel.P3.escalate()
            escalated = self._build(
                target,
                [],
                digest=True,
                at=at,
                extra_body=(
                    f"以下规则连续 {P3_CONSECUTIVE_WEEKS_TO_ESCALATE} 周超标，"
                    f"由 {IssueLevel.P3.value} 升级为 {target.value}："
                    f"{', '.join(sorted(escalating))}"
                ),
                escalated_from=IssueLevel.P3,
            )
            self._emit(escalated)
            for rule_id in escalating:
                self._weekly_breach_streak[rule_id] = 0
        return alert

    def pending_digest_counts(self) -> dict[str, int]:
        """待发汇总条数，进门禁自身的可观测指标。"""
        return {"daily_pending": len(self._daily_queue), "weekly_pending": len(self._weekly_queue)}

    # ---- 内部 ----

    def _build(
        self,
        level: IssueLevel,
        issues: Sequence[IssueRecord],
        *,
        digest: bool = False,
        at: datetime | None = None,
        extra_body: str = "",
        escalated_from: IssueLevel | None = None,
    ) -> Alert:
        policy = LEVEL_POLICIES[level]
        now = at or datetime.now()
        owners = {i.owner for i in issues if i.owner}
        targets = tuple(sorted(owners) + [self.on_duty])
        tables = sorted({i.source_table for i in issues})
        rules = sorted({r for i in issues for r in i.rule_ids})
        title = f"{'质量门禁汇总' if digest else '质量门禁拦截'}·{len(issues)} 条异常" + (
            f"·表 {', '.join(tables[:3])}" if tables else ""
        )
        body_lines = [
            f"命中规则: {', '.join(rules[:10]) if rules else '-'}",
            f"典型场景: {policy.typical_cases}",
        ]
        for issue in issues[:5]:
            body_lines.append(
                f"  - {issue.issue_id} [{issue.source_table}] {issue.message}"
                f"（{issue.dimension.name_cn}/{issue.severity.value}）"
            )
        if len(issues) > 5:
            body_lines.append(f"  ... 其余 {len(issues) - 5} 条见隔离表 ods_quality_issue")
        if extra_body:
            body_lines.append(extra_body)
        due = issues[0].response_due_at if issues else policy.response_due_at(now)
        return Alert(
            level=level,
            title=title,
            body="\n".join(body_lines),
            channels=policy.notify_channels,
            targets=targets or (self.on_duty,),
            issue_ids=tuple(i.issue_id for i in issues),
            created_at=now,
            response_due_at=due,
            digest=digest,
            escalated_from=escalated_from,
        )

    def _emit(self, alert: Alert) -> None:
        sinks: Iterable[Any] = self.sinks.get(alert.level) or [self.default_sink]
        for sink in sinks:
            try:
                sink.send(alert)
            except Exception as exc:  # noqa: BLE001 - 告警通道故障不能打挂入湖
                _log.warning("告警出口 %r 发送失败: %s", sink, exc)
