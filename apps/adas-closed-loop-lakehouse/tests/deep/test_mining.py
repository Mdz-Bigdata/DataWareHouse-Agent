"""mining 深度审计：CAN 减速度阈值、规则挖掘引擎、VLM 补语义级长尾标签。

与 ``tests/smoke/test_mining.py`` 的分工：
  · 冒烟那份测「跑得通」——规则能编译、SQL 能渲染、列契约对得上 registry。
  · 这份测「逐字 + 真能用」——原文给的每个数字一个字不许改；原文唯一给了阈值的那条
    规则必须**真的能求值**（-3.9 不触发 / -4.1 持续 0.6s 触发 / -4.1 只持续 0.4s 不
    触发）；批流双模、结果双写、补抽帧联动、断点续跑的**异常路径**都要被打到，
    而不只是 happy path。

判据来源（引用一律原话，标篇号以便回查）：
  [S3-04] 系列三 · 数据挖掘与 AI 第 4 篇《数据闭环规则挖掘引擎实战：从结构化元数据中
          批量发现高价值场景》（2026-09-12）——本模块主线。原文给出的全部硬数字：
            · 「CAN 减速度 < -4m/s² 持续 ≥ 0.5s 等，准实时」（二、六大种类表「车辆信号」行）
            · 「消费回传触发事件流，含前 15 后 5 秒窗口，准实时」（同表「事件触发」行）
            · 「亿级以下数据 4 小时内跑完」（三、批流双模）
            · 「规则按条件来源分六大种类」（二）
            · 「规则先验粗筛 → 模型不确定性细筛 → 检索相似性扩散」（四）
  [S3-03] 同系列第 3 篇《统一标签体系设计》（2026-09-11）——三源收口、CAPTION 特殊类别、
          血缘五字段、「未审核标签不得进入训练集圈选」。
  [S3-02] 同系列第 2 篇《分层抽帧策略》——「每 clip 打分选 1~5 关键帧」。
  [S3-01] 同系列第 1 篇《数据挖掘平台架构设计》——11 张新增表、成本分级、OpenAPI 路径。
  ⚠️ [S3-05]《VLM 推理挖掘》本项目未取得原文；VLM 段只断言能从上面四篇推出来的部分，
     批大小 / 重试次数 / 提示词措辞这些本项目自定的参数**不当成原文断言**。
"""

from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from adas_lakehouse import mining as M
from adas_lakehouse.catalog import registry
from adas_lakehouse.domains import DataDomain, Layer
from adas_lakehouse.mining import backends as B
from adas_lakehouse.mining import compiler as C
from adas_lakehouse.mining import config_sync as CS
from adas_lakehouse.mining import constants as K
from adas_lakehouse.mining import executor as E
from adas_lakehouse.mining import gaps as G
from adas_lakehouse.mining import rules as R
from adas_lakehouse.mining import scoring as S
from adas_lakehouse.mining import tables as T
from adas_lakehouse.mining import vlm as V
from adas_lakehouse.mining import watermark as W

RUN_ID = "run_mining_20260912000000_deadbeef"
DATA_ID = "COLLECT_BP_20260301123045_b7e2"
NOW = datetime(2026, 9, 12, 10, 0, 0)
LOW = datetime(2026, 9, 11, 10, 0, 0)

MINING_SRC = Path(M.__file__).resolve().parent


# =========================================================== 假件（不连任何外部服务）


class RoutingBackend:
    """按 SQL 形状分流的假后端：COUNT 走计数，其余走候选行。"""

    name = "routing"

    def __init__(self, *, hit_count: int = 0, rows: list[dict] | None = None) -> None:
        self.hit_count = hit_count
        self.rows = rows or []
        self.queries: list[str] = []
        self.executed: list[str] = []

    def query(self, sql: str) -> list[dict]:
        self.queries.append(sql)
        if "COUNT(1) AS hit_count" in sql:
            return [{"hit_count": self.hit_count}]
        return [dict(r) for r in self.rows]

    def execute(self, sql: str) -> int:
        self.executed.append(sql)
        return len(self.rows)


class ExplodingResultSink(B.ResultSink):
    """写结果表时炸掉——用来验「水位必须在结果落表之后才提交」。"""

    def write_results(self, rows):
        raise B.BackendError("模拟 Paimon 写失败")


class ExplodingTagSink(V.ModelTagSink):
    """统一标签服务写不进去——用来验「断点不能在写库失败时打上」。"""

    def __init__(self) -> None:
        self.calls = 0

    def write_tags(self, requests):
        self.calls += 1
        raise B.BackendError("模拟统一标签服务不可用")


class CountingBackfill(B.FrameBackfillDispatcher):
    def __init__(self) -> None:
        self.received: list[B.BackfillRequest] = []

    def dispatch(self, requests) -> int:
        self.received.extend(requests)
        return len(requests)


class ExplodingBackfill(B.FrameBackfillDispatcher):
    def dispatch(self, requests) -> int:
        raise B.BackendError("模拟抽帧引擎交接表不可写")


def _samples(
    values, *, step_ms: int = 100, data_id: str = DATA_ID, signal: str = "", start: datetime = NOW
) -> list[R.SignalSample]:
    """按固定采样间隔造一串 CAN 采样。

    ``values`` 里的每个元素是 ``float``（该采样点的实测值）或 ``(signal_name, value)``
    二元组（用来在序列里插入别的信号，验证预过滤）。
    """
    sig = signal or R.harsh_deceleration_condition().signal
    out: list[R.SignalSample] = []
    for i, item in enumerate(values):
        name, value = item if isinstance(item, tuple) else (sig, item)
        out.append(
            R.SignalSample(
                data_id=data_id,
                signal_name=name,
                signal_value=value,
                event_time=start + timedelta(milliseconds=i * step_ms),
                event_ts_ms=i * step_ms,
                vehicle_code="BP-0001",
                project_code="PRJ-A",
            )
        )
    return out


def _rule(rule_id: str, **kw) -> R.RuleDefinition:
    kw.setdefault("rule_name", "测试规则")
    kw.setdefault("rule_type", R.RuleType.TAG_COMBINATION)
    kw.setdefault("rule_status", R.RuleStatus.ENABLED)
    kw.setdefault("scene_label", "test_scene")
    kw.setdefault("visual_config", R.TagCondition("weather", ("rain",)))
    return R.RuleDefinition(rule_id=rule_id, **kw)


def _sample(rule_id: str) -> R.RuleDefinition:
    return next(r for r in R.SAMPLE_RULES if r.rule_id == rule_id)


HARSH_DECEL_RULE = _sample("RULE_SIGNAL_HARSH_DECEL")
TAKEOVER_RULE = _sample("RULE_EVENT_DRIVER_TAKEOVER")
NIGHT_RAIN_BRAKE_RULE = _sample("RULE_COMPOSITE_NIGHT_RAIN_BRAKE")


def _watermark(rule_id: str, *, layer: Layer = Layer.DWD) -> W.Watermark:
    return W.Watermark(
        rule_id=rule_id,
        table=T.DWD_COLLECT_CLIP_DETAIL.resolve(),
        column=W.watermark_column(layer),
        low=LOW,
        high=NOW,
    )


# ====================================================== 一、原文参数逐字（判据 A）


def test_harsh_decel_threshold_is_the_source_literal_minus_four():
    """[S3-04] 二「车辆信号」行：「CAN 减速度 < -4m/s²」——不许四舍五入成 -4.5 之类。"""
    assert K.HARSH_DECEL_THRESHOLD_MPS2 == -4.0
    assert isinstance(K.HARSH_DECEL_THRESHOLD_MPS2, float)
    cond = R.harsh_deceleration_condition()
    assert cond.threshold == K.HARSH_DECEL_THRESHOLD_MPS2
    # 原文写的是「<」不是「<=」：严格小于
    assert cond.op is R.CompareOp.LT
    assert cond.op.sql == "<"


def test_harsh_decel_duration_is_the_source_literal_half_second():
    """[S3-04] 二同一行：「持续 ≥ 0.5s」——是「≥」，也就是恰好 0.5 秒算命中。"""
    assert K.HARSH_DECEL_MIN_DURATION_SEC == 0.5
    assert R.harsh_deceleration_condition().min_duration_sec == 0.5


def test_event_window_is_fifteen_before_five_after():
    """[S3-04] 二「事件触发」行 + 三隐藏联动：「前 15 后 5 秒窗口」。"""
    assert K.EVENT_WINDOW_BEFORE_SEC == 15
    assert K.EVENT_WINDOW_AFTER_SEC == 5
    assert K.EVENT_WINDOW_TOTAL_SEC == 20
    assert S.event_window_seconds() == 20
    evt = R.EventCondition(trigger_types=("driver_takeover",))
    # 窗口不可配置：init=False，构造参数里改不了
    assert evt.window_before_sec == 15 and evt.window_after_sec == 5
    with pytest.raises(TypeError):
        R.EventCondition(trigger_types=("x",), window_before_sec=30)  # type: ignore[call-arg]


def test_batch_sla_is_one_hundred_million_rows_within_four_hours():
    """[S3-04] 三：「亿级以下数据 4 小时内跑完」。亿 = 1e8，4 小时 = 14400 秒。"""
    assert K.BATCH_SCAN_ROW_CEILING == 100_000_000
    assert K.BATCH_SLA_HOURS == 4
    assert K.BATCH_SLA_SECONDS == 14_400


def test_rule_kinds_are_exactly_six():
    """[S3-04] 二标题：「六大种类」。多一类少一类都不是原文那张表。"""
    assert K.RULE_TYPE_COUNT == 6
    assert len(list(R.RuleType)) == 6


def test_funnel_is_the_three_source_stages_in_order():
    """[S3-04] 四：「规则先验粗筛 → 模型不确定性细筛 → 检索相似性扩散」。"""
    assert K.FUNNEL_STAGES == ("规则先验粗筛", "模型不确定性细筛", "检索相似性扩散")


def test_funnel_position_is_written_into_the_generated_sql():
    """漏斗位置要写进渲染出来的 SQL 注释头——湖仓里躺着的 SQL 是唯一还能回读的东西。"""
    batch = C.RuleCompiler().compile_batch(
        _sample("RULE_TAG_RAINY_HIGHWAY"), _watermark("RULE_TAG_RAINY_HIGHWAY"), run_id=RUN_ID
    )
    assert K.FUNNEL_STAGES[0] in batch.select_sql

    engine = V.VlmInferenceEngine(client=V.EchoVlmClient(), tag_sink=V.InMemoryModelTagSink())
    assert K.FUNNEL_STAGES[1] in engine.candidate_sql()


def test_keyframe_bounds_agree_with_sampling():
    """constants.py 承诺由本用例钉住：mining 与 sampling 各持一份 1~5，必须相等。

    两个子系统刻意不互相 import（controlplane/subsystems.py 的硬约束：子系统之间
    零耦合，只经 Paimon 表交接），所以一致性只能用测试对账。
    """
    from adas_lakehouse.sampling import constants as SC

    assert (K.INFERENCE_MIN_KEYFRAMES, K.INFERENCE_MAX_KEYFRAMES) == (1, 5)
    assert K.INFERENCE_MIN_KEYFRAMES == SC.INFERENCE_MIN_KEYFRAMES
    assert K.INFERENCE_MAX_KEYFRAMES == SC.INFERENCE_MAX_KEYFRAMES


def test_cost_tier_constants_agree_with_the_control_plane():
    """constants.py 承诺由本用例钉住：成本分级那几个数字与控制面那份必须一致。

    [S3-01] 五：「Embedding 走凌晨窗口（凌晨 6 点前完成）」「高价值数据（规则命中 /
    事件抽帧 / VLM 标签）优先向量化」「每服务 4C8G × 2 起」。
    """
    from adas_lakehouse.controlplane import constants as CPK

    assert K.EMBEDDING_WINDOW_DEADLINE_HOUR == 6 == CPK.EMBEDDING_WINDOW_DEADLINE_HOUR
    assert K.HIGH_VALUE_SOURCES == ("规则命中", "事件抽帧", "VLM 标签")
    assert (K.SERVICE_CPU_CORES, K.SERVICE_MEMORY_GB, K.SERVICE_MIN_REPLICAS) == (4, 8, 2)
    assert K.SERVICE_CPU_CORES == CPK.ONLINE_SERVICE_CPU_CORES
    assert K.SERVICE_MEMORY_GB == CPK.ONLINE_SERVICE_MEMORY_GB
    assert K.SERVICE_MIN_REPLICAS == CPK.ONLINE_SERVICE_MIN_REPLICAS


def test_the_two_kinds_of_source_are_not_conflated():
    """「规则命中」是成本分级口径（[S3-01] 五），``rule`` 才是标签表的 tag_source 取值。

    两者混用的后果是双份的：统一标签服务按三来源枚举校验会直接拒收；就算收了，
    湖仓里也落下一个取值域外的字符串，按 tag_source 做的一切统计从此少一块。
    """
    req = B.TagWriteRequest(
        data_id=DATA_ID, scene_label="x", rule_id="R", rule_version=1, run_id=RUN_ID, value_score=1
    )
    assert req.high_value_source == K.HIGH_VALUE_SOURCES[0] == "规则命中"
    assert req.tag_source == B.TAG_SOURCE_RULE == "rule"

    payload = req.to_payload()
    assert payload["source"] == "rule"
    assert payload["high_value_source"] == "规则命中"
    # registry 的取值域：collect 采集/rule 规则/vlm 模型
    comment = next(
        c.comment
        for c in registry.by_name("dwd_mining_data_tag_detail").all_columns()
        if c.name == "tag_source"
    )
    assert "rule" in comment and payload["source"] in comment
    assert K.VLM_TAG_SOURCE in comment  # VLM 那半同理
    assert payload["high_value_source"] not in comment  # 成本分级口径不该落进这一列


def test_tag_payload_speaks_the_tag_services_vocabulary():
    """backends.py 承诺由本用例钉住：打标 payload 的键名与统一标签服务的公开入参同名。

    mining **不 import tags**（子系统之间零耦合），所以只能用测试对账：键名对齐之后，
    外部那层 adapter 把 payload 翻成 RawTag 是一次机械的同名映射，中间不需要翻译表。
    """
    from adas_lakehouse.tags.records import RawTag
    from adas_lakehouse.tags.sources import TagSource

    payload = B.TagWriteRequest(
        data_id=DATA_ID,
        scene_label="night_rain_harsh_brake",
        rule_id="RULE_X",
        rule_version=3,
        run_id=RUN_ID,
        value_score=88.0,
    ).to_payload()

    raw_tag_fields = set(RawTag.__dataclass_fields__)
    # 与 RawTag 同名的那些键必须真的同名
    assert {"raw_tag", "source", "data_id", "rule_id", "rule_version", "confidence"} <= set(payload)
    assert {
        "raw_tag",
        "source",
        "data_id",
        "rule_id",
        "rule_version",
        "confidence",
    } <= raw_tag_fields
    # 其余几个键是挖掘侧的附加信息，落 RawTag.extra，不许冒充 RawTag 的字段
    extras = set(payload) - raw_tag_fields
    # event_time 是命中的**事件时刻**，与 RawTag.tag_time（打标时刻）不是一回事，
    # 所以它也走 extra，不许硬塞进同名字段
    assert extras == {"run_id", "value_score", "high_value_source", "event_time"}
    # 取值也要在三来源枚举里，否则 profile_for 会拒收
    assert payload["source"] == TagSource.RULE.value
    # 类型口径：registry 的 rule_version 是 STRING
    assert isinstance(payload["rule_version"], str) and payload["rule_version"] == "3"
    # 规则是确定性判定，置信度恒为 1.0（registry 该列注释）
    assert payload["confidence"] == B.RULE_TAG_CONFIDENCE == 1.0


