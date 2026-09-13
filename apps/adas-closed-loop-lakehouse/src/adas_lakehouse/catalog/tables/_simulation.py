"""仿真域（simulation_）：仿真场景与运行结果。

闭环链路上的位置：模型训练产出版本后，先在仿真场景库里回归一轮，通过了才进实车
评测与 OTA。因此本域的两端都要「挂得住」——
  · 向上游挂 data_id：真实路采 clip 回灌成的场景，场景表保留来源 clip 的 data_id；
  · 向下游挂 model_version / dataset_id：仿真结果是模型版本对比与准出的依据。

表结构与注释风格对齐参考实现 tables/_collect.py。
"""

from __future__ import annotations

from ...domains import DataDomain, Layer
from ..spec import Column as C
from ..spec import TableSpec

D = DataDomain.SIMULATION

TABLES: list[TableSpec] = [
    TableSpec(
        name="ods_simulation_scenario",
        layer=Layer.ODS,
        domain=D,
        comment="仿真场景库（场景定义与来源）",
        source_system="仿真平台 MySQL",
        bucket=4,
        primary_key=("scenario_id",),
        notes=(
            "分区规则三：主键 Upsert 且无明确分区维度 → 不分区。"
            "场景库是维表量级（万级），CDC 从仿真平台 MySQL 同步，靠 bucket + 主键 Upsert 管理。"
            "scenario_source=real_clip 的场景带 data_id，是「仿真失败 → 回溯原始 clip」的第一跳"
        ),
        columns=[
            C("scenario_id", "STRING", "仿真场景 ID（仿真平台场景库主键）", nullable=False),
            C("scenario_name", "STRING", "场景名称"),
            C("scenario_type", "STRING", "场景类型：cut_in/aeb/加塞/路口左转/施工区绕行"),
            C(
                "scenario_source",
                "STRING",
                "场景来源：real_clip 真实回灌 / manual 人工构造 / mining 挖掘生成",
            ),
            C("data_id", "STRING", "来源 clip 的 data_id；仅 real_clip 回灌场景有值，关联采集域"),
            C("project_code", "STRING", "所属项目"),
            C("map_name", "STRING", "高精地图 / 路网名称"),
            C("road_type", "STRING", "道路类型：城市/高速/乡道/园区"),
            C("weather", "STRING", "天气：晴/雨/雪/雾"),
            C("light_condition", "STRING", "光照条件：白天/夜间/黄昏/隧道"),
            C("traffic_density", "STRING", "交通流密度：low/medium/high"),
            C("npc_count", "INT", "NPC 交通参与者数量"),
            C("ego_init_speed_kmh", "DOUBLE", "主车初始车速（km/h）"),
            C("duration_sec", "DOUBLE", "场景时长（秒）"),
            C("difficulty_level", "STRING", "难度等级：easy/normal/hard/corner"),
            C("scenario_version", "STRING", "场景版本号（场景本身也会迭代）"),
            C("scenario_file_key", "STRING", "OpenSCENARIO / OpenDRIVE 文件对象存储 key"),
            C(
                "scene_tag_list",
                "STRING",
                "场景标签列表（逗号分隔，口径对齐数据资产域 ods_scene_tag）",
            ),
            C("scenario_status", "STRING", "场景状态：draft/online/offline"),
            C("create_time", "TIMESTAMP(3)", "场景创建时间"),
        ],
    ),
    TableSpec(
        name="ods_simulation_result",
        layer=Layer.ODS,
        domain=D,
        comment="仿真运行结果（一个场景一次执行一条）",
        source_system="仿真平台 MySQL",
        bucket=4,
        primary_key=("simulation_run_id",),
        notes=(
            "分区规则三：主键 Upsert 且无明确分区维度 → 不分区。"
            "主键用源系统自己的 simulation_run_id（ODS 不改造、不丢失、可追溯，"
            "湖仓三级 run_id 的映射留到 DWD 层做）；metric_json 原样入湖，拆解同样留给 DWD"
        ),
        columns=[
            C("simulation_run_id", "STRING", "仿真运行 ID（仿真平台主键）", nullable=False),
            C("simulation_task_id", "STRING", "仿真任务 ID：一次回归批次下发 N 个场景"),
            C("scenario_id", "STRING", "被执行的仿真场景 ID"),
            C("project_code", "STRING", "所属项目"),
            C("model_version", "STRING", "被测模型版本"),
            C("sim_engine", "STRING", "仿真引擎：carla/lgsvl/自研闭环仿真"),
            C("sim_mode", "STRING", "仿真模式：open_loop 开环回灌 / closed_loop 闭环"),
            C("run_status", "STRING", "运行状态：success/failed/timeout"),
            C("pass_flag", "STRING", "准出判定：pass/fail"),
            C("collision_flag", "BOOLEAN", "是否发生碰撞"),
            C("takeover_count", "INT", "虚拟接管次数"),
            C("min_ttc_sec", "DOUBLE", "最小碰撞时间 TTC（秒）"),
            C("max_lateral_deviation_m", "DOUBLE", "最大横向偏差（米）"),
            C("score", "DOUBLE", "综合评分"),
            C("start_time", "TIMESTAMP(3)", "仿真开始时间"),
            C("end_time", "TIMESTAMP(3)", "仿真结束时间"),
            C("duration_sec", "DOUBLE", "运行耗时（秒）"),
            C("log_object_key", "STRING", "仿真日志 / 回放包对象存储 key"),
            C("metric_json", "STRING", "平台原始指标（JSON，ODS 层不拆解）"),
            C("executor", "STRING", "提交人"),
        ],
    ),
    TableSpec(
        name="dwd_simulation_result_detail",
        layer=Layer.DWD,
        domain=D,
        comment="仿真结果明细（run 级，串联 data_id / model_version）",
        bucket=8,
        primary_key=("run_id",),
        notes=(
            "分区规则三：主键 Upsert 且无明确分区维度 → 不分区。"
            "bucket=8：模型版本回归一次跑上万场景，属大体量 DWD 明细。"
            "粒度 = 一次仿真运行（一个场景一次执行），主键用三级 ID run_id；"
            "data_id 作为关联键冗余落表，让「仿真失败 → 原始采集 clip」保持一次主键查询。"
            "重刷（同场景换算法版本重跑）不覆盖：生成新 artifact_id，旧行标 superseded"
        ),
        columns=[
            C(
                "run_id",
                "STRING",
                "三级 ID：一次仿真运行，run_{stage}_{yyyyMMddHHmmss}_{seq}",
                nullable=False,
            ),
            C("data_id", "STRING", "一级 ID：回灌场景对应的原始 clip，Badcase 回溯的关联键"),
            C("artifact_id", "STRING", "二级 ID：本次仿真产出的结果产物"),
            C(
                "parent_artifact_id",
                "STRING",
                "血缘父产物（被测模型 / 回灌输入产物），图库对账兜底",
            ),
            C("artifact_status", "STRING", "产物状态：active/superseded/invalid"),
            C("scenario_id", "STRING", "仿真场景 ID（关联 ods_simulation_scenario）"),
            C("simulation_run_id", "STRING", "源系统运行 ID，冗余保留用于与仿真平台对账"),
            C("project_code", "STRING", "所属项目"),
            C("model_version", "STRING", "被测模型版本（关联训练域）"),
            C("dataset_id", "STRING", "回归所用数据集 ID"),
            C("dataset_version", "STRING", "回归所用数据集版本"),
            C("sim_mode", "STRING", "仿真模式：open_loop 开环回灌 / closed_loop 闭环"),
            C("sim_start_time", "TIMESTAMP(3)", "仿真开始时间"),
            C("sim_end_time", "TIMESTAMP(3)", "仿真结束时间"),
            C("duration_sec", "DOUBLE", "仿真耗时（秒），闭环效率指标输入"),
            C("pass_flag", "STRING", "准出判定：pass/fail"),
            C(
                "failure_reason",
                "STRING",
                "失败原因分类：collision/timeout/lane_departure/takeover",
            ),
            C("collision_flag", "BOOLEAN", "是否发生碰撞"),
            C("min_ttc_sec", "DOUBLE", "最小碰撞时间 TTC（秒）"),
            C("score", "DOUBLE", "综合评分"),
        ],
    ),
]
