"""vector 深度审计：CLIP 图文双塔双向量列、以图搜图、HNSW 索引治理。

与 ``tests/smoke/test_vector.py`` 的分工：
  · 冒烟那份测「跑得通」——五步流水线能跑完、DDL 能渲染、常量在。
  · 这份测「逐字 + 真能用」——原文给的每个数字一个字不许改；以图搜图必须真的能从
    一张 Badcase 图片走到 SQL；索引治理（幂等键 / 预过滤 / 出处字段 / 分区级刷新 /
    版本并存）的每条分支与异常路径都要被打到。

判据来源（标注篇号以便回查，引用一律原话）：
  [a7] 系列二 · 湖仓实战 第 7 篇《HNSW 向量索引落地 StarRocks：从 Paimon 外部表到语义检索》
       （2026-09-06）——本子系统主线；
  [a5] 全景综述《智驾数据闭环的湖仓架构全景》第七章——**HNSW 落地参数的唯一出处**：
       「向量落湖于 Paimon，HNSW 索引建在 StarRocks 外部表上（cosine 距离、M=16、
       efConstruction=200），按分区增量刷新」。[a7] 第四章对应位置是一张未公开文字版的
       图片，正文只说「POC 阶段压测定参」——所以这组数字必须标 [a5]，标成 [a7] 会被原文打脸；
  [a9] 《Paimon 2.0 系列：从 JSON 到 Variant》（2026-08-17）——vector_meta 的基准数字。
"""

from __future__ import annotations

import inspect
import json
from datetime import date, datetime
from pathlib import Path

import pytest

from adas_lakehouse import vector as V
from adas_lakehouse.vector import embedding as E
from adas_lakehouse.vector import params as P
from adas_lakehouse.vector import variant as VA

DT = "2026-09-06"  # [a7] 发表日，用作示例分区
DATA_ID = "COLLECT_BP_20260301123045_b7e2"
IN_WINDOW = datetime(2026, 9, 7, 3, 0, 0)  # 凌晨 6 点前（[a7] 三的硬截止）


# =============================================================== 假件（不连任何外部服务）


class FakeClip:
    """CLIP 双塔编码器假实现——**批量**接口，和入湖侧 ClipEncoder 协议一致。

    图片塔与文本塔给出不同的常量向量，这样就能从 SQL / 打分里看出来哪一路被用了。
    """

    embedding_version = "clip_v1"
    model_version = "ViT-B/32-2026Q3"
    model_name = "CLIP-ViT-B/32"
    dim = 512

    def __init__(self) -> None:
        self.image_calls = 0
        self.text_calls = 0

    def encode_images(self, uris):
        self.image_calls += len(uris)
        return [tuple(0.25 for _ in range(self.dim)) for _ in uris]

    def encode_texts(self, texts):
        self.text_calls += len(texts)
        return [tuple(0.75 for _ in range(self.dim)) for _ in texts]


class ExplodingSink:
    """第 N 批写回时炸掉的 sink——用来验「编完没写成，批次不许算完成」。"""

    def __init__(self, fail_on_call: int) -> None:
        self.fail_on_call = fail_on_call
        self.calls = 0
        self.rows: list[dict] = []

    def upsert(self, rows):
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise RuntimeError("模拟写回失败（GPU 算完了但 Paimon 挂了）")
        self.rows.extend(rows)
        return len(rows)


def _records(n: int, *, dt: str = DT) -> list[V.ImageRecord]:
    return [
        V.ImageRecord(
            image_id=f"IMG-{i:04d}",
            data_id=DATA_ID,
            dt=dt,
            image_uri=f"s3://adas-frames/{dt}/IMG-{i:04d}.jpg",
            caption="雨天夜间高速行人横穿",
            image_content_hash=f"{i:08x}",
            create_time=datetime(2026, 9, 6, 12, i % 60),
            meta=V.build_meta(
                perception={"weather": "rain"}, scene={"tags": "pedestrian_crossing"}
            ),
        )
        for i in range(n)
    ]


def _pipeline(**kw) -> V.EmbeddingPipeline:
    kw.setdefault("encoder", FakeClip())
    kw.setdefault("sink", V.RecordingSink())
    kw.setdefault("sample_ratio", 1.0)  # 关掉抽样，让断言确定
    return V.EmbeddingPipeline(**kw)


# =================================================== 一、HNSW 参数逐字（[a5] 第七章）


def test_hnsw_landing_values_are_the_a5_chapter_seven_literals():
    """M=16 / efConstruction=200 / cosine —— [a5] 七的三个字面量，一个字不许改。"""
    p = V.DEFAULT_HNSW_PARAMS
    assert p.m == 16
    assert p.ef_construction == 200
    assert p.metric_type == "cosine_similarity"
    assert V.SIMILARITY_METRIC_NAME == "cosine"


def test_the_same_three_numbers_are_registered_once_more_in_ads_and_agree():
    """params 的 docstring 承诺「同一组数字在 ads.constants 也有登记，两处必须一致」。"""
    from adas_lakehouse.ads import constants as A

    assert (V.DEFAULT_HNSW_PARAMS.m, 200) == (A.HNSW_M, A.HNSW_EF_CONSTRUCTION)
    assert A.HNSW_METRIC == V.SIMILARITY_METRIC_NAME == "cosine"
    assert A.VECTOR_SEARCH_P95_LATENCY_SECONDS == V.SEARCH_P95_SLA_SECONDS == 2.0


def test_provenance_annotation_credits_a5_for_m_and_efconstruction():
    """出处标注本身也是被审计对象：这组数字**是原文给的**，不许标成「本项目设计」。

    之前有过把 M / efConstruction 标成「原文未明确」的错误——那等于把 [a5] 正文里
    白纸黑字的落地值说成拍脑袋，任何人照着注释调参都会理直气壮地改掉原文口径。
    """
    doc = inspect.getdoc(V.HnswIndexParams) or ""
    assert "[a5]" in doc
    assert "M=16" in doc and "efConstruction=200" in doc
    # M / efConstruction 明确署名原文
    assert "是原文数字" in doc
    # 只有 ef_search 可以标「原文未明确」
    assert "⚠️ 原文未明确，本项目设计：``ef_search``" in doc
    src = inspect.getsource(P)
    assert "[a7] 对应位置是一张未公开文字版的图片" in src


def test_ef_search_is_honestly_marked_as_a_project_default():
    """efSearch 原文两篇都没给——默认 128 必须显式署名本项目，不许冒充原文。"""
    assert V.DEFAULT_HNSW_PARAMS.ef_search == 128
    assert V.DEFAULT_HNSW_PARAMS.ann_params() == '{"efsearch":"128"}'


def test_index_properties_carry_the_literals_into_the_ddl():
    props = V.DEFAULT_HNSW_PARAMS.index_properties()
    assert props["M"] == "16"
    assert props["efconstruction"] == "200"
    assert props["metric_type"] == "cosine_similarity"
    assert props["index_type"] == "hnsw"
    assert props["dim"] == str(V.DEFAULT_VECTOR_DIM) == "512"
    ddl = V.render_create_index_ddl()
    assert '"M" = "16"' in ddl and '"efconstruction" = "200"' in ddl


