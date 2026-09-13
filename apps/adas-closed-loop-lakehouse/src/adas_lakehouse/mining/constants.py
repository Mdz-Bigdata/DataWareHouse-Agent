"""规则挖掘引擎的全部数值常量——每一个都逐字来自原文，注释标出处。

出处缩写：
  [S3-04] 系列三第 4 篇《数据闭环规则挖掘引擎实战：从结构化元数据中批量发现高价值场景》
  [S3-01] 系列三第 1 篇《数据闭环数据挖掘平台架构设计：控制面/数据面分离的工程实践》
  [S3-02] 系列三第 2 篇《数据闭环分层抽帧策略：从 TB 级采集数据中提取高价值帧》
  [S3-03] 系列三第 3 篇《数据闭环统一标签体系设计：三来源标签的字典映射与去重治理》

  ⚠️ [S3-05]《VLM 推理挖掘：多模态大模型如何补足长尾场景标签》**本项目未取得原文**，
  只有 [S3-04] 结尾的一句预告。因此 VLM 段的常量出处一律落在 S3-01/02/03/04 上，
  凡是那四篇没写的（批大小、重试次数、超时……）一律不放进本模块，见 vlm.py。

本模块只放「原文写死的数字」。凡是原文没写、由本项目补的参数，一律放在各自模块里，
并在 docstring 里用「⚠️ 原文未明确，本项目设计：」显式标注，绝不混进来冒充原文。
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "HARSH_DECEL_THRESHOLD_MPS2",
    "HARSH_DECEL_MIN_DURATION_SEC",
    "EVENT_WINDOW_BEFORE_SEC",
    "EVENT_WINDOW_AFTER_SEC",
    "EVENT_WINDOW_TOTAL_SEC",
    "BATCH_SCAN_ROW_CEILING",
    "BATCH_SLA_HOURS",
    "BATCH_SLA_SECONDS",
    "RULE_TYPE_COUNT",
    "FUNNEL_STAGES",
    "MINING_TABLE_COUNT",
    "MINING_TABLE_COUNT_BY_LAYER",
    "EMBEDDING_WINDOW_DEADLINE_HOUR",
    "HIGH_VALUE_SOURCES",
    "SERVICE_CPU_CORES",
    "SERVICE_MEMORY_GB",
    "SERVICE_MIN_REPLICAS",
    "RULE_JOBS_API_PATH",
    "RULE_JOB_PROGRESS_API_PATH",
    "TAG_COVERAGE_API_PATH",
    "VLM_INFER_ENGINE",
    "VLM_INFER_SERVING_RUNTIME",
    "VLM_OUTPUT_KINDS",
    "VLM_CAPTION_TAG_CATEGORY",
    "VLM_TAG_SOURCE",
    "VLM_TASK_TYPE",
    "VLM_LONG_TAIL_EXAMPLES",
    "VLM_REQUIRED_LINEAGE_FIELDS",
    "INFERENCE_MIN_KEYFRAMES",
    "INFERENCE_MAX_KEYFRAMES",
]

# --------------------------------------------------------------------------- 车辆信号阈值

#: CAN 减速度阈值，单位 m/s²。
#: [S3-04] 二、六大种类表格「车辆信号」行原文：「CAN 减速度 < -4m/s² 持续 ≥ 0.5s 等，准实时」。
#: 注意是严格小于（<），不是 <=——原文写的就是 `<`。
HARSH_DECEL_THRESHOLD_MPS2: Final[float] = -4.0

#: 急减速的最短持续时长，单位秒。
#: [S3-04] 同上行原文：「持续 ≥ 0.5s」。注意是大于等于（≥）。
HARSH_DECEL_MIN_DURATION_SEC: Final[float] = 0.5

# --------------------------------------------------------------------------- 事件窗口

#: 事件触发规则的回看窗口，单位秒。
#: [S3-04] 二、「事件触发」行：「消费回传触发事件流，含前 15 后 5 秒窗口，准实时」；
#: [S3-04] 三、隐藏联动：「规则识别出接管事件，事件抽帧引擎立刻回头对前 15 后 5 秒窗口加密采样」。
EVENT_WINDOW_BEFORE_SEC: Final[int] = 15

#: 事件触发规则的前看窗口，单位秒。出处同 EVENT_WINDOW_BEFORE_SEC。
EVENT_WINDOW_AFTER_SEC: Final[int] = 5

#: 事件窗口总长 = 15 + 5 = 20 秒。派生量，非原文直接给出。
EVENT_WINDOW_TOTAL_SEC: Final[int] = EVENT_WINDOW_BEFORE_SEC + EVENT_WINDOW_AFTER_SEC

# --------------------------------------------------------------------------- 批处理 SLA

#: T+1 批处理的数据量上界：「亿级」= 1 亿行。
#: [S3-04] 三、批流双模原文：「Spark SQL 直接在 Paimon 表上执行，亿级以下数据 4 小时内跑完」。
BATCH_SCAN_ROW_CEILING: Final[int] = 100_000_000

#: T+1 批处理 SLA，单位小时。出处同上：「4 小时内跑完」。
BATCH_SLA_HOURS: Final[int] = 4

#: SLA 换算成秒，供超时判定使用。派生量。
BATCH_SLA_SECONDS: Final[int] = BATCH_SLA_HOURS * 3600

# --------------------------------------------------------------------------- 规则分类与漏斗

#: 规则种类数量。[S3-04] 二、标题：「六大种类：真实规则长什么样」「规则按条件来源分六大种类」。
RULE_TYPE_COUNT: Final[int] = 6

#: 三级挖掘漏斗。[S3-04] 四、原文：「规则先验粗筛 → 模型不确定性细筛 → 检索相似性扩散」。
#: 规则挖掘引擎是第一层：「挖掘漏斗的第一层过滤器」（[S3-04] 开篇）。
FUNNEL_STAGES: Final[tuple[str, str, str]] = (
    "规则先验粗筛",
    "模型不确定性细筛",
    "检索相似性扩散",
)

# --------------------------------------------------------------------------- 挖掘域表数量

#: 挖掘域新增表总数。[S3-01] 二、对齐原则表「数仓命名规范」行：
#: 「新增表遵循 {层级}_{挖掘域}_{实体}_detail，共 11 张表（1 ODS + 8 DWD + 1 DWS + 1 ADS）」。
MINING_TABLE_COUNT: Final[int] = 11

#: 同上，拆到层级。
MINING_TABLE_COUNT_BY_LAYER: Final[dict[str, int]] = {
    "ods": 1,
    "dwd": 8,
    "dws": 1,
    "ads": 1,
}

# --------------------------------------------------------------------------- 向量化分级（规则优先级的下游）

#: Embedding 凌晨窗口的截止时钟（本地时区小时数）。
#: [S3-01] 五、原文：「Embedding 走凌晨窗口（凌晨 6 点前完成），用时间错峰避免资源争抢」。
#: 规则挖掘在这里的责任：rule_priority 决定命中数据进哪一档向量化队列（[S3-04] 一）。
EMBEDDING_WINDOW_DEADLINE_HOUR: Final[int] = 6

#: 优先向量化的三类高价值数据来源。
#: [S3-01] 五、原文：「高价值数据（规则命中 / 事件抽帧 / VLM 标签）优先向量化，普通数据抽样处理」。
HIGH_VALUE_SOURCES: Final[tuple[str, str, str]] = ("规则命中", "事件抽帧", "VLM 标签")

# --------------------------------------------------------------------------- 部署规格

#: 在线服务区单服务规格与起步副本数。
#: [S3-01] 五、部署表「在线服务区」行：「每服务 4C8G × 2 起，无状态 + HPA 随检索 QPS 扩缩」。
SERVICE_CPU_CORES: Final[int] = 4
SERVICE_MEMORY_GB: Final[int] = 8
SERVICE_MIN_REPLICAS: Final[int] = 2

# --------------------------------------------------------------------------- OpenAPI 出口

#: [S3-01] 六、接口表「任务类」行：「POST /api/v1/mining/rule-jobs；GET /jobs/{jobId}/progress，
#: 任务创建与进度查询，幂等键防重复提交」。
RULE_JOBS_API_PATH: Final[str] = "/api/v1/mining/rule-jobs"
RULE_JOB_PROGRESS_API_PATH: Final[str] = "/api/v1/mining/rule-jobs/{jobId}/progress"

#: [S3-01] 六、接口表「检索类」行：「GET /api/v1/scene/tag-coverage」——场景缺口识别的对外出口。
TAG_COVERAGE_API_PATH: Final[str] = "/api/v1/scene/tag-coverage"

# --------------------------------------------------------------------------- VLM 推理挖掘

#: VLM 推理的调度引擎。落 dwd_mining_task_detail.engine（该列取值域 spark/flink/ray）。
#: [S3-01] 三、五层应用架构表「核心引擎层」行原文：「VLM 推理（Ray + GPU）」；
#: [S3-01] 五、技术选型原文：「GPU 推理调度选 Ray + vLLM（Triton 适合单模型服务化，
#: 但缺任务编排与断点续跑）」——选 Ray 的理由就是「任务编排与断点续跑」，
#: 所以本引擎的断点续跑不是附加功能，是选型的兑现，见 vlm.InferCheckpoint。
VLM_INFER_ENGINE: Final[str] = "ray"

#: 模型服务运行时。出处同上：「Ray + vLLM」。
VLM_INFER_SERVING_RUNTIME: Final[str] = "vllm"

#: VLM 的双输出。[S3-04] 结尾预告逐字：「下篇讲 VLM 推理引擎：选帧打分、
#: **双输出（标签 + caption）**、Ray + GPU 调度与断点续跑，长尾场景的标签它来补」；
#: [S3-01] 一同义：「多模态大模型做语义级标签与关键说明（caption）生成」。
#: 两项缺一不可——只回标签就丢了 caption 的语义检索价值，只回 caption 就没有结构化过滤。
VLM_OUTPUT_KINDS: Final[tuple[str, str]] = ("标签", "caption")

#: caption 落库时的特殊标签类别。
#: [S3-03] 二原文：「VLM 生成的关键说明（caption）以 tag_category=CAPTION 的特殊标签
#: 写入图片标签表，与结构化标签同条记录口径并存，同时冗余一份到向量表」。
VLM_CAPTION_TAG_CATEGORY: Final[str] = "CAPTION"

#: VLM 产出的标签来源码。registry 里 dwd_mining_image_tag_detail.tag_source 的注释写死了
#: 取值域「collect 采集/rule 规则/vlm 模型」；[S3-03] 一称其为「模型标签」。
VLM_TAG_SOURCE: Final[str] = "vlm"

#: 执行追溯里 VLM 推理的任务类型。registry 里 dwd_mining_task_detail.task_type 的注释
#: 写死了取值域「rule_mining/frame_extract/vlm_infer/embedding」。
VLM_TASK_TYPE: Final[str] = "vlm_infer"

#: 原文点名的两个「规则写不出来」的语义级长尾场景。
#: [S3-04] 结尾逐字：「「施工区锥桶摆放混乱」「行人撑着花伞」这类语义级场景，
#: 结构化条件写不出来——这正是大模型推理挖掘的领地」。
#: 留在代码里不是当装饰：它们是 VLM 提示词的场景清单基线，也是验收「补长尾」是否名副其实的样例。
VLM_LONG_TAIL_EXAMPLES: Final[tuple[str, str]] = ("施工区锥桶摆放混乱", "行人撑着花伞")

#: 模型标签必须携带的血缘字段。
#: [S3-03] 二、③ 血缘填充逐字：「每条标签携带 tag_source、rule_id / model_name /
#: model_version、confidence、infer_job_id——回答「这个标签从哪来、可信度多少」」。
#: 规则侧带 rule_id，模型侧带 model_name/model_version/infer_job_id，confidence 两侧都有。
VLM_REQUIRED_LINEAGE_FIELDS: Final[tuple[str, ...]] = (
    "tag_source",
    "model_name",
    "model_version",
    "confidence",
    "infer_job_id",
)

#: 每个 clip 送进 VLM 的关键帧张数下限 / 上限。
#: [S3-02] 二、三道闸门表「推理抽帧」行逐字：「每 clip 打分选 1~5 关键帧」。
#:
#: 这两个数字 sampling 子系统也持有一份（``sampling.constants.INFERENCE_MIN/MAX_KEYFRAMES``），
#: 本模块**刻意不 import 它**：控制面的接入契约（controlplane/subsystems.py 模块 docstring）
#: 把「子系统之间零耦合，只经 Paimon 表交接」写成了硬约束，mining 一旦 import sampling，
#: 「任何一个引擎故障都不影响其他链路」就不再成立。两份定义的一致性由
#: tests/deep/test_mining.py 的 ``test_keyframe_bounds_agree_with_sampling`` 钉住——
#: 用测试跨子系统对账，而不是用 import 制造运行期依赖。
INFERENCE_MIN_KEYFRAMES: Final[int] = 1
INFERENCE_MAX_KEYFRAMES: Final[int] = 5
