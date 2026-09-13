"""OSS 侧：文件元信息、对象存储准入校验、完整性与可解码性探针。

来源：[a8] 第五章「入湖原则：文件外置，湖仓只存元信息」，三条原则逐字：
  1. **文件本体存 OSS**：文件本身保存在对象存储，湖仓保存的是文件元信息和解析后的关键信息；
  2. **元信息经 Kafka 实时写入 ods_data_file_meta**：实时任务实时消费写入湖仓；
  3. **元信息写入即可用**：下游 DWD 加工与查询立即可以引用，文件本体通过 file_path 按需读取。

[a8] 点名三个字段：``data_id`` 是全局三级 ID 的起点，把文件挂进血缘体系；
``checksum`` 让文件完整性随时可验证；``storage_class`` 对接存储生命周期管理——
标准 / 低频 / 归档的降冷策略。

⚠️ 契约边界：共享契约 ``catalog/tables/_collect.py`` 里的 ``ods_data_file_meta``
登记了 file_id / file_type / data_id / object_key / file_size_bytes / checksum_md5 /
sensor_id / duration_sec 八个基础业务字段，外加一组门禁判据列——其中
``vehicle_desensitized_flag`` / ``cloud_compliance_decrypted_flag`` / ``decodable_flag``
正是本模块的产出物，**必须按契约列名落表**（见 ``compliance.CONTRACT_REDACTION_FLAGS``）：
[a5] 第六章要求大文件外置后湖仓保留「文件大小、脱敏标记、校验和、归属 data_id」四项，
脱敏标记丢了就等于这条要求没落地。

原文强调的 ``storage_class`` 在契约里仍无对应列。契约只读不得修改，因此本模块的
``FileMeta`` 是「入湖侧的完整元信息对象」，落表时经 ``rows.project_to_table()``
投影到注册列，未进湖的字段由 ``FileMeta.off_table_fields()`` 明确列出，不做静默丢弃。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlparse

from ..config import settings
from ..ids import parse_data_id
from .compliance import ComplianceMarks
from .constants import (
    ARCHIVE_RESTORE_MAX_HOURS,
    ARCHIVE_TIER_COST_FACTOR,
    ARCHIVE_TIER_NO_ACCESS_DAYS,
    COLD_TIER_COST_FACTOR,
    COLD_TIER_NO_ACCESS_DAYS,
    DECODE_PROBE_BYTES,
    FILE_STORAGE_CLASSES,
    HOT_TIER_BUFFER_DAYS,
    STORAGE_TIER_COUNT,
    WARM_TIER_DAYS,
)
from .errors import MissingDependency
from .rows import dropped_fields, project_to_table

__all__ = [
    "FILE_META_TABLE",
    "StorageClass",
    "FileType",
    "FILE_TYPE_ALIASES",
    "normalize_file_type",
    "FileMeta",
    "ObjectStat",
    "ObjectUri",
    "ObjectStore",
    "NullObjectStore",
    "InMemoryObjectStore",
    "S3CompatibleObjectStore",
    "parse_object_uri",
    "validate_object_key",
    "validate_data_id",
    "verify_checksum",
    "DecodeProbeResult",
    "probe_decodable",
    "md5_of",
]

#: 元信息落的 ODS 表（[a8] 第五章原则二点名）
FILE_META_TABLE = "ods_data_file_meta"


class StorageClass(str, Enum):
    """文件的存储级别。

    [a8]：「storage_class 对接存储生命周期管理——标准 / 低频 / 归档的降冷策略，
    正是系列一收官篇讲过的五级分层在文件域的应用」。

    五级分层（[a5] 第八章）完整口径为 热 / 温 / 冷 / 归档 / 删除，其中「热」是
    CPFS/NAS 介质、「删除」不是一种存储级别，因此文件域的 storage_class 取值
    落在 OSS 的三档上；温度分层与降冷调度由 ``adas_lakehouse.lifecycle`` 子系统负责，
    本模块只负责把 storage_class 如实带进湖。
    """

    STANDARD = "standard"
    INFREQUENT = "infrequent"
    ARCHIVE = "archive"

    @property
    def label_cn(self) -> str:
        return {"standard": "标准", "infrequent": "低频", "archive": "归档"}[self.value]

    @property
    def cost_factor(self) -> float:
        """相对 OSS 标准存储的成本系数（[a5] 第八章：低频约 0.5x、归档约 0.15x）。"""
        return {
            "standard": 1.0,
            "infrequent": COLD_TIER_COST_FACTOR,
            "archive": ARCHIVE_TIER_COST_FACTOR,
        }[self.value]

    @property
    def enter_condition(self) -> str:
        """进入该级别的条件（[a5] 第八章表格「进入条件」列）。"""
        return {
            "standard": f"创建 {WARM_TIER_DAYS} 天内，或 {WARM_TIER_DAYS} 天内有访问"
            f"（训练结束 {HOT_TIER_BUFFER_DAYS} 天缓冲后从 CPFS/NAS 淘汰回此层）",
            "infrequent": f"连续 {COLD_TIER_NO_ACCESS_DAYS} 天无访问",
            "archive": f"连续 {ARCHIVE_TIER_NO_ACCESS_DAYS} 天无访问且过保留策略阈值，"
            f"取回 ≤{ARCHIVE_RESTORE_MAX_HOURS} 小时",
        }[self.value]


assert tuple(sc.label_cn for sc in StorageClass) == FILE_STORAGE_CLASSES
assert STORAGE_TIER_COUNT == 5  # 热 / 温 / 冷 / 归档 / 删除


class FileType(str, Enum):
    """采集文件类型。

    取值口径来自共享契约 ``ods_data_file_meta.file_type`` 的字段注释
    「video/pointcloud/radar/imu/gps/can」，该字段同时是该表的分区字段
    （分区规则二：有明确业务分类过滤 → 按业务字段分区）。
    """

    VIDEO = "video"
    POINTCLOUD = "pointcloud"
    RADAR = "radar"
    IMU = "imu"
    GPS = "gps"
    CAN = "can"


#: ⚠️ 原文未明确，本项目设计：[a8] 用「相机图像 / 激光雷达点云 / IMU / GNSS 传感器数据」
#: 描述采集数据，与共享契约的六类取值不是同一套词。这里给出别名映射——
#: 相机图像按契约归入 video 分区（采集侧相机数据是连续帧序列，不是散帧）。
FILE_TYPE_ALIASES: dict[str, FileType] = {
    "image": FileType.VIDEO,
    "camera": FileType.VIDEO,
    "lidar": FileType.POINTCLOUD,
    "pcd": FileType.POINTCLOUD,
    "gnss": FileType.GPS,
}


def normalize_file_type(raw: str) -> FileType:
    """把来源侧的类型词归一到契约取值。

    Raises:
        ValueError: 既不是契约取值也不在别名表里——有效性维度的门禁违规。
    """
    key = (raw or "").strip().lower()
    try:
        return FileType(key)
    except ValueError:
        pass
    if key in FILE_TYPE_ALIASES:
        return FILE_TYPE_ALIASES[key]
    raise ValueError(
        f"未知文件类型 {raw!r}；契约取值为 {[t.value for t in FileType]}，"
        f"别名为 {sorted(FILE_TYPE_ALIASES)}"
    )


# --------------------------------------------------------------------------- 元信息


def _opt_float(value: Any) -> float | None:
    """可选数值字段：缺失 / 空串 → None，非法值 → None（交给门禁按「判据缺失」处理）。"""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _opt_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class FileMeta:
    """一个采集文件的完整入湖侧元信息。

    [a8] 第五章：「元信息表的结构，每个字段都服务于『找回文件、验证文件、管理文件』」。
    对应关系：
      · 找回文件 —— ``object_key`` / ``file_path``（本体通过 file_path 按需读取）
      · 验证文件 —— ``checksum_md5`` / ``file_size_bytes`` / 双合规标记
      · 管理文件 —— ``storage_class`` / ``data_id``（挂进血缘体系）

    [a5] 第六章「大文件外置」要求湖仓保留的四项：文件大小、脱敏标记、校验和、归属 data_id，
    在本结构里分别是 file_size_bytes / marks / checksum_md5 / data_id。
    """

    file_id: str
    file_type: FileType
    data_id: str
    object_key: str
    file_size_bytes: int
    checksum_md5: str
    sensor_id: str = ""
    duration_sec: float | None = None
    #: ⚠️ 契约 ods_data_file_meta 无此列，保留在入湖侧供门禁与生命周期使用
    storage_class: StorageClass = StorageClass.STANDARD
    #: 双合规标记（[a8] 门禁 P0 检查项的判据）
    marks: ComplianceMarks = field(default_factory=ComplianceMarks)
    #: 可解码探针结论，落契约列 ``decodable_flag``（「上游解码探针的结论，随元信息落表」）。
    #: None = 尚未探测 / 探针不可用；由 ``gate.OssComplianceGate`` 在 P1 检查时回填。
    decodable_flag: bool | None = None
    #: 合规数据副本落到智驾云 OSS 的时间（第 ④ 步分发完成时刻）
    distributed_at: datetime | None = None
    #: 采集项目 / 车辆，跨域公共键
    project_code: str = ""
    vehicle_code: str = ""
    #: 上游算好、门禁只判阈值的物理完整性判据。它们都是共享契约
    #: ``ods_data_file_meta`` 真实存在的列，取值由采集 / 合规分发侧随元信息一起投递，
    #: 本通道**原样带进湖**——ODS 是「原样入湖」层，不在这里推算。
    #: 不带进来的后果是湖表这几列恒为 NULL，靠它们判定的规则（多模态完整性、
    #: 连续丢帧、时间同步、近重复、批次到达完整性）会静默失效。
    frame_group_modalities: str = ""
    continuous_frame_loss_rate: float | None = None
    vehicle_type: str = ""
    time_sync_error_ms: int | None = None
    near_duplicate_similarity: float | None = None
    batch_missing_ratio: float | None = None

    def __post_init__(self) -> None:
        if isinstance(self.file_type, str):
            self.file_type = normalize_file_type(self.file_type)
        if isinstance(self.storage_class, str):
            self.storage_class = StorageClass(self.storage_class)

    # ---- 派生 ----

    @property
    def file_path(self) -> str:
        """文件本体的完整 URI。[a8]：「文件本体通过 file_path 按需读取」。"""
        if "://" in self.object_key:
            return self.object_key
        return f"s3://{settings().minio.raw_bucket}/{self.object_key.lstrip('/')}"

    def to_row(self) -> dict[str, Any]:
        """转成入湖行（尚未盖系统字段、尚未投影）。

        ``marks.to_meta()`` 同时给出入湖侧的 ``redaction_*`` 与契约列名
        ``vehicle_desensitized_flag`` / ``cloud_compliance_decrypted_flag``；
        ``decodable_flag`` 单列在这里补上——这三列是契约 ``ods_data_file_meta``
        真实存在的列，投影后会一起进湖，脱敏标记因此不会在投影时丢失。
        """
        return {
            "file_id": self.file_id,
            "file_type": self.file_type.value,
            "data_id": self.data_id,
            "object_key": self.object_key,
            "file_size_bytes": int(self.file_size_bytes),
            "checksum_md5": self.checksum_md5,
            "sensor_id": self.sensor_id,
            "duration_sec": self.duration_sec,
            "decodable_flag": self.decodable_flag,
            "frame_group_modalities": self.frame_group_modalities,
            "continuous_frame_loss_rate": self.continuous_frame_loss_rate,
            "vehicle_type": self.vehicle_type,
            "time_sync_error_ms": self.time_sync_error_ms,
            "near_duplicate_similarity": self.near_duplicate_similarity,
            "batch_missing_ratio": self.batch_missing_ratio,
            "storage_class": self.storage_class.value,
            "file_path": self.file_path,
            "project_code": self.project_code,
            "vehicle_code": self.vehicle_code,
            "distributed_at": self.distributed_at,
            **self.marks.to_meta(),
        }

    def to_ods_row(self) -> dict[str, Any]:
        """投影到 ``ods_data_file_meta`` 的注册列。"""
        return project_to_table(FILE_META_TABLE, self.to_row())

    def off_table_fields(self) -> tuple[str, ...]:
        """列出未能进湖的字段（契约无对应列），避免静默丢弃。"""
        return dropped_fields(FILE_META_TABLE, self.to_row())

    @classmethod
    def from_kafka_message(cls, payload: Mapping[str, Any]) -> FileMeta:
        """从合规分发侧投递的 Kafka 文件元信息消息构造。

        [a8] 第 ④ 步：「合规数据副本复制至智驾云 OSS，同时向业务方 Kafka 发送文件元信息」。

        Raises:
            KeyError / ValueError: 必填字段缺失或取值非法——交由门禁转成拒绝入湖。
        """
        required = ("file_id", "file_type", "data_id", "object_key", "file_size_bytes")
        missing = [k for k in required if payload.get(k) in (None, "")]
        if missing:
            raise KeyError(f"文件元信息缺少必填字段: {missing}")
        moment = payload.get("distributed_at")
        if isinstance(moment, str):
            try:
                moment = datetime.fromisoformat(moment)
            except ValueError:
                moment = None
        return cls(
            file_id=str(payload["file_id"]),
            file_type=normalize_file_type(str(payload["file_type"])),
            data_id=str(payload["data_id"]),
            object_key=str(payload["object_key"]),
            file_size_bytes=int(payload["file_size_bytes"]),
            checksum_md5=str(payload.get("checksum_md5") or payload.get("checksum") or ""),
            sensor_id=str(payload.get("sensor_id") or ""),
            duration_sec=(
                float(payload["duration_sec"]) if payload.get("duration_sec") is not None else None
            ),
            storage_class=StorageClass(str(payload.get("storage_class") or "standard")),
            marks=ComplianceMarks.from_meta(dict(payload)),
            decodable_flag=(
                None if payload.get("decodable_flag") is None else bool(payload["decodable_flag"])
            ),
            distributed_at=moment,
            project_code=str(payload.get("project_code") or ""),
            vehicle_code=str(payload.get("vehicle_code") or ""),
            frame_group_modalities=str(payload.get("frame_group_modalities") or ""),
            continuous_frame_loss_rate=_opt_float(payload.get("continuous_frame_loss_rate")),
            vehicle_type=str(payload.get("vehicle_type") or ""),
            time_sync_error_ms=_opt_int(payload.get("time_sync_error_ms")),
            near_duplicate_similarity=_opt_float(payload.get("near_duplicate_similarity")),
            batch_missing_ratio=_opt_float(payload.get("batch_missing_ratio")),
        )


# --------------------------------------------------------------------------- 对象存储


@dataclass(frozen=True, slots=True)
class ObjectStat:
    """对象存储侧的对象事实，用于与元信息比对（门禁 P1 检查项）。"""

    key: str
    size_bytes: int
    etag: str = ""
    storage_class: str = ""
    last_modified: datetime | None = None


@runtime_checkable
class ObjectStore(Protocol):
    """对象存储探针接口。实现可以是真 S3/OSS，也可以是测试替身。"""

    def head(self, key: str) -> ObjectStat | None:
        """取对象元数据；对象不存在返回 None。"""

    def read_range(self, key: str, length: int) -> bytes:
        """读取对象前 length 字节（用于可解码性探针）。"""


class NullObjectStore:
    """空探针：不访问任何对象存储，一律返回「未知」。

    用于离线回放与单元测试。门禁遇到未知会把 P1 检查降级为「带标放行」而不是拒绝
    （见 gate.py 的 SKIPPED 处理），避免因为探针不可用误杀数据。
    """

    def head(self, key: str) -> ObjectStat | None:  # noqa: D102 - 见 Protocol
        return None

    def read_range(self, key: str, length: int) -> bytes:  # noqa: D102
        return b""


class InMemoryObjectStore:
    """内存对象存储：把 key -> bytes 放进字典，供本地演练与测试使用。"""

    def __init__(self, objects: Mapping[str, bytes] | None = None) -> None:
        self._objects: dict[str, bytes] = dict(objects or {})

    def put(self, key: str, data: bytes) -> ObjectStat:
        self._objects[key] = data
        return ObjectStat(key, len(data), etag=hashlib.md5(data).hexdigest())

    def head(self, key: str) -> ObjectStat | None:
        data = self._objects.get(_bare_key(key))
        if data is None:
            return None
        return ObjectStat(key, len(data), etag=hashlib.md5(data).hexdigest())

    def read_range(self, key: str, length: int) -> bytes:
        return self._objects.get(_bare_key(key), b"")[:length]


class S3CompatibleObjectStore:
    """S3 / 阿里云 OSS / MinIO 探针，连接信息取自 ``config.settings().minio``。

    boto3 **延迟 import**：验收阶段会做全量 import 检查，裸环境下
    ``import adas_lakehouse.ingest`` 不能因为缺少客户端库而失败。
    """

    def __init__(self, bucket: str | None = None) -> None:
        cfg = settings().minio
        self.bucket = bucket or cfg.raw_bucket
        self._endpoint = cfg.endpoint
        self._access_key = cfg.access_key
        self._secret_key = cfg.secret_key
        self._client: Any | None = None

    def _lazy_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - 取决于环境
            raise MissingDependency("boto3", "访问智驾云 OSS / MinIO 对象存储") from exc
        self._client = boto3.client(
            "s3",
            endpoint_url=self._endpoint,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
        )
        return self._client

    def head(self, key: str) -> ObjectStat | None:
        client = self._lazy_client()
        try:
            resp = client.head_object(Bucket=self.bucket, Key=_bare_key(key))
        except Exception:  # 对象不存在 / 无权限，统一按「取不到」处理
            return None
        return ObjectStat(
            key=key,
            size_bytes=int(resp.get("ContentLength", 0)),
            etag=str(resp.get("ETag", "")).strip('"'),
            storage_class=str(resp.get("StorageClass", "") or "STANDARD"),
            last_modified=resp.get("LastModified"),
        )

    def read_range(self, key: str, length: int) -> bytes:
        client = self._lazy_client()
        try:
            resp = client.get_object(
                Bucket=self.bucket, Key=_bare_key(key), Range=f"bytes=0-{max(length - 1, 0)}"
            )
            return resp["Body"].read()
        except Exception:
            return b""


def _bare_key(key: str) -> str:
    """把 ``s3://bucket/a/b.pcd`` 归一成 ``a/b.pcd``。"""
    if "://" in key:
        return urlparse(key).path.lstrip("/")
    return key.lstrip("/")