def test_the_sweep_grid_contains_the_published_landing_point():
    """[a7] 六说「M / efConstruction 在 POC 阶段按数据规模压测定参」——

    压测网格是本项目补的，但**原文的落地值必须落在网格内**，否则压测会把 (16, 200)
    这个已知答案排除在外。
    """
    assert 16 in V.HNSW_POC_SWEEP_GRID["m"]
    assert 200 in V.HNSW_POC_SWEEP_GRID["ef_construction"]
    combos = {(c.m, c.ef_construction) for c in V.sweep_grid()}
    assert (16, 200) in combos
    # 余弦相似度是原文写死的，不在扫参维度里
    assert "metric_type" not in V.HNSW_POC_SWEEP_GRID
    assert {c.metric_type for c in V.sweep_grid()} == {"cosine_similarity"}


def test_hnsw_params_reject_nonsense_before_starrocks_does():
    with pytest.raises(ValueError, match="M 必须为正整数"):
        V.HnswIndexParams(m=0)
    with pytest.raises(ValueError, match="不应小于 M"):
        V.HnswIndexParams(m=64, ef_construction=32)
    with pytest.raises(ValueError, match="efSearch 必须为正整数"):
        V.HnswIndexParams(ef_search=0)
    with pytest.raises(ValueError, match="余弦相似度"):
        V.HnswIndexParams(metric_type="dot_product")


# =========================================== 二、[a7] 正文里的其余数字与逐字表格


def test_the_only_acceptance_line_is_ten_million_scale_p95_two_seconds():
    """[a7] 六：「性能目标只有一条：千万级数据量单次向量检索 P95 ≤ 2 秒」。"""
    assert V.SEARCH_P95_SLA_SECONDS == 2.0
    assert V.SEARCH_SLA_SCALE_CN == "千万级"
    assert V.VECTOR_TABLE_SCALE_CN == "千万~亿级行"
    assert V.EXAMPLE_SCALAR_FILTER_RECENT_DAYS == 30


def test_pipeline_is_five_steps_transcribed_verbatim():
    """[a7] 三的五步表：「关键机制」列逐字。"""
    assert V.PIPELINE_STEP_COUNT == len(V.PIPELINE_STEPS) == 5
    assert V.PIPELINE_DEADLINE_HOUR == 6
    mech = {s.name_cn: s.mechanism_cn for s in V.PIPELINE_STEPS}
    assert mech["增量识别"] == "按 create_time / update_time 水位，仅处理新增与标签变更图片"
    assert mech["双路编码"] == "图片与 caption 经同一 CLIP 模型双塔编码，保证向量同空间"
    assert (
        mech["幂等写回"]
        == "按 (image_id, embedding_version) Upsert，重跑无副作用；标签变图片不变不重算"
    )
    assert mech["索引刷新"] == "写入完成后通知 StarRocks 增量刷新当日分区索引，新数据当日可检索"
    assert V.PIPELINE_RUNTIME_CN == "Spark / Ray + GPU 算子，批次失败可断点续跑"


def test_four_retrieval_capabilities_transcribed_verbatim():
    """[a7] 五的四类检索能力表——「索引路径」那一列决定了 SQL 该打哪个索引。"""
    by_mode = {c.mode: c for c in V.RETRIEVAL_CAPABILITIES}
    assert len(by_mode) == 4
    assert by_mode[V.RetrievalMode.TEXT_TO_IMAGE].index_path_cn == "text query 向量 → 双索引"
    assert (
        by_mode[V.RetrievalMode.TEXT_TO_IMAGE].scenario_cn == "「雨天夜间高速行人横穿」找同类场景"
    )
    assert by_mode[V.RetrievalMode.IMAGE_TO_IMAGE].index_path_cn == "image query 向量 → 图片索引"
    assert by_mode[V.RetrievalMode.IMAGE_TO_IMAGE].scenario_cn == "Badcase 找相似样本"
    assert by_mode[V.RetrievalMode.TAG_PLUS_VECTOR].index_path_cn == "标量预过滤 + ANN"
    assert by_mode[V.RetrievalMode.HYBRID].index_path_cn == "双向量加权融合 + 重排"


def test_poc_checklist_is_five_items_with_three_hard_gates():
    """[a7] 四：「全量上线前必须先过 POC 验证。五个前置验证项」。"""
    assert V.POC_CHECK_COUNT == len(V.POC_CHECKLIST) == 5
    items = {i.ordinal: i for i in V.POC_CHECKLIST}
    assert items[1].name_cn == "多向量列同表索引支持度"
    assert items[1].question_cn == "两个 ARRAY<FLOAT> 列能否各建一个"
    assert items[2].question_cn == "刷新能否精确到单个分区"
    assert items[3].question_cn == "千万级向量的建索引时间"
    assert items[4].question_cn == "当日分区刷完的时间窗"
    assert items[5].name_cn == "千万级检索 P95"
    assert [i.ordinal for i in V.POC_CHECKLIST if i.blocking] == [1, 2, 5]


def test_three_vector_store_options_and_the_chosen_one():
    """[a7] 一的三方案对比表，选第三类（StarRocks 向量索引）。"""
    chosen = [o for o in V.VECTOR_STORE_OPTIONS if o.chosen]
    assert len(V.VECTOR_STORE_OPTIONS) == 3
    assert len(chosen) == 1
    assert chosen[0].name_cn == "StarRocks 向量索引"
    assert chosen[0].advantage_cn == "湖仓一体、外部表免冗余、标量过滤原生融合"
    assert chosen[0].cost_cn == "超大向量规模能力需 POC 验证"


# =================================== 三、图文双塔双向量「同行存储」（[a7] 二 设计决策一）


def test_both_embeddings_are_two_array_float_columns_on_the_same_row():
    """[a7] 二：「image_embedding 与 text_embedding 都来自同一个 CLIP 模型的双塔编码」。

    「同行」不是修辞——它是文搜图 / 图搜图 / 混合检索共用一张表的前提，也是 POC 第 1 项
    「两个 ARRAY<FLOAT> 列能否各建一个索引」要验的东西。
    """
    spec = V.paimon_table_spec()
    cols = {c.name: c for c in spec.all_columns()}
    assert cols["image_embedding"].type == "ARRAY<FLOAT>"
    assert cols["text_embedding"].type == "ARRAY<FLOAT>"
    # 两列同表同行：主键只认 (image_id, embedding_version, dt)，没有「向量种类」这一维
    assert spec.primary_key == ("image_id", "embedding_version", "dt")
    assert V.UPSERT_KEY == ("image_id", "embedding_version")
    assert spec.partition_by == (V.PARTITION_FIELD,) == ("dt",)


