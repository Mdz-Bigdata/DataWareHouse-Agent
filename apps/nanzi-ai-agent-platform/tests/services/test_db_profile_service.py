import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.db_connection import DbProfileTask
from app.services.db_profile_service import (
    DbProfileService,
    TASK_STATUS_RUNNING,
    TASK_STATUS_DONE,
    TASK_STATUS_CANCELLED,
    STALE_TASK_MINUTES,
)


def test_extract_result_rows_prefers_rows_then_items():
    assert DbProfileService._extract_result_rows({"rows": [[1]], "items": [[2]]}) == [[1]]
    assert DbProfileService._extract_result_rows({"items": [[2]]}) == [[2]]
    assert DbProfileService._extract_result_rows({}) == []


def test_extract_column_names_from_dict_columns():
    cols = [{"name": "ID", "type": "NUMBER"}, {"name": "NAME", "type": "VARCHAR2"}]
    assert DbProfileService._extract_column_names(cols) == ["ID", "NAME"]


def test_sanitize_sample_value_truncates_long_strings():
    long_text = "x" * 200
    result = DbProfileService._sanitize_sample_value(long_text)
    assert result.endswith("...")
    assert len(result) == 153


def test_truncate_ddl():
    ddl = "A" * 70000
    truncated = DbProfileService._truncate_ddl(ddl)
    assert len(truncated) < len(ddl)
    assert "truncated" in truncated


def test_post_process_scores_penalizes_empty_samples():
    score, temp, reason, ignored = DbProfileService._post_process_scores(
        {"confidence_score": 90, "is_temporary": False, "confidence_reason": "ok"},
        "[]",
        "orders",
    )
    assert score == 60
    assert "样例数据为空" in reason


def test_post_process_scores_penalizes_temp_table_names():
    score, temp, reason, ignored = DbProfileService._post_process_scores(
        {"confidence_score": 90, "is_temporary": False, "confidence_reason": ""},
        json.dumps([{"id": 1}]),
        "tmp_orders",
    )
    assert temp == 1
    assert score == 50
    assert ignored == 1


def test_is_task_stale():
    fresh = DbProfileTask(
        connection_id=1,
        status=TASK_STATUS_RUNNING,
        total_tables=10,
        processed_tables=1,
        updated_at=datetime.now(),
    )
    stale = DbProfileTask(
        connection_id=1,
        status=TASK_STATUS_RUNNING,
        total_tables=10,
        processed_tables=1,
        updated_at=datetime.now() - timedelta(minutes=STALE_TASK_MINUTES + 1),
    )
    assert DbProfileService._is_task_stale(fresh) is False
    assert DbProfileService._is_task_stale(stale) is True


@pytest.mark.asyncio
async def test_fetch_sample_data_builds_json():
    adapter = AsyncMock()
    adapter.execute_sql.return_value = {
        "columns": [{"name": "ID", "type": "int"}],
        "items": [[1], [2]],
    }
    result = await DbProfileService._fetch_sample_data(adapter, "mysql", "orders")
    parsed = json.loads(result)
    assert len(parsed) == 2
    assert parsed[0]["ID"] == 1


@pytest.mark.asyncio
async def test_should_stop_when_task_cancelled():
    cancelled_task = MagicMock()
    cancelled_task.status = TASK_STATUS_CANCELLED

    with patch.object(DbProfileService, "get_task_status", AsyncMock(return_value=cancelled_task)):
        assert await DbProfileService._should_stop_profiling(1) is True


def test_parse_column_types_from_ddl():
    ddl = """
    CREATE TABLE orders (
      id INT PRIMARY KEY,
      `user_name` VARCHAR(100),
      created_at DATETIME
    );
    """
    types = DbProfileService._parse_column_types_from_ddl(ddl)
    assert types["id"] == "INT"
    assert types["user_name"] == "VARCHAR(100)"
    assert types["created_at"] == "DATETIME"


