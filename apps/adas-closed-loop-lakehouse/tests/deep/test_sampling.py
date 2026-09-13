"""深度对账：三级分层抽帧的三道成本闸门。

对账基准是原文——系列三 · 数据挖掘与 AI 第 2 篇《数据闭环分层抽帧策略：从 TB 级
采集数据中提取高价值帧：三道成本闸门》（公众号「小周」，2026-09-10，
https://mp.weixin.qq.com/s/RrD59_FPqek-zSFMIdCRKQ）。

本文件的断言纪律：**打在原文给的具体数字上**，不满足于「函数能跑通」。
原文原话逐条对应到下面的测试名：

    引言  "一路摄像头 10 秒 30 帧就是 300 张图"          → 30 fps / 300 帧基线
    二    闸门一 全量 clip，"默认 2 秒 1 帧"              → 保留 1/60，过滤 59/60
    二    闸门二 "事件前 15 秒 + 后 5 秒共 20 秒窗口，
                 1 秒 1 帧（约 20 帧）"                   → 20 个采样点，保留 20/600 = 1/30
    二    闸门三 "每 clip 打分选 1~5 关键帧"              → 数量恒落在 [1, 5]
    三    "又不至于把 20 秒变成 600 张全量帧"            → 窗口全量分母 = 600
    三    触发条件四类 / "二次抽帧" / "异步补抽"          → 补抽不阻塞主链路
    四    打分三维：清晰度 / 目标丰富度 / 时间位置        → 三个维度分单独落湖
    四    "规则命中、事件抽帧产出的帧优先进 VLM，普通帧抽样"
    五    多路摄像头同步 / 批流双模 / 统一帧表 / 脱敏前置校验
"""

from __future__ import annotations

from datetime import datetime, timedelta
from fractions import Fraction

import pytest

from adas_lakehouse import sampling as S
from adas_lakehouse.sampling import constants as K

DATA_ID = "COLLECT_BP_20260301123045_b7e2"
CLIP_START = datetime(2026, 3, 1, 12, 30, 45)
NOW = datetime(2026, 3, 1, 13, 0, 0)
#: 两路摄像头：一前视一左侧视，对应原文 "前视、侧视、后视等多路摄像头"
CAMERAS = ("cam-front", "side-left")
#: clip 标称时长 60 秒，事件放在第 30 秒——前 15 后 5 的窗口完整落在 clip 内
EVENT_OFFSET_SEC = 30.0


def _marks(data_id: str = DATA_ID) -> S.ClipComplianceMarks:
    return S.ClipComplianceMarks(
        data_id=data_id, vehicle_side_desensitized=True, compliance_cloud_declassified=True
    )


def _clip(**overrides) -> S.ClipInput:
    payload = {
        "data_id": DATA_ID,
        "clip_start": CLIP_START,
        "duration_sec": float(K.CLIP_NOMINAL_DURATION_SECONDS),
        "camera_ids": CAMERAS,
        "project_code": "BP",
        "vehicle_code": "BP",
        "compliance": _marks(),
        "vlm_scope": True,
    }
    payload.update(overrides)
    return S.ClipInput(**payload)


def _marker(
    offset_sec: float = EVENT_OFFSET_SEC,
    trigger: S.EventTriggerType = S.EventTriggerType.ACTIVE_SAFETY,
) -> S.EventMarker:
    return S.EventMarker(
        data_id=DATA_ID,
        trigger_type=trigger,
        event_time=CLIP_START + timedelta(seconds=offset_sec),
        detected_at=CLIP_START + timedelta(seconds=offset_sec + 300),
    )


# =========================================================================== A
# 参数逐字：原文每一个数字都要在常量里一字不差
# ===========================================================================


def test_native_fps_is_30_because_the_source_says_10_seconds_is_300_frames():
    """引言："一路摄像头 10 秒 30 帧就是 300 张图"——三个数必须同时对上。"""
    assert K.NATIVE_VIDEO_FPS == 30
    assert K.NATIVE_EXAMPLE_WINDOW_SECONDS == 10
    assert K.NATIVE_EXAMPLE_WINDOW_FRAMES == 300
    assert S.full_frame_count(duration_seconds=10, camera_count=1) == 300


def test_routine_gate_is_one_frame_every_two_seconds():
    """闸门表第 1 行："全量 clip"｜"默认 2 秒 1 帧"。"""
    assert K.ROUTINE_INTERVAL_SECONDS == 2
    assert K.ROUTINE_FRAMES_PER_INTERVAL == 1
    assert Fraction(1, 2) == K.ROUTINE_FPS
    assert S.SamplingTier.ROUTINE.trigger_condition == "全量 clip"
    assert S.SamplingTier.ROUTINE.frequency == "默认 2 秒 1 帧"


def test_event_window_is_15_before_and_5_after_totalling_20_seconds():
    """闸门表第 2 行 + 三章："事件前 15 秒 + 后 5 秒共 20 秒窗口，1 秒 1 帧（约 20 帧）"。"""
    assert K.EVENT_PRE_SECONDS == 15
    assert K.EVENT_POST_SECONDS == 5
    assert K.EVENT_WINDOW_SECONDS == 20
    assert K.EVENT_PRE_SECONDS + K.EVENT_POST_SECONDS == K.EVENT_WINDOW_SECONDS
    assert K.EVENT_INTERVAL_SECONDS == 1
    assert K.EVENT_EXPECTED_FRAMES == 20


def test_twenty_seconds_of_full_sampling_would_be_600_frames():
    """三章："1 秒 1 帧的密度足够还原因果链，又不至于把 20 秒变成 600 张全量帧"。"""
    assert K.EVENT_WINDOW_FULL_FRAMES == 600
    assert K.EVENT_WINDOW_SECONDS * K.NATIVE_VIDEO_FPS == 600