def test_mining_new_tables_reconcile_with_the_registry():
    """[S3-01] 二：「共 11 张表（1 ODS + 8 DWD + 1 DWS + 1 ADS）」——数字与名单都要对上。"""
    assert K.MINING_TABLE_COUNT == 11
    assert K.MINING_TABLE_COUNT_BY_LAYER == {"ods": 1, "dwd": 8, "dws": 1, "ads": 1}

    flat = [n for names in T.MINING_NEW_TABLES.values() for n in names]
    assert len(flat) == 11 and len(set(flat)) == 11
    for layer, names in T.MINING_NEW_TABLES.items():
        assert len(names) == K.MINING_TABLE_COUNT_BY_LAYER[layer]
        for name in names:
            spec = registry.by_name(name)  # 没登记就是 KeyError
            assert spec.layer.value == layer
            assert spec.domain is DataDomain.MINING


def test_the_engine_reuses_the_collect_clip_table_instead_of_copying_it():
    """[S3-01] 二：「平台直接复用采集域既有的 dwd_collect_clip_detail，而不是复制一份自己的」。"""
    base = C.default_batch_plan().base
    assert base.ref is T.DWD_COLLECT_CLIP_DETAIL
    assert T.DWD_COLLECT_CLIP_DETAIL.role == "read"
    assert T.DWD_COLLECT_CLIP_DETAIL.name not in [
        n for names in T.MINING_NEW_TABLES.values() for n in names
    ]


def test_openapi_paths_are_the_source_literals():
    """[S3-01] 六接口表：任务类 POST /api/v1/mining/rule-jobs + GET /jobs/{jobId}/progress；
    检索类 GET /api/v1/scene/tag-coverage。"""
    assert K.RULE_JOBS_API_PATH == "/api/v1/mining/rule-jobs"
    assert K.RULE_JOB_PROGRESS_API_PATH == "/api/v1/mining/rule-jobs/{jobId}/progress"
    assert K.TAG_COVERAGE_API_PATH == "/api/v1/scene/tag-coverage"

    assert B.rule_job_endpoint("https://gw/") == "https://gw/api/v1/mining/rule-jobs"
    assert (
        B.rule_job_progress_endpoint("https://gw", "job-7")
        == "https://gw/api/v1/mining/rule-jobs/job-7/progress"
    )
    assert G.tag_coverage_endpoint("https://gw") == "https://gw/api/v1/scene/tag-coverage"
    with pytest.raises(ValueError, match="job_id"):
        B.rule_job_progress_endpoint("https://gw", "")


def test_vlm_output_and_caption_constants_are_source_literals():
    """[S3-04] 结尾预告「双输出（标签 + caption）」；[S3-03] 二「tag_category=CAPTION」。"""
    assert K.VLM_OUTPUT_KINDS == ("标签", "caption")
    assert K.VLM_CAPTION_TAG_CATEGORY == "CAPTION"
    assert K.VLM_TAG_SOURCE == "vlm"
    assert K.VLM_TASK_TYPE == "vlm_infer"
    assert K.VLM_INFER_ENGINE == "ray" and K.VLM_INFER_SERVING_RUNTIME == "vllm"
    assert K.VLM_LONG_TAIL_EXAMPLES == ("施工区锥桶摆放混乱", "行人撑着花伞")
    assert K.VLM_REQUIRED_LINEAGE_FIELDS == (
        "tag_source",
        "model_name",
        "model_version",
        "confidence",
        "infer_job_id",
    )


# ============================ 二、★CAN 急减速：阈值 -4 m/s² / 持续 ≥ 0.5s 的求值★


def test_minus_3_9_never_triggers_however_long_it_lasts():
    """喂 -3.9 m/s²：差 0.1 也是不到阈值，持续一整秒也不许命中。"""
    cond = R.harsh_deceleration_condition()
    hits = R.sustained_matches(cond, _samples([-3.9] * 11 + [0.0]))
    assert hits == ()


def test_minus_4_1_sustained_600ms_triggers():
    """喂 -4.1 m/s² 持续 0.6s（> 0.5s）：命中一段，峰值与时长都要对得上。"""
    cond = R.harsh_deceleration_condition()
    hits = R.sustained_matches(cond, _samples([-4.1] * 7 + [0.0]))
    assert len(hits) == 1
    hit = hits[0]
    assert hit.duration_sec == pytest.approx(0.6)
    assert hit.duration_sec >= K.HARSH_DECEL_MIN_DURATION_SEC
    assert hit.peak_value == -4.1
    assert hit.sample_count == 7
    assert hit.data_id == DATA_ID
    assert hit.closed is True


def test_minus_4_1_sustained_only_400ms_does_not_trigger():
    """喂 -4.1 m/s² 但只持续 0.4s（< 0.5s）：超阈了也不算，原文要的是「持续」。"""
    cond = R.harsh_deceleration_condition()
    assert R.sustained_matches(cond, _samples([-4.1] * 5 + [0.0])) == ()


def test_exactly_minus_four_does_not_trigger_because_the_source_says_strict_less_than():
    """恰好 -4.0：原文写的是「<」而不是「≤」，所以不触发。"""
    cond = R.harsh_deceleration_condition()
    assert cond.matches_point(-4.0) is False
    assert cond.matches_point(-4.000001) is True
    assert R.sustained_matches(cond, _samples([-4.0] * 11 + [0.0])) == ()


def test_exactly_500ms_triggers_because_the_source_says_greater_or_equal():
    """恰好 0.5s：原文写的是「≥ 0.5s」，所以要触发。这是与上一条对称的边界。"""
    cond = R.harsh_deceleration_condition()
    hits = R.sustained_matches(cond, _samples([-4.1] * 6 + [0.0]))
    assert len(hits) == 1
    assert hits[0].duration_sec == pytest.approx(0.5)
    # 差一个采样点（0.4s）就不该命中——上下界各钉一次，防「>= 写成 >」
    assert R.sustained_matches(cond, _samples([-4.1] * 5 + [0.0])) == ()


def test_a_missing_or_nan_sample_is_never_a_hit():
    """缺值不是命中：宁可漏，也不能凭空造一次急减速。"""
    cond = R.harsh_deceleration_condition()
    assert cond.matches_point(None) is False
    assert cond.matches_point(float("nan")) is False
    assert R.sustained_matches(cond, _samples([None] * 11 + [0.0])) == ()


def test_an_unclosed_run_waits_for_the_closing_sample():
    """流侧 ``PATTERN (A+ B)`` 必须等到「不再满足」的那条采样才出匹配。

    Python 侧默认与流一致（还没收尾就还没出结果）；离线复算一段录完的信号时
    传 emit_open_run=True 才把尾段吐出来，并标 closed=False。
    """
    cond = R.harsh_deceleration_condition()
    open_run = _samples([-4.1] * 7)  # 没有恢复采样收尾
    assert R.sustained_matches(cond, open_run) == ()
    tail = R.sustained_matches(cond, open_run, emit_open_run=True)
    assert len(tail) == 1 and tail[0].closed is False
    assert tail[0].duration_sec == pytest.approx(0.6)


def test_another_signal_interleaved_does_not_split_the_run():
    """信号流是多信号共用一条：别的信号插进来不许把一段急减速切成两截。

    这正是编译器把 ``WHERE signal_name = ...`` 放在 MATCH_RECOGNIZE **之前**的理由。
    """
    cond = R.harsh_deceleration_condition()
    values: list = [-4.1, -4.2, ("can_lateral_accel_mps2", 9.9), -4.3, -4.4, -4.5, -4.6, 0.0]
    hits = R.sustained_matches(cond, _samples(values))
    assert len(hits) == 1
    assert hits[0].sample_count == 6  # 6 条减速度采样，横向加速度那条不计入
    # 预过滤把那条别的信号整行拿掉，这一段仍是 0ms -> 600ms 的一整段，没被切开
    assert hits[0].duration_sec == pytest.approx(0.6)


def test_two_runs_separated_by_a_recovery_are_two_hits():
    """AFTER MATCH SKIP PAST LAST ROW：恢复之后再次超阈算新的一段。"""
    cond = R.harsh_deceleration_condition()
    values = [-4.5] * 6 + [0.0] + [-5.0] * 6 + [0.0]
    hits = R.sustained_matches(cond, _samples(values))
    assert len(hits) == 2
    assert [h.peak_value for h in hits] == [-4.5, -5.0]
    assert hits[0].sustain_end_ms < hits[1].sustain_start_ms


def test_hits_are_partitioned_by_data_id():
    """PARTITION BY data_id：两条 clip 的采样不许被拼成一段。"""
    cond = R.harsh_deceleration_condition()
    a = _samples([-4.5] * 3, data_id="COLLECT_BP_20260301123045_aaaa")
    b = _samples([-4.5] * 3, data_id="COLLECT_BP_20260301123045_bbbb")
    # 单看任一 clip 都只有 0.2s，拼起来才够 0.5s——不许拼
    assert R.sustained_matches(cond, a + b, emit_open_run=True) == ()


def test_peak_value_points_at_the_most_severe_sample_in_both_directions():
    """峰值要往超阈的那一侧取：``<`` 取最小、``>`` 取最大。

    取反了的后果是严重度打分把最轻的采样当成最严重（scoring.signal_severity）。
    """
    decel = R.harsh_deceleration_condition()
    hits = R.sustained_matches(decel, _samples([-4.2, -6.0, -4.1, -4.3, -4.9, -4.4, 0.0]))
    assert decel.peak_aggregate == "MIN"
    assert hits[0].peak_value == -6.0

    lateral = R.SignalCondition(
        signal="can_lateral_accel_mps2", op=R.CompareOp.GT, threshold=4.0, min_duration_sec=0.5
    )
    up = _samples([4.2, 7.5, 4.1, 4.3, 5.0, 4.4, 0.0], signal="can_lateral_accel_mps2")
    lateral_hits = R.sustained_matches(lateral, up)
    assert lateral.peak_aggregate == "MAX"
    assert lateral_hits[0].peak_value == 7.5


def test_severity_scores_the_peak_against_the_source_threshold():
    """scoring 的严重度是「超阈幅度 / 阈值」：-4 → 0、-6 → 0.5、-8 及以上 → 1.0 封顶。"""
    cond = R.harsh_deceleration_condition()
    assert S.signal_severity(cond, -4.0) == 0.0
    assert S.signal_severity(cond, -6.0) == pytest.approx(0.5)
    assert S.signal_severity(cond, -8.0) == 1.0
    assert S.signal_severity(cond, -20.0) == 1.0  # 封顶，不外溢
    assert S.signal_severity(cond, None) == 0.0
    assert S.signal_severity(None, -6.0) == 0.0


def test_sustained_matches_rejects_a_non_positive_duration():
    with pytest.raises(R.RuleValidationError, match="持续时长"):
        R.SignalCondition(signal="s", op=R.CompareOp.LT, threshold=-4.0, min_duration_sec=0.0)


def test_signal_sample_fields_match_the_stream_contract():
    """rules.py 承诺由本用例钉住：SignalSample 的字段 ⊆ vehicle_signal_stream 的列契约。

    两边分叉的后果是 Python 侧求值读得到、Flink 侧作业提交时报 column not found。
    """
    fields = set(R.SignalSample.__dataclass_fields__)
    assert fields <= set(T.VEHICLE_SIGNAL_STREAM.columns), (
        f"SignalSample 多出流契约没有的字段 {fields - set(T.VEHICLE_SIGNAL_STREAM.columns)}"
    )
    # 亚秒判定必须有毫秒列——Flink 的 TIMESTAMPDIFF 只到秒，判不了 0.5s
    assert "event_ts_ms" in T.VEHICLE_SIGNAL_STREAM.columns
    assert "signal_name" in fields and "signal_value" in fields


def test_ts_ms_falls_back_to_event_time_when_the_stream_omits_it():
    s = R.SignalSample(
        data_id=DATA_ID, signal_name="x", signal_value=-5.0, event_time=NOW, event_ts_ms=None
    )
    assert s.ts_ms == int(NOW.timestamp() * 1000)


# ---------------------------------------------- 急减速规则「扫的是哪张源」不许退化


def test_the_can_signal_source_is_a_declared_stream_not_an_unregistered_table():
    """历史问题钉死：RULE_SIGNAL_HARSH_DECEL 曾扫描未登记的 vehicle_signal_stream。

    现在的处理是**显式声明成外部流源**（StreamRef，不是 TableRef）：
      · 它不在 registry 的湖仓表里，也不许伪装成 ods_/dwd_/dws_/ads_ 开头的表名；
      · 它的列契约写在 tables.VEHICLE_SIGNAL_STREAM.columns 上，编译期据此做列检查；
      · FROM 片段是单段标识符，不套 Paimon 三段式前缀（套了反而找不到）。
    三条任何一条退化，这条原文唯一带数字的规则就又变成「扫一张不存在的表」。
    """
    stream = T.VEHICLE_SIGNAL_STREAM
    assert isinstance(stream, T.StreamRef) and not isinstance(stream, T.TableRef)
    assert stream.resolve() == "vehicle_signal_stream"
    assert not stream.resolve().startswith(("ods_", "dwd_", "dws_", "ads_"))
    with pytest.raises(KeyError):
        registry.by_name(stream.resolve())  # 不是湖仓表，registry 里查不到才对

    query = C.RuleCompiler().compile_stream(HARSH_DECEL_RULE, run_id=RUN_ID, now=NOW)
    assert query.scan_tables == ("vehicle_signal_stream",)
    assert "FROM `vehicle_signal_stream`" in query.select_sql
    assert "`paimon`" not in query.select_sql.split("MATCH_RECOGNIZE")[0].split("FROM")[-1]


def test_every_scan_source_is_either_registered_or_a_declared_stream():
    """六大种类的示例规则，扫描源只有两种合法身份：registry 的表，或声明过的流。"""
    known = {t.name for t in registry.all_tables()} | {T.VEHICLE_SIGNAL_STREAM.resolve()}
    compiler = C.RuleCompiler()
    for rule in R.SAMPLE_RULES:
        if rule.effective_mode is R.ExecutionMode.BATCH_T_PLUS_1:
            q = compiler.compile_batch(rule, _watermark(rule.rule_id), run_id=RUN_ID, now=NOW)
        else:
            q = compiler.compile_stream(rule, run_id=RUN_ID, now=NOW)
        for table in q.scan_tables:
            assert table in known, f"{rule.rule_id} 扫了未登记的源 {table!r}"


def test_stream_source_selection_follows_the_condition_source_not_the_duration():
    """CAN 信号一律读信号流，带不带「持续时长」只决定怎么匹配，不决定读哪个源。

    原文「车辆信号」行的两个示例是「急减速 / 急变道」：后者是瞬时尖峰、并不带持续时长，
    若按「有无时长」分流，它会被送去扫 ods_vehicle_trigger_event——那张表里没有
    signal_name / signal_value，作业提交时才报 column not found。
    """
    instant = _rule(
        "RULE_SIGNAL_LANE_CHANGE",
        rule_name="急变道",
        rule_type=R.RuleType.VEHICLE_SIGNAL,
        scene_label="harsh_lane_change",
        visual_config=R.SignalCondition(
            signal="can_lateral_accel_mps2", op=R.CompareOp.GT, threshold=4.0
        ),
    )
    q = C.RuleCompiler().compile_stream(instant, run_id=RUN_ID, now=NOW)
    assert q.scan_tables == ("vehicle_signal_stream",)
    assert "MATCH_RECOGNIZE" not in q.select_sql  # 没有持续时长就不需要模式匹配


def test_event_rules_read_the_registered_trigger_event_table():
    """事件触发类读的是回传事件表，事件时刻列是 registry 的 trigger_time 不是 event_time。"""
    q = C.RuleCompiler().compile_stream(TAKEOVER_RULE, run_id=RUN_ID, now=NOW)
    assert q.scan_tables == ("ods_vehicle_trigger_event",)
    assert "`trigger_time`" in q.select_sql
    assert C.TRIGGER_EVENT_TIME_COLUMN == "trigger_time"
    assert "trigger_time" in T.columns_of(T.ODS_VEHICLE_TRIGGER_EVENT)


