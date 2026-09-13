"""分层抽帧「三道成本闸门」全部数值常量的唯一出处。

来源：系列三 · 数据挖掘与 AI 第 2 篇
《数据闭环分层抽帧策略：从 TB 级采集数据中提取高价值帧：三道成本闸门》
（公众号「小周」，2026-09-10，https://mp.weixin.qq.com/s/RrD59_FPqek-zSFMIdCRKQ）。

原则同 quality.thresholds：原文出现过的每一个数字都在这里逐字落地，并在注释里
标注它在原文的章节位置；禁止在业务代码里再写裸数字。原文没给的数字一律标注
「⚠️ 原文未明确，本项目设计：」，不冒充原文方案。

⚠️ 关于「成本与耗时」的诚实说明：
原文通篇只做定性成本论述（"频率越高，覆盖越细，但存储、算力与下游推理成本同步
放大"、"GPU 推理成本与送进去的图片数量成正比"），**没有给出任何耗时（秒/毫秒）
或金额（元/美元）数字**。因此本模块不杜撰任何耗时与单价常量：cost.py 里的成本
模型以「帧数」为唯一可信计量，单价一律是调用方注入的相对单位（默认 1.0）。
凡出现比例数字，要么是原文原话，要么由原文数字精确相除得到（用 Fraction 保持
精确，不做四舍五入），并在注释里写明推导链路。
"""

from __future__ import annotations

from fractions import Fraction
from typing import Final

# --------------------------------------------------------------------------- 出处
SOURCE_TITLE: Final[str] = "分层抽帧策略：从 TB 级采集数据中提取高价值帧：三道成本闸门"
SOURCE_SERIES: Final[str] = "小周谈智驾数据闭环 · 系列三 · 数据挖掘与 AI 第 2 篇"
SOURCE_URL: Final[str] = "https://mp.weixin.qq.com/s/RrD59_FPqek-zSFMIdCRKQ"
SOURCE_DATE: Final[str] = "2026-09-10"

# --------------------------------------------------------------------------- 全局
# 二、为什么必须分层：三道成本闸门 —— "我们把闸门分成三道"
#: 闸门道数：常规抽帧 / 事件抽帧 / 推理抽帧
GATE_COUNT: Final[int] = 3

# 三层产物统一写入的目标表（原文一、二、五章反复点名）
#: 抽帧产物只新增这一张表（原文一："抽帧产物只新增一张表"）
FRAME_TABLE_NAME: Final[str] = "dwd_mining_image_frame_detail"
#: 上游复用的采集域两张表（原文一："元信息落 dwd_collect_clip_detail 与 ods_data_file_meta"）
UPSTREAM_CLIP_TABLE: Final[str] = "dwd_collect_clip_detail"
UPSTREAM_FILE_META_TABLE: Final[str] = "ods_data_file_meta"

# --------------------------------------------------------------------------- 原生帧率
# 引言 —— "一路摄像头 10 秒 30 帧就是 300 张图"
#: 原生视频帧率（帧/秒）：由原文 "10 秒 30 帧" 直接给出
NATIVE_VIDEO_FPS: Final[int] = 30
#: 原文举例的观察窗口长度（秒）
NATIVE_EXAMPLE_WINDOW_SECONDS: Final[int] = 10
#: 原文举例：一路摄像头 10 秒全量抽帧 = 300 张图
NATIVE_EXAMPLE_WINDOW_FRAMES: Final[int] = 300