def test_inference_gate_picks_between_1_and_5_keyframes():
    """闸门表第 3 行："进入 VLM 推理范围的 clip"｜"每 clip 打分选 1~5 关键帧"。"""
    assert K.INFERENCE_MIN_KEYFRAMES == 1
    assert K.INFERENCE_MAX_KEYFRAMES == 5
    assert K.INFERENCE_SCORE_DIMENSION_COUNT == 3
    assert S.SamplingTier.INFERENCE.trigger_condition == "进入 VLM 推理范围的 clip"
    assert S.SamplingTier.INFERENCE.frequency == "每 clip 打分选 1~5 关键帧"


def test_there_are_exactly_three_gates_and_four_trigger_types():
    """二章："我们把闸门分成三道"；三章："触发条件有四类"。"""
    assert K.GATE_COUNT == 3
    assert len(S.SamplingTier) == 3
    assert K.EVENT_TRIGGER_TYPE_COUNT == 4
    assert len(S.EventTriggerType) == 4
    assert {t.name_cn for t in S.EventTriggerType} == {
        "命中挖掘规则",
        "主动安全触发（AEB 等）",
        "驾驶员接管",
        "模型低置信度",
    }


def test_the_three_gates_carry_the_sources_own_metaphors():
    """三章："常规抽帧是「普查」、事件抽帧是「现场勘查」、推理抽帧是「重点取证」"。"""
    assert S.SamplingTier.ROUTINE.role == "普查"
    assert S.SamplingTier.EVENT.role == "现场勘查"
    assert S.SamplingTier.INFERENCE.role == "重点取证"


def test_keep_ratios_are_exact_fractions_not_rounded_decimals():
    """两个比例都由原文数字精确相除得到：(1/2)/30 = 1/60；20/600 = 1/30。"""
    assert Fraction(1, 60) == K.ROUTINE_KEEP_RATIO
    assert Fraction(1, 30) == K.EVENT_KEEP_RATIO
    assert S.gate_keep_ratio(S.SamplingTier.ROUTINE) == Fraction(1, 60)
    assert S.gate_keep_ratio(S.SamplingTier.EVENT) == Fraction(1, 30)
    # 闸门三给的是区间，因为原文给的是 "1~5" 而不是定值
    lo, hi = S.gate_keep_ratio(S.SamplingTier.INFERENCE)
    assert (lo, hi) == (Fraction(1, 30), Fraction(1, 6))


def test_the_only_new_table_is_the_frame_detail_table():
    """一章："抽帧产物只新增一张表——dwd_mining_image_frame_detail"。"""
    assert K.FRAME_TABLE_NAME == "dwd_mining_image_frame_detail"
    assert K.UPSTREAM_CLIP_TABLE == "dwd_collect_clip_detail"
    assert K.UPSTREAM_FILE_META_TABLE == "ods_data_file_meta"
    assert S.FRAME_TABLE_SPEC.name == "dwd_mining_image_frame_detail"
    # 上游两张表必须真的在 registry 里——"只消费采集域既有表" 是有前提的
    assert S.UPSTREAM_CLIP_SPEC.name == "dwd_collect_clip_detail"
    assert S.UPSTREAM_FILE_META_SPEC.name == "ods_data_file_meta"


# =========================================================================== B
# 闸门一 · 常规抽帧：2 秒 1 帧，保留 1/60
# ===========================================================================


def test_routine_gate_samples_exactly_every_two_seconds_across_a_60s_clip():
    offsets = S.RoutineGate().offsets(_clip())
    assert offsets == [float(i * 2) for i in range(30)], "60 秒 ÷ 2 秒 1 帧 = 30 个采样时刻"
    assert offsets[0] == 0.0 and offsets[-1] == 58.0


def test_routine_gate_does_not_drift_on_a_long_clip():
    """浮点累加会漂移；漂到 1/30 秒就会把帧号 round 到隔壁，去重随之失效。"""
    long_clip = _clip(duration_sec=3600.0)
    offsets = S.RoutineGate().offsets(long_clip)
    assert len(offsets) == 1800
    assert offsets[-1] == 3598.0
    assert S.offset_to_frame_index(offsets[-1]) == 3598 * K.NATIVE_VIDEO_FPS


def test_routine_gate_keeps_exactly_one_sixtieth_of_the_full_frames():
    """闸门一的成本账：2 秒 1 帧 ÷ 30 fps = 1/60，过滤掉 59/60。"""
    result = S.SamplingPipeline().run(_clip(), [], now=NOW)
    gate1 = next(g for g in result.cost.gates if g.tier is S.SamplingTier.ROUTINE)
    assert gate1.input_frames == 30 * 60 * len(CAMERAS) == 3600
    assert gate1.output_frames == 30 * len(CAMERAS) == 60
    assert gate1.keep_ratio == Fraction(1, 60)
    assert gate1.filtered_ratio == Fraction(59, 60)
    assert gate1.keep_ratio == gate1.declared_keep_ratio


# =========================================================================== B
# 闸门二 · 事件抽帧：前 15 后 5，1 秒 1 帧，约 20 帧，保留 1/30
# ===========================================================================


def test_event_window_spans_from_15s_before_to_5s_after_the_event():
    marker = _marker()
    start, end = marker.window()
    assert start == marker.event_time - timedelta(seconds=15)
    assert end == marker.event_time + timedelta(seconds=5)
    assert (end - start).total_seconds() == 20


def test_event_gate_yields_about_twenty_sampling_moments():
    """ "1 秒 1 帧（约 20 帧）"：窗口完整落在 clip 内时恰好 20 个采样点。"""
    offsets = S.EventGate().offsets(_clip(), _marker())
    assert len(offsets) == K.EVENT_EXPECTED_FRAMES == 20
    # 采样点逐秒推进，起点是事件前 15 秒
    assert offsets == [EVENT_OFFSET_SEC - 15 + i for i in range(20)]


