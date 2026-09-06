import json
from types import SimpleNamespace

import pytest

from app.services.ai.dimension_enrichment_service import (
    DimensionEnrichmentService,
    DimensionRelationCandidate,
)


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_dimension_enrichment_uses_relation_to_add_display_columns(monkeypatch):
    candidate = DimensionRelationCandidate(
        source_column="sales_user_id",
        target_dataset_name="org_dim",
        target_data_source="mysql_hr",
        target_table_name="employee",
        target_key_column="user_id",
        display_columns=["real_name", "dept_name"],
    )
    executed = []

    async def fake_load_candidates(*args, **kwargs):
        return [candidate]

    async def fake_execute_sql_query_core(*args, **kwargs):
        executed.append(kwargs)
        return json.dumps(
            {
                "columns": [{"name": "user_id"}, {"name": "real_name"}, {"name": "dept_name"}],
                "items": [["u1", "张三", "销售一部"], ["u2", "李四", "销售二部"]],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(DimensionEnrichmentService, "_load_relation_candidates", fake_load_candidates)
    monkeypatch.setattr(
        "app.services.ai.dimension_enrichment_service.execute_sql_query_core",
        fake_execute_sql_query_core,
    )

    result = await DimensionEnrichmentService.enrich_sql_result(
        SimpleNamespace(),
        result_payload={
            "columns": [{"name": "sales_user_id"}, {"name": "visit_count"}],
            "items": [["u1", 3], ["u2", 5], ["u1", 2]],
        },
        source_dataset_name="crm",
        user_id=7,
        is_admin=False,
        agent_context=None,
    )

    assert result.applied is True
    assert executed
    assert executed[0]["dataset_name"] == "org_dim"
    assert "employee" in executed[0]["sql"]
    assert "user_id IN" in executed[0]["sql"]
    assert result.payload["columns"] == [
        {"name": "sales_user_id"},
        {"name": "visit_count"},
        {"name": "sales_user_id__real_name"},
        {"name": "sales_user_id__dept_name"},
    ]
    assert result.payload["items"] == [
        ["u1", 3, "张三", "销售一部"],
        ["u2", 5, "李四", "销售二部"],
        ["u1", 2, "张三", "销售一部"],
    ]


@pytest.mark.asyncio
async def test_dimension_enrichment_skips_when_no_relation_permission(monkeypatch):
    async def fake_load_candidates(*args, **kwargs):
        return []

    async def fake_load_inferred_candidates(*args, **kwargs):
        return []

    async def fake_execute_sql_query_core(*args, **kwargs):
        raise AssertionError("should not execute dimension lookup without candidates")

    monkeypatch.setattr(DimensionEnrichmentService, "_load_relation_candidates", fake_load_candidates)
    monkeypatch.setattr(DimensionEnrichmentService, "_load_inferred_candidates", fake_load_inferred_candidates)
    monkeypatch.setattr(
        "app.services.ai.dimension_enrichment_service.execute_sql_query_core",
        fake_execute_sql_query_core,
    )

    payload = {
        "columns": [{"name": "dept_id"}, {"name": "amount"}],
        "items": [["d1", 9]],
    }
    result = await DimensionEnrichmentService.enrich_sql_result(
        SimpleNamespace(),
        result_payload=payload,
        source_dataset_name="sales",
        user_id=7,
        is_admin=False,
        agent_context=None,
    )

    assert result.applied is False
    assert result.payload == payload


@pytest.mark.asyncio
async def test_dimension_enrichment_falls_back_to_inferred_dimension_candidate(monkeypatch):
    candidate = DimensionRelationCandidate(
        source_column="dept_id",
        target_dataset_name="org_dim",
        target_data_source="mysql_hr",
        target_table_name="department",
        target_key_column="dept_id",
        display_columns=["dept_name"],
        inferred=True,
    )
    executed = []

    async def fake_load_relation_candidates(*args, **kwargs):
        return []

    async def fake_load_inferred_candidates(*args, **kwargs):
        return [candidate]

    async def fake_execute_sql_query_core(*args, **kwargs):
        executed.append(kwargs)
        return json.dumps(
            {
                "columns": [{"name": "dept_id"}, {"name": "dept_name"}],
                "items": [["d1", "销售一部"]],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(DimensionEnrichmentService, "_load_relation_candidates", fake_load_relation_candidates)
    monkeypatch.setattr(DimensionEnrichmentService, "_load_inferred_candidates", fake_load_inferred_candidates)
    monkeypatch.setattr(
        "app.services.ai.dimension_enrichment_service.execute_sql_query_core",
        fake_execute_sql_query_core,
    )

    result = await DimensionEnrichmentService.enrich_sql_result(
        SimpleNamespace(),
        result_payload={
            "columns": [{"name": "dept_id"}, {"name": "amount"}],
            "items": [["d1", 9]],
        },
        source_dataset_name="sales",
        user_id=7,
        is_admin=False,
        agent_context=None,
    )

    assert result.applied is True
    assert executed
    assert result.payload["columns"][-1] == {"name": "dept_id__dept_name"}
    assert result.payload["items"] == [["d1", 9, "销售一部"]]
    assert any("推断" in log for log in result.logs)


def test_dimension_enrichment_infers_only_unique_dimension_candidate():
    employee_dataset = SimpleNamespace(id=2, name="hr", data_source="mysql", status=1)
    employee_table = SimpleNamespace(
        physical_name="employee",
        dataset_id=2,
        status=1,
        dataset=employee_dataset,
        columns=[
            SimpleNamespace(physical_name="user_id", term="用户ID", description=""),
            SimpleNamespace(physical_name="real_name", term="姓名", description=""),
        ],
    )
    crm_dataset = SimpleNamespace(id=1, name="crm", data_source="mysql", status=1, tables=[])
    hr_dataset = SimpleNamespace(id=2, name="hr", data_source="mysql", status=1, tables=[employee_table])

    candidates = DimensionEnrichmentService._infer_candidates_from_datasets(
        source_dataset_name="crm",
        result_column_names=["user_id", "visit_count"],
        datasets=[crm_dataset, hr_dataset],
        default_data_source="clickhouse",
    )

    assert candidates == [
        DimensionRelationCandidate(
            source_column="user_id",
            target_dataset_name="hr",
            target_data_source="mysql",
            target_table_name="employee",
            target_key_column="user_id",
            display_columns=["real_name"],
            inferred=True,
        )
    ]

    another_dataset = SimpleNamespace(id=3, name="iam", data_source="mysql", status=1)
    another_table = SimpleNamespace(
        physical_name="sys_user",
        dataset_id=3,
        status=1,
        dataset=another_dataset,
        columns=[
            SimpleNamespace(physical_name="user_id", term="用户ID", description=""),
            SimpleNamespace(physical_name="user_name", term="用户名", description=""),
        ],
    )
    another_dataset.tables = [another_table]

    assert DimensionEnrichmentService._infer_candidates_from_datasets(
        source_dataset_name="crm",
        result_column_names=["user_id", "visit_count"],
        datasets=[crm_dataset, hr_dataset, another_dataset],
        default_data_source="clickhouse",
    ) == []


def test_dimension_enrichment_does_not_match_generic_id_on_unrelated_table():
    customer_dataset = SimpleNamespace(id=2, name="customer_ds", data_source="mysql", status=1)
    customer_table = SimpleNamespace(
        physical_name="customer",
        dataset_id=2,
        status=1,
        dataset=customer_dataset,
        columns=[
            SimpleNamespace(physical_name="id", term="客户ID", description=""),
            SimpleNamespace(physical_name="name", term="客户名称", description=""),
        ],
    )
    customer_dataset.tables = [customer_table]

    assert DimensionEnrichmentService._infer_candidates_from_datasets(
        source_dataset_name="crm",
        result_column_names=["user_id", "visit_count"],
        datasets=[
            SimpleNamespace(id=1, name="crm", data_source="mysql", status=1, tables=[]),
            customer_dataset,
        ],
        default_data_source="clickhouse",
    ) == []


def test_dimension_enrichment_infers_pinyin_user_and_department_columns():
    employee_dataset = SimpleNamespace(id=2, name="hr", data_source="mysql", status=1)
    employee_table = SimpleNamespace(
        physical_name="ygxx",
        term="员工信息",
        description="人员主数据",
        dataset_id=2,
        status=1,
        dataset=employee_dataset,
        columns=[
            SimpleNamespace(physical_name="yh_id", term="用户ID", description=""),
            SimpleNamespace(physical_name="xm", term="姓名", description=""),
        ],
    )
    dept_dataset = SimpleNamespace(id=3, name="org", data_source="mysql", status=1)
    dept_table = SimpleNamespace(
        physical_name="bmxx",
        term="部门信息",
        description="组织部门维表",
        dataset_id=3,
        status=1,
        dataset=dept_dataset,
        columns=[
            SimpleNamespace(physical_name="bm_id", term="部门ID", description=""),
            SimpleNamespace(physical_name="bmmc", term="部门名称", description=""),
        ],
    )
    employee_dataset.tables = [employee_table]
    dept_dataset.tables = [dept_table]

    candidates = DimensionEnrichmentService._infer_candidates_from_datasets(
        source_dataset_name="crm",
        result_column_names=["yh_id", "bm_id", "visit_count"],
        datasets=[
            SimpleNamespace(id=1, name="crm", data_source="mysql", status=1, tables=[]),
            employee_dataset,
            dept_dataset,
        ],
        default_data_source="clickhouse",
    )

    assert candidates == [
        DimensionRelationCandidate(
            source_column="yh_id",
            target_dataset_name="hr",
            target_data_source="mysql",
            target_table_name="ygxx",
            target_key_column="yh_id",
            display_columns=["xm"],
            inferred=True,
        ),
        DimensionRelationCandidate(
            source_column="bm_id",
            target_dataset_name="org",
            target_data_source="mysql",
            target_table_name="bmxx",
            target_key_column="bm_id",
            display_columns=["bmmc"],
            inferred=True,
        ),
    ]


def test_dimension_enrichment_infers_chinese_key_alias_but_not_display_name_as_key():
    dept_dataset = SimpleNamespace(id=2, name="org", data_source="mysql", status=1)
    dept_table = SimpleNamespace(
        physical_name="department",
        term="部门信息",
        description="组织部门维表",
        dataset_id=2,
        status=1,
        dataset=dept_dataset,
        columns=[
            SimpleNamespace(physical_name="bm_id", term="部门ID", description=""),
            SimpleNamespace(physical_name="bmmc", term="部门名称", description=""),
        ],
    )
    dept_dataset.tables = [dept_table]

    candidates = DimensionEnrichmentService._infer_candidates_from_datasets(
        source_dataset_name="crm",
        result_column_names=["部门ID", "姓名", "amount"],
        datasets=[
            SimpleNamespace(id=1, name="crm", data_source="mysql", status=1, tables=[]),
            dept_dataset,
        ],
        default_data_source="clickhouse",
    )

    assert candidates == [
        DimensionRelationCandidate(
            source_column="部门ID",
            target_dataset_name="org",
            target_data_source="mysql",
            target_table_name="department",
            target_key_column="bm_id",
            display_columns=["bmmc"],
            inferred=True,
        )
    ]
    assert DimensionEnrichmentService._looks_like_dimension_key("姓名") is False
    assert DimensionEnrichmentService._looks_like_dimension_key("xm") is False


def test_dimension_enrichment_supports_chinese_physical_columns():
    dept_dataset = SimpleNamespace(id=2, name="org", data_source="mysql", status=1)
    dept_table = SimpleNamespace(
        physical_name="department",
        term="部门信息",
        description="组织部门维表",
        dataset_id=2,
        status=1,
        dataset=dept_dataset,
        columns=[
            SimpleNamespace(physical_name="部门ID", term="部门ID", description=""),
            SimpleNamespace(physical_name="部门名称", term="部门名称", description=""),
        ],
    )
    dept_dataset.tables = [dept_table]

    assert DimensionEnrichmentService._infer_candidates_from_datasets(
        source_dataset_name="crm",
        result_column_names=["部门ID", "amount"],
        datasets=[
            SimpleNamespace(id=1, name="crm", data_source="mysql", status=1, tables=[]),
            dept_dataset,
        ],
        default_data_source="clickhouse",
    ) == [
        DimensionRelationCandidate(
            source_column="部门ID",
            target_dataset_name="org",
            target_data_source="mysql",
            target_table_name="department",
            target_key_column="部门ID",
            display_columns=["部门名称"],
            inferred=True,
        )
    ]


@pytest.mark.asyncio
async def test_dimension_enrichment_does_not_scan_metadata_without_dimension_keys():
    class FakeSession:
        async def execute(self, stmt):
            raise AssertionError("should not scan metadata when result has no dimension-like keys")

    result = await DimensionEnrichmentService._load_inferred_candidates(
        FakeSession(),
        source_dataset_name="crm",
        result_column_names=["amount", "visit_count"],
        user_id=7,
        is_admin=False,
    )

    assert result == []


def test_dimension_enrichment_parses_only_qualified_join_conditions():
    source_table = SimpleNamespace(physical_name="crm_visit")
    target_table = SimpleNamespace(physical_name="employee")

    assert DimensionEnrichmentService._extract_join_columns(
        "crm_visit.sales_user_id = employee.user_id",
        source_table=source_table,
        target_table=target_table,
    ) == ("sales_user_id", "user_id")

    assert DimensionEnrichmentService._extract_join_columns(
        "sales_user_id = user_id",
        source_table=source_table,
        target_table=target_table,
    ) is None