def test_stream_compile_fails_loudly_when_the_source_lacks_a_column():
    """流路的列检查必须是硬失败：长驻作业提交失败只在网关日志里留一行，没人盯着。"""
    bad = _rule(
        "RULE_MODEL_NO_SUCH_COLUMN",
        rule_type=R.RuleType.MODEL_OUTPUT,
        execution_mode=R.ExecutionMode.NEAR_REALTIME,
        visual_config=R.ModelOutputCondition(output_column="aeb_triggered", value=True),
    )
    with pytest.raises(C.CompileError, match="没有这些列"):
        C.RuleCompiler().compile_stream(bad, run_id=RUN_ID, now=NOW)


def test_model_output_rules_may_run_near_realtime_on_the_event_stream():
    """原文「模型输出」行是唯一双模式的种类：「T+1 批或准实时」。准实时那条腿要真能编。"""
    rt = _rule(
        "RULE_MODEL_AEB_REALTIME",
        rule_name="AEB 触发",
        rule_type=R.RuleType.MODEL_OUTPUT,
        execution_mode=R.ExecutionMode.NEAR_REALTIME,
        scene_label="aeb_triggered",
        visual_config=R.ModelOutputCondition(
            output_column="trigger_type", op=R.CompareOp.EQ, value="aeb"
        ),
    )
    q = C.RuleCompiler().compile_stream(rt, run_id=RUN_ID, now=NOW)
    assert q.scan_tables == ("ods_vehicle_trigger_event",)
    assert q.execution_mode is R.ExecutionMode.NEAR_REALTIME


def test_match_recognize_keeps_the_two_source_numbers_verbatim():
    """编译出来的 SQL 里，-4.0 与 0.5 必须逐字在，0.5 不许被预先算成 500。"""
    sql = C.harsh_decel_reference_sql()
    assert "-4.0" in sql
    assert "(0.5 * 1000)" in sql
    assert ">= (0.5 * 1000)" in sql  # 「≥」不是「>」
    assert "500" not in sql.replace("(0.5 * 1000)", "")
    assert "MATCH_RECOGNIZE" in sql and "PATTERN (A+ B)" in sql
    assert "AFTER MATCH SKIP PAST LAST ROW" in sql


def test_match_recognize_prefilters_by_signal_name_before_matching():
    """预过滤必须在 MATCH_RECOGNIZE 之前——否则 A+ 会被别的信号打断（见上面的求值用例）。"""
    sql = C.harsh_decel_reference_sql()
    # 注释头里也有「MATCH_RECOGNIZE」字样，所以按真正的子句开头切
    cut = sql.index("MATCH_RECOGNIZE (")
    head, tail = sql[:cut], sql[cut:]
    assert "`signal_name` = 'can_longitudinal_accel_mps2'" in head
    assert head.index("WHERE `evt`.`signal_name`") < cut
    assert "signal_name" not in tail.split("DEFINE")[1]  # DEFINE 里不再重复判信号名
    assert "MIN(A.`signal_value`)  AS `peak_value`" in tail  # `<` 规则取 MIN


def test_hit_reason_is_the_source_sentence():
    """registry 给 hit_reason 举的例子就是「CAN 减速度 < -4m/s² 持续 ≥ 0.5s」。"""
    reason = HARSH_DECEL_RULE.condition().describe()
    assert "< -4.0" in reason and "持续 ≥ 0.5s" in reason
    sql = C.harsh_decel_reference_sql()
    assert reason in sql


def test_stream_executor_evaluates_can_samples_end_to_end():
    """★真能求值★：一串 CAN 采样 → 命中 → 统一标签服务打标 → 异步补抽帧。

    这条链路不经 Flink，因此原文那组数字在纯 Python 环境下就能被验证；
    也让故障后按采样回放补命中成为可能。
    """
    tags = B.InMemoryTagService()
    backfill = CountingBackfill()
    ex = E.StreamRuleExecutor(
        backend=B.DryRunBackend(),
        task_sink=B.InMemoryResultSink(),
        tag_service=tags,
        backfill=backfill,
    )
    record = E.RuleRunRecord(
        task_id=f"{RUN_ID}_{HARSH_DECEL_RULE.rule_id}",
        run_id=RUN_ID,
        rule_id=HARSH_DECEL_RULE.rule_id,
        rule_version=HARSH_DECEL_RULE.rule_version,
        execution_mode=R.ExecutionMode.NEAR_REALTIME,
        executed_at=NOW,
    )
    hits = ex.evaluate_signal_samples(HARSH_DECEL_RULE, _samples([-4.1, -4.8] + [-4.2] * 5 + [0.0]))
    assert len(hits) == 1
    assert hits[0]["peak_value"] == -4.8

    tagged, dispatched = ex.handle_hits(
        HARSH_DECEL_RULE, hits, run_id=RUN_ID, now=NOW, record=record
    )
    assert tagged == 1
    assert dispatched == 1  # 车辆信号类也有明确时刻，同样触发补抽帧
    req = backfill.received[0]
    assert req.window_before_sec == 15 and req.window_after_sec == 5
    assert req.window_start_time == req.event_time - timedelta(seconds=15)
    assert req.window_end_time == req.event_time + timedelta(seconds=5)
    assert record.to_row()["frame_supplement_triggered"] is True
    assert record.to_row()["tag_write_count"] == 1


def test_evaluating_samples_against_the_wrong_kind_of_rule_is_refused():
    ex = E.StreamRuleExecutor(
        backend=B.DryRunBackend(),
        task_sink=B.InMemoryResultSink(),
        tag_service=B.InMemoryTagService(),
    )
    with pytest.raises(C.CompileError, match="信号采样求值只服务准实时规则"):
        ex.evaluate_signal_samples(_sample("RULE_TAG_RAINY_HIGHWAY"), [])
    with pytest.raises(C.CompileError, match="没有信号条件"):
        ex.evaluate_signal_samples(TAKEOVER_RULE, [])


def test_the_composite_rule_carries_the_same_decel_numbers():
    """原文「多条件复合」行的示例「夜间雨天急刹」= 标签 + 信号叠加，信号那半仍是 -4/0.5。"""
    cond = E.StreamRuleExecutor.signal_condition_of(NIGHT_RAIN_BRAKE_RULE)
    assert cond is not None
    assert cond.threshold == K.HARSH_DECEL_THRESHOLD_MPS2
    assert cond.min_duration_sec == K.HARSH_DECEL_MIN_DURATION_SEC
    # 复合规则走 T+1 批（原文「标签 + 信号多条件叠加，T+1 批」），批方言下持续时长下推 UDF
    assert NIGHT_RAIN_BRAKE_RULE.effective_mode is R.ExecutionMode.BATCH_T_PLUS_1
    sql = NIGHT_RAIN_BRAKE_RULE.where_sql(R.Dialect.SPARK, alias="clip")
    assert "mining_signal_sustained(" in sql
    assert "-4.0" in sql and "0.5" in sql


def test_the_composite_rule_compiles_on_a_clip_only_scan_plan():
    """批扫描计划只有 clip 基表；带时长的信号条件在批方言下只读 data_id，不该误报缺列。"""
    q = C.RuleCompiler().compile_batch(
        NIGHT_RAIN_BRAKE_RULE,
        _watermark(NIGHT_RAIN_BRAKE_RULE.rule_id),
        run_id=RUN_ID,
        now=NOW,
        strict=True,  # strict 下多报一列就直接抛
    )
    assert q.unresolved_columns == ()
    cond = E.StreamRuleExecutor.signal_condition_of(NIGHT_RAIN_BRAKE_RULE)
    assert cond is not None
    assert cond.referenced_columns(R.Dialect.SPARK) == {"data_id"}
    assert cond.referenced_columns(R.Dialect.FLINK) == {"signal_name", "signal_value"}


# ================================= 三、六大种类：与原文表格逐行对账（判据 A）


SOURCE_RULE_TABLE = {
    # 规则种类: (示例, 条件与执行, 允许的执行模式)
    R.RuleType.TAG_COMBINATION: (
        "标签组合",
        ("雨天高速", "夜间雾天"),
        "采集标签多字段 AND 组合，T+1 批",
        (R.ExecutionMode.BATCH_T_PLUS_1,),
    ),
    R.RuleType.SPATIOTEMPORAL: (
        "时空地理",
        ("城市行人场景", "通勤高峰"),
        "GPS 围栏 + 视角 + 时间段组合，T+1 批",
        (R.ExecutionMode.BATCH_T_PLUS_1,),
    ),
    R.RuleType.VEHICLE_SIGNAL: (
        "车辆信号",
        ("急减速", "急变道"),
        "CAN 减速度 < -4m/s² 持续 ≥ 0.5s 等，准实时",
        (R.ExecutionMode.NEAR_REALTIME,),
    ),
    R.RuleType.MODEL_OUTPUT: (
        "模型输出",
        ("AEB 触发", "行人险肇"),
        "模型信号直接引用，T+1 批或准实时",
        (R.ExecutionMode.BATCH_T_PLUS_1, R.ExecutionMode.NEAR_REALTIME),
    ),
    R.RuleType.EVENT_TRIGGER: (
        "事件触发",
        ("驾驶员接管",),
        "消费回传触发事件流，含前 15 后 5 秒窗口，准实时",
        (R.ExecutionMode.NEAR_REALTIME,),
    ),
    R.RuleType.COMPOSITE: (
        "多条件复合",
        ("夜间雨天急刹",),
        "标签 + 信号多条件叠加，T+1 批",
        (R.ExecutionMode.BATCH_T_PLUS_1,),
    ),
}


def test_six_rule_kinds_match_the_source_table_row_by_row():
    """[S3-04] 二那张表，一行一行对：种类名、示例、条件与执行、允许的执行模式。"""
    assert set(SOURCE_RULE_TABLE) == set(R.RuleType)
    for kind, (name_cn, examples, note, modes) in SOURCE_RULE_TABLE.items():
        spec = kind.spec
        assert spec.name_cn == name_cn
        assert spec.examples == examples
        assert spec.condition_note == note, f"{name_cn} 的「条件与执行」列与原文不符"
        assert spec.modes == modes
        assert kind.default_execution_mode is modes[0]


def test_execution_mode_follows_the_condition_source_not_a_blanket_rule():
    """[S3-04] 二：「执行模式跟着条件来源走——静态标签与时空条件走 T+1 批，
    车辆信号与事件流走准实时，不是一刀切」。"""
    batch_only = {R.RuleType.TAG_COMBINATION, R.RuleType.SPATIOTEMPORAL, R.RuleType.COMPOSITE}
    stream_only = {R.RuleType.VEHICLE_SIGNAL, R.RuleType.EVENT_TRIGGER}
    for kind in batch_only:
        assert kind.allows(R.ExecutionMode.BATCH_T_PLUS_1)
        assert not kind.allows(R.ExecutionMode.NEAR_REALTIME)
    for kind in stream_only:
        assert kind.allows(R.ExecutionMode.NEAR_REALTIME)
        assert not kind.allows(R.ExecutionMode.BATCH_T_PLUS_1)
    # 唯一双模式的是模型输出
    assert all(R.RuleType.MODEL_OUTPUT.allows(m) for m in R.ExecutionMode)


def test_a_rule_cannot_claim_a_mode_its_kind_forbids():
    """配错模式要在构造期就拒，不能等跑到错误的引擎上才发现。"""
    with pytest.raises(R.RuleValidationError, match="不支持执行模式"):
        _rule(
            "RULE_TAG_WRONG_MODE",
            rule_type=R.RuleType.TAG_COMBINATION,
            execution_mode=R.ExecutionMode.NEAR_REALTIME,
        )
    with pytest.raises(R.RuleValidationError, match="不支持执行模式"):
        _rule(
            "RULE_SIGNAL_WRONG_MODE",
            rule_type=R.RuleType.VEHICLE_SIGNAL,
            execution_mode=R.ExecutionMode.BATCH_T_PLUS_1,
            visual_config=R.harsh_deceleration_condition(),
        )


def test_dialect_follows_the_execution_mode():
    """[S3-01] 五的技术选型：批走 Spark、流走 Flink，方言不能混。"""
    assert R.ExecutionMode.BATCH_T_PLUS_1.dialect is R.Dialect.SPARK
    assert R.ExecutionMode.NEAR_REALTIME.dialect is R.Dialect.FLINK
    assert R.ExecutionMode.BATCH_T_PLUS_1.label_cn == "T+1 批"
    assert R.ExecutionMode.NEAR_REALTIME.label_cn == "准实时"


def test_sample_rules_cover_every_kind_and_both_expression_modes():
    """六大种类各有示例，且 SQL 与可视化两种表达都有样例——原文说两条路等价。"""
    kinds = {r.rule_type for r in R.SAMPLE_RULES}
    assert kinds == set(R.RuleType)
    modes = {r.expression_mode for r in R.SAMPLE_RULES}
    assert modes == {R.ExpressionMode.SQL, R.ExpressionMode.VISUAL}


def test_the_five_condition_sources_of_the_source_text_all_exist():
    """[S3-04] 一：「可组合标签、GPS 范围、时间、传感器信号、模型输出等多类条件」
    + 二「事件触发」行的事件流 = 六类条件来源。"""
    leaves = {
        R.ConditionKind.TAG,
        R.ConditionKind.GEO,
        R.ConditionKind.TIME,
        R.ConditionKind.SIGNAL,
        R.ConditionKind.MODEL_OUTPUT,
        R.ConditionKind.EVENT,
    }
    assert leaves <= set(R.ConditionKind)
    used = set()
    for rule in R.SAMPLE_RULES:
        used |= rule.condition_kinds()
    assert leaves <= used, f"示例规则没覆盖到的条件来源: {leaves - used}"


# ============================ 四、规则即数据：双模式表达 / 生命周期 / CDC（A + B + C）


def test_dual_expression_modes_compile_to_the_same_predicate():
    """[S3-04] 一：「工程师写 SQL，业务同学拖配置，产出的规则等价」。

    「等价」是硬要求，所以这里拿同一个条件的两种写法比最终 WHERE 谓词。
    """
    visual = _rule(
        "RULE_EQUIV_VISUAL",
        visual_config=R.ConditionGroup(
            "AND",
            (
                R.TagCondition("weather", ("rain", "heavy_rain")),
                R.TagCondition("road_type", ("highway",)),
            ),
        ),
    )
    handwritten = _rule(
        "RULE_EQUIV_SQL",
        expression_mode=R.ExpressionMode.SQL,
        sql_condition=visual.where_sql(R.Dialect.SPARK, alias="clip"),
    )
    visual_sql = visual.where_sql(R.Dialect.SPARK, alias="clip")
    # 手写那条只是在外面多包一层括号保结合优先级，谓词本体逐字相同
    assert handwritten.where_sql(R.Dialect.SPARK, alias="clip") == f"({visual_sql})"
    assert "IN ('rain', 'heavy_rain')" in visual_sql and "IN ('highway')" in visual_sql
    # 两条路下游都只看 Condition，不关心当初是敲的还是拖的
    assert isinstance(handwritten.condition(), R.RawSqlCondition)
    assert isinstance(visual.condition(), R.ConditionGroup)


def test_sql_mode_without_sql_and_visual_mode_without_tree_are_both_refused():
    with pytest.raises(R.RuleValidationError, match="SQL 模式但 sql_condition 为空"):
        R.RuleDefinition(
            rule_id="RULE_EMPTY_SQL",
            rule_name="空 SQL",
            rule_type=R.RuleType.TAG_COMBINATION,
            expression_mode=R.ExpressionMode.SQL,
        )
    with pytest.raises(R.RuleValidationError, match="可视化模式但 visual_config 为空"):
        R.RuleDefinition(
            rule_id="RULE_EMPTY_VISUAL",
            rule_name="空树",
            rule_type=R.RuleType.TAG_COMBINATION,
        )


