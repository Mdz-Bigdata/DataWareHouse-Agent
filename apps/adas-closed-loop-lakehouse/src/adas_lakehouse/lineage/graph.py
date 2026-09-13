"""Neo4j 图库访问层：幂等 MERGE 建图 + 受控的多跳遍历。

[a13] 4.1 的标题就是本模块的设计原则：「图库建图：MERGE 幂等是一切的前提」。
实时链路（链路二）可能重复投递，对账链路（链路三）按天重跑——只有 MERGE 语义
能让两条链路叠加之后图的状态仍然确定。

依赖处理
    ``neo4j`` 官方驱动是**延迟 import** 的：没装驱动时 ``import
    adas_lakehouse.lineage.graph`` 照样成功，只有真正建连接时才抛
    :class:`Neo4jUnavailable`。这样全量 import 检查与纯语句生成（
    :meth:`Neo4jGraphStore.merge_statements`）都不需要装驱动。

语句注入安全
    Cypher 里的标签与关系类型**不能**参数化，只能拼字符串。本模块所有拼进语句的
    标签 / 关系类型都来自封闭枚举 :class:`~adas_lakehouse.lineage.model.NodeLabel`
    与 :class:`~adas_lakehouse.lineage.model.RelType`，深度来自
    :func:`resolve_depth` 的整数校验，其余一律走 ``$参数``。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..config import Neo4jConfig, settings
from .constants import (
    GRAPH_QUERY_TIMEOUT_SECONDS,
    MAX_PATHS_PER_QUERY,
    MAX_TRAVERSAL_DEPTH,
    MIN_TRAVERSAL_DEPTH,
    REALTIME_MAX_RETRIES,
    REALTIME_RETRY_BACKOFF_SECONDS,
    TRAVERSAL_DEPTH_RANGE_TEXT,
)
from .model import (
    GraphEdge,
    GraphMutation,
    GraphNode,
    NodeLabel,
    RelType,
)

__all__ = [
    "Neo4jUnavailable",
    "LineageGraphError",
    "TraversalDepthError",
    "Statement",
    "Neo4jGraphStore",
    "resolve_depth",
    "rel_pattern",
    "CONSTRAINT_STATEMENTS",
    "SOURCE_MERGE_SNIPPET",
]

_log = logging.getLogger(__name__)


class LineageGraphError(RuntimeError):
    """图库操作失败的通用错误。"""


class Neo4jUnavailable(LineageGraphError):
    """neo4j 驱动未安装，或图库连不上。

    红线①「实时链路失败不阻塞湖仓写入」要求这个异常在同步链路里被**吞掉并记账**，
    而不是穿到湖仓写入侧。见 :mod:`adas_lakehouse.lineage.sync`。
    """


class TraversalDepthError(LineageGraphError):
    """遍历深度越界。护栏三「遍历边界」：多跳遍历限定 3-5 跳防扇出爆炸（[a13] 六）。"""


# --------------------------------------------------------------------------- 深度闸门


def resolve_depth(requested: int | None = None, *, clamp: bool = False) -> int:
    """把请求深度收敛到 3-5 跳的安全区间。

    出处 [a13] 六·遍历边界与 [a11] 六·遍历边界：「多跳遍历限定深度 3–5 跳防止扇出爆炸」，
    [a13] 高频踩坑补了一句「3–5 跳是实践验证过的安全区间」。

    :param requested: 请求深度；None 表示用 ``settings().neo4j.max_traversal_depth``
        （默认 5，即区间上界）
    :param clamp: False（默认）越界直接抛错——宁可让调用方改代码，也不要悄悄改语义；
        True 则夹到区间内，供交互式探索工具使用
    :raises TraversalDepthError: clamp=False 且深度越界

    >>> resolve_depth(4)
    4
    >>> resolve_depth(9, clamp=True)
    5
    """
    if requested is None:
        requested = settings().neo4j.max_traversal_depth
    if not isinstance(requested, int) or isinstance(requested, bool):
        raise TraversalDepthError(f"遍历深度必须是整数，收到 {requested!r}")
    if MIN_TRAVERSAL_DEPTH <= requested <= MAX_TRAVERSAL_DEPTH:
        return requested
    if clamp:
        clamped = min(max(requested, MIN_TRAVERSAL_DEPTH), MAX_TRAVERSAL_DEPTH)
        _log.warning(
            "遍历深度 %s 越界，已夹到 %s（安全区间 %s，[a13] 六·遍历边界）",
            requested,
            clamped,
            TRAVERSAL_DEPTH_RANGE_TEXT,
        )
        return clamped
    raise TraversalDepthError(
        f"遍历深度 {requested} 越界；安全区间是 {TRAVERSAL_DEPTH_RANGE_TEXT}"
        f"（{MIN_TRAVERSAL_DEPTH}-{MAX_TRAVERSAL_DEPTH}）。"
        "超大规模的下游影响评估别在图库里硬算，走湖仓离线统计（[a13] 六·高频踩坑）"
    )


def rel_pattern(rels: Iterable[RelType]) -> str:
    """把关系类型集合渲染成 Cypher 的 ``:A|B|C`` 片段（去重、保持枚举声明序）。

    :raises LineageGraphError: 集合为空——无类型限定的变长遍历正是扇出爆炸的经典成因
    """
    ordered = [r for r in RelType if r in set(rels)]
    if not ordered:
        raise LineageGraphError("变长遍历必须限定关系类型，否则扇出不可控（[a13] 六·遍历边界）")
    return ":" + "|".join(r.value for r in ordered)


# --------------------------------------------------------------------------- 语句


@dataclass(frozen=True, slots=True)
class Statement:
    """一条待执行的 Cypher：语句文本 + 参数。

    分离出这个类型是为了让「生成语句」与「执行语句」解耦——
    :meth:`Neo4jGraphStore.merge_statements` 是纯函数，不连库也能单测与 review。
    """

    cypher: str
    params: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.cypher


#: 图库约束：五类节点各一个 id 唯一约束。
#:
#: ⚠️ 原文未明确，本项目设计：[a13] 只给了 MERGE 语句，没提约束。但 MERGE 在没有唯一
#: 约束时并发下会产生重复节点（Neo4j 的 MERGE 只在读锁范围内保证唯一），所以唯一约束
#: 是「MERGE 幂等」这条前提能成立的必要条件，必须建。
CONSTRAINT_STATEMENTS: tuple[str, ...] = tuple(
    f"CREATE CONSTRAINT `lineage_{label.value.lower()}_id` IF NOT EXISTS "
    f"FOR (n:`{label.value}`) REQUIRE n.`id` IS UNIQUE"
    for label in NodeLabel
)

#: [a13] 4.1 的原文建图语句，逐字保留用于对照（本模块生成的语句与它等价，
#: 差异只有 r.params 不落图库——见 model 模块 docstring 的矛盾说明）。
SOURCE_MERGE_SNIPPET: str = """\
// 产物节点 + 版本替代边 + 运行输入输出边 + 数据集引用边   —— [a13] 4.1 原文
MERGE (a:Artifact {id:'..._slam_v4_b7e8f9a0'})
SET a.step='slam', a.algo_version='v4', a.status='active';
MATCH (old:Artifact {id:'..._slam_v3_c4d5e6f7'})
MERGE (old)-[:SUPERSEDED_BY]->(a);
MERGE (r:Run {id:'run_slam_20240116103000_9f3a21c7'})
SET r.status='success', r.params='{"max_iter":200}';
MERGE (r)-[:INPUT]->(:Artifact {id:'..._align_v1_9f3a21c7'});
MERGE (r)-[:PRODUCED]->(a);
MERGE (d:DatasetVersion {id:'DS_0001_V2'})
MERGE (d)-[:REFERENCES]->(a);"""


# --------------------------------------------------------------------------- 图库


class Neo4jGraphStore:
    """Neo4j 访问封装：建约束、应用变更集、执行受控读查询。

    典型用法::

        store = Neo4jGraphStore()
        store.init_schema()
        store.apply(fact.to_mutation())
        rows = store.run_read("MATCH (n:Clip {id:$id}) RETURN n.id AS id", {"id": did})
        store.close()

    也可以注入一个假驱动做单测（``driver`` 参数），或干脆只用
    :meth:`merge_statements` 生成语句而完全不连库。
    """

    def __init__(
        self,
        config: Neo4jConfig | None = None,
        *,
        driver: Any | None = None,
        query_timeout: float = GRAPH_QUERY_TIMEOUT_SECONDS,
    ) -> None:
        """
        :param config: 连接配置；默认取 ``settings().neo4j``
        :param driver: 已建好的 neo4j Driver（或鸭子类型兼容的假对象），
            给定时不会再去 import neo4j——单测与离线渲染场景用
        :param query_timeout: 读查询超时（秒）
        """
        self._config = config if config is not None else settings().neo4j
        self._driver = driver
        self._owns_driver = driver is None
        self._query_timeout = query_timeout

    # ---- 连接生命周期 ----

    @property
    def config(self) -> Neo4jConfig:
        return self._config

    def _load_driver_module(self) -> Any:
        """延迟 import neo4j 驱动。未安装时给出可操作的错误信息。"""
        try:
            import neo4j  # type: ignore[import-not-found]
        except ImportError as exc:
            raise Neo4jUnavailable(
                "未安装 neo4j 驱动；血缘图库功能不可用。安装：pip install neo4j。"
                "（模块本身可正常 import，纯语句生成不需要驱动）"
            ) from exc
        return neo4j

    def driver(self) -> Any:
        """返回驱动实例，首次调用时建连接。"""
        if self._driver is None:
            neo4j = self._load_driver_module()
            cfg = self._config
            try:
                self._driver = neo4j.GraphDatabase.driver(cfg.uri, auth=(cfg.user, cfg.password))
            except Exception as exc:  # 驱动内部异常类型很杂，统一包一层
                raise Neo4jUnavailable(f"连接 Neo4j 失败: {cfg.uri}: {exc}") from exc
        return self._driver

    def verify_connectivity(self) -> bool:
        """探活。连不上返回 False 而不是抛——健康检查不该把调用方打断。"""
        try:
            self.driver().verify_connectivity()
        except Exception as exc:
            _log.warning("Neo4j 探活失败 uri=%s: %s", self._config.uri, exc)
            return False
        return True

    def close(self) -> None:
        """关闭自己建的驱动；外部注入的驱动由外部负责关闭。"""
        if self._driver is not None and self._owns_driver:
            try:
                self._driver.close()
            except Exception as exc:  # pragma: no cover - 关闭失败无需阻断
                _log.warning("关闭 Neo4j 驱动失败: %s", exc)
        if self._owns_driver:
            self._driver = None

    def __enter__(self) -> Neo4jGraphStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ---- 语句生成（纯函数，不连库） ----

    @staticmethod
    def node_statement(node: GraphNode, *, channel: str = "realtime") -> Statement:
        """一个节点的幂等 MERGE。

        属性用 ``SET n += $props`` 增量合并而不是覆盖——对账链路只补它知道的字段，
        不该把实时链路先写进去的遍历键抹掉。
        """
        return Statement(
            cypher=(
                f"MERGE (n:`{node.label.value}` {{id: $id}})\n"
                f"SET n += $props, n.`_synced_at` = timestamp(), n.`_sync_channel` = $channel"
            ),
            params={
                "id": node.node_id,
                "props": dict(node.properties),
                "channel": channel,
            },
        )

    @staticmethod
    def edge_statement(
        edge: GraphEdge,
        *,
        channel: str = "realtime",
        create_missing_endpoints: bool = True,
    ) -> Statement:
        """一条边的幂等 MERGE。

        :param create_missing_endpoints: True（默认）两端也用 MERGE——与 [a13] 4.1 原文
            ``MERGE (r)-[:INPUT]->(:Artifact {id:'...'})`` 的语义一致：乱序到达时先建
            一个「裸 ID 节点」占位，属性等它自己那条 changelog 到了再补。
            False 则用 MATCH，两端不存在就静默不建边（对账链路发现孤儿边时用）。
        """
        spec = edge.spec
        verb = "MERGE" if create_missing_endpoints else "MATCH"
        return Statement(
            cypher=(
                f"{verb} (s:`{spec.src.value}` {{id: $src}})\n"
                f"{verb} (d:`{spec.dst.value}` {{id: $dst}})\n"
                f"MERGE (s)-[r:`{edge.rel.value}`]->(d)\n"
                f"SET r.`_synced_at` = timestamp(), r.`_sync_channel` = $channel"
            ),
            params={"src": edge.src_id, "dst": edge.dst_id, "channel": channel},
        )

    @classmethod
    def merge_statements(
        cls,
        mutation: GraphMutation,
        *,
        create_missing_endpoints: bool = True,
    ) -> list[Statement]:
        """把一个变更集翻译成幂等语句序列（节点先、边后）。

        节点先于边是必要的：边语句里的 MERGE 端点只带 ``id``，若节点语句后跑，
        属性会被写在同一个节点上不会重复建点，但顺序反了会让「带属性的节点」
        在并发下更容易撞锁。同一个 ID 的节点会被去重，避免一批里重复 MERGE。
        """
        seen: set[tuple[str, str]] = set()
        out: list[Statement] = []
        for node in mutation.nodes:
            key = (node.label.value, node.node_id)
            if key in seen and not node.properties:
                continue  # 裸占位节点重复出现，跳过
            seen.add(key)
            out.append(cls.node_statement(node, channel=mutation.channel))
        edge_seen: set[tuple[str, str, str]] = set()
        for edge in mutation.edges:
            key3 = (edge.rel.value, edge.src_id, edge.dst_id)
            if key3 in edge_seen:
                continue
            edge_seen.add(key3)
            out.append(
                cls.edge_statement(
                    edge,
                    channel=mutation.channel,
                    create_missing_endpoints=create_missing_endpoints,
                )
            )
        return out

    # ---- 执行 ----

    def init_schema(self) -> int:
        """建五类节点的 id 唯一约束。幂等（IF NOT EXISTS），可反复执行。

        :returns: 执行的约束语句条数
        :raises Neo4jUnavailable: 驱动缺失或连不上
        """
        for cypher in CONSTRAINT_STATEMENTS:
            self._execute_write(Statement(cypher))
        _log.info("Neo4j 血缘图约束就绪：%d 条", len(CONSTRAINT_STATEMENTS))
        return len(CONSTRAINT_STATEMENTS)

    def apply(
        self,
        mutation: GraphMutation,
        *,
        create_missing_endpoints: bool = True,
        max_retries: int = REALTIME_MAX_RETRIES,
    ) -> int:
        """把一个变更集写进图库，整批一个事务。

        :param max_retries: 失败重试次数，退避秒数见
            :data:`constants.REALTIME_RETRY_BACKOFF_SECONDS`
        :returns: 执行的语句条数
        :raises Neo4jUnavailable: 重试耗尽仍失败。**调用方若在实时链路上，必须
            捕获它而不是往湖仓写入侧抛**（红线①）——:class:`sync.RealtimeLineageSync`
            已经替你做了。
        """
        statements = self.merge_statements(
            mutation, create_missing_endpoints=create_missing_endpoints
        )
        if not statements:
            return 0
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                self._execute_write_batch(statements)
            except Neo4jUnavailable:
                raise
            except Exception as exc:
                last_exc = exc
                if attempt >= max_retries:
                    break
                backoff = REALTIME_RETRY_BACKOFF_SECONDS[
                    min(attempt, len(REALTIME_RETRY_BACKOFF_SECONDS) - 1)
                ]
                _log.warning(
                    "图库写入失败（第 %d/%d 次），%.1fs 后重试: %s",
                    attempt + 1,
                    max_retries,
                    backoff,
                    exc,
                )
                time.sleep(backoff)
            else:
                return len(statements)
        raise Neo4jUnavailable(
            f"图库写入重试 {max_retries} 次仍失败（{mutation.summary()}）: {last_exc}"
        ) from last_exc

    def run_read(
        self,
        cypher: str,
        params: Mapping[str, Any] | None = None,
        *,
        limit_guard: int = MAX_PATHS_PER_QUERY,
    ) -> list[dict[str, Any]]:
        """执行只读查询，返回行字典列表。

        :param limit_guard: 返回行数上限；超过即截断并告警。护栏三只限制了跳数，
            挡不住横向扇出，这是补的第二道闸门（⚠️ 本项目设计，见 constants）。
        :raises Neo4jUnavailable: 驱动缺失或执行失败
        """
        driver = self.driver()
        try:
            with driver.session(database=self._config.database) as session:
                result = session.run(cypher, dict(params or {}), timeout=self._query_timeout)
                rows = [dict(record) for record in result]
        except Neo4jUnavailable:
            raise
        except Exception as exc:
            raise Neo4jUnavailable(f"图库查询失败: {exc}\nCypher:\n{cypher}") from exc
        if len(rows) > limit_guard:
            _log.warning(
                "图查询返回 %d 行，超过扇出闸门 %d，已截断；"
                "大批量下游影响分析请改走湖仓离线统计（[a13] 六·遍历边界）",
                len(rows),
                limit_guard,
            )
            rows = rows[:limit_guard]
        return rows

    # ---- 内部 ----

    def _execute_write(self, statement: Statement) -> None:
        self._execute_write_batch([statement])

    def _execute_write_batch(self, statements: Sequence[Statement]) -> None:
        driver = self.driver()
        try:
            with driver.session(database=self._config.database) as session:

                def _unit(tx: Any) -> None:
                    for st in statements:
                        tx.run(st.cypher, st.params)

                execute_write = getattr(session, "execute_write", None)
                if execute_write is None:  # 兼容 neo4j 4.x 的旧 API
                    execute_write = session.write_transaction
                execute_write(_unit)
        except Neo4jUnavailable:
            raise
        except Exception as exc:
            raise LineageGraphError(f"图库写事务失败（{len(statements)} 条语句）: {exc}") from exc
