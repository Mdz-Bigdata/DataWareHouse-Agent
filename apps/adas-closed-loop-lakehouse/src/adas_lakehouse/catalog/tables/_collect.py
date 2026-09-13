"""采集域（collect_）：采集任务/车辆/传感器/clip。

参考实现——其余数据域的表文件照此结构编写。
"""

from __future__ import annotations

from ...domains import DataDomain, Layer
from ..spec import Column as C
from ..spec import TableSpec

D = DataDomain.COLLECT

TABLES: list[TableSpec] = [
    TableSpec(
        name="ods_collect_task",
        layer=Layer.ODS,
        domain=D,
        comment="采集任务",
        source_system="采集管理系统",
        bucket=4,
        primary_key=("collect_task_id",),
        notes=(
            "[a12] 第四章 Bucket 五档表点名本表作 4 档「中等体量 ODS/DWD」的代表表"
            "（与 dwd_training_task_detail 并列）；"
            "[a10] 第五章①以本表举「业务主键 + NOT ENFORCED」——PK 取 collect_task_id，"
            "Paimon 不在写入时强制校验唯一性（由上游保证），但按主键做 Upsert 合并"
        ),
        columns=[
            C("collect_task_id", "STRING", "采集任务 ID", nullable=False),
            C("task_name", "STRING", "任务名称"),
            C("project_code", "STRING", "所属项目"),
            C("vehicle_code", "STRING", "执行车辆编码"),
            C("target_scene", "STRING", "目标采集场景"),
            C("planned_duration_min", "INT", "计划采集时长（分钟）"),
            C("actual_clip_count", "INT", "实际产出 clip 数"),
            C("task_status", "STRING", "任务状态"),
            C("start_time", "TIMESTAMP(3)", "开始时间"),
            C("end_time", "TIMESTAMP(3)", "结束时间"),
        ],
    ),
    TableSpec(
        name="ods_vehicle_info",
        layer=Layer.ODS,
        name_omits_domain=True,
        domain=D,
        comment="车辆基础信息",
        source_system="车辆管理系统",
        bucket=4,
        primary_key=("vehicle_code",),
        columns=[
            C("vehicle_code", "STRING", "车辆编码，data_id 第二段来源", nullable=False),
            C("vin", "STRING", "车架号"),
            C("vehicle_model", "STRING", "车型"),
            C("fleet_name", "STRING", "所属车队"),
            C("autonomy_level", "STRING", "智驾等级"),
            C("register_date", "DATE", "入队日期"),
            C("status", "STRING", "车辆状态"),
        ],
    ),
    TableSpec(
        name="ods_sensor_config",
        layer=Layer.ODS,
        name_omits_domain=True,
        domain=D,
        comment="传感器配置",
        source_system="配置管理系统",
        bucket=4,
        primary_key=("vehicle_code", "sensor_id"),
        notes="复合主键表达完整粒度：同一车辆挂载多个传感器",
        columns=[
            C("vehicle_code", "STRING", "车辆编码", nullable=False),
            C("sensor_id", "STRING", "传感器 ID", nullable=False),
            C("sensor_type", "STRING", "类型：camera/lidar/radar/imu/gps"),
            C("sensor_model", "STRING", "型号"),
            C("mount_position", "STRING", "安装位置"),
            C("intrinsic_json", "STRING", "内参（JSON）"),
            C("extrinsic_json", "STRING", "外参（JSON）"),
            C("sample_rate_hz", "DOUBLE", "采样率"),
            C("effective_from", "TIMESTAMP(3)", "生效时间"),
        ],
    ),
    TableSpec(
        name="ods_data_file_meta",
        layer=Layer.ODS,
        name_omits_domain=True,
        domain=D,
        comment="采集文件元信息",
        source_system="文件管理系统",
        bucket=4,
        partition_by=("file_type",),
        primary_key=("file_id", "file_type"),
        notes="分区规则二：有明确业务分类过滤 → 按业务字段分区；文件类型体量差异大",
        columns=[
            C("file_id", "STRING", "文件 ID", nullable=False),
            C(
                "file_type",
                "STRING",
                "文件类型：video/pointcloud/radar/imu/gps/can",
                nullable=False,
            ),
            C("data_id", "STRING", "所属 clip 的 data_id"),
            C("object_key", "STRING", "对象存储 key"),
            C("file_size_bytes", "BIGINT", "文件大小"),
            C("checksum_md5", "STRING", "文件校验和"),
            C("sensor_id", "STRING", "产出传感器"),
            C("duration_sec", "DOUBLE", "时长（秒）"),
            # ---- 入湖门禁判据（系列二第 6 篇《数据质量门禁设计》4.3 OSS 采集文件 · 文件查「物理完整」）----
            # 分区字段仍是 file_type、主键仍是 (file_id, file_type)——下面全是追加的普通列，
            # 不参与分区也不进主键，不动原有物理策略。
            C(
                "frame_group_modalities",
                "STRING",
                "本帧组已到齐的模态清单（逗号分隔，字典 camera/lidar/radar/imu/gnss）。"
                "对应 QG-OSS-002 多模态完整性：同一帧组内相机/激光雷达/毫米波/IMU/GNSS 文件齐全，"
                "缺帧 P0 拒绝入湖（L1）",
            ),
            C(
                "continuous_frame_loss_rate",
                "DOUBLE",
                "连续丢帧率（0~1）。对应 QG-OSS-003：连续丢帧率 > 1% 升级告警"
                "（阈值见 thresholds.CONTINUOUS_FRAME_LOSS_RATE_ESCALATE_THRESHOLD）",
            ),
            C(
                "vehicle_type",
                "STRING",
                "车辆类型：collect 采集车 / production 量产车。"
                "QG-OSS-004、QG-OSS-005 的 when 前置条件字段——两档同步容差按车型分流，"
                "该列缺失会让 ±10ms / ±50ms 两条规则一起被跳过",
            ),
            C(
                "time_sync_error_ms",
                "INT",
                "多传感器时间同步误差（毫秒，带符号，取绝对值判定）。对应 QG-OSS-004 / QG-OSS-005 "
                "时间同步：采集车硬件同步 ≤ ±10ms、量产车软同步 ≤ ±50ms，超限告警放行（L1）",
            ),
            C(
                "near_duplicate_similarity",
                "DOUBLE",
                "与已入湖数据的最高相似度（0~1，由上游算好，门禁只做阈值判定）。"
                "对应 QG-OSS-011 近重复：超 0.98 标记抑制（L2 近重复抑制 / L5 近重复）。"
                "⚠️ 原文未明确，本项目设计：原文只说「近重复抑制」，既没说用什么度量、"
                "也没给判定口径；本项目落成一个 0~1 的相似度标量列，由上游算好后门禁只比阈值",
            ),
            C(
                "batch_missing_ratio",
                "DOUBLE",
                "所属批次的缺片比例（0~1，批级统计量）。对应 QG-OSS-012 批次到达完整性："
                "六维之及时性，批次轻微缺片 P3 观察告警。"
                "⚠️ 原文未明确，本项目设计：原文只说「批次到达完整性」，未给统计量形式；"
                "本项目落成批级缺片比例。注意它是「应到 vs 实到」的差，光看收到的记录推不出来，"
                "必须由上游随批次喂进 check_batch(batch_stats=...)",
            ),
            # 下面三列是规则通过 params 引用的判据列（不是 RuleSpec.field），
            # 并列阶段按 field 并列时漏掉了——列不存在即规则恒取 NULL、静默不生效。
            C(
                "vehicle_desensitized_flag",
                "BOOLEAN",
                "车端脱敏标记：人脸/车牌等敏感信息在车端已脱敏。"
                "QG-OSS-001 脱敏标记合规的双标记之一，缺失或未置位即 P0 合规风险拒绝入湖",
            ),
            C(
                "cloud_compliance_decrypted_flag",
                "BOOLEAN",
                "合规云脱密标记：数据已在合规云完成脱密处理。"
                "QG-OSS-001 脱敏标记合规的双标记之二，与车端脱敏标记必须同时为真",
            ),
            C(
                "decodable_flag",
                "BOOLEAN",
                "文件可解码标记（上游解码探针的结论，随元信息落表）。"
                "QG-OSS-006 文件损坏检查在没有注入 file_probe 探针、也没有 checksum 比对时"
                "退化为读这一列；该列缺失会让「压缩损伤 / 传输截断」这条 P1 规则失去兜底判据",
            ),
        ],
    ),
    TableSpec(
        name="dwd_collect_clip_detail",
        layer=Layer.DWD,
        domain=D,
        comment="采集数据单元元信息（clip 级，血缘起点）",
        bucket=16,
        primary_key=("data_id",),
        notes="血缘起点。一个 clip ≈ 1 分钟连续采集片段，对应一个 data_id，终身不变",
        columns=[
            C("data_id", "STRING", "一级 ID：clip 级终身锚点", nullable=False),
            # 血缘对账的被引用端：QG-COM-007 拿任意表的 parent_artifact_id 回查本列，
            # 本列缺失会让「血缘缺失告警」（L5 门禁动作）永远查不到目标而静默失效。
            C(
                "artifact_id",
                "STRING",
                "二级 ID：本 clip 的处理产物 ID，血缘链路的被引用端——"
                "下游表的 parent_artifact_id 指向它，QG-COM-007 据此做图库对账兜底",
            ),
            C("collect_task_id", "STRING", "采集任务 ID"),
            C("vehicle_code", "STRING", "车辆编码"),
            C("project_code", "STRING", "所属项目"),
            C("collect_start_time", "TIMESTAMP(3)", "采集开始时间"),
            C("collect_end_time", "TIMESTAMP(3)", "采集结束时间"),
            C("duration_sec", "DOUBLE", "片段时长（秒）"),
            C("gps_start_lat", "DOUBLE", "起点纬度"),
            C("gps_start_lon", "DOUBLE", "起点经度"),
            C("road_type", "STRING", "道路类型"),
            C("weather", "STRING", "天气"),
            C("light_condition", "STRING", "光照条件"),
            C("sensor_count", "INT", "传感器数量"),
            C("total_size_bytes", "BIGINT", "原始数据总量"),
            C("upload_status", "STRING", "上云状态"),
            C("compliance_status", "STRING", "合规状态：见 ingest.compliance"),
        ],
    ),
]