# --------------------------------------------------------------------------- 闸门一：常规抽帧
# 二、闸门表格第 1 行 —— 触发条件「全量 clip」，频率「默认 2 秒 1 帧」
#: 常规抽帧默认间隔（秒）：原文 "默认 2 秒 1 帧"
ROUTINE_INTERVAL_SECONDS: Final[int] = 2
#: 常规抽帧每个间隔取的帧数：原文 "2 秒 1 帧" 的 "1"
ROUTINE_FRAMES_PER_INTERVAL: Final[int] = 1
#: 常规抽帧等效帧率（帧/秒）= 1 / 2，精确分数不取近似
ROUTINE_FPS: Final[Fraction] = Fraction(ROUTINE_FRAMES_PER_INTERVAL, ROUTINE_INTERVAL_SECONDS)
#: 常规抽帧保留比例 = (1/2) / 30 = 1/60。
#: 推导：原文 "2 秒 1 帧" ÷ 原文 "10 秒 30 帧" 的 30 fps。原文未直接写出这个比例。
ROUTINE_KEEP_RATIO: Final[Fraction] = ROUTINE_FPS / NATIVE_VIDEO_FPS

# --------------------------------------------------------------------------- 闸门二：事件抽帧
# 二、闸门表格第 2 行 + 三、事件抽帧：20 秒窗口与异步补抽
#: 事件前窗口（秒）：原文 "事件发生前 15 秒"
EVENT_PRE_SECONDS: Final[int] = 15
#: 事件后窗口（秒）：原文 "后 5 秒"
EVENT_POST_SECONDS: Final[int] = 5
#: 事件窗口总长（秒）：原文 "共 20 秒"
EVENT_WINDOW_SECONDS: Final[int] = 20
#: 事件抽帧间隔（秒）：原文 "1 秒 1 帧"
EVENT_INTERVAL_SECONDS: Final[int] = 1
#: 事件抽帧每个间隔取的帧数：原文 "1 秒 1 帧" 的 "1"
EVENT_FRAMES_PER_INTERVAL: Final[int] = 1
#: 事件抽帧预期帧数：原文 "约 20 帧"
EVENT_EXPECTED_FRAMES: Final[int] = 20
#: 同一 20 秒窗口全量抽帧的帧数：原文 "又不至于把 20 秒变成 600 张全量帧"
EVENT_WINDOW_FULL_FRAMES: Final[int] = 600
#: 事件抽帧保留比例 = 20 / 600 = 1/30。两个数都是原文原话，比例为精确相除。
EVENT_KEEP_RATIO: Final[Fraction] = Fraction(EVENT_EXPECTED_FRAMES, EVENT_WINDOW_FULL_FRAMES)
#: 触发条件类别数：原文 "触发条件有四类"
EVENT_TRIGGER_TYPE_COUNT: Final[int] = 4

# --------------------------------------------------------------------------- 闸门三：推理抽帧
# 二、闸门表格第 3 行 + 四、推理抽帧：用打分代替随机选帧
#: 每个 clip 选取关键帧的下限：原文 "每 clip 打分选 1~5 关键帧"
INFERENCE_MIN_KEYFRAMES: Final[int] = 1
#: 每个 clip 选取关键帧的上限：原文 "1~5"
INFERENCE_MAX_KEYFRAMES: Final[int] = 5
#: 打分维度数：原文 "选谁由打分决定，三个维度"
INFERENCE_SCORE_DIMENSION_COUNT: Final[int] = 3

# --------------------------------------------------------------------------- clip 时长
#: 一个 clip 的标称时长（秒）。
#: 出处不是本篇 a3，而是共享契约 catalog/tables/_collect.py 对
#: dwd_collect_clip_detail 的注释「一个 clip ≈ 1 分钟连续采集片段」（系列一/系列二）。
#: 本模块只用它做「每 clip 帧数」的量级推导，真实值以 clip 表 duration_sec 为准。
CLIP_NOMINAL_DURATION_SECONDS: Final[int] = 60