def test_dual_indexes_map_one_to_one_onto_the_two_vector_columns():
    """[a7] 四「双索引——对应图文双向量」。"""
    idx = {i.name: i.column for i in V.dual_indexes()}
    assert idx == {
        V.IMAGE_INDEX_NAME: "image_embedding",
        V.TEXT_INDEX_NAME: "text_embedding",
    }
    assert all(i.params is V.DEFAULT_HNSW_PARAMS for i in V.dual_indexes())


def test_one_encoder_feeds_both_towers_so_the_vectors_share_a_space():
    """双塔必须同模型，否则向量不同空间——出处列（model_name）就是这条约束的凭证。"""
    sink = V.RecordingSink()
    clip = FakeClip()
    _pipeline(encoder=clip, sink=sink).run(DT, _records(3), now=IN_WINDOW)
    assert clip.image_calls == clip.text_calls == 3
    for row in sink.rows:
        assert len(row["image_embedding"]) == len(row["text_embedding"]) == 512
        assert row["model_name"] == "CLIP-ViT-B/32"
        assert row["model_version"] == "ViT-B/32-2026Q3"


# ===================================================== 四、以图搜图（IMAGE_TO_IMAGE）


def test_image_to_image_sql_only_touches_the_image_index():
    """[a7] 五：「图搜图 | image query 向量 → 图片索引」——不碰文本索引。"""
    assert V.index_path_for(V.RetrievalMode.IMAGE_TO_IMAGE) == (V.IMAGE_INDEX_NAME,)
    sql, _ = V.render_search_sql(
        V.SearchRequest(V.RetrievalMode.IMAGE_TO_IMAGE, image_uri="s3://badcase/1.jpg"),
        image_vector=[0.25] * 512,
    )
    assert "approx_cosine_similarity(`image_embedding`" in sql
    assert "text_embedding" not in sql
    assert f"索引路径: {V.IMAGE_INDEX_NAME}" in sql
    assert "ORDER BY image_sim DESC" in sql


def test_text_to_image_sql_hits_both_indexes_as_the_source_says():
    """[a7] 五：「文搜图 | text query 向量 → **双索引**」。

    只比 text_embedding 的话，捞回来的是「caption 像的图」而不是「画面像的图」，
    CLIP 双塔就白编了——所以同一条 text query 向量必须同时打 image / text 两个索引。
    """
    assert V.index_path_for(V.RetrievalMode.TEXT_TO_IMAGE) == (
        V.IMAGE_INDEX_NAME,
        V.TEXT_INDEX_NAME,
    )
    sql, _ = V.render_search_sql(
        V.SearchRequest(V.RetrievalMode.TEXT_TO_IMAGE, text="雨天夜间高速行人横穿"),
        text_vector=[0.75] * 512,
    )
    assert "approx_cosine_similarity(`image_embedding`" in sql
    assert "approx_cosine_similarity(`text_embedding`" in sql
    assert V.IMAGE_INDEX_NAME in sql and V.TEXT_INDEX_NAME in sql
    # [a7] 五 ④ 的融合公式 image×w1 + text×w2 下推进 SQL
    assert f"(0.5 * {V.IMAGE_SIM_ALIAS} + 0.5 * {V.TEXT_SIM_ALIAS}) AS {V.FUSED_SCORE_ALIAS}" in sql
    assert f"ORDER BY {V.FUSED_SCORE_ALIAS} DESC" in sql


def test_hybrid_uses_two_different_query_vectors():
    """[a7] 五：「混合检索 | 双向量加权融合 + 重排」——图 query 打图索引、文 query 打文索引。"""
    sql, _ = V.render_search_sql(
        V.SearchRequest(
            V.RetrievalMode.HYBRID,
            text="雨天",
            image_uri="s3://badcase/1.jpg",
            weights=V.FusionWeights(0.7, 0.3),
        ),
        image_vector=[0.1] * 512,
        text_vector=[0.9] * 512,
    )
    assert "approx_cosine_similarity(`image_embedding`, [0.100000" in sql
    assert "approx_cosine_similarity(`text_embedding`, [0.900000" in sql
    assert f"(0.7 * {V.IMAGE_SIM_ALIAS} + 0.3 * {V.TEXT_SIM_ALIAS})" in sql


def test_image_to_image_runs_end_to_end_with_the_ingest_side_clip_encoder():
    """真·以图搜图：拿一张 Badcase 图片 → 编码 → SQL → 命中 → 回补元数据。

    关键点：注入的是**入湖侧那个批量编码器**（[a7] 五 ① 要求「用同一个 CLIP 模型」）。
    它的接口是 encode_images / encode_texts（复数），服务层必须自己适配——
    以前这里是断的，`search()` 会撞 AttributeError，这条检索路径根本走不通。
    """
    clip = FakeClip()
    executor = V.RecordingExecutor(
        responses=[
            V.QueryResult(("image_id", V.IMAGE_SIM_ALIAS), (("IMG-1", 0.91), ("IMG-2", 0.83)), 0.0),
            V.QueryResult(("image_id", "caption_text"), (("IMG-1", "雨天夜间"),), 0.0),
        ]
    )
    svc = V.VectorSearchService(encoder=clip, executor=executor)
    resp = svc.image_to_image("s3://badcase/img_001.jpg", top_k=10)

    assert clip.image_calls == 1
    assert clip.text_calls == 0, "图搜图不该去编码文本"
    assert [h.image_id for h in resp.hits] == ["IMG-1", "IMG-2"]
    assert resp.hits[0].score == pytest.approx(0.91)
    assert resp.hits[0].image_sim == pytest.approx(0.91)
    assert resp.hits[0].text_sim is None
    assert resp.hits[0].metadata["caption_text"] == "雨天夜间"
    assert resp.backend is V.VectorBackend.EXTERNAL_PAIMON
    assert resp.within_sla is True
    assert "image_embedding" in resp.sql
    # 检索期把 efSearch 下推
    assert any("ann_params" in s for s in executor.statements)


def test_text_to_image_end_to_end_fuses_both_similarity_columns():
    clip = FakeClip()
    executor = V.RecordingExecutor(
        responses=[
            V.QueryResult(
                ("image_id", V.IMAGE_SIM_ALIAS, V.TEXT_SIM_ALIAS),
                (("IMG-A", 0.9, 0.1), ("IMG-B", 0.2, 1.0)),
                0.0,
            )
        ]
    )
    svc = V.VectorSearchService(encoder=clip, executor=executor)
    resp = svc.search(
        V.SearchRequest(V.RetrievalMode.TEXT_TO_IMAGE, text="雨天夜间高速行人横穿"), enrich=False
    )
    assert clip.text_calls == 1 and clip.image_calls == 0
    # 0.5*0.9+0.5*0.1 = 0.50 ; 0.5*0.2+0.5*1.0 = 0.60 → B 应当排前面
    assert [h.image_id for h in resp.hits] == ["IMG-B", "IMG-A"]
    assert resp.hits[0].score == pytest.approx(0.6)


