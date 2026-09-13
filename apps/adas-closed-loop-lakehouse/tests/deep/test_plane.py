"""深度审计：GPU 池分时复用与优先级抢占 / Ray + vLLM 推理引擎 / 控制面-数据面分离。

对应原文：系列三第 1 篇《数据闭环数据挖掘平台架构设计：控制面/数据面分离的工程实践》
（2026-09-09，https://mp.weixin.qq.com/s/bSekzM_WjtGtAd_1WdbBAQ）。

本文件的断言原则：**打在原文给的具体数值与逐字表述上**，不写「函数能跑通」这类
无判别力的断言。原文没给的数字（峰值期区间、抽样比例、抢占优先级差…）只断言
「代码里确实把它标成了本项目设计」以及它的行为自洽，不冒充原文方案。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from adas_lakehouse import dataplane as D
from adas_lakehouse.controlplane import (
    ControlPlane,
    ReviewDecision,
    SubmitRequest,
    TaskEnvelope,
    TaskKind,
    TaskState,
)
from adas_lakehouse.controlplane import constants as K
from adas_lakehouse.controlplane.contracts import (
    WRITEBACK_COLUMN_MAP,
    WRITEBACK_UNMAPPED_FIELDS,
    ControlStateLeak,
    assert_no_control_state,
    writeback_column_gap,
)
from adas_lakehouse.controlplane.reconcile import reconcile_tasks
from adas_lakehouse.dataplane.ray_engine import (
    RAY_CAPABILITIES_REQUIRED,
    TRITON_REJECTION_REASON,
    EchoBatchRunner,
    InMemoryCheckpointStore,
    RayInferenceEngine,
    RayVllmBatchRunner,
    VllmEngineConfig,
    describe_engine_choice,
)

# --------------------------------------------------------------------------- 时刻夹具
#
# 原文给的唯一硬时间是「凌晨 6 点前完成」，所以下面的时刻全部围绕 6 点这条线选：

#: 凌晨窗口内（Embedding 该跑的时候）
NIGHT = datetime(2026, 3, 2, 3, 0, 0)
#: 凌晨窗口的最后一秒
NIGHT_EDGE = datetime(2026, 3, 2, 5, 59, 59)
#: 原文的截止线：凌晨 6 点整，此刻起窗口已经交还给推理
DEADLINE = datetime(2026, 3, 2, 6, 0, 0)
#: 白天 + 峰值期（⚠️ 峰值期区间为本项目设计）
PEAK = datetime(2026, 3, 2, 10, 0, 0)
#: 白天但非峰值期
OFF_PEAK = datetime(2026, 3, 2, 20, 0, 0)

RUN_ID_VLM = "run_vlm_20260302100000_0001"
RUN_ID_EMB = "run_embedding_20260302030000_0001"


def _envelope(
    task_id: str,
    kind: TaskKind = TaskKind.VLM_INFERENCE,
    *,
    priority: int = K.PRIORITY_DEFAULT,
    run_id: str | None = None,
) -> TaskEnvelope:
    stage = "vlm" if kind is TaskKind.VLM_INFERENCE else "embedding"
    return TaskEnvelope(
        task_id=task_id,
        kind=kind,
        subsystem="mining" if kind is TaskKind.VLM_INFERENCE else "vector",
        run_id=run_id or f"run_{stage}_20260302100000_{abs(hash(task_id)) % 9000 + 1000}",
        priority=priority,
    )


# ===========================================================================
# 一、原文数值逐字核对
# ===========================================================================


def test_embedding_deadline_is_exactly_six_the_only_hard_number_in_the_gpu_section():
    """原文第五章唯一的硬数字：Embedding「凌晨 6 点前完成」。"""
    assert K.EMBEDDING_WINDOW_DEADLINE_HOUR == 6
    assert D.EMBEDDING_WINDOW_DEADLINE_HOUR == 6
    # 凌晨窗口 [0, 6)：6 点整就不再是 Embedding 的窗口了
    assert D.current_window(datetime(2026, 3, 2, 0, 0, 0)) is D.GpuWindow.NIGHT_EMBEDDING
    assert D.current_window(NIGHT_EDGE) is D.GpuWindow.NIGHT_EMBEDDING
    assert D.current_window(DEADLINE) is D.GpuWindow.DAY_INFERENCE


def test_embedding_deadline_check_draws_the_line_at_six_sharp():
    """「6 点前完成」——5:59:59 算数，6:00:01 不算数，6:00:00 整正好踩线。"""
    start = datetime(2026, 3, 2, 0, 30)
    assert D.embedding_deadline_ok(start, NIGHT_EDGE)
    assert D.embedding_deadline_ok(start, DEADLINE)
    assert not D.embedding_deadline_ok(start, datetime(2026, 3, 2, 6, 0, 1))
    assert not D.embedding_deadline_ok(start, datetime(2026, 3, 2, 6, 1))
    with pytest.raises(ValueError):
        D.embedding_deadline_ok(DEADLINE, start)


def test_online_zone_spec_is_four_cores_eight_gb_two_replicas():
    """原文第五章部署表格：在线服务区「每服务 4C8G × 2 起」。"""
    assert (K.ONLINE_SERVICE_CPU_CORES, K.ONLINE_SERVICE_MEMORY_GB) == (4, 8)
    assert K.ONLINE_SERVICE_MIN_REPLICAS == 2
    online = next(z for z in D.DEPLOY_ZONES if z.zone is D.DeployZone.ONLINE)
    assert (online.cpu_cores, online.memory_gb, online.min_replicas) == (4, 8, 2)
    assert "4C8G × 2 起" in online.scaling


def test_k8s_has_exactly_three_zones_and_gpu_zone_scaling_is_verbatim():
    """原文第五章：「按「三区 + 托管依赖」组织」，GPU 区伸缩策略逐字三件套。"""
    assert K.DEPLOY_ZONE_COUNT == 3
    assert len(D.DEPLOY_ZONES) == 3
    gpu_zone = next(z for z in D.DEPLOY_ZONES if z.zone is D.DeployZone.GPU)
    assert gpu_zone.scaling == "分时复用 + 优先级队列 + 弹性扩缩"
    assert gpu_zone.components == "Ray 集群（VLM 推理 vLLM / Embedding）"
    # 池子自己也认这条策略，三样都得真的实现（分时 / 优先级 / 扩缩）
    assert gpu_zone.scaling == D.GPU_POOL_SCALING_POLICY


def test_high_value_sources_are_the_three_the_article_names():
    """原文第五章括号内容：「高价值数据（规则命中 / 事件抽帧 / VLM 标签）优先向量化」。"""
    assert D.HIGH_VALUE_SOURCES == ("规则命中", "事件抽帧", "VLM 标签")
    for key in ("rule_hit", "event_trigger", "vlm_tag"):
        assert D.classify_value_tier(key) is D.ValueTier.HIGH


def test_ordinary_data_sampling_is_ten_percent_marked_as_project_design():
    """原文只说「普通数据抽样处理」，没给比例——10% 是本项目设计，但必须自洽。"""
    assert D.ORDINARY_DATA_SAMPLE_RATIO == 0.1
    assert D.vectorization_quota("uniform_sampling", 1_000_000) == 100_000
    # 高价值全量：原文「优先向量化」= 一条不落
    assert D.vectorization_quota("rule_hit", 1_000_000) == 1_000_000
    assert "⚠️ 原文未明确" in (D.gpu.__doc__ or "")  # 自补参数必须标注出处
    assert "本项目设计" in D.describe_gpu_policy()["cost_tiers"]["ordinary"]


def test_vector_pipeline_is_t_plus_one():
    """原文第一章：「T+1 向量化流水线构建图片与文本向量」。"""
    assert K.VECTOR_PIPELINE_LAG_DAYS == 1
    embedding = D.engine_for(TaskKind.EMBEDDING)
    assert "T+1" in embedding.note
    assert f"凌晨 {K.EMBEDDING_WINDOW_DEADLINE_HOUR} 点前完成" in embedding.note


def test_the_four_core_engines_and_their_runtimes_are_verbatim():
    """原文第三章核心引擎层：抽帧（K8s Job）/ 规则挖掘（Spark 批 + Flink 流）/
    VLM 推理（Ray + GPU）/ Embedding 流水线。"""
    assert K.CORE_CAPABILITY_COUNT == 4
    assert len(D.ENGINES) == 4
    runtimes = {e.key: e.runtime for e in D.ENGINES}
    assert runtimes["frame_sampling"] == "K8s Job"
    assert runtimes["rule_mining"] == "Spark 批 + Flink 流"
    assert runtimes["vlm_inference"] == "Ray + GPU"
    assert "vLLM" in runtimes["embedding"]  # 与推理同池：Ray + vLLM


def test_triton_rejection_reason_is_verbatim_and_defines_our_obligations():
    """原文第五章：「GPU 推理调度选 Ray + vLLM（Triton 适合单模型服务化，
    但缺任务编排与断点续跑）」——这句话既是选型也是验收标准。"""
    tradeoff = next(t for t in D.TECH_TRADEOFFS if t.decision == "GPU 推理调度")
    assert tradeoff.chosen == "Ray + vLLM"
    assert tradeoff.rejected == "Triton"
    assert tradeoff.reason == "Triton 适合单模型服务化，但缺任务编排与断点续跑"
    assert tradeoff.reason == TRITON_REJECTION_REASON
    # 放弃 Triton 换来的两样能力必须真的交付
    assert RAY_CAPABILITIES_REQUIRED == ("任务编排", "断点续跑")
    delivered = describe_engine_choice()["capabilities_we_must_deliver"]
    assert set(delivered) == set(RAY_CAPABILITIES_REQUIRED)


def test_the_other_three_tech_tradeoffs_are_verbatim():
    """原文第五章一共四个取舍，一个不少。"""
    assert len(D.TECH_TRADEOFFS) == 4
    by_decision = {t.decision: t for t in D.TECH_TRADEOFFS}
    assert by_decision["批处理"].chosen == "Spark on K8s"
    assert by_decision["批处理"].reason == "迭代开发与 UDF 扩展性不足"
    assert by_decision["流处理"].chosen == "Flink"
    assert by_decision["流处理"].reason == "事件准实时语义弱"
    assert by_decision["向量检索"].chosen == "StarRocks 外部表 HNSW"


def test_control_plane_structural_counts_match_the_article():
    """五层架构 / 五个微服务 / 五条对齐约定 / 11 张表 / 四组接口 / 四级权限。"""
    assert K.APP_ARCH_LAYER_COUNT == len(K.APP_ARCH_LAYERS) == 5
    assert K.APP_SERVICE_COUNT == len(K.APP_SERVICES) == 5
    assert K.ALIGNMENT_PRINCIPLE_COUNT == len(K.ALIGNMENT_PRINCIPLES) == 5
    assert K.MINING_TABLE_COUNT == 11
    assert (
        K.MINING_ODS_TABLE_COUNT
        + K.MINING_DWD_TABLE_COUNT
        + K.MINING_DWS_TABLE_COUNT
        + K.MINING_ADS_TABLE_COUNT
        == 11
    )
    assert (K.MINING_ODS_TABLE_COUNT, K.MINING_DWD_TABLE_COUNT) == (1, 8)
    assert K.OPENAPI_GROUP_COUNT == 4
    assert K.LAKEHOUSE_PERMISSION_LEVELS == 4
    assert K.HEALTH_CRITERION == "把平台的数据库清空重建，业务数据是否完好？"


# ===========================================================================
# 二、GPU 池：分时复用
# ===========================================================================


def test_embedding_and_inference_share_one_pool_but_not_one_window():
    """原文：「VLM 推理与 Embedding 共享同一个 GPU 池……用时间错峰避免资源争抢」。"""
    pool = D.GpuPool(slots=4)
    emb = _envelope("t_emb", TaskKind.EMBEDDING, run_id=RUN_ID_EMB)
    vlm = _envelope("t_vlm", TaskKind.VLM_INFERENCE, run_id=RUN_ID_VLM)
    pool.offer(emb)
    pool.offer(vlm)  # 同一个池子同时接住两类任务 —— 共享同池

    night = pool.schedule(NIGHT)
    assert [lease.task_id for lease in night] == ["t_emb"]  # 凌晨只放 Embedding
    assert pool.queued_task_ids() == ["t_vlm"]  # 推理在排队等白天窗口

    pool.release("t_emb")
    day = pool.schedule(PEAK)
    assert [lease.task_id for lease in day] == ["t_vlm"]  # 白天只放推理


def test_non_gpu_kinds_are_refused_by_the_pool():
    """规则挖掘白天跑批，不吃 GPU——塞进来直接报错，而不是悄悄占卡。"""
    pool = D.GpuPool(slots=1)
    with pytest.raises(ValueError, match="不吃 GPU"):
        pool.offer(_envelope("t_rule", TaskKind.RULE_MINING))


def test_offering_the_same_task_twice_does_not_double_book_a_slot():
    """同一个 task_id 重复 offer 必须幂等，否则它会同时占两张卡写同一批产物。"""
    pool = D.GpuPool(slots=2)
    env = _envelope("t_dup")
    assert pool.offer(env) is True
    assert pool.offer(env) is False
    assert pool.queued_task_ids() == ["t_dup"]
    pool.schedule(PEAK)
    assert pool.offer(env) is False  # 已经在跑了，更不能再排一次
    assert pool.running_task_ids() == ["t_dup"]


def test_priority_queue_orders_by_value_then_arrival():
    """数值越小越优先；同优先级按入队先后。"""
    pool = D.GpuPool(slots=1)
    pool.offer(_envelope("low", priority=K.PRIORITY_LOWEST))
    pool.offer(_envelope("high", priority=K.PRIORITY_HIGHEST))
    pool.offer(_envelope("mid_first", priority=K.PRIORITY_DEFAULT))
    pool.offer(_envelope("mid_second", priority=K.PRIORITY_DEFAULT))
    assert pool.queued_task_ids() == ["high", "mid_first", "mid_second", "low"]


# ===========================================================================
# 三、GPU 池：优先级抢占的完整语义
# ===========================================================================


def _pool_with_running_low_priority_task(priority: int = K.PRIORITY_LOWEST) -> D.GpuPool:
    pool = D.GpuPool(slots=1)
    pool.offer(_envelope("victim", priority=priority))
    assert [lease.task_id for lease in pool.schedule(PEAK)] == ["victim"]
    return pool


def test_peak_hours_are_project_design_and_exclude_the_embedding_window():
    """⚠️ 峰值期区间是本项目设计；但它必须与原文的凌晨窗口不重叠，否则两条规则打架。"""
    assert frozenset({9, 10, 11, 14, 15, 16, 17}) == D.PEAK_HOURS
    for hour in D.PEAK_HOURS:
        assert hour >= K.EMBEDDING_WINDOW_DEADLINE_HOUR, "峰值期不能落进凌晨 Embedding 窗口"
        assert D.is_peak(datetime(2026, 3, 2, hour, 30))
    assert D.is_peak(PEAK)
    assert not D.is_peak(OFF_PEAK)
    assert not D.is_peak(NIGHT)


def test_peak_high_priority_task_preempts_a_low_priority_one():
    """原文第五章：「峰值期低优任务可被抢占」。"""
    pool = _pool_with_running_low_priority_task()
    pool.offer(_envelope("winner", priority=K.PRIORITY_HIGHEST))

    granted = pool.schedule(PEAK)

    assert [lease.task_id for lease in granted] == ["winner"]
    assert pool.running_task_ids() == ["winner"]
    assert pool.lease_for("victim") is None


def test_the_preempted_task_is_requeued_not_dropped():
    """「抢完被抢的任务怎么办」：当场交卡 → 按原序回队 → 留痕 → 下次接着跑。"""
    pool = _pool_with_running_low_priority_task()
    pool.offer(_envelope("winner", priority=K.PRIORITY_HIGHEST))
    pool.schedule(PEAK)

    # 1. 回队，没有丢
    assert "victim" in pool.queued_task_ids()
    # 2. 被标记为「被抢占」，执行侧据此决定从 checkpoint 续跑
    assert pool.is_preempted("victim")
    # 3. 留痕可查
    (record,) = pool.preemption_log()
    assert record.victim_task_id == "victim"
    assert record.winner_task_id == "winner"
    assert record.victim_priority == K.PRIORITY_LOWEST
    assert record.winner_priority == K.PRIORITY_HIGHEST
    assert record.reason is D.EvictReason.PEAK_PREEMPTION
    assert record.requeued is True
    assert record.wasted_seconds >= 0.0
    # 4. 抢占者跑完交卡，被抢的立刻拿到卡接着跑
    pool.release("winner")
    assert [lease.task_id for lease in pool.schedule(PEAK)] == ["victim"]


def test_a_requeued_victim_keeps_its_place_ahead_of_later_arrivals():
    """被抢者按**原入队序号**回队：不能被后来的同优先级任务插队，否则会被反复饿死。"""
    pool = D.GpuPool(slots=1)
    pool.offer(_envelope("victim", priority=K.PRIORITY_LOWEST))
    pool.schedule(PEAK)
    pool.offer(_envelope("winner", priority=K.PRIORITY_HIGHEST))
    pool.schedule(PEAK)  # victim 被抢，回队
    pool.offer(_envelope("latecomer", priority=K.PRIORITY_LOWEST))  # 后来者，同优先级

    assert pool.queued_task_ids() == ["victim", "latecomer"]


def test_off_peak_never_preempts_even_with_the_highest_priority():
    """非峰值期不抢占：等待成本低于重跑成本，原文也只授权了峰值期。"""
    pool = D.GpuPool(slots=1)
    pool.offer(_envelope("victim", priority=K.PRIORITY_LOWEST))
    assert [lease.task_id for lease in pool.schedule(OFF_PEAK)] == ["victim"]

    pool.offer(_envelope("winner", priority=K.PRIORITY_HIGHEST))
    assert pool.schedule(OFF_PEAK) == []  # 抢不动

    assert pool.running_task_ids() == ["victim"]  # 低优任务安然跑完
    assert pool.queued_task_ids() == ["winner"]  # 高优任务老实排队
    assert pool.preemption_log() == []
    assert not pool.is_preempted("victim")


def test_who_may_preempt_whom_is_decided_by_can_preempt():
    """谁能抢谁：峰值期 + 被抢者可抢 + 优先级差 ≥ 2（⚠️ 差值为本项目设计）。"""
    assert D.DEFAULT_PREEMPT_PRIORITY_GAP == 2
    victim = D.GpuLease(
        task_id="v",
        kind=TaskKind.VLM_INFERENCE,
        priority=K.PRIORITY_LOWEST,  # 9
        slot=0,
        acquired_at=PEAK,
        preemptible=True,
    )
    assert D.can_preempt(K.PRIORITY_HIGHEST, victim, now=PEAK)  # 0 vs 9：抢
    assert not D.can_preempt(K.PRIORITY_HIGHEST, victim, now=OFF_PEAK)  # 非峰值期：不抢
    assert not D.can_preempt(8, victim, now=PEAK)  # 只差 1 档：不值得抢

    protected = D.GpuLease(
        task_id="p",
        kind=TaskKind.VLM_INFERENCE,
        priority=K.PRIORITY_DEFAULT,
        slot=0,
        acquired_at=PEAK,
        preemptible=False,
    )
    assert not D.can_preempt(K.PRIORITY_HIGHEST, protected, now=PEAK)


def test_default_priority_inference_is_not_preemptible_but_embedding_always_is():
    """高优推理不可被抢（否则优先级失去意义）；Embedding 有整个凌晨窗口可以重来。"""
    pool = D.GpuPool(slots=1)
    pool.offer(_envelope("mid", priority=K.PRIORITY_DEFAULT))
    (lease,) = pool.schedule(PEAK)
    assert lease.preemptible is False

    night_pool = D.GpuPool(slots=1)
    night_pool.offer(_envelope("emb", TaskKind.EMBEDDING, priority=K.PRIORITY_HIGHEST))
    (emb_lease,) = night_pool.schedule(NIGHT)
    assert emb_lease.preemptible is True
    assert emb_lease.window is D.GpuWindow.NIGHT_EMBEDDING


def test_a_low_priority_task_cannot_jump_the_queue_when_the_head_is_blocked():
    """队头抢不到卡就停止本轮——否则低优任务会越过高优任务先拿到卡。"""
    pool = D.GpuPool(slots=1)
    pool.offer(_envelope("occupier", priority=K.PRIORITY_DEFAULT))
    pool.schedule(PEAK)
    pool.offer(_envelope("blocked_high", priority=K.PRIORITY_HIGHEST))
    pool.offer(_envelope("eager_low", priority=K.PRIORITY_LOWEST))

    assert pool.schedule(PEAK) == []
    assert sorted(pool.queued_task_ids()) == ["blocked_high", "eager_low"]


# ===========================================================================
# 四、GPU 池：弹性扩缩
# ===========================================================================


def test_scale_out_lets_queued_tasks_run_immediately():
    """原文 GPU 区伸缩策略的第三件事：弹性扩缩。"""
    pool = D.GpuPool(slots=1, max_slots=4)
    pool.offer(_envelope("a", priority=0))
    pool.offer(_envelope("b", priority=1))
    pool.schedule(PEAK)
    assert pool.queued_task_ids() == ["b"]

    outcome = pool.scale_to(2, now=PEAK)
    assert outcome.direction == "scale_out"
    assert (outcome.before, outcome.after) == (1, 2)
    assert [lease.task_id for lease in pool.schedule(PEAK)] == ["b"]
    assert pool.utilization() == 1.0


def test_scale_in_evicts_from_the_tail_and_requeues_the_evicted():
    """缩容不丢任务：被裁掉槽位上的任务走与抢占完全一样的善后路径。"""
    pool = D.GpuPool(slots=2)
    pool.offer(_envelope("keep", priority=0))
    pool.offer(_envelope("evicted", priority=1))
    pool.schedule(PEAK)

    outcome = pool.scale_to(1, now=PEAK)

    assert outcome.direction == "scale_in"
    assert outcome.evicted_task_ids == ("evicted",)
    assert pool.running_task_ids() == ["keep"]
    assert "evicted" in pool.queued_task_ids()
    (record,) = pool.preemption_log()
    assert record.reason is D.EvictReason.SCALE_IN
    assert record.requeued is True


def test_scaling_respects_its_bounds():
    pool = D.GpuPool(slots=2, max_slots=4)
    assert pool.max_slots == 4
    with pytest.raises(ValueError):
        pool.scale_to(0)
    with pytest.raises(ValueError, match="扩容上限"):
        pool.scale_to(5)


def test_desired_slots_only_counts_tasks_that_fit_the_current_window():
    """扩缩建议只看「当前窗口能立刻消化的积压」——为等窗口的任务扩容等于制造争抢。"""
    pool = D.GpuPool(slots=1, max_slots=8)
    pool.offer(_envelope("vlm1", priority=0))
    pool.offer(_envelope("vlm2", priority=1))
    pool.offer(_envelope("emb", TaskKind.EMBEDDING, run_id=RUN_ID_EMB))

    assert pool.desired_slots(PEAK) == 2  # 两个推理算数，Embedding 不算
    assert pool.desired_slots(NIGHT) == 1  # 凌晨反过来，只算 Embedding


def test_cancelling_a_queued_gpu_task_frees_its_place():
    pool = D.GpuPool(slots=1)
    pool.offer(_envelope("running", priority=0))
    pool.schedule(PEAK)
    pool.offer(_envelope("waiting", priority=1))

    assert pool.cancel("waiting") is True
    assert pool.queued_task_ids() == []
    assert pool.cancel("nobody") is False


def test_snapshot_reports_the_policy_and_the_deadline():
    pool = D.GpuPool(slots=2)
    snap = pool.snapshot(PEAK)
    assert snap["embedding_deadline_hour"] == 6
    assert snap["scaling_policy"] == "分时复用 + 优先级队列 + 弹性扩缩"
    assert snap["window"] == D.GpuWindow.DAY_INFERENCE.value
    assert snap["is_peak"] is True
    assert pool.snapshot(NIGHT)["is_peak"] is False


# ===========================================================================
# 五、Ray + vLLM：任务编排与断点续跑（放弃 Triton 换来的两样能力）
# ===========================================================================


def test_plan_splits_the_task_into_ordered_batches():
    """任务编排：Triton 缺的第一样。批次是调度、计费、断点的最小单位。"""
    engine = RayInferenceEngine(pool=D.GpuPool(slots=1), config=VllmEngineConfig(batch_size=64))
    batches = engine.plan(_envelope("t_plan"), [f"clip_{i}" for i in range(200)])

    assert [b.batch_index for b in batches] == [0, 1, 2, 3]
    assert [b.size for b in batches] == [64, 64, 64, 8]
    assert batches[0].data_ids[0] == "clip_0"
    assert batches[3].data_ids[-1] == "clip_199"
    assert D.DEFAULT_INFERENCE_BATCH_SIZE == 64


def test_plan_refuses_non_gpu_kinds_and_empty_input():
    engine = RayInferenceEngine(pool=D.GpuPool(slots=1))
    with pytest.raises(ValueError, match="只接 VLM 推理与 Embedding"):
        engine.plan(_envelope("t", TaskKind.RULE_MINING), ["clip_0"])
    with pytest.raises(ValueError, match="没有任何 data_id"):
        engine.plan(_envelope("t2"), [])


def test_dispatch_runs_every_batch_once_and_hands_the_card_back():
    runner = EchoBatchRunner()
    pool = D.GpuPool(slots=1)
    engine = RayInferenceEngine(pool=pool, runner=runner, config=VllmEngineConfig(batch_size=2))

    result = engine.dispatch(_envelope("t_run"), ["a", "b", "c", "d", "e"], now=PEAK)

    assert result.admitted and result.finished
    assert result.batches_total == 3
    assert runner.calls == [0, 1, 2]
    assert len(result.rows) == 5
    assert pool.running_task_ids() == []  # 跑完交卡


def test_a_task_that_cannot_get_a_card_is_queued_not_failed():
    """没抢到卡不是失败：任务在池子里排着，下一轮再试（原文「按优先级队列执行」）。"""
    pool = D.GpuPool(slots=1)
    engine = RayInferenceEngine(pool=pool, runner=EchoBatchRunner())
    emb = _envelope("t_emb_day", TaskKind.EMBEDDING, run_id=RUN_ID_EMB)

    result = engine.dispatch(emb, ["a", "b"], now=PEAK)  # 白天派发 Embedding

    assert result.admitted is False
    assert result.finished is False
    assert "凌晨" in result.reason and str(K.EMBEDDING_WINDOW_DEADLINE_HOUR) in result.reason
    assert pool.queued_task_ids() == ["t_emb_day"]

    # 到了凌晨窗口，同一个任务不用重新提交就能跑起来
    later = engine.dispatch(emb, ["a", "b"], now=NIGHT)
    assert later.admitted and later.finished


def test_checkpoints_make_a_preempted_task_resume_instead_of_restarting():
    """断点续跑：Triton 缺的第二样，也是抢占之所以可接受的前提。"""
    pool = D.GpuPool(slots=1)
    runner = EchoBatchRunner()
    checkpoints = InMemoryCheckpointStore()
    engine = RayInferenceEngine(
        pool=pool,
        runner=runner,
        checkpoints=checkpoints,
        config=VllmEngineConfig(batch_size=1),
    )
    victim = _envelope("t_victim", priority=K.PRIORITY_LOWEST, run_id=RUN_ID_VLM)
    data_ids = ["a", "b", "c", "d"]

    # 跑完第 0 批之后，一个高优任务在峰值期把卡抢走
    original_run_batch = runner.run_batch

    def run_then_preempt(batch, config):
        out = original_run_batch(batch, config)
        if batch.batch_index == 0:
            pool.offer(_envelope("t_winner", priority=K.PRIORITY_HIGHEST))
            pool.schedule(PEAK)
        return out

    runner.run_batch = run_then_preempt  # type: ignore[method-assign]

    first = engine.dispatch(victim, data_ids, now=PEAK)

    assert first.preempted is True
    assert first.finished is False
    assert first.batches_completed == 1  # 只跑完了第 0 批
    assert engine.resume_point(RUN_ID_VLM) == frozenset({0})
    assert pool.is_preempted("t_victim")

    # 抢占者交卡后重新派发：从断点接着跑，第 0 批**不重算**
    runner.run_batch = original_run_batch  # type: ignore[method-assign]
    pool.release("t_winner")
    second = engine.dispatch(victim, data_ids, now=PEAK)

    assert second.finished is True
    assert second.metrics["resumed_from_batch"] == 1
    assert runner.calls == [0, 1, 2, 3], "每个批次只应该跑一次，断点续跑才算数"
    assert len(second.rows) == 3  # 本轮只产出剩下 3 批的行


def test_run_to_completion_survives_preemption():
    pool = D.GpuPool(slots=1)
    engine = RayInferenceEngine(pool=pool, config=VllmEngineConfig(batch_size=2))
    result = engine.run_to_completion(_envelope("t_loop"), ["a", "b", "c"], now=PEAK)
    assert result.finished
    assert result.metrics["rounds"] >= 1


def test_run_to_completion_gives_up_loudly_when_the_window_never_opens():
    pool = D.GpuPool(slots=1)
    engine = RayInferenceEngine(pool=pool)
    emb = _envelope("t_never", TaskKind.EMBEDDING, run_id=RUN_ID_EMB)
    with pytest.raises(RuntimeError, match="派发"):
        engine.run_to_completion(emb, ["a"], now=PEAK, max_rounds=3)


def test_vllm_engine_config_validates_its_parameters():
    """⚠️ 引擎参数全是本项目设计，但不能让明显非法的值静默通过。"""
    assert VllmEngineConfig().batch_size == D.DEFAULT_INFERENCE_BATCH_SIZE
    for bad in ({"batch_size": 0}, {"tensor_parallel_size": 0}, {"gpu_memory_utilization": 1.5}):
        with pytest.raises(ValueError):
            VllmEngineConfig(**bad)


def test_ray_runtime_must_be_injected_and_says_so_clearly():
    """ray / vllm 是 GPU 节点运行时，本包不声明也不 import，必须注入。"""
    runner = RayVllmBatchRunner()
    batch = D.InferenceBatch(RUN_ID_VLM, 0, ("a",))
    with pytest.raises(RuntimeError, match="ray_module"):
        runner.run_batch(batch, VllmEngineConfig())


# ===========================================================================
# 六、控制面/数据面：GPU 池真的被接上了
# ===========================================================================


class _VlmAdapter(D.BaseSubsystemAdapter):
    """最小推理适配器。``now`` 可注入，好确定性地驱动窗口与峰值期。"""

    name = "mining"
    kinds = frozenset({TaskKind.VLM_INFERENCE, TaskKind.EMBEDDING})

    def __init__(self, data_plane=None, *, now=PEAK):
        super().__init__(data_plane)
        self.clock = now
        self.ran: list[str] = []

    def _now(self):
        return self.clock

    def run(self, ctx):
        self.ran.append(ctx.envelope.task_id)
        # 跑的时候必须已经握着租约——「推理引擎按优先级抢 GPU」不是口号
        assert self.data_plane.gpu_pool.lease_for(ctx.envelope.task_id) is not None
        return [
            D.ExecutionResult(
                target_table=K.TABLE_IMAGE_FRAME_DETAIL,
                rows=[{"image_id": "img_1", "caption": "雨天夜间无灯路口"}],
                data_id="COLLECT_BP_20260301123045_b7e2",
            )
        ]


def test_a_gpu_task_holds_a_lease_while_running_and_releases_it_after():
    plane = D.DataPlane(lake=D.DryRunLakeSink())
    adapter = _VlmAdapter(plane, now=PEAK)
    env = _envelope("t_wired", run_id=RUN_ID_VLM)

    handle = adapter.submit(env)
    report = adapter.poll(handle)

    assert adapter.ran == ["t_wired"]
    assert report.state is TaskState.SUCCEEDED
    assert plane.gpu_pool.running_task_ids() == []  # 跑完交卡


def test_an_embedding_submitted_in_the_daytime_waits_for_the_night_window():
    """分时错峰真的生效在执行链路上，而不只是 GpuPool 内部的一个方法。"""
    plane = D.DataPlane(lake=D.DryRunLakeSink())
    adapter = _VlmAdapter(plane, now=PEAK)
    env = _envelope("t_emb_wait", TaskKind.EMBEDDING, run_id=RUN_ID_EMB)

    handle = adapter.submit(env)
    queued = adapter.poll(handle)

    assert adapter.ran == []  # 一行都没跑
    assert queued.state is TaskState.RUNNING  # 排队不是失败
    assert queued.metrics["gpu_granted"] is False
    assert "窗口" in queued.metrics["gpu_wait_reason"]

    adapter.clock = NIGHT  # 到了凌晨窗口
    done = adapter.poll(handle)
    assert done.state is TaskState.SUCCEEDED
    assert adapter.ran == ["t_emb_wait"]


def test_cancelling_a_task_that_is_still_queued_for_gpu_actually_cancels_it():
    plane = D.DataPlane(lake=D.DryRunLakeSink())
    adapter = _VlmAdapter(plane, now=PEAK)
    env = _envelope("t_cancel", TaskKind.EMBEDDING, run_id=RUN_ID_EMB)
    handle = adapter.submit(env)

    assert adapter.cancel(handle) is True
    assert plane.gpu_pool.queued_task_ids() == []
    assert adapter.cancel(handle) is False  # 取消过了，没有第二次


def test_a_non_gpu_task_never_touches_the_pool():
    class RuleAdapter(D.BaseSubsystemAdapter):
        name = "mining"
        kinds = frozenset({TaskKind.RULE_MINING})

        def run(self, ctx):
            return []

    plane = D.DataPlane(lake=D.DryRunLakeSink())
    adapter = RuleAdapter(plane)
    adapter.submit(
        _envelope("t_rule", TaskKind.RULE_MINING, run_id="run_mining_20260302100000_0001")
    )
    assert plane.gpu_pool.queued_task_ids() == []
    assert plane.gpu_pool.running_task_ids() == []


def test_acquire_gpu_reports_why_it_could_not_get_a_card():
    plane = D.DataPlane(lake=D.DryRunLakeSink(), gpu_pool=D.GpuPool(slots=1))
    first = plane.acquire_gpu(_envelope("t1", priority=0), now=PEAK)
    assert first.granted and first.lease is not None

    second = plane.acquire_gpu(_envelope("t2", priority=K.PRIORITY_DEFAULT), now=PEAK)
    assert second.granted is False
    assert "无空闲卡" in second.reason
    assert second.as_metrics()["gpu_granted"] is False


# ===========================================================================
# 七、控制面不碰数据 / 数据面不存状态
# ===========================================================================


def test_control_state_may_not_be_written_into_a_product_table():
    """反向守卫：数据面产物表里不许出现审核结论、幂等键这类控制面运行态。"""
    with pytest.raises(ControlStateLeak, match="review_decision"):
        assert_no_control_state(
            [{"image_id": "img_1", "review_decision": "approve"}],
            table=K.TABLE_IMAGE_FRAME_DETAIL,
            where="test",
        )
    with pytest.raises(ControlStateLeak, match="idempotency_key"):
        assert_no_control_state(
            {"data_id": "d", "idempotency_key": "k"},
            table=K.TABLE_IMAGE_VECTOR_DETAIL,
            where="test",
        )


def test_the_two_writeback_tables_are_exempt_because_the_article_says_so():
    """原文第四章「控制面回流数据面」点名了这两张表，回流是设计不是泄漏。"""
    from adas_lakehouse.controlplane.contracts import WRITEBACK_TABLES

    assert WRITEBACK_TABLES == ("ods_mining_rule_config", "dwd_mining_task_detail")
    for table in WRITEBACK_TABLES:
        assert_no_control_state(
            [{"review_decision": "approve", "attempt": 2, "task_state": "succeeded"}],
            table=table,
            where="test",
        )


def test_the_data_plane_refuses_to_write_control_state_into_a_product_table():
    """守卫真的接在写湖仓的路径上，不是一个没人调的函数。"""
    plane = D.DataPlane(lake=D.DryRunLakeSink())
    env = _envelope("t_leak", TaskKind.RULE_MINING, run_id="run_mining_20260302100000_0002")
    result = D.ExecutionResult(
        target_table=K.TABLE_IMAGE_FRAME_DETAIL,
        rows=[{"image_id": "img_1", "reviewer": "alice"}],
        data_id="COLLECT_BP_20260301123045_b7e2",
    )
    with pytest.raises(ControlStateLeak, match="reviewer"):
        plane.execute(env, [result])


def test_master_data_still_may_not_ride_into_the_control_plane():
    """另一半守卫没被削弱：两条闸都在。"""
    from adas_lakehouse.controlplane import MasterDataLeak, assert_no_master_data

    with pytest.raises(MasterDataLeak):
        assert_no_master_data({"embedding": [0.1] * 8}, where="test")
    assert_no_master_data({"vector_dim": 512, "artifact_id": "art_x"}, where="test")


# ===========================================================================
# 八、控制面回流数据面：列名与取值以 catalog 为准
# ===========================================================================


def _succeeded_record():
    plane = ControlPlane()
    task = plane.submit(
        SubmitRequest(kind=TaskKind.VLM_INFERENCE, input_selector="dt='2026-03-01'")
    )
    record = plane._advance(task, TaskState.DISPATCHED, detail="t")
    record = plane._advance(record, TaskState.RUNNING, detail="t")
    record = plane._advance(record, TaskState.AWAITING_REVIEW, detail="t")
    return plane, plane.review(record.task_id, ReviewDecision.APPROVE, reviewer="alice")


def test_writeback_row_uses_catalog_column_names():
    """catalog/registry.py 是表结构唯一事实源：控制面内部叫法必须转译成表的列名。"""
    from adas_lakehouse.catalog import registry

    _, record = _succeeded_record()
    row = record.as_writeback_row()
    columns = {c.name for c in registry.by_name(K.TABLE_TASK_DETAIL).columns}

    for column in WRITEBACK_COLUMN_MAP.values():
        assert column in columns, f"{column} 不是 {K.TABLE_TASK_DETAIL} 的列"
        assert column in row, f"回写行漏了 {column}"
    # 控制面的内部叫法不该原样落表
    assert "task_id" not in row and "mining_task_id" in row
    assert "task_kind" not in row and "task_type" in row


def test_writeback_values_collapse_into_the_catalog_vocabulary():
    """task_type / task_status 的取值表由 catalog 该列注释定义，不能写它不认识的字面量。"""
    assert TaskKind.VLM_INFERENCE.writeback_value == "vlm_infer"
    assert TaskKind.FRAME_SAMPLING.writeback_value == "frame_extract"
    assert TaskKind.RULE_MINING.writeback_value == "rule_mining"
    assert TaskKind.EMBEDDING.writeback_value == "embedding"

    assert TaskState.QUEUED.writeback_value == "pending"
    assert TaskState.DISPATCHED.writeback_value == "running"
    assert TaskState.AWAITING_REVIEW.writeback_value == "running"
    assert TaskState.SUCCEEDED.writeback_value == "success"
    assert TaskState.CANCELLED.writeback_value == "canceled"  # catalog 拼的是单 l
    # 10 态全部有归宿，不许漏
    for state in TaskState:
        assert state.writeback_value


def test_execution_timestamps_are_stamped_so_the_trace_columns_can_be_filled():
    """catalog 的 dwd_mining_task_detail 要 start_time / end_time / duration_sec。"""
    _, record = _succeeded_record()
    assert record.started_at is not None
    assert record.finished_at is not None
    assert record.duration_seconds is not None and record.duration_seconds >= 0
    row = record.as_writeback_row()
    assert row["start_time"] == record.started_at
    assert row["end_time"] == record.finished_at
    assert row["task_status"] == "success"


def test_writeback_column_gap_names_exactly_what_catalog_still_owes_us():
    """回流链路的可执行对账：缺的列报出来交给建表侧，不静默丢审计信息。"""
    gap = writeback_column_gap()
    assert gap["table"] == K.TABLE_TASK_DETAIL
    assert set(gap["missing_columns"]) <= set(WRITEBACK_UNMAPPED_FIELDS), (
        "回写行里出现了既不在 catalog、也没登记进 WRITEBACK_UNMAPPED_FIELDS 的键"
    )
    assert set(WRITEBACK_COLUMN_MAP.values()) <= set(gap["mapped_columns"])


def test_reconcile_reads_both_spellings_and_normalises_the_states():
    """对账要能直接吃两侧原始行：控制面 task_id/queued，湖仓 mining_task_id/pending。"""
    report = reconcile_tasks(
        control_rows=[{"task_id": "T1", "task_state": "queued"}],
        lake_rows=[{"mining_task_id": "T1", "task_status": "pending"}],
    )
    assert report.is_consistent, report.as_dict()
    assert report.aligned == 1

    mismatched = reconcile_tasks(
        control_rows=[{"task_id": "T1", "task_state": "succeeded"}],
        lake_rows=[{"mining_task_id": "T1", "task_status": "failed"}],
    )
    assert mismatched.state_mismatch == (("T1", "success", "failed"),)
    assert mismatched.baseline == "湖仓（Paimon）"


# ===========================================================================
# 九、健康判据：真的清一次库
# ===========================================================================


def test_rebuild_drill_actually_empties_the_control_plane_and_data_survives():
    """原文第四章 💡：「把平台的数据库清空重建，业务数据是否完好？」——真清一次。"""
    plane = ControlPlane()
    for _ in range(3):
        plane.submit(SubmitRequest(kind=TaskKind.RULE_MINING, input_selector=f"dt='{_}'"))
    lake_rows = 1_000_000  # 假装湖仓里有一百万行业务数据

    check = plane.rebuild_drill(lambda: lake_rows)

    assert check.criterion == "把平台的数据库清空重建，业务数据是否完好？"
    assert check.drill_executed is True
    assert check.control_rows_dropped == 3  # 控制面确实被清空了
    assert check.data_plane_before == check.data_plane_after == lake_rows  # 业务数据完好
    assert check.passed is True
    assert plane.queue_depth()["queued"] == 0


def test_rebuild_drill_fails_when_clearing_the_control_plane_takes_data_with_it():
    """探针读数变了 = 控制面持有了本该在湖仓的数据 = 判据不通过。"""
    plane = ControlPlane()
    plane.submit(SubmitRequest(kind=TaskKind.RULE_MINING))
    readings = iter([1_000_000, 999_999])

    check = plane.rebuild_drill(lambda: next(readings))

    assert check.passed is False
    assert "违反第一设计原则" in check.detail


def test_static_rebuild_check_still_passes_and_says_why():
    check = ControlPlane().rebuild_check()
    assert check.passed is True
    assert check.drill_executed is False
    assert "主数据" in check.detail


# ===========================================================================
# 十、边界契约：两个信封，方向单一
# ===========================================================================


def test_the_data_plane_may_not_declare_control_plane_only_states():
    """排队 / 下发 / 取消是控制面的权力，数据面无权宣告。"""
    from adas_lakehouse.controlplane import PlaneBoundaryError, RunReport

    for forbidden in (
        TaskState.QUEUED,
        TaskState.DISPATCHED,
        TaskState.CANCELLED,
        TaskState.REJECTED,
    ):
        with pytest.raises(PlaneBoundaryError, match="数据面无权宣告状态"):
            RunReport(run_id=RUN_ID_VLM, task_id="t", state=forbidden)


def test_every_data_plane_asset_lives_in_the_lakehouse_and_every_control_asset_does_not():
    """原文第四章两句话的封闭世界：没有第三个地方可以放东西。"""
    from adas_lakehouse.controlplane import (
        CONTROL_PLANE_ASSETS,
        DATA_PLANE_ASSETS,
        Plane,
        asset_plane,
    )

    assert set(K.DATA_PLANE_MASTER_DATA) == {"clip", "图片", "标签", "向量"}
    for asset in DATA_PLANE_ASSETS:
        assert asset_plane(asset) is Plane.DATA
    for asset in CONTROL_PLANE_ASSETS:
        assert asset_plane(asset) is Plane.CONTROL
    assert set(CONTROL_PLANE_ASSETS) & set(DATA_PLANE_ASSETS) == set()


def test_gpu_policy_description_covers_all_four_clauses_of_the_article():
    policy = D.describe_gpu_policy()
    assert policy["shared_pool"] == "VLM 推理与 Embedding 共享同一个 GPU 池"
    assert policy["preemption"] == "峰值期低优任务可被抢占"
    assert policy["goal"] == "GPU 成本花在刀刃上"
    assert "凌晨 6 点前完成" in policy["night_window"]
    rules = policy["preemption_rules"]
    assert "当场交卡" in rules["victim_fate"] and "回队" in rules["victim_fate"]
    assert "净收益为负" in rules["why_not_off_peak"]
