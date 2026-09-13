"""把 ``adas_lakehouse.quality`` 的通用六维门禁挂进三条入湖通道。

为什么需要这个模块：[a5] 第六章的收口句是「三条通道有一个共同终点：所有入湖数据
统一经数据质量门禁校验后写入 ODS 层。**通道可以分，门禁不能分**」。本包里
``gate.OssComplianceGate`` 只实现了 [a8] 第六章点名的 OSS 通道**四项专属检查**；
六维检查框架（完整性 / 准确性 / 一致性 / 唯一性 / 有效性 / 及时性）、P0~P3 分级、
规则灰度发布这些三通道通用的部分，实现在 ``adas_lakehouse.quality`` 子系统里。

两边各有一半，中间必须有一根真实的接线，否则「门禁不能分」就只是一句注释：
``IngestChannel(generic_gate=...)`` 是插口，本模块是插头。

用法::

    from adas_lakehouse.ingest import build_default_channels
    from adas_lakehouse.ingest.quality_bridge import unified_gate_hook

    channels = build_default_channels(generic_gate=unified_gate_hook())
    # 三条通道共用同一个 QualityGate 实例：规则一套、灰度状态一套、指标一套

语义对齐（本模块唯一的技术活）：

======================  ==========================  ==============================
quality 侧              ingest 侧                   依据
======================  ==========================  ==============================
severity=ERROR 的命中   ``CheckStatus.FAIL``        [a5]「ERROR 拒绝」
severity=WARNING 的命中 ``CheckStatus.SKIPPED``     [a5]「WARNING 带标放行」
灰度影子命中            不映射（只观测不处置）      [a5] 第八章「规则本身灰度发布」
issue_level P0~P3       ``gate.Severity`` P0~P3     [a5]「按 P0~P3 分级告警」
QualityDimension.name_cn ``GateCheck.dimension``    [a5] 六维检查框架
======================  ==========================  ==============================

合并规则见 ``gate.merge_outcomes``：两边结论取**更严**的一侧，挂上通用门禁只会让
准入更紧，不会把通道专属规则已经拦下的数据放行。

``adas_lakehouse.quality`` 一律**延迟 import**：它在 import 期会装载整套内置规则，
而 ``import adas_lakehouse.ingest`` 不该为此付代价，也不该因为规则集的加载顺序出问题。
"""

from __future__ import annotations

from collections.abc import Callable, Container, Mapping, Sequence
from typing import Any

from .channels import ChannelKind
from .gate import CheckResult, CheckStatus, GateCheck, Severity

__all__ = [
    "COMPLIANCE_RULE_IDS",
    "CHANNEL_MAP",
    "quality_channel_of",
    "hits_to_check_results",
    "unified_gate_hook",
]

#: 归「合规问题」而非「数据质量问题」的规则。[a8] 第六章：「注意脱敏标记缺失是最高
#: 优先级的 P0——**这不是数据质量问题，而是合规问题**，门禁在这里承担了合规的最后
#: 核验职责。」命中它的记录在五步异常闭环里不得复验放行（见 ``gate.AnomalyClosedLoop.recheck``），
#: 必须退回合规云补做双脱敏。
COMPLIANCE_RULE_IDS: frozenset[str] = frozenset({"QG-OSS-001-desensitization-flags"})

#: 入湖通道 → quality 侧通道枚举的名字对应。两边都是 [a5] 第六章那三条通道，
#: 只是各自用了自己的枚举；这里用**值的字符串**对应，避免 import 期依赖 quality。
#: 取值来自 ``gate.ISSUE_CHANNEL_CODES``——隔离表 ``ods_quality_issue.source_channel``
#: 写的也是这一套字面量，一个通道在两边只能有一个名字。
CHANNEL_MAP: dict[ChannelKind, str] = {kind: kind.issue_code for kind in ChannelKind}


def quality_channel_of(kind: ChannelKind) -> Any:
    """``ingest.ChannelKind`` → ``quality.severity.Channel``。"""
    from ..quality import Channel

    return Channel(CHANNEL_MAP[kind])