def test_the_window_is_asymmetric_fifteen_before_five_after():
    """原文解释了为什么不对称：价值主要在「它是怎么发生的」。"""
    offsets = S.EventGate().offsets(_clip(), _marker())
    before = [o for o in offsets if o < EVENT_OFFSET_SEC]
    after = [o for o in offsets if o >= EVENT_OFFSET_SEC]
    assert len(before) == 15, "事件之前 15 个采样点"
    assert len(after) == 5, "事件之后 5 个采样点"


def test_event_gate_keeps_twenty_of_six_hundred_frames():
    """闸门二的成本账：约 20 帧 ÷ 600 张全量帧 = 1/30，过滤掉 29/30。"""
    result = S.SamplingPipeline().run(_clip(), [_marker()], now=NOW)
    gate2 = next(g for g in result.cost.gates if g.tier is S.SamplingTier.EVENT)
    assert gate2.input_frames == 600 * len(CAMERAS) == 1200
    assert gate2.output_frames == 20 * len(CAMERAS) == 40
    assert gate2.keep_ratio == Fraction(1, 30)
    assert gate2.filtered_ratio == Fraction(29, 30)


def test_event_frames_carry_the_trigger_type_and_the_window_bounds():
    marker = _marker(trigger=S.EventTriggerType.DRIVER_TAKEOVER)
    result = S.SamplingPipeline().run(_clip(), [marker], now=NOW)
    tagged = [f for f in result.frames if f.event_trigger_type]
    assert len(tagged) == 20 * len(CAMERAS), "窗口覆盖的 20 个时刻 × 2 路都要带事件上下文"
    for frame in tagged:
        assert frame.event_trigger_type == "driver_takeover"
        assert frame.event_time == marker.event_time
        assert frame.event_window_start == marker.event_time - timedelta(seconds=15)
        assert frame.event_window_end == marker.event_time + timedelta(seconds=5)


def test_a_window_clipped_by_the_clip_boundary_yields_fewer_than_twenty():
    """原文说的是「约 20 帧」而不是「恰好」——事件贴着 clip 头部时窗口被截断。"""
    marker = _marker(offset_sec=3.0)  # 前 15 秒大半落在 clip 起点之前
    offsets = S.EventGate().offsets(_clip(), marker)
    assert 0 < len(offsets) < 20
    assert min(offsets) >= 0.0


def test_an_event_outside_the_clip_produces_nothing_but_does_not_explode():
    """规则引擎可能把事件对到别的 clip 上；这时补抽零产出，而不是抛异常。"""
    far_marker = _marker(offset_sec=9999.0)
    gate = S.EventGate()
    assert gate.covers(_clip(), far_marker) is False
    created, enriched = gate.plan(_clip(), [far_marker])
    assert created == [] and enriched == []


def test_event_markers_from_another_clip_are_rejected():
    other = S.EventMarker(
        data_id="COLLECT_BP_20260301123045_ffff",
        trigger_type=S.EventTriggerType.RULE_HIT,
        event_time=CLIP_START,
    )
    with pytest.raises(ValueError, match="不符"):
        S.EventGate().plan(_clip(), [other])


# =========================================================================== B
# 闸门三 · 推理抽帧：打分选 1~5 关键帧
# ===========================================================================


def test_keyframe_count_stays_within_one_to_five_per_camera():
    """ "每 clip 打分选 1~5 关键帧"——多路时按 camera 维度分别应用该区间。"""
    result = S.SamplingPipeline().run(_clip(), [_marker()], now=NOW)
    assert result.keyframes
    for camera_id in CAMERAS:
        per_camera = [f for f in result.keyframes if f.camera_id == camera_id]
        assert 1 <= len(per_camera) <= 5, f"{camera_id} 选出了 {len(per_camera)} 张，越界"
    assert all(f.is_keyframe for f in result.keyframes)


def test_a_clip_outside_the_vlm_scope_gets_no_keyframes():
    """闸门三的触发条件是 "进入 VLM 推理范围的 clip"——不在范围内就是零。"""
    result = S.SamplingPipeline().run(_clip(vlm_scope=False), [_marker()], now=NOW)
    assert result.keyframes == []
    assert result.keyframe_candidates == []
    gate3 = next(g for g in result.cost.gates if g.tier is S.SamplingTier.INFERENCE)
    assert gate3.input_frames == 0 and gate3.output_frames == 0
    assert result.cost.vlm_frames == 0


def test_all_three_score_dimensions_land_in_the_lake_not_just_the_total():
    """四章："frame_quality_score 作为帧表字段落湖……可以重算分数重新圈选"。"""
    result = S.SamplingPipeline().run(_clip(), [_marker()], now=NOW)
    for frame in result.frames:
        assert frame.frame_quality_score is not None, "清晰度分"
        assert frame.object_richness_score is not None, "目标丰富度分"
        assert frame.temporal_position_score is not None, "时间位置分"
        assert frame.keyframe_score is not None, "三维加权综合分"
    row = result.frames[0].to_row()
    for col in (
        "frame_quality_score",
        "object_richness_score",
        "temporal_position_score",
        "keyframe_score",
    ):
        assert col in row


def test_every_frame_is_scored_even_the_ones_outside_the_candidate_pool():
    """打分覆盖全量帧，收窄只发生在候选池这一步——否则「重新圈选」没有底数。"""
    result = S.SamplingPipeline().run(_clip(), [_marker()], now=NOW)
    assert len(result.keyframe_candidates) < len(result.frames), "候选池必须比全量帧窄"
    assert all(f.keyframe_score is not None for f in result.frames)