def test_the_batch_encoder_adapter_is_explicit_and_checked():
    clip = FakeClip()
    wrapped = V.as_query_encoder(clip)
    assert isinstance(wrapped, V.ClipQueryEncoder)
    assert wrapped.dim == 512 and wrapped.embedding_version == "clip_v1"
    assert len(wrapped.encode_image("s3://a.jpg")) == 512
    with pytest.raises(ValueError, match="图片 query 为空"):
        wrapped.encode_image("")
    with pytest.raises(TypeError, match="无法用于查询向量化"):
        V.as_query_encoder(object())


def test_service_refuses_an_encoder_whose_dim_disagrees_with_the_index():
    class Wrong(FakeClip):
        dim = 768

    with pytest.raises(ValueError, match="不在同一空间"):
        V.VectorSearchService(encoder=Wrong(), executor=V.RecordingExecutor())


def test_searching_without_any_way_to_get_a_query_vector_fails_loudly():
    svc = V.VectorSearchService(encoder=None, executor=V.RecordingExecutor())
    with pytest.raises(RuntimeError, match="不能省略"):
        svc.search(V.SearchRequest(V.RetrievalMode.IMAGE_TO_IMAGE, image_uri="s3://x.jpg"))


def test_request_validation_covers_every_mode():
    with pytest.raises(ValueError, match="image_to_image 需要"):
        V.SearchRequest(V.RetrievalMode.IMAGE_TO_IMAGE)
    with pytest.raises(ValueError, match="需要 text"):
        V.SearchRequest(V.RetrievalMode.TEXT_TO_IMAGE)
    with pytest.raises(ValueError, match="图文互补"):
        V.SearchRequest(V.RetrievalMode.HYBRID, text="雨天")
    with pytest.raises(ValueError, match="top_k"):
        V.SearchRequest(V.RetrievalMode.TEXT_TO_IMAGE, text="雨天", top_k=V.MAX_TOP_K + 1)


def test_query_vectors_with_nan_are_rejected_instead_of_silently_rendered():
    """NaN 能过 float()，渲染出来是裸的 nan——StarRocks 不会报错，只会静默返回 NULL 相似度。"""
    with pytest.raises(ValueError, match="不是有限浮点数"):
        V.render_search_sql(
            V.SearchRequest(V.RetrievalMode.IMAGE_TO_IMAGE, image_uri="s3://x.jpg"),
            image_vector=[float("nan")] * 512,
        )


# ====================================== 五、标量预过滤 + 版本过滤（[a7] 五 ② / 二 设计决策三）


def test_every_search_sql_carries_the_active_version_filter():
    """[a7] 二：「检索默认只查 active 版本」——这句 WHERE 不许漏。"""
    for mode, kw in (
        (V.RetrievalMode.IMAGE_TO_IMAGE, {"image_uri": "s3://x.jpg"}),
        (V.RetrievalMode.TEXT_TO_IMAGE, {"text": "雨天"}),
        (V.RetrievalMode.TAG_PLUS_VECTOR, {"text": "雨天"}),
        (V.RetrievalMode.HYBRID, {"text": "雨天", "image_uri": "s3://x.jpg"}),
    ):
        sql, _ = V.render_search_sql(
            V.SearchRequest(mode, **kw), image_vector=[0.1] * 512, text_vector=[0.2] * 512
        )
        assert V.ACTIVE_FILTER_CLAUSE in sql
    # 回补元数据那一跳同样只认 active
    enrich_sql, _ = V.render_enrich_sql(["IMG-1"])
    assert V.ACTIVE_FILTER_CLAUSE in enrich_sql


def test_scalar_prefilter_is_parameterised_and_partition_pruned_first():
    """[a7] 五 ②：「先用标量条件缩小范围，再进 ANN，检索快且准」。"""
    filters = V.ScalarFilters(
        dt_from=date(2026, 8, 7),
        dt_to=date(2026, 9, 6),
        equals={"city_code": "SH", "weather": "rain", "camera_position": ["front", "rear"]},
        gps_bbox=(31.0, 121.0, 31.5, 121.6),
    )
    clauses, params = V.compile_scalar_filters(filters)
    assert clauses[0] == "`dt` >= %s" and clauses[1] == "`dt` <= %s"  # 分区裁剪排最前
    assert params[:2] == ["2026-08-07", "2026-09-06"]
    assert "`camera_position` IN (%s, %s)" in clauses
    assert all(c != "SH" and c != "rain" for c in clauses)  # 取值一律走占位符


def test_filter_columns_outside_the_whitelist_are_rejected():
    with pytest.raises(ValueError, match="白名单"):
        V.ScalarFilters(equals={"1=1; DROP TABLE x": "boom"})
    # 白名单里的每一列都必须真的在 registry 上，否则过滤器会静默失效
    assert V.vector_column_names() >= V.SCALAR_FILTER_COLUMNS


def test_a_search_with_no_prefilter_at_all_leaves_a_trace():
    """[a7] 六把「dt 分区裁剪 + 标量预过滤」列为第一优化手段——全量 ANN 直接赌上验收线。

    这里只警告不拦截（离线圈选确实会全量跑），但必须留痕，否则 P95 崩了查不到根因。
    """
    assert V.prefilter_warning(V.ScalarFilters()) is not None
    assert "P95" in V.prefilter_warning(V.ScalarFilters())
    assert V.prefilter_warning(V.recent_days_window(30, today=date(2026, 9, 6))) is None
    assert V.prefilter_warning(V.ScalarFilters(equals={"city_code": "SH"})) is None
    assert V.prefilter_warning(V.ScalarFilters(gps_bbox=(31.0, 121.0, 31.5, 121.6))) is None
    assert V.prefilter_warning(V.ScalarFilters(time_from=datetime(2026, 9, 6))) is None


def test_recent_thirty_days_window_matches_the_source_example():
    """[a7] 一的示例：「最近 30 天 + 某城市 + 雨天」。"""
    f = V.recent_days_window(today=date(2026, 9, 6))
    assert f.dt_to == date(2026, 9, 6)
    assert (f.dt_to - f.dt_from).days == 30


def test_embedding_version_cannot_smuggle_sql_into_the_where_clause():
    """embedding_version 是唯一被原样拼进 SQL 文本的取值，必须走白名单。"""
    with pytest.raises(ValueError, match="非法 embedding_version"):
        V.render_active_filter("clip_v1' OR '1'='1")
    with pytest.raises(ValueError, match="非法 embedding_version"):
        V.render_search_sql(
            V.SearchRequest(
                V.RetrievalMode.IMAGE_TO_IMAGE,
                image_uri="s3://x.jpg",
                embedding_version="x'; DROP TABLE dwd_mining_image_vector_detail; --",
            ),
            image_vector=[0.1] * 512,
        )
    ok = V.render_active_filter("clip_v2.1")
    assert ok == f"{V.ACTIVE_FILTER_CLAUSE} AND embedding_version = 'clip_v2.1'"


def test_enrichment_never_projects_the_vector_bodies():
    """[a7] 五 ⑤ 回补元数据——把千万级向量本体拉回服务层是打穿 P95 最快的方式。"""
    sql, params = V.render_enrich_sql(["IMG-1", "IMG-2"])
    assert "image_embedding" not in sql and "text_embedding" not in sql
    assert params == ["IMG-1", "IMG-2"]
    assert "caption_text" in sql  # 标签 / caption / 地理 / 时间要补齐
    with pytest.raises(ValueError, match="至少一个 image_id"):
        V.render_enrich_sql([])


