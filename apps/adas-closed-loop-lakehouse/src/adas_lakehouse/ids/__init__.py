"""三级 ID 体系：data_id / artifact_id / run_id。

来源：系列一《数据闭环全局 data_id 设计：贯穿智驾全链路的三级 ID 体系》。

设计意图是「ID 本身即信息」——不查任何表，光看 ID 字符串就知道这是哪辆车、
什么时间采集的、做了哪个环节、哪个算法版本。

    一级 · data_id      clip 级，采集单元的终身锚点，采集端生成，重刷不变
      └─ 二级 · artifact_id   处理产物级，由「输入 + 算法版本」共同决定，新旧版本并存
           └─ 三级 · run_id   处理运行级，一次执行一条，绑定算法版本与参数快照

四条核心生成规则：
  1. 唯一性：data_id 的 sequence 段取自 UUID；artifact_id 追加 content_hash——
     内容相同则 ID 相同，重试天然幂等
  2. 时间戳：yyyyMMddHHmmss，精确到秒，按时间排序即可还原生产顺序
  3. 重刷：同一 clip 因算法更新重刷产线时 data_id 不变，新产物生成新 artifact_id，
     旧产物保留并标记 superseded
  4. 关联：衍生产物通过湖仓 parent_artifact_id 字段（冗余落表）+ 图库 DERIVED_FROM 边
     关联输入产物，逐级追溯至源头 clip
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

__all__ = [
    "TS_FORMAT",
    "ArtifactId",
    "DataId",
    "RunId",
    "ArtifactStatus",
    "new_data_id",
    "derive_artifact_id",
    "new_run_id",
    "parse_data_id",
    "parse_artifact_id",
    "parse_run_id",
    "content_hash",
]

#: 规则二：时间戳格式统一 yyyyMMddHHmmss，按字典序排序即为生产顺序
TS_FORMAT = "%Y%m%d%H%M%S"

_DATA_ID_RE = re.compile(r"^COLLECT_(?P<vehicle>[A-Z0-9]+)_(?P<ts>\d{14})_(?P<seq>[0-9a-f]{4,})$")
_ARTIFACT_ID_RE = re.compile(
    r"^(?P<data_id>COLLECT_[A-Z0-9]+_\d{14}_[0-9a-f]{4,})"
    r"_(?P<stage>[a-z][a-z0-9]*)_(?P<algo_version>v[0-9][0-9a-z.]*)"
    r"_(?P<content_hash>[0-9a-f]{8,})$"
)
_RUN_ID_RE = re.compile(r"^run_(?P<stage>[a-z][a-z0-9]*)_(?P<ts>\d{14})_(?P<seq>[0-9a-f]{4,})$")


class ArtifactStatus(str, Enum):
    """产物状态。重刷不是覆盖，而是长出新枝：旧产物保留并指向新产物。"""

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    INVALID = "invalid"


def _ts(moment: datetime | None = None) -> str:
    return (moment or datetime.now()).strftime(TS_FORMAT)


def content_hash(payload: bytes | str, *, length: int = 8) -> str:
    """内容哈希：artifact_id 的幂等来源。内容相同 → ID 相同 → 重试天然幂等。"""
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:length]


# --------------------------------------------------------------------------- 一级


@dataclass(frozen=True, slots=True)
class DataId:
    """clip 级锚点。一个 clip（约 1 分钟连续采集片段）对应一个 data_id，终身不变。"""

    raw: str
    vehicle_code: str
    collected_at: datetime
    sequence: str

    def __str__(self) -> str:
        return self.raw


def new_data_id(vehicle_code: str, collected_at: datetime | None = None) -> DataId:
    """采集端生成 data_id，随数据文件上传。

    >>> str(new_data_id("BP", datetime(2024, 1, 15, 14, 30, 22)))[:32]
    'COLLECT_BP_20240115143022_'[:32]
    """
    vehicle_code = vehicle_code.upper()
    ts = _ts(collected_at)
    seq = uuid.uuid4().hex[:4]  # 规则一：唯一性由 UUID 保证
    raw = f"COLLECT_{vehicle_code}_{ts}_{seq}"
    return DataId(raw, vehicle_code, collected_at or datetime.now(), seq)


def parse_data_id(raw: str) -> DataId:
    m = _DATA_ID_RE.match(raw)
    if not m:
        raise ValueError(f"不是合法的 data_id: {raw!r}")
    return DataId(
        raw,
        m["vehicle"],
        datetime.strptime(m["ts"], TS_FORMAT),
        m["seq"],
    )


# --------------------------------------------------------------------------- 二级


@dataclass(frozen=True, slots=True)
class ArtifactId:
    """处理产物级。内嵌 data_id——产物永远可回溯到采集单元。"""

    raw: str
    data_id: str
    stage: str
    algo_version: str
    content_hash: str

    def __str__(self) -> str:
        return self.raw


def derive_artifact_id(
    data_id: str | DataId,
    stage: str,
    algo_version: str,
    payload: bytes | str,
) -> ArtifactId:
    """由「输入 + 算法版本 + 内容」派生产物 ID。

    同一 clip 因算法升级重刷时 data_id 不变，stage/algo_version 变化即产出新 artifact_id，
    旧产物保留（标记 superseded）——版本分支由此形成，v3/v4 效果可对比。
    """
    did = str(data_id)
    parse_data_id(did)  # 早失败：产物必须挂在合法锚点上
    stage = stage.lower()
    if not algo_version.startswith("v"):
        raise ValueError(f"算法版本需形如 v3 / v4.1，收到 {algo_version!r}")
    ch = content_hash(payload)
    raw = f"{did}_{stage}_{algo_version}_{ch}"
    return ArtifactId(raw, did, stage, algo_version, ch)


def parse_artifact_id(raw: str) -> ArtifactId:
    m = _ARTIFACT_ID_RE.match(raw)
    if not m:
        raise ValueError(f"不是合法的 artifact_id: {raw!r}")
    return ArtifactId(raw, m["data_id"], m["stage"], m["algo_version"], m["content_hash"])


# --------------------------------------------------------------------------- 三级


@dataclass(frozen=True, slots=True)
class RunId:
    """处理运行级。一次执行一条，绑定算法版本与参数快照。"""

    raw: str
    stage: str
    started_at: datetime
    sequence: str

    def __str__(self) -> str:
        return self.raw


def new_run_id(stage: str, started_at: datetime | None = None) -> RunId:
    ts = _ts(started_at)
    seq = uuid.uuid4().hex[:8]
    raw = f"run_{stage.lower()}_{ts}_{seq}"
    return RunId(raw, stage.lower(), started_at or datetime.now(), seq)


def parse_run_id(raw: str) -> RunId:
    m = _RUN_ID_RE.match(raw)
    if not m:
        raise ValueError(f"不是合法的 run_id: {raw!r}")
    return RunId(raw, m["stage"], datetime.strptime(m["ts"], TS_FORMAT), m["seq"])