def test_priority_frames_always_enter_the_candidate_pool_ordinary_ones_are_sampled():
    """四章末尾："规则命中、事件抽帧产出的帧优先进 VLM，普通帧抽样"。"""
    assert K.ORDINARY_FRAME_VLM_SAMPLE_RATIO == 0.1
    result = S.SamplingPipeline().run(_clip(), [_marker()], now=NOW)
    candidates = {id(f) for f in result.keyframe_candidates}

    priority = [f for f in result.frames if S.vlm_priority(f)]
    assert priority, "本用例必须真的产出优先帧，否则断言空转"
    assert all(id(f) in candidates for f in priority), "优先帧必须全部进候选池"

    ordinary = [f for f in result.frames if not S.vlm_priority(f)]
    sampled = [f for f in ordinary if id(f) in candidates]
    assert 0 < len(sampled) < len(ordinary), "普通帧是抽样进池，不是全进也不是全不进"


def test_event_frames_outrank_ordinary_frames_for_the_vlm():
    marker = _marker(trigger=S.EventTriggerType.RULE_HIT)
    result = S.SamplingPipeline().run(_clip(), [marker], now=NOW)
    event_frame = next(f for f in result.frames if f.tier is S.SamplingTier.EVENT)
    plain = next(
        f for f in result.frames if f.tier is S.SamplingTier.ROUTINE and not f.event_trigger_type
    )
    assert S.vlm_priority(event_frame) is True
    assert S.vlm_priority(plain) is False


def test_scoring_dimensions_behave_as_the_source_describes():
    """四章的三个维度各自的方向性：糊/过曝/遮挡降权，目标越多越高，离事件越近越高。"""
    crisp = S.compute_clarity_score(S.FrameSignals(blur=0.0, overexposure=0.0, occlusion=0.0))
    blurry = S.compute_clarity_score(S.FrameSignals(blur=0.9))
    assert crisp == 1.0 and blurry < crisp
    # "模糊、过曝、遮挡的帧直接降权"——任一劣化拉满即归零
    assert S.compute_clarity_score(S.FrameSignals(occlusion=1.0)) == 0.0

    busy = S.compute_object_richness_score(
        S.FrameSignals(vehicle_count=5, pedestrian_count=3, traffic_facility_count=2)
    )
    empty = S.compute_object_richness_score(S.FrameSignals())
    assert busy > empty == 0.0

    at_event = S.compute_temporal_position_score(S.FrameSignals(seconds_from_event=0.0))
    far = S.compute_temporal_position_score(S.FrameSignals(seconds_from_event=15.0))
    assert at_event == 1.0 and far < at_event
    # "场景切换时刻的帧优先"
    assert S.compute_temporal_position_score(S.FrameSignals(is_scene_change=True)) == 1.0


# =========================================================================== B
# 五章实现要点一 · 多路摄像头 camera_id 同步分组
# ===========================================================================


def test_frames_at_the_same_moment_from_all_cameras_form_one_group():
    """五章："同一时刻的多路图片作为一组样本，逐图保留 camera_id"。"""
    result = S.SamplingPipeline().run(_clip(), [_marker()], now=NOW)
    assert result.groups
    for group in result.groups:
        assert group.camera_ids == tuple(sorted(CAMERAS)), "每一组都要集齐全部视角"
        assert group.camera_count == len(CAMERAS)
        assert group.is_complete(CAMERAS)
        assert len({f.camera_id for f in group.frames}) == len(group.frames), "组内一路一图"
    assert result.incomplete_groups() == []


def test_every_frame_carries_its_own_camera_id_and_group_id():
    result = S.SamplingPipeline().run(_clip(), [_marker()], now=NOW)
    for frame in result.frames:
        assert frame.camera_id in CAMERAS
        assert frame.frame_group_id, "多路同步组 ID 不能为空"
        assert S.parse_image_id(frame.image_id).camera_id == frame.camera_id


def test_group_ids_are_shared_across_cameras_and_distinct_across_moments():
    result = S.SamplingPipeline().run(_clip(), [], now=NOW)
    by_offset: dict[int, set[str]] = {}
    for frame in result.frames:
        by_offset.setdefault(frame.clip_offset_ms, set()).add(frame.frame_group_id)
    for offset, group_ids in by_offset.items():
        assert len(group_ids) == 1, f"偏移 {offset}ms 的多路帧必须共用一个组 ID"
    assert len({next(iter(v)) for v in by_offset.values()}) == len(by_offset)


def test_camera_sync_tolerance_matches_the_production_vehicle_soft_sync_budget():
    """±50ms 与 quality 门禁的量产车软同步预算同口径，避免两套标准。"""
    assert K.CAMERA_GROUP_SYNC_TOLERANCE_MS == 50
    base = _routine_frame(offset_ms=0, camera_id="cam-front")
    inside = _routine_frame(offset_ms=50, camera_id="side-left")
    outside = _routine_frame(offset_ms=51, camera_id="side-right")
    groups = S.group_by_capture_moment([base, inside, outside])
    assert len(groups) == 2
    assert set(groups[0].camera_ids) == {"cam-front", "side-left"}
    assert groups[1].camera_ids == ("side-right",)


def test_a_dropped_camera_shows_up_as_an_incomplete_group():
    """某一路掉线时，那一时刻不能再当一组多视角样本用。"""
    frames = [
        _routine_frame(offset_ms=0, camera_id="cam-front"),
        _routine_frame(offset_ms=0, camera_id="side-left"),
        _routine_frame(offset_ms=2000, camera_id="cam-front"),  # 左视这一刻丢了
    ]
    groups = S.group_by_capture_moment(frames)
    assert [g.is_complete(CAMERAS) for g in groups] == [True, False]


