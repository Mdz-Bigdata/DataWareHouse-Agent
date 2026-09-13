"""数据面资产清单：主数据到底放在哪里，以及五条对齐约定的可执行版本。

原文第二章的第一设计原则是「平台不持有主数据」，围绕它给了五条对齐约定
（:data:`~adas_lakehouse.controlplane.constants.ALIGNMENT_PRINCIPLES`）。本模块把
其中与「资产归属」相关的几条变成代码：

  · **湖仓单一事实源**：抽帧元信息、标签、向量统一落 Paimon，平台自身仅存任务配置
    与运行态 → :data:`DATA_PLANE_TABLES` 与 :func:`assert_lakehouse_owned`；
  · **全局 ID 贯穿**：采集最小单元为 clip，统一用全局 data_id；image_id 内嵌 data_id，
    天然可回溯 → :func:`image_id_for` / :func:`data_id_of_image`；
  · **数仓命名规范**：新增表遵循 ``{层级}_{挖掘域}_{实体}_detail``，共 11 张表
    （1 ODS + 8 DWD + 1 DWS + 1 ADS）→ :func:`mining_table_budget`；
  · 一个容易被忽略的细节：**clip 元数据不新建表**——平台直接复用采集域既有的
    ``dwd_collect_clip_detail``，而不是复制一份自己的 → :data:`REUSED_TABLES`。

本模块**不定义 TableSpec**。挖掘域 11 张表的规格归 ``catalog/tables/`` 下的挖掘域
模块所有，这里只登记「谁是主数据、落在哪张表」，避免两处定义打架。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..controlplane import constants as K
from ..controlplane.contracts import DATA_PLANE_ASSETS, Plane

__all__ = [
    "DataPlaneTable",
    "DATA_PLANE_TABLES",
    "REUSED_TABLES",
    "LakehouseOwnershipError",
    "assert_lakehouse_owned",
    "mining_table_budget",
    "image_id_for",
    "data_id_of_image",
    "alignment_principles",
    "asset_inventory",
]


class LakehouseOwnershipError(RuntimeError):
    """资产归属错误：有人试图让控制面持有主数据，或让数据面存运行态。"""


@dataclass(frozen=True, slots=True)
class DataPlaneTable:
    """一张数据面表的登记项。

    :param name: 表名（湖仓四段式）
    :param asset: 它承载的主数据类别，取值见
        :data:`~adas_lakehouse.controlplane.contracts.DATA_PLANE_ASSETS`
    :param owner_module: 谁定义它的 TableSpec（本模块不定义）
    :param reused: 是否复用既有表而非新建
    """

    name: str
    asset: str
    purpose: str
    owner_module: str
    reused: bool = False


#: 原文点名过的数据面表。其余 7 张挖掘域 DWD 表由挖掘域表模块登记，这里不猜表名。
DATA_PLANE_TABLES: tuple[DataPlaneTable, ...] = (
    DataPlaneTable(
        name=K.TABLE_CLIP_DETAIL,
        asset="clip",
        purpose="采集数据单元元信息，血缘起点，data_id 的落点",
        owner_module="catalog.tables._collect",
        reused=True,  # 原文：clip 元数据不新建表
    ),
    DataPlaneTable(
        name=K.TABLE_IMAGE_FRAME_DETAIL,
        asset="image",
        purpose="抽帧结果；规则挖掘与 VLM 推理各自基于该表独立运行（引擎解耦点）",
        owner_module="catalog.tables._mining",
    ),
    DataPlaneTable(
        name=K.TABLE_IMAGE_VECTOR_DETAIL,
        asset="vector",
        purpose=f"T+{K.VECTOR_PIPELINE_LAG_DAYS} 向量化产物，支撑以文搜图 / 以图搜图",
        owner_module="catalog.tables._mining",
    ),
    DataPlaneTable(
        name=K.TABLE_RULE_CONFIG,
        asset="tag",
        purpose="控制面规则配置经 Flink CDC 回流的落点（成因入湖，血缘不断链）",
        owner_module="catalog.tables._mining",
    ),
    DataPlaneTable(
        name=K.TABLE_TASK_DETAIL,
        asset="tag",
        purpose="控制面任务与审核动作定期回写的落点（每一步操作都进血缘）",
        owner_module="catalog.tables._mining",
    ),
)

#: 复用而非新建的表。原文第二章末段点名的那个「容易被忽略的细节」。
REUSED_TABLES: tuple[str, ...] = tuple(t.name for t in DATA_PLANE_TABLES if t.reused)


def assert_lakehouse_owned(asset: str) -> None:
    """断言某类资产归数据面（湖仓）所有。

    :raises LakehouseOwnershipError: 该资产不在数据面资产清单里
    """
    from ..controlplane.contracts import asset_plane

    try:
        plane = asset_plane(asset)
    except KeyError as exc:
        raise LakehouseOwnershipError(str(exc)) from exc
    if plane is not Plane.DATA:
        raise LakehouseOwnershipError(
            f"资产 {asset!r} 归 {plane.value} 面所有；主数据（"
            f"{' / '.join(K.DATA_PLANE_MASTER_DATA)}）必须全部在 Paimon"
        )


def mining_table_budget() -> dict[str, int]:
    """挖掘域新增表的分层预算（原文第二章表格逐字：共 11 张，1 ODS + 8 DWD + 1 DWS + 1 ADS）。

    供挖掘域表模块与 ``catalog.registry`` 对账用——本模块只给数，不给规格。
    """
    budget = {
        "total": K.MINING_TABLE_COUNT,
        "ods": K.MINING_ODS_TABLE_COUNT,
        "dwd": K.MINING_DWD_TABLE_COUNT,
        "dws": K.MINING_DWS_TABLE_COUNT,
        "ads": K.MINING_ADS_TABLE_COUNT,
    }
    assert budget["ods"] + budget["dwd"] + budget["dws"] + budget["ads"] == budget["total"]
    return budget


# --------------------------------------------------------------------------- 全局 ID 贯穿


def image_id_for(data_id: str, frame_index: int) -> str:
    """由 clip 的 data_id 生成 image_id。

    原文第二章对齐约定二逐字：「采集最小单元为 clip，统一用全局 data_id；
    **image_id 内嵌 data_id**，天然可回溯」。

    ⚠️ 原文未明确，本项目设计：内嵌的具体格式原文没给，这里取
    ``{data_id}_f{帧序号:06d}``——前缀仍是完整 data_id，因此
    :func:`data_id_of_image` 可以无损还原，满足「天然可回溯」。

    :param data_id: 所属 clip 的 data_id
    :param frame_index: 帧序号（从 0 开始）
    :raises ValueError: data_id 不合法或帧序号为负
    """
    from ..ids import parse_data_id

    parse_data_id(data_id)  # 早失败：image_id 必须挂在合法锚点上
    if frame_index < 0:
        raise ValueError(f"帧序号不能为负：{frame_index}")
    return f"{data_id}_f{frame_index:06d}"


def data_id_of_image(image_id: str) -> str:
    """从 image_id 还原 data_id——「天然可回溯」的反向操作。

    :raises ValueError: image_id 不是本项目格式
    """
    from ..ids import parse_data_id

    marker = image_id.rfind("_f")
    if marker < 0:
        raise ValueError(f"不是合法的 image_id: {image_id!r}")
    data_id = image_id[:marker]
    parse_data_id(data_id)
    return data_id


# --------------------------------------------------------------------------- 对齐约定


def alignment_principles() -> list[dict[str, str]]:
    """五条对齐约定（原文第二章表格，逐字）。"""
    return [{"principle": name, "implementation": how} for name, how in K.ALIGNMENT_PRINCIPLES]


def asset_inventory() -> dict[str, Any]:
    """完整资产盘点，供运维面板与架构自检。"""
    return {
        "first_principle": K.FIRST_PRINCIPLE,
        "master_data": list(K.DATA_PLANE_MASTER_DATA),
        "data_plane_assets": dict(DATA_PLANE_ASSETS),
        "tables": [
            {
                "name": t.name,
                "asset": t.asset,
                "purpose": t.purpose,
                "owner_module": t.owner_module,
                "reused": t.reused,
            }
            for t in DATA_PLANE_TABLES
        ],
        "reused_tables": list(REUSED_TABLES),
        "mining_table_budget": mining_table_budget(),
        "alignment_principles": alignment_principles(),
    }