# --------------------------------------------------------------------------- 校验


_SCHEME_WHITELIST = ("s3", "oss")
#: ⚠️ 原文未明确，本项目设计：object_key 的字符白名单。
#: 原文只要求「file_path 合法且指向智驾云 OSS」，未给字符级规则；这里禁掉
#: 反斜杠、空白与 `..`，避免路径穿越与跨 bucket 拼接。
_KEY_RE = re.compile(r"^[A-Za-z0-9!_.*'()\-/=:+]+$")


@dataclass(frozen=True, slots=True)
class ObjectUri:
    """解析后的对象 URI。"""

    scheme: str
    bucket: str
    key: str

    def __str__(self) -> str:
        return f"{self.scheme}://{self.bucket}/{self.key}"


def parse_object_uri(uri: str, *, default_bucket: str | None = None) -> ObjectUri:
    """解析对象 URI；裸 key 用 ``default_bucket`` 补全（缺省取智驾云 raw_bucket）。

    Raises:
        ValueError: scheme 非 s3/oss，或 bucket / key 为空。
    """
    bucket = default_bucket or settings().minio.raw_bucket
    if "://" not in uri:
        key = uri.lstrip("/")
        if not key:
            raise ValueError("object_key 为空")
        return ObjectUri("s3", bucket, key)
    parsed = urlparse(uri)
    if parsed.scheme not in _SCHEME_WHITELIST:
        raise ValueError(
            f"file_path 必须指向智驾云对象存储（{'/'.join(_SCHEME_WHITELIST)}），"
            f"收到 scheme={parsed.scheme!r}：http(s) 直链意味着数据走公网"
        )
    if not parsed.netloc:
        raise ValueError(f"file_path 缺少 bucket: {uri!r}")
    key = parsed.path.lstrip("/")
    if not key:
        raise ValueError(f"file_path 缺少对象 key: {uri!r}")
    return ObjectUri(parsed.scheme, parsed.netloc, key)