def test_condition_tree_round_trips_through_the_visual_config_json():
    """可视化配置落 ods_mining_rule_config.rule_condition_json，回读必须一模一样。"""
    tree = R.ConditionGroup(
        "AND",
        (
            R.TagCondition("light_condition", ("night",)),
            R.GeoFenceCondition(
                shape="bbox", min_lat=31.1, max_lat=31.4, min_lon=121.35, max_lon=121.65
            ),
            R.TimeWindowCondition(start_time=None, weekdays=(1, 2, 3, 4, 5)),
            R.harsh_deceleration_condition(),
            R.EventCondition(trigger_types=("driver_takeover",)),
            R.ModelOutputCondition(output_column="aeb_triggered", value=True),
        ),
    )
    payload = json.loads(json.dumps(tree.to_dict(), ensure_ascii=False))
    back = R.condition_from_dict(payload)
    assert back.to_dict() == tree.to_dict()
    assert back.to_sql(R.Dialect.SPARK, alias="clip") == tree.to_sql(R.Dialect.SPARK, alias="clip")
    # 原文那两个数字过一轮 JSON 也不许变
    signal = next(n for n in back.walk() if isinstance(n, R.SignalCondition))
    assert signal.threshold == -4.0 and signal.min_duration_sec == 0.5


def test_a_broken_visual_config_is_rejected_with_a_reason():
    with pytest.raises(R.RuleValidationError, match="缺少 kind"):
        R.condition_from_dict({"column": "weather"})
    with pytest.raises(R.RuleValidationError, match="未知条件类型"):
        R.condition_from_dict({"kind": "telepathy"})
    with pytest.raises(R.RuleValidationError, match="不合法"):
        R.condition_from_dict({"kind": "signal", "op": "<"})  # 缺 signal / threshold
    with pytest.raises(R.RuleValidationError, match="条件组不能为空"):
        R.condition_from_dict({"kind": "group", "operator": "AND", "children": []})


def test_lifecycle_actions_each_leave_a_change_record():
    """[S3-04] 一：「创建 / 修改 / 禁用 / 优先级 / 版本，变更全程留痕」。"""
    rule = _rule("RULE_LIFECYCLE", rule_status=R.RuleStatus.DRAFT, rule_priority=R.RulePriority.P2)
    enabled, c1 = rule.enable(by="alice", at=NOW)
    disabled, c2 = enabled.disable(by="bob", reason="误报太多", at=NOW)
    reenabled, c3 = disabled.enable(by="alice", at=NOW)
    bumped, c4 = reenabled.set_priority(R.RulePriority.P0, by="carol", at=NOW)
    updated, c5 = bumped.with_changes(
        by="dave", at=NOW, visual_config=R.TagCondition("weather", ("fog",))
    )
    archived, c6 = updated.archive(by="eve", reason="场景已下线", at=NOW)

    assert [c.action for c in (c1, c2, c3, c4, c5, c6)] == [
        "enable",
        "disable",
        "enable",
        "priority",
        "update",
        "archive",
    ]
    assert all(c.changed_by for c in (c1, c2, c3, c4, c5, c6))
    assert all(c.changed_at == NOW for c in (c1, c2, c3, c4, c5, c6))
    assert c2.detail == "误报太多"
    assert "P2 -> P0" in c4.detail and "向量化分级" in c4.detail
    # 只有实质修改才 bump 版本
    assert (c1.to_version, c2.to_version, c3.to_version, c4.to_version) == (1, 1, 1, 1)
    assert c5.to_version == 2
    assert disabled.disabled_at == NOW and reenabled.disabled_at is None
    assert archived.rule_status is R.RuleStatus.ARCHIVED
    # 原对象始终不被就地改（留痕的前提是旧版本还在）
    assert rule.rule_status is R.RuleStatus.DRAFT and rule.rule_version == 1


def test_archived_rules_cannot_be_re_enabled():
    rule = _rule("RULE_ARCHIVED")
    archived, _ = rule.archive(by="eve", at=NOW)
    with pytest.raises(R.RuleValidationError, match="已归档"):
        archived.enable(by="alice", at=NOW)


def test_a_cosmetic_edit_does_not_bump_the_version():
    """改描述不算语义变更——版本号被无意义推高，命中结果就没法按版本对账了。"""
    rule = _rule("RULE_COSMETIC", notes="旧描述")
    same, change = rule.with_changes(by="alice", at=NOW, notes="新描述")
    assert same.rule_version == rule.rule_version == 1
    assert "非语义变更" in change.detail
    assert same.fingerprint() == rule.fingerprint()

    real, change2 = rule.with_changes(
        by="alice", at=NOW, visual_config=R.TagCondition("weather", ("snow",))
    )
    assert real.rule_version == 2 and change2.to_version == 2
    assert real.fingerprint() != rule.fingerprint()


def test_priority_drives_the_vectorize_queue_and_the_score_does_not():
    """[S3-04] 一：「高优先级规则命中的数据优先进入向量化队列」——分级只看优先级。"""
    assert R.RulePriority.P0.vectorize_policy is R.VectorizePolicy.PRIORITY
    assert R.RulePriority.P1.vectorize_policy is R.VectorizePolicy.PRIORITY
    assert R.RulePriority.P2.vectorize_policy is R.VectorizePolicy.SAMPLED
    assert R.RulePriority.P3.vectorize_policy is R.VectorizePolicy.SAMPLED

    low_priority_high_score = _rule(
        "RULE_P3_HIGH", rule_priority=R.RulePriority.P3, target_clip_count=10**6
    )
    breakdown = S.score_hit(
        low_priority_high_score, existing_hit_count=0, now=NOW, collected_at=NOW
    )
    assert breakdown.value_score > 0
    # 分再高也不许把 P3 的命中塞进优先队列
    assert S.resolve_vectorize_policy(low_priority_high_score) is R.VectorizePolicy.SAMPLED


def test_priority_parsing_from_the_lakehouse_row_rejects_out_of_range():
    """rule_priority 在 registry 是 INT、引擎侧是 P0-P3；越界必须拒，不许静默兜底。"""
    assert CS._priority_from_db(0) is R.RulePriority.P0
    assert CS._priority_from_db("2") is R.RulePriority.P2
    assert CS._priority_from_db("p1") is R.RulePriority.P1
    assert CS._priority_from_db(None) is R.RulePriority.P2  # 缺省档
    for bad in (9, "P9", "urgent"):
        with pytest.raises(R.RuleValidationError, match="rule_priority"):
            CS._priority_from_db(bad)


def test_rule_rows_round_trip_through_the_registry_column_names():
    """规则 → ods_mining_rule_config 行 → 规则：六大种类与两种表达都不掉语义。"""
    for rule in R.SAMPLE_RULES:
        row = rule.to_row()
        assert set(row) <= set(T.RULE_CONFIG_COLUMNS)
        back = CS.rule_from_row(row)
        assert back.fingerprint() == rule.fingerprint()
        assert back.rule_priority is rule.rule_priority
        assert back.rule_version == rule.rule_version
        assert back.effective_mode is rule.effective_mode


def test_bad_rows_do_not_take_down_the_whole_batch():
    """一条规则写坏了，不该让当天所有规则都跑不了。"""
    good = R.SAMPLE_RULES[0].to_row()
    bad_kind = dict(good, rule_id="RULE_BAD_KIND", rule_category="telepathy")
    bad_json = dict(good, rule_id="RULE_BAD_JSON", rule_condition_json="{not json")
    missing = {"rule_id": "", "rule_name": "", "rule_category": ""}
    ok, failures = CS.rows_to_rules([good, bad_kind, bad_json, missing])
    assert [r.rule_id for r in ok] == [good["rule_id"]]
    assert {rid for rid, _ in failures} == {"RULE_BAD_KIND", "RULE_BAD_JSON", ""}
    assert any("六大种类" in msg for _, msg in failures)


