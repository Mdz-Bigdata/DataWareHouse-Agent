"""链路一的产物：湖仓四张事实表的行 → 图库变更集。

[a13] 四把写入拆成三条链路，本模块负责的是「链路一 → 链路二/三 的翻译层」：

===================== ================================================ ==============
链路                   机制                                              定位
===================== ================================================ ==============
链路一（事实源）        run / 产物 / 数据集版本写入湖仓四张表，关系字段冗余落表   一切血缘的出发点
链路二（实时）          监听湖仓 binlog / 变更消息，MERGE 图库节点与关系        低延迟；失败不阻塞湖仓写入
链路三（对账）          T+1 / 定时扫描湖仓增量（_ingest_time），按关系字段 UPSERT 补齐  兜底；幂等可重复执行
===================== ================================================ ==============

链路二与链路三拿到的都是「湖仓的一行」，只是来源不同（一个来自 changelog，一个来自
增量扫描）。所以两条链路共用同一套 Fact → GraphMutation 的转换——这正是幂等的来源：
同一行数据无论从哪条链路进来，产出的 MERGE 语句完全一致。

关系冗余字段的多值编码
    ``input_artifact_ids`` / ``output_artifact_ids`` / ``artifact_refs`` /
    ``traced_artifact_ids`` 在湖仓里都是 STRING 列。
    ⚠️ 原文未明确编码方式（[a13] 3.1 只写了「冗余 input·output_artifact_ids」），
    本项目设计：优先按 JSON 数组解析，失败则回退到逗号分隔——两种写法在实践中都常见，
    解析层做兼容比逼着上游统一更现实。见 :func:`parse_id_list`。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..ids import ArtifactStatus, parse_artifact_id, parse_data_id, parse_run_id
from .model import (
    CONSISTENCY_REDLINES,
    DatasetVersionStatus,
    GraphEdge,
    GraphMutation,
    GraphNode,
    GraphPropertyPolicy,
    LakehouseSource,
    LineageModelError,
    NodeLabel,
    RelType,
    RunStatus,
    lakehouse_source_for,
    sanitize_properties,
)

__all__ = [
    "LineageFact",
    "ClipFact",
    "ArtifactFact",
    "RunFact",
    "DatasetVersionFact",
    "BadcaseFact",
    "parse_id_list",
    "fact_from_row",
    "FACT_BY_TABLE",
    "mutation_from_rows",
]


def parse_id_list(raw: object) -> tuple[str, ...]:
    """把湖仓 STRING 列里的 ID 列表解析成元组。

    兼容三种形态（⚠️ 原文未明确编码，本项目设计的兼容策略）：
      * 已经是 list/tuple（Flink Row 直接给了 ARRAY 类型时）
      * JSON 数组字符串 ``["a","b"]``
      * 逗号分隔 ``a,b``（空白自动 strip，空串自动丢弃）

    None / 空串返回空元组——「没有输入产物」是合法状态（源头 clip 的第一个环节）。

    >>> parse_id_list('["a", "b"]')
    ('a', 'b')
    >>> parse_id_list(" a , b ,")
    ('a', 'b')
    >>> parse_id_list(None)
    ()
    """
    if raw is None:
        return ()
    if isinstance(raw, (list, tuple)):
        return tuple(str(x).strip() for x in raw if str(x).strip())
    text = str(raw).strip()
    if not text:
        return ()
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(parsed, list):
                return tuple(str(x).strip() for x in parsed if str(x).strip())
    return tuple(part.strip() for part in text.split(",") if part.strip())


def _opt(raw: object) -> str | None:
    """把湖仓的空串 / None 统一成 None——图库上 null 属性与空串同义但更省事。"""
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


# --------------------------------------------------------------------------- 基类


@dataclass(frozen=True, slots=True, kw_only=True)
class LineageFact:
    """湖仓事实行的基类。子类实现 :meth:`to_mutation`。

    子类必须是 frozen 的：一条事实行从湖仓读出来之后不该再被改写，
    要改就回湖仓改（湖仓是唯一事实源）。
    """

    #: 入湖时间，对账链路（链路三）按这个字段切增量窗口（[a13] 四·链路三）
    ingest_time: datetime | None = None

    @property
    def node_label(self) -> NodeLabel:  # pragma: no cover - 抽象
        raise NotImplementedError

    @property
    def node_id(self) -> str:  # pragma: no cover - 抽象
        raise NotImplementedError

    @property
    def source(self) -> LakehouseSource:
        """这条事实行在湖仓的来源坐标（表名 + 主键列 + 值）。"""
        return lakehouse_source_for(self.node_label, self.node_id)

    def to_mutation(
        self,
        *,
        policy: GraphPropertyPolicy = GraphPropertyPolicy.TRAVERSAL_KEYS,
        channel: str = "realtime",
    ) -> GraphMutation:  # pragma: no cover - 抽象
        raise NotImplementedError


# --------------------------------------------------------------------------- Clip


@dataclass(frozen=True, slots=True, kw_only=True)
class ClipFact(LineageFact):
    """dwd_collect_clip_detail 的一行——血缘追溯的起点，图库 Clip 节点。

    出处 [a13] 3.1 表格第一行：「dwd_collect_clip_detail | 采集数据单元（clip 级）|
    追溯起点，图库 Clip 节点」。

    一个 clip ≈ 1 分钟连续采集片段（[a11] 一），对应一个 data_id，终身不变。
    """

    data_id: str = ""

    def __post_init__(self) -> None:
        parse_data_id(self.data_id)  # 早失败：非法锚点不许进图

    @property
    def node_label(self) -> NodeLabel:
        return NodeLabel.CLIP

    @property
    def node_id(self) -> str:
        return self.data_id

    def to_mutation(
        self,
        *,
        policy: GraphPropertyPolicy = GraphPropertyPolicy.TRAVERSAL_KEYS,
        channel: str = "realtime",
    ) -> GraphMutation:
        """Clip 节点是纯 ID 节点——采集元信息（车辆、时间、天气、GPS）全在湖仓。"""
        node = GraphNode(
            NodeLabel.CLIP,
            self.data_id,
            sanitize_properties(NodeLabel.CLIP, {}, policy=policy),
        )
        return GraphMutation(nodes=[node], channel=channel, sources=[self.source])


# --------------------------------------------------------------------------- Artifact


@dataclass(frozen=True, slots=True, kw_only=True)
class ArtifactFact(LineageFact):
    """dwd_production_artifact_detail 的一行——图库 Artifact 节点。

    字段对齐 [a13] 3.1 的建表节选（原文 DDL 逐字）::

        artifact_id STRING   -- {data_id}_{step}_{algo_version}_{content_hash}，兼作图库节点 ID
        data_id     STRING   -- 所属 clip
        step        STRING   -- align / slam / ann / qc / post
        algo_version STRING, content_hash STRING,
        param_snapshot STRING -- 参数快照（JSON），可重放依据
        parent_artifact_id STRING -- 实体血缘冗余字段（DERIVED_FROM 对账源）
        superseded_by STRING      -- 版本血缘冗余字段（SUPERSEDED_BY 对账源）
        status STRING             -- active / superseded / invalid

    注意 ``param_snapshot`` / ``content_hash`` **不会**进图库——它们是护栏黑名单里的
    大属性，查询时按 artifact_id 回湖仓取（[a13] 5.1 结尾原话：「参数快照、质量分
    这些大属性全部回湖仓按 ID 批量取」）。

    ⚠️ 上面是**原文 DDL 的列名**，本项目 catalog 登记的列名有四处不同：
    ``step→stage`` / ``param_snapshot→param_snapshot_json`` /
    ``superseded_by→superseded_by_artifact_id`` / ``status→artifact_status``。
    本类字段沿用原文/图库口径，读湖仓行时由 :data:`_FACT_COLUMNS` 负责换名。
    """

    artifact_id: str = ""
    data_id: str = ""
    step: str = ""
    algo_version: str = ""
    content_hash: str = ""
    param_snapshot: str | None = None
    parent_artifact_id: str | None = None
    superseded_by: str | None = None
    status: ArtifactStatus = ArtifactStatus.ACTIVE

    def __post_init__(self) -> None:
        parsed = parse_artifact_id(self.artifact_id)
        # data_id / step / algo_version 三列与 artifact_id 内嵌信息必须一致，
        # 否则「ID 本身即信息」就破了（[a11] 三）。
        object.__setattr__(self, "data_id", self.data_id or parsed.data_id)
        object.__setattr__(self, "step", self.step or parsed.stage)
        object.__setattr__(self, "algo_version", self.algo_version or parsed.algo_version)
        object.__setattr__(self, "content_hash", self.content_hash or parsed.content_hash)
        mismatches = [
            f"data_id({self.data_id} != {parsed.data_id})"
            if self.data_id != parsed.data_id
            else "",
            f"step({self.step} != {parsed.stage})" if self.step != parsed.stage else "",
            (
                f"algo_version({self.algo_version} != {parsed.algo_version})"
                if self.algo_version != parsed.algo_version
                else ""
            ),
        ]
        bad = [m for m in mismatches if m]
        if bad:
            raise LineageModelError(
                f"artifact_id {self.artifact_id!r} 与列值不一致: {', '.join(bad)}"
            )
        if isinstance(self.status, str) and not isinstance(self.status, ArtifactStatus):
            object.__setattr__(self, "status", ArtifactStatus(self.status))

    @property
    def node_label(self) -> NodeLabel:
        return NodeLabel.ARTIFACT

    @property
    def node_id(self) -> str:
        return self.artifact_id

    def to_mutation(
        self,
        *,
        policy: GraphPropertyPolicy = GraphPropertyPolicy.TRAVERSAL_KEYS,
        channel: str = "realtime",
    ) -> GraphMutation:
        """产物节点 + CONTAINS（所属 clip）+ DERIVED_FROM（父产物）+ SUPERSEDED_BY（新版本）。

        对应 [a13] 4.1 的前两条语句::

            MERGE (a:Artifact {id:'..._slam_v4_b7e8f9a0'})
            SET a.step='slam', a.algo_version='v4', a.status='active';
            MATCH (old:Artifact {id:'..._slam_v3_c4d5e6f7'})
            MERGE (old)-[:SUPERSEDED_BY]->(a);
        """
        node = GraphNode(
            NodeLabel.ARTIFACT,
            self.artifact_id,
            sanitize_properties(
                NodeLabel.ARTIFACT,
                {
                    "step": self.step,
                    "algo_version": self.algo_version,
                    "status": self.status.value,
                },
                policy=policy,
            ),
        )
        mutation = GraphMutation(nodes=[node], channel=channel, sources=[self.source])
        # 实体血缘①：clip 包含产物（CONTAINS 的对账列就是产物表上的 data_id）
        mutation.add_node(GraphNode(NodeLabel.CLIP, self.data_id, {}))
        mutation.add_edge(GraphEdge(RelType.CONTAINS, self.data_id, self.artifact_id))
        # 实体血缘②：派生自父产物（[a11] 三·规则 4）
        parent = _opt(self.parent_artifact_id)
        if parent:
            mutation.add_node(GraphNode(NodeLabel.ARTIFACT, parent, {}))
            mutation.add_edge(GraphEdge(RelType.DERIVED_FROM, self.artifact_id, parent))
        # 版本血缘：旧 → 新（本行是旧产物时，superseded_by 指向新产物）
        newer = _opt(self.superseded_by)
        if newer:
            mutation.add_node(GraphNode(NodeLabel.ARTIFACT, newer, {}))
            mutation.add_edge(GraphEdge(RelType.SUPERSEDED_BY, self.artifact_id, newer))
        return mutation


# --------------------------------------------------------------------------- Run


@dataclass(frozen=True, slots=True, kw_only=True)
class RunFact(LineageFact):
    """dwd_production_run_detail 的一行——图库 Run 节点，冗余 input·output_artifact_ids。

    出处 [a13] 3.1 表格第三行 + 三·运行血缘。

    ⚠️ 红线②在这里落地：「failed 的 run 不产生 Artifact 节点，其输入输出不建边」
    （[a13] 4.2）。本项目的解读与补充：
      * Run 节点本身**保留**——运行记录（含失败）是审计对象，图上要能查到它失败过；
        ⚠️ 原文只说「不产生 Artifact 节点、不建边」，没说 Run 节点删不删，此为本项目设计。
      * cancelled 与 running 同样不建边——前者没产出，后者还没产出；
        ⚠️ 原文红线只点名 failed，此处是本项目按同一逻辑的推广，已在 skipped 里记名。
    """

    run_id: str = ""
    stage: str = ""
    status: RunStatus = RunStatus.RUNNING
    algo_version: str | None = None
    params: str | None = None
    input_artifact_ids: tuple[str, ...] = ()
    output_artifact_ids: tuple[str, ...] = ()
    start_time: datetime | None = None
    end_time: datetime | None = None

    def __post_init__(self) -> None:
        parsed = parse_run_id(self.run_id)
        object.__setattr__(self, "stage", self.stage or parsed.stage)
        if self.stage != parsed.stage:
            raise LineageModelError(
                f"run_id {self.run_id!r} 的环节段 {parsed.stage!r} 与列值 {self.stage!r} 不一致"
            )
        if isinstance(self.status, str) and not isinstance(self.status, RunStatus):
            object.__setattr__(self, "status", RunStatus(self.status))
        object.__setattr__(self, "input_artifact_ids", parse_id_list(self.input_artifact_ids))
        object.__setattr__(self, "output_artifact_ids", parse_id_list(self.output_artifact_ids))

    @property
    def node_label(self) -> NodeLabel:
        return NodeLabel.RUN

    @property
    def node_id(self) -> str:
        return self.run_id

    @property
    def produces_edges(self) -> bool:
        """是否允许建 INPUT / PRODUCED 边。只有 success 的 run 才建（红线②）。"""
        return self.status is RunStatus.SUCCESS

    def to_mutation(
        self,
        *,
        policy: GraphPropertyPolicy = GraphPropertyPolicy.TRAVERSAL_KEYS,
        channel: str = "realtime",
    ) -> GraphMutation:
        """运行节点 + INPUT / PRODUCED 边。

        对应 [a13] 4.1 的中间三条语句::

            MERGE (r:Run {id:'run_slam_20240116103000_9f3a21c7'})
            SET r.status='success', r.params='{"max_iter":200}';
            MERGE (r)-[:INPUT]->(:Artifact {id:'..._align_v1_9f3a21c7'});
            MERGE (r)-[:PRODUCED]->(a);

        与原文的唯一差异：``r.params`` 不落图库（护栏「属性单一事实源」），
        查询时按 run_id 回 dwd_production_run_detail 取。
        """
        node = GraphNode(
            NodeLabel.RUN,
            self.run_id,
            sanitize_properties(NodeLabel.RUN, {"status": self.status.value}, policy=policy),
        )
        mutation = GraphMutation(nodes=[node], channel=channel, sources=[self.source])
        if not self.produces_edges:
            mutation.skipped.append(
                f"run {self.run_id} status={self.status.value}：不建 INPUT/PRODUCED 边、"
                f"不产生 Artifact 节点（红线② {CONSISTENCY_REDLINES[1]}）"
            )
            return mutation
        for aid in self.input_artifact_ids:
            mutation.add_node(GraphNode(NodeLabel.ARTIFACT, aid, {}))
            mutation.add_edge(GraphEdge(RelType.INPUT, self.run_id, aid))
        for aid in self.output_artifact_ids:
            mutation.add_node(GraphNode(NodeLabel.ARTIFACT, aid, {}))
            mutation.add_edge(GraphEdge(RelType.PRODUCED, self.run_id, aid))
        return mutation


# --------------------------------------------------------------------------- DatasetVersion


@dataclass(frozen=True, slots=True, kw_only=True)
class DatasetVersionFact(LineageFact):
    """dwd_dataset_version_detail 的一行——图库 DatasetVersion 节点，冗余 artifact_refs。

    出处 [a13] 3.1 表格第四行 与 3.2 第三步：「数据集版本经 artifact_refs 字段 +
    REFERENCES 边锁定引用的产物版本列表」。

    红线③在这里落地：「数据集绑定产物版本后，即使产物被替代，该数据集版本依然可复现」
    ——所以 REFERENCES 边指向的是**具体 artifact_id**（含 content_hash），
    而不是「某 clip 某环节的最新版」。绑定之后产物即使 status=superseded，边也不动。
    """

    dataset_version_id: str = ""
    dataset_id: str = ""
    version: str = ""
    status: DatasetVersionStatus = DatasetVersionStatus.DRAFT
    artifact_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.dataset_version_id:
            raise LineageModelError("dataset_version_id 不能为空（示例形如 DS_0001_V2）")
        if isinstance(self.status, str) and not isinstance(self.status, DatasetVersionStatus):
            object.__setattr__(self, "status", DatasetVersionStatus(self.status))
        object.__setattr__(self, "artifact_refs", parse_id_list(self.artifact_refs))

    @property
    def node_label(self) -> NodeLabel:
        return NodeLabel.DATASET_VERSION

    @property
    def node_id(self) -> str:
        return self.dataset_version_id

    def to_mutation(
        self,
        *,
        policy: GraphPropertyPolicy = GraphPropertyPolicy.TRAVERSAL_KEYS,
        channel: str = "realtime",
    ) -> GraphMutation:
        """数据集版本节点 + REFERENCES 边。

        对应 [a13] 4.1 的最后两条语句::

            MERGE (d:DatasetVersion {id:'DS_0001_V2'})
            MERGE (d)-[:REFERENCES]->(a);
        """
        node = GraphNode(
            NodeLabel.DATASET_VERSION,
            self.dataset_version_id,
            sanitize_properties(
                NodeLabel.DATASET_VERSION,
                {
                    "dataset_id": self.dataset_id or None,
                    "version": self.version or None,
                    "status": self.status.value,
                },
                policy=policy,
            ),
        )
        mutation = GraphMutation(nodes=[node], channel=channel, sources=[self.source])
        for aid in self.artifact_refs:
            mutation.add_node(GraphNode(NodeLabel.ARTIFACT, aid, {}))
            mutation.add_edge(GraphEdge(RelType.REFERENCES, self.dataset_version_id, aid))
        return mutation


# --------------------------------------------------------------------------- Badcase


@dataclass(frozen=True, slots=True, kw_only=True)
class BadcaseFact(LineageFact):
    """Badcase 事实行——图库 Badcase 节点，反向追溯的入口。

    出处：Badcase 是 [a13] 二列出的五类节点之一，TRACED_TO 是七类关系之一，
    5.1 的反向追溯 Cypher 以 ``(b:Badcase {id:'BC_20240120_001'})`` 起手。

    ⚠️ 原文未明确，本项目设计：3.1 的四张事实表里没有 Badcase 表，故其湖仓事实源与
    冗余列名（traced_artifact_ids）为本项目设计。事实源即评测域已登记的
    ``dwd_badcase_detail``，见 :data:`model.NODE_SOURCE_TABLES` 的注释。
    """

    badcase_id: str = ""
    data_id: str | None = None
    evaluation_type: str | None = None
    traced_artifact_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.badcase_id:
            raise LineageModelError("badcase_id 不能为空（示例形如 BC_20240120_001）")
        object.__setattr__(self, "traced_artifact_ids", parse_id_list(self.traced_artifact_ids))
        if self.data_id:
            parse_data_id(self.data_id)

    @property
    def node_label(self) -> NodeLabel:
        return NodeLabel.BADCASE

    @property
    def node_id(self) -> str:
        return self.badcase_id

    def to_mutation(
        self,
        *,
        policy: GraphPropertyPolicy = GraphPropertyPolicy.TRAVERSAL_KEYS,
        channel: str = "realtime",
    ) -> GraphMutation:
        """Badcase 节点 + TRACED_TO 边（指向被追溯的产物）。

        ``evaluation_type`` 不落图库——它是 dwd_evaluation_result_detail 的分区字段，
        属于湖仓侧的过滤维度，图库不需要它做路径剪枝。
        """
        node = GraphNode(
            NodeLabel.BADCASE,
            self.badcase_id,
            sanitize_properties(NodeLabel.BADCASE, {}, policy=policy),
        )
        mutation = GraphMutation(nodes=[node], channel=channel, sources=[self.source])
        for aid in self.traced_artifact_ids:
            mutation.add_node(GraphNode(NodeLabel.ARTIFACT, aid, {}))
            mutation.add_edge(GraphEdge(RelType.TRACED_TO, self.badcase_id, aid))
        return mutation


# --------------------------------------------------------------------------- 行 → 事实


#: 湖仓表名 → 事实类型。链路二/三拿到一行时按表名分派。
#: 表名一律取 :mod:`adas_lakehouse.catalog.registry` 登记的名字——本模块只引用不定义。
FACT_BY_TABLE: dict[str, type[LineageFact]] = {
    "dwd_collect_clip_detail": ClipFact,
    "dwd_production_artifact_detail": ArtifactFact,
    "dwd_production_run_detail": RunFact,
    "dwd_dataset_version_detail": DatasetVersionFact,
    "dwd_badcase_detail": BadcaseFact,
}

#: 各事实类型接受的**湖仓列名** → 事实字段名。
#:
#: 两边不同名的地方全在这张表里，不要在别处再写第二份映射：
#: 事实字段沿用 [a13] 4.1 Cypher 的图库属性口径（``a.step`` / ``a.status`` /
#: ``r.params``），而湖仓列名以 catalog.registry 为准（``stage`` /
#: ``artifact_status`` / ``param_snapshot_json``）。两套名字各有出处，
#: 硬统一成一套会有一边失真——所以显式映射，而不是让谁迁就谁。
_FACT_COLUMNS: dict[type[LineageFact], dict[str, str]] = {
    ClipFact: {"data_id": "data_id"},
    ArtifactFact: {
        "artifact_id": "artifact_id",
        "data_id": "data_id",
        "stage": "step",
        "algo_version": "algo_version",
        "content_hash": "content_hash",
        "param_snapshot_json": "param_snapshot",
        "parent_artifact_id": "parent_artifact_id",
        "superseded_by_artifact_id": "superseded_by",
        "artifact_status": "status",
    },
    RunFact: {
        "run_id": "run_id",
        "stage": "stage",
        "run_status": "status",
        "algo_version": "algo_version",
        "param_snapshot_json": "params",
        "input_artifact_ids": "input_artifact_ids",
        "output_artifact_ids": "output_artifact_ids",
        "start_time": "start_time",
        "end_time": "end_time",
    },
    DatasetVersionFact: {
        "dataset_version_id": "dataset_version_id",
        "dataset_id": "dataset_id",
        "version": "version",
        "version_status": "status",
        "artifact_refs": "artifact_refs",
    },
    BadcaseFact: {
        "badcase_id": "badcase_id",
        "data_id": "data_id",
        "evaluation_type": "evaluation_type",
        "traced_artifact_ids": "traced_artifact_ids",
    },
}


def fact_from_row(table: str, row: Mapping[str, Any]) -> LineageFact:
    """把湖仓一行（dict）转成对应的事实对象。

    这是链路二（changelog 消息）与链路三（增量扫描结果）的公共入口——
    两条链路走同一个转换，所以同一行数据无论从哪来，产出的 MERGE 完全一致（幂等）。

    :param table: 湖仓表名，必须在 :data:`FACT_BY_TABLE` 里
    :param row: 该表的一行。键既可以是湖仓列名（``artifact_status`` / ``stage`` …），
        也可以是事实字段名（``status`` / ``step`` …）——两种写法都认，
        对照表见 :data:`_FACT_COLUMNS`；不认识的列会被忽略
    :raises LineageModelError: 表名未登记，或行内容不合法（ID 非法、状态越界等）

    >>> f = fact_from_row("dwd_collect_clip_detail",
    ...                   {"data_id": "COLLECT_BP_20240115143022_a3f8", "weather": "rain"})
    >>> f.node_id
    'COLLECT_BP_20240115143022_a3f8'
    """
    try:
        cls = FACT_BY_TABLE[table]
    except KeyError as exc:
        raise LineageModelError(
            f"表 {table!r} 不是血缘事实表；可选: {sorted(FACT_BY_TABLE)}"
        ) from exc
    kwargs: dict[str, Any] = {}
    for column, field_name in _FACT_COLUMNS[cls].items():
        # 湖仓列名优先；两边同名时这两条分支等价
        if column in row:
            kwargs[field_name] = row[column]
        elif field_name in row:
            kwargs[field_name] = row[field_name]
    # status 列在湖仓是 STRING，交给各 Fact 的 __post_init__ 转枚举
    if "_ingest_time" in row:
        kwargs["ingest_time"] = row["_ingest_time"]
    elif "ingest_time" in row:
        kwargs["ingest_time"] = row["ingest_time"]
    try:
        return cls(**kwargs)  # type: ignore[arg-type]
    except (ValueError, TypeError) as exc:
        raise LineageModelError(f"{table} 行转事实失败: {exc}；row_keys={sorted(row)}") from exc


def mutation_from_rows(
    rows: Sequence[tuple[str, Mapping[str, Any]]],
    *,
    policy: GraphPropertyPolicy = GraphPropertyPolicy.TRAVERSAL_KEYS,
    channel: str = "reconcile",
) -> GraphMutation:
    """批量把 ``(表名, 行)`` 转成一个合并后的图变更集。对账链路按天批处理时用。

    单行失败不会中断整批——失败原因记进 ``skipped``，其余照常补齐。
    这符合护栏「双链路可靠性」：对账是兜底，兜底自己不能被一条脏数据噎死。
    """
    merged = GraphMutation(channel=channel)
    for table, row in rows:
        try:
            fact = fact_from_row(table, row)
        except LineageModelError as exc:
            merged.skipped.append(f"{table}: {exc}")
            continue
        merged.extend(fact.to_mutation(policy=policy, channel=channel))
    return merged