# ====================================== 六、embedding_version 并存 / 灰度 / 一键回滚


def test_switching_versions_only_flips_a_status_column():
    """[a7] 二：「灰度切换与一键回滚都不需要重写数据」。"""
    reg = V.VersionRegistry()
    reg.register(V.EmbeddingVersion("clip_v1", "CLIP-ViT-B/32"))
    reg.register(V.EmbeddingVersion("clip_v2", "CLIP-ViT-L/14", status=V.VectorStatus.DEPRECATED))

    stmts = reg.plan_switch("clip_v1", "clip_v2")
    # 顺序：先激活新版本再退役旧版本，否则有一瞬间没有 active 版本 → 检索空窗
    assert "'active'" in stmts[0] and "clip_v2" in stmts[0]
    assert "'deprecated'" in stmts[1] and "clip_v1" in stmts[1]
    for s in stmts:
        assert "image_embedding" not in s and "text_embedding" not in s
    assert reg.require_single_active().version == "clip_v2"

    rollback = reg.rollback("clip_v2", "clip_v1")[0]
    assert "clip_v2" in rollback and "clip_v1" in rollback
    assert reg.require_single_active().version == "clip_v1"


def test_two_active_versions_must_be_disambiguated_not_guessed():
    reg = V.VersionRegistry()
    reg.register(V.EmbeddingVersion("clip_v1", "a"))
    reg.register(V.EmbeddingVersion("clip_v2", "b"))
    with pytest.raises(RuntimeError, match="多个 active"):
        reg.require_single_active()
    reg2 = V.VersionRegistry()
    with pytest.raises(RuntimeError, match="没有任何 active"):
        reg2.require_single_active()


def test_dimension_drift_between_model_and_index_is_caught_before_search():
    reg = V.VersionRegistry()
    reg.register(V.EmbeddingVersion("clip_v2", "CLIP-ViT-L/14", dim=768))
    with pytest.raises(ValueError, match="必须同步重建索引"):
        reg.check_dim_compatible("clip_v2", V.DEFAULT_HNSW_PARAMS.dim)
    reg.check_dim_compatible("clip_v2", 768)  # 一致就放行


def test_vector_status_has_exactly_the_two_documented_values():
    assert [s.value for s in V.VectorStatus] == ["active", "deprecated"]


# ================================ 七、分区级索引增量刷新（[a7] 四「分区级刷新」/ 三 ⑤）


def test_partition_refresh_touches_one_day_and_both_indexes():
    stmts = V.render_partition_refresh_sql(DT)
    joined = "\n".join(stmts)
    assert joined.count(DT) == len(stmts)  # 每条语句都带分区
    assert V.IMAGE_INDEX_NAME in joined and V.TEXT_INDEX_NAME in joined
    assert "REFRESH EXTERNAL TABLE" in stmts[0]
    assert all("PARTITION" in s for s in stmts)
    # 分区级刷新的反面：不许出现全表重建
    assert "BUILD INDEX" in joined and "REBUILD ALL" not in joined.upper()


def test_internal_table_refresh_skips_the_external_metadata_hop():
    stmts = V.render_partition_refresh_sql(DT, backend=V.VectorBackend.INTERNAL_STARROCKS)
    assert not any("REFRESH EXTERNAL TABLE" in s for s in stmts)
    assert len(stmts) == 2  # 图文两个索引各一条


def test_partition_value_is_whitelisted_before_it_reaches_ddl():
    for bad in ("2026-9-6", "2026-09-06'; DROP TABLE x; --", "", "today"):
        with pytest.raises(ValueError, match="yyyy-MM-dd"):
            V.render_partition_refresh_sql(bad)
    V.validate_partition_value("2026-09-06")


def test_index_service_rebuild_drops_before_it_creates():
    ex = V.RecordingExecutor()
    svc = V.IndexService(executor=ex)
    svc.rebuild_all()
    text = "\n".join(ex.statements)
    assert text.index("DROP INDEX") < text.index("CREATE INDEX")
    assert ex.statements[0].startswith("DROP INDEX")


def test_index_service_refresh_is_replayable():
    ex = V.RecordingExecutor()
    svc = V.IndexService(executor=ex)
    first = svc.refresh_partition(DT)
    second = svc.refresh_partition(DT)
    assert first == second, "分区刷新必须幂等——调度重试不能有副作用"


# ============================================ 八、POC 五项与降级（[a7] 四 / 六）


def _poc(passed: dict[int, bool]) -> list[V.PocResult]:
    return [V.PocResult(i, passed.get(i.ordinal, True), "measured") for i in V.POC_CHECKLIST]


def test_a_missing_poc_item_is_not_a_silent_pass():
    with pytest.raises(ValueError, match="缺项不得默认通过"):
        V.evaluate_poc(_poc({})[:4])


def test_all_five_pass_keeps_the_first_tier():
    report = V.evaluate_poc(_poc({}))
    assert report.recommended_backend is V.VectorBackend.EXTERNAL_PAIMON
    assert report.blocking_failures == ()


@pytest.mark.parametrize("failed_ordinal", [1, 2, 5])
def test_any_hard_gate_failure_downgrades_to_the_internal_table(failed_ordinal):
    report = V.evaluate_poc(_poc({failed_ordinal: False}))
    assert report.recommended_backend is V.VectorBackend.INTERNAL_STARROCKS
    assert [r.item.ordinal for r in report.blocking_failures] == [failed_ordinal]
    assert "降级" in report.summary()


@pytest.mark.parametrize("failed_ordinal", [3, 4])
def test_observation_items_do_not_trigger_a_downgrade(failed_ordinal):
    report = V.evaluate_poc(_poc({failed_ordinal: False}))
    assert report.recommended_backend is V.VectorBackend.EXTERNAL_PAIMON


def test_runtime_guard_downgrades_only_after_three_consecutive_breaches():
    sel = V.BackendSelector()
    assert sel.decide_from_runtime(1.5).backend is V.VectorBackend.EXTERNAL_PAIMON
    assert sel.decide_from_runtime(2.4).backend is V.VectorBackend.EXTERNAL_PAIMON
    assert sel.decide_from_runtime(2.4).backend is V.VectorBackend.EXTERNAL_PAIMON
    third = sel.decide_from_runtime(2.4)
    assert third.backend is V.VectorBackend.INTERNAL_STARROCKS
    assert third.is_downgraded
    # 恰好卡在 2.0 秒上不算超线（原文写的是 ≤ 2 秒）
    assert sel.decide_from_runtime(2.0).backend is V.VectorBackend.EXTERNAL_PAIMON


