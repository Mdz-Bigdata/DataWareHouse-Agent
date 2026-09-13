"""湖仓属性回取：按节点 ID 批量补齐属性、参数快照与质量指标。

这是「图库找关系、湖仓取明细」口诀的后半句（[a11] 六 / [a13] 五）。
一次完整的血缘查询按三步走（[a13] 五）::

    ① Neo4j 多跳遍历找路径、定范围（只返回节点 ID 与关系类型）
    → ② 按节点 ID 回 Paimon 补齐属性、参数快照与质量指标
    → ③ 组装输出带数据来源的血缘结果

本模块干的是第 ②步。[a13] 5.1 结尾的原话是判据：
「图库返回的只是节点 ID 集合——参数快照、质量分这些大属性全部回湖仓按 ID 批量取。
这样图库保持轻、遍历快，湖仓保持全、可审计。」

读湖仓的通道
    走 StarRocks External Catalog 直查 Paimon（``settings().starrocks.external_catalog``），
    这是本项目既有的查询侧通道，不再单独引一套 Paimon Python 客户端。
    ``pymysql`` 延迟 import；没装也不影响本模块被 import，
    且可以通过 ``fetch_fn`` 注入任意执行器（单测 / 换成 Flink SQL Gateway / Trino）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..config import StarRocksConfig, settings
from .constants import ID_BATCH_SIZE
from .model import (
    NODE_SOURCE_TABLES,
    LakehouseSource,
    NodeLabel,
    NodeRef,
    lakehouse_source_for,
)

__all__ = [
    "LakehouseUnavailable",
    "FetchFn",
    "LakehouseResolver",
    "ResolveResult",
    "ATTRIBUTE_COLUMNS",
    "chunked",
]

_log = logging.getLogger(__name__)

#: 执行器签名：(sql, 参数序列) -> 行字典列表。
FetchFn = Callable[[str, Sequence[Any]], list[dict[str, Any]]]


class LakehouseUnavailable(RuntimeError):
    """湖仓查询通道不可用（客户端库缺失 / 连接失败 / SQL 执行失败）。"""


def chunked(items: Sequence[str], size: int = ID_BATCH_SIZE) -> Iterable[tuple[str, ...]]:
    """按 :data:`constants.ID_BATCH_SIZE` 切批，供 ``IN (...)`` 批量回取。

    >>> list(chunked(("a", "b", "c"), 2))
    [('a', 'b'), ('c',)]
    """
    if size <= 0:
        raise ValueError(f"批次大小必须为正，收到 {size}")
    for i in range(0, len(items), size):
        yield tuple(items[i : i + size])


#: 各节点回湖仓要取的列。
#:
#: 原则：图库上**没有**的东西才在这里取——这正是护栏「属性单一事实源」的镜像。
#: ⚠️ 这里写的是**湖仓真实列名**（以 catalog.registry 为准），不是图库属性名。
#: 原文 DDL 与本项目 catalog 有四处不同名，对照表见 :data:`events._FACT_COLUMNS`：
#: ``step→stage`` / ``param_snapshot→param_snapshot_json`` /
#: ``superseded_by→superseded_by_artifact_id`` / ``status→artifact_status|run_status|version_status``。
#:
#: 列名依据：
#:   * Artifact：[a13] 3.1 的建表节选（param_snapshot / content_hash / status /
#:     parent_artifact_id / superseded_by 等），按 catalog 列名落地
#:   * Run：[a13] 三·运行血缘「每次处理的输入 / 输出 / 参数快照 / 时间」+ 5.1 反向追溯
#:     Cypher 的 RETURN 列（r.algo_version / r.params / r.start_time）
#:   * DatasetVersion：[a13] 5.1 影响分析的 RETURN 列（dataset_id / version / status）
#:     + 3.1「冗余 artifact_refs」
#:   * Clip：[a13] 3.1「采集数据单元（clip 级）」——具体列取自 catalog 的
#:     dwd_collect_clip_detail（车辆、时间、路况、天气）
#:   * Badcase：⚠️ 原文未明确，本项目设计（该表原文未给）
#:
#: ⚠️ 这些列所属的表归 catalog/tables/ 下各域模块所有，本模块只读不定义；
#: 若某列在实际建表里不存在，:meth:`LakehouseResolver.resolve` 会退化为
#: ``SELECT *``（见该方法的 fallback 说明），不会把查询打死。
ATTRIBUTE_COLUMNS: dict[NodeLabel, tuple[str, ...]] = {
    NodeLabel.CLIP: (
        "data_id",
        "collect_task_id",
        "vehicle_code",
        "project_code",
        "collect_start_time",
        "collect_end_time",
        "duration_sec",
        "road_type",
        "weather",
        "light_condition",
    ),
    NodeLabel.ARTIFACT: (
        "artifact_id",
        "data_id",
        "stage",
        "algo_version",
        "content_hash",
        "param_snapshot_json",
        "parent_artifact_id",
        "superseded_by_artifact_id",
        "artifact_status",
    ),
    NodeLabel.RUN: (
        "run_id",
        "stage",
        "run_status",
        "algo_version",
        "param_snapshot_json",
        "input_artifact_ids",
        "output_artifact_ids",
        "start_time",
        "end_time",
    ),
    NodeLabel.DATASET_VERSION: (
        "dataset_version_id",
        "dataset_id",
        "version",
        "version_status",
        "artifact_refs",
    ),
    NodeLabel.BADCASE: (
        "badcase_id",
        "data_id",
        "evaluation_type",
        "traced_artifact_ids",
    ),
}


@dataclass(slots=True)
class ResolveResult:
    """一次回取的结果：属性 + 来源坐标 + 缺失名单。

    ``missing`` 不是错误——图库上有节点、湖仓查不到行，正是「实时链路先建了裸占位节点、
    湖仓那一行还没到 / 已被生命周期归档」的正常中间态，调用方按需决定是否告警。
    """

    label: NodeLabel
    attributes: dict[str, dict[str, Any]] = field(default_factory=dict)
    sources: list[LakehouseSource] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


class LakehouseResolver:
    """按 ID 批量回湖仓补属性。

    典型用法::

        resolver = LakehouseResolver()
        res = resolver.resolve(NodeLabel.ARTIFACT, ["COLLECT_BP_..._slam_v4_b7e8f9a0"])
        res.attributes["COLLECT_BP_..._slam_v4_b7e8f9a0"]["param_snapshot_json"]

    单测或换执行器时注入 ``fetch_fn``::

        resolver = LakehouseResolver(fetch_fn=lambda sql, args: fake_rows)
    """

    def __init__(
        self,
        config: StarRocksConfig | None = None,
        *,
        fetch_fn: FetchFn | None = None,
        batch_size: int = ID_BATCH_SIZE,
    ) -> None:
        """
        :param config: StarRocks 连接配置，默认 ``settings().starrocks``
        :param fetch_fn: 自定义执行器；给定时完全不碰 pymysql
        :param batch_size: 单条 SQL 的 IN 列表长度上限
        """
        self._config = config if config is not None else settings().starrocks
        self._fetch_fn = fetch_fn
        self._conn: Any | None = None
        self._batch_size = batch_size

    # ---- 连接 ----

    def _connect(self) -> Any:
        """延迟 import pymysql 并建连接。未安装时抛 :class:`LakehouseUnavailable`。"""
        if self._conn is not None:
            return self._conn
        try:
            import pymysql  # type: ignore[import-not-found]
        except ImportError as exc:
            raise LakehouseUnavailable(
                "未安装 pymysql，无法直连 StarRocks 回取湖仓属性。"
                "安装：pip install pymysql；或给 LakehouseResolver 传 fetch_fn 自定义执行器"
            ) from exc
        cfg = self._config
        try:
            self._conn = pymysql.connect(
                host=cfg.fe_host,
                port=cfg.query_port,
                user=cfg.user,
                password=cfg.password,
                charset="utf8mb4",
                autocommit=True,
                cursorclass=pymysql.cursors.DictCursor,
            )
        except Exception as exc:
            raise LakehouseUnavailable(
                f"连接 StarRocks 失败 {cfg.fe_host}:{cfg.query_port}: {exc}"
            ) from exc
        return self._conn

    def _default_fetch(self, sql: str, args: Sequence[Any]) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(args))
                return [dict(row) for row in cur.fetchall()]
        except LakehouseUnavailable:
            raise
        except Exception as exc:
            raise LakehouseUnavailable(f"湖仓查询失败: {exc}\nSQL:\n{sql}") from exc

    def close(self) -> None:
        """关闭连接（注入 fetch_fn 时无操作）。"""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception as exc:  # pragma: no cover
                _log.warning("关闭 StarRocks 连接失败: %s", exc)
            self._conn = None

    def __enter__(self) -> LakehouseResolver:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ---- 查询 ----

    @property
    def fetch(self) -> FetchFn:
        """当前使用的执行器：注入的 ``fetch_fn``，否则内建的 StarRocks 执行器。

        对账链路（:class:`sync.ReconciliationJob`）复用它做增量扫描，
        这样「回取属性」与「扫描增量」共用同一条湖仓通道与同一套失败语义。
        """
        return self._fetch_fn or self._default_fetch

    #: 内部别名，保持旧调用点可用
    _fetch = fetch

    def qualified(self, table: str) -> str:
        """``catalog.database.table`` 三段式限定名（走 StarRocks External Catalog 直查 Paimon）。"""
        st = self._config
        paimon = settings().paimon
        return f"`{st.external_catalog}`.`{paimon.database}`.`{table}`"

    def build_select(
        self, label: NodeLabel, ids: Sequence[str], *, columns: Sequence[str] | None = None
    ) -> tuple[str, tuple[str, ...]]:
        """生成一条按 ID 批量回取的 SELECT（参数化，ID 走占位符）。

        :returns: (sql, 参数元组)

        >>> r = LakehouseResolver(fetch_fn=lambda s, a: [])
        >>> sql, args = r.build_select(NodeLabel.CLIP, ["A", "B"], columns=["data_id"])
        >>> args
        ('A', 'B')
        >>> "IN (%s, %s)" in sql
        True
        """
        table, id_column = NODE_SOURCE_TABLES[label]
        cols = tuple(columns) if columns is not None else ATTRIBUTE_COLUMNS[label]
        col_sql = ", ".join(f"`{c}`" for c in cols) if cols else "*"
        placeholders = ", ".join(["%s"] * len(ids))
        sql = (
            f"SELECT {col_sql}\n"
            f"FROM {self.qualified(table)}\n"
            f"WHERE `{id_column}` IN ({placeholders})"
        )
        return sql, tuple(ids)

    def resolve(
        self,
        label: NodeLabel,
        ids: Iterable[str],
        *,
        columns: Sequence[str] | None = None,
    ) -> ResolveResult:
        """按 ID 批量回取某一类节点的湖仓属性。

        ID 会去重并保持首次出现顺序；按 :data:`constants.ID_BATCH_SIZE` 分批发 SQL。

        列不存在时的退化：若指定列查询失败（湖仓那张表的实际列与
        :data:`ATTRIBUTE_COLUMNS` 不完全一致——这四张表归 catalog 各域模块所有，
        本模块只能推断），会自动退成 ``SELECT *`` 重试一次，并记 warning。
        这比直接报错更合适：血缘查询的价值在「能查到」，不在「列名完美对齐」。

        :raises LakehouseUnavailable: 通道不可用，或退化重试后仍失败
        """
        unique: list[str] = list(dict.fromkeys(i for i in ids if i))
        result = ResolveResult(label=label)
        if not unique:
            return result
        _, id_column = NODE_SOURCE_TABLES[label]
        for batch in chunked(unique, self._batch_size):
            sql, args = self.build_select(label, batch, columns=columns)
            try:
                rows = self._fetch(sql, args)
            except LakehouseUnavailable:
                if columns is None and ATTRIBUTE_COLUMNS[label]:
                    _log.warning(
                        "按推断列回取 %s 失败，退化为 SELECT * 重试（表列名可能与本模块推断不一致）",
                        label.value,
                    )
                    sql, args = self.build_select(label, batch, columns=())
                    rows = self._fetch(sql, args)
                else:
                    raise
            for row in rows:
                key = str(row.get(id_column, "")) or None
                if key is None:
                    continue
                result.attributes[key] = dict(row)
                result.sources.append(lakehouse_source_for(label, key))
        result.missing = [i for i in unique if i not in result.attributes]
        if result.missing:
            _log.info(
                "%s 有 %d 个 ID 在湖仓查无此行（图库裸占位节点或已归档）：%s",
                label.value,
                len(result.missing),
                result.missing[:5],
            )
        return result

    def resolve_refs(
        self, refs: Iterable[NodeRef]
    ) -> tuple[dict[str, dict[str, Any]], list[LakehouseSource]]:
        """按节点引用（混合标签）批量回取，自动按标签分组发 SQL。

        这是查询层的主入口——图库遍历返回的是混标签的 :class:`NodeRef` 列表。

        :returns: (``{node_id: 属性字典}``, 湖仓来源坐标列表)
        """
        grouped: dict[NodeLabel, list[str]] = {}
        for ref in refs:
            grouped.setdefault(ref.label, []).append(ref.node_id)
        attributes: dict[str, dict[str, Any]] = {}
        sources: list[LakehouseSource] = []
        for label, ids in grouped.items():
            try:
                res = self.resolve(label, ids)
            except LakehouseUnavailable as exc:
                # 属性取不到不该把整条血缘查询打死：路径本身（图库那半边）仍然有效，
                # 审计来源坐标也仍然能给出来——这正是「结果可审计」护栏的下限。
                _log.error("回取 %s 属性失败，仅返回路径与来源坐标: %s", label.value, exc)
                sources.extend(lakehouse_source_for(label, i) for i in dict.fromkeys(ids))
                continue
            attributes.update(res.attributes)
            sources.extend(res.sources)
            # 查无此行的节点也要给出来源坐标，否则审计链断在这里
            sources.extend(lakehouse_source_for(label, i) for i in res.missing)
        return attributes, sources

    def count_downstream_datasets(self, step: str, algo_version: str) -> int:
        """影响分析的「体检」：先在湖仓数个数，决定走图库还是走离线统计。

        对应护栏三的后半句：「大批量下游影响分析改走湖仓离线统计审计链路」（[a13] 六）。
        本方法只回一个数，用它跟
        :data:`constants.OFFLINE_IMPACT_FANOUT_THRESHOLD` 比较。

        SQL 用的是 artifact_refs 的字符串包含匹配——⚠️ 原文未明确 artifact_refs 的
        存储编码，本项目按 JSON 数组 / 逗号分隔两种写法都能命中的 LIKE 来数。
        精确版本见 ddl/starrocks_lineage.sql 里的 v_lineage_dataset_artifact_ref 视图。
        """
        artifact_table, _ = NODE_SOURCE_TABLES[NodeLabel.ARTIFACT]
        dataset_table, _ = NODE_SOURCE_TABLES[NodeLabel.DATASET_VERSION]
        sql = (
            "SELECT COUNT(DISTINCT d.`dataset_version_id`) AS cnt\n"
            f"FROM {self.qualified(dataset_table)} d\n"
            f"JOIN {self.qualified(artifact_table)} a\n"
            "  ON d.`artifact_refs` LIKE CONCAT('%%', a.`artifact_id`, '%%')\n"
            # 湖仓列名是 stage（catalog 口径），图库属性才叫 step
            "WHERE a.`stage` = %s AND a.`algo_version` = %s"
        )
        rows = self._fetch(sql, (step, algo_version))
        if not rows:
            return 0
        return int(next(iter(rows[0].values())) or 0)