def validate_object_key(
    uri: str,
    *,
    allowed_buckets: Iterable[str] | None = None,
    forbidden_buckets: Iterable[str] | None = None,
) -> list[str]:
    """校验 file_path 合法且指向智驾云 OSS（门禁 P1 检查项的前半段）。

    Args:
        uri: object_key 或完整 file_path。
        allowed_buckets: 允许的 bucket 白名单，缺省为 ``settings().minio.raw_bucket``。
            **合规云的 bucket 永远不在白名单里**——[a8] 第四章：合规云对象存储
            不对外暴露，智驾云侧的 file_path 只能指向合规数据副本所在的智驾云 OSS。
        forbidden_buckets: 明令禁止的 bucket（合规云对象存储）。白名单已经能拦下它，
            这里单列一条是为了让拒绝理由说清楚**越的是哪条边界**：指向合规云的
            file_path 意味着智驾云侧的下游会直接去读合规云的对象，而 [a8] 第二章的
            分界是「进入智驾云的只有合规数据副本 + 文件元信息」。

    Returns:
        违规描述列表，空列表表示通过。
    """
    problems: list[str] = []
    try:
        parsed = parse_object_uri(uri)
    except ValueError as exc:
        return [str(exc)]

    for bucket in forbidden_buckets or ():
        if bucket and parsed.bucket == bucket:
            problems.append(
                f"file_path 指向合规云 bucket={bucket!r}：合规云对象存储不对外暴露，"
                "只有脱敏脱密后的数据副本与文件元信息可以离开，湖表里的 file_path "
                "必须指向智驾云 OSS 上的副本"
            )
    buckets = tuple(allowed_buckets or (settings().minio.raw_bucket,))
    if parsed.bucket not in buckets:
        problems.append(
            f"file_path 指向的 bucket={parsed.bucket!r} 不在智驾云 OSS 白名单 {buckets} 内"
        )
    if ".." in parsed.key.split("/"):
        problems.append("object_key 含 `..` 路径穿越片段")
    if not _KEY_RE.match(parsed.key):
        problems.append(f"object_key 含非法字符: {parsed.key!r}")
    return problems