def test_the_sweep_keeps_only_combinations_inside_the_two_second_line():
    measured = {16: 1.2, 32: 2.5}
    scored = V.run_poc_sweep(lambda p: measured.get(p.m, 3.0), grid={"m": (16, 32)})
    assert [p.m for p, _ in scored] == [16]
    assert scored[0][1] == 1.2
    assert V.run_poc_sweep(lambda p: 9.9, grid={"m": (16,)}) == ()


def test_downgrade_sync_only_carries_active_rows_with_non_null_vectors():
    sql = V.render_sync_sql(DT)
    assert V.ACTIVE_FILTER_CLAUSE in sql
    assert "`image_embedding` IS NOT NULL" in sql
    assert "`text_embedding` IS NOT NULL" in sql
    assert f"`dt` = '{DT}'" in sql


def test_reconciliation_uses_the_lake_as_the_baseline():
    sql = V.render_reconcile_sql(DT)
    for key in V.PRIMARY_KEY:
        assert f"e.`{key}` = i.`{key}`" in sql
    assert "missing_in_internal" in sql and "artifact_drift" in sql
    report = V.ReconcileReport(DT, 100, 100, 0, 0)
    assert report.consistent
    assert not V.ReconcileReport(DT, 100, 99, 1, 0).consistent
    assert not V.ReconcileReport(DT, 100, 100, 0, 3).consistent


# ============== 九、向量派生索引治理：幂等键 / 出处字段 / 断点续跑（[a7] 三 ④⑤）


def test_written_rows_carry_every_provenance_column_the_registry_defines():
    """出处字段不是装饰：换代、降级、混存出问题时，靠它们才说得清一行向量是谁编的。

    registry 定义了 model_name / similarity_metric / index_refresh_status 三列——
    定义了却没人写 = 永远是 NULL = 静默失效（这正是通用侧 vector_service 的老毛病）。
    """
    sink = V.RecordingSink()
    _pipeline(sink=sink).run(DT, _records(2), now=IN_WINDOW)
    row = sink.rows[0]
    assert row["vector_status"] == "active"
    assert row["embedding_version"] == "clip_v1"
    assert row["model_name"] == "CLIP-ViT-B/32"
    assert row["similarity_metric"] == V.SIMILARITY_METRIC_NAME == "cosine"
    assert row["index_refresh_status"] == V.INDEX_REFRESH_PENDING == "pending"
    assert row["artifact_status"] == "active"
    assert row["data_id"] == DATA_ID and row["artifact_id"] and row["run_id"]
    assert row["embedding_dim"] == 512
    assert set(row) <= V.vector_column_names()


def test_index_refresh_status_is_closed_out_after_the_partition_is_refreshed():
    report = _pipeline().run(DT, _records(2), now=IN_WINDOW)
    assert V.INDEX_REFRESH_DONE == "done"
    assert f"'{V.INDEX_REFRESH_DONE}'" in report.refresh_status_sql
    assert f"`dt` = '{DT}'" in report.refresh_status_sql
    assert "clip_v1" in report.refresh_status_sql
    assert "image_embedding" not in report.refresh_status_sql  # 只改一列
    assert V.INDEX_REFRESH_STATUSES == ("pending", "refreshing", "done")


def test_the_artifact_id_is_derived_so_a_rerun_produces_the_same_row():
    """[a7] 三 ④：「按 (image_id, embedding_version) Upsert，重跑无副作用」。"""
    a, b = V.RecordingSink(), V.RecordingSink()
    records = _records(4)
    _pipeline(sink=a).run(DT, records, now=IN_WINDOW)
    _pipeline(sink=b).run(DT, records, now=IN_WINDOW)
    key = lambda rows: sorted((r["image_id"], r["embedding_version"]) for r in rows)  # noqa: E731
    assert key(a.rows) == key(b.rows)
    assert len(set(key(a.rows))) == len(a.rows)
    # artifact_id 由 (输入, 算法版本, 内容) 派生 → 两次运行完全一致
    assert sorted(r["artifact_id"] for r in a.rows) == sorted(r["artifact_id"] for r in b.rows)


def test_a_tag_change_that_leaves_the_image_alone_does_not_burn_gpu():
    """[a7] 三 ④：「标签变图片不变不重算」。"""
    clip = FakeClip()
    sink = V.RecordingSink()
    records = _records(3)
    pipe = _pipeline(encoder=clip, sink=sink)
    pipe.known_fingerprints = {r.image_id: r.fingerprint() for r in records}

    report = pipe.run(DT, records, now=IN_WINDOW)
    assert clip.image_calls == 0 and clip.text_calls == 0
    assert report.meta_only_count == 3
    assert report.encoded_count == 0
    assert report.upserted_count == 3
    # 只改元数据的行不带向量列
    assert all("image_embedding" not in r for r in sink.rows)
    assert all("vector_meta" in r for r in sink.rows)


def test_resume_key_is_the_partition_and_version_not_the_run_id(tmp_path: Path):
    """run_id 含运行时间戳，每次重跑都变——拿它当断点键，断点续跑永远是全量重跑。"""
    assert E.resume_key_for(DT, "clip_v1") == f"{DT}|clip_v1"
    cp_file = tmp_path / "ckpt.json"
    cp = V.BatchCheckpoint.load(E.resume_key_for(DT, "clip_v1"), cp_file)
    cp.mark_done(0)
    assert json.loads(cp_file.read_text())["resume_key"] == f"{DT}|clip_v1"
    # 同一分区 + 同一版本 → 接着跑
    assert V.BatchCheckpoint.load(f"{DT}|clip_v1", cp_file).is_done(0)
    # 换了版本 → 批次编号对不上，检查点作废
    assert not V.BatchCheckpoint.load(f"{DT}|clip_v2", cp_file).is_done(0)


def test_a_batch_that_was_encoded_but_never_written_is_not_marked_done(tmp_path: Path):
    """「批次失败可断点续跑」的要害：**写回成功**才算这批做完。

    先标记后写回的话，「编完 0 号批次 → 崩在写回之前 → 重跑跳过 0 号批次」会让那一批
    向量永远丢失，而且流水线还会报成功。
    """
    cp_file = tmp_path / "ckpt.json"
    records = _records(4)
    bad = ExplodingSink(fail_on_call=2)  # 第 2 批写回时炸
    pipe = _pipeline(sink=bad, batch_size=2, checkpoint_path=cp_file)
    with pytest.raises(RuntimeError, match="模拟写回失败"):
        pipe.run(DT, records, now=IN_WINDOW)

    done = json.loads(cp_file.read_text())["done_batches"]
    assert done == [0], "只有写回成功的那一批能算完成"
    assert len(bad.rows) == 2

    # 重跑：0 号批跳过，1 号批补上，一条不多一条不少
    clip2, good = FakeClip(), V.RecordingSink()
    _pipeline(encoder=clip2, sink=good, batch_size=2, checkpoint_path=cp_file).run(
        DT, records, now=IN_WINDOW
    )
    assert clip2.image_calls == 2, "已完成的批次不该再烧一次 GPU"
    assert [r["image_id"] for r in good.rows] == ["IMG-0002", "IMG-0003"]
    assert json.loads(cp_file.read_text())["done_batches"] == [0, 1]