def test_profile_to_import_table_maps_columns():
    profile = MagicMock()
    profile.table_name = "orders"
    profile.ai_term = "订单表"
    profile.ai_description = "存储订单"
    profile.ai_tags = ["交易"]
    profile.ddl = "CREATE TABLE orders (id INT, amount DECIMAL(10,2));"
    profile.columns_profile = [
        {"name": "id", "term": "订单ID", "desc": "主键"},
        {"name": "amount", "term": "金额", "desc": "订单金额"},
    ]

    table = DbProfileService._profile_to_import_table(profile)
    assert table["physical_name"] == "orders"
    assert table["term"] == "订单表"
    assert len(table["columns"]) == 2
    assert table["columns"][0]["type"] == "INT"
    assert table["columns"][1]["type"] == "DECIMAL(10,2)"


def test_normalize_page_clamps_values():
    page, size = DbProfileService._normalize_page(0, 9999)
    assert page == 1
    assert size == 200
    page, size = DbProfileService._normalize_page(3, 50)
    assert page == 3
    assert size == 50


@pytest.mark.asyncio
async def test_reconcile_marks_done_when_all_profiles_finished():
    task = DbProfileTask(
        connection_id=1,
        status=TASK_STATUS_RUNNING,
        total_tables=4816,
        processed_tables=4815,
        current_table="last_table",
        updated_at=datetime.now(),
    )
    db = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    with patch.object(DbProfileService, "get_task_status", AsyncMock(return_value=task)), patch.object(
        DbProfileService, "_get_profile_status_counts", AsyncMock(return_value={2: 4816})
    ):
        result = await DbProfileService.reconcile_profiling_task_status(db, 1, commit=True)

    assert result.status == TASK_STATUS_DONE
    assert result.processed_tables == 4816
    assert result.current_table is None
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconcile_keeps_running_when_pending_tables_remain():
    task = DbProfileTask(
        connection_id=1,
        status=TASK_STATUS_RUNNING,
        total_tables=100,
        processed_tables=10,
        updated_at=datetime.now(),
    )
    db = AsyncMock()
    db.commit = AsyncMock()

    with patch.object(DbProfileService, "get_task_status", AsyncMock(return_value=task)), patch.object(
        DbProfileService,
        "_get_profile_status_counts",
        AsyncMock(return_value={0: 90, 2: 10}),
    ):
        result = await DbProfileService.reconcile_profiling_task_status(db, 1, commit=True)

    assert result.status == TASK_STATUS_RUNNING
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_resets_stale_running_tables():
    task = DbProfileTask(
        connection_id=1,
        status=TASK_STATUS_RUNNING,
        total_tables=10,
        processed_tables=9,
        updated_at=datetime.now() - timedelta(minutes=STALE_TASK_MINUTES + 1),
    )
    db = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.execute = AsyncMock()
    db.flush = AsyncMock()

    with patch.object(DbProfileService, "get_task_status", AsyncMock(return_value=task)), patch.object(
        DbProfileService,
        "_get_profile_status_counts",
        AsyncMock(return_value={1: 1, 2: 9}),
    ):
        result = await DbProfileService.reconcile_profiling_task_status(db, 1, commit=True)

    assert result.status == TASK_STATUS_RUNNING
    db.execute.assert_awaited_once()
    db.commit.assert_not_awaited()


def test_apply_profile_order_defaults_to_table_name_asc():
    from sqlalchemy import select
    from app.models.db_connection import DbTableProfile

    stmt = DbProfileService._apply_profile_order(select(DbTableProfile))
    order_sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "table_name" in order_sql.lower()
    assert "asc" in order_sql.lower()


def test_apply_profile_order_supports_confidence_desc():
    from sqlalchemy import select
    from app.models.db_connection import DbTableProfile

    stmt = DbProfileService._apply_profile_order(
        select(DbTableProfile), sort_by="confidence_score", sort_order="desc"
    )
    order_sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "confidence_score" in order_sql.lower()
    assert "desc" in order_sql.lower()
