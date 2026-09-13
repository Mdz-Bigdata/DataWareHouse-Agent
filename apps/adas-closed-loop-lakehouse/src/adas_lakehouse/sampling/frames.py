"""帧域模型：三层抽帧的产物形态、image_id 规则、多路摄像头同步分组。

对应原文五章「四个实现要点」里的两点：
  · 多路摄像头同步——同一时刻的多路图片作为一组样本，逐图保留 camera_id
  · 产物统一落帧表——三层抽帧结果全部写 dwd_mining_image_frame_detail，
    字段含 image_id、clip 归属、camera_id、帧序号、时间戳、GPS、文件路径与
    frame_quality_score；image_id 内嵌 data_id，免查表即可回溯采集单元

三级 ID 体系复用共享契约 ``adas_lakehouse.ids``，本模块不自造 ID 规则：
  data_id      ← 采集端生成，抽帧不改变
  artifact_id  ← derive_artifact_id(data_id, stage="sampling", algo_version, payload)
  run_id       ← new_run_id("sampling")
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from ..ids import ArtifactStatus, derive_artifact_id, parse_data_id
from . import constants as K
from .table import frame_column_for, frame_column_names

__all__ = [
    "SamplingTier",
    "EventTriggerType",
    "ImageId",
    "build_image_id",
    "parse_image_id",
    "derive_camera_position",
    "FrameRecord",
    "FrameGroup",
    "group_by_capture_moment",
    "InvalidImageIdError",
]


class InvalidImageIdError(ValueError):
    """image_id 不符合本项目约定的格式。"""


class SamplingTier(str, Enum):
    """三道成本闸门 = 三个抽帧层级（原文二章闸门表）。

    原文对三层分工的比喻逐字保留在 ``role`` 里：
    "常规抽帧是「普查」、事件抽帧是「现场勘查」、推理抽帧是「重点取证」"。
    """

    ROUTINE = "routine"
    EVENT = "event"
    INFERENCE = "inference"

    @property
    def name_cn(self) -> str:
        return _TIER_META[self][0]

    @property
    def role(self) -> str:
        """原文的比喻：普查 / 现场勘查 / 重点取证。"""
        return _TIER_META[self][1]

    @property
    def trigger_condition(self) -> str:
        """原文闸门表「触发条件」列，逐字。"""
        return _TIER_META[self][2]

    @property
    def frequency(self) -> str:
        """原文闸门表「频率」列，逐字。"""
        return _TIER_META[self][3]

    @property
    def purpose(self) -> str:
        """原文闸门表「用途」列，逐字。"""
        return _TIER_META[self][4]


#: 原文二章闸门表的三行，逐字搬运（顺序：中文名、比喻、触发条件、频率、用途）
_TIER_META: dict[SamplingTier, tuple[str, str, str, str, str]] = {
    SamplingTier.ROUTINE: (
        "常规抽帧",
        "普查",
        "全量 clip",
        "默认 2 秒 1 帧",
        "基础场景覆盖，支撑标签统计与粗粒度检索",
    ),
    SamplingTier.EVENT: (
        "事件抽帧",
        "现场勘查",
        "命中规则 / 主动安全触发（AEB 等）/ 驾驶员接管 / 模型低置信度",
        "事件前 15 秒 + 后 5 秒共 20 秒窗口，1 秒 1 帧（约 20 帧）",
        "事件上下文精细挖掘，可异步补抽",
    ),
    SamplingTier.INFERENCE: (
        "推理抽帧",
        "重点取证",
        "进入 VLM 推理范围的 clip",
        "每 clip 打分选 1~5 关键帧",
        "按清晰度/目标丰富度/时间位置选帧，控制推理成本",
    ),
}


class EventTriggerType(str, Enum):
    """事件抽帧的四类触发条件（原文三章："触发条件有四类"）。

    原文对四类的统一解读："每一类都对应一种「模型表现与预期有偏差」的信号"。

    注意：这里的取值与回传域 ods_vehicle_trigger_event 的 trigger_type 是同一口径的
    公共键（跨域同名同义），落表时直接写 ``value``。
    """

    RULE_HIT = "rule_hit"
    ACTIVE_SAFETY = "active_safety"
    DRIVER_TAKEOVER = "driver_takeover"
    LOW_CONFIDENCE = "low_confidence"

    @property
    def name_cn(self) -> str:
        return {
            EventTriggerType.RULE_HIT: "命中挖掘规则",
            EventTriggerType.ACTIVE_SAFETY: "主动安全触发（AEB 等）",
            EventTriggerType.DRIVER_TAKEOVER: "驾驶员接管",
            EventTriggerType.LOW_CONFIDENCE: "模型低置信度",
        }[self]


assert len(EventTriggerType) == K.EVENT_TRIGGER_TYPE_COUNT, "触发条件必须是原文的四类"
assert len(SamplingTier) == K.GATE_COUNT, "抽帧层级必须是原文的三道闸门"


# --------------------------------------------------------------------------- image_id

#: image_id 格式：{data_id}_{camera_id}_F{frame_index:06d}
#: ⚠️ 原文未明确，本项目设计：原文只要求 "image_id 内嵌 data_id，免查表即可回溯到
#: 采集单元"，没给具体格式。本项目在 data_id 之后拼 camera_id（多路摄像头同步要求
#: 逐图保留视角）与零填充的帧序号（保证字典序 == 时间序）。
_IMAGE_ID_RE = re.compile(
    r"^(?P<data_id>COLLECT_[A-Z0-9]+_\d{14}_[0-9a-f]{4,})"
    r"_(?P<camera_id>[a-z0-9]+(?:-[a-z0-9]+)*)"
    r"_F(?P<frame_index>\d{6,})$"
)
_CAMERA_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True, slots=True)
class ImageId:
    """解析后的 image_id。``data_id`` 直接可用，无需回查帧表或 clip 表。"""

    raw: str
    data_id: str
    camera_id: str
    frame_index: int

    def __str__(self) -> str:  # pragma: no cover - 便于日志
        return self.raw


def build_image_id(data_id: str, camera_id: str, frame_index: int) -> str:
    """拼 image_id。

    Args:
        data_id: 一级 ID，必须是合法的 ``COLLECT_{车码}_{yyyyMMddHHmmss}_{seq}``。
        camera_id: 摄像头视角标识，小写短横线风格（如 ``front``、``side-left``）。
        frame_index: 该 clip 该路摄像头内的帧序号，从 0 起，非负。

    Raises:
        ValueError: data_id 非法、camera_id 不符合字符集、或 frame_index 为负。
    """
    parse_data_id(data_id)  # 早失败：产物必须挂在合法锚点上
    cam = camera_id.strip().lower()
    if not _CAMERA_ID_RE.match(cam):
        raise ValueError(f"camera_id 需为小写字母数字/短横线，收到 {camera_id!r}")
    if frame_index < 0:
        raise ValueError(f"frame_index 不能为负，收到 {frame_index}")
    return f"{data_id}_{cam}_F{frame_index:06d}"


def parse_image_id(raw: str) -> ImageId:
    """从 image_id 反解出 data_id / camera_id / frame_index（免查表回溯）。

    Raises:
        InvalidImageIdError: 格式不匹配。
    """
    m = _IMAGE_ID_RE.match(raw)
    if not m:
        raise InvalidImageIdError(
            f"不是合法的 image_id: {raw!r}；要求 {{data_id}}_{{camera_id}}_F{{帧序号:06d}}"
        )
    return ImageId(raw, m["data_id"], m["camera_id"], int(m["frame_index"]))


#: 位置词 → camera_position 取值。检查顺序即优先级：带方位后缀的侧视/后视先判，
#: 免得 "front-left"（左前视，装在侧面）被当成正前视。
_POSITION_ALIASES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("rear", "back", "tail"), "rear"),
    (("left",), "left"),
    (("right",), "right"),
    (("front", "fwd", "forward"), "front"),
)


def derive_camera_position(camera_id: str) -> str:
    """从 camera_id 推出 camera_position（front/left/right/rear）。

    原文五章只说 "同一时刻有前视、侧视、后视等多路摄像头"，没规定 camera_id 的命名法；
    取值域则来自共享契约 catalog 里 dwd_mining_image_frame_detail.camera_position 的
    列注释 front/left/right/rear（见 constants.CAMERA_POSITIONS）。

    ⚠️ 原文未明确，本项目设计：按短横线/下划线切词后匹配位置词。推不出来时返回
    空串而不是瞎猜一个——检索侧宁可看到 NULL，也不能看到错的视角。真实项目应由
    车型配置表给出权威映射，用 ``ClipInput.camera_positions`` 直接注入覆盖本函数。

    Returns:
        ``constants.CAMERA_POSITIONS`` 中的一个，或空串（无法判定）。
    """
    tokens = {t for t in re.split(r"[-_]+", camera_id.strip().lower()) if t}
    for aliases, position in _POSITION_ALIASES:
        if tokens.intersection(aliases):
            return position
    return ""


# --------------------------------------------------------------------------- 帧记录


@dataclass(slots=True)
class FrameRecord:
    """一条抽帧产物的**内存形态**，落湖目标是 dwd_mining_image_frame_detail。

    原文五章点名必须有的字段：image_id、clip 归属（data_id）、camera_id、帧序号、
    时间戳、GPS、文件路径、frame_quality_score。其余字段是闭环公共键与打分明细，
    ⚠️ 原文未明确，本项目设计：为了让选帧逻辑「可配置、可回溯」（原文四章原话），
    三个维度的分数都单独落湖，而不只落一个综合分。

    字段名与湖仓列名的关系：表结构的唯一事实源是 catalog/tables/_mining.py，本类
    **不是**表定义。少数字段在两边近义异名（如内存 ``clip_offset_ms`` 对列
    ``frame_offset_sec``），落湖时由 :meth:`to_row` 按 ``table.FRAME_COLUMN_MAP``
    统一翻译——内存侧保留毫秒整数与原文用语，湖仓侧一律用 registry 的列名。
    """

    # ---- 身份与血缘（闭环公共键）----
    image_id: str
    data_id: str
    camera_id: str
    frame_index: int
    frame_timestamp: datetime
    #: 相对 clip 起点的偏移（毫秒），驱动多路同步分组与时间位置打分
    clip_offset_ms: int
    artifact_id: str = ""
    parent_artifact_id: str = ""
    run_id: str = ""
    artifact_status: str = ArtifactStatus.ACTIVE.value
    project_code: str = ""
    vehicle_code: str = ""

    # ---- 多路摄像头同步 ----
    #: 同一时刻多路图片共享的组 ID（原文五章："同一时刻的多路图片作为一组样本"）
    frame_group_id: str = ""
    #: 摄像头安装位置 front/left/right/rear（原文五章 "前视、侧视、后视"）。
    #: 留空时由 :func:`derive_camera_position` 从 camera_id 推断。
    camera_position: str = ""

    # ---- 采集上下文 ----
    gps_lat: float | None = None
    gps_lon: float | None = None
    file_path: str = ""
    file_size_bytes: int | None = None
    image_width: int | None = None
    image_height: int | None = None

    # ---- 闸门归属 ----
    sampling_tier: str = SamplingTier.ROUTINE.value
    sampling_interval_sec: float = float(K.ROUTINE_INTERVAL_SECONDS)
    event_trigger_type: str = ""
    event_time: datetime | None = None
    event_window_start: datetime | None = None
    event_window_end: datetime | None = None

    # ---- 打分（推理抽帧）----
    #: 图像清晰度分，原文四章点名的落湖字段
    frame_quality_score: float | None = None
    object_richness_score: float | None = None
    temporal_position_score: float | None = None
    keyframe_score: float | None = None
    is_keyframe: bool = False

    # ---- 合规与版本 ----
    #: 抽帧前置脱敏校验结论，见 sampling.compliance
    desensitization_status: str = ""
    algo_version: str = K.SAMPLING_ALGO_VERSION_DEFAULT

    def __post_init__(self) -> None:
        if not self.image_id:
            self.image_id = build_image_id(self.data_id, self.camera_id, self.frame_index)
        parsed = parse_image_id(self.image_id)
        if parsed.data_id != self.data_id:
            raise ValueError(
                f"image_id 内嵌的 data_id={parsed.data_id!r} 与字段 data_id={self.data_id!r} 不一致"
            )
        if parsed.camera_id != self.camera_id:
            if parsed.camera_id == self.camera_id.strip().lower():
                # 大小写/空白差异：按 image_id 里的归一形式收敛，与 build_image_id 同口径
                self.camera_id = parsed.camera_id
            else:
                raise ValueError(
                    f"image_id 内嵌的 camera_id={parsed.camera_id!r} 与字段 "
                    f"camera_id={self.camera_id!r} 不一致——多路同步分组按 camera_id 走，"
                    "两者不一致会让同一路相机分裂成两路"
                )
        if not self.camera_position:
            self.camera_position = derive_camera_position(self.camera_id)
        elif self.camera_position not in K.CAMERA_POSITIONS:
            raise ValueError(
                f"camera_position 必须取自 {K.CAMERA_POSITIONS}，收到 {self.camera_position!r}"
            )

    @property
    def tier(self) -> SamplingTier:
        return SamplingTier(self.sampling_tier)

    def assign_artifact_id(self, algo_version: str | None = None) -> str:
        """按三级 ID 规则派生本帧的 artifact_id 并写回。

        payload 取 ``image_id|层级|间隔``——内容相同则 ID 相同，重试天然幂等
        （ids 模块规则一）。算法版本变化会产出新的 artifact_id，旧产物保留并由
        调用方标记 superseded（规则三：重刷不覆盖）。
        """
        version = algo_version or self.algo_version
        payload = f"{self.image_id}|{self.sampling_tier}|{self.sampling_interval_sec}"
        aid = derive_artifact_id(self.data_id, K.SAMPLING_STAGE, version, payload)
        self.artifact_id = aid.raw
        self.algo_version = version
        return self.artifact_id

    def mark_superseded(self) -> None:
        """重刷时把旧产物标记为 superseded（ids 规则三：重刷不是覆盖）。"""
        self.artifact_status = ArtifactStatus.SUPERSEDED.value

    def to_row(self) -> dict[str, Any]:
        """转成可直接写 Paimon 的行字典：**键一律是 registry 的列名**。

        近义异名按 ``table.FRAME_COLUMN_MAP`` 翻译（唯一的翻译点），其中
        ``clip_offset_ms``（毫秒整数）→ ``frame_offset_sec``（秒，DOUBLE）还要除 1000。
        枚举值已是字面量，datetime 原样保留交给写入器渲染。
        """
        row = {frame_column_for(k): v for k, v in asdict(self).items()}
        row["frame_offset_sec"] = self.clip_offset_ms / 1000.0
        return row


# --------------------------------------------------------------------------- 多路同步


@dataclass(slots=True)
class FrameGroup:
    """同一时刻的多路摄像头图片，作为一组样本（原文五章第 1 个实现要点）。

    "智驾车同一时刻有前视、侧视、后视等多路摄像头，同一时刻的多路图片作为一组样本，
    逐图保留 camera_id——检索时可按视角过滤，训练时可按视角组合"。
    """

    group_id: str
    data_id: str
    #: 组的基准时刻（组内首帧时间戳）
    captured_at: datetime
    frames: list[FrameRecord] = field(default_factory=list)

    @property
    def camera_ids(self) -> tuple[str, ...]:
        return tuple(sorted({f.camera_id for f in self.frames}))

    @property
    def camera_count(self) -> int:
        return len(self.camera_ids)

    def is_complete(self, expected_cameras: Sequence[str]) -> bool:
        """该组是否齐全（训练按视角组合时要求多路完整）。"""
        return set(expected_cameras).issubset(set(self.camera_ids))

    def __iter__(self) -> Iterator[FrameRecord]:
        return iter(self.frames)

    def __len__(self) -> int:
        return len(self.frames)


def group_by_capture_moment(
    frames: Iterable[FrameRecord],
    *,
    tolerance_ms: int = K.CAMERA_GROUP_SYNC_TOLERANCE_MS,
) -> list[FrameGroup]:
    """把多路摄像头的帧按「同一时刻」聚成组，并回写 frame_group_id。

    Args:
        frames: 待分组的帧，可以跨 camera_id，但必须同属一个 data_id。
        tolerance_ms: 「同一时刻」的容忍窗口（毫秒）。默认 ±50ms
            （⚠️ 原文未明确，本项目设计，见 constants.CAMERA_GROUP_SYNC_TOLERANCE_MS）。

    Returns:
        按时间升序的分组列表。每组内逐图保留各自的 camera_id。

    Raises:
        ValueError: 传入的帧跨了多个 data_id（分组是 clip 内概念）。
    """
    items = sorted(frames, key=lambda f: (f.clip_offset_ms, f.camera_id))
    if not items:
        return []
    data_ids = {f.data_id for f in items}
    if len(data_ids) > 1:
        raise ValueError(f"多路同步分组只在单个 clip 内进行，收到 {len(data_ids)} 个 data_id")

    groups: list[FrameGroup] = []
    anchor_ms: int | None = None
    current: FrameGroup | None = None
    for frame in items:
        if (
            current is None
            or anchor_ms is None
            or abs(frame.clip_offset_ms - anchor_ms) > tolerance_ms
        ):
            anchor_ms = frame.clip_offset_ms
            group_id = f"{frame.data_id}_G{anchor_ms:09d}"
            current = FrameGroup(group_id, frame.data_id, frame.frame_timestamp)
            groups.append(current)
        frame.frame_group_id = current.group_id
        current.frames.append(frame)
    return groups


# --------------------------------------------------------------------------- 落表自检

#: FrameRecord 的每个字段，经 table.FRAME_COLUMN_MAP 翻译后必须在 registry 里真有这一列。
#: 不加这道 import 期断言的话，多出来的字段会在 :meth:`FrameRecord.to_row` 里被拼进
#: INSERT 的列清单，单测用 InMemoryFrameWriter 看不出任何异常，一上真实 Paimon 才报
#: 「列不存在」——正是 a6 质量门禁里点名的那类静默失效。
_ROW_KEYS = {frame_column_for(f) for f in FrameRecord.__dataclass_fields__}
_MISSING_COLUMNS = sorted(_ROW_KEYS - set(frame_column_names()))
if _MISSING_COLUMNS:  # pragma: no cover - 只有 registry 改名/删列才会触发
    raise RuntimeError(
        f"FrameRecord 的字段 {_MISSING_COLUMNS} 在 {K.FRAME_TABLE_NAME} 里没有对应列；"
        "表结构的唯一事实源是 catalog/tables/_mining.py，请在那里补列，"
        "或在 sampling/table.py 的 FRAME_COLUMN_MAP 里登记近义异名"
    )
#: 反向：原文五章逐字点名「字段含 image_id、clip 归属、camera_id、帧序号、时间戳、
#: GPS、文件路径与 frame_quality_score」——这八项必须真的被 FrameRecord 产出，
#: 缺一项就是「产物统一落帧表」这个实现要点没落全。
_REQUIRED_BY_SOURCE = (
    "image_id",  # 原文："image_id 内嵌 data_id，免查表即可回溯采集单元"
    "data_id",  # 原文："clip 归属"
    "camera_id",  # 原文："逐图保留 camera_id"
    "frame_index",  # 原文："帧序号"
    "frame_timestamp",  # 原文："时间戳"
    "gps_lat",  # 原文："GPS"
    "gps_lon",
    "image_object_key",  # 原文："文件路径"（registry 列名）
    "frame_quality_score",  # 原文："与 frame_quality_score"
)
_UNCOVERED = sorted(set(_REQUIRED_BY_SOURCE) - _ROW_KEYS)
if _UNCOVERED:  # pragma: no cover - 只有本模块删字段才会触发
    raise RuntimeError(f"原文点名必须落表的字段 {_UNCOVERED} 在 FrameRecord 里缺失")