def test_cross_partition_records_are_rejected_so_the_wrong_index_is_never_refreshed():
    pipe = _pipeline()
    mixed = _records(1) + _records(1, dt="2026-09-05")
    with pytest.raises(ValueError, match="与目标分区"):
        pipe.run(DT, mixed, now=IN_WINDOW)


def test_cost_tiering_is_deterministic_and_spares_high_value_data():
    """[a7] 三 ②：「高价值数据全量处理，普通数据按比例抽样」。"""
    pipe = _pipeline(sample_ratio=0.1)
    records = _records(200)
    high = [V.ImageRecord(image_id="HV-1", data_id=DATA_ID, dt=DT, cost_tier=V.CostTier.HIGH_VALUE)]
    kept1, dropped1 = pipe.apply_cost_tiering(records + high)
    kept2, _ = pipe.apply_cost_tiering(records + high)
    assert [r.image_id for r in kept1] == [r.image_id for r in kept2], "抽样必须确定性，否则不幂等"
    assert "HV-1" in {r.image_id for r in kept1}
    assert dropped1, "10% 抽样下 200 条普通数据不可能一条不丢"
    assert pipe.gpu_window_hint() == (0, V.PIPELINE_DEADLINE_HOUR)


def test_the_watermark_only_moves_forward():
    """[a7] 三 ①：「按 create_time / update_time 水位」。"""
    wm = V.Watermark(create_time_wm=datetime(2026, 9, 6, 12, 0))
    old = V.ImageRecord("IMG-x", DATA_ID, DT, create_time=datetime(2026, 9, 6, 11, 0))
    new = V.ImageRecord("IMG-y", DATA_ID, DT, create_time=datetime(2026, 9, 6, 13, 0))
    assert not wm.accepts(old)
    assert wm.accepts(new)
    wm.advance([new, old])
    assert wm.create_time_wm == datetime(2026, 9, 6, 13, 0)


def test_encoder_returning_the_wrong_batch_size_poisons_nothing():
    class Misaligned(FakeClip):
        def encode_texts(self, texts):
            return [tuple(0.1 for _ in range(self.dim))]

    with pytest.raises(RuntimeError, match="返回条数不匹配"):
        _pipeline(encoder=Misaligned(), batch_size=8).run(DT, _records(3), now=IN_WINDOW)


def test_missing_the_six_am_deadline_is_visible_in_the_report():
    """[a7] 三：「T+1 向量化流水线每日凌晨 6 点前完成增量处理」。"""
    late = _pipeline().run(DT, _records(1), now=datetime(2026, 9, 7, 7, 30))
    assert late.meets_deadline is False
    assert "未满足" in late.summary()
    early = _pipeline().run(DT, _records(1), now=IN_WINDOW)
    assert early.meets_deadline is True


def test_rows_with_columns_the_table_does_not_have_are_refused():
    pipe = _pipeline()
    rec = V.ImageRecord("IMG-z", DATA_ID, DT, scalars={"weather": "rain"})
    ok = pipe.build_meta_only_rows([rec])
    assert ok[0]["weather"] == "rain"
    bad = V.ImageRecord("IMG-z", DATA_ID, DT, scalars={"nonexistent_col": 1})
    with pytest.raises(ValueError, match="不存在的列"):
        pipe.build_meta_only_rows([bad])


def test_upsert_and_refresh_sql_validate_their_inputs():
    sql = E.render_upsert_sql(embedding_version="clip_v1", dt=DT, source_table="tmp_stg")
    assert "INSERT INTO" in sql and "clip_v1" in sql
    with pytest.raises(ValueError, match="非法 embedding_version"):
        E.render_upsert_sql(embedding_version="v1'; --", dt=DT, source_table="tmp_stg")
    with pytest.raises(ValueError, match="yyyy-MM-dd"):
        E.render_mark_refreshed_sql(dt="oops", embedding_version="clip_v1")


# ==================================== 十、vector_meta 的 Variant 落地（[a9] 逐字数字）


def test_shredding_schema_materialises_exactly_the_hot_paths():
    """[a9] 04：「显式 schema 适合热路径稳定的生产表」。"""
    schema = json.loads(V.VECTOR_META_SHREDDING_SCHEMA)
    assert schema["type"] == "ROW"
    payload = schema["fields"][0]
    assert payload["name"] == "vector_meta"
    names = {f["name"]: f["type"] for f in payload["type"]["fields"]}
    assert names == {
        "perception_weather": "STRING",
        "perception_object_count": "INT",
        "scene_tags": "STRING",
        "diagnostics_code": "STRING",
        "model_debug_score": "DOUBLE",
    }
    # 建表时作为表属性追加，registry 的对象不被就地改
    spec = V.paimon_table_spec()
    assert spec.extra_options["variant.shreddingSchema"] == V.VECTOR_META_SHREDDING_SCHEMA
    assert spec.extra_options["file.format"] == "parquet"  # [a9] 03 对 VARIANT 的硬要求


def test_build_meta_keys_line_up_with_the_hot_paths():
    meta = V.build_meta(perception={"weather": "rain"}, diagnostics={"code": "P0420"})
    for path in V.VECTOR_META_HOT_PATHS:
        top = path.removeprefix("$.").split(".")[0]
        assert top in meta, f"热路径 {path} 的顶层键不在 build_meta 的结构里，shredding 会落空"


def test_auto_infer_options_are_the_a9_literals():
    """[a9] 04 的 TBLPROPERTIES 示例，四个数值逐字。"""
    assert V.INFER_SHREDDING_OPTIONS == {
        "variant.inferShreddingSchema": "true",
        "variant.shredding.maxInferBufferRow": "4096",
        "variant.shredding.maxSchemaWidth": "300",
        "variant.shredding.maxSchemaDepth": "50",
        "variant.shredding.minFieldCardinalityRatio": "0.1",
    }
    assert V.SQL_ENGINE_REQUIREMENT == {"spark": "4.0+", "flink": "2.1+", "file_format": "parquet"}
    assert V.DUAL_WRITE_COMPARE_DAYS == (7, 14)


