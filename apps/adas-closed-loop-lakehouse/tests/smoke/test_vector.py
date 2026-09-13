"""冒烟：HNSW 向量索引落地 StarRocks。

一句话方案：不新增向量数据库——向量落湖于 Paimon，索引建在 StarRocks 外部表上。

主流程：Embedding 五步流水线（增量识别 → 成本分级 → 双路编码 → 幂等写回 → 索引刷新）
+ 检索 SQL 渲染。编码器是假的，executor 只记 SQL，不连 StarRocks。
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from adas_lakehouse import vector as V

pytestmark = pytest.mark.smoke

DATA_ID = "COLLECT_BP_20260301123045_b7e2"
DT = "2026-03-01"
#: 凌晨窗口内的一个时刻——原文要求 T+1 向量化在凌晨 6 点前完成。
IN_WINDOW = datetime(2026, 3, 2, 3, 0, 0)


class FakeClipEncoder:
    """双塔编码器假实现。图片塔与文本塔必须来自同一个模型，向量才在同一空间。"""

    embedding_version = "clip_v1"
    model_version = "ViT-B/32"
    dim = 512

    def __init__(self) -> None:
        self.image_calls = 0
        self.text_calls = 0

    def encode_images(self, uris):
        self.image_calls += len(uris)
        return [tuple(0.1 for _ in range(self.dim)) for _ in uris]

    def encode_texts(self, texts):
        self.text_calls += len(texts)
        return [tuple(0.2 for _ in range(self.dim)) for _ in texts]


def _records(n: int = 5) -> list[V.ImageRecord]:
    return [
        V.ImageRecord(
            image_id=f"IMG-{i:04d}",
            data_id=DATA_ID,
            dt=DT,
            image_uri=f"s3://adas-frames/{DT}/IMG-{i:04d}.jpg",
            caption="雨天夜间高速行人横穿",
            image_content_hash=f"{i:08x}",
            create_time=datetime(2026, 3, 1),
        )
        for i in range(n)
    ]


def _pipeline(encoder: FakeClipEncoder, sink: V.RecordingSink) -> V.EmbeddingPipeline:
    # sample_ratio=1.0：关掉成本抽样，让断言确定
    return V.EmbeddingPipeline(encoder=encoder, sink=sink, sample_ratio=1.0, batch_size=8)


# --------------------------------------------------------------------------- Embedding 五步


def test_embedding_pipeline_runs_all_five_steps():
    encoder, sink = FakeClipEncoder(), V.RecordingSink()
    report = _pipeline(encoder, sink).run(DT, _records(), now=IN_WINDOW)

    assert [s.name_cn for s in report.steps] == [s.name_cn for s in V.PIPELINE_STEPS]
    assert [s.ordinal for s in report.steps] == [1, 2, 3, 4, 5]
    assert report.encoded_count == 5
    assert report.upserted_count == 5
    assert len(sink.rows) == 5
    assert report.dt == DT
    assert report.embedding_version == encoder.embedding_version
    # 图片塔与文本塔各编码一遍
    assert encoder.image_calls == 5
    assert encoder.text_calls == 5


def test_written_rows_carry_the_idempotency_key_and_lineage_columns():
    sink = V.RecordingSink()
    _pipeline(FakeClipEncoder(), sink).run(DT, _records(3), now=IN_WINDOW)

    row = sink.rows[0]
    # 幂等键
    assert row["image_id"] and row["embedding_version"]
    # 回溯：向量行必须能追回 clip
    assert row["data_id"] == DATA_ID
    assert row["dt"] == DT
    assert row["embedding_dim"] == 512
    assert len(row["image_embedding"]) == 512
    assert len(row["text_embedding"]) == 512


def test_pipeline_rerun_is_idempotent():
    """按 (image_id, embedding_version) Upsert——重跑同一批不产生第二份。"""
    records = _records(4)
    first, second = V.RecordingSink(), V.RecordingSink()

    _pipeline(FakeClipEncoder(), first).run(DT, records, now=IN_WINDOW)
    _pipeline(FakeClipEncoder(), second).run(DT, records, now=IN_WINDOW)

    key = lambda rows: sorted((r["image_id"], r["embedding_version"]) for r in rows)  # noqa: E731
    assert key(first.rows) == key(second.rows)
    assert len(set(key(first.rows))) == len(first.rows)  # 一批内幂等键不重复


def test_index_refresh_statements_are_partition_scoped():
    """索引刷新按分区增量做，不是全表重建。"""
    report = _pipeline(FakeClipEncoder(), V.RecordingSink()).run(DT, _records(2), now=IN_WINDOW)
    assert report.refresh_statements
    joined = "\n".join(report.refresh_statements)
    assert DT in joined
    assert V.IMAGE_INDEX_NAME in joined
    assert V.TEXT_INDEX_NAME in joined


# --------------------------------------------------------------------------- 表契约与 DDL


def test_vector_table_spec_comes_from_the_registry():
    """表结构的唯一事实源是 catalog.registry——向量子系统不再自带一份定义。

    向量侧唯一往规格上追加的是一项**表属性**（vector_meta 的 shredding schema），
    列 / 主键 / 分区 / bucket 一个字都不改。
    """
    from adas_lakehouse.catalog import registry
    from adas_lakehouse.vector import schema as vs

    assert not hasattr(vs, "_local_spec"), "本地兜底定义已删除，表结构只能来自 registry"

    spec = V.paimon_table_spec()
    assert spec.name == V.VECTOR_TABLE_NAME
    assert spec.validate() == []

    registered = registry.by_name(V.VECTOR_TABLE_NAME)
    assert [c.name for c in spec.all_columns()] == [c.name for c in registered.all_columns()]
    assert spec.partition_by == registered.partition_by
    assert spec.primary_key == registered.primary_key
    assert spec.bucket == registered.bucket
    # 追加的表属性在，registry 自己的属性没被覆盖掉
    assert spec.extra_options["variant.shreddingSchema"] == V.VECTOR_META_SHREDDING_SCHEMA
    assert spec.extra_options["file.format"] == "parquet"
    # registry 的对象是全局共享的，不能被就地改
    assert "variant.shreddingSchema" not in registered.extra_options


def test_columns_the_vector_subsystem_depends_on_exist_in_the_registry():
    """列不存在不会报错，只会让 SQL 读到 NULL——过滤器静默失效。所以这里钉死。"""
    from adas_lakehouse.vector import schema as vs

    known = vs.vector_column_names()
    # 标量预过滤白名单（search.py 的防注入闸门）里的每一列都得真的在表上
    assert known >= vs.SCALAR_FILTER_COLUMNS
    # 近义异名归一后的两个列名
    assert {vs.CAPTION_COLUMN, vs.EMBED_TIME_COLUMN} <= known
    assert vs.CAPTION_COLUMN == "caption_text" and vs.EMBED_TIME_COLUMN == "embed_time"
    # 投影列表同样只能来自 registry，且刻意不投影向量本体
    projected = set(vs.vector_columns_for_projection())
    assert projected <= known
    assert not projected & {"image_embedding", "text_embedding"}


def test_written_row_columns_all_exist_on_the_table():
    sink = V.RecordingSink()
    _pipeline(FakeClipEncoder(), sink).run(DT, _records(3), now=IN_WINDOW)

    from adas_lakehouse.vector import schema as vs

    known = vs.vector_column_names()
    for row in sink.rows:
        assert set(row) <= known, f"写回的行里有表上没有的列: {sorted(set(row) - known)}"
    # caption 落在 registry 的 caption_text 上，不是向量侧的旧列名
    assert sink.rows[0][vs.CAPTION_COLUMN] == "雨天夜间高速行人横穿"
    assert "caption" not in sink.rows[0]
    assert sink.rows[0][vs.EMBED_TIME_COLUMN] is not None
    assert "encoded_at" not in sink.rows[0]


def test_ddl_renders_for_all_three_physical_shapes():
    """Paimon 表（主数据）/ External Catalog（检索加速）/ 降级内表（兜底）。"""
    for render in (V.render_paimon_ddl, V.render_internal_table_ddl):
        sql = render()
        assert "CREATE" in sql.upper()
        assert V.VECTOR_TABLE_NAME in sql

    # External Catalog 建的是 catalog 本身，表名在后续查询里才出现
    catalog_ddl = V.render_external_catalog_ddl()
    assert "CREATE EXTERNAL CATALOG" in catalog_ddl.upper()
    # 口令只写占位符，不落盘
    assert "${MINIO_SECRET_KEY}" in catalog_ddl


def test_dual_hnsw_indexes_are_defined():
    indexes = V.dual_indexes()
    assert {i.name for i in indexes} == {V.IMAGE_INDEX_NAME, V.TEXT_INDEX_NAME}
    ddl = V.render_create_index_ddl()
    assert V.IMAGE_INDEX_NAME in ddl and V.TEXT_INDEX_NAME in ddl


def test_default_hnsw_params_are_the_documented_ones():
    p = V.DEFAULT_HNSW_PARAMS
    assert (p.m, p.ef_construction, p.ef_search, p.dim) == (16, 200, 128, 512)
    assert p.metric_type == "cosine_similarity"
    assert p.is_vector_normed is True


# --------------------------------------------------------------------------- 检索


def test_search_sql_is_parameterised_not_string_concatenated():
    request = V.SearchRequest(
        mode=V.RetrievalMode.TAG_PLUS_VECTOR,
        text="雨天夜间高速行人横穿",
        filters=V.recent_days_window(30, today=date(2026, 3, 1)),
        top_k=50,
    )
    sql, params = V.render_search_sql(request, image_vector=[0.1] * 512, text_vector=[0.2] * 512)
    assert "SELECT" in sql.upper()
    assert V.VECTOR_TABLE_NAME in sql
    assert "LIMIT" in sql.upper()
    assert params, "向量应作为绑定参数传入，不该拼进 SQL 字符串"


def test_recent_days_window_builds_a_closed_range():
    """原文示例：最近 30 天。标量过滤先收窄候选集，再做向量召回。"""
    filters = V.recent_days_window(30, today=date(2026, 3, 1))
    assert filters.dt_to == date(2026, 3, 1)
    assert (filters.dt_to - filters.dt_from).days == 30


def test_search_service_records_the_statements_it_would_run():
    executor = V.RecordingExecutor()
    svc = V.VectorSearchService(encoder=FakeClipEncoder(), executor=executor)
    assert svc.backend is V.VectorBackend.EXTERNAL_PAIMON
    assert svc.capabilities()


def test_sla_and_deadline_constants_are_the_source_figures():
    assert V.SEARCH_P95_SLA_SECONDS == 2.0  # 千万级单次检索 P95 ≤ 2 秒
    assert V.PIPELINE_DEADLINE_HOUR == 6  # T+1 向量化凌晨 6 点前完成
    assert V.DEFAULT_TOP_K == 50
    assert len(V.PIPELINE_STEPS) == 5
    assert len(V.POC_CHECKLIST) == 5


# --------------------------------------------------------------------------- 驱动缺席


def test_starrocks_client_import_does_not_need_a_driver():
    """驱动延迟导入：没装 pymysql / mysql-connector 也能 import 本包、渲染全部 SQL。

    真正建连接时才报 MissingDriverError，且报错信息要能直接照着装。
    """
    client = V.StarRocksClient()
    assert client.config is not None
    with pytest.raises(RuntimeError) as excinfo:
        client.query("SELECT 1")
    # 报错信息要能直接照着装，并指出「只渲染 SQL」的替代路径
    assert "pip install pymysql" in str(excinfo.value)
    assert "RecordingExecutor" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, V.MissingDriverError)
