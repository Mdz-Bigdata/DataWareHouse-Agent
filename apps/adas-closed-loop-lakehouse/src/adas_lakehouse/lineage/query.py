"""四大查询方向：图库找关系、湖仓取明细、结果带来源。

三步走（[a13] 五，逐字）::

    ① Neo4j 多跳遍历找路径、定范围（只返回节点 ID 与关系类型）
    → ② 按节点 ID 回 Paimon 补齐属性、参数快照与质量指标
    → ③ 组装输出带数据来源的血缘结果

四个方向（[a13] 五 / [a11] 六）：

======== ==================================================== ==========================
方向      回答的典型问题                                          遍历路径
======== ==================================================== ==========================
正向追踪   这批采集数据最终用在了哪些数据集与模型上？                 clip → 产物 → 数据集 → 训练 → 评测
反向追溯   这个 Badcase 是哪个版本模型、什么参数跑出来的？            Badcase → 评测 → 数据集版本 → 产物算法版本与参数
版本对比   同一批数据，SLAM v3 与 v4 哪个质量更好？                 同一 clip 不同 algo_version 产物并列
影响分析   算法升级后，哪些数据集与训练任务需要重刷？                 SUPERSEDED_BY + 下游 REFERENCES 统计
======== ==================================================== ==========================

两条硬约束贯穿本模块：
  * **深度 3-5 跳**（护栏三）——所有变长遍历都过 :func:`graph.resolve_depth`；
  * **结果可审计**（护栏四）——每个 :class:`LineageResult` 都带 ``sources``
    （表名 + ID）与 ``paths``（血缘路径），两者齐备才算「可复现、可审计」。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..ids import parse_artifact_id, parse_data_id
from .constants import (
    MAX_PATHS_PER_QUERY,
    OFFLINE_IMPACT_FANOUT_THRESHOLD,
    SLAM_NEW_VERSION,
    SLAM_OLD_VERSION,
    TRAVERSAL_DEPTH_RANGE_TEXT,
)
from .graph import Neo4jGraphStore, rel_pattern, resolve_depth
from .model import (
    GUARDRAILS,
    GraphPropertyPolicy,
    LakehouseSource,
    NodeLabel,
    NodeRef,
    RelType,
)
from .resolver import LakehouseResolver, LakehouseUnavailable

__all__ = [
    "QueryDirection",
    "PathHop",
    "LineagePath",
    "LineageResult",
    "LineageQueryService",
    "SOURCE_CYPHER_BACKWARD_TRACE",
    "SOURCE_CYPHER_IMPACT_ANALYSIS",
]

_log = logging.getLogger(__name__)


class QueryDirection(str, Enum):
    """四个查询方向。出处 [a13] 五 的表格 与 [a11] 六 的表格。"""

    FORWARD_TRACE = "forward_trace"  # 正向追踪：数据生产与资产管理
    BACKWARD_TRACE = "backward_trace"  # 反向追溯：问题分析与根因定位
    VERSION_COMPARE = "version_compare"  # 版本分支对比：算法迭代决策
    IMPACT_ANALYSIS = "impact_analysis"  # 影响分析：重刷排期与成本评估


#: [a13] 5.1 的两个代表性 Cypher，逐字保留用于对照。
#:
#: ⚠️ 注意：这两条原文语句直接从图库 RETURN 了 ``r.params`` / ``a.param_snapshot`` /
#: ``a.content_hash``，与 [a13] 六护栏「属性一律取自湖仓」冲突（详见 model 模块
#: docstring 的矛盾说明）。本模块执行的是等价的「只取 ID、属性回湖仓」版本。
SOURCE_CYPHER_BACKWARD_TRACE: str = """\
-- 反向追溯：Badcase → 运行记录 → 产物算法版本与参数快照        —— [a13] 5.1 原文
MATCH (b:Badcase {id:'BC_20240120_001'})-[:TRACED_TO]->(a:Artifact)
      <-[:PRODUCED]-(r:Run)