def md5_of(payload: bytes) -> str:
    """计算 MD5，与 ``ods_data_file_meta.checksum_md5`` 同口径。"""
    return hashlib.md5(payload).hexdigest()


def verify_checksum(
    expected_md5: str, *, payload: bytes | None = None, stat: ObjectStat | None = None
) -> list[str]:
    """校验 checksum 可用性与一致性（门禁 P1 检查项的后半段）。

    [a8]：「checksum 让文件完整性随时可验证」。三种情形：
      · 给了 payload —— 直接算 MD5 比对（最强）；
      · 只给了 ObjectStat —— 用 ETag 比对（分片上传的 ETag 带 ``-N`` 后缀，
        此时只能确认「对象存在且 ETag 非空」，不做强比对）；
      · 都没有 —— 只校验 checksum 字段本身是否是合法 MD5 字面量。

    Returns:
        违规描述列表，空列表表示通过。
    """
    problems: list[str] = []
    if not expected_md5:
        return ["checksum 缺失，文件完整性无法验证"]
    if not re.fullmatch(r"[0-9a-fA-F]{32}", expected_md5):
        problems.append(f"checksum 不是合法的 MD5 字面量: {expected_md5!r}")
        return problems
    if payload is not None:
        actual = md5_of(payload)
        if actual.lower() != expected_md5.lower():
            problems.append(f"checksum 不一致：元信息 {expected_md5}，实际 {actual}")
        return problems
    if (
        stat is not None
        and stat.etag
        and "-" not in stat.etag
        and stat.etag.lower() != expected_md5.lower()
    ):
        problems.append(f"checksum 与对象 ETag 不一致：元信息 {expected_md5}，ETag {stat.etag}")
    return problems