def hits_to_check_results(
    hits: Sequence[Any],
    *,
    compliance_rule_ids: Container[str] = COMPLIANCE_RULE_IDS,
) -> list[CheckResult]:
    """``quality.RuleHit`` → ``ingest.CheckResult``。

    ERROR 命中映射成 FAIL（拒绝入湖），WARNING 命中映射成 SKIPPED（带标放行）——
    后者在 ``gate.decide()`` 里的效果正是 ``ACCEPT_WITH_WARNING``，与 [a5] 的
    「ERROR 拒绝、WARNING 带标放行」严丝合缝。不做别的加工：分级、维度、修复动作
    都由规则自己带着，本函数只换一层外壳。
    """
    from ..quality import Severity as QSeverity

    results: list[CheckResult] = []
    for hit in hits:
        if getattr(hit, "shadow", False):
            # 灰度影子命中只观测不处置，不参与处置判定
            continue
        status = CheckStatus.FAIL if hit.severity is QSeverity.ERROR else CheckStatus.SKIPPED
        check = GateCheck(
            code=hit.rule_id,
            name=hit.message,
            description=hit.detail or hit.message,
            severity=Severity(hit.issue_level.value),
            dimension=hit.dimension.value.name_cn,
            is_compliance=hit.rule_id in compliance_rule_ids,
        )
        reason = f"{hit.rule_id}: {hit.message}"
        if hit.detail:
            reason += f"（{hit.detail}）"
        results.append(CheckResult(check, status, (reason,)))
    return results


def unified_gate_hook(
    kind: ChannelKind | None = None,
    *,
    table: str = "",
    gate: Any = None,
    isolate: bool = False,
    compliance_rule_ids: Container[str] = COMPLIANCE_RULE_IDS,
) -> Callable[[Mapping[str, Any]], list[CheckResult]]:
    """造一个可直接传给 ``IngestChannel(generic_gate=...)`` 的通用门禁钩子。

    Args:
        kind: 通道类型，决定按哪条通道的分源规则来查（[a5] 第八章：「CDC 通道查流程
            合规、Kafka 通道查时空合理、OSS 通道查物理完整与脱敏标记」）。
            不传则用 ``Channel.COMMON``，只跑三通道通用规则。
        table: 目标 ODS 表名。不传时从行里的 ``_target_table`` 取；两者都没有就按
            OSS 通道的 ``ods_data_file_meta`` 兜底——规则是按表注册的，表名错了
            等于一条规则都不跑，所以这里宁可显式传。
        gate: 复用已有的 ``quality.QualityGate``（想让三条通道共享同一份灰度状态与指标时
            传它）。不传则在首次调用时新建一个装载内置规则集的实例。类型写成 ``Any`` 而
            不是 ``QualityGate``，是为了连 ``if TYPE_CHECKING:`` 形式的跨子系统 import
            都不写——本模块对 quality 的依赖必须是纯运行期的、可缺省的。
        isolate: 命中拒绝规则时，是否**同时**由 quality 侧写自己的隔离表。缺省 False：
            入湖侧的五步异常闭环（``gate.AnomalyClosedLoop``）已经负责「拦截 → 隔离 →
            告警」，两边都写会让同一条脏数据在隔离表里出现两次。想改用 quality 的
            ``ClosedLoop`` 做后两步（分流处置 / 复验重入湖）时再置 True。

    Returns:
        ``(row) -> list[CheckResult]``，与 ``IngestChannel.generic_gate`` 的签名一致。
    """
    channel = quality_channel_of(kind) if kind is not None else None
    fallback_table = table or "ods_data_file_meta"

    def hook(row: Mapping[str, Any]) -> list[CheckResult]:
        from ..quality import Channel, QualityGate

        nonlocal gate
        if gate is None:
            gate = QualityGate()
        target = table or str(row.get("_target_table") or fallback_table)
        decision = gate.check(
            target,
            row,
            channel=channel if channel is not None else Channel.COMMON,
            isolate=isolate,
        )
        return hits_to_check_results(decision.hits, compliance_rule_ids=compliance_rule_ids)

    return hook