RETURN r.id, r.algo_version, r.params, a.param_snapshot, a.content_hash
ORDER BY r.start_time DESC;"""

SOURCE_CYPHER_IMPACT_ANALYSIS: str = """\
-- 影响分析：算法升级后，仍引用旧版本产物的数据集（重刷排期依据）  —— [a13] 5.1 原文
MATCH (a:Artifact {step:'slam', algo_version:'v3'})<-[:REFERENCES]-(d:DatasetVersion)
RETURN DISTINCT d.dataset_id, d.version, d.status;"""


# --------------------------------------------------------------------------- 结果模型


@dataclass(frozen=True, slots=True)
class PathHop:
    """血缘路径上的一跳：源节点 -[关系]-> 目标节点。"""

    rel: RelType
    src: NodeRef
    dst: NodeRef

    def __str__(self) -> str:
        return f"{self.src} -[:{self.rel.value}]-> {self.dst}"


@dataclass(frozen=True, slots=True)
class LineagePath:
    """一条血缘路径：节点序列 + 跳序列。只含 ID 与关系类型，不含任何业务属性。"""

    nodes: tuple[NodeRef, ...]
    hops: tuple[PathHop, ...]

    @property
    def depth(self) -> int:
        """跳数。"""
        return len(self.hops)

    def as_text(self) -> str:
        """人可读路径串，形如 ``Clip(COLLECT_..) -[:CONTAINS]-> Artifact(..)``。"""
        if not self.hops:
            return str(self.nodes[0]) if self.nodes else ""
        parts = [str(self.hops[0].src)]
        for hop in self.hops:
            parts.append(f"-[:{hop.rel.value}]->")
            parts.append(str(hop.dst))
        return " ".join(parts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "depth": self.depth,
            "nodes": [{"label": n.label.value, "id": n.node_id} for n in self.nodes],
            "hops": [
                {"rel": h.rel.value, "src": h.src.node_id, "dst": h.dst.node_id} for h in self.hops
            ],
        }


@dataclass(slots=True)
class LineageResult:
    """一次血缘查询的完整结果——路径 + 属性 + 来源，三样齐备才叫可审计。

    护栏四（[a13] 六）原文：「查询结果同时返回血缘路径与湖仓数据来源（表名 + ID），
    可复现、可审计」。:attr:`paths` 是前者，:attr:`sources` 是后者，
    :attr:`attributes` 是按来源坐标取回来的明细。
    """

    direction: QueryDirection
    anchor: NodeRef
    depth: int
    paths: list[LineagePath] = field(default_factory=list)
    #: {node_id: 湖仓属性字典}
    attributes: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: 湖仓数据来源坐标（表名 + 主键列 + 值），去重后按出现顺序
    sources: list[LakehouseSource] = field(default_factory=list)
    #: 命中扇出闸门被截断
    truncated: bool = False
    #: 建议改走的湖仓离线统计 SQL（影响分析超过阈值时给出）
    offline_sql: str | None = None
    notes: list[str] = field(default_factory=list)

    def node_refs(self) -> tuple[NodeRef, ...]:
        """结果里出现过的全部节点，去重后按首次出现顺序。"""
        seen: dict[tuple[str, str], NodeRef] = {}
        for path in self.paths:
            for n in path.nodes:
                seen.setdefault((n.label.value, n.node_id), n)
        return tuple(seen.values())

    def nodes_of(self, label: NodeLabel) -> tuple[NodeRef, ...]:
        """结果里某一类节点。"""
        return tuple(n for n in self.node_refs() if n.label is label)

    def dedup_sources(self) -> list[LakehouseSource]:
        seen: dict[str, LakehouseSource] = {}
        for s in self.sources:
            seen.setdefault(s.locator, s)
        return list(seen.values())

    def as_dict(self) -> dict[str, Any]:
        """可直接 JSON 序列化的审计输出。"""
        return {
            "direction": self.direction.value,
            "anchor": {"label": self.anchor.label.value, "id": self.anchor.node_id},
            "depth": self.depth,
            "guardrail": GUARDRAILS[3][1],  # 结果可审计
            "paths": [p.as_dict() for p in self.paths],
            "attributes": self.attributes,
            "lakehouse_sources": [s.as_dict() for s in self.dedup_sources()],
            "truncated": self.truncated,
            "offline_sql": self.offline_sql,
            "notes": list(self.notes),
        }


# --------------------------------------------------------------------------- 查询服务


#: 路径返回片段：只取节点 ID / 标签与关系类型 + 两端 ID，绝不 RETURN 业务属性。
_RETURN_PATH = (
    "RETURN [n IN nodes(path) | {id: n.`id`, label: head(labels(n))}] AS nodes,\n"
    "       [r IN relationships(path) | "
    "{type: type(r), start: startNode(r).`id`, end: endNode(r).`id`}] AS rels\n"
    "LIMIT $limit"
)


class LineageQueryService:
    """四方向血缘查询的门面。

    构造时可以只给图库（属性回取降级为「只给来源坐标」），也可以两个都给::

        svc = LineageQueryService(Neo4jGraphStore(), LakehouseResolver())
        res = svc.forward_trace("COLLECT_BP_20240115143022_a3f8")
        print(res.as_dict()["lakehouse_sources"])
    """

    def __init__(
        self,
        graph: Neo4jGraphStore | None = None,
        resolver: LakehouseResolver | None = None,
        *,
        policy: GraphPropertyPolicy = GraphPropertyPolicy.TRAVERSAL_KEYS,
        path_limit: int = MAX_PATHS_PER_QUERY,
    ) -> None:
        """
        :param graph: 图库；默认新建一个（连接在首次查询时才建立）
        :param resolver: 湖仓回取器；None 表示只返回路径与来源坐标，不补属性
        :param policy: 图库属性策略，必须与写入端一致。ID_ONLY 时影响分析会先回湖仓
            取 artifact_id 列表再进图库（因为图上没有 step / algo_version 可过滤）
        :param path_limit: 单次查询返回的路径条数上限（扇出闸门）
        """
        self._graph = graph if graph is not None else Neo4jGraphStore()
        self._resolver = resolver
        self._policy = policy
        self._path_limit = path_limit

    # ---- 内部：执行 + 组装 ----

    @staticmethod
    def _to_paths(rows: Sequence[Mapping[str, Any]]) -> list[LineagePath]:
        """把图库返回的 (nodes, rels) 行组装成 :class:`LineagePath`。

        非法标签 / 非法关系类型的行会被跳过并告警——图上可能存在本模块之外写入的
        节点（例如别的子系统的实验数据），血缘查询不该因此炸掉。
        """
        paths: list[LineagePath] = []
        for row in rows:
            try:
                node_refs = tuple(
                    NodeRef(NodeLabel(n["label"]), str(n["id"])) for n in row.get("nodes", [])
                )
                by_id = {n.node_id: n for n in node_refs}
                hops = tuple(
                    PathHop(
                        RelType(r["type"]),
                        by_id[str(r["start"])],
                        by_id[str(r["end"])],
                    )
                    for r in row.get("rels", [])
                )
            except (KeyError, ValueError) as exc:
                _log.warning("跳过无法解析的血缘路径行: %s", exc)
                continue
            if node_refs:
                paths.append(LineagePath(node_refs, hops))
        return paths

    def _enrich(self, result: LineageResult, extra_refs: Iterable[NodeRef] = ()) -> LineageResult:
        """第②③步：按节点 ID 回湖仓补属性，并把来源坐标（表名 + ID）挂到结果上。"""
        refs = list(result.node_refs()) + list(extra_refs)
        if not refs:
            return result
        if self._resolver is None:
            # 没有湖仓通道时仍然给出来源坐标——审计链不能断（护栏四的下限）
            result.sources.extend(r.source for r in refs)
            result.notes.append(
                "未配置 LakehouseResolver：仅返回血缘路径与湖仓来源坐标，未补属性明细"
            )
            return result
        try:
            attributes, sources = self._resolver.resolve_refs(refs)
        except LakehouseUnavailable as exc:
            result.sources.extend(r.source for r in refs)
            result.notes.append(f"湖仓回取失败，仅返回路径与来源坐标: {exc}")
            return result
        result.attributes.update(attributes)
        result.sources.extend(sources)
        return result

    def _run_paths(
        self,
        cypher: str,
        params: dict[str, Any],
        *,
        direction: QueryDirection,
        anchor: NodeRef,
        depth: int,
    ) -> LineageResult:
        params = {"limit": self._path_limit, **params}
        rows = self._graph.run_read(cypher, params, limit_guard=self._path_limit)
        result = LineageResult(direction=direction, anchor=anchor, depth=depth)
        result.paths = self._to_paths(rows)
        result.truncated = len(rows) >= self._path_limit
        if result.truncated:
            result.notes.append(
                f"命中扇出闸门：路径数已达 {self._path_limit} 条被截断。"
                "大批量下游分析请改走湖仓离线统计（[a13] 六·遍历边界）"
            )
        result.notes.append(f"遍历深度 {depth}（安全区间 {TRAVERSAL_DEPTH_RANGE_TEXT}）")
        return result

    # ---- 方向一：正向追踪 ----

    def forward_trace(self, data_id: str, *, depth: int | None = None) -> LineageResult:
        """正向追踪：这批采集数据最终用在了哪些数据集与模型上？

        遍历路径（[a13] 五）：clip → 产物 → 数据集 → 训练 → 评测。

        ⚠️ 原文未明确，本项目设计：图库只有五类节点（Clip / Artifact / Run /
        DatasetVersion / Badcase），**没有 Training / Evaluation 节点**。所以图库这一段
        只能走到 DatasetVersion；「→ 训练 → 评测」两跳改由湖仓按 dataset_version_id
        关联训练域 / 评测域的表完成——这与「图库找关系、湖仓取明细」的分工也一致。
        结果里会用 note 显式说明这一点，不假装图库全走完了。

        方向说明：CONTAINS 是 Clip→Artifact、DERIVED_FROM 是子→父、REFERENCES 是
        DatasetVersion→Artifact，三者方向并不同向，因此这里用**无向**变长匹配加
        关系类型白名单来表达「顺着链路往下游走」，再靠深度闸门控扇出。

        :param data_id: 一级 ID（clip 锚点）
        :param depth: 遍历深度，默认取配置（5），必须落在 3-5 跳
        :raises ValueError: data_id 非法
        """
        parse_data_id(data_id)
        d = resolve_depth(depth)
        anchor = NodeRef(NodeLabel.CLIP, data_id)
        pattern = rel_pattern(
            (
                RelType.CONTAINS,
                RelType.DERIVED_FROM,
                RelType.SUPERSEDED_BY,
                RelType.REFERENCES,
            )
        )
        cypher = (
            f"MATCH path = (c:`Clip` {{id: $anchor}})-[{pattern}*1..{d}]-(n)\n"
            "WHERE n:`Artifact` OR n:`DatasetVersion`\n" + _RETURN_PATH
        )
        result = self._run_paths(
            cypher,
            {"anchor": data_id},
            direction=QueryDirection.FORWARD_TRACE,
            anchor=anchor,
            depth=d,
        )
        result.notes.append(
            "图库段止于 DatasetVersion；训练任务与评测结果按 dataset_version_id 回湖仓关联"
            "（⚠️ 原文未明确，本项目设计：五类节点里没有 Training/Evaluation 节点）"
        )
        return self._enrich(result, extra_refs=[anchor])

    # ---- 方向二：反向追溯 ----

    def backward_trace(self, badcase_id: str, *, depth: int | None = None) -> LineageResult:
        """反向追溯：这个 Badcase 是哪个版本模型、什么参数跑出来的？

        遍历路径（[a13] 五）：Badcase → 评测 → 数据集版本 → 产物算法版本与参数。

        对应 [a13] 5.1 的原文 Cypher（见 :data:`SOURCE_CYPHER_BACKWARD_TRACE`）。
        与原文的差异，逐条说明：
          1. 原文只走 ``TRACED_TO`` + ``PRODUCED`` 两跳；本方法走变长 1..depth，
             顺带把 DERIVED_FROM / REFERENCES / CONTAINS 一起带出来——这样一次查询就能
             追到源头 clip 与所属数据集版本，符合「Badcase → 评测 → 数据集版本 → 产物」
             这条原文声明的完整路径。
          2. 原文 ``RETURN r.params, a.param_snapshot, a.content_hash`` 直接读图库属性；
             本方法只取 ID，参数快照与 content_hash 按 run_id / artifact_id 回湖仓取
             （护栏「属性单一事实源」）。
          3. 原文 ``ORDER BY r.start_time DESC``；start_time 是黑名单属性不落图库，
             排序改在湖仓侧完成——见结果里 Run 的 ``start_time`` 属性。

        :param badcase_id: Badcase ID，示例 BC_20240120_001
        """
        if not badcase_id:
            raise ValueError("badcase_id 不能为空（示例形如 BC_20240120_001）")
        d = resolve_depth(depth)
        anchor = NodeRef(NodeLabel.BADCASE, badcase_id)
        pattern = rel_pattern(
            (
                RelType.CONTAINS,
                RelType.DERIVED_FROM,
                RelType.SUPERSEDED_BY,
                RelType.PRODUCED,
                RelType.INPUT,
                RelType.REFERENCES,
                RelType.TRACED_TO,
            )
        )
        cypher = (
            f"MATCH path = (b:`Badcase` {{id: $anchor}})-[{pattern}*1..{d}]-(n)\n"
            "WHERE n:`Artifact` OR n:`Run` OR n:`Clip` OR n:`DatasetVersion`\n" + _RETURN_PATH
        )
        result = self._run_paths(
            cypher,
            {"anchor": badcase_id},
            direction=QueryDirection.BACKWARD_TRACE,
            anchor=anchor,
            depth=d,
        )
        result = self._enrich(result, extra_refs=[anchor])
        # 原文的 ORDER BY r.start_time DESC —— 在湖仓属性上做
        runs = [r.node_id for r in result.nodes_of(NodeLabel.RUN)]
        if runs:
            ordered = sorted(
                runs,
                key=lambda rid: str(result.attributes.get(rid, {}).get("start_time") or ""),
                reverse=True,
            )
            result.notes.append("Run 按湖仓 start_time 倒序：" + ", ".join(ordered[:10]))
        return result

    # ---- 方向三：版本分支对比 ----

    def compare_versions(
        self, data_id: str, step: str, *, depth: int | None = None
    ) -> LineageResult:
        """版本分支对比：同一批数据，SLAM v3 与 v4 哪个质量更好？

        遍历路径（[a13] 五）：同一 clip 不同 algo_version 产物并列。
        承载关系是 SUPERSEDED_BY（[a13] 三·版本血缘），语义见 3.2：
        「血缘图形成版本分支：同一 clip 的 SLAM 环节存在 v3 / v4 两个分支，
        直接对比两版的标注与评测效果」。

        质量指标不在图库——回湖仓按 artifact_id 取（结果的 ``attributes``）。

        :param data_id: 一级 ID（clip 锚点），重刷不变（[a11] 三·规则 3）
        :param step: 产线环节，取值见 :data:`constants.PIPELINE_STAGES`
            （align / slam / ann / qc / post）
        """
        parse_data_id(data_id)
        if not step:
            raise ValueError("step 不能为空（align / slam / ann / qc / post）")
        d = resolve_depth(depth)
        anchor = NodeRef(NodeLabel.CLIP, data_id)
        pattern = rel_pattern((RelType.CONTAINS, RelType.SUPERSEDED_BY))
        if self._policy is GraphPropertyPolicy.ID_ONLY:
            # 图上没有 step 可过滤：改用 artifact_id 的字符串结构过滤
            # （artifact_id = {data_id}_{step}_{algo_version}_{content_hash}，[a11] 二）
            where = "WHERE n:`Artifact` AND n.`id` STARTS WITH $prefix"
            params = {"anchor": data_id, "prefix": f"{data_id}_{step.lower()}_"}
        else:
            where = "WHERE n:`Artifact` AND n.`step` = $step"
            params = {"anchor": data_id, "step": step.lower()}
        cypher = (
            f"MATCH path = (c:`Clip` {{id: $anchor}})-[{pattern}*1..{d}]-(n)\n"
            f"{where}\n" + _RETURN_PATH
        )
        result = self._run_paths(
            cypher,
            params,
            direction=QueryDirection.VERSION_COMPARE,
            anchor=anchor,
            depth=d,
        )
        result = self._enrich(result, extra_refs=[anchor])
        branches = self.group_by_algo_version(result)
        result.notes.append(
            f"clip {data_id} 在 {step} 环节的版本分支: "
            + (", ".join(f"{v}×{len(ids)}" for v, ids in sorted(branches.items())) or "无")
            + f"（原文示例为 SLAM {SLAM_OLD_VERSION} → {SLAM_NEW_VERSION}）"
        )
        result.notes.append("重刷绝不是删旧写新——旧产物与旧分支必须完整保留（[a13] 3.2 实践提醒）")
        return result

    @staticmethod
    def group_by_algo_version(result: LineageResult) -> dict[str, list[str]]:
        """把结果里的 Artifact 按算法版本分组，用于并列对比。

        版本来源优先用湖仓属性（事实源），拿不到时退回解析 artifact_id
        ——「ID 本身即信息」在这里兑现（[a11] 三）。
        """
        groups: dict[str, list[str]] = {}
        for ref in result.nodes_of(NodeLabel.ARTIFACT):
            attrs = result.attributes.get(ref.node_id, {})
            version = str(attrs.get("algo_version") or "") or None
            if version is None:
                try:
                    version = parse_artifact_id(ref.node_id).algo_version
                except ValueError:
                    version = "unknown"
            groups.setdefault(version, []).append(ref.node_id)
        return groups

    # ---- 方向四：影响分析 ----

    def impact_analysis(
        self,
        step: str,
        algo_version: str,
        *,
        depth: int | None = None,
        fanout_threshold: int = OFFLINE_IMPACT_FANOUT_THRESHOLD,
    ) -> LineageResult:
        """影响分析：算法升级后，哪些数据集与训练任务需要重刷？

        遍历路径（[a13] 五）：SUPERSEDED_BY + 下游 REFERENCES 统计。
        对应 [a13] 5.1 的原文 Cypher，见 :data:`SOURCE_CYPHER_IMPACT_ANALYSIS`::

            MATCH (a:Artifact {step:'slam', algo_version:'v3'})<-[:REFERENCES]-(d:DatasetVersion)
            RETURN DISTINCT d.dataset_id, d.version, d.status;

        与原文的差异：原文 RETURN 图库属性，本方法只取 DatasetVersion 的 ID，
        dataset_id / version / status 回 dwd_dataset_version_detail 取（护栏一）。

        护栏三的后半句在这里落地：**大批量下游影响分析改走湖仓离线统计审计链路**。
        本方法先在图库数一次命中的旧产物个数，超过 ``fanout_threshold``
        （⚠️ 原文未给阈值，本项目设定 10000，见 constants）就不在图库硬算，
        直接返回 ``offline_sql`` 让调用方去 StarRocks 跑离线统计。

        :param step: 产线环节，如 slam
        :param algo_version: 被替代的旧算法版本，如 v3
        """
        if not step or not algo_version:
            raise ValueError("step 与 algo_version 都不能为空，例如 step='slam', algo_version='v3'")
        d = resolve_depth(depth)
        step = step.lower()
        anchor = NodeRef(NodeLabel.ARTIFACT, f"{step}@{algo_version}")
        result = LineageResult(direction=QueryDirection.IMPACT_ANALYSIS, anchor=anchor, depth=d)
        if self._policy is GraphPropertyPolicy.ID_ONLY:
            result.notes.append(
                "属性策略 ID_ONLY：图库无 step/algo_version 可过滤，"
                "请先回湖仓取旧版本 artifact_id 列表再调 impact_analysis_by_ids()"
            )
            result.offline_sql = self.offline_impact_sql(step, algo_version)
            return result

        # 体检：先数个数，决定走图库还是走离线
        count_rows = self._graph.run_read(
            "MATCH (a:`Artifact` {step: $step, algo_version: $algo_version})\n"
            "RETURN count(a) AS cnt",
            {"step": step, "algo_version": algo_version},
            limit_guard=1,
        )
        hit = int(count_rows[0]["cnt"]) if count_rows else 0
        result.notes.append(f"命中旧版本产物 {hit} 个（阈值 {fanout_threshold}）")
        if hit > fanout_threshold:
            result.offline_sql = self.offline_impact_sql(step, algo_version)
            result.truncated = True
            result.notes.append(
                f"超过扇出阈值 {fanout_threshold}，不在图库硬算；"
                "请执行 offline_sql（ddl/starrocks_lineage.sql 的离线统计链路，[a13] 六·遍历边界）"
            )
            return result

        pattern = rel_pattern((RelType.REFERENCES, RelType.SUPERSEDED_BY))
        cypher = (
            "MATCH path = (a:`Artifact` {step: $step, algo_version: $algo_version})"
            f"-[{pattern}*1..{d}]-(n)\n"
            "WHERE n:`DatasetVersion` OR n:`Artifact`\n" + _RETURN_PATH
        )
        result = self._run_paths(
            cypher,
            {"step": step, "algo_version": algo_version},
            direction=QueryDirection.IMPACT_ANALYSIS,
            anchor=anchor,
            depth=d,
        )
        result.notes.append(f"命中旧版本产物 {hit} 个（阈值 {fanout_threshold}）")
        result = self._enrich(result)
        datasets = result.nodes_of(NodeLabel.DATASET_VERSION)
        result.notes.append(
            f"需要重刷排期的数据集版本 {len(datasets)} 个: "
            + ", ".join(n.node_id for n in datasets[:10])
        )
        result.offline_sql = self.offline_impact_sql(step, algo_version)
        return result

    def impact_analysis_by_ids(
        self, artifact_ids: Sequence[str], *, depth: int | None = None
    ) -> LineageResult:
        """影响分析的 ID_ONLY 变体：先在湖仓筛出旧产物 ID，再拿 ID 进图库统计下游。

        ⚠️ 原文未明确，本项目设计：原文的影响分析 Cypher 依赖图库上的 step /
        algo_version 属性；在严格护栏（:attr:`GraphPropertyPolicy.ID_ONLY`）下图上没有
        这两个属性，只能由湖仓先给 ID 列表。语义与原文等价，只是过滤下推到了湖仓。
        """
        ids = [i for i in dict.fromkeys(artifact_ids) if i]
        if not ids:
            raise ValueError("artifact_ids 不能为空")
        d = resolve_depth(depth)
        anchor = NodeRef(NodeLabel.ARTIFACT, ids[0])
        pattern = rel_pattern((RelType.REFERENCES, RelType.SUPERSEDED_BY))
        cypher = (
            "MATCH path = (a:`Artifact`)"
            f"-[{pattern}*1..{d}]-(n)\n"
            "WHERE a.`id` IN $ids AND (n:`DatasetVersion` OR n:`Artifact`)\n" + _RETURN_PATH
        )
        result = self._run_paths(
            cypher,
            {"ids": ids},
            direction=QueryDirection.IMPACT_ANALYSIS,
            anchor=anchor,
            depth=d,
        )
        result.notes.append(f"输入旧产物 {len(ids)} 个（由湖仓过滤下推得到）")
        return self._enrich(result)

    @staticmethod
    def offline_impact_sql(step: str, algo_version: str) -> str:
        """渲染「大批量影响分析」的湖仓离线统计 SQL（护栏三的逃生通道）。

        与 ddl/starrocks_lineage.sql 里的 ``v_lineage_impact_by_algo_version`` 视图等价，
        这里内联一份便于直接贴进 StarRocks 执行。
        """
        cat = settings_external_catalog()
        return (
            "-- 大批量下游影响分析：走湖仓离线统计，不在图库硬算（[a13] 六·遍历边界）\n"
            # 列名一律用 catalog.registry 的湖仓口径：图库属性 step/status 在湖仓
            # 分别叫 stage / artifact_status / version_status
            "SELECT d.`dataset_id`, d.`version`, d.`version_status`,\n"
            "       COUNT(DISTINCT a.`artifact_id`) AS stale_artifact_cnt\n"
            f"FROM {cat}.`dwd_dataset_version_detail` d\n"
            f"JOIN {cat}.`dwd_production_artifact_detail` a\n"
            "  ON d.`artifact_refs` LIKE CONCAT('%', a.`artifact_id`, '%')\n"
            f"WHERE a.`stage` = '{step}' AND a.`algo_version` = '{algo_version}'\n"
            "  AND a.`artifact_status` = 'superseded'\n"
            "GROUP BY d.`dataset_id`, d.`version`, d.`version_status`\n"
            "ORDER BY stale_artifact_cnt DESC;"
        )


def settings_external_catalog() -> str:
    """``catalog.database`` 前缀，供离线 SQL 拼接。"""
    from ..config import settings

    st = settings()
    return f"`{st.starrocks.external_catalog}`.`{st.paimon.database}`"