#: 单路摄像头、单个标称 clip 的常规抽帧产出帧数 = 60 / 2 = 30。由上面两个常量相除。
ROUTINE_FRAMES_PER_CLIP_PER_CAMERA: Final[int] = (
    CLIP_NOMINAL_DURATION_SECONDS // ROUTINE_INTERVAL_SECONDS
)
#: 推理抽帧相对「常规抽帧产出」的保留比例区间 = 1/30 ~ 5/30(=1/6)。
#: 由原文 "1~5 关键帧" 与上面的每 clip 30 帧推导，原文未直接写出该比例。
INFERENCE_KEEP_RATIO_MIN: Final[Fraction] = Fraction(
    INFERENCE_MIN_KEYFRAMES, ROUTINE_FRAMES_PER_CLIP_PER_CAMERA
)
INFERENCE_KEEP_RATIO_MAX: Final[Fraction] = Fraction(
    INFERENCE_MAX_KEYFRAMES, ROUTINE_FRAMES_PER_CLIP_PER_CAMERA
)

# --------------------------------------------------------------------------- 本项目补充
# 以下常量原文只有定性描述，没有数字。全部标注来源，全部可被调用方覆盖。

#: ⚠️ 原文未明确，本项目设计：
#: 原文四章说 "选谁由打分决定，三个维度：图像清晰度 / 目标丰富度 / 时间位置"，
#: 但没给三个维度的权重。三等分是最不夹带私货的默认值（三个 1/3 之和恰为 1）。
#: 真实项目应按场景在 sampling_scoring.yaml 里覆盖。
SCORE_WEIGHT_CLARITY: Final[Fraction] = Fraction(1, INFERENCE_SCORE_DIMENSION_COUNT)
SCORE_WEIGHT_OBJECT_RICHNESS: Final[Fraction] = Fraction(1, INFERENCE_SCORE_DIMENSION_COUNT)
SCORE_WEIGHT_TEMPORAL_POSITION: Final[Fraction] = Fraction(1, INFERENCE_SCORE_DIMENSION_COUNT)

#: ⚠️ 原文未明确，本项目设计：
#: 原文只说 "模糊、过曝、遮挡的帧直接降权"，没给降权系数。这里把三种劣化建模成
#: [0,1] 的乘性保留系数，默认不额外加权（系数即劣化程度本身）。
CLARITY_BLUR_PENALTY_WEIGHT: Final[float] = 1.0
CLARITY_EXPOSURE_PENALTY_WEIGHT: Final[float] = 1.0
CLARITY_OCCLUSION_PENALTY_WEIGHT: Final[float] = 1.0

#: ⚠️ 原文未明确，本项目设计：
#: 原文 "画面里车辆、行人、交通设施越多，语义信息量越大" 没给饱和点。
#: 取 10 个目标为饱和参考值——再多信息增量已很小，避免少数密集帧垄断预算。
OBJECT_RICHNESS_SATURATION_COUNT: Final[int] = 10

#: ⚠️ 原文未明确，本项目设计：
#: 原文 "事件窗口中心、场景切换时刻的帧优先" 没给衰减尺度。取 5.0 秒半衰尺度——
#: 与原文事件后窗口 5 秒同量级，距离事件时刻 5 秒外时间位置分显著回落。
TEMPORAL_DECAY_SCALE_SECONDS: Final[float] = 5.0

#: ⚠️ 原文未明确，本项目设计：
#: 原文只说选 "1~5" 张，没说怎么决定到底选几张。本项目的规则是：
#: 综合分 ≥ 该阈值的候选帧全选，再按 [1,5] 裁剪。0.5 取自 [0,1] 打分域的中位线。
KEYFRAME_SCORE_FLOOR: Final[float] = 0.5

#: ⚠️ 原文未明确，本项目设计：
#: 原文四章说 "相邻帧高度相似"，但没给去重间隔。取 1.0 秒最小间距——
#: 与事件抽帧 1 秒 1 帧的密度对齐，保证选出的关键帧不会是同一瞬间的重复画面。
KEYFRAME_MIN_SPACING_SECONDS: Final[float] = 1.0

#: ⚠️ 原文未明确，本项目设计：
#: 原文四章末尾引用上一篇的「向量化成本分级」——"规则命中、事件抽帧产出的帧优先
#: 进 VLM，普通帧抽样"，但没给「抽样」的比例。取 10% 作为普通帧进 VLM 的默认抽样率。
ORDINARY_FRAME_VLM_SAMPLE_RATIO: Final[float] = 0.1