def test_the_engine_never_writes_the_rule_config_table():
    """方向不许反：MySQL 是规则的写入端，湖仓是只读副本。"""
    assert T.ODS_MINING_RULE_CONFIG.role == "read"
    for path in sorted(MINING_SRC.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "INSERT INTO {qualified(ODS_MINING_RULE_CONFIG" not in text
    # 只有 CDC 作业往里写，而那是 Flink 作业脚本，不是引擎的运行期写入
    job_sql = CS.render_cdc_sync_job()
    assert "INSERT INTO" in job_sql and "ods_mining_rule_config" in job_sql
    assert "mining_rule_config_cdc_source" in job_sql


def test_cdc_job_carries_every_registry_column_plus_the_ods_system_fields():
    """CDC 是按列名对位同步的：源表 DDL / 目标列表 / SELECT 三处必须同名同序。"""
    business = list(T.RULE_CONFIG_CDC_COLUMNS)
    assert "_ingest_time" not in business and "_source_system" not in business
    job = CS.render_cdc_sync_job()
    for col in business:
        assert f"`{col}`" in job
    # ODS 层的系统字段由入湖作业补齐（对齐 domains.Layer.system_fields）
    assert "`_ingest_time`" in job and "`_source_system`" in job
    assert set(Layer.ODS.system_fields) == {"_ingest_time", "_source_system"}

    source_ddl = CS.render_cdc_source_ddl()
    mysql_ddl = CS.render_mysql_control_plane_ddl()
    for col in business:
        assert f"`{col}`" in source_ddl, f"CDC 源表 DDL 少了列 {col}"
        assert f"`{col}`" in mysql_ddl, f"控制面 MySQL DDL 少了列 {col}"
    # 控制面独有的变更留痕列不入湖
    assert "`change_log`" in mysql_ddl and "`change_log`" not in job


def test_the_control_plane_ddl_defaults_the_event_window_to_the_source_numbers():
    ddl = CS.render_mysql_control_plane_ddl()
    assert "`event_window_before_sec` INT           NOT NULL DEFAULT 15" in ddl
    assert "`event_window_after_sec`  INT           NOT NULL DEFAULT 5" in ddl


def test_cdc_ddl_never_hardcodes_a_password():
    ddl = CS.render_cdc_source_ddl()
    assert "${MYSQL_PASSWORD}" in ddl


def test_loader_only_takes_enabled_rules_and_orders_by_priority():
    backend = RoutingBackend(rows=[R.SAMPLE_RULES[0].to_row()])
    loader = CS.RuleConfigLoader(backend=backend)
    rules, bad = loader.load(execution_mode=R.ExecutionMode.BATCH_T_PLUS_1, project_code="PRJ-A")
    sql = backend.queries[0]
    assert "rule_status = 'enabled'" in sql
    assert "exec_mode = 'batch_t_plus_1'" in sql
    assert "project_code = 'PRJ-A'" in sql
    assert "ORDER BY `rule_priority`, `rule_id`" in sql
    assert len(rules) == 1 and not bad


def test_loading_a_missing_rule_says_so():
    loader = CS.RuleConfigLoader(backend=RoutingBackend(rows=[]))
    with pytest.raises(B.BackendError, match="不存在"):
        loader.load_one("RULE_NO_SUCH")


def test_handwritten_sql_cannot_smuggle_dml():
    """「工程师写 SQL」这条路天然是注入点，编译期必须拦。"""
    R.RawSqlCondition("clip.weather = 'rain' AND clip.duration_sec > 10")  # 正常谓词放行
    for danger in (
        "DROP TABLE dwd_collect_clip_detail",
        "1=1; DELETE FROM x",
        "weather = 'rain' -- 注释",
        "weather = 'rain' /* 块注释 */",
        "INSERT INTO x VALUES (1)",
        "SET role = 'admin'",
    ):
        with pytest.raises(R.RuleValidationError):
            R.RawSqlCondition(danger)
    with pytest.raises(R.RuleValidationError, match="括号不配对"):
        R.RawSqlCondition("(weather = 'rain'")


def test_tag_values_are_escaped_not_interpolated():
    cond = R.TagCondition("weather", ("ra'in",))
    assert "'ra''in'" in cond.to_sql(R.Dialect.SPARK, alias="clip")
    with pytest.raises(R.RuleValidationError, match="取值列表不能为空"):
        R.TagCondition("weather", ())


# ================= 五、批流双模执行链路与执行追溯（判据 B + C）


def _batch_executor(**kw) -> E.BatchRuleExecutor:
    kw.setdefault("backend", RoutingBackend())
    kw.setdefault("result_sink", B.InMemoryResultSink())
    kw.setdefault("task_sink", B.InMemoryResultSink())
    kw.setdefault("tag_service", B.InMemoryTagService())
    return E.BatchRuleExecutor(**kw)


def _clip_rows(n: int) -> list[dict]:
    return [
        {
            "data_id": f"COLLECT_BP_2026030112304{i}_b7e2",
            "collect_start_time": NOW - timedelta(days=1),
            "project_code": "PRJ-A",
            "vehicle_code": "BP-0001",
        }
        for i in range(n)
    ]


def test_batch_run_records_the_four_traceability_facts():
    """[S3-04] 一「执行追溯」：执行时间 / 扫描范围 / 命中数量 / 写入标签量，缺一不可。"""
    rows = _clip_rows(3)
    backend = RoutingBackend(hit_count=3, rows=rows)
    result_sink = B.InMemoryResultSink()
    task_sink = B.InMemoryResultSink()
    tags = B.InMemoryTagService()
    ex = _batch_executor(
        backend=backend, result_sink=result_sink, task_sink=task_sink, tag_service=tags
    )
    rule = _sample("RULE_TAG_RAINY_HIGHWAY")
    record = ex.execute_rule(rule, run_id=RUN_ID, now=NOW)

    assert record.task_status is E.TaskStatus.SUCCESS
    row = record.to_row()
    assert list(row) == list(T.MINING_TASK_WRITE_COLUMNS)  # 顺序对齐写入投影
    # 执行时间
    assert row["start_time"] == NOW and row["end_time"] is not None
    assert row["duration_sec"] < 60  # 注入的 now 不许把耗时算成几万秒
    # 扫描范围
    assert row["scan_start_time"] == record.scan_low_watermark
    assert row["scan_end_time"] == NOW
    assert row["scan_row_count"] == 3
    # 命中数量 / 写入标签量
    assert row["hit_data_count"] == 3
    assert row["tag_write_count"] == 3
    # 引擎画像
    assert row["task_type"] == "rule_mining" and row["engine"] == "spark"
    assert row["rule_category"] == rule.rule_type.value
    assert row["rule_priority"] == rule.rule_priority.rank
    assert row["sla_breached"] is False
    assert task_sink.rows and task_sink.rows[0] == row


def test_a_replayed_run_is_not_reported_as_an_sla_breach():
    """回刷/重放常把 now 定在过去某一刻：耗时必须用单调钟量，不是「现在离那一刻多久」。

    算错的后果不只是 duration_sec 难看——sla_breached 会被几万秒顶成 true，
    执行追溯表上凭空多出一片「突破 4 小时 SLA」的假记录。
    """
    long_ago = datetime(2020, 1, 1, 0, 0, 0)
    ex = _batch_executor(backend=RoutingBackend(hit_count=0))
    record = ex.execute_rule(_sample("RULE_TAG_RAINY_HIGHWAY"), run_id=RUN_ID, now=long_ago)
    assert record.task_status is E.TaskStatus.SUCCESS
    assert record.elapsed_seconds < 60
    assert record.sla_breached is False
    assert record.to_row()["sla_breached"] is False


def test_sla_breach_flips_exactly_at_the_four_hour_line():
    """[S3-04] 三承诺「4 小时内跑完」：4 小时整不算超，多 1 秒才算。"""

    def _record(seconds: float) -> E.RuleRunRecord:
        rec = E.RuleRunRecord(
            task_id="t",
            run_id=RUN_ID,
            rule_id="RULE_X",
            rule_version=1,
            execution_mode=R.ExecutionMode.BATCH_T_PLUS_1,
            executed_at=NOW,
        )
        rec.finish(E.TaskStatus.SUCCESS, at=NOW + timedelta(seconds=seconds))
        return rec

    assert _record(K.BATCH_SLA_SECONDS).sla_breached is False
    assert _record(K.BATCH_SLA_SECONDS + 1).sla_breached is True
    # 流作业无界，不拿 4 小时去判它
    stream = E.RuleRunRecord(
        task_id="t",
        run_id=RUN_ID,
        rule_id="RULE_X",
        rule_version=1,
        execution_mode=R.ExecutionMode.NEAR_REALTIME,
        executed_at=NOW,
    )
    stream.finish(E.TaskStatus.SUCCESS, at=NOW + timedelta(days=7))
    assert stream.sla_breached is False


def test_the_watermark_is_committed_only_after_the_results_land():
    """结果没落表就推进水位 = 丢命中。宁可下一轮重扫（命中按 artifact_id 幂等）。"""
    ex = _batch_executor(
        backend=RoutingBackend(hit_count=2, rows=_clip_rows(2)), result_sink=ExplodingResultSink()
    )
    rule = _sample("RULE_TAG_RAINY_HIGHWAY")
    record = ex.execute_rule(rule, run_id=RUN_ID, now=NOW)
    assert record.task_status is E.TaskStatus.FAILED
    assert "BackendError" in record.error_message
    assert ex.watermarks.get(rule.rule_id, T.DWD_COLLECT_CLIP_DETAIL.resolve()) is None

    # 成功那次才提交
    ok_ex = _batch_executor(backend=RoutingBackend(hit_count=2, rows=_clip_rows(2)))
    ok_ex.execute_rule(rule, run_id=RUN_ID, now=NOW)
    committed = ok_ex.watermarks.get(rule.rule_id, T.DWD_COLLECT_CLIP_DETAIL.resolve())
    assert committed is not None and committed.high == NOW


def test_the_next_window_starts_from_the_previous_high_minus_the_lookback():
    """[S3-04] 三「基于 _ingest_time / update_time 水位做增量，避免每次全表回扫」。"""
    store = W.InMemoryWatermarkStore()
    table = T.DWD_COLLECT_CLIP_DETAIL.resolve()
    first = store.next_window("RULE_X", table, Layer.DWD, now=NOW)
    assert first.low == W.EPOCH_WATERMARK  # 首轮就是一次全表回扫，只该发生一次
    assert first.column == "update_time"
    store.commit(first)

    later = NOW + timedelta(hours=6)
    second = store.next_window("RULE_X", table, Layer.DWD, now=later)
    assert second.low == NOW - timedelta(minutes=W.DEFAULT_LOOKBACK_MINUTES)
    assert second.high == later
    assert second.span_seconds == pytest.approx(6 * 3600 + W.DEFAULT_LOOKBACK_MINUTES * 60)

    # 层级决定水位列：ODS 层没有 update_time
    assert W.watermark_column(Layer.ODS) == "_ingest_time"
    assert W.watermark_column(Layer.DWS) == "update_time"


def test_the_incremental_predicate_is_a_half_open_interval():
    """上界取「不含」，相邻两轮才能严丝合缝拼接，不重不漏。"""
    wm = _watermark("RULE_X")
    pred = W.incremental_predicate(wm, alias="clip")
    assert ">= TIMESTAMP '2026-09-11 10:00:00'" in pred
    assert "< TIMESTAMP '2026-09-12 10:00:00'" in pred
    assert "<=" not in pred


def test_a_clock_rollback_degrades_to_an_empty_window_instead_of_crashing():
    store = W.InMemoryWatermarkStore()
    table = T.DWD_COLLECT_CLIP_DETAIL.resolve()
    store.commit(store.next_window("RULE_X", table, Layer.DWD, now=NOW))
    rolled_back = store.next_window("RULE_X", table, Layer.DWD, now=NOW - timedelta(days=30))
    assert rolled_back.low == rolled_back.high  # 空区间，本轮扫 0 行
    with pytest.raises(ValueError, match="水位区间非法"):
        W.Watermark(rule_id="R", table="t", column="update_time", low=NOW, high=LOW)


def test_a_disabled_rule_is_skipped_not_run():
    disabled, _ = _sample("RULE_TAG_RAINY_HIGHWAY").disable(by="ops", at=NOW)
    ex = _batch_executor()
    record = ex.execute_rule(disabled, run_id=RUN_ID, now=NOW)
    assert record.task_status is E.TaskStatus.SKIPPED
    assert "disabled" in record.error_message
    assert not ex.backend.queries  # 连 COUNT 都不该发


def test_dry_run_writes_nothing_anywhere():
    backend = RoutingBackend(hit_count=7, rows=_clip_rows(7))
    result_sink = B.InMemoryResultSink()
    task_sink = B.InMemoryResultSink()
    tags = B.InMemoryTagService()
    ex = _batch_executor(
        backend=backend, result_sink=result_sink, task_sink=task_sink, tag_service=tags
    )
    record = ex.execute_rule(
        _sample("RULE_TAG_RAINY_HIGHWAY"), run_id=RUN_ID, now=NOW, dry_run=True
    )
    assert record.task_status is E.TaskStatus.SUCCESS
    assert record.hit_count == 7  # 数得出来
    assert result_sink.rows == [] and task_sink.rows == [] and tags.written == {}
    assert ex.watermarks.get("RULE_TAG_RAINY_HIGHWAY", T.DWD_COLLECT_CLIP_DETAIL.resolve()) is None


def test_a_rule_that_matches_too_much_is_refused_before_pulling_rows():
    """圈得太宽的规则要在拉数据之前就被挡住，不能把几百万行拖进单机内存。"""
    backend = RoutingBackend(hit_count=500_000, rows=_clip_rows(1))
    ex = _batch_executor(backend=backend)
    ex.max_fetch_rows = 10
    record = ex.execute_rule(_sample("RULE_TAG_RAINY_HIGHWAY"), run_id=RUN_ID, now=NOW)
    assert record.task_status is E.TaskStatus.FAILED
    assert "超过单轮拉取上界" in record.error_message
    assert "execute_pushdown" in record.error_message
    assert len(backend.queries) == 1  # 只发了 COUNT，没有发 SELECT


def test_pushdown_writes_by_sql_and_defers_tagging():
    """pushdown 是把打标推迟，不是绕过「所有命中统一经标签服务打标」。"""
    backend = RoutingBackend(rows=_clip_rows(4))
    ex = _batch_executor(backend=backend)
    record = ex.execute_pushdown(_sample("RULE_TAG_RAINY_HIGHWAY"), run_id=RUN_ID, now=NOW)
    assert record.task_status is E.TaskStatus.SUCCESS
    assert record.tag_written_count == 0
    assert "打标由下游批次补" in record.error_message
    assert backend.executed and backend.executed[0].startswith("INSERT INTO")
    assert "dwd_mining_result_detail" in backend.executed[0]


def test_the_batch_note_warns_past_the_row_ceiling():
    """预估扫描量超过原文的亿级上界时，4 小时 SLA 不再适用——要说出来。"""
    rule = _sample("RULE_TAG_RAINY_HIGHWAY")
    wm = _watermark(rule.rule_id)
    inside = C.RuleCompiler().compile_batch(
        rule, wm, run_id=RUN_ID, now=NOW, estimated_rows=K.BATCH_SCAN_ROW_CEILING
    )
    outside = C.RuleCompiler().compile_batch(
        rule, wm, run_id=RUN_ID, now=NOW, estimated_rows=K.BATCH_SCAN_ROW_CEILING + 1
    )
    assert not any("超出原文承诺" in n for n in inside.notes)
    assert any("超出原文承诺" in n for n in outside.notes)
    assert str(K.BATCH_SCAN_ROW_CEILING) in inside.notes[0]
    assert f"{K.BATCH_SLA_HOURS} 小时" in inside.notes[0]


def test_execute_all_runs_batch_rules_in_priority_order():
    """按优先级排序不只为了整齐：高优先级命中要早排进向量化队列。"""
    backend = RoutingBackend(hit_count=0)
    ex = _batch_executor(backend=backend)
    report = ex.execute_all(R.SAMPLE_RULES, run_id=RUN_ID, now=NOW)
    ran = [r.rule_id for r in report.records]
    priorities = [_sample(rid).rule_priority.rank for rid in ran]
    assert priorities == sorted(priorities)
    # 准实时那几条不该混进批的这一轮
    assert all(_sample(rid).effective_mode is R.ExecutionMode.BATCH_T_PLUS_1 for rid in ran)
    assert set(ran) == {
        r.rule_id
        for r in R.SAMPLE_RULES
        if r.effective_mode is R.ExecutionMode.BATCH_T_PLUS_1 and r.is_runnable
    }


def test_compile_many_isolates_the_bad_rule():
    """一条坏规则不该拖垮整批编译。"""
    good = _sample("RULE_TAG_RAINY_HIGHWAY")
    queries, failed = C.RuleCompiler().compile_many(
        [good, _sample("RULE_GEO_RUSH_HOUR")],
        watermarks={good.rule_id: _watermark(good.rule_id)},  # 另一条故意不给水位
        run_id=RUN_ID,
        now=NOW,
    )
    assert [q.rule_id for q in queries] == [good.rule_id]
    assert failed and failed[0][0] == "RULE_GEO_RUSH_HOUR"
    assert "缺少水位" in failed[0][1]


def test_traceability_write_failure_does_not_fail_a_successful_mining_run():
    """追溯写不进去是运维问题，不该让已经成功的挖掘任务被判失败。"""
    ex = _batch_executor(
        backend=RoutingBackend(hit_count=1, rows=_clip_rows(1)), task_sink=ExplodingResultSink()
    )
    record = ex.execute_rule(_sample("RULE_TAG_RAINY_HIGHWAY"), run_id=RUN_ID, now=NOW)
    assert record.task_status is E.TaskStatus.SUCCESS


def test_stream_submit_reports_the_event_window_and_the_engine():
    backend = RoutingBackend()
    ex = E.StreamRuleExecutor(
        backend=backend, task_sink=B.InMemoryResultSink(), tag_service=B.InMemoryTagService()
    )
    record = ex.submit_rule(TAKEOVER_RULE, run_id=RUN_ID, now=NOW)
    assert record.task_status is E.TaskStatus.SUCCESS
    assert "前15秒/后5秒" in record.error_message
    assert record.to_row()["engine"] == "flink"
    assert record.to_row()["exec_mode"] == "near_realtime"
    assert backend.executed and "INSERT INTO" in backend.executed[0]


def test_task_insert_sql_covers_the_registry_projection():
    """导出脚本那条 INSERT 与 Sink 走同一份列投影，不另起一套列名。"""
    rec = E.RuleRunRecord(
        task_id="t1",
        run_id=RUN_ID,
        rule_id="RULE_X",
        rule_version=1,
        execution_mode=R.ExecutionMode.BATCH_T_PLUS_1,
        executed_at=NOW,
    )
    sql = E.task_insert_sql([rec])
    assert "dwd_mining_task_detail" in sql
    for col in T.MINING_TASK_WRITE_COLUMNS:
        assert f"`{col}`" in sql
    with pytest.raises(ValueError, match="没有执行记录"):
        E.task_insert_sql([])


# ====================== 六、结果双写 + 统一标签服务 + 补抽帧联动（判据 B + C）


def test_every_hit_carries_the_rule_id_lineage():
    """[S3-04] 二：「所有命中统一经标签服务打标，携带 rule_id 血缘」。"""
    tags = B.InMemoryTagService()
    ex = _batch_executor(backend=RoutingBackend(hit_count=2, rows=_clip_rows(2)), tag_service=tags)
    rule = _sample("RULE_TAG_RAINY_HIGHWAY")
    ex.execute_rule(rule, run_id=RUN_ID, now=NOW)
    payloads = [r.to_payload() for r in tags.written.values()]
    assert payloads
    for p in payloads:
        assert p["rule_id"] == rule.rule_id
        assert p["rule_version"] == str(rule.rule_version)  # registry 是 STRING
        assert p["run_id"] == RUN_ID
        assert p["raw_tag"] == rule.scene_label  # 归一前的写法，字典映射交给服务
        assert p["source"] == B.TAG_SOURCE_RULE


def test_the_engine_has_no_code_path_that_writes_a_tag_table_itself():
    """[S3-04] 二：「不需要规则引擎自建一套标签写入逻辑」——字典映射与去重都在服务侧。"""
    assert T.DWD_MINING_TAG_DETAIL.role == "read"
    assert T.DWD_MINING_IMAGE_TAG_DETAIL.role == "read"
    writable = {ref.name for ref in T.ALL_REFS if "write" in ref.role}
    assert writable == {
        "dwd_mining_result_detail",
        "dwd_mining_task_detail",
        "dwd_scene_gap_detail",
    }
    for path in sorted(MINING_SRC.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "qualified(DWD_MINING_TAG_DETAIL" not in text
        assert "qualified(DWD_MINING_IMAGE_TAG_DETAIL" not in text
    # 标签的唯一出口是接口，不是 SQL
    assert hasattr(B.TagService, "write_tags") and hasattr(V.ModelTagSink, "write_tags")


def test_result_rows_write_exactly_the_registry_projection():
    """结果双写的湖仓那一路：列与顺序逐列对齐 registry 派生的投影。"""
    spec = registry.by_name("dwd_mining_result_detail")
    assert set(T.RESULT_WRITE_COLUMNS) <= {c.name for c in spec.all_columns()}
    assert set(spec.primary_key) <= set(T.RESULT_WRITE_COLUMNS)
    insert = (
        C.RuleCompiler()
        .compile_batch(
            _sample("RULE_TAG_RAINY_HIGHWAY"), _watermark("RULE_TAG_RAINY_HIGHWAY"), run_id=RUN_ID
        )
        .insert_sql
    )
    head = insert.split("\n")[1]
    assert head.strip().startswith("(`mining_task_id`, `data_id`,")
    for col in T.RESULT_WRITE_COLUMNS:
        assert f"`{col}`" in insert


def test_rule_hits_score_one_because_rules_are_deterministic():
    """registry 的列注释：「规则命中为 1.0，模型命中为模型置信度」。"""
    assert C.RULE_HIT_SCORE == 1.0
    assert C.RULE_HIT_TYPE == "rule"
    sql = (
        C.RuleCompiler()
        .compile_batch(
            _sample("RULE_TAG_RAINY_HIGHWAY"), _watermark("RULE_TAG_RAINY_HIGHWAY"), run_id=RUN_ID
        )
        .select_sql
    )
    assert "1.0 AS `hit_score`" in sql
    assert "'rule' AS `hit_type`" in sql


def test_rerunning_the_same_rule_version_yields_the_same_artifact_id():
    """重跑幂等：规则语义没变，命中的 artifact_id 就不变（ids 模块规则一）。"""
    rows = _clip_rows(1)
    rule = _sample("RULE_TAG_RAINY_HIGHWAY")
    first = B.InMemoryResultSink()
    second = B.InMemoryResultSink()
    _batch_executor(backend=RoutingBackend(hit_count=1, rows=rows), result_sink=first).execute_rule(
        rule, run_id=RUN_ID, now=NOW
    )
    _batch_executor(
        backend=RoutingBackend(hit_count=1, rows=rows), result_sink=second
    ).execute_rule(rule, run_id=RUN_ID, now=NOW)
    assert first.rows[0]["artifact_id"] == second.rows[0]["artifact_id"]
    assert first.rows[0]["artifact_id"].startswith(rows[0]["data_id"])

    # 规则改版后指纹变 → artifact_id 变（旧命中另行标 superseded）
    changed, _ = rule.with_changes(
        by="a", at=NOW, visual_config=R.TagCondition("weather", ("snow",))
    )
    third = B.InMemoryResultSink()
    _batch_executor(backend=RoutingBackend(hit_count=1, rows=rows), result_sink=third).execute_rule(
        changed, run_id=RUN_ID, now=NOW
    )
    assert third.rows[0]["artifact_id"] != first.rows[0]["artifact_id"]


def test_a_dirty_data_id_does_not_sink_the_whole_batch():
    """一行脏 data_id 只让那一行拿不到 artifact_id，其余照常落库。"""
    rows = [
        {"data_id": "not-a-valid-id", "project_code": "P", "vehicle_code": "V"},
        *_clip_rows(1),
    ]
    sink = B.InMemoryResultSink()
    _batch_executor(backend=RoutingBackend(hit_count=2, rows=rows), result_sink=sink).execute_rule(
        _sample("RULE_TAG_RAINY_HIGHWAY"), run_id=RUN_ID, now=NOW
    )
    assert sink.rows[0]["artifact_id"] == ""
    assert sink.rows[1]["artifact_id"] != ""


def test_event_hits_trigger_backfill_over_the_fifteen_five_window():
    """[S3-04] 三隐藏联动：「规则识别出接管事件，事件抽帧引擎立刻回头对前 15 后 5 秒
    窗口加密采样，两个引擎经湖仓表解耦协作，谁也不阻塞谁」。"""
    backfill = CountingBackfill()
    ex = E.StreamRuleExecutor(
        backend=B.DryRunBackend(),
        task_sink=B.InMemoryResultSink(),
        tag_service=B.InMemoryTagService(),
        backfill=backfill,
    )
    hits = [{"data_id": DATA_ID, "event_time": NOW}]
    tagged, dispatched = ex.handle_hits(TAKEOVER_RULE, hits, run_id=RUN_ID, now=NOW)
    assert (tagged, dispatched) == (1, 1)
    req = backfill.received[0]
    assert req.window_start_time == NOW - timedelta(seconds=15)
    assert req.window_end_time == NOW + timedelta(seconds=5)
    payload = req.to_payload()
    assert payload["window_before_sec"] == 15 and payload["window_after_sec"] == 5
    assert payload["sample_mode"] == "dense"  # 「加密采样」


def test_static_rules_never_trigger_backfill():
    """标签组合 / 时空地理没有「时刻」，不该触发补抽帧。"""
    assert E.StreamRuleExecutor._needs_backfill(TAKEOVER_RULE) is True
    assert E.StreamRuleExecutor._needs_backfill(HARSH_DECEL_RULE) is True
    assert E.StreamRuleExecutor._needs_backfill(_sample("RULE_TAG_RAINY_HIGHWAY")) is False
    assert E.StreamRuleExecutor._needs_backfill(_sample("RULE_GEO_URBAN_PEDESTRIAN")) is False


def test_backfill_dispatch_failure_does_not_roll_back_the_tags():
    """两个引擎本来就是解耦的：补抽帧派发失败不该把已经打好的标签回滚。"""
    tags = B.InMemoryTagService()
    ex = E.StreamRuleExecutor(
        backend=B.DryRunBackend(),
        task_sink=B.InMemoryResultSink(),
        tag_service=tags,
        backfill=ExplodingBackfill(),
    )
    tagged, dispatched = ex.handle_hits(
        TAKEOVER_RULE, [{"data_id": DATA_ID, "event_time": NOW}], run_id=RUN_ID, now=NOW
    )
    assert tagged == 1 and dispatched == 0
    assert len(tags.written) == 1


def test_backfill_handoff_reuses_the_result_table_columns():
    """交接不另起私有请求表——registry 早就在结果表上留好了那四列。"""
    captured: list[str] = []

    class _Capture(B.DryRunBackend):
        def execute(self, sql: str) -> int:
            captured.append(sql)
            return 1

    n = B.LakehouseBackfillDispatcher(backend=_Capture()).dispatch(
        [
            B.BackfillRequest.for_event(
                data_id=f"clip_{i}", rule_id="RULE_X", run_id=RUN_ID, event_time=NOW
            )
            for i in range(3)
        ]
    )
    assert n == 3 and len(captured) == 1  # 同窗口合成一条语句
    known = {c.name for c in registry.by_name("dwd_mining_result_detail").all_columns()}
    for col in (
        "frame_supplement_status",
        "event_time",
        "event_window_start_time",
        "event_window_end_time",
    ):
        assert col in known and f"`{col}`" in captured[0]
    assert B.FRAME_SUPPLEMENT_PENDING == "pending"


def test_stream_hits_without_a_data_id_are_skipped_not_written():
    tags = B.InMemoryTagService()
    ex = E.StreamRuleExecutor(
        backend=B.DryRunBackend(), task_sink=B.InMemoryResultSink(), tag_service=tags
    )
    tagged, _ = ex.handle_hits(
        TAKEOVER_RULE, [{"event_time": NOW}, {"data_id": DATA_ID, "event_time": NOW}], run_id=RUN_ID
    )
    assert tagged == 1


# ============= 七、高价值评分 / 排序 / 场景缺口识别（判据 C；公式为本项目设计）


def test_score_weights_must_sum_to_one():
    assert S.DEFAULT_WEIGHTS.priority == 0.40
    assert (
        S.DEFAULT_WEIGHTS.priority
        + S.DEFAULT_WEIGHTS.rarity
        + S.DEFAULT_WEIGHTS.severity
        + S.DEFAULT_WEIGHTS.type_bonus
        + S.DEFAULT_WEIGHTS.freshness
        == pytest.approx(1.0)
    )
    with pytest.raises(ValueError, match="加总为 1.0"):
        S.ScoreWeights(priority=0.9, rarity=0.9)


def test_priority_prior_score_is_a_linear_ladder():
    assert S.classify_tier(100.0) is S.ValueTier.S
    assert [p.prior_score for p in R.RulePriority] == [
        1.0,
        pytest.approx(2 / 3),
        pytest.approx(1 / 3),
        0.0,
    ]
    assert [p.rank for p in R.RulePriority] == [0, 1, 2, 3]


def test_rarity_is_zero_once_the_target_is_met():
    assert S.rarity_prior(1000, 0) == 1.0
    assert S.rarity_prior(1000, 500) == pytest.approx(0.5)
    assert S.rarity_prior(1000, 1000) == 0.0
    assert S.rarity_prior(1000, 5000) == 0.0  # 不会变成负数
    # 没填目标量时用兜底分母，不当成「无限稀缺」
    assert S.rarity_prior(0, S.RARITY_FLOOR_TARGET) == 0.0


def test_freshness_halves_every_thirty_days():
    assert S.freshness(NOW, now=NOW) == 1.0
    assert S.freshness(NOW - timedelta(days=S.FRESHNESS_HALF_LIFE_DAYS), now=NOW) == pytest.approx(
        0.5
    )
    assert S.freshness(NOW - timedelta(days=60), now=NOW) == pytest.approx(0.25)
    assert S.freshness(None) == 0.5  # 未知不奖不罚
    assert S.freshness(NOW + timedelta(days=5), now=NOW) == 1.0  # 未来时间不加成


def test_tier_thresholds_are_stable_and_the_sql_agrees_with_python():
    assert S.TIER_THRESHOLDS == (
        (80.0, S.ValueTier.S),
        (60.0, S.ValueTier.A),
        (40.0, S.ValueTier.B),
        (0.0, S.ValueTier.C),
    )
    assert S.classify_tier(80.0) is S.ValueTier.S
    assert S.classify_tier(79.99) is S.ValueTier.A
    assert S.classify_tier(60.0) is S.ValueTier.A
    assert S.classify_tier(39.99) is S.ValueTier.C
    sql = S.sql_tier_expression("`t`.`value_score`")
    for floor, tier in S.TIER_THRESHOLDS:
        if floor > 0:
            assert f">= {floor} THEN '{tier.value}'" in sql
    assert "ELSE 'C'" in sql


def test_the_score_is_bounded_and_explainable():
    rule = HARSH_DECEL_RULE
    breakdown = S.score_hit(
        rule, existing_hit_count=0, observed_signal_value=-8.0, collected_at=NOW, now=NOW
    )
    assert 0.0 <= breakdown.value_score <= 100.0
    # P0 + 全缺 + 严重度封顶 + 车辆信号加成 + 最新鲜 = 0.4+0.25+0.2+0.1*0.8+0.05 → 97
    assert breakdown.value_score == pytest.approx(100 * (0.4 + 0.25 + 0.2 + 0.08 + 0.05))
    assert breakdown.tier is S.ValueTier.S
    assert breakdown.to_dict()["value_tier"] == "S"
    assert "prio" in breakdown.explain() and "sev" in breakdown.explain()


def test_ranking_is_queue_then_priority_then_score():
    """职责边界：队列（原文）> 优先级档位（原文）> 得分（本项目设计的队内排序）。"""
    p0 = _rule("RULE_RANK_P0", rule_priority=R.RulePriority.P0)
    p1 = _rule("RULE_RANK_P1", rule_priority=R.RulePriority.P1)
    p2 = _rule("RULE_RANK_P2", rule_priority=R.RulePriority.P2)

    def bd(score: float) -> S.ScoreBreakdown:
        return S.ScoreBreakdown(score, S.classify_tier(score), 0, 0, 0, 0, 0)

    ranked = S.rank_hits([(p2, bd(99.0), "x"), (p1, bd(10.0), "y"), (p0, bd(50.0), "z")])
    assert [r.rule_id for r, _, _ in ranked] == ["RULE_RANK_P0", "RULE_RANK_P1", "RULE_RANK_P2"]
    # P2 得 99 分也排在 P1 得 10 分之后——分数不许越过队列与档位
    assert S.top_n(ranked, 1)[0][0].rule_id == "RULE_RANK_P0"
    with pytest.raises(ValueError):
        S.top_n(ranked, -1)


def test_the_sql_score_expression_mirrors_the_python_one():
    """批路与流路的 value_score 必须同源，否则同一条命中两边算出两个分。"""
    rule = HARSH_DECEL_RULE
    expr = S.sql_score_expression(rule, alias="clip", existing_hit_count=0, now=NOW)
    assert str(S.DEFAULT_WEIGHTS.priority) in expr
    assert str(S.FRESHNESS_HALF_LIFE_DAYS) in expr
    assert "LEAST(100.0, GREATEST(0.0," in expr  # 与 Python 侧同样夹在 [0,100]
    assert "-4.0" in expr  # 严重度按原文阈值算
    # MATCH_RECOGNIZE 把实测值聚合成 peak_value，打分要读那一列
    peak_expr = S.sql_score_expression(
        rule, alias="m", existing_hit_count=0, now=NOW, signal_value_column="peak_value"
    )
    assert "`m`.`peak_value`" in peak_expr


def test_gap_status_covers_the_three_source_outcomes():
    """[S3-01] 一「挖掘双出口」：命中就回补（零采集成本），未命中才下发定向采集。"""
    rule = _rule("RULE_GAP", target_clip_count=1000, rule_priority=R.RulePriority.P0)
    satisfied = G.evaluate_gap(rule, 1000, run_id=RUN_ID, now=NOW)
    partial = G.evaluate_gap(rule, 400, run_id=RUN_ID, now=NOW)
    missing = G.evaluate_gap(rule, 0, run_id=RUN_ID, now=NOW)

    assert satisfied.gap_status is G.GapStatus.SATISFIED
    assert satisfied.gap_clip_count == 0 and satisfied.coverage_ratio == 1.0
    assert "零采集成本" in satisfied.gap_status.action_cn

    assert partial.gap_status is G.GapStatus.PARTIAL
    assert partial.gap_clip_count == 600 and partial.coverage_ratio == pytest.approx(0.4)

    assert missing.gap_status is G.GapStatus.MISSING
    assert missing.gap_clip_count == 1000 and missing.coverage_ratio == 0.0
    # 「一条都没有」必须排在「差一点点」前面
    assert missing.gap_severity >= 60.0
    assert missing.gap_severity > partial.gap_severity > satisfied.gap_severity

    assert G.SATISFIED_COVERAGE_RATIO == 1.0
    with pytest.raises(ValueError, match="命中量不能为负"):
        G.evaluate_gap(rule, -1, run_id=RUN_ID, now=NOW)


def test_only_unsatisfied_gaps_turn_into_collect_demands():
    rule = _rule("RULE_DEMAND", target_clip_count=1000, rule_priority=R.RulePriority.P0)
    assert G.evaluate_gap(rule, 1000, run_id=RUN_ID, now=NOW).to_demand() is None
    demand = G.evaluate_gap(rule, 100, run_id=RUN_ID, now=NOW).to_demand(now=NOW)
    assert demand is not None
    assert demand.demand_clip_count == 900
    assert demand.to_payload()["demand_clip_count"] == 900
    assert demand.collect_demand_id.startswith("DEMAND_")


def test_the_scene_gap_table_deliberately_has_no_data_id():
    """dwd_scene_gap_detail 是聚合表（一次评估 × 一个场景一行），挂 data_id 恒为 NULL。

    这是已登记的设计决策，不是漏列——所以这里反过来钉住它「不许被补上」。
    """
    spec = registry.by_name("dwd_scene_gap_detail")
    cols = {c.name for c in spec.all_columns()}
    assert "data_id" not in cols
    assert spec.primary_key == ("project_code", "tag_id")
    assert "data_id" not in T.SCENE_GAP_WRITE_COLUMNS
    gap = G.evaluate_gap(_rule("RULE_GAP2", target_clip_count=10), 3, run_id=RUN_ID, now=NOW)
    assert "data_id" not in gap.to_row()
    assert list(gap.to_row()) == list(T.SCENE_GAP_WRITE_COLUMNS)


def test_coverage_sql_counts_distinct_clips_of_active_artifacts_only():
    """重刷会把旧产物标 superseded，把它们也数进来会虚高覆盖率。"""
    sql = G.coverage_sql(R.SAMPLE_RULES, project_code="PRJ-A", since=LOW)
    assert "COUNT(DISTINCT `data_id`) AS `hit_clip_count`" in sql
    assert "`artifact_status` = 'active'" in sql
    assert "`matched_tag_id` IN (" in sql  # 用 registry 的列名，不是引擎侧的 scene_label
    assert "`project_code` = 'PRJ-A'" in sql
    assert "`hit_time` >= TIMESTAMP '2026-09-11 10:00:00'" in sql
    assert "GROUP BY `matched_tag_id`, `rule_id`" in sql
    with pytest.raises(ValueError, match="没有可统计的场景标签"):
        G.coverage_sql([_rule("RULE_NO_LABEL", scene_label="")])


def test_gap_detection_ranks_by_severity_and_writes_one_row_per_scene():
    rules = [
        _rule(
            "RULE_GAP_A", target_clip_count=1000, rule_priority=R.RulePriority.P0, scene_label="a"
        ),
        _rule(
            "RULE_GAP_B", target_clip_count=1000, rule_priority=R.RulePriority.P3, scene_label="b"
        ),
    ]
    backend = RoutingBackend(rows=[{"rule_id": "RULE_GAP_B", "hit_clip_count": 900}])
    sink = B.InMemoryResultSink()
    gaps = G.SceneGapDetector(backend=backend, sink=sink).detect(rules, run_id=RUN_ID, now=NOW)

    assert [g.rule_id for g in gaps] == ["RULE_GAP_A", "RULE_GAP_B"]  # 未命中的排前面
    assert gaps[0].gap_status is G.GapStatus.MISSING
    assert gaps[1].gap_status is G.GapStatus.PARTIAL and gaps[1].hit_clip_count == 900
    assert len(sink.rows) == 2
    demands = G.SceneGapDetector.to_demands(gaps, rules)
    assert [d.rule_id for d in demands] == ["RULE_GAP_A", "RULE_GAP_B"]
    assert demands[0].rule_condition_summary  # 需求单回指规则条件


def test_gap_detection_reports_a_query_failure_instead_of_writing_zeros():
    class _Broken(RoutingBackend):
        def query(self, sql: str):
            raise RuntimeError("External Catalog 挂了")

    detector = G.SceneGapDetector(backend=_Broken(), sink=B.InMemoryResultSink())
    with pytest.raises(B.BackendError, match="场景覆盖度查询失败"):
        detector.detect([_rule("RULE_GAP_C", scene_label="c")], run_id=RUN_ID, now=NOW)


def test_gap_insert_sql_matches_the_registry_projection():
    gap = G.evaluate_gap(_rule("RULE_GAP4", target_clip_count=10), 1, run_id=RUN_ID, now=NOW)
    sql = G.gap_insert_sql([gap])
    assert "dwd_scene_gap_detail" in sql
    for col in T.SCENE_GAP_WRITE_COLUMNS:
        assert f"`{col}`" in sql
    with pytest.raises(ValueError, match="没有缺口"):
        G.gap_insert_sql([])


# ===================== 八、VLM 补语义级长尾标签（判据 B + C）
#
# ⚠️ [S3-05] 原文未取得。下面只断言能从已取得四篇推出来的部分：
#    送什么（关键帧 + 提示词）、回什么（双输出）、落哪张表（图片标签表 + 向量表冗余）、
#    批量与失败重试（Ray 选型换来的任务编排与断点续跑）、与标签子系统的衔接（走接口）。
#    批大小 32 / 重试 3 次这类本项目自定的参数**不当成原文断言**，只验行为自洽。


def _candidates(n: int, **kw) -> list[V.InferCandidate]:
    return [
        V.InferCandidate(
            image_id=f"IMG-{i:03d}",
            data_id=f"COLLECT_BP_2026030112304{i % 10}_b7e2",
            image_object_key=f"s3://adas-frames/{i}.jpg",
            keyframe_score=1.0 - i * 0.01,
            frame_quality_score=0.9,
            object_richness_score=0.8,
            temporal_position_score=0.7,
            parent_artifact_id=f"ART-{i}",
            camera_id="front_wide",
            project_code="PRJ-A",
            vehicle_code="BP-0001",
            rule_id="RULE_COMPOSITE_NIGHT_RAIN_BRAKE",
            rule_version=1,
            **kw,
        )
        for i in range(n)
    ]


def _engine(**kw) -> V.VlmInferenceEngine:
    kw.setdefault("client", V.EchoVlmClient())
    kw.setdefault("tag_sink", V.InMemoryModelTagSink())
    kw.setdefault("task_sink", B.InMemoryResultSink())
    kw.setdefault("caption_sink", V.InMemoryCaptionVectorSink())
    return V.VlmInferenceEngine(**kw)


def test_the_dual_output_is_mandatory_on_both_halves():
    """[S3-04] 结尾预告：「双输出（标签 + caption）」。缺任一半这条产出只剩半个用处。"""
    with pytest.raises(V.VlmOutputError, match="没有标签"):
        V.VlmOutput(image_id="IMG-1", tags=(), caption="一句话")
    with pytest.raises(V.VlmOutputError, match="没有 caption"):
        V.VlmOutput(image_id="IMG-1", tags=(V.VlmTag("行人撑着花伞", 0.9),), caption="   ")
    with pytest.raises(V.VlmOutputError, match="置信度"):
        V.VlmTag("行人撑着花伞", 1.4)
    with pytest.raises(V.VlmOutputError, match="不能为空"):
        V.VlmTag("  ", 0.5)
    ok = V.VlmOutput(
        image_id="IMG-1",
        tags=(V.VlmTag("施工区锥桶摆放混乱", 0.7), V.VlmTag("行人撑着花伞", 0.93)),
        caption="施工区锥桶散乱摆放，右前方有行人撑伞横穿",
    )
    assert ok.top_confidence == 0.93


def test_the_prompt_names_the_long_tail_scenes_the_source_called_out():
    """[S3-04] 结尾：「施工区锥桶摆放混乱」「行人撑着花伞」这类语义级场景，
    结构化条件写不出来——这正是大模型推理挖掘的领地。"""
    prompt = V.build_prompt()
    for scene in K.VLM_LONG_TAIL_EXAMPLES:
        assert scene in prompt
    # 提示词把双输出写成硬要求，与 VlmOutput 的校验一一对应
    assert K.VLM_OUTPUT_KINDS[0] in prompt and K.VLM_OUTPUT_KINDS[1] in prompt
    assert '"tags"' in prompt and '"caption"' in prompt
    assert "confidence" in prompt


def test_caption_lands_as_a_caption_category_tag_row():
    """[S3-03] 二：caption「以 tag_category=CAPTION 的特殊标签写入图片标签表，
    与结构化标签同条记录口径并存，同时冗余一份到向量表」。"""
    sink = V.InMemoryModelTagSink()
    caption_sink = V.InMemoryCaptionVectorSink()
    engine = _engine(tag_sink=sink, caption_sink=caption_sink)
    report = engine.run(_candidates(2), job_id="job-cap", run_id=RUN_ID, now=NOW)

    assert report.ok
    captions = sink.captions
    assert len(captions) == 2
    for payload in captions:
        assert payload["tag_category"] == "CAPTION"
        assert payload["caption_text"]
        assert payload["tag_source"] == "vlm"
    # 结构化标签与 caption 落同一张表、同一次写入
    structured = [p for p in sink.written.values() if p["tag_category"] != "CAPTION"]
    assert structured and {p["tag_source"] for p in structured} == {"vlm"}
    # 向量表冗余一份，键与标签行对得上
    assert len(caption_sink.rows) == 2
    by_image = {p["image_id"]: p["caption_text"] for p in captions}
    assert {r["image_id"] for r in caption_sink.rows} == set(by_image)
    # 同一段说明只维护一份：向量表那份必须与标签行逐字相同
    assert all(r["caption_text"] == by_image[r["image_id"]] for r in caption_sink.rows)


def test_the_write_payload_keys_are_exactly_the_registry_columns():
    """字段名与 registry 分叉在 Python 侧完全静默：要么映射失败，要么写进影子列。"""
    req = V.ImageTagWriteRequest(
        image_id="IMG-1",
        data_id=DATA_ID,
        raw_tag="行人撑着花伞",
        tag_category="",
        infer_job_id="job-1",
        run_id=RUN_ID,
        model_name="qwen-vl",
        model_version="2026Q3",
        confidence=0.9,
    )
    payload = req.to_payload()
    assert set(payload) == set(T.IMAGE_TAG_WRITE_COLUMNS)
    known = {c.name for c in registry.by_name("dwd_mining_image_tag_detail").all_columns()}
    assert set(payload) <= known
    pk = registry.by_name("dwd_mining_image_tag_detail").primary_key
    assert set(pk) <= set(payload)  # (image_id, tag_id, tag_source) 缺一就 Upsert 不进去


def test_every_lineage_field_is_filled_on_every_model_tag():
    """[S3-03] 二③：「每条标签携带 tag_source、rule_id / model_name / model_version、
    confidence、infer_job_id——回答『这个标签从哪来、可信度多少』」。"""
    sink = V.InMemoryModelTagSink()
    _engine(tag_sink=sink).run(_candidates(3), job_id="job-lineage", run_id=RUN_ID, now=NOW)
    assert sink.written
    for payload in sink.written.values():
        for field in K.VLM_REQUIRED_LINEAGE_FIELDS:
            assert payload.get(field) not in (None, ""), f"{field} 没填"
        # 漏斗第一层的血缘不能在第二层断掉
        assert payload["rule_id"] == "RULE_COMPOSITE_NIGHT_RAIN_BRAKE"
        assert payload["rule_version"] == "1"
        assert payload["infer_job_id"] == "job-lineage"
        assert payload["run_id"] == RUN_ID
        assert payload["parent_artifact_id"].startswith("ART-")


def test_model_tags_are_always_written_unreviewed():
    """[S3-03] 四：「未审核标签不得进入训练集圈选」——模型产出永远先过审再上岗。"""
    sink = V.InMemoryModelTagSink()
    _engine(tag_sink=sink).run(_candidates(2), job_id="job-review", run_id=RUN_ID, now=NOW)
    assert {p["review_status"] for p in sink.written.values()} == {V.REVIEW_PENDING}
    assert V.REVIEW_PENDING == "pending"
    # 不许存在任何「跳过审核」的开关：按 AST 查标识符与字面量，不数注释里的说明文字
    for path in sorted(MINING_SRC.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.arg):
                assert "auto_approve" not in node.arg, f"{path.name} 有跳过审核的参数"
            if isinstance(node, ast.Name):
                assert "auto_approve" not in node.id, f"{path.name} 有跳过审核的开关"
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value != "approved", f"{path.name} 直接写死了 approved"


def test_the_engine_does_not_normalise_tags_itself():
    """字典映射与别名归一是统一标签服务的职权（[S3-03] 二①），这里先原样递过去。"""
    sink = V.InMemoryModelTagSink()
    client = V.EchoVlmClient(tags=("下雨天",))  # 模型的自由发挥写法
    _engine(client=client, tag_sink=sink).run(
        _candidates(1), job_id="job-raw", run_id=RUN_ID, now=NOW
    )
    structured = [p for p in sink.written.values() if p["tag_category"] != "CAPTION"]
    assert structured[0]["tag_id"] == "下雨天"  # 未归一，交给服务查字典
    assert structured[0]["source_raw_tag"] == "下雨天"  # 归一前的写法留痕
    assert structured[0]["tag_category"] == ""  # 类别由字典裁定，模型不自封


def test_the_mining_package_never_imports_the_tags_subsystem():
    """子系统之间零耦合：衔接标签字典只经接口，不 import tags 的内部实现。"""
    for path in sorted(MINING_SRC.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "from ..tags" not in text
        assert "import adas_lakehouse.tags" not in text
        assert "from adas_lakehouse.tags" not in text
        assert "from ..sampling" not in text  # 选帧产物也只经湖仓表交接


def test_candidates_are_batched_by_descending_keyframe_score():
    """[S3-04] 四：「对候选里信息密度最高的帧做语义确认」——GPU 预算先花在最值钱的帧上。"""
    candidates = _candidates(5)
    shuffled = [candidates[3], candidates[0], candidates[4], candidates[1], candidates[2]]
    batches = V.plan_batches(shuffled, job_id="job-order", batch_size=2)
    ordered = [c.image_id for b in batches for c in b.candidates]
    assert ordered == [c.image_id for c in candidates]  # keyframe_score 降序
    assert [b.batch_index for b in batches] == [0, 1, 2]
    assert [b.size for b in batches] == [2, 2, 1]
    assert batches[0].job_id == "job-order"
    with pytest.raises(ValueError, match="batch_size 必须为正"):
        V.plan_batches(candidates, job_id="x", batch_size=0)


def test_undesensitised_frames_are_blocked_and_reported_not_silently_dropped():
    """合规红线的第二道闸。拦下来的帧必须在回报里看得见，否则覆盖率凭空少一块没人知道。"""
    ok = _candidates(2)
    blocked = [
        V.InferCandidate(
            image_id="IMG-BAD",
            data_id=DATA_ID,
            image_object_key="s3://x/bad.jpg",
            desensitize_status="pending",
        ),
        V.InferCandidate(image_id="IMG-NOKEY", data_id=DATA_ID, image_object_key=""),
    ]
    sendable, dropped = V.partition_sendable(ok + blocked)
    assert [c.image_id for c in sendable] == [c.image_id for c in ok]
    assert {i for i, _ in dropped} == {"IMG-BAD", "IMG-NOKEY"}
    assert any("脱敏" in reason for _, reason in dropped)

    sink = V.InMemoryModelTagSink()
    report = _engine(tag_sink=sink).run(ok + blocked, job_id="job-block", run_id=RUN_ID, now=NOW)
    assert {i for i, _ in report.rejected} >= {"IMG-BAD", "IMG-NOKEY"}
    assert report.record.rejected_count >= 2
    assert "IMG-BAD" not in {p["image_id"] for p in sink.written.values()}
    assert V.DESENSITIZE_PASSED == "passed"


def test_resume_skips_the_batches_that_already_landed():
    """[S3-01] 五选 Ray 换来的「断点续跑」——不是附加功能，是选型的兑现。"""
    checkpoints = V.InMemoryCheckpointStore()
    sink = V.InMemoryModelTagSink()
    client = V.EchoVlmClient()
    engine = _engine(client=client, tag_sink=sink, checkpoints=checkpoints, batch_size=2)
    candidates = _candidates(5)

    first = engine.run(candidates, job_id="job-resume", run_id=RUN_ID, now=NOW)
    assert first.batches_run == 3 and first.batches_skipped == []
    assert checkpoints.completed("job-resume") == {0, 1, 2}

    written_before = dict(sink.written)
    client.inferred_batches.clear()
    second = engine.run(candidates, job_id="job-resume", run_id=RUN_ID, now=NOW)
    assert second.batches_run == 0
    assert second.batches_skipped == [0, 1, 2]
    assert second.record.resumed_batch_count == 3
    assert client.inferred_batches == []  # 一次 GPU 都没白烧
    assert sink.written == written_before

    # resume=False 是重刷：无视断点全量重跑
    third = engine.run(candidates, job_id="job-resume", run_id=RUN_ID, now=NOW, resume=False)
    assert third.batches_run == 3


def test_rewriting_the_same_tags_is_idempotent():
    """[S3-03] 二②：联合主键 Upsert，重复写入无副作用——断点续跑必然带来重复写。"""
    sink = V.InMemoryModelTagSink()
    engine = _engine(tag_sink=sink, batch_size=2)
    engine.run(_candidates(3), job_id="job-a", run_id=RUN_ID, now=NOW)
    count_after_first = len(sink.written)
    engine.run(_candidates(3), job_id="job-a", run_id=RUN_ID, now=NOW, resume=False)
    assert len(sink.written) == count_after_first  # 没有产生重复标签


def test_a_failed_batch_is_retried_then_the_job_continues():
    """一批坏图不该让整个推理作业前功尽弃——这正是断点续跑要的行为。"""
    client = V.EchoVlmClient(fail_batches=frozenset({1}), max_failures_per_batch=1)
    engine = _engine(client=client, batch_size=1, max_attempts=3)
    report = engine.run(_candidates(3), job_id="job-retry", run_id=RUN_ID, now=NOW)
    assert report.ok  # 第二次尝试成功
    assert report.record.retry_count == 1
    assert report.batches_run == 3
    assert client.inferred_batches == [0, 1, 2]


def test_a_batch_that_burns_all_retries_is_recorded_and_the_rest_still_run():
    client = V.EchoVlmClient(fail_batches=frozenset({0}))  # 永远失败
    checkpoints = V.InMemoryCheckpointStore()
    task_sink = B.InMemoryResultSink()
    engine = _engine(
        client=client, checkpoints=checkpoints, task_sink=task_sink, batch_size=1, max_attempts=2
    )
    report = engine.run(_candidates(3), job_id="job-fail", run_id=RUN_ID, now=NOW)

    assert not report.ok
    assert [i for i, _ in report.batches_failed] == [0]
    assert report.batches_run == 2  # 另外两批照跑
    assert report.record.failed_batch_count == 1
    assert checkpoints.completed("job-fail") == {1, 2}  # 失败那批不打断点，下轮会重试
    # 部分失败不许被记成 success，否则追溯表看起来一切正常而那批图一条标签都没有
    row = task_sink.rows[0]
    assert row["task_status"] == "failed"
    assert "[0]" in row["error_message"]


def test_a_tag_service_failure_never_marks_the_batch_done():
    """断点只能在**落库之后**打：写库失败还打点，那批图就永久没有标签，而且没人会发现。"""
    checkpoints = V.InMemoryCheckpointStore()
    task_sink = B.InMemoryResultSink()
    tag_sink = ExplodingTagSink()
    engine = _engine(tag_sink=tag_sink, checkpoints=checkpoints, task_sink=task_sink, batch_size=2)
    report = engine.run(_candidates(4), job_id="job-sink", run_id=RUN_ID, now=NOW)

    assert not report.ok
    assert len(report.batches_failed) == 2
    assert all("标签服务写入失败" in msg for _, msg in report.batches_failed)
    assert checkpoints.completed("job-sink") == set()
    assert report.record.tag_written_count == 0
    assert task_sink.rows[0]["task_status"] == "failed"  # 追溯如实记账


def test_an_output_that_is_structurally_invalid_is_judged_not_retried():
    """输出不合格重试还是不合格：整批判废计入 rejected，且不打断点。"""

    class _BadOutputClient(V.VlmClient):
        model_name = "bad-vlm"
        model_version = "v0"

        def __init__(self) -> None:
            self.calls = 0

        def infer_batch(self, candidates, *, prompt):
            self.calls += 1
            raise V.VlmOutputError("模型只回了标签，没有 caption")

    client = _BadOutputClient()
    checkpoints = V.InMemoryCheckpointStore()
    engine = _engine(client=client, checkpoints=checkpoints, batch_size=2, max_attempts=3)
    report = engine.run(_candidates(2), job_id="job-bad", run_id=RUN_ID, now=NOW)

    assert client.calls == 1  # 判废不重试
    assert report.batches_failed == []  # 「判废」不是「失败」
    assert len(report.rejected) == 2
    assert all("产出不合格" in reason for _, reason in report.rejected)
    assert report.record.rejected_count == 2
    assert checkpoints.completed("job-bad") == set()  # 换模型版本后还能重跑


def test_an_output_for_a_foreign_image_is_dropped():
    """模型回了一个不在本批里的 image_id：放行的后果是把标签挂到别的图上。"""

    class _WrongIdClient(V.VlmClient):
        model_name = "wrong-vlm"
        model_version = "v0"

        def infer_batch(self, candidates, *, prompt):
            return [
                V.VlmOutput(
                    image_id="IMG-FROM-ANOTHER-JOB",
                    tags=(V.VlmTag("行人撑着花伞", 0.8),),
                    caption="不属于本批",
                )
            ]

    sink = V.InMemoryModelTagSink()
    report = _engine(client=_WrongIdClient(), tag_sink=sink).run(
        _candidates(1), job_id="job-wrong", run_id=RUN_ID, now=NOW
    )
    assert sink.written == {}
    assert report.rejected and "不在本批候选内" in report.rejected[0][1]


def test_a_caption_redundancy_failure_does_not_roll_back_the_tags():
    """标签表是事实源，向量表那份是副本：副本写失败不该让事实回滚。"""

    class _BrokenCaptionSink(V.CaptionVectorSink):
        def write_captions(self, rows):
            raise B.BackendError("向量表不可写")

    sink = V.InMemoryModelTagSink()
    report = _engine(tag_sink=sink, caption_sink=_BrokenCaptionSink()).run(
        _candidates(2), job_id="job-cap-fail", run_id=RUN_ID, now=NOW
    )
    assert report.ok
    assert len(sink.captions) == 2  # 标签照样落了


def test_candidate_sql_keeps_all_three_gates():
    """选帧三条件缺一不可：只取关键帧、只取脱敏通过的、只扫增量。"""
    engine = _engine()
    wm = W.Watermark(
        rule_id="vlm",
        table=T.DWD_MINING_IMAGE_FRAME_DETAIL.resolve(),
        column=V.keyframe_watermark_column(),
        low=LOW,
        high=NOW,
    )
    sql = engine.candidate_sql(watermark=wm, project_code="PRJ-A", data_ids=(DATA_ID,), limit=100)
    assert "`is_keyframe` = TRUE" in sql
    assert "`desensitize_status` = 'passed'" in sql
    assert "`update_time` >= TIMESTAMP '2026-09-11 10:00:00'" in sql
    assert "ORDER BY `frm`.`keyframe_score` DESC" in sql
    assert "LIMIT 100" in sql
    assert f"{K.INFERENCE_MIN_KEYFRAMES}~{K.INFERENCE_MAX_KEYFRAMES} 关键帧" in sql
    assert V.keyframe_watermark_column() == "update_time"
    for col in T.KEYFRAME_READ_COLUMNS:
        assert f"`{col}`" in sql
    with pytest.raises(ValueError, match="limit 必须为正"):
        engine.candidate_sql(limit=0)


def test_bad_candidate_rows_do_not_take_down_the_batch():
    backend = RoutingBackend(
        rows=[
            {"image_id": "IMG-1", "data_id": DATA_ID, "image_object_key": "s3://x/1.jpg"},
            {"image_id": "IMG-2", "data_id": "", "image_object_key": "s3://x/2.jpg"},
            {"image_id": "", "data_id": DATA_ID},
        ]
    )
    ok, bad = _engine().load_candidates(backend, rule_id="RULE_X", rule_version=3)
    assert [c.image_id for c in ok] == ["IMG-1"]
    assert len(bad) == 2
    assert ok[0].rule_id == "RULE_X" and ok[0].rule_version == 3


def test_the_infer_trace_row_matches_the_registry_projection():
    """推理的追溯行与规则挖掘同表不同投影：多 hit_image_count，不写 sla_breached。"""
    task_sink = B.InMemoryResultSink()
    report = _engine(task_sink=task_sink, batch_size=2).run(
        _candidates(3),
        job_id="job-trace",
        run_id=RUN_ID,
        now=NOW,
        rule_id="RULE_X",
        rule_version=2,
        rule_category="composite",
        rule_priority=0,
        project_code="PRJ-A",
    )
    row = task_sink.rows[0]
    assert list(row) == list(T.VLM_TASK_WRITE_COLUMNS)
    assert row["task_type"] == "vlm_infer" and row["engine"] == "ray"
    assert row["hit_image_count"] == 3 and row["hit_data_count"] == 3
    assert row["tag_write_count"] == report.record.tag_written_count
    assert row["rule_id"] == "RULE_X" and row["rule_version"] == "2"
    assert row["scan_row_count"] == 3
    assert row["duration_sec"] < 60  # 注入 now 不许把耗时算成几万秒
    assert "sla_breached" not in row  # 4 小时 SLA 承诺的是批扫描，不是 GPU 推理
    assert "frame_supplement_triggered" not in row
    assert set(row) <= {c.name for c in registry.by_name("dwd_mining_task_detail").all_columns()}


def test_the_gpu_task_description_carries_no_master_data():
    """控制面信封「不装数据本体」是硬约束：只回标量，不回图片或对象存储 key。"""
    desc = _engine().describe_gpu_task("job-gpu", batches=3, images=96)
    assert desc["task_type"] == "vlm_infer"
    assert desc["engine"] == "ray" and desc["serving_runtime"] == "vllm"
    assert desc["batch_count"] == 3 and desc["image_count"] == 96
    assert all(not isinstance(v, (list, dict)) for v in desc.values())
    assert not any("s3://" in str(v) for v in desc.values())


def test_a_client_without_model_identity_is_refused():
    """血缘要回答「这个标签从哪来」，模型名与版本缺了就答不出。"""

    class _Anonymous(V.VlmClient):
        def infer_batch(self, candidates, *, prompt):
            return []

    with pytest.raises(ValueError, match="model_name"):
        V.VlmInferenceEngine(client=_Anonymous(), tag_sink=V.InMemoryModelTagSink())
    with pytest.raises(ValueError, match="max_attempts"):
        V.VlmInferenceEngine(
            client=V.EchoVlmClient(), tag_sink=V.InMemoryModelTagSink(), max_attempts=0
        )


def test_the_json_checkpoint_survives_a_process_restart(tmp_path: Path):
    """推理作业会被抢占（[S3-01] 五），断点必须落盘且写入是原子的。"""
    path = tmp_path / "ckpt" / "vlm.json"
    store = V.JsonFileCheckpointStore(path)
    store.mark_done("job-p", 0)
    store.mark_done("job-p", 2)
    assert V.JsonFileCheckpointStore(path).completed("job-p") == {0, 2}  # 「重启」后还在
    assert json.loads(path.read_text(encoding="utf-8")) == {"job-p": [0, 2]}
    store.clear("job-p")
    assert V.JsonFileCheckpointStore(path).completed("job-p") == set()

    # 文件损坏时退化成「无断点」（全量重跑），不是崩掉
    path.write_text("{坏掉的 json", encoding="utf-8")
    assert V.JsonFileCheckpointStore(path).completed("job-p") == set()


def test_running_without_a_caption_sink_still_writes_tags():
    """不冗余 caption 不是错误，但收益拿不到——行为上不许因此丢标签。"""
    sink = V.InMemoryModelTagSink()
    report = V.VlmInferenceEngine(client=V.EchoVlmClient(), tag_sink=sink, caption_sink=None).run(
        _candidates(1), job_id="job-nocap", run_id=RUN_ID, now=NOW
    )
    assert report.ok and sink.captions


def test_the_sql_caption_sink_updates_instead_of_inserting():
    """向量行由 Embedding 流水线产出；推理跑在它前面，绝不抢着建一行没有向量的空壳。"""
    backend = RoutingBackend()
    sink = V.SqlCaptionVectorSink(backend=backend)
    written = sink.write_captions([{"image_id": "IMG-1", "caption_text": "施工区锥桶散乱"}])
    assert written == 1
    sql = backend.executed[0]
    assert sql.startswith("UPDATE ")
    assert "dwd_mining_image_vector_detail" in sql
    assert "SET `caption_text` = '施工区锥桶散乱'" in sql
    assert "INSERT" not in sql


# ================================ 九、公开 API 与跨模块契约（判据 B）


def test_every_public_helper_is_exported_by_its_module():
    """找不到内部调用方的公开函数，至少要在 __all__ 里——否则就是没人用的孤岛。"""
    for module, names in (
        (E, ("task_insert_sql", "BatchRuleExecutor", "StreamRuleExecutor")),
        (B, ("rule_job_endpoint", "rule_job_progress_endpoint")),
        (G, ("gap_insert_sql", "tag_coverage_endpoint", "coverage_sql")),
        (V, ("keyframe_watermark_column", "partition_sendable", "INFER_EXEC_MODE")),
        (C, ("harsh_decel_reference_sql",)),
        (R, ("sustained_matches", "SignalSample", "SustainedHit")),
        (T, ("MINING_NEW_TABLES",)),
    ):
        for name in names:
            assert name in module.__all__, f"{module.__name__}.{name} 没在 __all__ 里"
            assert hasattr(module, name)


def test_the_package_reexports_resolve_and_cover_the_orchestration_entrypoints():
    """外部编排只认包级出口：五个引擎入口 + 原文关键数字都要能从包里直接取到。"""
    missing = [n for n in M.__all__ if not hasattr(M, n)]
    assert missing == []
    for name in (
        "BatchRuleExecutor",
        "StreamRuleExecutor",
        "VlmInferenceEngine",
        "SceneGapDetector",
        "RuleConfigLoader",
        "sustained_matches",
        "HARSH_DECEL_THRESHOLD_MPS2",
        "HARSH_DECEL_MIN_DURATION_SEC",
        "EVENT_WINDOW_BEFORE_SEC",
        "EVENT_WINDOW_AFTER_SEC",
    ):
        assert name in M.__all__
    assert M.HARSH_DECEL_THRESHOLD_MPS2 == -4.0
    assert M.HARSH_DECEL_MIN_DURATION_SEC == 0.5


def test_importing_mining_never_needs_a_third_party_driver():
    """裸环境可导入：驱动一律延迟 import，缺了也只在构造那个实现时才报错。"""
    for path in sorted(MINING_SRC.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(
                ("import pymysql", "from pymysql", "import kafka", "from kafka")
            ):
                assert line.startswith((" ", "\t")), f"{path.name} 顶层导入了驱动: {stripped}"
    with pytest.raises(B.BackendError, match="kafka-python"):
        B.KafkaBackfillDispatcher().dispatch(
            [
                B.BackfillRequest.for_event(
                    data_id=DATA_ID, rule_id="R", run_id=RUN_ID, event_time=NOW
                )
            ]
        )


def test_table_names_can_be_overridden_by_environment(monkeypatch):
    """部署环境改物理表名不用改 SQL；表结构仍以 registry 的同名表为准。"""
    monkeypatch.setenv(T.DWD_MINING_RESULT_DETAIL.env_key, "dwd_mining_result_detail_v2")
    assert T.DWD_MINING_RESULT_DETAIL.resolve() == "dwd_mining_result_detail_v2"
    assert T.DWD_MINING_RESULT_DETAIL.spec().name == "dwd_mining_result_detail"
    monkeypatch.setenv(T.VEHICLE_SIGNAL_STREAM.env_key, "kafka_can_signal")
    assert T.VEHICLE_SIGNAL_STREAM.resolve() == "kafka_can_signal"
    assert T.stream_sql(T.VEHICLE_SIGNAL_STREAM) == "`kafka_can_signal`"


def test_column_projections_refuse_a_column_the_registry_lacks():
    """registry 是表结构的唯一事实源：子系统不许另立一份列名。"""
    with pytest.raises(ValueError, match="没有这些列"):
        T.projection(T.DWD_MINING_RESULT_DETAIL, "scene_label")
    with pytest.raises(ValueError, match="重复列"):
        T.projection(T.DWD_MINING_RESULT_DETAIL, "data_id", "data_id")
    assert T.projection(T.DWD_MINING_RESULT_DETAIL, "data_id", "rule_id") == ("data_id", "rule_id")


def test_the_freshness_expression_uses_each_engines_own_date_function():
    """两条腿编译目标是两个引擎：``DATEDIFF`` 是 Spark/Hive 的，Flink 只有
    ``TIMESTAMPDIFF``。混用的后果是流作业**提交时**才报「找不到函数」，
    而准实时作业长驻，提交失败只在网关日志里留一行——线上表现是这条规则从来不出命中。
    """
    batch = (
        C.RuleCompiler()
        .compile_batch(
            _sample("RULE_TAG_RAINY_HIGHWAY"),
            _watermark("RULE_TAG_RAINY_HIGHWAY"),
            run_id=RUN_ID,
            now=NOW,
        )
        .select_sql
    )
    assert "DATEDIFF(" in batch and "TIMESTAMPDIFF" not in batch

    for stream_sql in (
        C.harsh_decel_reference_sql(),  # MATCH_RECOGNIZE 那一路
        C.RuleCompiler()
        .compile_stream(TAKEOVER_RULE, run_id=RUN_ID, now=NOW)
        .select_sql,  # 事件流那一路
    ):
        assert "TIMESTAMPDIFF(DAY," in stream_sql
        assert "DATEDIFF(" not in stream_sql

    # 参数顺序是反的：Spark DATEDIFF(end, start) == Flink TIMESTAMPDIFF(DAY, start, end)
    spark_expr = S.sql_score_expression(
        TAKEOVER_RULE, alias="evt", now=NOW, dialect=R.Dialect.SPARK
    )
    flink_expr = S.sql_score_expression(
        TAKEOVER_RULE, alias="evt", now=NOW, dialect=R.Dialect.FLINK
    )
    assert "DATEDIFF(TIMESTAMP '2026-09-12 10:00:00', `evt`.`collect_start_time`)" in spark_expr
    assert (
        "TIMESTAMPDIFF(DAY, `evt`.`collect_start_time`, TIMESTAMP '2026-09-12 10:00:00')"
        in flink_expr
    )


def test_no_generated_flink_sql_uses_a_spark_only_function():
    """流路产出的 SQL 里不许出现 Spark/Hive 专有函数名——Flink 侧没有等价实现。"""
    spark_only = ("DATEDIFF(", "DATE_ADD(", "NVL(", "IFNULL(", "UNIX_TIMESTAMP(")
    compiler = C.RuleCompiler()
    stream_rules = [r for r in R.SAMPLE_RULES if r.effective_mode is R.ExecutionMode.NEAR_REALTIME]
    assert stream_rules
    for rule in stream_rules:
        sql = compiler.compile_stream(rule, run_id=RUN_ID, now=NOW).select_sql
        for fn in spark_only:
            assert fn not in sql, f"{rule.rule_id} 的流 SQL 用了 Spark 专有函数 {fn}"


def test_batch_counters_never_exceed_the_total():
    """实跑 + 跳过 + 失败 ≤ 总批次：三个计数器互不重复计一批，追溯表的账才对得上。"""
    checkpoints = V.InMemoryCheckpointStore()
    checkpoints.mark_done("job-count", 0)
    engine = _engine(tag_sink=ExplodingTagSink(), checkpoints=checkpoints, batch_size=1)
    report = engine.run(_candidates(3), job_id="job-count", run_id=RUN_ID, now=NOW)
    assert report.batches_total == 3
    assert report.batches_skipped == [0]
    assert len(report.batches_failed) == 2  # 另外两批都卡在打标
    assert report.batches_run == 0  # 写库没成功就不算实跑
    assert report.batches_run + len(report.batches_skipped) + len(report.batches_failed) == 3