def test_grouping_refuses_to_mix_two_clips():
    a = _routine_frame(offset_ms=0, camera_id="cam-front")
    b = _routine_frame(offset_ms=0, camera_id="cam-front", data_id="COLLECT_BP_20260301123045_ffff")
    with pytest.raises(ValueError, match="data_id"):
        S.group_by_capture_moment([a, b])


def test_camera_position_is_derived_from_the_camera_id():
    """五章："同一时刻有前视、侧视、后视等多路摄像头"；取值域来自 catalog 的列注释。"""
    assert K.CAMERA_POSITIONS == ("front", "left", "right", "rear")
    assert S.derive_camera_position("cam-front") == "front"
    assert S.derive_camera_position("side-left") == "left"
    assert S.derive_camera_position("rear-cam") == "rear"
    assert S.derive_camera_position("cam-right") == "right"
    assert S.derive_camera_position("lidar-top") == "", "推不出来就留空，不瞎猜视角"


def test_an_explicit_camera_position_map_overrides_the_guess():
    clip = _clip(camera_ids=("cam-a", "cam-b"), camera_positions={"cam-a": "rear", "cam-b": "left"})
    result = S.SamplingPipeline().run(clip, [], now=NOW)
    positions = {f.camera_id: f.camera_position for f in result.frames}
    assert positions == {"cam-a": "rear", "cam-b": "left"}


def test_duplicate_camera_ids_are_rejected():
    with pytest.raises(ValueError, match="重复"):
        _clip(camera_ids=("cam-front", "cam-front"))


# =========================================================================== B
# 三章 · 二次抽帧与异步补抽
# ===========================================================================


def test_the_main_lane_runs_without_any_event_markers():
    """三章："常规抽帧先行，规则引擎事后识别出事件，再回头对对应窗口补抽"。"""
    result = S.SamplingPipeline().run(_clip(), [], now=NOW)
    assert result.routine_frames and result.event_frames == []
    assert result.backfill_tasks == []
    gate2 = next(g for g in result.cost.gates if g.tier is S.SamplingTier.EVENT)
    assert gate2.input_frames == 0 and gate2.output_frames == 0


def test_backfill_only_writes_the_frames_the_routine_pass_missed():
    """二次抽帧不能把已有帧再产一遍——重合时刻共享同一个 frame_index / image_id。"""
    pipeline = S.SamplingPipeline()
    main = pipeline.run(_clip(), [], now=NOW)
    created = pipeline.backfill(_clip(), [_marker()], main.frames)

    # 20 个窗口时刻里，偶数秒与常规抽帧（2 秒 1 帧）重合，只剩 10 个是新增的
    assert len(created) == 10 * len(CAMERAS) == 20
    existing_ids = {f.image_id for f in main.frames}
    assert not (existing_ids & {f.image_id for f in created}), "补抽不得重复产出已有 image_id"
    assert all(f.tier is S.SamplingTier.EVENT for f in created)


def test_backfilled_frames_are_grouped_with_the_frames_already_in_the_lake():
    """补抽帧也要有多路同步组 ID，否则事件那 20 秒反而成不了组样本。"""
    pipeline = S.SamplingPipeline()
    main = pipeline.run(_clip(), [], now=NOW)
    created = pipeline.backfill(_clip(), [_marker()], main.frames)
    assert created
    assert all(f.frame_group_id for f in created), "补抽帧的 frame_group_id 不能为空"
    by_group: dict[str, set[str]] = {}
    for frame in list(main.frames) + created:
        by_group.setdefault(frame.frame_group_id, set()).add(frame.camera_id)
    assert all(cams == set(CAMERAS) for cams in by_group.values())


def test_a_backfill_task_carries_the_detection_latency_of_the_rule_engine():
    """三章："调度上把事件抽帧任务挂在规则结果之后……谁也不阻塞谁"。"""
    result = S.SamplingPipeline().run(_clip(), [_marker()], now=NOW)
    assert len(result.backfill_tasks) == 1
    task = result.backfill_tasks[0]
    assert task.enqueued_at == NOW
    assert task.backfill_latency_seconds() is not None


def test_backfill_ttl_and_retry_budget_come_from_the_constants():
    assert K.EVENT_BACKFILL_TTL_SECONDS == 24 * 60 * 60
    assert K.EVENT_BACKFILL_MAX_RETRIES == 3
    task = S.EventBackfillTask(_marker(), enqueued_at=NOW)
    assert task.ttl_seconds == 86400 and task.max_retries == 3
    assert task.is_expired(NOW + timedelta(hours=23)) is False
    assert task.is_expired(NOW + timedelta(hours=25)) is True


def test_an_expired_backfill_task_is_failed_not_retried_forever():
    pipeline = S.SamplingPipeline()
    main = pipeline.run(_clip(), [], now=NOW)
    task = S.EventBackfillTask(_marker(), enqueued_at=NOW)
    outcome = pipeline.run_backfill_task(_clip(), task, main.frames, now=NOW + timedelta(hours=25))
    assert outcome.succeeded is False
    assert "超时" in outcome.reason
    assert outcome.created == []
    assert task.attempts == 0, "超时任务不该再消耗一次重试预算"


def test_a_backfill_task_out_of_retries_is_failed():
    pipeline = S.SamplingPipeline()
    main = pipeline.run(_clip(), [], now=NOW)
    task = S.EventBackfillTask(_marker(), enqueued_at=NOW, attempts=3)
    outcome = pipeline.run_backfill_task(_clip(), task, main.frames, now=NOW)
    assert outcome.succeeded is False and "重试" in outcome.reason


def test_a_healthy_backfill_task_runs_and_counts_one_attempt():
    pipeline = S.SamplingPipeline()
    main = pipeline.run(_clip(), [], now=NOW)
    task = S.EventBackfillTask(_marker(), enqueued_at=NOW)
    outcome = pipeline.run_backfill_task(_clip(), task, main.frames, now=NOW)
    assert outcome.succeeded is True
    assert task.attempts == 1
    assert len(outcome.created) == 20


