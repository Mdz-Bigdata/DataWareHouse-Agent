"""冒烟：分层抽帧的三道成本闸门。

主流程：一条 clip 进 → 常规闸（稀疏均匀）+ 事件闸（触发点前后加密）+ 推理闸（关键帧）
→ 帧记录出。全程 plan-only，不解码任何真实视频。

合规红线：未脱敏数据一律拒绝抽帧——这条在下面被直接打靶。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from adas_lakehouse import sampling as S

pytestmark = pytest.mark.smoke

DATA_ID = "COLLECT_BP_20260301123045_b7e2"
CLIP_START = datetime(2026, 3, 1, 12, 30, 45)
NOW = datetime(2026, 3, 1, 13, 0, 0)
CAMERAS = ("cam-front", "cam-left")


def _compliant_marks(data_id: str = DATA_ID) -> S.ClipComplianceMarks:
    return S.ClipComplianceMarks(
        data_id=data_id,
        vehicle_side_desensitized=True,
        compliance_cloud_declassified=True,
    )


def _clip(**overrides) -> S.ClipInput:
    payload = {
        "data_id": DATA_ID,
        "clip_start": CLIP_START,
        "duration_sec": 60.0,  # 原文：clip 约 1 分钟
        "camera_ids": CAMERAS,
        "project_code": "BP",
        "vehicle_code": "BP",
        "compliance": _compliant_marks(),
    }
    payload.update(overrides)
    return S.ClipInput(**payload)


def _marker(offset_sec: float = 15.0) -> S.EventMarker:
    return S.EventMarker(
        data_id=DATA_ID,
        trigger_type=list(S.EventTriggerType)[0],
        event_time=CLIP_START.replace(second=45) if offset_sec == 0 else CLIP_START,
    )


# --------------------------------------------------------------------------- 主流程


def test_pipeline_produces_frames_through_three_gates():
    result = S.SamplingPipeline().run(_clip(), [_marker()], now=NOW)

    assert result.frames
    assert result.routine_frames
    assert result.event_frames
    assert result.run_id.startswith("run_sampling_")

    # 三道闸门各自出报表
    tiers = {gate.tier for gate in result.cost.gates}
    assert tiers == {S.SamplingTier.ROUTINE, S.SamplingTier.EVENT, S.SamplingTier.INFERENCE}


def test_gates_cut_cost_monotonically():
    """成本闸门的意义就在于层层收窄：全量帧 >> 落盘帧。"""
    result = S.SamplingPipeline().run(_clip(), [_marker()], now=NOW)
    cost = result.cost

    assert cost.full_frames > cost.stored_frames > 0
    for gate in cost.gates:
        assert gate.output_frames <= gate.input_frames


def test_event_window_densifies_around_the_trigger():
    """事件闸在触发点前后加密——否则最该看清的那几秒反而最糊。"""
    with_event = S.SamplingPipeline().run(_clip(), [_marker()], now=NOW)
    without = S.SamplingPipeline().run(_clip(), [], now=NOW)
    assert len(with_event.frames) > len(without.frames)
    assert without.event_frames == () or len(without.event_frames) == 0


def test_frames_are_grouped_by_capture_moment():
    """同一时刻多相机的帧要成组，否则多模态对齐就无从谈起。"""
    result = S.SamplingPipeline().run(_clip(), [_marker()], now=NOW)
    assert result.groups
    for group in result.groups:
        assert len(group.frames) <= len(CAMERAS)


def test_every_frame_carries_a_parsable_image_id_rooted_at_the_clip():
    result = S.SamplingPipeline().run(_clip(), [_marker()], now=NOW)
    for frame in result.frames[:20]:
        parsed = S.parse_image_id(frame.image_id)
        assert parsed.data_id == DATA_ID
        assert parsed.camera_id in CAMERAS


def test_rows_render_for_the_frame_table():
    result = S.SamplingPipeline().run(_clip(), [_marker()], now=NOW)
    rows = list(result.rows())
    assert rows
    columns = set(S.frame_column_names())
    assert set(rows[0]) <= columns, "抽帧行里出现了帧表没有的字段"


# --------------------------------------------------------------------------- 合规红线


def test_undesensitized_clip_is_refused():
    """未脱敏一律拒绝抽帧。抽帧会把原始画面复制成成千上万张图，红线必须在最前面。"""
    with pytest.raises(S.ComplianceRejectedError) as excinfo:
        S.SamplingPipeline().run(_clip(compliance=None), [], now=NOW)
    assert "脱敏" in str(excinfo.value)


def test_half_desensitized_clip_is_also_refused():
    half = S.ClipComplianceMarks(
        data_id=DATA_ID, vehicle_side_desensitized=True, compliance_cloud_declassified=False
    )
    with pytest.raises(S.ComplianceRejectedError):
        S.SamplingPipeline().run(_clip(compliance=half), [], now=NOW)


def test_strict_compliance_can_be_switched_off_for_replay():
    """非严格模式留给历史数据回刷，但默认必须是严格的。"""
    assert S.SamplingPipeline().strict_compliance is True
    lax = S.SamplingPipeline(strict_compliance=False)
    result = lax.run(_clip(compliance=None), [], now=NOW)
    assert result.frames


# --------------------------------------------------------------------------- 帧 ID 与换算


def test_image_id_round_trip():
    image_id = S.build_image_id(DATA_ID, "cam-front", 42)
    parsed = S.parse_image_id(image_id)
    assert parsed.data_id == DATA_ID
    assert parsed.camera_id == "cam-front"
    assert parsed.frame_index == 42


@pytest.mark.parametrize("camera_id", ["cam_front", "cam front", "", "cam/front"])
def test_image_id_rejects_bad_camera_ids(camera_id):
    with pytest.raises((ValueError, S.InvalidImageIdError)):
        S.build_image_id(DATA_ID, camera_id, 0)


def test_camera_id_case_and_padding_are_normalised():
    """大小写与首尾空白统一归一，否则同一路相机会生成两套 image_id。"""
    assert S.build_image_id(DATA_ID, " CAM-FRONT ", 7) == S.build_image_id(DATA_ID, "cam-front", 7)


def test_negative_frame_index_is_rejected():
    with pytest.raises((ValueError, S.InvalidImageIdError)):
        S.build_image_id(DATA_ID, "cam-front", -1)


def test_image_id_rejects_bad_data_id():
    with pytest.raises((ValueError, S.InvalidImageIdError)):
        S.build_image_id("NOT_AN_ANCHOR", "cam-front", 0)


def test_offset_and_frame_index_are_inverse():
    """帧号 ↔ 时间偏移在 30fps 下互为反函数（一个收毫秒、一个给秒，注意单位）。"""
    for index in (0, 1, 42, 1799):
        offset_ms = S.frame_index_to_offset_ms(index)
        assert S.offset_to_frame_index(offset_ms / 1000) == index

    with pytest.raises(ValueError):
        S.offset_to_frame_index(-1.0)
    with pytest.raises(ValueError):
        S.frame_index_to_offset_ms(-1)


# --------------------------------------------------------------------------- 关键帧打分


def test_keyframe_scoring_is_deterministic_and_bounded():
    frame = S.SamplingPipeline().run(_clip(), [_marker()], now=NOW).frames[0]
    signals = S.FrameSignals(
        blur=0.1,
        overexposure=0.0,
        occlusion=0.0,
        vehicle_count=3,
        pedestrian_count=2,
        traffic_facility_count=1,
        seconds_from_event=0.5,
    )

    first = S.score_frame(frame, signals)
    again = S.score_frame(frame, signals)
    assert first.total == again.total  # 同样输入必须同样分数
    assert 0.0 <= first.total <= 1.0


def test_a_crisp_busy_frame_outscores_a_blurry_empty_one():
    frame = S.SamplingPipeline().run(_clip(), [_marker()], now=NOW).frames[0]
    good = S.score_frame(
        frame,
        S.FrameSignals(blur=0.0, vehicle_count=5, pedestrian_count=3, seconds_from_event=0.0),
    )
    bad = S.score_frame(
        frame,
        S.FrameSignals(blur=0.95, occlusion=0.9, vehicle_count=0, seconds_from_event=30.0),
    )
    assert good.total > bad.total


def test_score_weights_sum_to_one():
    import dataclasses

    weights = S.ScoreWeights()
    total = sum(getattr(weights, f.name) for f in dataclasses.fields(weights))
    assert total == pytest.approx(1.0, abs=1e-6)
