import pytest

from app.services.ai.runners.chatbi.metadata_guide import (
    build_grounded_clarification_queries,
    build_metadata_guide,
)


pytestmark = pytest.mark.no_infrastructure


SCHEMA = """
--- [Schema:1] type=table dataset=sales_ds table=fact_sales score=0.91 ---
dataset: sales_ds
table_name: fact_sales
table_term: 销售明细
columns:
  - physical_name: sale_date
    term: 销售日期
    type: date
  - physical_name: region_name
    term: 区域
    type: varchar
  - physical_name: sale_amount
    term: 销售额
    type: decimal
"""


def test_metadata_guide_uses_only_authorized_schema_names():
    guide = build_metadata_guide(SCHEMA)

    assert guide["version"] == 1
    assert guide["datasets"] == ["sales_ds"]
    assert guide["tables"][0]["physical_name"] == "fact_sales"
    assert guide["metrics"][0]["physical_name"] == "sale_amount"
    assert {item["physical_name"] for item in guide["dimensions"]} == {
        "sale_date", "region_name"
    }
    serialized = str(guide)
    assert "profit_amount" not in serialized
    assert all("sales_ds" in item["query"] for item in guide["suggestions"])


def test_metadata_guide_marks_freshness_from_real_time_field():
    guide = build_metadata_guide(SCHEMA)
    assert guide["freshness"]["physical_name"] == "sale_date"
    assert "销售日期" in guide["freshness"]["label"]


def test_clarification_queries_only_use_authorized_dataset_menu():
    menu = """Available Datasets:
- Dataset: sales_ds
  Display Name: 销售主题
  Includes Tables: 销售明细, 区域
  Metrics: 销售额, 订单量
"""
    queries = build_grounded_clarification_queries(
        menu,
        ("data_object", "metric"),
    )
    assert queries
    assert all("sales_ds" in query for query in queries)
    assert all("利润" not in query for query in queries)