def test_two_overlapping_backfill_tasks_do_not_produce_the_same_frame_twice():
    """后一个任务必须看得见前一个刚补出来的帧，否则同一时刻会被补两次。"""
    pipeline = S.SamplingPipeline()
    main = pipeline.run(_clip(), [], now=NOW)
    tasks = [
        S.EventBackfillTask(_marker(offset_sec=30.0), enqueued_at=NOW),
        S.EventBackfillTask(_marker(offset_sec=31.0), enqueued_at=NOW),
    ]
    outcomes = pipeline.process_backfill_queue(_clip(), tasks, main.frames, now=NOW)
    assert all(o.succeeded for o in outcomes)
    produced = [f.image_id for o in outcomes for f in o.created]
    assert len(produced) == len(set(produced)), "两个重叠窗口不得产出重复 image_id"


def test_backfill_still_refuses_undesensitized_data():
    """补抽是独立调度的任务，但合规红线不因为解耦而松动。"""
    pipeline = S.SamplingPipeline()
    task = S.EventBackfillTask(_marker(), enqueued_at=NOW)
    with pytest.raises(S.ComplianceRejectedError):
        pipeline.run_backfill_task(_clip(compliance=None), task, [], now=NOW)


# =========================================================================== B
# 三级之间的衔接：重合帧只留一份，层级不被冲掉
# ===========================================================================


def test_a_moment_covered_by_both_gates_yields_one_frame_not_two():
    result = S.SamplingPipeline().run(_clip(), [_marker()], now=NOW)
    image_ids = [f.image_id for f in result.frames]
    assert len(image_ids) == len(set(image_ids)), "同一时刻同一路不得产出两条帧"
    # 60 秒 ÷ 2 秒 = 30 常规 + 事件窗口新增 10（奇数秒）= 40，每路
    assert len(result.frames) == 40 * len(CAMERAS) == 80


def test_an_overlapping_frame_keeps_its_routine_tier_but_gains_event_context():
    """本项目口径：谁先产出算谁的层级，事件覆盖情况靠 event_trigger_type 查。"""
    result = S.SamplingPipeline().run(_clip(), [_marker()], now=NOW)
    overlapped = [
        f for f in result.frames if f.tier is S.SamplingTier.ROUTINE and f.event_trigger_type
    ]
    assert overlapped, "偶数秒的窗口时刻应当是「常规层级 + 事件上下文」"
    for frame in overlapped:
        assert frame.sampling_interval_sec == 2.0, "层级没被冲掉，间隔仍是常规的 2 秒"


def test_inference_does_not_create_new_images_it_only_marks_existing_ones():
    """四章说的是「选帧」——闸门三削的是 GPU 成本，不是存储成本。"""
    result = S.SamplingPipeline().run(_clip(), [_marker()], now=NOW)
    frame_ids = {f.image_id for f in result.frames}
    assert {f.image_id for f in result.keyframes} <= frame_ids
    assert result.cost.lake_frames == len(result.frames)
    assert all(f.tier is not S.SamplingTier.INFERENCE for f in result.frames)


def test_the_three_gates_narrow_the_funnel_monotonically():
    result = S.SamplingPipeline().run(_clip(), [_marker()], now=NOW)
    cost = result.cost
    assert cost.full_frames == 3600
    assert cost.lake_frames == 80
    assert cost.vlm_frames == len(result.keyframes)
    assert cost.full_frames > cost.lake_frames > cost.vlm_frames > 0
    assert cost.overall_keep_ratio == Fraction(80, 3600) == Fraction(1, 45)


# =========================================================================== B
# 五章实现要点三 · 产物统一落帧表，字段齐
# ===========================================================================


def test_the_row_carries_every_field_the_source_names():
    """五章："字段含 image_id、clip 归属、camera_id、帧序号、时间戳、GPS、
    文件路径与 frame_quality_score"。"""
    clip = _clip(gps_lat=31.23, gps_lon=121.47, camera_resolutions={"cam-front": (1920, 1080)})
    result = S.SamplingPipeline().run(clip, [_marker()], now=NOW)
    row = next(r for r in result.rows() if r["camera_id"] == "cam-front")

    assert row["image_id"].startswith(DATA_ID)
    assert row["data_id"] == DATA_ID  # clip 归属
    assert row["camera_id"] == "cam-front"
    assert row["camera_position"] == "front"
    assert isinstance(row["frame_index"], int)
    assert isinstance(row["frame_timestamp"], datetime)
    assert row["gps_lat"] == 31.23 and row["gps_lon"] == 121.47
    assert row["image_object_key"].endswith(".jpg")  # 文件路径
    assert row["frame_quality_score"] is not None
    assert (row["image_width"], row["image_height"]) == (1920, 1080)
    # 列名一律是 registry 的，不是 sampling 内存侧的
    assert set(row) <= set(S.frame_column_names())
    assert "clip_offset_ms" not in row and "frame_offset_sec" in row


def test_millisecond_offsets_are_converted_to_seconds_on_the_way_into_the_lake():
    result = S.SamplingPipeline().run(_clip(), [], now=NOW)
    frame = next(f for f in result.frames if f.clip_offset_ms > 0)
    assert frame.to_row()["frame_offset_sec"] == pytest.approx(frame.clip_offset_ms / 1000.0)


def test_image_id_embeds_the_data_id_so_no_lookup_is_needed():
    """一章："image_id 内嵌 data_id，免查表即可回溯到采集单元"。"""
    result = S.SamplingPipeline().run(_clip(), [], now=NOW)
    for frame in result.frames[:10]:
        parsed = S.parse_image_id(frame.image_id)
        assert parsed.data_id == DATA_ID
        assert parsed.camera_id == frame.camera_id
        assert parsed.frame_index == frame.frame_index


