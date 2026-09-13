"""地理网格与热力等级：热力图表（表 9）的口径唯一出处。

来源 [S1-05] 第五章表 9：「按「统计日期 × 车型 × 触发类型 × 地理网格」统计触发总量、
上传/处理完成率与入数据集量。热力图显示触发集中在城区晚高峰路口；某区域「AEB 误触发」
周环比上升 45% → 问题分析平台关联该区域回传数据定位到逆光路口场景……」

热力图只要两个口径就能成立，本模块把这两个口径各实现一次，供两处共用：

  ① **网格划分**：经纬度 → 网格键 ``geo_grid_id``。
     catalog 把该列注释为 GeoHash，但上游 ``dwd_vehicle_trigger_detail`` 只落了
     ``gps_lat`` / ``gps_lon``，没有 GeoHash 编码列（见本项目 docs/source-deviations）。
     本项目先按 :data:`constants.GEO_GRID_PRECISION_DEGREES`（0.01 度，纬向约 1.1 km）
     取整成网格键——够表达「城区某个路口」这一粒度；接真实 GeoHash 时只改本模块。
  ② **热力等级**：网格内触发次数 → ``heat_level`` 1~5，前端直接取色、零计算。

两处共用是本模块存在的理由：同一份定义既渲染进 Flink 批作业的 SQL
（:func:`grid_id_sql` / :func:`heat_level_sql`，见 ``materialize`` 表 9 的加工计划），
又在服务层做内存聚合（:func:`grid_id` / :func:`heat_level`，见
``services.TriggerMiningClosedLoopService``）。批作业算出来的等级与服务层
重算出来的等级永远同档，不会出现「大屏取色和明细对不上」。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .constants import (
    GEO_GRID_ID_DECIMALS,
    GEO_GRID_ID_SEPARATOR,
    GEO_GRID_PRECISION_DEGREES,
    GEO_LAT_RANGE,
    GEO_LON_RANGE,
    TRIGGER_HEAT_LEVEL_MAX,
    TRIGGER_HEAT_LEVEL_MIN,
    TRIGGER_HEAT_LEVEL_THRESHOLDS,
)

__all__ = [
    "GeoGridCell",
    "grid_id",
    "grid_center",
    "parse_grid_id",
    "grid_id_sql",
    "heat_level",
    "heat_level_sql",
    "GRID_SIZE_DEGREES",
]

#: 网格边长（度）。别名，读代码时比常量名更直观。
GRID_SIZE_DEGREES: Final[float] = GEO_GRID_PRECISION_DEGREES


def _snap(value: float) -> float:
    """把一个坐标吸附到网格中心所在的刻度上。

    用「除以精度 → 四舍五入 → 乘回精度」而不是 :func:`round(value, 2)`，
    这样精度换成 0.05 / 0.25 这类非十进制刻度时公式依然成立。
    """
    return round(value / GEO_GRID_PRECISION_DEGREES) * GEO_GRID_PRECISION_DEGREES


def _fmt(value: float) -> str:
    """格式化成固定小数位，并抹掉 ``-0.00`` 这种负零写法。

    固定小数位是网格键可比性的前提：``31.2`` 与 ``31.20`` 必须是同一个网格。
    Flink 侧用 ``CAST(... AS DECIMAL(p, 2))`` 得到同样的定标字符串
    （见 :func:`grid_id_sql`），两边编码逐字一致。
    """
    text = f"{value:.{GEO_GRID_ID_DECIMALS}f}"
    return text[1:] if text.startswith("-") and float(text) == 0.0 else text


def grid_id(lat: float, lon: float) -> str:
    """经纬度 → 网格键（``ads_trigger_heatmap.geo_grid_id``）。

    Args:
        lat: 纬度，WGS-84。
        lon: 经度，WGS-84。

    Returns:
        形如 ``"31.23_121.47"`` 的网格键：两个坐标各自吸附到
        :data:`GRID_SIZE_DEGREES` 刻度后，按固定小数位拼接。

    Raises:
        ValueError: 经纬度越界（脏 GPS 不进热力图，宁可报错也不落一个假热点）。

    Examples:
        >>> grid_id(31.2345, 121.4678)
        '31.23_121.47'
        >>> grid_id(31.2301, 121.4701) == grid_id(31.2345, 121.4678)
        True
    """
    lat_min, lat_max = GEO_LAT_RANGE
    lon_min, lon_max = GEO_LON_RANGE
    if not lat_min <= lat <= lat_max:
        raise ValueError(f"纬度 {lat!r} 越界，合法区间 [{lat_min}, {lat_max}]")
    if not lon_min <= lon <= lon_max:
        raise ValueError(f"经度 {lon!r} 越界，合法区间 [{lon_min}, {lon_max}]")
    return f"{_fmt(_snap(lat))}{GEO_GRID_ID_SEPARATOR}{_fmt(_snap(lon))}"


def parse_grid_id(value: str) -> tuple[float, float]:
    """网格键 → (纬度, 经度)。:func:`grid_id` 的逆运算。

    Raises:
        ValueError: 不是本模块编码出来的网格键。
    """
    parts = value.split(GEO_GRID_ID_SEPARATOR)
    if len(parts) != 2:
        raise ValueError(
            f"{value!r} 不是网格键：应形如 "
            f"'31.23{GEO_GRID_ID_SEPARATOR}121.47'（纬度{GEO_GRID_ID_SEPARATOR}经度）"
        )
    try:
        lat, lon = float(parts[0]), float(parts[1])
    except ValueError:
        raise ValueError(f"{value!r} 不是网格键：经纬度段不是数字") from None
    return lat, lon


@dataclass(frozen=True, slots=True)
class GeoGridCell:
    """一个地理网格：键 + 中心点 + 边长。"""

    grid_id: str
    center_lat: float
    center_lon: float
    size_degrees: float = GRID_SIZE_DEGREES

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """(南, 西, 北, 东) 边界——前端画网格矩形用。"""
        half = self.size_degrees / 2.0
        return (
            self.center_lat - half,
            self.center_lon - half,
            self.center_lat + half,
            self.center_lon + half,
        )

    def contains(self, lat: float, lon: float) -> bool:
        """该坐标是否落在本网格内（按同一套编码判定，不用浮点边界比较）。"""
        return grid_id(lat, lon) == self.grid_id


def grid_center(value: str) -> GeoGridCell:
    """网格键 → 网格对象（含中心点与边界）。

    Raises:
        ValueError: 不是本模块编码出来的网格键。
    """
    lat, lon = parse_grid_id(value)
    return GeoGridCell(grid_id=value, center_lat=lat, center_lon=lon)


def grid_id_sql(lat_expr: str, lon_expr: str) -> str:
    """渲染出与 :func:`grid_id` 同口径的 Flink SQL 表达式。

    Args:
        lat_expr: 纬度列表达式，如 ``t.gps_lat``。
        lon_expr: 经度列表达式，如 ``t.gps_lon``。

    Returns:
        一段 SQL：先按网格精度吸附，再 CAST 成定标 DECIMAL 转字符串拼接。
        定标 DECIMAL 这一步不能省——``CAST(31.2 AS STRING)`` 会得到 ``'31.2'``，
        与 Python 侧的 ``'31.20'`` 对不上，同一个网格就会裂成两行。
    """
    step = GEO_GRID_PRECISION_DEGREES
    d = GEO_GRID_ID_DECIMALS

    def _snap_sql(expr: str, precision: int) -> str:
        # ROUND(expr / step) * step 与 Python 的 _snap 同式；DECIMAL(p, d) 定标输出
        return f"CAST(CAST(ROUND({expr} / {step}) * {step} AS DECIMAL({precision}, {d})) AS STRING)"

    # 纬度 ±90、经度 ±180：整数位分别最多 2 位与 3 位，加上小数位即总精度
    return (
        f"CONCAT({_snap_sql(lat_expr, 2 + d)}, "
        f"'{GEO_GRID_ID_SEPARATOR}', {_snap_sql(lon_expr, 3 + d)})"
    )


def heat_level(trigger_count: int) -> int:
    """网格内触发次数 → 热力等级 1~5。

    分档阈值见 :data:`constants.TRIGGER_HEAT_LEVEL_THRESHOLDS`
    （⚠️ 原文只说热力等级 1~5，阈值是本项目设计）。

    Args:
        trigger_count: 网格内触发次数，非负。

    Returns:
        1~:data:`constants.TRIGGER_HEAT_LEVEL_MAX` 的整数。

    Raises:
        ValueError: 触发次数为负——负数说明上游聚合错了，不能默默取最低档。

    Examples:
        >>> [heat_level(n) for n in (0, 4, 5, 19, 20, 49, 50, 99, 100, 10_000)]
        [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]
    """
    if trigger_count < 0:
        raise ValueError(f"触发次数不能为负，收到 {trigger_count!r}")
    level = TRIGGER_HEAT_LEVEL_MIN + sum(
        1 for threshold in TRIGGER_HEAT_LEVEL_THRESHOLDS if trigger_count >= threshold
    )
    return min(level, TRIGGER_HEAT_LEVEL_MAX)


def heat_level_sql(count_expr: str) -> str:
    """渲染出与 :func:`heat_level` 同口径的 Flink SQL CASE 表达式。

    Args:
        count_expr: 触发次数列表达式，如 ``g.trigger_cnt``。
    """
    lines = ["CASE"]
    # 从高档往低档写，先命中先返回——与 Python 侧「数有几个阈值被跨过」等价
    for offset, threshold in enumerate(reversed(TRIGGER_HEAT_LEVEL_THRESHOLDS)):
        level = min(
            TRIGGER_HEAT_LEVEL_MIN + len(TRIGGER_HEAT_LEVEL_THRESHOLDS) - offset,
            TRIGGER_HEAT_LEVEL_MAX,
        )
        lines.append(f"       WHEN {count_expr} >= {threshold} THEN {level}")
    lines.append(f"       ELSE {TRIGGER_HEAT_LEVEL_MIN} END")
    return "\n".join(lines)
