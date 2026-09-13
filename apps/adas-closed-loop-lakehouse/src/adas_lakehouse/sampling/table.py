"""dwd_mining_image_frame_detail 的表契约入口——结构**只**从 catalog.registry 取。

原文一章："抽帧产物只新增一张表——dwd_mining_image_frame_detail（抽帧图片明细表），
image_id 内嵌 data_id，免查表即可回溯到采集单元。上游一张表、下游一张表，链路干净。"
原文五章："三层抽帧结果全部写 dwd_mining_image_frame_detail，字段含 image_id、
clip 归属、camera_id、帧序号、时间戳、GPS、文件路径与 frame_quality_score。"

⚠️ 本模块**不再自带 TableSpec**。表结构的唯一事实源是 ``catalog/tables/_mining.py``
（经 ``catalog.registry`` 聚合）。此前这里有一份同名表的本地定义，与 registry 的列名
两套并存——按本地那份拼出来的 SQL 跑到真实 Paimon 上会直接报「列不存在」。
现在 ``FRAME_TABLE_SPEC`` 就是 registry 里的那一个对象（同一个实例，不是副本），
物理策略（bucket=16 / 不分区 / changelog-producer=lookup / 主键 image_id）一并跟随 registry。

sampling 侧的内存模型 ``frames.FrameRecord`` 保留自己的字段名（毫秒偏移、
``sampling_tier`` 这类贴着原文用语的名字），落湖时经 :data:`FRAME_COLUMN_MAP`
统一翻译成 registry 的列名——**翻译只发生在这一处**，别处一律直接用 registry 列名。
"""

from __future__ import annotations

from ..catalog import registry
from ..catalog.spec import TableSpec
from ..config import settings
from . import constants as K

__all__ = [
    "FRAME_TABLE_SPEC",
    "UPSTREAM_CLIP_SPEC",
    "UPSTREAM_FILE_META_SPEC",
    "FRAME_COLUMN_MAP",
    "frame_column_names",
    "frame_column_for",
    "render_frame_table_ddl",
]


#: 帧表规格。**直接是 registry 里的那一个 TableSpec**，本模块不复制、不改写。
FRAME_TABLE_SPEC: TableSpec = registry.by_name(K.FRAME_TABLE_NAME)

#: 上游两张采集域表的规格。原文一章划的边界是「抽帧产物只新增一张表」，代价是
#: 上游那两张表必须真的在——抽帧引擎读不到 clip 元信息与文件脱敏标记就无从启动。
#: 在这里解析一次，等于把「只消费采集域既有表」这条边界做成 import 期契约：
#: registry 里哪天没了这两张表，抽帧子系统当场炸，而不是等到跑批时 SELECT 报错。
UPSTREAM_CLIP_SPEC: TableSpec = registry.by_name(K.UPSTREAM_CLIP_TABLE)
UPSTREAM_FILE_META_SPEC: TableSpec = registry.by_name(K.UPSTREAM_FILE_META_TABLE)


#: sampling 内存字段名 → registry 列名（只登记两边不同名的；同名的不必写）。
#:
#: 每一条都是「近义异名」而非新增语义，出处见 catalog/tables/_mining.py 的 docstring：
#:   · clip_offset_ms → frame_offset_sec —— 还带单位换算（毫秒 ÷ 1000 → 秒）。
#:     内存侧坚持用整数毫秒：多路摄像头同步的 ±50ms 判定与关键帧最小间距都按毫秒算，
#:     换成浮点秒会引入比较误差；换算只在落湖这一步做。
#:   · sampling_tier → extract_level
#:   · event_trigger_type → trigger_event_type（字序颠倒的同义列，取值域与
#:     ods_vehicle_trigger_event.trigger_type 同口径）
#:   · event_window_start/end → event_window_start_time/_end_time
#:   · file_path → image_object_key，file_size_bytes → image_size_bytes
#:   · desensitization_status → desensitize_status
FRAME_COLUMN_MAP: dict[str, str] = {
    "clip_offset_ms": "frame_offset_sec",
    "sampling_tier": "extract_level",
    "event_trigger_type": "trigger_event_type",
    "event_window_start": "event_window_start_time",
    "event_window_end": "event_window_end_time",
    "file_path": "image_object_key",
    "file_size_bytes": "image_size_bytes",
    "desensitization_status": "desensitize_status",
}


def frame_column_names() -> tuple[str, ...]:
    """表的全部列名（含由 spec 按层自动追加的系统字段），取自 registry。"""
    return tuple(c.name for c in FRAME_TABLE_SPEC.all_columns())


def frame_column_for(field_name: str) -> str:
    """把 sampling 内存字段名翻译成 registry 列名（同名字段原样返回）。"""
    return FRAME_COLUMN_MAP.get(field_name, field_name)


# 早失败：映射的目标列必须真的存在于 registry，否则拼出来的 SQL 会在真实 Paimon 上
# 报「列不存在」，而单测里用 InMemoryFrameWriter 是发现不了的。
_UNKNOWN_TARGETS = sorted(set(FRAME_COLUMN_MAP.values()) - set(frame_column_names()))
if _UNKNOWN_TARGETS:  # pragma: no cover - 只有 registry 改名/删列才会触发
    raise RuntimeError(
        f"FRAME_COLUMN_MAP 指向了 {K.FRAME_TABLE_NAME} 不存在的列 {_UNKNOWN_TARGETS}；"
        "表结构的唯一事实源是 catalog/tables/_mining.py，请在那里补列或对齐列名"
    )


def render_frame_table_ddl(catalog: str | None = None, database: str | None = None) -> str:
    """渲染帧表的 Flink SQL 建表语句，连接信息取自 config.settings()。"""
    cfg = settings().paimon
    return FRAME_TABLE_SPEC.render_ddl(
        catalog=catalog or cfg.catalog,
        database=database or cfg.database,
    )
