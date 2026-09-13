"""原文出现的全部具体数字与专名，逐字登记，不做四舍五入、不改「合理值」。

出处（下文注释里的「原文」一律指这一篇）：
    系列三《数据闭环统一标签体系设计：三来源标签的字典映射与去重治理》
    小周谈智驾数据闭环 · 系列三 · 数据挖掘与 AI 第 3 篇
    2026-09-11 · https://mp.weixin.qq.com/s/YP58mq0QYOATRZUzsC0QLg

本文件只放「原文写死的数字/专名」。凡本项目自行补的阈值（置信度门限、
低覆盖判定线、候选池 TTL 等）一律不放这里，而是放在各自模块，并在 docstring
用「⚠️ 原文未明确，本项目设计：」标注清楚，避免把自研设计冒充成原文方案。
"""

from __future__ import annotations

from typing import Final

# --------------------------------------------------------------------------- 文章元信息

ARTICLE_TITLE: Final[str] = "数据闭环统一标签体系设计：三来源标签的字典映射与去重治理"
ARTICLE_SERIES: Final[str] = "小周谈智驾数据闭环 · 系列三 · 数据挖掘与 AI"
#: 原文页眉：「系列三 · 数据挖掘与 AI 第 3 篇」
ARTICLE_INDEX_IN_SERIES: Final[int] = 3
ARTICLE_DATE: Final[str] = "2026-09-11"
ARTICLE_URL: Final[str] = "https://mp.weixin.qq.com/s/YP58mq0QYOATRZUzsC0QLg"

# --------------------------------------------------------------------------- 三来源

#: 原文开篇：「标签从三个来源涌进来：采集系统随车带回的采集标签、规则挖掘引擎
#: 批量产出的规则标签、VLM 推理生成的模型标签。」
TAG_SOURCE_COUNT: Final[int] = 3

# --------------------------------------------------------------------------- 标签字典

#: 原文第一章标题：「标签字典：五大类别的受控词表」——它把标签空间分成五大类别。
TAG_CATEGORY_COUNT: Final[int] = 5

#: 原文：「类别下再支持二级分类（parent_tag_id）」+ 表头「典型三级标签」
#: → 类别(1) → 二级分类(2) → 三级标签(3)，共三层。
TAG_TREE_DEPTH: Final[int] = 3

#: 原文：「这套分类不是拍脑袋设计的，枚举参照了四套业界实践」
REFERENCE_PRACTICE_COUNT: Final[int] = 4

#: 原文：「华为云八爪鱼的九大类标签（吸收「自车–他车–环境」三视角）」
HUAWEI_OCTOPUS_CATEGORY_COUNT: Final[int] = 9
HUAWEI_OCTOPUS_PERSPECTIVE_COUNT: Final[int] = 3
HUAWEI_OCTOPUS_PERSPECTIVES: Final[tuple[str, str, str]] = ("自车", "他车", "环境")

#: 原文：「端到端世界模型的三级标签体系（吸收细化粒度与多源产出的思路）」
WORLD_MODEL_TAG_LEVEL_COUNT: Final[int] = 3

#: 原文：「ISO 34504 / SOTIF 场景本体（吸收层级树 + 同层互斥原则）」
ISO_SCENARIO_ONTOLOGY_STANDARD: Final[str] = "ISO 34504"
SOTIF_ONTOLOGY_NAME: Final[str] = "SOTIF 场景本体"

#: 原文：「ODD 运行设计域（五类正好映射时空/道路/参与者要素，支撑覆盖率统计与定向采集）」
ODD_MAPPED_CATEGORY_COUNT: Final[int] = 5
ODD_MAPPED_ELEMENTS: Final[tuple[str, str, str]] = ("时空", "道路", "参与者")