#: ⚠️ 原文未明确，本项目设计：[a8] 只说「图像 / 点云文件完整性与可解码性校验」，
#: 没给校验方式。本项目用「魔数 + 非空」做轻量探针——真正逐帧解码代价过高，
#: 不适合放在实时入湖链路上；重解码校验应由下游 DWD 加工阶段承担。
FILE_MAGIC: dict[FileType, tuple[bytes, ...]] = {
    FileType.VIDEO: (
        b"\xff\xd8\xff",  # JPEG
        b"\x89PNG\r\n\x1a\n",  # PNG
        b"RIFF",  # AVI
    ),
    FileType.POINTCLOUD: (
        b"# .PCD",  # PCL 点云
        b"LASF",  # LAS/LAZ
        b"PLY",
        b"ply",
    ),
}

#: ISO BMFF（mp4 / mov）没有固定的首字节魔数：前 4 字节是 box size，第 5~8 字节才是
#: ``ftyp``。早先这里用 ``b"\x00\x00\x00"`` 当前缀，等于「任何以三个零字节开头的文件都算
#: 可解码」——一个被截断成全零的坏文件正好从这里溜过去。改判偏移 4 处的 box type。
_ISO_BMFF_BOX_TYPES: tuple[bytes, ...] = (b"ftyp",)


