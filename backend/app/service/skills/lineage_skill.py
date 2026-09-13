# -*- coding: utf-8 -*-
"""Catalog-backed lineage: every node and edge must trace back to readable metadata.

This project has no graph database, no lakehouse catalog and no scheduler log
wired in, so this skill never asserts a processing pipeline.  It reports only
what the connected data source actually exposes:

* objects discovered by the semantic layer (real tables and views),
* foreign-key constraints declared in the catalog,
* table references parsed out of view definitions,
* JOIN paths the semantic layer inferred from column naming (labelled as inferred).

When none of those yield a dependency, the skill says the evidence is
insufficient instead of drawing a topology.
"""
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import sqlglot
from sqlglot import exp

from app.service.skills.base_skill import BaseSkill, SkillContext, SkillResult
from app.service.db_service import db_service
from app.service.semantic_layer import semantic_layer

logger = logging.getLogger(__name__)

# 仅识别通用数仓分层命名前缀；无法识别时诚实标记为 UNKNOWN，不猜测分层。
LAYER_PREFIXES: Tuple[Tuple[str, str], ...] = (
    ("ods_", "ODS"), ("dwd_", "DWD"), ("dim_", "DIM"), ("dws_", "DWS"), ("ads_", "ADS"),
)
SAFE_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# 血缘证据类型，按可信度从高到低排序：数据库声明 > 视图定义解析 > 语义层列名推断。
EVIDENCE_PRIORITY = {"foreign_key": 3, "view_definition": 2, "semantic_join_path": 1}
SQLGLOT_DIALECTS = {
    "postgresql": "postgres", "postgres": "postgres", "mysql": "mysql", "mariadb": "mysql",
    "sqlite": "sqlite", "duckdb": "duckdb", "clickhouse": "clickhouse", "starrocks": "starrocks",
    "doris": "doris",
}


