"""核心引擎层与部署三区：数据面「实际计算」的那一半。

原文第三章核心引擎层：
  「抽帧（K8s Job）/ 规则挖掘（Spark 批 + Flink 流）/ VLM 推理（Ray + GPU）/
    Embedding 流水线……计算密集，各引擎独立调度独立扩容，互不阻塞」

原文第五章部署三区（在线服务区 / 计算引擎区 / GPU 资源池），以及技术选型的四个取舍。

本模块只做**声明**——引擎有哪些、跑在哪个区、伸缩策略是什么、当初为什么这么选。
真正提交作业的是各子系统自己的适配器（见 controlplane.subsystems），本模块不替它们
封装 Spark/Flink/Ray 客户端，也就不引入任何外部依赖。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..controlplane import constants as K
from ..controlplane.contracts import TaskKind

__all__ = [
    "DeployZone",
    "ZoneSpec",
    "DEPLOY_ZONES",
    "EngineSpec",
    "ENGINES",
    "ENGINE_BY_KIND",
    "TECH_TRADEOFFS",
    "engine_for",
    "engine_decoupling_note",
]


class DeployZone(str, Enum):
    """K8s 三区（原文第五章「平台部署于 K8s，按「三区 + 托管依赖」组织」）。"""

    ONLINE = "在线服务区"
    COMPUTE = "计算引擎区"
    GPU = "GPU 资源池"


@dataclass(frozen=True, slots=True)
class ZoneSpec:
    """一个部署区。字段逐字对应原文第五章表格的三列。"""

    zone: DeployZone
    components: str
    scaling: str
    #: 在线服务区专属：每服务 4C8G × 2 起
    cpu_cores: int | None = None
    memory_gb: int | None = None
    min_replicas: int | None = None


#: 三区规格。数字全部来自原文第五章表格：「每服务 4C8G × 2 起」。
DEPLOY_ZONES: tuple[ZoneSpec, ...] = (
    ZoneSpec(
        DeployZone.ONLINE,
        components="控制台、网关、检索 / 任务 / 标签 / 审核服务",
        scaling="每服务 4C8G × 2 起，无状态 + HPA 随检索 QPS 扩缩",
        cpu_cores=K.ONLINE_SERVICE_CPU_CORES,  # 4C
        memory_gb=K.ONLINE_SERVICE_MEMORY_GB,  # 8G
        min_replicas=K.ONLINE_SERVICE_MIN_REPLICAS,  # × 2 起
    ),
    ZoneSpec(
        DeployZone.COMPUTE,
        components="Spark / Flink 执行器池、抽帧作业",
        scaling="批处理窗口期弹性扩容，跑完即释放",
    ),
    ZoneSpec(
        DeployZone.GPU,
        components="Ray 集群（VLM 推理 vLLM / Embedding）",
        scaling="分时复用 + 优先级队列 + 弹性扩缩",
    ),
)
assert len(DEPLOY_ZONES) == K.DEPLOY_ZONE_COUNT  # 原文：三区


@dataclass(frozen=True, slots=True)
class EngineSpec:
    """一个核心引擎。

    :param key: 引擎键
    :param name_cn: 原文里的中文名
    :param runtime: 运行时（原文括号里那部分）
    :param zone: 部署区
    :param output_table: 主要产出落点（湖仓表）
    :param needs_gpu: 是否吃 GPU 池
    :param note: 补充说明
    """

    key: str
    name_cn: str
    runtime: str
    zone: DeployZone
    output_table: str
    needs_gpu: bool = False
    note: str = ""


#: 四个核心引擎（原文第三章核心引擎层，括号内运行时逐字）。
ENGINES: tuple[EngineSpec, ...] = (
    EngineSpec(
        key="frame_sampling",
        name_cn="抽帧",
        runtime="K8s Job",
        zone=DeployZone.COMPUTE,
        output_table=K.TABLE_IMAGE_FRAME_DETAIL,
        note="下游解耦点：抽帧结果写入该表之后，规则挖掘与 VLM 推理各自基于它独立运行",
    ),
    EngineSpec(
        key="rule_mining",
        name_cn="规则挖掘",
        runtime="Spark 批 + Flink 流",
        zone=DeployZone.COMPUTE,
        output_table=K.TABLE_TASK_DETAIL,
        note="存量批量圈选走 Spark 批；增量回传实时命中走 Flink 流。白天跑批",
    ),
    EngineSpec(
        key="vlm_inference",
        name_cn="VLM 推理",
        runtime="Ray + GPU",
        zone=DeployZone.GPU,
        output_table=K.TABLE_IMAGE_FRAME_DETAIL,
        needs_gpu=True,
        note="按优先级抢 GPU；规则覆盖不了的长尾场景，让大模型看图说话来补（语义标签 + caption）",
    ),
    EngineSpec(
        key="embedding",
        name_cn="Embedding 流水线",
        runtime="Ray + vLLM 同池",
        zone=DeployZone.GPU,
        output_table=K.TABLE_IMAGE_VECTOR_DETAIL,
        needs_gpu=True,
        note=(
            f"T+{K.VECTOR_PIPELINE_LAG_DAYS} 向量化；与 VLM 推理共享同一个 GPU 池，"
            f"走凌晨窗口，凌晨 {K.EMBEDDING_WINDOW_DEADLINE_HOUR} 点前完成"
        ),
    ),
)
assert len(ENGINES) == K.CORE_CAPABILITY_COUNT  # 四项核心能力 ↔ 四个引擎

#: 任务种类 -> 引擎键。
#: ⚠️ 原文未明确，本项目设计：标签治理与圈选导出原文归在应用服务层，
#: 但它们同样要在数据面落地写入，这里各自复用最接近的批处理引擎。
ENGINE_BY_KIND: dict[TaskKind, str] = {
    TaskKind.FRAME_SAMPLING: "frame_sampling",
    TaskKind.RULE_MINING: "rule_mining",
    TaskKind.VLM_INFERENCE: "vlm_inference",
    TaskKind.EMBEDDING: "embedding",
    TaskKind.TAG_GOVERNANCE: "rule_mining",
    TaskKind.CURATION: "rule_mining",
}


def engine_for(kind: TaskKind) -> EngineSpec:
    """按任务种类取引擎规格。"""
    key = ENGINE_BY_KIND[kind]
    for engine in ENGINES:
        if engine.key == key:
            return engine
    raise KeyError(f"未登记的引擎: {key!r}")  # pragma: no cover


@dataclass(frozen=True, slots=True)
class TechTradeoff:
    """一个技术选型取舍。原文第五章「技术选型上几个关键取舍」，四条逐字。"""

    decision: str
    chosen: str
    rejected: str
    reason: str


#: 四个关键取舍（原文第五章）。
TECH_TRADEOFFS: tuple[TechTradeoff, ...] = (
    TechTradeoff(
        decision="批处理",
        chosen="Spark on K8s",
        rejected="Hive",
        reason="迭代开发与 UDF 扩展性不足",
    ),
    TechTradeoff(
        decision="流处理",
        chosen="Flink",
        rejected="Spark Streaming",
        reason="事件准实时语义弱",
    ),
    TechTradeoff(
        decision="GPU 推理调度",
        chosen="Ray + vLLM",
        rejected="Triton",
        reason="Triton 适合单模型服务化，但缺任务编排与断点续跑",
    ),
    TechTradeoff(
        decision="向量检索",
        chosen="StarRocks 外部表 HNSW",
        rejected="Milvus 等独立向量库",
        reason=("放弃独立向量库换取湖仓单一事实源与免数据冗余，属差异化权衡，并保留内表降级路径"),
    ),
)


def engine_decoupling_note() -> str:
    """原文第三章那句关键约束，供文档与自检引用（逐字）。"""
    return (
        "引擎与服务解耦、引擎之间也解耦：抽帧结果写入 "
        f"{K.TABLE_IMAGE_FRAME_DETAIL} 之后，规则挖掘与 VLM 推理各自基于该表独立运行"
        "——批处理引擎白天跑批，推理引擎按优先级抢 GPU，谁也不等谁。"
        "任何一个引擎故障或扩容，都不影响其他链路。"
    )