def test_frame_index_is_the_native_30fps_frame_number():
    """帧号按原生 30fps 定，两道闸门在重叠时刻才会自然撞上同一个 image_id。"""
    routine_idx = S.offset_to_frame_index(16.0)
    event_idx = S.offset_to_frame_index(16.0)
    assert routine_idx == event_idx == 16 * 30 == 480
    assert S.frame_index_to_offset_ms(480) == 16000


def test_rerunning_with_a_new_algo_version_yields_a_new_artifact_id():
    """算法一变即产出新 artifact_id，旧帧由调用方标 superseded（ids 规则三）。"""
    result = S.SamplingPipeline().run(_clip(), [], now=NOW)
    frame = result.frames[0]
    first = frame.artifact_id
    assert first
    assert frame.assign_artifact_id() == first, "同样输入同样版本 → 同一个 ID，重试幂等"
    assert frame.assign_artifact_id("v2") != first
    frame.mark_superseded()
    assert frame.artifact_status == "superseded"


# =========================================================================== B
# 五章实现要点四 · 抽帧前置脱敏校验
# ===========================================================================


def test_undesensitized_data_is_refused_outright():
    """五章："未脱敏数据一律拒绝抽帧——合规红线在挖掘侧再设一道闸"。"""
    with pytest.raises(S.ComplianceRejectedError, match="一律拒绝抽帧"):
        S.SamplingPipeline().run(_clip(compliance=None), [], now=NOW)


@pytest.mark.parametrize(
    ("vehicle_side", "cloud"),
    [(True, False), (False, True), (False, False)],
)
def test_double_desensitization_means_both_stages_not_either(vehicle_side, cloud):
    """一章："车端脱敏 → 合规云脱密 → 智驾云入湖"，两道缺一不可。"""
    marks = S.ClipComplianceMarks(DATA_ID, vehicle_side, cloud)
    with pytest.raises(S.ComplianceRejectedError):
        S.SamplingPipeline().run(_clip(compliance=marks), [], now=NOW)


def test_a_missing_mark_snapshot_is_told_apart_from_an_incomplete_one():
    """「没查到标记」与「没脱完」都拒绝，但要能分得出该去修谁。"""
    missing = S.check_desensitization(None, DATA_ID)
    assert missing.allowed is False
    assert missing.status is S.DesensitizationStatus.UNKNOWN

    half = S.check_desensitization(S.ClipComplianceMarks(DATA_ID, True, False))
    assert half.allowed is False
    assert half.status is S.DesensitizationStatus.REJECTED
    assert half.missing == (S.DesensitizationStage.COMPLIANCE_CLOUD,)

    ok = S.check_desensitization(_marks())
    assert ok.allowed is True and ok.status is S.DesensitizationStatus.PASSED


def test_passing_frames_record_the_desensitization_verdict():
    result = S.SamplingPipeline().run(_clip(), [], now=NOW)
    assert all(f.desensitization_status == "double_desensitized" for f in result.frames)


def test_missing_marks_land_as_unknown_in_non_strict_replay_mode():
    lax = S.SamplingPipeline(strict_compliance=False)
    result = lax.run(_clip(compliance=None), [], now=NOW)
    assert all(f.desensitization_status == "unknown" for f in result.frames)

    result2 = lax.run(_clip(compliance=S.ClipComplianceMarks(DATA_ID, True, False)), [], now=NOW)
    assert all(f.desensitization_status == "rejected_not_desensitized" for f in result2.frames)


# =========================================================================== B
# 五章实现要点二 · 批流双模式，共用同一套抽帧逻辑
# ===========================================================================


def test_batch_and_stream_map_to_spark_and_flink_with_the_sources_use_cases():
    assert S.SamplingMode.BATCH.runtime == "Spark"
    assert S.SamplingMode.BATCH.use_case == "存量历史数据回刷"
    assert S.SamplingMode.STREAM.runtime == "Flink"
    assert S.SamplingMode.STREAM.use_case == "新入湖数据实时抽帧"


def test_both_modes_write_byte_identical_rows():
    """五章："共用同一套抽帧逻辑保证结果一致"——比的是真正落表的行。"""
    assert S.verify_mode_consistency(_clip(), [_marker()]) is True


def test_the_engine_writes_every_frame_to_the_single_frame_table():
    writer = S.InMemoryFrameWriter()
    engine = S.SamplingEngine(S.SamplingMode.BATCH, writer=writer)
    result = engine.run_clip(_clip(), [_marker()])
    assert len(writer.rows) == len(result.frames) == 80
    assert all(set(row) <= set(S.frame_column_names()) for row in writer.rows)


def test_a_rejected_clip_is_skipped_without_killing_the_batch():
    writer = S.InMemoryFrameWriter()
    engine = S.SamplingEngine(S.SamplingMode.BATCH, writer=writer)
    good = _clip()
    bad = _clip(data_id="COLLECT_BP_20260301123045_ffff", compliance=None)
    results = engine.run_many([bad, good], skip_rejected=True)
    assert len(results) == 1 and results[0].clip.data_id == DATA_ID
    with pytest.raises(S.ComplianceRejectedError):
        engine.run_many([bad], skip_rejected=False)


# =========================================================================== B
# 二章 · 成本闸门的账：预估与实跑必须对得上
# ===========================================================================