class LineageSkill(BaseSkill):
    name: str = "lineage_skill"
    description: str = "基于数据源目录元数据（表/视图/外键）的对象依赖与指标口径追溯技能"

    TRIGGER_KEYWORDS = [
        "血缘", "溯源", "上游", "下游", "链路", "来源", "从哪来", "如何加工",
        "依赖", "流转", "data_id", "三级id", "闭环链路", "拓扑"
    ]

    # 系统实际具备的血缘证据来源，用于向用户如实说明能力边界。
    CAPABILITY_NOTE = (
        "说明：本系统未接入图数据库或湖仓血缘引擎，也未采集调度作业与 SQL 执行日志，"
        "因此仅能给出目录级（表/视图/外键约束）依赖与语义层登记的指标口径，"
        "不含字段级加工链路、任务级流转与采集片段追溯。"
    )

    @property
    def lineage_graph(self) -> Dict[str, Any]:
        """Current catalog-backed graph. Rebuilt per read so it never goes stale."""
        return self.build_graph()

    # ------------------------------------------------------------------
    # 目录元数据读取
    # ------------------------------------------------------------------
    @staticmethod
    def _sqlglot_dialect() -> Optional[str]:
        engine = getattr(db_service, "real_engine", None)
        if engine is not None:
            name = str(getattr(getattr(engine, "dialect", None), "name", "")).lower()
        else:
            name = "sqlite"
        return SQLGLOT_DIALECTS.get(name)

    def _read_catalog(self, table_names: List[str]):
        """Read object type/schema, FK constraints and view definitions. Never raises."""
        info: Dict[str, Dict[str, Any]] = {
            name: {"schema": None, "type": "unknown"} for name in table_names
        }
        foreign_keys: List[Tuple[str, str, str]] = []  # (parent, child, relation)
        views: Dict[str, str] = {}
        try:
            if getattr(db_service, "real_engine", None) is not None:
                self._read_engine_catalog(info, foreign_keys, views)
            elif getattr(db_service, "conn", None) is not None:
                self._read_sqlite_catalog(info, foreign_keys, views)
        except Exception:
            logger.exception("[LineageSkill] 读取数据源目录元数据失败，将仅使用语义层已发现的信息")
        return info, foreign_keys, views

    def _read_engine_catalog(self, info, foreign_keys, views) -> None:
        from sqlalchemy import inspect

        inspector = inspect(db_service.real_engine)
        # 与语义层一致的 schema 解析顺序：先命中的 schema 才是无限定名实际读到的对象。
        schemas = list(getattr(db_service, "query_schemas", None) or [None])
        layout = []
        for schema in schemas:
            view_names = set(self._safe(inspector.get_view_names, schema=schema) or [])
            table_names = list(self._safe(inspector.get_table_names, schema=schema) or [])
            layout.append((schema, table_names, view_names))
            for name in table_names + sorted(view_names):
                entry = info.get(name)
                if entry is None or entry["type"] != "unknown":
                    continue
                entry["schema"] = schema
                entry["type"] = "view" if name in view_names else "table"

        # 依赖关系在对象归属确定之后再读，避免把同名对象错连到另一个 schema 上。
        for schema, table_names, view_names in layout:
            for name in sorted(view_names):
                entry = info.get(name)
                if entry is None or entry["schema"] != schema or name in views:
                    continue
                definition = self._safe(inspector.get_view_definition, name, schema=schema)
                if definition:
                    views[name] = definition

            constraints = self._safe(getattr(inspector, "get_multi_foreign_keys", None), schema=schema)
            if constraints is None:
                constraints = {}
                for name in table_names:
                    if name in info:
                        constraints[name] = self._safe(inspector.get_foreign_keys, name, schema=schema) or []
            for key, entries in constraints.items():
                child = key[-1] if isinstance(key, tuple) else key
                if child in info and info[child]["schema"] == schema:
                    self._collect_foreign_keys(child, entries, info, foreign_keys)

    def _read_sqlite_catalog(self, info, foreign_keys, views) -> None:
        cursor = db_service.conn.cursor()
        cursor.execute("SELECT name, type, sql FROM sqlite_master WHERE type IN ('table', 'view')")
        for name, obj_type, ddl in cursor.fetchall():
            entry = info.get(name)
            if entry is None:
                continue
            entry["schema"] = "main"
            entry["type"] = "view" if obj_type == "view" else "table"
            if obj_type == "view" and ddl:
                views[name] = ddl
        for name, entry in info.items():
            # PRAGMA 不支持参数绑定，因此只对合法标识符执行，杜绝拼接注入。
            if entry["type"] != "table" or not SAFE_IDENTIFIER.fullmatch(name):
                continue
            rows = self._safe(cursor.execute, f"PRAGMA foreign_key_list({name})")
            if rows is None:
                continue
            entries = [{"referred_table": row[2], "constrained_columns": [row[3]],
                        "referred_columns": [row[4]]} for row in rows.fetchall()]
            self._collect_foreign_keys(name, entries, info, foreign_keys)

    @staticmethod
    def _collect_foreign_keys(child, entries, info, foreign_keys) -> None:
        if child not in info:
            return
        for entry in entries or []:
            parent = entry.get("referred_table")
            # 端点必须都是本数据源已发现的对象，否则不画这条边。
            if not parent or parent not in info or parent == child:
                continue
            # 外键显式指向别的 schema 时，不能认定它就是这里发现的同名对象。
            referred_schema = entry.get("referred_schema")
            if referred_schema is not None and info[parent]["schema"] != referred_schema:
                continue
            child_cols = ", ".join(c for c in (entry.get("constrained_columns") or []) if c)
            parent_cols = ", ".join(c for c in (entry.get("referred_columns") or []) if c)
            condition = (f"{child}.{child_cols} = {parent}.{parent_cols}"
                         if child_cols and parent_cols else f"{child} -> {parent}")
            foreign_keys.append((parent, child, f"数据库外键约束: {condition}"))

    @staticmethod
    def _safe(func, *args, **kwargs):
        """Best-effort catalog call: a dialect that cannot answer must not break lineage."""
        if func is None:
            return None
        try:
            return func(*args, **kwargs)
        except Exception:
            logger.debug("[LineageSkill] 目录元数据调用失败: %s", getattr(func, "__name__", func))
            return None

    def _view_dependencies(self, view_name: str, definition: str, known: Dict[str, str]):
        """Tables a view really selects from. Returns (resolved_names, unresolved_count)."""
        tree = None
        # 先按数据源方言解析，失败再退回通用方言，两者都失败就不画这条边。
        for read in dict.fromkeys((self._sqlglot_dialect(), None)):
            try:
                tree = sqlglot.parse_one(definition, read=read) if read else sqlglot.parse_one(definition)
                break
            except Exception:
                continue
        if tree is None:
            logger.debug("[LineageSkill] 视图定义无法解析，跳过: %s", view_name)
            return [], 0
        local = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE) if cte.alias_or_name}
        resolved, unresolved = [], 0
        for table in tree.find_all(exp.Table):
            name = (table.name or "").lower()
            if not name or name in local or name == view_name.lower():
                continue
            actual = known.get(name)
            if actual is None:
                unresolved += 1
            elif actual != view_name and actual not in resolved:
                resolved.append(actual)
        return resolved, unresolved

    # ------------------------------------------------------------------
    # 图构建
    # ------------------------------------------------------------------
    @staticmethod
    def _layer_of(table_name: str) -> Tuple[str, str]:
        lowered = table_name.lower()
        for prefix, layer in LAYER_PREFIXES:
            if lowered.startswith(prefix):
                return layer, "表名前缀命名约定推断"
        return "UNKNOWN", "无分层元数据，未推断"

    def build_graph(self) -> Dict[str, Any]:
        """Build the lineage graph from catalog metadata only; fabricates nothing."""
        columns = dict(getattr(semantic_layer, "discovered_table_columns", None) or {})
        table_names = sorted(columns)
        info, foreign_keys, views = self._read_catalog(table_names)
        known = {name.lower(): name for name in table_names}
        node_ids = set(table_names)

        nodes = []
        for name in table_names:
            entry = info.get(name, {"schema": None, "type": "unknown"})
            layer, layer_evidence = self._layer_of(name)
            nodes.append({
                "id": name,
                "name": name,
                "layer": layer,
                "type": entry["type"],
                "domain": entry["schema"] or "未标注",
                "schema": entry["schema"],
                "column_count": len(columns.get(name) or []),
                "layer_evidence": layer_evidence,
                "evidence": "数据源目录元数据（表结构发现）",
            })

        # 边方向统一为 上游 -> 下游（被引用方 -> 引用方）。
        edges: Dict[Tuple[str, str], Dict[str, Any]] = {}
        unresolved_view_refs = 0

        def add_edge(source, target, relation, evidence):
            if source not in node_ids or target not in node_ids or source == target:
                return
            key = (source, target)
            current = edges.get(key)
            if current and EVIDENCE_PRIORITY[current["evidence"]] >= EVIDENCE_PRIORITY[evidence]:
                current.setdefault("also_evidenced_by", [])
                if evidence not in current["also_evidenced_by"]:
                    current["also_evidenced_by"].append(evidence)
                return
            merged = list(current.get("also_evidenced_by", [])) if current else []
            if current and current["evidence"] not in merged:
                merged.append(current["evidence"])
            edges[key] = {"source": source, "target": target, "relation": relation,
                          "evidence": evidence, "also_evidenced_by": merged}

        for parent, child, relation in foreign_keys:
            add_edge(parent, child, relation, "foreign_key")

        for view_name, definition in views.items():
            if view_name not in node_ids:
                continue
            upstreams, unresolved = self._view_dependencies(view_name, definition, known)
            unresolved_view_refs += unresolved
            for upstream in upstreams:
                add_edge(upstream, view_name, f"视图定义引用: {view_name} SELECT FROM {upstream}",
                         "view_definition")

        for path in getattr(semantic_layer, "join_paths", None) or []:
            add_edge(path.to_table, path.from_table,
                     f"语义层按列名推断的 {path.join_type} JOIN（非数据库声明）: {path.condition}",
                     "semantic_join_path")

        edge_list = sorted(edges.values(), key=lambda e: (e["source"], e["target"]))
        counts = {evidence: 0 for evidence in EVIDENCE_PRIORITY}
        for edge in edge_list:
            counts[edge["evidence"]] += 1
        return {
            "nodes": nodes,
            "edges": edge_list,
            "stats": {
                "node_count": len(nodes),
                "edge_count": len(edge_list),
                "foreign_key_edges": counts["foreign_key"],
                "view_definition_edges": counts["view_definition"],
                "inferred_join_edges": counts["semantic_join_path"],
                "unresolved_view_references": unresolved_view_refs,
                # 本系统没有图血缘引擎，此处如实置空，不得填写未部署的组件名。
                "graph_engine": None,
                "evidence_sources": [
                    "SQLAlchemy Inspector 目录元数据" if getattr(db_service, "real_engine", None) is not None
                    else "sqlite_master / PRAGMA 目录元数据",
                    "语义层按列名推断的 JOIN 路径",
                ],
            },
        }

    # ------------------------------------------------------------------
    # 技能路由与执行
    # ------------------------------------------------------------------
    def can_handle(self, ctx: SkillContext) -> Tuple[bool, float]:
        q = ctx.rewritten_question or ctx.question
        q_lower = q.lower()
        # “按来源统计文章” asks for a business grouping, not data lineage.
        explicit_lineage = any(kw in q_lower for kw in self.TRIGGER_KEYWORDS if kw != "来源")
        source_grouping = any(term in q_lower for term in ("按来源", "各来源", "每个来源", "来源平台", "source_platform"))
        matched = explicit_lineage or ("来源" in q_lower and not source_grouping)
        if matched:
            return True, 0.95
        return False, 0.0

    def _resolve_focus(self, question: str, node_ids):
        """Resolve the object asked about, or (None, None) when the question is unspecific."""
        for table in semantic_layer.mentioned_tables(question):
            if table in node_ids:
                return table, None

        def best_match(candidates):
            best_len, matches = 0, []
            for terms, table, item in candidates:
                lengths = [len(term) for term in terms
                           if term and len(term) >= 2 and semantic_layer.mentions_term(question, term)]
                if not lengths:
                    continue
                longest = max(lengths)
                if longest > best_len:
                    best_len, matches = longest, [(table, item)]
                elif longest == best_len:
                    matches.append((table, item))
            # 命中长度相同但指向不同表时无法判定归属，宁可不给焦点。
            if matches and len({table for table, _ in matches}) == 1:
                return matches[0]
            return None, None

        metrics = [([metric.name, *metric.aliases], metric.source_table, metric)
                   for metric in getattr(semantic_layer, "metrics", {}).values()
                   if metric.source_table in node_ids]
        table, metric = best_match(metrics)
        if table:
            return table, metric

        dimensions = [([dim.name, *dim.aliases], dim.source_table, None)
                      for dim in getattr(semantic_layer, "table_dimensions", {}).values()
                      if dim.source_table in node_ids]
        table, _ = best_match(dimensions)
        return (table, None) if table else (None, None)

    def execute(self, ctx: SkillContext) -> SkillResult:
        started = time.perf_counter()
        question = ctx.rewritten_question or ctx.question
        logger.info("[LineageSkill] 基于目录元数据执行血缘追溯: '%s'", question)

        demo = bool(getattr(db_service, "is_sample_data", False))
        engine_name = str(getattr(db_service, "active_db_type", "") or "unknown")
        details: Dict[str, Any] = {
            "sql": "",  # 本技能读取目录元数据，不执行用户查询 SQL。
            "dialect": ctx.dialect,
            "tables": [],
            "filters": [],
            "estimated_rows": 0,
            "data_source": "demo" if getattr(db_service, "real_engine", None) is None else "configured",
            "source_desc": ("项目示例数据；" if demo else "")
                           + f"{engine_name} 数据源目录元数据（表/视图/外键约束）与语义层登记的 JOIN 路径",
        }

        def failure(message: str) -> SkillResult:
            details["elapsed_time"] = f"{time.perf_counter() - started:.3f}s"
            details["source_desc"] = "数据不足，未构建血缘图"
            return SkillResult(
                success=False, skill_type="lineage", error=message, data=[], details=details,
                clarification={"need_clarification": True, "message": message, "options": []},
            )

        try:
            graph = self.build_graph()
        except Exception:
            logger.exception("[LineageSkill] 构建血缘图失败")
            return failure("【数据不足】读取数据源目录元数据失败，无法构建数据血缘。请检查数据源连接与元数据读取权限后重试。")

        nodes, edges, stats = graph["nodes"], graph["edges"], graph["stats"]
        if not nodes:
            return failure(
                "【数据不足】当前数据源尚未发现任何物理表或视图，无法构建数据血缘。"
                "请检查数据源连接、表访问权限并同步表结构后重试。"
            )

        node_ids = {node["id"] for node in nodes}
        focus, metric = self._resolve_focus(question, node_ids)
        upstream = sorted({e["source"] for e in edges if e["target"] == focus}) if focus else []
        downstream = sorted({e["target"] for e in edges if e["source"] == focus}) if focus else []

        lines = []
        if edges:
            lines.append(
                f"【数据血缘】已从当前数据源的目录元数据构建依赖视图：{stats['node_count']} 个对象、"
                f"{stats['edge_count']} 条依赖关系。"
            )
        else:
            lines.append(
                f"【数据不足】当前数据源已发现 {stats['node_count']} 个对象，但未读到任何可证实的血缘关系"
                "（无外键约束、无视图依赖、无可推断的 JOIN 路径），下表仅为对象清单，不构成加工链路。"
            )
        lines.append(
            f"证据来源：数据库外键约束 {stats['foreign_key_edges']} 条、视图定义解析 {stats['view_definition_edges']} 条、"
            f"语义层按列名推断的 JOIN 路径 {stats['inferred_join_edges']} 条（推断项非数据库声明，仅供参考）。"
        )
        if focus:
            lines.append(
                f"焦点对象「{focus}」：直接上游 {'、'.join(upstream) if upstream else '未发现'}；"
                f"直接下游 {'、'.join(downstream) if downstream else '未发现'}。"
            )
        else:
            lines.append("未能从提问中确定具体的表或指标，以上为当前数据源的整体依赖视图；可补充表名或指标名以聚焦查看。")
        if metric is not None:
            lines.append(
                f"指标「{metric.name}」的登记口径：{metric.default_agg}({metric.calculation})，"
                f"来源表 {metric.source_table}（语义层登记，非执行日志实测）。"
            )
        if stats["unresolved_view_references"]:
            lines.append(
                f"另有 {stats['unresolved_view_references']} 处视图引用的对象不在当前扫描范围内，已略去未画入图中。"
            )
        lines.append(self.CAPABILITY_NOTE)
        conclusion = "\n".join(lines)

        table_records = [{
            "table_name": node["id"],
            "layer": node["layer"],
            "description": f"{node['type']}，{node['column_count']} 列",
            "domain": node["domain"],
        } for node in nodes]

        details["tables"] = [node["id"] for node in nodes]
        details["estimated_rows"] = len(table_records)
        details["elapsed_time"] = f"{time.perf_counter() - started:.3f}s"

        return SkillResult(
            success=True,
            skill_type="lineage",
            conclusion=conclusion,
            chart={"type": "table", "title": "数据源对象与依赖关系", "config": {}},
            data=table_records,
            column_types={"table_name": "string", "layer": "string", "description": "string", "domain": "string"},
            lineage_data=graph,
            details=details,
        )


# 单例导出
lineage_skill = LineageSkill()
