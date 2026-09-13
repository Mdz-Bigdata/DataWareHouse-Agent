"""控制面 ⇄ 数据面的边界契约：谁持有什么、谁能传什么、传的是什么形状。

原文第四章把分离讲成了两句话：
  · 控制面（平台本地）：MySQL 存规则配置、任务配置与执行状态、审核流状态；
    Redis 存检索热点与字典热缓存——全部是「平台自身运行态」，体量小、可随时重建；
  · 数据面（湖仓）：clip / 图片 / 标签 / 向量全部在 Paimon，StarRocks 只做查询与
    向量索引加速，同样不持有主数据。

本模块把这两句话变成可执行的约束：

  1. :class:`Plane` / :func:`asset_plane` —— 每一类资产归属哪一面，写死不含糊；
  2. :class:`TaskEnvelope` —— 控制面下发给数据面的**唯一**载体，只装「怎么算」
     （规则、参数、输入选择器、run_id），不装「算什么数据」的数据本体；
  3. :class:`RunReport` —— 数据面回报控制面的**唯一**载体，只回指针
     （artifact_id / 表名 / 行数），不回主数据；
  4. :func:`assert_no_master_data` —— 上面两条的运行时守卫。越界直接抛
     :class:`MasterDataLeak`，让「平台不持有主数据」从口号变成断言。

为什么要分离（原文给的理由，逐条落在这里的设计上）：
  · 反面教训：平台顺手建一套自己的「数据副本」——抽帧结果存一份、标签存一份、
    向量再存一份，时间一长平台里的数据与湖仓对不上，**口径分裂、血缘断裂**；
  · 正面收益：**平台可以整体重建与迁移，主数据不受影响**——MySQL 丢了，重建配置
    即可；服务挂了，无状态重启即可；
  · 对账基准：**湖仓是唯一对账基准**，不存在双写导致的口径分裂；
  · 健康判据：常量 :data:`~.constants.HEALTH_CRITERION`——把平台数据库清空重建，
    业务数据是否完好。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any

from ..ids import ArtifactStatus, parse_run_id
from . import constants as K

__all__ = [
    "Plane",
    "TaskKind",
    "TaskState",
    "ReviewDecision",
    "MasterDataLeak",
    "ControlStateLeak",
    "PlaneBoundaryError",
    "CONTROL_STATE_FIELD_HINTS",
    "WRITEBACK_TABLES",
    "WRITEBACK_COLUMN_MAP",
    "WRITEBACK_UNMAPPED_FIELDS",
    "assert_no_control_state",
    "writeback_column_gap",
    "ArtifactRef",
    "TaskEnvelope",
    "RunReport",
    "TaskRecord",
    "TaskEvent",
    "SUBSYSTEM_ROUTING",
    "REVIEW_REQUIRED_KINDS",
    "TERMINAL_STATES",
    "CONTROL_PLANE_ASSETS",
    "DATA_PLANE_ASSETS",
    "asset_plane",
    "assert_no_master_data",
]


# --------------------------------------------------------------------------- 面


class Plane(str, Enum):
    """两个面。除此之外没有第三个地方可以放东西——这是本子系统的封闭世界假设。"""

    CONTROL = "control"
    DATA = "data"

    @property
    def storage(self) -> tuple[str, ...]:
        """该面的存储底座。控制面 = 平台本地；数据面 = 湖仓与查询加速层。"""
        if self is Plane.CONTROL:
            # 原文第三章「平台支撑层」：MySQL / Redis / Kafka / OSS / 模型注册表
            return ("MySQL", "Redis", "Kafka", "OSS", "模型注册表")
        # 原文第三章「外部依赖」：DLF + Paimon 湖仓 / StarRocks / DolphinScheduler / GPU 资源池
        return ("DLF + Paimon", "StarRocks", "DolphinScheduler", "GPU 资源池")


#: 控制面持有的资产 -> 说明。原文第四章：全部是「平台自身运行态」，体量小、可随时重建。
CONTROL_PLANE_ASSETS: dict[str, str] = {
    "rule_config": "规则配置（MySQL）",
    "task_config": "任务配置与执行状态（MySQL）",
    "review_state": "审核流状态（MySQL）",
    "search_hotspot": "检索热点缓存（Redis）",
    "tag_dict_cache": "字典热缓存（Redis）",
    "idempotency_key": "幂等键（Redis，防重复提交）",
    "audit_event": "网关审计事件（MySQL）",
}

#: 数据面持有的资产 -> 说明。原文第四章：clip / 图片 / 标签 / 向量全部在 Paimon。
DATA_PLANE_ASSETS: dict[str, str] = {
    "clip": f"采集单元元信息，复用采集域既有的 {K.TABLE_CLIP_DETAIL}（原文：clip 元数据不新建表）",
    "image": f"抽帧图片元信息，{K.TABLE_IMAGE_FRAME_DETAIL}",
    "tag": "标签（采集/规则/大模型三来源统一字典）",
    "vector": f"图片与文本向量，{K.TABLE_IMAGE_VECTOR_DETAIL}",
    "dataset": "圈选结果回写数据资产域",
}


def asset_plane(asset: str) -> Plane:
    """查一类资产归属哪个面。未知资产直接报错——不给「随便放哪」留口子。

    :param asset: 资产键，取自 :data:`CONTROL_PLANE_ASSETS` 或 :data:`DATA_PLANE_ASSETS`
    :raises KeyError: 资产未登记
    """
    if asset in CONTROL_PLANE_ASSETS:
        return Plane.CONTROL
    if asset in DATA_PLANE_ASSETS:
        return Plane.DATA
    raise KeyError(
        f"未登记的资产 {asset!r}；新增资产必须先在 CONTROL_PLANE_ASSETS / DATA_PLANE_ASSETS 里定归属"
    )


# --------------------------------------------------------------------------- 异常


class PlaneBoundaryError(RuntimeError):
    """控制面/数据面边界被违反。"""


class MasterDataLeak(PlaneBoundaryError):
    """主数据泄漏到控制面：违反第一设计原则「平台不持有主数据」。

    触发场景通常是有人图省事，把抽帧图片的 bytes、向量数组或整批标签值塞进任务参数
    或运行回报里——那正是原文点名的「平台顺手建了一套自己的数据副本」的开端。
    """


class ControlStateLeak(PlaneBoundaryError):
    """控制面运行态漏进数据面：边界被违反的**另一半**。

    :class:`MasterDataLeak` 管的是「控制面不碰数据」，本异常管的是
    「数据面不存状态」。两条一起才是原文第四章那句话的完整意思：

      · 控制面（平台本地）：规则配置、任务配置与执行状态、审核流状态；
      · 数据面（湖仓）：clip / 图片 / 标签 / 向量。

    典型越界：某个引擎顺手把审核结论、幂等键、重试次数写进
    ``dwd_mining_image_frame_detail`` 这类**产物表**。一旦这么做，湖仓里就有了
    一份平台运行态的副本，「把平台的数据库清空重建，业务数据是否完好」的答案
    就不再是「完好」——因为清库之后湖仓里那份状态成了没人维护的孤儿。

    例外只有原文点名的两张**回流表**（见 :data:`WRITEBACK_TABLES`）：
    ``ods_mining_rule_config`` 与 ``dwd_mining_task_detail``。原文第四章明确
    「规则配置经 Flink CDC 同步入湖……任务与审核动作定期回写」——回流是设计，
    不是泄漏；它让平台的每一步操作都进血缘，且湖仓仍是唯一对账基准。
    """


# --------------------------------------------------------------------------- 主数据守卫

#: 一眼就是主数据的字段名（小写匹配）。⚠️ 原文未明确，本项目设计：
#: 原文只给了原则，没给字段级黑名单，这份清单是按「clip / 图片 / 标签 / 向量」四类主数据推断的。
_MASTER_DATA_FIELD_HINTS: tuple[str, ...] = (
    "image_bytes",
    "image_base64",
    "image_blob",
    "frame_bytes",
    "embedding",
    "vector",
    "vectors",
    "feature_vector",
    "point_cloud",
    "pointcloud",
    "raw_payload",
    "caption_text_batch",
    "tag_values",
    "clip_payload",
)

#: 允许出现的「指针型」字段——它们只是引用，不是数据本体。
_POINTER_FIELD_WHITELIST: tuple[str, ...] = (
    "vector_table",
    "vector_column",
    "vector_dim",
    "vector_index_type",
    "embedding_model",
    "embedding_model_version",
)

#: 单个标量字符串的长度上限。⚠️ 原文未明确，本项目设计：
#: 4096 字符足够放下最长的 SQL 谓词，又放不下任何有意义的图片/向量载荷。
_MAX_SCALAR_CHARS: int = 4096

#: 嵌套容器的最大元素数。⚠️ 原文未明确，本项目设计：超过它基本只能是批量主数据。
_MAX_SEQUENCE_ITEMS: int = 512

_BASE64_ISH = re.compile(r"^[A-Za-z0-9+/=\s]{512,}$")


def assert_no_master_data(payload: Mapping[str, Any], *, where: str) -> None:
    """守卫：控制面载荷里不允许出现主数据本体。

    检查三件事（任一命中即抛 :class:`MasterDataLeak`）：
      1. 字段名命中主数据黑名单（``embedding`` / ``image_bytes`` / ``tag_values`` …）；
      2. 出现 ``bytes`` / ``bytearray`` 值——控制面没有任何理由搬二进制；
      3. 标量过长或容器过大——疑似把整批数据当参数塞进来了。

    :param payload: 待检查的参数字典（``TaskEnvelope.params`` / ``RunReport.metrics`` 等）
    :param where: 出错信息里显示的位置，便于定位是谁塞的
    :raises MasterDataLeak: 命中任一条
    """

    def _walk(node: Any, path: str, depth: int) -> None:
        if depth > 8:  # ⚠️ 原文未明确，本项目设计：8 层足够表达任何任务参数
            raise MasterDataLeak(f"{where}: {path} 嵌套过深（>8 层），疑似夹带主数据")
        if isinstance(node, (bytes, bytearray, memoryview)):
            raise MasterDataLeak(
                f"{where}: {path} 是二进制载荷；主数据必须留在数据面（Paimon/OSS），"
                f"控制面只能传 artifact_id / object_key 这类指针"
            )
        if isinstance(node, str):
            if len(node) > _MAX_SCALAR_CHARS:
                raise MasterDataLeak(
                    f"{where}: {path} 长度 {len(node)} 超过 {_MAX_SCALAR_CHARS} 字符上限，疑似数据本体"
                )
            if _BASE64_ISH.match(node):
                raise MasterDataLeak(f"{where}: {path} 形似 base64 载荷，控制面不搬数据本体")
            return
        if isinstance(node, Mapping):
            for key, value in node.items():
                key_l = str(key).lower()
                if key_l in _POINTER_FIELD_WHITELIST:
                    _walk(value, f"{path}.{key}", depth + 1)
                    continue
                if any(
                    hint == key_l or key_l.endswith("_" + hint) for hint in _MASTER_DATA_FIELD_HINTS
                ):
                    raise MasterDataLeak(
                        f"{where}: 字段 {path}.{key} 属于主数据（clip / 图片 / 标签 / 向量），"
                        f"按第一设计原则「{K.FIRST_PRINCIPLE}」不得进入控制面"
                    )
                _walk(value, f"{path}.{key}", depth + 1)
            return
        if isinstance(node, Sequence):
            if len(node) > _MAX_SEQUENCE_ITEMS:
                raise MasterDataLeak(
                    f"{where}: {path} 含 {len(node)} 个元素，超过 {_MAX_SEQUENCE_ITEMS} 上限，疑似批量主数据"
                )
            for i, item in enumerate(node):
                _walk(item, f"{path}[{i}]", depth + 1)
            return
        # int / float / bool / None / datetime 之类标量直接放行

    _walk(dict(payload), where, 0)


# --------------------------------------------------------------------------- 控制态守卫

#: 原文第四章「控制面回流数据面」点名的两张回流落点表——**只有**这两张表可以装控制面运行态。
#: 其余任何数据面表出现控制态字段，都是「数据面开始存状态」，属边界违反。
WRITEBACK_TABLES: tuple[str, ...] = (K.TABLE_RULE_CONFIG, K.TABLE_TASK_DETAIL)

#: 一眼就是控制面运行态的字段名（小写匹配）。⚠️ 原文未明确，本项目设计：
#: 原文只给了「控制面存什么」的四类描述（规则配置 / 任务配置与执行状态 / 审核流状态 /
#: 热缓存），这份字段级清单是按那四类推断的。
CONTROL_STATE_FIELD_HINTS: tuple[str, ...] = (
    # 审核流状态
    "review_decision",
    "reviewer",
    "review_note",
    "approved_by",
    "rejected_by",
    # 任务配置与执行状态
    "idempotency_key",
    "external_handle",
    "attempt",
    "retry_count",
    "task_state",
    "dispatched_at",
    "queue_position",
    # 规则配置本体（规则的**引用** rule_id / rule_version 是允许的，本体不行）
    "rule_config",
    "rule_config_json",
    "rule_sql_template",
    # 热缓存
    "cache_key",
    "cache_ttl_seconds",
)


def assert_no_control_state(
    rows: Sequence[Mapping[str, Any]] | Mapping[str, Any],
    *,
    table: str,
    where: str,
) -> None:
    """守卫：数据面产物表里不允许出现控制面运行态。

    这是 :func:`assert_no_master_data` 的镜像。前者拦「控制面碰数据」，
    本函数拦「数据面存状态」——原文第四章把分离讲成两句话，代码里就得有两道闸。

    放行规则只有一条：目标表在 :data:`WRITEBACK_TABLES` 里（原文点名的两张回流表）
    时整张表放行，因为往那两张表里写控制面状态**正是原文要求的回流动作**。

    :param rows: 要写进数据面的行，或单独一行
    :param table: 目标表名
    :param where: 出错信息里显示的位置
    :raises ControlStateLeak: 非回流表里出现了控制态字段
    """
    if table in WRITEBACK_TABLES:
        return
    row_list: Sequence[Mapping[str, Any]]
    row_list = [rows] if isinstance(rows, Mapping) else list(rows)
    seen: set[str] = set()
    for row in row_list:
        seen.update(str(key).lower() for key in row)
    hit = sorted(seen & set(CONTROL_STATE_FIELD_HINTS))
    if hit:
        raise ControlStateLeak(
            f"{where}: 目标表 {table} 里出现了控制面运行态字段 {hit}；"
            f"控制面状态只能回流到 {list(WRITEBACK_TABLES)}（原文第四章「控制面回流数据面」），"
            f"写进产物表会让湖仓多出一份没人维护的状态副本，"
            f"健康判据「{K.HEALTH_CRITERION}」就不再成立"
        )


# --------------------------------------------------------------------------- 任务种类


class TaskKind(str, Enum):
    """控制面能编排的任务种类。

    与原文第三章「核心引擎层」的四个引擎一一对应，外加两个由应用服务层发起、
    但同样要落到数据面执行的动作（标签治理与圈选导出）。
    """

    #: 抽帧（K8s Job）——原文核心引擎层第 1 个引擎
    FRAME_SAMPLING = "frame_sampling"
    #: 规则挖掘（Spark 批 + Flink 流）——原文核心引擎层第 2 个引擎
    RULE_MINING = "rule_mining"
    #: VLM 推理（Ray + GPU）——原文核心引擎层第 3 个引擎
    VLM_INFERENCE = "vlm_inference"
    #: Embedding 流水线——原文核心引擎层第 4 个引擎，走凌晨窗口
    EMBEDDING = "embedding"
    #: 标签治理：字典 CRUD / 候选审核 approve-reject / 标签 merge（原文第六章标签治理类接口）
    TAG_GOVERNANCE = "tag_governance"
    #: 圈选导出：结果回写数据资产域，clip 级防泄漏（原文第六章数据集类接口）
    CURATION = "curation"

    @property
    def label(self) -> str:
        return _TASK_KIND_LABEL[self]

    @property
    def writeback_value(self) -> str:
        """回写 ``dwd_mining_task_detail.task_type`` 时用的取值。

        取值表由 catalog 定义（该列注释：``rule_mining/frame_extract/vlm_infer/embedding``），
        catalog 是表结构与取值的唯一事实源，所以这里做一次转译而不是直接写枚举名。
        ⚠️ 标签治理与圈选导出在 catalog 的取值表里**没有对应值**，暂用自身 slug，
        待 catalog 补齐（见 :func:`writeback_column_gap`）。
        """
        return _TASK_KIND_WRITEBACK.get(self, self.value)


_TASK_KIND_LABEL: dict[TaskKind, str] = {
    TaskKind.FRAME_SAMPLING: "抽帧（K8s Job）",
    TaskKind.RULE_MINING: "规则挖掘（Spark 批 + Flink 流）",
    TaskKind.VLM_INFERENCE: "VLM 推理（Ray + GPU）",
    TaskKind.EMBEDDING: "Embedding 流水线",
    TaskKind.TAG_GOVERNANCE: "统一标签治理",
    TaskKind.CURATION: "圈选导出",
}

#: 任务种类 -> ``dwd_mining_task_detail.task_type`` 的取值（取值表见 catalog 该列注释）。
_TASK_KIND_WRITEBACK: dict[TaskKind, str] = {
    TaskKind.FRAME_SAMPLING: "frame_extract",
    TaskKind.RULE_MINING: "rule_mining",
    TaskKind.VLM_INFERENCE: "vlm_infer",
    TaskKind.EMBEDDING: "embedding",
    # ⚠️ 以下两类 catalog 取值表里没有，暂用自身 slug，待 catalog 补齐
    TaskKind.TAG_GOVERNANCE: "tag_governance",
    TaskKind.CURATION: "curation",
}

#: 任务种类 -> 子系统名。控制面只认这张表，永远不 import 子系统内部实现。
#:
#: ⚠️ 原文未明确，本项目设计：原文没有把「引擎」映射到代码子系统。本项目按职责就近落位：
#:   · 抽帧 → sampling（分层抽帧策略是系列三第 2 篇 S3-02 的主题）
#:   · 规则挖掘 / VLM 推理 → mining（两者都是「挖掘引擎」，共用规则与场景语义）
#:   · 标签治理 → tags（原文「一律经统一标签服务收口」）
#:   · 圈选导出 → mining（它本质是一次带过滤条件的批量圈选，与规则挖掘同款 Spark 批作业）
#:   · Embedding → vector（可选子系统；原文把它与 VLM 推理并列在 GPU 池里）
SUBSYSTEM_ROUTING: dict[TaskKind, str] = {
    TaskKind.FRAME_SAMPLING: "sampling",
    TaskKind.RULE_MINING: "mining",
    TaskKind.VLM_INFERENCE: "mining",
    TaskKind.EMBEDDING: "vector",
    TaskKind.TAG_GOVERNANCE: "tags",
    TaskKind.CURATION: "mining",
}

#: 产出需要人工审核才能生效的任务种类。
#:
#: 原文依据：第六章标签治理类接口「候选审核 approve / reject」——大模型标签是候选，
#: 审核通过才进统一字典。⚠️ 原文未明确，本项目设计：把「哪些种类必须过审核流」显式化，
#: 原文只说存在审核流状态，没有列出触发条件。
REVIEW_REQUIRED_KINDS: frozenset[TaskKind] = frozenset(
    {TaskKind.VLM_INFERENCE, TaskKind.TAG_GOVERNANCE}
)


# --------------------------------------------------------------------------- 任务状态


class TaskState(str, Enum):
    """任务生命周期状态。

    ⚠️ 原文未明确，本项目设计：原文只说控制面 MySQL 存「任务配置与执行状态、审核流状态」，
    并给了 ``GET /jobs/{jobId}/progress`` 查进度，但没有列状态机。以下 10 态是本项目
    按「提交 → 排队 → 下发 → 运行 → 审核 → 终态」补全的，状态迁移表见 lifecycle.py。
    """

    DRAFT = "draft"  # 控制台草稿，未提交
    SUBMITTED = "submitted"  # 经 OpenAPI 提交，幂等键已登记
    QUEUED = "queued"  # 通过准入校验，进入控制面队列
    DISPATCHED = "dispatched"  # 已下发给数据面子系统，拿到外部作业句柄
    RUNNING = "running"  # 数据面确认开跑
    AWAITING_REVIEW = "awaiting_review"  # 数据面跑完，产物待审核（候选标签）
    SUCCEEDED = "succeeded"  # 终态：成功
    FAILED = "failed"  # 终态（可重试）：失败
    REJECTED = "rejected"  # 终态：审核驳回
    CANCELLED = "cancelled"  # 终态：人工取消

    @property
    def is_terminal(self) -> bool:
        return self in TERMINAL_STATES

    @property
    def writeback_value(self) -> str:
        """回写 ``dwd_mining_task_detail.task_status`` 时用的取值。

        控制面的 10 态状态机（⚠️ 本项目设计）要收敛到 catalog 该列的取值表
        ``pending/running/success/failed/skipped/canceled``——catalog 是唯一事实源，
        控制面不能往湖仓里写它不认识的状态字面量。
        ⚠️ ``rejected``（审核驳回）在 catalog 取值表里没有对应值：它既不是执行失败
        也不是取消，不能硬塞进 ``failed``，因此原样写出，待 catalog 补齐
        （见 :func:`writeback_column_gap`）。细粒度的 10 态仍完整保留在控制面
        ``task_state`` 与事件流里，回写只是投影。
        """
        return _TASK_STATE_WRITEBACK[self]


#: 终态集合。终态任务不再占用调度循环。
TERMINAL_STATES: frozenset[TaskState] = frozenset(
    {TaskState.SUCCEEDED, TaskState.FAILED, TaskState.REJECTED, TaskState.CANCELLED}
)

#: 控制面 10 态 -> ``dwd_mining_task_detail.task_status`` 取值（catalog 该列注释定义）。
_TASK_STATE_WRITEBACK: dict[TaskState, str] = {
    TaskState.DRAFT: "pending",
    TaskState.SUBMITTED: "pending",
    TaskState.QUEUED: "pending",
    TaskState.DISPATCHED: "running",
    TaskState.RUNNING: "running",
    TaskState.AWAITING_REVIEW: "running",
    TaskState.SUCCEEDED: "success",
    TaskState.FAILED: "failed",
    TaskState.CANCELLED: "canceled",  # 注意 catalog 拼的是单 l 的 canceled
    TaskState.REJECTED: "rejected",  # ⚠️ catalog 取值表暂无，待补
}


class ReviewDecision(str, Enum):
    """审核动作。原文第六章逐字给的是 ``approve / reject``。"""

    APPROVE = "approve"
    REJECT = "reject"


# --------------------------------------------------------------------------- 载荷


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """数据面产物的**指针**。注意这里没有任何数据本体字段，只有 ID 与落点。

    ``artifact_id`` 由 :func:`adas_lakehouse.ids.derive_artifact_id` 生成；
    ``parent_artifact_id`` 冗余落表，作为图库血缘的对账兜底。
    """

    artifact_id: str
    table: str
    row_count: int = 0
    parent_artifact_id: str | None = None
    status: ArtifactStatus = ArtifactStatus.ACTIVE

    def as_row(self) -> dict[str, Any]:
        """展平成一行，供回写 ``dwd_mining_task_detail`` 与血缘登记使用。"""
        return {
            "artifact_id": self.artifact_id,
            "target_table": self.table,
            "row_count": self.row_count,
            "parent_artifact_id": self.parent_artifact_id,
            "artifact_status": self.status.value,
        }


@dataclass(frozen=True, slots=True)
class TaskEnvelope:
    """控制面 → 数据面的唯一载体（「信封」）。

    信封里装的全是「怎么算」：规则版本、算法参数、输入选择器（一段 SQL 谓词）、
    优先级、run_id。**不装数据本体**——构造时即调 :func:`assert_no_master_data` 自检。

    数据面拿到信封后自己去湖仓读数据，读完把产物写回湖仓，只把指针回报给控制面。
    这就是「平台从不自建数据通道，永远走在湖仓既有的链路上」（原文第六章）。
    """

    task_id: str
    kind: TaskKind
    subsystem: str
    run_id: str
    #: 输入选择器：一段作用在数据面表上的 SQL 谓词，不是数据本身
    input_selector: str = ""
    #: 输入表（数据面），默认由子系统按 kind 决定
    input_tables: tuple[str, ...] = ()
    #: 算法参数快照（标量为主）。会被主数据守卫扫描。
    params: Mapping[str, Any] = field(default_factory=dict)
    #: 引用的规则配置（rule_id, rule_version）——规则本体存控制面，经 CDC 回流数据面
    rule_id: str | None = None
    rule_version: int | None = None
    #: 数值越小越优先，供 GPU 优先级队列复用
    priority: int = K.PRIORITY_DEFAULT
    requested_by: str = "system"
    created_at: datetime = field(default_factory=datetime.now)
    #: 幂等键：原文第六章「任务类……幂等键防重复提交」
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        parse_run_id(self.run_id)  # 早失败：run_id 必须合法，否则血缘断链
        if self.priority < K.PRIORITY_HIGHEST or self.priority > K.PRIORITY_LOWEST:
            raise ValueError(
                f"priority 必须落在 [{K.PRIORITY_HIGHEST}, {K.PRIORITY_LOWEST}]，收到 {self.priority}"
            )
        assert_no_master_data(self.params, where=f"TaskEnvelope(task_id={self.task_id}).params")
        if len(self.input_selector) > _MAX_SCALAR_CHARS:
            raise MasterDataLeak(
                f"TaskEnvelope(task_id={self.task_id}).input_selector 超过 {_MAX_SCALAR_CHARS} 字符；"
                f"选择器是谓词，不是数据"
            )

    def to_dict(self) -> dict[str, Any]:
        """序列化成可走 HTTP/Kafka 的纯字典。"""
        return {
            "task_id": self.task_id,
            "kind": self.kind.value,
            "subsystem": self.subsystem,
            "run_id": self.run_id,
            "input_selector": self.input_selector,
            "input_tables": list(self.input_tables),
            "params": dict(self.params),
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "priority": self.priority,
            "requested_by": self.requested_by,
            "created_at": self.created_at.isoformat(timespec="seconds"),
            "idempotency_key": self.idempotency_key,
        }


@dataclass(frozen=True, slots=True)
class RunReport:
    """数据面 → 控制面的唯一载体。同样只回指针，不回数据。

    ``state`` 只允许 RUNNING / SUCCEEDED / FAILED / AWAITING_REVIEW 四种——
    排队、下发、取消都是控制面自己的事，数据面无权宣告。
    """

    run_id: str
    task_id: str
    state: TaskState
    engine: str = ""
    artifacts: tuple[ArtifactRef, ...] = ()
    rows_written: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    message: str = ""
    #: 轻量指标（耗时、扫描行数、GPU 占用秒数…），同样被主数据守卫扫描
    metrics: Mapping[str, Any] = field(default_factory=dict)

    _ALLOWED_STATES = (
        TaskState.RUNNING,
        TaskState.SUCCEEDED,
        TaskState.FAILED,
        TaskState.AWAITING_REVIEW,
    )

    def __post_init__(self) -> None:
        parse_run_id(self.run_id)
        if self.state not in self._ALLOWED_STATES:
            raise PlaneBoundaryError(
                f"数据面无权宣告状态 {self.state.value}；只能回报 "
                f"{[s.value for s in self._ALLOWED_STATES]}——排队/下发/取消由控制面决定"
            )
        assert_no_master_data(self.metrics, where=f"RunReport(run_id={self.run_id}).metrics")

    @property
    def progress_percent(self) -> int:
        """粗粒度进度，供 ``GET /jobs/{jobId}/progress``。

        ⚠️ 原文未明确，本项目设计：原文只给了进度查询接口，没给进度语义。
        这里用状态映射给出保守的百分比，真实细粒度进度由子系统在 metrics 里带
        ``progress_percent`` 覆盖。
        """
        reported = self.metrics.get("progress_percent")
        if isinstance(reported, (int, float)) and 0 <= reported <= 100:
            return int(reported)
        return {
            TaskState.RUNNING: 50,
            TaskState.AWAITING_REVIEW: 90,
            TaskState.SUCCEEDED: 100,
            TaskState.FAILED: 100,
        }[self.state]


# --------------------------------------------------------------------------- 控制面记录


@dataclass(frozen=True, slots=True)
class TaskEvent:
    """一条状态迁移审计记录。

    原文第四章：「平台的每一步操作都进血缘，与湖仓闭环」——事件流就是那「每一步」，
    由 reconcile.py 定期回写 ``dwd_mining_task_detail``。
    """

    task_id: str
    seq: int
    from_state: TaskState | None
    to_state: TaskState
    at: datetime
    actor: str = "system"
    detail: str = ""

    def as_row(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "event_seq": self.seq,
            "from_state": self.from_state.value if self.from_state else None,
            "to_state": self.to_state.value,
            "event_time": self.at,
            "actor": self.actor,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class TaskRecord:
    """控制面 MySQL 里的一行任务。**这一行丢了可以重建，业务数据不受影响。**

    它持有的全部是「运行态」：状态、外部作业句柄、重试次数、审核结论、产物指针。
    产物本体在湖仓，这里只存 artifact_id。
    """

    envelope: TaskEnvelope
    state: TaskState = TaskState.DRAFT
    external_handle: str | None = None
    attempt: int = 0
    artifacts: tuple[ArtifactRef, ...] = ()
    rows_written: int = 0
    review_decision: ReviewDecision | None = None
    reviewer: str | None = None
    message: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    #: 数据面确认开跑的时刻。回写成 ``dwd_mining_task_detail.start_time``
    #: （catalog 要求的「执行时间」三件套之一），由 lifecycle 在进 RUNNING 时打点。
    started_at: datetime | None = None
    #: 进终态的时刻，回写成 ``end_time``。
    finished_at: datetime | None = None
    #: 是否已回写数据面（dwd_mining_task_detail）
    written_back: bool = False

    @property
    def task_id(self) -> str:
        return self.envelope.task_id

    @property
    def duration_seconds(self) -> float | None:
        """执行耗时，回写成 ``duration_sec``。没开跑或没跑完时为 ``None``。"""
        if self.started_at is None or self.finished_at is None:
            return None
        return round((self.finished_at - self.started_at).total_seconds(), 3)

    @property
    def kind(self) -> TaskKind:
        return self.envelope.kind

    @property
    def subsystem(self) -> str:
        return self.envelope.subsystem

    def evolve(self, **changes: Any) -> TaskRecord:
        """返回一个更新了字段的新记录（不可变记录的标准做法），自动刷新 updated_at。"""
        changes.setdefault("updated_at", datetime.now())
        return replace(self, **changes)

    def as_writeback_row(self) -> dict[str, Any]:
        """展平成回写 ``dwd_mining_task_detail`` 的一行（原文第四章「控制面回流数据面」）。

        **列名与取值一律以 catalog 为准**：``catalog/registry.py`` 是表结构的唯一事实源，
        控制面内部叫 ``task_id/kind/state``，落表就得叫 ``mining_task_id/task_type/
        task_status``，取值也要收敛到该列注释给的取值表（见
        :attr:`TaskKind.writeback_value` / :attr:`TaskState.writeback_value`）。
        映射关系见 :data:`WRITEBACK_COLUMN_MAP`。

        ``data_id`` 不在任务粒度上（任务是 clip 集合级），因此用 ``run_id`` 串血缘。

        ⚠️ :data:`WRITEBACK_UNMAPPED_FIELDS` 里的字段在 catalog 当前的
        ``dwd_mining_task_detail`` 里**还没有列**（子系统名、重试次数、产物指针、
        审核结论……）。它们照常写出——回写是 at-least-once 的 Upsert，多余的键由写入
        实现忽略——并由 :func:`writeback_column_gap` 在运行时报出来，交给建表侧补列。
        少写一个键会让审计信息凭空消失，比多写一个键更糟。
        """
        head = self.artifacts[0] if self.artifacts else None
        return {
            # ---- 与 catalog 列一一对应 ----
            "mining_task_id": self.task_id,
            "run_id": self.envelope.run_id,
            "rule_id": self.envelope.rule_id,
            "rule_version": self.envelope.rule_version,
            "task_type": self.kind.writeback_value,
            "task_status": self.state.writeback_value,
            "engine": self.envelope.subsystem,
            "start_time": self.started_at,
            "end_time": self.finished_at,
            "duration_sec": self.duration_seconds,
            "error_message": self.message[:2000],
            # ---- ⚠️ catalog 尚无对应列，见 WRITEBACK_UNMAPPED_FIELDS ----
            "task_state_detail": self.state.value,
            "subsystem": self.subsystem,
            "priority": self.envelope.priority,
            "requested_by": self.envelope.requested_by,
            "attempt": self.attempt,
            "artifact_id": head.artifact_id if head else None,
            "parent_artifact_id": head.parent_artifact_id if head else None,
            "artifact_status": (head.status.value if head else None),
            "artifact_count": len(self.artifacts),
            "rows_written": self.rows_written,
            "review_decision": self.review_decision.value if self.review_decision else None,
            "reviewer": self.reviewer,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# --------------------------------------------------------------------------- 回写列对账

#: 控制面字段 -> ``dwd_mining_task_detail`` 列名。catalog 是表结构唯一事实源，
#: 这张表是「控制面内部叫法」到「湖仓列名」的唯一转译处。
WRITEBACK_COLUMN_MAP: dict[str, str] = {
    "task_id": "mining_task_id",
    "run_id": "run_id",
    "rule_id": "rule_id",
    "rule_version": "rule_version",
    "kind": "task_type",
    "state": "task_status",
    "subsystem": "engine",
    "started_at": "start_time",
    "finished_at": "end_time",
    "duration_seconds": "duration_sec",
    "message": "error_message",
}

#: ⚠️ 控制面要回写、但 catalog 的 ``dwd_mining_task_detail`` 目前**没有列**的键。
#: 这不是可以省掉的字段——原文第四章要求「平台的每一步操作都进血缘」，审核结论、
#: 重试次数、产物指针都是「每一步」的一部分。列要由建表侧（catalog/tables/_mining.py）补。
WRITEBACK_UNMAPPED_FIELDS: tuple[str, ...] = (
    "task_state_detail",
    "subsystem",
    "priority",
    "requested_by",
    "attempt",
    "artifact_id",
    "parent_artifact_id",
    "artifact_status",
    "artifact_count",
    "rows_written",
    "review_decision",
    "reviewer",
    "created_at",
    "updated_at",
)


def writeback_column_gap(record: TaskRecord | None = None) -> dict[str, list[str]]:
    """拿回写行去对 catalog 的表结构，报出两侧的缺口。

    这是「控制面回流数据面」这条链路的**可执行对账**：回写行的键必须都是目标表的列，
    否则那部分审计信息写不进湖仓，血缘就断在控制面这一侧。

    :return: ``{"missing_columns": [...], "table": ...}``——``missing_columns``
        是回写行里有、而 catalog 表里没有的列，需要建表侧补。
        catalog 一旦补上，这个列表自动变短，不需要改本函数。
    """
    from ..catalog import registry  # 延迟 import：契约模块不该在顶层拖进 catalog

    spec = registry.by_name(K.TABLE_TASK_DETAIL)
    columns = {c.name for c in spec.columns} if spec else set()
    if record is None:
        keys = set(WRITEBACK_COLUMN_MAP.values()) | set(WRITEBACK_UNMAPPED_FIELDS)
    else:
        keys = set(record.as_writeback_row())
    return {
        "table": K.TABLE_TASK_DETAIL,
        "missing_columns": sorted(keys - columns),
        "mapped_columns": sorted(keys & columns),
    }
