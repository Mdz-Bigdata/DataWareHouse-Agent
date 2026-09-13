"""湖图双引擎的图模型：五类节点、七类关系、三级血缘、三套状态机。

一句话分工（[a13] 二）：**湖仓保存数据处理的元信息（唯一事实源），图库保存血缘节点
与关系（关系视图）**。三级 ID 是两边的统一语言——同一个 ID 在湖仓是主键、在图库是
节点 ``id`` 属性，跨库 JOIN 零成本。

铁律（[a13] 六·属性单一事实源）
    图库只存节点与关系（ID + 关系类型），属性、参数快照与指标一律取自湖仓，
    避免双源冗余不一致。

⚠️ 原文自相矛盾之处，本项目的处理（必须诚实标注）
    [a13] 4.1 的建图语句其实往图库写了属性::

        MERGE (a:Artifact {id:'..._slam_v4_b7e8f9a0'})
        SET a.step='slam', a.algo_version='v4', a.status='active';
        MERGE (r:Run {id:'run_slam_20240116103000_9f3a21c7'})
        SET r.status='success', r.params='{"max_iter":200}';

    而 [a13] 六的护栏又说「属性一律取自湖仓」。5.1 的影响分析 Cypher
    ``MATCH (a:Artifact {step:'slam', algo_version:'v3'})`` 也必须依赖图库上的属性
    才能做谓词下推。两者不可能同时满足。

    本项目的取舍：以护栏为准，但保留一个**极小的遍历键白名单**
    （:data:`TRAVERSAL_PROPERTIES`）——只放「不落图库就没法做谓词过滤 / 路径剪枝」
    的低基数标识型字段（step / algo_version / status / dataset_id / version），
    其余一律拉黑（:data:`FORBIDDEN_GRAPH_PROPERTIES`，含 params / param_snapshot /
    content_hash / 质量指标）。想要 100% 严格的护栏语义，把写入策略切成
    :attr:`GraphPropertyPolicy.ID_ONLY` 即可，此时影响分析改为「先回湖仓查 ID 列表，
    再拿 ID 进图库遍历」。

出处见 :mod:`adas_lakehouse.lineage.constants`。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum

from ..ids import ArtifactStatus
from .constants import (
    FACT_TABLE_COUNT,
    NODE_LABEL_COUNT,
    REL_TYPE_COUNT,
    SOURCE_A11,
    SOURCE_A13,
)

__all__ = [
    "NodeLabel",
    "RelType",
    "LineageLevel",
    "GraphPropertyPolicy",
    "RunStatus",
    "DatasetVersionStatus",
    "ArtifactStatus",
    "NodeRef",
    "GraphNode",
    "GraphEdge",
    "GraphMutation",
    "LakehouseSource",
    "RelSpec",
    "REL_SPECS",
    "NODE_SOURCE_TABLES",
    "TRAVERSAL_PROPERTIES",
    "FORBIDDEN_GRAPH_PROPERTIES",
    "ARTIFACT_TRANSITIONS",
    "RUN_TRANSITIONS",
    "DATASET_VERSION_TRANSITIONS",
    "CONSISTENCY_REDLINES",
    "GUARDRAILS",
    "LineageModelError",
    "InvalidTransitionError",
    "sanitize_properties",
    "check_transition",
    "lakehouse_source_for",
]


class LineageModelError(ValueError):
    """图模型层的通用错误：端点类型不匹配、属性越界、状态流转非法等。"""


class InvalidTransitionError(LineageModelError):
    """状态机非法流转。见 [a13] 4.2 状态机表。"""


# --------------------------------------------------------------------------- 节点


class NodeLabel(str, Enum):
    """图库五类节点。出处 [a13] 二：「图库里共五类节点」。

    节点唯一标识统一落在 ``id`` 属性上（[a13] 4.1 全部 MERGE 语句都是 ``{id: '...'}``），
    取值就是三级 ID 或业务 ID：

    ============== ================================ ======================
    节点            id 取值                           湖仓事实表
    ============== ================================ ======================
    Clip           data_id                          dwd_collect_clip_detail
    Artifact       artifact_id                      dwd_production_artifact_detail
    Run            run_id                           dwd_production_run_detail
    DatasetVersion dataset_version_id（如 DS_0001_V2） dwd_dataset_version_detail
    Badcase        badcase_id（如 BC_20240120_001）   ⚠️ 见 NODE_SOURCE_TABLES
    ============== ================================ ======================
    """

    CLIP = "Clip"
    ARTIFACT = "Artifact"
    RUN = "Run"
    DATASET_VERSION = "DatasetVersion"
    BADCASE = "Badcase"


class RelType(str, Enum):
    """图库七类关系。出处 [a13] 二：

    「七类关系——CONTAINS / DERIVED_FROM / SUPERSEDED_BY / INPUT / PRODUCED /
    REFERENCES / TRACED_TO」。
    """

    CONTAINS = "CONTAINS"
    DERIVED_FROM = "DERIVED_FROM"
    SUPERSEDED_BY = "SUPERSEDED_BY"
    INPUT = "INPUT"
    PRODUCED = "PRODUCED"
    REFERENCES = "REFERENCES"
    TRACED_TO = "TRACED_TO"


class LineageLevel(str, Enum):
    """三级血缘模型。出处 [a13] 三的表格，每一级「由图库的一类关系承载、
    湖仓的一个冗余字段兜底」。"""

    ENTITY = "entity"  # 实体血缘：clip → 各环节产物 → 数据集版本的父子链
    VERSION = "version"  # 版本血缘：同一环节不同算法版本产物构成版本分支
    RUN = "run"  # 运行血缘：每次处理的输入 / 输出 / 参数快照 / 时间


class GraphPropertyPolicy(str, Enum):
    """图库属性写入策略——护栏「属性单一事实源」的两档实现。

    ``ID_ONLY``
        最严格：图库只有 ``id`` + 关系类型，一个业务属性都不落。完全符合 [a13] 六
        的字面表述；代价是影响分析这类「按 step/algo_version 过滤」的查询必须先回
        湖仓取 ID 列表。
    ``TRAVERSAL_KEYS``
        默认：额外允许 :data:`TRAVERSAL_PROPERTIES` 白名单里的低基数遍历键。
        这一档才能跑通 [a13] 5.1 原样的影响分析 Cypher。
    """

    ID_ONLY = "id_only"
    TRAVERSAL_KEYS = "traversal_keys"


class RunStatus(str, Enum):
    """运行状态。出处 [a13] 4.2：「running → success / failed / cancelled，
    触发事件：产线执行结果回写」。"""

    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DatasetVersionStatus(str, Enum):
    """数据集版本状态。出处 [a13] 4.2：「draft → released → deprecated，
    触发事件：人工发布 / 被新版本替代」。"""

    DRAFT = "draft"
    RELEASED = "released"
    DEPRECATED = "deprecated"


# --------------------------------------------------------------------------- 状态机

#: artifact 状态流转。出处 [a13] 4.2 前两行：
#:   active → superseded  触发：同 (data_id, step) 新版本产物写入成功
#:   active → invalid     触发：质检 / 评测判定不可用（节点保留供追溯）
#: superseded / invalid 是终态——⚠️ 原文未明确是否允许回滚，本项目设计：不允许，
#: 「可演进」的前提是历史可查（[a13] 3.2 实践提醒：重刷绝不是删旧写新）。
ARTIFACT_TRANSITIONS: dict[ArtifactStatus, tuple[ArtifactStatus, ...]] = {
    ArtifactStatus.ACTIVE: (ArtifactStatus.SUPERSEDED, ArtifactStatus.INVALID),
    ArtifactStatus.SUPERSEDED: (),
    ArtifactStatus.INVALID: (),
}

#: run 状态流转。出处 [a13] 4.2 第三行。
RUN_TRANSITIONS: dict[RunStatus, tuple[RunStatus, ...]] = {
    RunStatus.RUNNING: (RunStatus.SUCCESS, RunStatus.FAILED, RunStatus.CANCELLED),
    RunStatus.SUCCESS: (),
    RunStatus.FAILED: (),
    RunStatus.CANCELLED: (),
}

#: 数据集版本状态流转。出处 [a13] 4.2 第四行。
#: ⚠️ 原文未明确 draft 能否直接废弃，本项目设计：允许 draft → deprecated（草稿作废）。
DATASET_VERSION_TRANSITIONS: dict[DatasetVersionStatus, tuple[DatasetVersionStatus, ...]] = {
    DatasetVersionStatus.DRAFT: (
        DatasetVersionStatus.RELEASED,
        DatasetVersionStatus.DEPRECATED,
    ),
    DatasetVersionStatus.RELEASED: (DatasetVersionStatus.DEPRECATED,),
    DatasetVersionStatus.DEPRECATED: (),
}


def check_transition(current: Enum, target: Enum) -> None:
    """校验状态流转是否合法，非法则抛 :class:`InvalidTransitionError`。

    同状态自转（幂等重放同一条事件）一律放行——对账链路（链路三）是可重复执行的，
    幂等是它的前提（[a13] 四·链路三「幂等可重复执行」）。

    :param current: 当前状态，必须是 ArtifactStatus / RunStatus / DatasetVersionStatus 之一
    :param target: 目标状态，需与 current 同类型
    :raises InvalidTransitionError: 类型不匹配或流转不在状态机白名单内
    """
    if type(current) is not type(target):
        raise InvalidTransitionError(
            f"状态类型不一致: {type(current).__name__} → {type(target).__name__}"
        )
    if current == target:
        return
    table: Mapping[Enum, tuple[Enum, ...]]
    if isinstance(current, ArtifactStatus):
        table = ARTIFACT_TRANSITIONS
    elif isinstance(current, RunStatus):
        table = RUN_TRANSITIONS
    elif isinstance(current, DatasetVersionStatus):
        table = DATASET_VERSION_TRANSITIONS
    else:  # pragma: no cover - Enum 类型受限于上面三种
        raise InvalidTransitionError(f"没有为 {type(current).__name__} 定义状态机")
    allowed = table.get(current, ())
    if target not in allowed:
        allowed_txt = "/".join(s.value for s in allowed) or "（终态，不可再流转）"
        raise InvalidTransitionError(
            f"非法状态流转 {current.value} → {target.value}；允许的目标: {allowed_txt}"
        )


# --------------------------------------------------------------------------- 湖仓来源


@dataclass(frozen=True, slots=True)
class LakehouseSource:
    """一条「湖仓数据来源」记录：表名 + 主键列 + 主键值。

    护栏四「结果可审计」（[a13] 六）要求：**查询结果同时返回血缘路径与湖仓数据来源
    （表名 + ID），可复现、可审计**。所有查询结果里的 ``sources`` 就是这个类型。
    """

    table: str
    id_column: str
    id_value: str
    layer: str = "dwd"

    def as_dict(self) -> dict[str, str]:
        """转成可直接 JSON 序列化的审计条目。"""
        return {
            "table": self.table,
            "id_column": self.id_column,
            "id_value": self.id_value,
            "layer": self.layer,
        }

    @property
    def locator(self) -> str:
        """人可读的定位串，形如 ``dwd_production_artifact_detail.artifact_id=COLLECT_...``。"""
        return f"{self.table}.{self.id_column}={self.id_value}"

    def is_registered(self) -> bool:
        """该表是否已在 catalog.registry 登记。

        血缘模块不拥有这四张事实表（它们归 catalog/tables/ 下的各域模块），
        因此这里只做「软探测」：登记了就是 True，没登记（或 registry 还没装配到该域）
        返回 False，不抛异常、不阻断查询。
        """
        try:
            from ..catalog import registry

            registry.by_name(self.table)
        except Exception:
            return False
        return True


#: 节点 → 湖仓事实表 + 主键列。出处 [a13] 3.1「湖仓四张元信息表：血缘的事实源」。
#:
#: 前四张表原文逐字给出。第五类节点 Badcase 在 [a13] 二被列为图库五类节点之一，
#: 但 3.1 的四张事实表里并没有它——
#: ⚠️ 原文未明确，本项目设计：Badcase 的事实源落在评测域（DataDomain.EVALUATION
#: 的语义是「评测/Badcase/根因分布」），即该域已登记的 dwd_badcase_detail。
#: 该表归评测域模块所有，本模块只引用不定义——表名一律以 catalog.registry 为准，
#: 血缘侧不得自造别名（LakehouseSource.is_registered() 可探测）。
NODE_SOURCE_TABLES: dict[NodeLabel, tuple[str, str]] = {
    NodeLabel.CLIP: ("dwd_collect_clip_detail", "data_id"),
    NodeLabel.ARTIFACT: ("dwd_production_artifact_detail", "artifact_id"),
    NodeLabel.RUN: ("dwd_production_run_detail", "run_id"),
    NodeLabel.DATASET_VERSION: ("dwd_dataset_version_detail", "dataset_version_id"),
    NodeLabel.BADCASE: ("dwd_badcase_detail", "badcase_id"),
}
assert len(NODE_SOURCE_TABLES) == NODE_LABEL_COUNT, "五类节点必须各有一个湖仓事实源"


def lakehouse_source_for(label: NodeLabel, node_id: str) -> LakehouseSource:
    """给定节点标签与节点 ID，返回它在湖仓的来源坐标（表名 + 主键列 + 值）。

    :param label: 节点标签
    :param node_id: 节点 ID（即三级 ID 或业务 ID）
    :raises LineageModelError: 未知标签
    """
    try:
        table, column = NODE_SOURCE_TABLES[label]
    except KeyError as exc:  # pragma: no cover - NodeLabel 是封闭枚举
        raise LineageModelError(f"未知节点标签: {label!r}") from exc
    return LakehouseSource(table=table, id_column=column, id_value=node_id)


# --------------------------------------------------------------------------- 关系规格


@dataclass(frozen=True, slots=True)
class RelSpec:
    """一类关系的完整规格：端点类型、血缘级别、湖仓对账字段、语义说明。

    ``reconcile_column`` 是 [a13] 四·链路三的核心——湖仓的关系冗余字段
    「不是主存储，而是图库实时更新失败时的兜底对账源」（[a13] 3.1）。
    T+1 对账就是拿这些列去 UPSERT 图库的边。
    """

    rel: RelType
    src: NodeLabel
    dst: NodeLabel
    level: LineageLevel
    reconcile_table: str
    reconcile_column: str
    multi_valued: bool
    meaning: str
    source_note: str

    @property
    def signature(self) -> str:
        """``(Src)-[:REL]->(Dst)`` 形式的签名，用于日志与错误信息。"""
        return f"({self.src.value})-[:{self.rel.value}]->({self.dst.value})"


#: 七类关系的规格表。
#:
#: 方向的取证：
#:   * SUPERSEDED_BY —— [a13] 4.1 原文 ``MERGE (old)-[:SUPERSEDED_BY]->(a)``，旧 → 新。
#:   * INPUT / PRODUCED —— [a13] 4.1 原文 ``MERGE (r)-[:INPUT]->(:Artifact {...})``
#:     与 ``MERGE (r)-[:PRODUCED]->(a)``，均由 Run 指向 Artifact。
#:   * REFERENCES —— [a13] 4.1 原文 ``MERGE (d)-[:REFERENCES]->(a)``，数据集版本 → 产物；
#:     [a13] 5.1 影响分析里写作 ``(a:Artifact)<-[:REFERENCES]-(d:DatasetVersion)``，方向一致。
#:   * TRACED_TO —— [a13] 5.1 原文 ``(b:Badcase)-[:TRACED_TO]->(a:Artifact)``。
#:   * CONTAINS —— ⚠️ 原文只在 [a13] 二列了关系名、三说了「实体血缘由 CONTAINS /
#:     DERIVED_FROM 承载」，没给 Cypher。本项目设计：Clip 包含它的各环节产物，
#:     方向 Clip → Artifact，对账列是产物表上的 data_id（产物内嵌 data_id，[a11] 二）。
#:   * DERIVED_FROM —— ⚠️ 原文没给 Cypher，但 [a11] 三·规则 4 写明「衍生产物通过湖仓
#:     parent_artifact_id 字段 + 图数据库关系（Neo4j DERIVED_FROM 边）关联输入产物」，
#:     即边从子产物指向父产物：(child)-[:DERIVED_FROM]->(parent)。
REL_SPECS: dict[RelType, RelSpec] = {
    RelType.CONTAINS: RelSpec(
        rel=RelType.CONTAINS,
        src=NodeLabel.CLIP,
        dst=NodeLabel.ARTIFACT,
        level=LineageLevel.ENTITY,
        reconcile_table="dwd_production_artifact_detail",
        reconcile_column="data_id",
        multi_valued=False,
        meaning="clip 包含其各环节产物（追溯起点）",
        source_note="⚠️ 方向为本项目设计；[a13] 二/三只给了关系名与承载职责",
    ),
    RelType.DERIVED_FROM: RelSpec(
        rel=RelType.DERIVED_FROM,
        src=NodeLabel.ARTIFACT,
        dst=NodeLabel.ARTIFACT,
        level=LineageLevel.ENTITY,
        reconcile_table="dwd_production_artifact_detail",
        reconcile_column="parent_artifact_id",
        multi_valued=False,
        meaning="子产物派生自父产物，逐级追溯至源头 clip",
        source_note="[a11] 三·规则 4 / [a13] 3.1 DDL 注释「实体血缘冗余字段（DERIVED_FROM 对账源）」",
    ),
    RelType.SUPERSEDED_BY: RelSpec(
        rel=RelType.SUPERSEDED_BY,
        src=NodeLabel.ARTIFACT,
        dst=NodeLabel.ARTIFACT,
        level=LineageLevel.VERSION,
        reconcile_table="dwd_production_artifact_detail",
        reconcile_column="superseded_by_artifact_id",
        multi_valued=False,
        meaning="旧版本产物被新版本替代，形成版本分支（v3/v4 并存）",
        source_note="[a13] 4.1 `MERGE (old)-[:SUPERSEDED_BY]->(a)` / 3.1 DDL「版本血缘冗余字段」",
    ),
    RelType.INPUT: RelSpec(
        rel=RelType.INPUT,
        src=NodeLabel.RUN,
        dst=NodeLabel.ARTIFACT,
        level=LineageLevel.RUN,
        reconcile_table="dwd_production_run_detail",
        reconcile_column="input_artifact_ids",
        multi_valued=True,
        meaning="一次运行消费的输入产物",
        source_note="[a13] 4.1 `MERGE (r)-[:INPUT]->(:Artifact {...})` / 三「湖仓冗余 input·output_artifact_ids」",
    ),
    RelType.PRODUCED: RelSpec(
        rel=RelType.PRODUCED,
        src=NodeLabel.RUN,
        dst=NodeLabel.ARTIFACT,
        level=LineageLevel.RUN,
        reconcile_table="dwd_production_run_detail",
        reconcile_column="output_artifact_ids",
        multi_valued=True,
        meaning="一次运行产出的产物",
        source_note="[a13] 4.1 `MERGE (r)-[:PRODUCED]->(a)`",
    ),
    RelType.REFERENCES: RelSpec(
        rel=RelType.REFERENCES,
        src=NodeLabel.DATASET_VERSION,
        dst=NodeLabel.ARTIFACT,
        level=LineageLevel.ENTITY,
        reconcile_table="dwd_dataset_version_detail",
        reconcile_column="artifact_refs",
        multi_valued=True,
        meaning="数据集版本锁定引用的产物版本列表（保证可复现）",
        source_note="[a13] 4.1 `MERGE (d)-[:REFERENCES]->(a)` / 3.1「冗余 artifact_refs」",
    ),
    RelType.TRACED_TO: RelSpec(
        rel=RelType.TRACED_TO,
        src=NodeLabel.BADCASE,
        dst=NodeLabel.ARTIFACT,
        level=LineageLevel.ENTITY,
        reconcile_table="dwd_badcase_detail",
        reconcile_column="traced_artifact_ids",
        multi_valued=True,
        meaning="Badcase 追溯到的产物（反向追溯入口）",
        source_note=(
            "关系与方向出自 [a13] 5.1 `(b:Badcase)-[:TRACED_TO]->(a:Artifact)`；"
            "⚠️ 对账表名与冗余列名为本项目设计——原文四张事实表里没有 Badcase 表"
        ),
    ),
}
assert len(REL_SPECS) == REL_TYPE_COUNT, "七类关系必须全部登记规格"
assert len({s.reconcile_table for s in REL_SPECS.values()}) == FACT_TABLE_COUNT, (
    "对账源表数应等于原文四张事实表（Badcase 表替代了 clip 表的对账位置，"
    "因为 CONTAINS 的对账列落在产物表上）"
)


# --------------------------------------------------------------------------- 属性白名单

#: 允许常驻图库的遍历键白名单（:attr:`GraphPropertyPolicy.TRAVERSAL_KEYS` 档）。
#:
#: 取舍依据见模块 docstring：只放低基数、标识型、**不落图就没法做谓词下推**的字段。
#:   * Artifact.step / algo_version / status —— [a13] 4.1 `SET a.step=... a.algo_version=... a.status=...`
#:     且 5.1 影响分析 `MATCH (a:Artifact {step:'slam', algo_version:'v3'})` 直接依赖它们。
#:   * Run.status —— [a13] 4.1 `SET r.status='success'`；红线②要判 failed，也需要它。
#:   * DatasetVersion.dataset_id / version / status —— [a13] 5.1 影响分析
#:     `RETURN DISTINCT d.dataset_id, d.version, d.status`。
#:   * Clip / Badcase —— 原文没往上 SET 过任何属性，保持纯 ID 节点。
TRAVERSAL_PROPERTIES: dict[NodeLabel, tuple[str, ...]] = {
    NodeLabel.CLIP: (),
    NodeLabel.ARTIFACT: ("step", "algo_version", "status"),
    NodeLabel.RUN: ("status",),
    NodeLabel.DATASET_VERSION: ("dataset_id", "version", "status"),
    NodeLabel.BADCASE: (),
}

#: 永不落图库的属性黑名单——护栏「属性单一事实源」的硬拦截。
#:
#: 注意 ``params`` 与 ``param_snapshot`` 在这里：[a13] 4.1 的示例把 params 写进了图库，
#: 而 [a13] 六的护栏与本项目铁律禁止这么做，且 5.1 原文自己也承认
#: 「参数快照、质量分这些大属性全部回湖仓按 ID 批量取」。以护栏为准。
FORBIDDEN_GRAPH_PROPERTIES: frozenset[str] = frozenset(
    {
        "params",
        "param_snapshot",
        "content_hash",
        "quality_score",
        "metrics",
        "start_time",
        "end_time",
        "vehicle_code",
        "project_code",
        "artifact_refs",
        "input_artifact_ids",
        "output_artifact_ids",
        "parent_artifact_id",
        "superseded_by",
    }
)

#: 图库上允许的运维字段（不是业务属性，不构成双源冗余）。
#: ⚠️ 原文未明确，本项目设计：对账链路需要知道「这个节点/边上次是被谁、什么时候同步的」，
#: 否则无法区分「实时链路已写入」与「对账补齐」，也无法做同步延迟观测。
OPERATIONAL_PROPERTIES: frozenset[str] = frozenset({"_synced_at", "_sync_channel"})


def sanitize_properties(
    label: NodeLabel,
    props: Mapping[str, object] | None,
    *,
    policy: GraphPropertyPolicy = GraphPropertyPolicy.TRAVERSAL_KEYS,
    strict: bool = True,
) -> dict[str, object]:
    """按护栏裁剪要写进图库的属性。

    :param label: 节点标签
    :param props: 待写入的属性（None 视为空）
    :param policy: ID_ONLY 一律清空；TRAVERSAL_KEYS 只保留该标签的遍历键白名单
    :param strict: True 时，遇到黑名单里的大属性直接抛错（用于写入端自检）；
                   False 时静默丢弃（用于消费历史脏数据的对账链路）
    :returns: 裁剪后的属性字典，值为 None 的键会被剔除（Neo4j 上 null 属性无意义）
    :raises LineageModelError: strict=True 且命中 :data:`FORBIDDEN_GRAPH_PROPERTIES`

    >>> sanitize_properties(NodeLabel.ARTIFACT, {"step": "slam", "algo_version": "v4"})
    {'step': 'slam', 'algo_version': 'v4'}
    >>> sanitize_properties(NodeLabel.ARTIFACT, {"step": "slam"},
    ...                     policy=GraphPropertyPolicy.ID_ONLY)
    {}
    """
    props = dict(props or {})
    if strict:
        offending = sorted(set(props) & FORBIDDEN_GRAPH_PROPERTIES)
        if offending:
            raise LineageModelError(
                f"{label.value} 节点试图把大属性写进图库: {offending}；"
                "护栏「属性单一事实源」要求这些字段一律回湖仓取（[a13] 六）"
            )
    if policy is GraphPropertyPolicy.ID_ONLY:
        return {}
    allowed = set(TRAVERSAL_PROPERTIES.get(label, ())) | OPERATIONAL_PROPERTIES
    return {k: v for k, v in props.items() if k in allowed and v is not None}


# --------------------------------------------------------------------------- 节点 / 边 / 变更集


@dataclass(frozen=True, slots=True)
class NodeRef:
    """节点引用：标签 + ID。图库遍历结果里只允许出现这个，不允许夹带属性。"""

    label: NodeLabel
    node_id: str

    def __post_init__(self) -> None:
        if not self.node_id:
            raise LineageModelError(f"{self.label.value} 节点 ID 不能为空")

    def __str__(self) -> str:
        return f"{self.label.value}({self.node_id})"

    @property
    def source(self) -> LakehouseSource:
        """该节点对应的湖仓来源坐标（表名 + ID），供审计输出。"""
        return lakehouse_source_for(self.label, self.node_id)


@dataclass(frozen=True, slots=True)
class GraphNode:
    """一次节点 UPSERT：MERGE 节点 + SET 白名单属性。"""

    label: NodeLabel
    node_id: str
    properties: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.node_id:
            raise LineageModelError(f"{self.label.value} 节点 ID 不能为空")

    @property
    def ref(self) -> NodeRef:
        return NodeRef(self.label, self.node_id)


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """一次关系 UPSERT：MERGE 边。端点类型在构造时即按 :data:`REL_SPECS` 校验。"""

    rel: RelType
    src_id: str
    dst_id: str

    def __post_init__(self) -> None:
        if not self.src_id or not self.dst_id:
            raise LineageModelError(f"{self.rel.value} 边的两个端点 ID 都不能为空")
        if self.src_id == self.dst_id:
            raise LineageModelError(
                f"{self.rel.value} 边自环: {self.src_id}；产物不可能派生自己，也不可能替代自己"
            )

    @property
    def spec(self) -> RelSpec:
        return REL_SPECS[self.rel]

    @property
    def src(self) -> NodeRef:
        return NodeRef(self.spec.src, self.src_id)

    @property
    def dst(self) -> NodeRef:
        return NodeRef(self.spec.dst, self.dst_id)

    def __str__(self) -> str:
        return f"({self.src_id})-[:{self.rel.value}]->({self.dst_id})"


@dataclass(slots=True)
class GraphMutation:
    """一批幂等的图库变更（MERGE 语义）。

    [a13] 4.1 的标题就是结论：「图库建图：MERGE 幂等是一切的前提」——
    同一批变更重复执行任意次，图的状态必须一致。这是实时链路（可能重复投递）
    与对账链路（按天重跑）能共存的根本原因。
    """

    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    #: 变更来源链路：realtime（链路二）/ reconcile（链路三）/ manual
    channel: str = "realtime"
    #: 这批变更对应的湖仓来源，供审计与失败重投
    sources: list[LakehouseSource] = field(default_factory=list)
    #: 因红线约束被有意跳过的动作（例如 failed run 不建边），供可观测
    skipped: list[str] = field(default_factory=list)

    def add_node(self, node: GraphNode) -> GraphMutation:
        self.nodes.append(node)
        return self

    def add_edge(self, edge: GraphEdge) -> GraphMutation:
        self.edges.append(edge)
        return self

    def extend(self, other: GraphMutation) -> GraphMutation:
        """并入另一批变更（对账链路按天批量合并时用）。"""
        self.nodes.extend(other.nodes)
        self.edges.extend(other.edges)
        self.sources.extend(other.sources)
        self.skipped.extend(other.skipped)
        return self

    def is_empty(self) -> bool:
        return not self.nodes and not self.edges

    def node_refs(self) -> tuple[NodeRef, ...]:
        return tuple(n.ref for n in self.nodes)

    def summary(self) -> str:
        return (
            f"GraphMutation(channel={self.channel}, nodes={len(self.nodes)}, "
            f"edges={len(self.edges)}, skipped={len(self.skipped)})"
        )


# --------------------------------------------------------------------------- 红线与护栏（文本常量）

#: 三条一致性红线，逐字抄自 [a13] 4.2「💡 一致性红线」。
CONSISTENCY_REDLINES: tuple[str, ...] = (
    "① 实时链路失败不阻塞湖仓写入——湖仓永远是事实源",
    "② failed 的 run 不产生 Artifact 节点，其输入输出不建边",
    "③ 数据集绑定产物版本后，即使产物被替代，该数据集版本依然可复现",
)

#: 四条工程护栏，逐字抄自 [a13] 六「工程边界：四条护栏防止血缘系统失控」。
GUARDRAILS: tuple[tuple[str, str], ...] = (
    (
        "属性单一事实源",
        "图库只存节点与关系（ID + 关系类型），属性、参数快照与指标一律取自湖仓，避免双源冗余不一致",
    ),
    (
        "双链路可靠性",
        "湖仓冗余血缘字段为对账源，图库实时同步失败时由定期对账（T+1）UPSERT 补齐",
    ),
    (
        "遍历边界",
        "多跳遍历限定深度 3–5 跳防止扇出爆炸；大批量下游影响分析改走湖仓离线统计审计链路",
    ),
    (
        "结果可审计",
        "查询结果同时返回血缘路径与湖仓数据来源（表名 + ID），可复现、可审计",
    ),
)

#: 模块出处，便于 `python -c "from adas_lakehouse.lineage import model; print(model.SOURCES)"`
SOURCES: tuple[str, str] = (SOURCE_A13, SOURCE_A11)


def validate_edges(edges: Iterable[GraphEdge]) -> list[str]:
    """批量校验边集合，返回问题描述列表（空列表表示全部合法）。

    校验项：
      1. 关系类型已登记（:data:`REL_SPECS`）；
      2. 无自环（GraphEdge 构造时已拦，此处兜底重复投递的手工构造场景）；
      3. SUPERSEDED_BY 的新旧产物必须属于同一个 clip 且同一个 step
         ——版本分支的定义是「同一环节不同算法版本」（[a13] 三·版本血缘）。
    """
    from ..ids import parse_artifact_id

    problems: list[str] = []
    for e in edges:
        if e.rel not in REL_SPECS:
            problems.append(f"未登记的关系类型: {e.rel!r}")
            continue
        if e.rel is RelType.SUPERSEDED_BY:
            try:
                old = parse_artifact_id(e.src_id)
                new = parse_artifact_id(e.dst_id)
            except ValueError as exc:
                problems.append(f"SUPERSEDED_BY 端点不是合法 artifact_id: {exc}")
                continue
            if old.data_id != new.data_id:
                problems.append(
                    f"SUPERSEDED_BY 跨 clip: {old.data_id} → {new.data_id}；"
                    "版本分支只在同一 clip 内成立（data_id 重刷不变，[a11] 三·规则 3）"
                )
            if old.stage != new.stage:
                problems.append(
                    f"SUPERSEDED_BY 跨环节: {old.stage} → {new.stage}；"
                    "版本血缘的定义是「同一环节不同算法版本」（[a13] 三）"
                )
            if old.algo_version == new.algo_version and old.content_hash == new.content_hash:
                problems.append(f"SUPERSEDED_BY 两端完全同版本同内容: {e.src_id}")
        if e.rel is RelType.DERIVED_FROM:
            try:
                child = parse_artifact_id(e.src_id)
                parent = parse_artifact_id(e.dst_id)
            except ValueError as exc:
                problems.append(f"DERIVED_FROM 端点不是合法 artifact_id: {exc}")
                continue
            if child.data_id != parent.data_id:
                problems.append(
                    f"DERIVED_FROM 跨 clip: {child.data_id} ← {parent.data_id}；"
                    "artifact_id 内嵌 data_id，产物永远可回溯到同一个采集单元（[a11] 二）"
                )
    return problems
