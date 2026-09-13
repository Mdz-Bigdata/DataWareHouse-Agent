"""四段式命名公式：{层级前缀}_{数据域}_{业务实体}_{粒度后缀}。

参考阿里巴巴 OneData 数仓标准，针对智驾场景适配。核心价值是「表名即文档」——
新人不翻数仓字典，看表名就知道 80% 的信息。

来源：系列二《数仓命名规范 + 11 数据域划分 + 分区策略全解》第一章。

⚠️ 源文偏差（已在 docs/source-deviations.md 登记）：
  文章给的实战示例 ``ads_badcase_root_cause_distribution`` 把评测域的域段写成
  ``badcase_`` 而非规范的 ``evaluation_``；另有若干 ADS 表（如 ads_hard_case_library、
  ads_data_asset_catalog）表名里根本没有域段。ADS 层面向应用命名，实践中确实会
  为了可读性牺牲域段。因此本模块的校验对 ADS 层放宽，域归属以 catalog.registry
  里的显式声明为准，解析仅作辅助与告警。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .domains import DataDomain, Layer

#: 标准粒度后缀 -> (语义, 惯用层级)。
#: [a12] 第一章第四段标题写「10 种标准后缀」，但表里有 3 行各塞了两个后缀
#: （`_chain/_trace`、`_info/_config`、`_log/_event`），逐个数是 **13 个**。
#: 校验需要的是后缀本身而不是表格行数，故收 13 个——见 source-deviations A-7。
GRANULARITY_SUFFIXES: dict[str, tuple[str, tuple[Layer, ...]]] = {
    "detail": ("明细表", (Layer.DWD,)),
    "daily": ("日粒度指标", (Layer.DWS,)),
    "statistics": ("多维度统计", (Layer.DWS,)),
    "summary": ("汇总指标", (Layer.DWS, Layer.ADS)),
    "relation": ("关联关系表", (Layer.DWD,)),
    "chain": ("链路", (Layer.DWD,)),
    "trace": ("追溯", (Layer.DWD,)),
    "dashboard": ("大屏看板", (Layer.ADS,)),
    "analysis": ("分析结果", (Layer.ADS,)),
    "info": ("基础信息", (Layer.ODS,)),
    "config": ("配置", (Layer.ODS,)),
    "log": ("日志", (Layer.ODS,)),
    "event": ("事件", (Layer.ODS,)),
}

#: 域段别名：源文表名里出现、且语义上无歧义地指向某个数据域的词根。
#: 只收录「这个词根只可能属于这一个域」的情况——靠猜的一律不放，
#: 域归属以 catalog.registry 的显式声明为准，解析只用于矛盾检测。
DOMAIN_ALIASES: dict[str, DataDomain] = {
    "badcase": DataDomain.EVALUATION,  # 源文实战示例 ads_badcase_root_cause_distribution
    "annotation": DataDomain.PRODUCTION,
    "qc": DataDomain.PRODUCTION,
    "argo": DataDomain.PRODUCTION,
    "ota": DataDomain.DEPLOYMENT,
    "shadow": DataDomain.TRIGGER,
}

_TABLE_NAME_RE = re.compile(r"^(ods|dwd|dws|ads)_[a-z][a-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class ParsedName:
    """表名四段解析结果。

    domain 可能为 None —— ADS 层允许无域段（见模块 docstring）。
    suffix 可能为 None —— ODS 层多数表不带标准粒度后缀（如 ods_collect_task）。
    """

    raw: str
    layer: Layer
    domain: DataDomain | None
    entity: str
    suffix: str | None

    @property
    def is_canonical(self) -> bool:
        """是否严格符合四段式（层级 + 规范域前缀 + 实体 + 标准后缀）。"""
        return self.domain is not None and self.suffix is not None


def parse(table_name: str) -> ParsedName:
    """解析表名。不合法的层级前缀会抛 ValueError；域/后缀缺失只是留 None。"""
    if not _TABLE_NAME_RE.match(table_name):
        raise ValueError(f"表名不合法: {table_name!r}；要求 ^(ods|dwd|dws|ads)_[a-z][a-z0-9_]*$")
    layer_token, body = table_name.split("_", 1)
    layer = Layer(layer_token)

    domain = None
    rest = body
    # 最长前缀匹配：closed_loop_ 必须优先于任何更短的候选
    for d in sorted(DataDomain, key=lambda x: len(x.prefix), reverse=True):
        if body.startswith(d.prefix):
            domain, rest = d, body[len(d.prefix) :]
            break
    if domain is None:
        head = body.split("_", 1)[0]
        if head in DOMAIN_ALIASES:
            domain = DOMAIN_ALIASES[head]
            rest = body[len(head) + 1 :] if "_" in body else ""

    suffix = None
    for suf in GRANULARITY_SUFFIXES:
        if rest.endswith("_" + suf) or rest == suf:
            suffix = suf
            rest = rest[: -(len(suf) + 1)] if rest != suf else ""
            break

    return ParsedName(table_name, layer, domain, rest or body, suffix)


def build(layer: Layer, domain: DataDomain, entity: str, suffix: str | None = None) -> str:
    """按四段式拼表名。suffix 必须是 10 种标准后缀之一。"""
    if suffix is not None and suffix not in GRANULARITY_SUFFIXES:
        raise ValueError(f"非标准粒度后缀: {suffix!r}；可选 {sorted(GRANULARITY_SUFFIXES)}")
    parts = [layer.value, domain.prefix.rstrip("_"), entity]
    if suffix:
        parts.append(suffix)
    return "_".join(parts)


def lint(table_name: str, *, expected_layer: Layer | None = None) -> list[str]:
    """返回该表名的规范告警列表；空列表表示完全合规。

    不抛异常——命名规范的价值在于团队能坚持执行，所以给的是可批量审计的告警，
    而不是拦死的硬校验。
    """
    warnings: list[str] = []
    try:
        p = parse(table_name)
    except ValueError as exc:
        return [str(exc)]

    if expected_layer is not None and p.layer is not expected_layer:
        warnings.append(f"层级前缀 {p.layer.value} 与预期 {expected_layer.value} 不符")
    if p.domain is None:
        warnings.append("缺少可识别的数据域段（第二段）")
    if p.suffix is None and p.layer in (Layer.DWD, Layer.DWS):
        warnings.append(f"{p.layer.value} 层建议带标准粒度后缀（第四段）")
    if p.suffix is not None:
        _, layers = GRANULARITY_SUFFIXES[p.suffix]
        if p.layer not in layers:
            warnings.append(
                f"后缀 _{p.suffix} 惯用于 {[x.value for x in layers]}，此处在 {p.layer.value}"
            )
    return warnings