def test_the_estimate_matches_what_the_pipeline_actually_produces():
    """闸门的意义是花钱之前就知道要花多少——预估口径必须与实跑一致。"""
    estimate = S.estimate_pipeline_cost(
        duration_seconds=60.0, camera_count=len(CAMERAS), event_count=1
    )
    actual = S.SamplingPipeline().run(_clip(), [_marker()], now=NOW).cost

    assert estimate.full_frames == actual.full_frames == 3600
    assert estimate.lake_frames == actual.lake_frames == 80
    est_gate1 = estimate.gates[0]
    act_gate1 = next(g for g in actual.gates if g.tier is S.SamplingTier.ROUTINE)
    assert (est_gate1.input_frames, est_gate1.output_frames) == (
        act_gate1.input_frames,
        act_gate1.output_frames,
    )
    est_gate2, act_gate2 = (
        estimate.gates[1],
        next(g for g in actual.gates if g.tier is S.SamplingTier.EVENT),
    )
    assert est_gate2.keep_ratio == act_gate2.keep_ratio == Fraction(1, 30)


def test_the_estimate_reproduces_the_sources_own_300_frame_example():
    """ "一路摄像头 10 秒 30 帧就是 300 张图"，常规抽帧后只剩 10 ÷ 2 = 5 张。"""
    estimate = S.estimate_pipeline_cost(duration_seconds=10.0, camera_count=1, event_count=0)
    assert estimate.full_frames == 300
    assert estimate.gates[0].output_frames == 5
    assert estimate.gates[0].keep_ratio == Fraction(1, 60)


def test_the_estimate_refuses_a_keyframe_count_outside_one_to_five():
    for bad in (0, 6):
        with pytest.raises(ValueError, match=r"\[1, 5\]"):
            S.estimate_pipeline_cost(60.0, 1, 1, keyframes_per_camera=bad)


def test_a_gate_can_never_output_more_frames_than_it_took_in():
    """闸门只过滤不造帧——账上凭空多出来的帧一定是算错了。"""
    with pytest.raises(ValueError, match="只过滤不造帧"):
        S.build_gate_report(S.SamplingTier.ROUTINE, input_frames=10, output_frames=11)


def test_costs_scale_with_injected_unit_prices_since_the_source_gives_none():
    """原文只有定性成本论述，没给任何单价——默认 1.0 相对单位，真实单价由调用方注入。"""
    default = S.CostModel()
    assert (default.storage_per_frame, default.decode_per_frame, default.vlm_per_frame) == (
        1.0,
        1.0,
        1.0,
    )
    pricey = S.CostModel(storage_per_frame=0.01, decode_per_frame=0.002, vlm_per_frame=0.5)
    report = S.build_gate_report(S.SamplingTier.INFERENCE, 100, 5, pricey)
    assert report.storage_cost() == pytest.approx(0.05)
    assert report.decode_cost() == pytest.approx(0.2)
    assert report.vlm_cost() == pytest.approx(2.5)
    # 只有推理抽帧的产物真正进 VLM
    assert S.build_gate_report(S.SamplingTier.ROUTINE, 100, 5, pricey).vlm_cost() == 0.0


def test_gate_reports_quote_the_sources_trigger_frequency_and_purpose():
    report = S.build_gate_report(S.SamplingTier.EVENT, 600, 20)
    text = report.describe()
    assert "命中规则 / 主动安全触发（AEB 等）/ 驾驶员接管 / 模型低置信度" in text
    assert "事件前 15 秒 + 后 5 秒共 20 秒窗口，1 秒 1 帧（约 20 帧）" in text
    assert report.benefit == "事件上下文精细挖掘，可异步补抽"


# =========================================================================== B
# 与湖仓的两个接缝：读采集域标记、写帧表 DDL/INSERT
# ===========================================================================


def test_compliance_marks_read_back_from_a_lakehouse_row():
    """一章："clip 的元数据完全复用采集域既有表"——标记从行里读，缺字段按未脱敏处理。"""
    marks = S.ClipComplianceMarks.from_row(
        {
            "data_id": DATA_ID,
            "vehicle_side_desensitized": True,
            "compliance_cloud_declassified": True,
            "compliance_status": "double_desensitized",
        }
    )
    assert S.check_desensitization(marks).allowed is True

    # 缺字段 = 默认拒绝，不默认放行
    bare = S.ClipComplianceMarks.from_row({"data_id": DATA_ID})
    assert bare.missing_stages() == (
        S.DesensitizationStage.VEHICLE_SIDE,
        S.DesensitizationStage.COMPLIANCE_CLOUD,
    )
    with pytest.raises(S.ComplianceRejectedError):
        S.require_desensitized(bare)


def test_the_frame_table_ddl_comes_from_the_registry_not_a_local_copy():
    ddl = S.render_frame_table_ddl()
    assert "dwd_mining_image_frame_detail" in ddl
    for column in ("image_id", "camera_id", "frame_quality_score", "frame_offset_sec"):
        assert f"`{column}`" in ddl


def test_the_insert_statement_only_names_columns_the_registry_knows():
    result = S.SamplingPipeline().run(_clip(), [_marker()], now=NOW)
    sql = S.PaimonFrameWriter().build_insert(result.rows()[:3])
    assert sql.startswith("INSERT INTO")
    assert "dwd_mining_image_frame_detail" in sql
    named = {c.strip("`") for c in sql.split("(")[1].split(")")[0].split(", ")}
    assert named <= set(S.frame_column_names())


# --------------------------------------------------------------------------- 工具


def _routine_frame(*, offset_ms: int, camera_id: str, data_id: str = DATA_ID) -> S.FrameRecord:
    index = S.offset_to_frame_index(offset_ms / 1000.0)
    return S.FrameRecord(
        image_id=S.build_image_id(data_id, camera_id, index),
        data_id=data_id,
        camera_id=camera_id,
        frame_index=index,
        frame_timestamp=CLIP_START + timedelta(milliseconds=offset_ms),
        clip_offset_ms=offset_ms,
    )
