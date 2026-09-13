"""三个标签来源：采集标签 / 规则标签 / 模型标签，以及各自的口径与血缘要求。

原文开篇：
    「上篇抽帧完成后，标签从三个来源涌进来：采集系统随车带回的采集标签、
      规则挖掘引擎批量产出的规则标签、VLM 推理生成的模型标签。
      三个来源各有各的口径——采集标签是人工约定，规则标签跟着规则版本走，
      VLM 标签是大模型自由发挥。」

三个来源的差异决定了三件事，本模块把它们固化成 :class:`SourceProfile`：
  1. **口径锚点不同**：采集=人工约定的枚举，规则=规则版本，模型=模型版本；
  2. **血缘字段不同**：原文③「每条标签携带 tag_source、rule_id / model_name /
     model_version、confidence、infer_job_id」——斜杠表示按来源二选一，
     规则标签填 rule_id，模型标签填 model_name/model_version/confidence/infer_job_id；
  3. **准入不同**：原文第四章只对模型标签强调「未审核标签不得进入训练集圈选」。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .constants import TAG_SOURCE_COUNT

__all__ = [
    "TagSource",
    "SourceProfile",
    "SOURCE_PROFILES",
    "profile_for",
    "required_lineage_fields",
    "source_priority",
]


class TagSource(str, Enum):
    """标签来源枚举。取值即事实表 ``tag_source`` 字段的字面量（联合主键第三段）。"""

    #: 采集系统随车带回的采集标签
    COLLECT = "collect"
    #: 规则挖掘引擎批量产出的规则标签
    RULE = "rule"
    #: VLM 推理生成的模型标签
    MODEL = "model"

    @property
    def profile(self) -> SourceProfile:
        return SOURCE_PROFILES[self]


@dataclass(frozen=True, slots=True)
class SourceProfile:
    """一个标签来源的完整画像。

    :param source: 来源枚举
    :param name_cn: 中文名（原文用词）
    :param producer: 谁产出的（原文用词）
    :param caliber: 口径锚点（原文用词：人工约定 / 跟着规则版本走 / 大模型自由发挥）
    :param required_lineage: 该来源**必须**填写的血缘字段（原文③）
    :param optional_lineage: 该来源可选的血缘字段
    :param needs_human_review: 是否必须过人工审核才能进训练集圈选（原文第四章）
    :param priority: 冲突消解时的来源优先级，数字越小越优先
        （⚠️ 原文未明确，本项目设计，见 :func:`source_priority`）
    """

    source: TagSource
    name_cn: str
    producer: str
    caliber: str
    required_lineage: tuple[str, ...]
    optional_lineage: tuple[str, ...]
    needs_human_review: bool
    priority: int

    @property
    def lineage_fields(self) -> tuple[str, ...]:
        """该来源会落到事实表血缘列上的全部字段。"""
        return self.required_lineage + self.optional_lineage


#: ⚠️ 原文未明确，本项目设计：来源优先级。
#: 原文只说了三来源「各有各的口径」（采集=人工约定、规则=跟版本、VLM=自由发挥），
#: 没有给出冲突时谁压谁的规则。本项目据「人工约定 > 规则确定性 > 大模型自由发挥」
#: 的可信度直觉定序：collect(1) < rule(2) < model(3)，数字小者胜。
#: 该定序只在 dedup.ConflictResolver 的互斥裁决里生效，不影响入库（三来源各存一行）。
SOURCE_PROFILES: dict[TagSource, SourceProfile] = {
    TagSource.COLLECT: SourceProfile(
        source=TagSource.COLLECT,
        name_cn="采集标签",
        producer="采集系统随车带回",
        caliber="人工约定",
        # 采集标签没有规则/模型版本可挂，口径锚点是人工约定的枚举本身
        required_lineage=("tag_source",),
        optional_lineage=("collect_task_id", "infer_job_id"),
        needs_human_review=False,
        priority=1,
    ),
    TagSource.RULE: SourceProfile(
        source=TagSource.RULE,
        name_cn="规则标签",
        producer="规则挖掘引擎批量产出",
        caliber="跟着规则版本走",
        required_lineage=("tag_source", "rule_id"),
        optional_lineage=("rule_version", "infer_job_id", "confidence"),
        needs_human_review=False,
        priority=2,
    ),
    TagSource.MODEL: SourceProfile(
        source=TagSource.MODEL,
        name_cn="模型标签",
        producer="VLM 推理生成",
        caliber="大模型自由发挥",
        # 原文③：模型标签必须能回答「从哪来、可信度多少」
        required_lineage=("tag_source", "model_name", "model_version", "confidence"),
        optional_lineage=("infer_job_id",),
        # 原文第四章：「未审核标签不得进入训练集圈选」——模型产出永远先过审再上岗
        needs_human_review=True,
        priority=3,
    ),
}

assert len(SOURCE_PROFILES) == TAG_SOURCE_COUNT, "三来源常量与画像表不一致"


def profile_for(source: TagSource | str) -> SourceProfile:
    """按来源取画像。

    :raises ValueError: 未知来源（防止旁路来源绕过统一标签服务——原文「没有旁路」）
    """
    try:
        key = source if isinstance(source, TagSource) else TagSource(str(source).lower())
    except ValueError as exc:  # pragma: no cover - 错误路径
        raise ValueError(
            f"未知标签来源 {source!r}；三来源一律经统一标签服务写入，"
            f"合法取值 {[s.value for s in TagSource]}"
        ) from exc
    return SOURCE_PROFILES[key]


def required_lineage_fields(source: TagSource | str) -> tuple[str, ...]:
    """该来源必须填写的血缘字段（管道第三步「血缘填充」的校验依据）。"""
    return profile_for(source).required_lineage


def source_priority(source: TagSource | str) -> int:
    """冲突消解用的来源优先级，越小越优先。

    ⚠️ 原文未明确，本项目设计：见 :data:`SOURCE_PROFILES` 顶部说明。
    """
    return profile_for(source).priority