#: 四套业界实践（名称 → 本项目吸收了什么），逐条对应原文正文。
REFERENCE_PRACTICES: Final[tuple[tuple[str, str], ...]] = (
    ("华为云八爪鱼的九大类标签", "吸收「自车–他车–环境」三视角"),
    ("端到端世界模型的三级标签体系", "吸收细化粒度与多源产出的思路"),
    ("ISO 34504 / SOTIF 场景本体", "吸收层级树 + 同层互斥原则"),
    ("ODD 运行设计域", "五类正好映射时空/道路/参与者要素，支撑覆盖率统计与定向采集"),
)

# --------------------------------------------------------------------------- 标签爆炸示例

#: 原文开篇的反面教材：「「雨天」「降雨」「rain」「下雨天」变成四个互不认识的标签，
#: 检索时查一个漏三个，统计时每个都算一小撮。」——四个写法必须归一到同一个 tag_id。
TAG_EXPLOSION_EXAMPLE_ALIASES: Final[tuple[str, str, str, str]] = (
    "雨天",
    "降雨",
    "rain",
    "下雨天",
)
TAG_EXPLOSION_EXAMPLE_ALIAS_COUNT: Final[int] = 4
#: 原文：「检索时查一个漏三个」——4 个写法里命中 1 个、漏掉 3 个。
TAG_EXPLOSION_MISSED_COUNT: Final[int] = 3

# --------------------------------------------------------------------------- 三源收口管道

#: 原文第二章标题：「三源收口管道：字典映射 → 幂等去重 → 血缘填充」，正文「管道三步」。
PIPELINE_STEP_COUNT: Final[int] = 3
PIPELINE_STEPS: Final[tuple[str, str, str]] = ("字典映射", "幂等去重", "血缘填充")

#: 原文②：「联合主键（data_id/image_id, tag_id, tag_source）Upsert」——三段联合主键。
FACT_TABLE_PK_FIELD_COUNT: Final[int] = 3

#: 原文：「管道出口是两张标签事实表：dwd_mining_data_tag_detail（clip 级）与
#: dwd_mining_image_tag_detail（image 级）」
FACT_TABLE_COUNT: Final[int] = 2

# --------------------------------------------------------------------------- 生命周期

#: 原文第三章标题：「生命周期：四态状态机治理标签从生到死」
LIFECYCLE_STATE_COUNT: Final[int] = 4
LIFECYCLE_STATES: Final[tuple[str, str, str, str]] = (
    "candidate",
    "active",
    "deprecated",
    "merged",
)

#: 原文：「字典变更走标签审核流（平台工单 + 双人复核，谁也不能直接改字典）」
DICT_CHANGE_REVIEWER_COUNT: Final[int] = 2

#: 原文：「湖仓里早已存在的 ods_scene_tag 场景标签统一归入 SCENE 类别，字典是它的超集」
LEGACY_SCENE_TAG_TABLE: Final[str] = "ods_scene_tag"
LEGACY_SCENE_TARGET_CATEGORY: Final[str] = "SCENE"

# --------------------------------------------------------------------------- 表名

DICT_TABLE: Final[str] = "dwd_mining_tag_dict_detail"
DATA_TAG_TABLE: Final[str] = "dwd_mining_data_tag_detail"
IMAGE_TAG_TABLE: Final[str] = "dwd_mining_image_tag_detail"
COVERAGE_TABLE: Final[str] = "dws_mining_tag_coverage_daily"
#: 原文：caption「同时冗余一份到向量表」，向量表即抽帧篇的 dwd_mining_image_vector_detail。
VECTOR_TABLE: Final[str] = "dwd_mining_image_vector_detail"

#: 原文第四章：「dws_mining_tag_coverage_daily（标签覆盖度日指标表）按
#: 「日期 × 标签类别」持续统计」——DWS 表的统计维度就是这两个字段。
#: 列名取 catalog.registry 里该表的写法：日期那一维叫 stat_date（DATE），不叫 dt。
COVERAGE_DIMENSIONS: Final[tuple[str, str]] = ("stat_date", "tag_category")

#: 原文：「VLM 生成的关键说明（caption）以 tag_category=CAPTION 的特殊标签写入图片标签表」
CAPTION_TAG_CATEGORY: Final[str] = "CAPTION"

__all__ = [name for name in dir() if name.isupper()]