#: ⚠️ 原文未明确，本项目设计：
#: 原文三章说事件抽帧 "挂在规则结果之后" "补抽与主链路解耦"，没给补抽任务的
#: 最大排队时长。取 24 小时作为异步补抽的默认 TTL——超时即判定补抽失败并告警，
#: 避免补抽任务无限堆积。
EVENT_BACKFILL_TTL_SECONDS: Final[int] = 24 * 60 * 60
#: ⚠️ 原文未明确，本项目设计：异步补抽任务的最大重试次数。
EVENT_BACKFILL_MAX_RETRIES: Final[int] = 3

#: ⚠️ 原文未明确，本项目设计：
#: 原文五章 "多路摄像头同步：同一时刻的多路图片作为一组样本"，没给「同一时刻」的
#: 容忍窗口。这里取 ±50 毫秒，与 quality.thresholds.PRODUCTION_VEHICLE_SYNC_TOLERANCE_MS
#: （量产车软同步 ≤ ±50ms，系列二质量门禁原文数字）保持一致，避免两套口径。
CAMERA_GROUP_SYNC_TOLERANCE_MS: Final[int] = 50

#: 抽帧阶段在 artifact_id 里的 stage 段（三级 ID 体系 ids.derive_artifact_id 的入参）。
#: ⚠️ 原文未明确，本项目设计：原文没规定 stage 字面量，取 "sampling"。
SAMPLING_STAGE: Final[str] = "sampling"
#: ⚠️ 原文未明确，本项目设计：抽帧算法的默认版本号，随选帧/打分逻辑变更递增。
SAMPLING_ALGO_VERSION_DEFAULT: Final[str] = "v1"

#: 摄像头安装位置的取值域。
#: 出处不是本篇 a3（原文只举了 "前视、侧视、后视等多路摄像头"），而是共享契约
#: catalog/tables/_mining.py 对 dwd_mining_image_frame_detail.camera_position 的
#: 列注释「front/left/right/rear」——以 registry 为准，本模块不另立口径。
CAMERA_POSITIONS: Final[tuple[str, ...]] = ("front", "left", "right", "rear")


# --------------------------------------------------------------------------- 自洽校验
# 原文给了一组互相能对上的数字，把它们的关系写成 import 期断言：任何一个常量被改动
# （比如有人把 15 改成 10、把 600 改成 500），这里立刻炸，而不是等到某条测试碰巧覆盖到。
# 每条断言的两边都必须是原文原话里的数字，不引入任何本项目自定的量。

#: 引言 "一路摄像头 10 秒 30 帧就是 300 张图" —— 30 fps × 10 秒 = 300 张，三个数自洽。
assert NATIVE_VIDEO_FPS * NATIVE_EXAMPLE_WINDOW_SECONDS == NATIVE_EXAMPLE_WINDOW_FRAMES, (
    "原文 '一路摄像头 10 秒 30 帧就是 300 张图' 三个数必须自洽"
)
#: 三章 "事件发生前 15 秒 + 后 5 秒，共 20 秒"
assert EVENT_PRE_SECONDS + EVENT_POST_SECONDS == EVENT_WINDOW_SECONDS, (
    "原文 '前 15 秒 + 后 5 秒，共 20 秒' 必须自洽"
)
#: 三章 "又不至于把 20 秒变成 600 张全量帧" —— 20 秒 × 30 fps = 600。
assert EVENT_WINDOW_SECONDS * NATIVE_VIDEO_FPS == EVENT_WINDOW_FULL_FRAMES, (
    "原文 '20 秒变成 600 张全量帧' 必须等于 20 × 30fps"
)
#: 二章闸门表 "1 秒 1 帧（约 20 帧）" —— 20 秒 ÷ 1 秒 = 20 帧。
assert (
    EVENT_WINDOW_SECONDS // EVENT_INTERVAL_SECONDS
) * EVENT_FRAMES_PER_INTERVAL == EVENT_EXPECTED_FRAMES, "原文 '1 秒 1 帧（约 20 帧）' 必须自洽"
#: 二章 "我们把闸门分成三道" 与四章 "选谁由打分决定，三个维度"
assert GATE_COUNT == 3 and INFERENCE_SCORE_DIMENSION_COUNT == 3
#: 四章 "每 clip 打分选 1~5 关键帧"
assert 1 <= INFERENCE_MIN_KEYFRAMES <= INFERENCE_MAX_KEYFRAMES
#: 由原文数字精确相除得到的两个比例，不允许被改成近似值
assert Fraction(1, 60) == ROUTINE_KEEP_RATIO, "常规抽帧保留比例必须精确等于 1/60"
assert Fraction(1, 30) == EVENT_KEEP_RATIO, "事件抽帧保留比例必须精确等于 1/30"
#: 三个打分维度的默认权重之和恰为 1（三等分）
assert Fraction(1) == (
    SCORE_WEIGHT_CLARITY + SCORE_WEIGHT_OBJECT_RICHNESS + SCORE_WEIGHT_TEMPORAL_POSITION
), "三个维度的默认权重之和必须为 1"