def _is_iso_bmff(header: bytes) -> bool:
    """mp4 / mov：前 4 字节是 box size，第 5~8 字节是 box type ``ftyp``。"""
    return len(header) >= 8 and header[4:8] in _ISO_BMFF_BOX_TYPES


@dataclass(frozen=True, slots=True)
class DecodeProbeResult:
    """可解码性探针结果。``known`` 为 False 表示探针拿不到数据，不构成拒绝理由。"""

    known: bool
    decodable: bool
    reason: str = ""


def probe_decodable(
    meta: FileMeta,
    store: ObjectStore,
    *,
    probe_bytes: int = DECODE_PROBE_BYTES,
) -> DecodeProbeResult:
    """文件本体完整性与可解码性探针（门禁 P1 检查项）。

    判定顺序：
      1. 对象必须存在（head 取不到 → 探针未知，不判失败，交由 SKIPPED 带标放行）；
      2. 对象大小必须与元信息一致且 > 0（完整性）；
      3. 图像 / 点云按魔数判断可解码性；其余类型（radar/imu/gps/can）
         原文未要求解码校验，只做非空判断。

    Args:
        meta: 文件元信息。
        store: 对象存储探针。
        probe_bytes: 读取的文件头字节数，缺省 32（见 constants.DECODE_PROBE_BYTES）。
    """
    stat = store.head(meta.object_key)
    if stat is None:
        return DecodeProbeResult(False, False, "对象存储探针不可用或对象不存在，跳过可解码性校验")
    if stat.size_bytes <= 0:
        return DecodeProbeResult(True, False, "对象大小为 0，文件本体不完整")
    if meta.file_size_bytes and stat.size_bytes != meta.file_size_bytes:
        return DecodeProbeResult(
            True,
            False,
            f"文件大小不一致：元信息 {meta.file_size_bytes} 字节，对象实际 {stat.size_bytes} 字节",
        )

    magics = FILE_MAGIC.get(meta.file_type)
    if not magics:
        return DecodeProbeResult(
            True, True, f"{meta.file_type.value} 类型不做魔数校验，仅校验非空与大小一致"
        )

    header = store.read_range(meta.object_key, probe_bytes)
    if not header:
        return DecodeProbeResult(False, False, "读取文件头失败，跳过可解码性校验")
    if any(header.startswith(m) for m in magics):
        return DecodeProbeResult(True, True, "")
    if meta.file_type is FileType.VIDEO and _is_iso_bmff(header):
        return DecodeProbeResult(True, True, "")
    return DecodeProbeResult(
        True,
        False,
        f"文件头魔数不匹配 {meta.file_type.value} 的已知格式：{header[:8]!r}",
    )


def validate_data_id(data_id: str) -> list[str]:
    """data_id 格式与来源前缀合法性校验（门禁 P0 检查项）。

    [a8]：「全局数据 ID 格式与来源前缀合法性（血缘追溯起点）」。
    实现直接复用共享契约 ``ids.parse_data_id``——格式为
    ``COLLECT_{车码}_{yyyyMMddHHmmss}_{seq}``，来源前缀必须是 ``COLLECT_``。
    """
    if not data_id:
        return ["data_id 缺失——血缘追溯起点丢失，文件无法挂进血缘体系"]
    try:
        parse_data_id(data_id)
    except ValueError as exc:
        return [str(exc)]
    return []