def test_a9_benchmark_numbers_are_transcribed_not_rounded():
    size = {b.doc_cn: b for b in VA.SIZE_BENCH}
    assert (size["4 层嵌套小对象"].json_bytes, size["4 层嵌套小对象"].variant_bytes) == (223, 215)
    assert size["扁平 200 字段"].ratio == 1.068
    assert VA.WIDE_DOC_SIZE_OVERHEAD_PCT == 6.8

    get = {b.doc_cn: b for b in VA.PATH_GET_BENCH}
    assert get["200 字段，$.field199"].json_parse_get_us == 8.871
    assert get["200 字段，$.field199"].variant_path_get_us == 0.257
    assert get["223 B 小对象"].speedup_cn == "Variant 慢 14%"
    assert VA.SMALL_DOC_VARIANT_SLOWDOWN_PCT == 14.0

    assert VA.ENCODE_COST_US["flat_200_fields"] == (8.691, 42.171)
    assert VA.PYTHON_ENCODE_COST_US_PER_ROW == {
        "python_shredding": 223.0,
        "from_python_wide": 223.5,
        "json_dumps": 20.8,
    }

    ab = {b.paths_per_row: b for b in VA.SHREDDING_AB_BENCH}
    assert (ab[256].plain_best_ms, ab[256].shredded_best_ms, ab[256].speedup) == (388.1, 12.6, 30.8)
    assert ab[64].plain_read_mib == 45.29
    assert VA.SHREDDING_AB_ROWS == 200_000 and VA.SHREDDING_AB_ROW_GROUP_MIB == 16

    rep = {b.doc_cn: b for b in VA.REPLACE_BENCH}
    assert (
        rep["扁平 20 字段"].json_parse_modify_dump_us,
        rep["扁平 20 字段"].variant_replace_us,
    ) == (
        7.417,
        0.201,
    )
    assert rep["扁平 20 字段"].speedup == 36.98
    assert rep["4 层嵌套小对象"].speedup == 22.23

    files = {b.representation: b for b in VA.FILE_BENCH}
    assert files["shredded Variant"].hot_path_read_ms == 11.663
    assert files["plain Variant"].full_decode_ms == 2215.983
    assert VA.FILE_BENCH_ROWS == 10_000 and VA.FILE_BENCH_UNCOMPRESSED_JSON_KIB_PER_ROW == 4.16


def test_breakeven_is_recomputed_from_the_source_numbers_not_hardcoded():
    """[a7 姊妹篇 a9] 07 的成本模型：20 字段约 11 次读取回本，200 字段约 5 次。"""
    assert V.breakeven_reads(2.687, 0.264) == V.BREAKEVEN_READS["flat_20_fields"] == 11
    assert V.breakeven_reads(42.171, 8.614) == V.BREAKEVEN_READS["flat_200_fields"] == 5
    assert V.PER_READ_SAVING_US == {"flat_20_fields": 0.264, "flat_200_fields": 8.614}
    # 223 B 的小而深对象：Variant 本身慢 14%，靠重复读取回不了本
    assert VA.BREAKEVEN_READS["nested_small_223b"] == float("inf")
    with pytest.raises(ValueError, match="回不了本"):
        V.breakeven_reads(1.621, -0.082)
    assert V.estimate_total_cost(
        writes=10, reads=100, encode_cost_us=2.687, per_read_cost_us=0.378
    ) == (pytest.approx(10 * 2.687 + 100 * 0.378))


def test_representation_advice_follows_the_four_case_table():
    """[a9] 07：「最终选择可以落到下面四类」。"""
    R = V.VariantRepresentation
    assert (
        V.recommend_representation(
            fields_change_often=False,
            hot_paths_stable=True,
            participates_in_key_or_filter=True,
            reads_per_write=1000,
            doc_is_wide=True,
        )
        is R.TYPED_COLUMN
    ), "参与主键/分区/join/排序/强 SLA 过滤 → 正式类型列"
    assert (
        V.recommend_representation(
            fields_change_often=True,
            hot_paths_stable=False,
            participates_in_key_or_filter=False,
            reads_per_write=1,
            doc_is_wide=False,
        )
        is R.JSON_STRING
    ), "几乎不查询内部字段 → 写入最便宜的 JSON STRING"
    assert (
        V.recommend_representation(
            fields_change_often=True,
            hot_paths_stable=False,
            participates_in_key_or_filter=False,
            reads_per_write=50,
            doc_is_wide=False,
        )
        is R.PLAIN_VARIANT
    ), "schema 高频变化、热路径不稳定 → plain VARIANT"
    assert (
        V.recommend_representation(
            fields_change_often=False,
            hot_paths_stable=True,
            participates_in_key_or_filter=False,
            reads_per_write=50,
            doc_is_wide=True,
        )
        is R.SHREDDED_VARIANT
    ), "文档很宽、少数路径被反复投影 → shredded VARIANT"


def test_variant_path_updates_refuse_overlapping_paths():
    """[a9] 05：「多路径共享一次规划；路径之间不能互为父子或重叠」。"""
    with pytest.raises(ValueError, match="互为父子或重叠"):
        V.variant_set_paths(object(), {"$.velocity": 1, "$.velocity.y": 2})


def test_pypaimon_absence_is_a_clear_message_not_an_import_error():
    """测试全程不装 pypaimon——缺库要给出可照着执行的修复建议，而不是裸 ImportError。"""
    pytest.importorskip("importlib")
    try:  # 真装了 pypaimon 的环境里这条断言没有意义，直接跳过
        __import__("pypaimon.data")
    except ImportError:
        pass
    else:  # pragma: no cover - 取决于环境
        pytest.skip("环境里装了 pypaimon，缺库分支跑不到")
    with pytest.raises(RuntimeError, match="pip install pypaimon"):
        V.variant_get_column(object(), "$.perception.weather", object())


# ============================================== 十一、公开 API 面（孤岛检查）


def test_every_public_name_is_reachable_from_the_package_root():
    """本包对外只有一个入口 ``adas_lakehouse.vector``——检索编排方按名字取用。

    导出表是「接线」的凭证：一个函数既没人调用、又不在 __all__ 里，就是孤岛。
    """
    for name in V.__all__:
        assert hasattr(V, name), f"__all__ 里的 {name} 取不到"
    must_export = {
        # 以图搜图这条路径上的每一环
        "RetrievalMode",
        "SearchRequest",
        "VectorSearchService",
        "ClipQueryEncoder",
        "as_query_encoder",
        "index_path_for",
        "render_search_sql",
        "render_enrich_sql",
        # 索引治理
        "dual_indexes",
        "render_create_index_ddl",
        "render_drop_index_ddl",
        "render_partition_refresh_sql",
        "validate_partition_value",
        "IndexService",
        # 版本并存与灰度
        "render_activate_sql",
        "render_deprecate_sql",
        "render_rollback_sql",
        "render_active_filter",
        "validate_embedding_version",
        # 流水线
        "render_upsert_sql",
        "render_mark_refreshed_sql",
        "resume_key_for",
        # Variant
        "variant_get_column",
        "variant_set_paths",
        "to_variant_array",
        "render_variant_get",
    }
    assert must_export <= set(V.__all__)


def test_the_whole_package_imports_without_a_starrocks_driver_or_pypaimon():
    """没装 pymysql / pypaimon 也要能 import 本包、渲染全部 SQL、跑 dry-run。"""
    import importlib

    mod = importlib.import_module("adas_lakehouse.vector.render_sql")
    rendered = mod.render_all()
    assert len(rendered) == 4
    starrocks = next(v for k, v in rendered.items() if k.name == "starrocks_vector.sql")
    assert "CREATE EXTERNAL CATALOG" in starrocks
    assert '"M" = "16"' in starrocks and '"efconstruction" = "200"' in starrocks
    assert V.IMAGE_INDEX_NAME in starrocks and V.TEXT_INDEX_NAME in starrocks
    assert "P95 ≤ 2.0 秒" in starrocks