__all__ = [
    "SOURCE_TITLE",
    "SOURCE_SERIES",
    "SOURCE_URL",
    "SOURCE_DATE",
    "GATE_COUNT",
    "FRAME_TABLE_NAME",
    "UPSTREAM_CLIP_TABLE",
    "UPSTREAM_FILE_META_TABLE",
    "NATIVE_VIDEO_FPS",
    "NATIVE_EXAMPLE_WINDOW_SECONDS",
    "NATIVE_EXAMPLE_WINDOW_FRAMES",
    "ROUTINE_INTERVAL_SECONDS",
    "ROUTINE_FRAMES_PER_INTERVAL",
    "ROUTINE_FPS",
    "ROUTINE_KEEP_RATIO",
    "EVENT_PRE_SECONDS",
    "EVENT_POST_SECONDS",
    "EVENT_WINDOW_SECONDS",
    "EVENT_INTERVAL_SECONDS",
    "EVENT_FRAMES_PER_INTERVAL",
    "EVENT_EXPECTED_FRAMES",
    "EVENT_WINDOW_FULL_FRAMES",
    "EVENT_KEEP_RATIO",
    "EVENT_TRIGGER_TYPE_COUNT",
    "INFERENCE_MIN_KEYFRAMES",
    "INFERENCE_MAX_KEYFRAMES",
    "INFERENCE_SCORE_DIMENSION_COUNT",
    "CLIP_NOMINAL_DURATION_SECONDS",
    "ROUTINE_FRAMES_PER_CLIP_PER_CAMERA",
    "INFERENCE_KEEP_RATIO_MIN",
    "INFERENCE_KEEP_RATIO_MAX",
    "SCORE_WEIGHT_CLARITY",
    "SCORE_WEIGHT_OBJECT_RICHNESS",
    "SCORE_WEIGHT_TEMPORAL_POSITION",
    "CLARITY_BLUR_PENALTY_WEIGHT",
    "CLARITY_EXPOSURE_PENALTY_WEIGHT",
    "CLARITY_OCCLUSION_PENALTY_WEIGHT",
    "OBJECT_RICHNESS_SATURATION_COUNT",
    "TEMPORAL_DECAY_SCALE_SECONDS",
    "KEYFRAME_SCORE_FLOOR",
    "KEYFRAME_MIN_SPACING_SECONDS",
    "ORDINARY_FRAME_VLM_SAMPLE_RATIO",
    "EVENT_BACKFILL_TTL_SECONDS",
    "EVENT_BACKFILL_MAX_RETRIES",
    "CAMERA_GROUP_SYNC_TOLERANCE_MS",
    "SAMPLING_STAGE",
    "SAMPLING_ALGO_VERSION_DEFAULT",
    "CAMERA_POSITIONS",
]
