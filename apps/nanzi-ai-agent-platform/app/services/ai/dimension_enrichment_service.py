from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.metadata import MetaDataset, MetaRelationship, MetaTable
from app.services.config_service import ConfigService
from app.services.permission_service import PermissionService
from app.services.sql_query_execution_service import execute_sql_query_core

logger = logging.getLogger(__name__)

_ROW_KEYS = ("items", "rows", "data", "records")
_MAX_DISPLAY_COLUMNS = 2
_SAFE_IDENTIFIER_RE = re.compile(r"^[^\W\d][\w$]*(?:\.[^\W\d][\w$]*)?$", re.UNICODE)


@dataclass(frozen=True)
class DimensionRelationCandidate:
    source_column: str
    target_dataset_name: str
    target_data_source: str
    target_table_name: str
    target_key_column: str
    display_columns: list[str]
    inferred: bool = False


@dataclass
class DimensionEnrichmentResult:
    payload: dict[str, Any]
    applied: bool = False
    logs: list[str] = field(default_factory=list)


class DimensionEnrichmentService:
    """Relation-driven post-query dimension lookup for ChatBI SQL results."""

    @classmethod
    async def enrich_sql_result(
        cls,
        session: AsyncSession,
        *,
        result_payload: dict[str, Any],
        source_dataset_name: str | None,
        user_id: int | None,
        is_admin: bool,
        agent_context: Any | None = None,
        max_distinct_keys: int = 200,
    ) -> DimensionEnrichmentResult:
        payload = cls._clone_payload(result_payload)
        source_dataset_name = str(source_dataset_name or "").strip()
        if not source_dataset_name:
            return DimensionEnrichmentResult(payload=payload, logs=["缺少源数据集，跳过维度补全"])

        row_key, rows = cls._find_rows(payload)
        column_names = cls._column_names(payload)
        if not row_key or not rows or not column_names:
            return DimensionEnrichmentResult(payload=payload, logs=["SQL 结果无可补全的结构化行列"])

        column_index = {name: idx for idx, name in enumerate(column_names)}
        relation_candidates = await cls._load_relation_candidates(
            session,
            source_dataset_name=source_dataset_name,
            user_id=user_id,
            is_admin=is_admin,
        )
        candidates = [candidate for candidate in relation_candidates if candidate.source_column in column_index]
        if not candidates:
            candidates = await cls._load_inferred_candidates(
                session,
                source_dataset_name=source_dataset_name,
                result_column_names=column_names,
                user_id=user_id,
                is_admin=is_admin,
            )
        if not candidates:
            return DimensionEnrichmentResult(payload=payload, logs=["未找到有权限的跨数据集维度关系或唯一推断候选"])

        logs: list[str] = []
        applied = False
        for candidate in candidates:
            if candidate.source_column not in column_index:
                continue
            if not candidate.display_columns:
                logs.append(f"{candidate.source_column}: 目标表无合适展示字段，跳过")
                continue
            if not cls._safe_identifier(candidate.target_table_name) or not cls._safe_identifier(candidate.target_key_column):
                logs.append(f"{candidate.source_column}: 目标表或关联字段不安全，跳过")
                continue

            keys = cls._distinct_keys(
                rows,
                column_index[candidate.source_column],
                max_distinct_keys,
                source_column=candidate.source_column,
            )
            if not keys:
                continue
            lookup = await cls._query_dimension_values(
                session,
                candidate=candidate,
                keys=keys,
                user_id=user_id,
                is_admin=is_admin,
                agent_context=agent_context,
            )
            if not lookup:
                logs.append(f"{candidate.source_column}: 维度表未返回可匹配数据")
                continue

            added = cls._merge_dimension_columns(
                payload,
                row_key=row_key,
                rows=rows,
                column_names=column_names,
                source_column=candidate.source_column,
                lookup=lookup,
                display_columns=candidate.display_columns,
            )
            if added:
                applied = True
                source = "推断候选" if candidate.inferred else "relation"
                logs.append(
                    f"{candidate.source_column}: 已根据{source}从 {candidate.target_dataset_name}."
                    f"{candidate.target_table_name} 补全 {', '.join(added)}"
                )

        return DimensionEnrichmentResult(payload=payload, applied=applied, logs=logs)

    @staticmethod
    def _clone_payload(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return json.loads(json.dumps(payload, ensure_ascii=False))
        except Exception:
            return dict(payload)

    @staticmethod
    def _find_rows(payload: dict[str, Any]) -> tuple[str | None, list[Any]]:
        for key in _ROW_KEYS:
            rows = payload.get(key)
            if isinstance(rows, list):
                return key, rows
        return None, []

    @staticmethod
    def _column_names(payload: dict[str, Any]) -> list[str]:
        columns = payload.get("columns")
        if not isinstance(columns, list):
            return []
        names: list[str] = []
        for column in columns:
            if isinstance(column, dict):
                name = column.get("name") or column.get("field") or column.get("key")
            else:
                name = column
            name = str(name or "").strip()
            if name:
                names.append(name)
        return names

    @staticmethod
    def _safe_identifier(identifier: str) -> bool:
        return bool(_SAFE_IDENTIFIER_RE.match(str(identifier or "").strip()))

    @staticmethod
    def _distinct_keys(
        rows: list[Any],
        index: int,
        max_distinct_keys: int,
        *,
        source_column: str | None = None,
    ) -> list[Any]:
        seen: set[str] = set()
        keys: list[Any] = []
        for row in rows:
            if isinstance(row, dict):
                value = row.get(source_column or "")
            elif isinstance(row, list) and index < len(row):
                value = row[index]
            else:
                continue
            if value is None or value == "":
                continue
            marker = str(value)
            if marker in seen:
                continue
            seen.add(marker)
            keys.append(value)
            if len(keys) >= max_distinct_keys:
                break
        return keys

    @classmethod
    async def _load_relation_candidates(
        cls,
        session: AsyncSession,
        *,
        source_dataset_name: str,
        user_id: int | None,
        is_admin: bool,
    ) -> list[DimensionRelationCandidate]:
        dataset_result = await session.execute(
            select(MetaDataset).where(MetaDataset.name == source_dataset_name, MetaDataset.status == 1)
        )
        dataset = dataset_result.scalar_one_or_none()
        if not dataset:
            return []

        stmt = (
            select(MetaRelationship)
            .join(MetaTable, MetaRelationship.source_table_id == MetaTable.id)
            .where(MetaTable.dataset_id == dataset.id, MetaTable.status == 1)
            .options(
                selectinload(MetaRelationship.source_table).selectinload(MetaTable.columns),
                selectinload(MetaRelationship.source_table).selectinload(MetaTable.dataset),
                selectinload(MetaRelationship.target_table).selectinload(MetaTable.columns),
                selectinload(MetaRelationship.target_table).selectinload(MetaTable.dataset),
            )
        )
        rel_result = await session.execute(stmt)
        relations = rel_result.scalars().all()
        if not relations:
            return []

        permission_service = PermissionService(session)
        default_data_source = await ConfigService.get("external_sql_data_source", default="default_clickhouse")
        candidates: list[DimensionRelationCandidate] = []
        for rel in relations:
            source_table = getattr(rel, "source_table", None)
            target_table = getattr(rel, "target_table", None)
            target_dataset = getattr(target_table, "dataset", None)
            if not source_table or not target_table or not target_dataset:
                continue
            if getattr(target_table, "dataset_id", None) == dataset.id:
                continue
            if getattr(target_table, "status", 1) != 1 or getattr(target_dataset, "status", 1) != 1:
                continue
            if not is_admin:
                if user_id is None:
                    continue
                allowed = await permission_service.check_permission(
                    int(user_id),
                    "metadata",
                    str(getattr(target_dataset, "id", "")),
                )
                if not allowed:
                    continue
            join_columns = cls._extract_join_columns(
                str(getattr(rel, "join_condition", "") or ""),
                source_table=source_table,
                target_table=target_table,
            )
            if not join_columns:
                continue
            source_column, target_key_column = join_columns
            display_columns = cls._select_display_columns(getattr(target_table, "columns", []) or [], target_key_column)
            if not display_columns:
                continue
            candidates.append(
                DimensionRelationCandidate(
                    source_column=source_column,
                    target_dataset_name=str(target_dataset.name),
                    target_data_source=str(target_dataset.data_source or default_data_source),
                    target_table_name=str(target_table.physical_name),
                    target_key_column=target_key_column,
                    display_columns=display_columns,
                )
            )
        return candidates

    @classmethod
    async def _load_inferred_candidates(
        cls,
        session: AsyncSession,
        *,
        source_dataset_name: str,
        result_column_names: list[str],
        user_id: int | None,
        is_admin: bool,
    ) -> list[DimensionRelationCandidate]:
        if not is_admin and user_id is None:
            return []
        if not any(cls._looks_like_dimension_key(name) for name in result_column_names):
            return []
        stmt = select(MetaDataset).where(MetaDataset.status == 1).options(
            selectinload(MetaDataset.tables).selectinload(MetaTable.columns),
        )
        ds_result = await session.execute(stmt)
        datasets = list(ds_result.scalars().all())
        if not is_admin:
            permission_service = PermissionService(session)
            permitted = []
            for dataset in datasets:
                if str(getattr(dataset, "name", "") or "") == source_dataset_name:
                    permitted.append(dataset)
                    continue
                allowed = await permission_service.check_permission(
                    int(user_id),
                    "metadata",
                    str(getattr(dataset, "id", "")),
                )
                if allowed:
                    permitted.append(dataset)
            datasets = permitted

        default_data_source = await ConfigService.get("external_sql_data_source", default="default_clickhouse")
        return cls._infer_candidates_from_datasets(
            source_dataset_name=source_dataset_name,
            result_column_names=result_column_names,
            datasets=datasets,
            default_data_source=str(default_data_source or "default_clickhouse"),
        )

    @classmethod
    def _infer_candidates_from_datasets(
        cls,
        *,
        source_dataset_name: str,
        result_column_names: list[str],
        datasets: list[Any],
        default_data_source: str,
    ) -> list[DimensionRelationCandidate]:
        result_keys = [name for name in result_column_names if cls._looks_like_dimension_key(name)]
        candidates_by_source: dict[str, list[DimensionRelationCandidate]] = {name: [] for name in result_keys}
        for dataset in datasets:
            dataset_name = str(getattr(dataset, "name", "") or "")
            if not dataset_name or dataset_name == source_dataset_name:
                continue
            if getattr(dataset, "status", 1) != 1:
                continue
            for table in getattr(dataset, "tables", []) or []:
                if getattr(table, "status", 1) != 1:
                    continue
                table_name = str(getattr(table, "physical_name", "") or "")
                if not table_name or not cls._safe_identifier(table_name):
                    continue
                columns = list(getattr(table, "columns", []) or [])
                physical_columns = {
                    str(getattr(column, "physical_name", "") or "").lower(): str(getattr(column, "physical_name", "") or "")
                    for column in columns
                    if str(getattr(column, "physical_name", "") or "").strip()
                }
                for source_column in result_keys:
                    target_key = cls._match_target_key_column(source_column, physical_columns, table=table)
                    if not target_key:
                        continue
                    display_columns = cls._select_display_columns(columns, target_key)
                    if not display_columns:
                        continue
                    candidates_by_source[source_column].append(
                        DimensionRelationCandidate(
                            source_column=source_column,
                            target_dataset_name=dataset_name,
                            target_data_source=str(getattr(dataset, "data_source", "") or default_data_source),
                            target_table_name=table_name,
                            target_key_column=target_key,
                            display_columns=display_columns,
                            inferred=True,
                        )
                    )

        inferred: list[DimensionRelationCandidate] = []
        for source_column, candidates in candidates_by_source.items():
            if len(candidates) == 1:
                inferred.append(candidates[0])
        return inferred

    @staticmethod
    def _looks_like_dimension_key(column_name: str) -> bool:
        name = str(column_name or "").strip().lower().replace("-", "_").replace(" ", "")
        if not name:
            return False
        explicit = (
            "user_id",
            "userid",
            "yh_id",
            "yhid",
            "yhbh",
            "employee_id",
            "staff_id",
            "yg_id",
            "ygid",
            "ygbh",
            "ry_id",
            "ryid",
            "rybh",
            "dept_id",
            "department_id",
            "bm",
            "bm_id",
            "bmid",
            "bmbh",
            "bmbm",
            "org_id",
            "zz_id",
            "zzid",
            "jg_id",
            "jgid",
            "owner_id",
            "fzr_id",
            "fzrid",
            "creator_id",
            "created_by",
            "cjr_id",
            "cjrid",
            "sales_id",
            "xs_id",
            "xsry_id",
            "xsy_id",
            "用户id",
            "用户编号",
            "员工id",
            "员工编号",
            "人员id",
            "人员编号",
            "部门id",
            "部门编号",
            "部门编码",
            "组织id",
            "组织编号",
            "机构id",
            "机构编号",
            "负责人id",
            "创建人id",
            "销售id",
        )
        if name in explicit:
            return True
        return bool(re.search(r"(^|_)(user|employee|staff|dept|department|org|owner|creator|sales)_(id|code)$", name))

    @staticmethod
    def _match_target_key_column(source_column: str, physical_columns: dict[str, str], *, table: Any | None = None) -> str | None:
        source_lower = str(source_column or "").lower()
        aliases = {
            "userid": ("userid", "user_id", "id"),
            "user_id": ("user_id", "userid", "id"),
            "yh_id": ("yh_id", "yhid", "yhbh", "user_id", "userid", "id"),
            "yhid": ("yhid", "yh_id", "yhbh", "user_id", "userid", "id"),
            "yhbh": ("yhbh", "yh_id", "yhid", "user_id", "userid", "id"),
            "employee_id": ("employee_id", "emp_id", "staff_id", "user_id", "id"),
            "staff_id": ("staff_id", "employee_id", "user_id", "id"),
            "yg_id": ("yg_id", "ygid", "ygbh", "employee_id", "staff_id", "user_id", "id"),
            "ygid": ("ygid", "yg_id", "ygbh", "employee_id", "staff_id", "user_id", "id"),
            "ygbh": ("ygbh", "yg_id", "ygid", "employee_id", "staff_id", "user_id", "id"),
            "ry_id": ("ry_id", "ryid", "rybh", "employee_id", "staff_id", "user_id", "id"),
            "ryid": ("ryid", "ry_id", "rybh", "employee_id", "staff_id", "user_id", "id"),
            "rybh": ("rybh", "ry_id", "ryid", "employee_id", "staff_id", "user_id", "id"),
            "dept_id": ("dept_id", "department_id", "org_id", "id"),
            "department_id": ("department_id", "dept_id", "org_id", "id"),
            "bm": ("bm", "bm_id", "bmid", "bmbh", "bmbm", "dept_id", "department_id", "id"),
            "bm_id": ("bm_id", "bmid", "bmbh", "bmbm", "dept_id", "department_id", "id"),
            "bmid": ("bmid", "bm_id", "bmbh", "bmbm", "dept_id", "department_id", "id"),
            "bmbh": ("bmbh", "bm_id", "bmid", "bmbm", "dept_id", "department_id", "id"),
            "bmbm": ("bmbm", "bm_id", "bmid", "bmbh", "dept_id", "department_id", "id"),
            "org_id": ("org_id", "dept_id", "department_id", "id"),
            "zz_id": ("zz_id", "zzid", "org_id", "dept_id", "id"),
            "zzid": ("zzid", "zz_id", "org_id", "dept_id", "id"),
            "jg_id": ("jg_id", "jgid", "org_id", "dept_id", "id"),
            "jgid": ("jgid", "jg_id", "org_id", "dept_id", "id"),
            "owner_id": ("owner_id", "user_id", "employee_id", "id"),
            "fzr_id": ("fzr_id", "fzrid", "owner_id", "user_id", "employee_id", "id"),
            "fzrid": ("fzrid", "fzr_id", "owner_id", "user_id", "employee_id", "id"),
            "creator_id": ("creator_id", "created_by", "user_id", "employee_id", "id"),
            "created_by": ("created_by", "creator_id", "user_id", "employee_id", "id"),
            "cjr_id": ("cjr_id", "cjrid", "creator_id", "created_by", "user_id", "employee_id", "id"),
            "cjrid": ("cjrid", "cjr_id", "creator_id", "created_by", "user_id", "employee_id", "id"),
            "sales_id": ("sales_id", "sales_user_id", "user_id", "employee_id", "id"),
            "xs_id": ("xs_id", "sales_id", "sales_user_id", "user_id", "employee_id", "id"),
            "xsry_id": ("xsry_id", "xs_id", "sales_id", "sales_user_id", "user_id", "employee_id", "id"),
            "xsy_id": ("xsy_id", "xs_id", "sales_id", "sales_user_id", "user_id", "employee_id", "id"),
            "用户id": ("yh_id", "yhid", "yhbh", "user_id", "userid", "id"),
            "用户编号": ("yhbh", "yh_id", "yhid", "user_id", "userid", "id"),
            "员工id": ("yg_id", "ygid", "ygbh", "employee_id", "staff_id", "user_id", "id"),
            "员工编号": ("ygbh", "yg_id", "ygid", "employee_id", "staff_id", "user_id", "id"),
            "人员id": ("ry_id", "ryid", "rybh", "employee_id", "staff_id", "user_id", "id"),
            "人员编号": ("rybh", "ry_id", "ryid", "employee_id", "staff_id", "user_id", "id"),
            "部门id": ("bm_id", "bmid", "bmbh", "bmbm", "dept_id", "department_id", "id"),
            "部门编号": ("bmbh", "bm_id", "bmid", "bmbm", "dept_id", "department_id", "id"),
            "部门编码": ("bmbm", "bm_id", "bmid", "bmbh", "dept_id", "department_id", "id"),
            "组织id": ("zz_id", "zzid", "org_id", "dept_id", "id"),
            "组织编号": ("zzid", "zz_id", "org_id", "dept_id", "id"),
            "机构id": ("jg_id", "jgid", "org_id", "dept_id", "id"),
            "机构编号": ("jgid", "jg_id", "org_id", "dept_id", "id"),
            "负责人id": ("fzr_id", "fzrid", "owner_id", "user_id", "employee_id", "id"),
            "创建人id": ("cjr_id", "cjrid", "creator_id", "created_by", "user_id", "employee_id", "id"),
            "销售id": ("xs_id", "xsry_id", "sales_id", "sales_user_id", "user_id", "employee_id", "id"),
        }
        for candidate in (source_lower, *aliases.get(source_lower, ())):
            if candidate in physical_columns:
                if candidate == "id" and not DimensionEnrichmentService._table_matches_source_dimension(source_lower, table):
                    continue
                return physical_columns[candidate]
        return None

    @staticmethod
    def _table_matches_source_dimension(source_column: str, table: Any | None) -> bool:
        if table is None:
            return False
        text = " ".join(
            str(value or "")
            for value in (
                getattr(table, "physical_name", ""),
                getattr(table, "term", ""),
                getattr(table, "description", ""),
            )
        ).lower()
        if source_column in {"user_id", "userid", "yh_id", "yhid", "yhbh", "employee_id", "staff_id", "yg_id", "ygid", "ygbh", "ry_id", "ryid", "rybh", "owner_id", "fzr_id", "fzrid", "creator_id", "created_by", "cjr_id", "cjrid", "sales_id", "xs_id", "xsry_id", "xsy_id", "用户id", "用户编号", "员工id", "员工编号", "人员id", "人员编号", "负责人id", "创建人id", "销售id"}:
            return any(word in text for word in ("user", "employee", "staff", "person", "sales", "yg", "ry", "yh", "xsy", "用户", "员工", "人员", "销售", "负责人"))
        if source_column in {"dept_id", "department_id", "bm", "bm_id", "bmid", "bmbh", "bmbm", "org_id", "zz_id", "zzid", "jg_id", "jgid", "部门id", "部门编号", "部门编码", "组织id", "组织编号", "机构id", "机构编号"}:
            return any(word in text for word in ("dept", "department", "org", "organization", "部门", "组织", "机构"))
        return False

    @classmethod
    def _extract_join_columns(
        cls,
        join_condition: str,
        *,
        source_table: Any,
        target_table: Any,
    ) -> tuple[str, str] | None:
        for left, right in re.findall(r"([`\"\w.$]+)\s*=\s*([`\"\w.$]+)", join_condition):
            left_table, left_column = cls._split_qualified_column(left)
            right_table, right_column = cls._split_qualified_column(right)
            if not left_table or not right_table or not left_column or not right_column:
                continue
            source_name = str(getattr(source_table, "physical_name", "") or "").lower()
            target_name = str(getattr(target_table, "physical_name", "") or "").lower()
            left_table_l = left_table.lower()
            right_table_l = right_table.lower()
            if cls._table_matches(left_table_l, source_name) and cls._table_matches(right_table_l, target_name):
                return left_column, right_column
            if cls._table_matches(left_table_l, target_name) and cls._table_matches(right_table_l, source_name):
                return right_column, left_column
        return None

    @staticmethod
    def _split_qualified_column(token: str) -> tuple[str | None, str | None]:
        cleaned = token.strip().strip("`\"")
        parts = [part.strip("`\"") for part in cleaned.split(".") if part.strip("`\"")]
        if len(parts) < 2:
            return None, None
        return parts[-2], parts[-1]

    @staticmethod
    def _table_matches(parsed_table: str, physical_name: str) -> bool:
        return parsed_table == physical_name or parsed_table.endswith(f".{physical_name}")

    @classmethod
    def _select_display_columns(cls, columns: list[Any], target_key_column: str) -> list[str]:
        scored: list[tuple[int, str]] = []
        key_lower = str(target_key_column or "").lower()
        for column in columns:
            physical = str(getattr(column, "physical_name", "") or "").strip()
            if not physical or physical.lower() == key_lower:
                continue
            if not cls._safe_identifier(physical):
                continue
            term = str(getattr(column, "term", "") or "")
            desc = str(getattr(column, "description", "") or "")
            haystack = f"{physical} {term} {desc}".lower()
            score = 0
            physical_lower = physical.lower()
            if any(word in haystack for word in ("real_name", "employee_name", "staff_name", "user_name", "username")):
                score += 80
            if physical_lower in {"xm", "ygxm", "ryxm", "zsxm", "xsyxm"}:
                score += 80
            if re.search(r"(^|_)name($|_)", physical_lower) or physical_lower == "name":
                score += 70
            if any(word in haystack for word in ("dept_name", "department_name", "org_name", "dept")):
                score += 65
            if physical_lower in {"mc", "bmmc", "bm_mc", "zzmc", "zz_mc", "jgmc", "jg_mc", "dwmc", "dw_mc"}:
                score += 65
            if any(word in f"{physical} {term} {desc}" for word in ("姓名", "名称", "名字", "员工", "销售", "负责人", "部门", "组织", "机构")):
                score += 60
            if any(word in physical_lower for word in ("id", "code", "uuid", "no", "bh", "bm")) and physical_lower not in {"bmmc", "bm_mc"}:
                score -= 30
            scored.append((score, physical))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [name for score, name in scored if score > 0][:_MAX_DISPLAY_COLUMNS]

    @classmethod
    async def _query_dimension_values(
        cls,
        session: AsyncSession,
        *,
        candidate: DimensionRelationCandidate,
        keys: list[Any],
        user_id: int | None,
        is_admin: bool,
        agent_context: Any | None,
    ) -> dict[str, dict[str, Any]]:
        display_columns = [col for col in candidate.display_columns if cls._safe_identifier(col)]
        if not display_columns:
            return {}
        select_columns = [candidate.target_key_column, *display_columns]
        sql = (
            f"SELECT {', '.join(select_columns)} FROM {candidate.target_table_name} "
            f"WHERE {candidate.target_key_column} IN ({', '.join(cls._sql_literal(key) for key in keys)})"
        )
        try:
            raw = await execute_sql_query_core(
                session,
                sql=sql,
                data_source=candidate.target_data_source,
                dataset_name=candidate.target_dataset_name,
                user_id=user_id,
                user_dimensions=None,
                trace_logs=None,
                api_key=None,
                agent_context=agent_context,
                dry_run=False,
                is_admin=is_admin,
                bypass_table_auth=False,
            )
        except Exception as exc:
            logger.warning("[DimensionEnrichment] dimension query failed: %s", exc)
            return {}
        parsed = cls._parse_json(raw)
        if not isinstance(parsed, dict):
            return {}
        _, rows = cls._find_rows(parsed)
        columns = cls._column_names(parsed)
        if not rows or not columns or candidate.target_key_column not in columns:
            return {}
        key_idx = columns.index(candidate.target_key_column)
        display_indexes = {
            col: columns.index(col)
            for col in display_columns
            if col in columns
        }
        lookup: dict[str, dict[str, Any]] = {}
        for row in rows:
            if isinstance(row, dict):
                key = str(row.get(candidate.target_key_column) or "")
                if not key:
                    continue
                lookup[key] = {col: row.get(col) for col in display_columns}
            elif isinstance(row, list) and key_idx < len(row):
                key = str(row[key_idx])
                lookup[key] = {
                    col: row[idx] if idx < len(row) else None
                    for col, idx in display_indexes.items()
                }
        return lookup

    @staticmethod
    def _parse_json(raw: Any) -> Any:
        if isinstance(raw, (dict, list)):
            return raw
        if not isinstance(raw, str):
            return raw
        text = raw.strip()
        if not text or text[0] not in "{[":
            return raw
        try:
            return json.loads(text)
        except Exception:
            return raw

    @staticmethod
    def _sql_literal(value: Any) -> str:
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        return "'" + str(value).replace("'", "''") + "'"

    @classmethod
    def _merge_dimension_columns(
        cls,
        payload: dict[str, Any],
        *,
        row_key: str,
        rows: list[Any],
        column_names: list[str],
        source_column: str,
        lookup: dict[str, dict[str, Any]],
        display_columns: list[str],
    ) -> list[str]:
        if source_column not in column_names:
            return []
        source_idx = column_names.index(source_column)
        existing = set(column_names)
        added_columns = [
            f"{source_column}__{display_column}"
            for display_column in display_columns
            if f"{source_column}__{display_column}" not in existing
        ]
        if not added_columns:
            return []

        payload_columns = payload.get("columns")
        if isinstance(payload_columns, list):
            for name in added_columns:
                payload_columns.append({"name": name})

        for row in rows:
            if isinstance(row, list):
                key = str(row[source_idx]) if source_idx < len(row) else ""
                dim_values = lookup.get(key, {})
                for name in added_columns:
                    display_column = name.split("__", 1)[1]
                    row.append(dim_values.get(display_column))
            elif isinstance(row, dict):
                key = str(row.get(source_column) or "")
                dim_values = lookup.get(key, {})
                for name in added_columns:
                    display_column = name.split("__", 1)[1]
                    row[name] = dim_values.get(display_column)

        payload[row_key] = rows
        return added_columns
