"""训练域（training_）：训练任务/指标/模型版本。

闭环第 5 环。上游承接数据资产域的数据集版本（dataset_id + dataset_version），
下游把 model_version 交给评测域与部署域——model_version 是训练域向外输出的
唯一契约字段，跨域同名同义。

三级 ID 在本域的落法：
  · data_id      训练是「数据集级」而非 clip 级作业，两张 DWD 表都不直接持有 data_id；
                 回溯路径是 dataset_id + dataset_version → dwd_dataset_data_relation
                 → data_id。这是原文「DWD 以 data_id 串联」在集合型作业上的正确展开，
                 硬塞一个恒为 NULL 的 data_id 反而会污染主键查询。
  · run_id       一次训练执行 = 一次处理运行（stage=train），挂在 dwd_training_task_detail。
  · artifact_id  不适用：artifact_id 格式以 data_id 打头，描述的是 clip 衍生产物；
                 模型文件的身份由 model_version 承载。
"""

from __future__ import annotations

from ...domains import DataDomain, Layer
from ..spec import Column as C
from ..spec import TableSpec

D = DataDomain.TRAINING

TABLES: list[TableSpec] = [
    TableSpec(
        name="ods_training_task",
        layer=Layer.ODS,
        domain=D,
        comment="训练任务",
        source_system="训练平台 MySQL",
        bucket=4,
        primary_key=("training_task_id",),
        notes="业务主键优先：直接用训练平台的任务 ID，不引入自增代理键",
        columns=[
            C("training_task_id", "STRING", "训练任务 ID（训练平台主键）", nullable=False),
            C("task_name", "STRING", "任务名称"),
            C("project_code", "STRING", "所属项目"),
            C("dataset_id", "STRING", "训练数据集 ID"),
            C("dataset_version", "STRING", "训练数据集版本"),
            C("base_model_version", "STRING", "基线模型版本（增量训练的起点）"),
            C("model_version", "STRING", "产出模型版本"),
            C("model_type", "STRING", "模型类型：perception/prediction/planning"),
            C("train_framework", "STRING", "训练框架：pytorch/tensorflow"),
            C("gpu_type", "STRING", "GPU 型号"),
            C("gpu_card_num", "INT", "GPU 卡数"),
            C("epoch_num", "INT", "训练轮数"),
            C("batch_size", "INT", "批大小"),
            C("learning_rate", "DOUBLE", "初始学习率"),
            C("hyper_param_json", "STRING", "超参快照（JSON，原样落地）"),
            C("task_status", "STRING", "任务状态：pending/running/success/failed/killed"),
            C("submit_time", "TIMESTAMP(3)", "提交时间"),
            C("start_time", "TIMESTAMP(3)", "开始时间"),
            C("end_time", "TIMESTAMP(3)", "结束时间"),
            C("submitter", "STRING", "提交人"),
        ],
    ),
    TableSpec(
        name="ods_training_metric",
        layer=Layer.ODS,
        domain=D,
        comment="训练指标（loss / mAP 等逐 step 上报）",
        source_system="训练平台 MySQL",
        bucket=4,
        primary_key=("training_task_id", "metric_name", "step_no"),
        notes="复合主键表达完整粒度：一个训练任务 × 一个指标 × 一个 step 一条记录",
        columns=[
            C("training_task_id", "STRING", "所属训练任务 ID", nullable=False),
            C(
                "metric_name",
                "STRING",
                "指标名称：loss/mAP/precision/recall/miss_rate",
                nullable=False,
            ),
            C("step_no", "BIGINT", "全局训练步数", nullable=False),
            C("epoch_no", "INT", "训练轮次"),
            C("metric_type", "STRING", "指标口径：train/val/test"),
            C("metric_value", "DOUBLE", "指标值"),
            C("metric_unit", "STRING", "指标单位（比率/绝对值）"),
            C("learning_rate", "DOUBLE", "该 step 的学习率"),
            C("gpu_util_pct", "DOUBLE", "GPU 利用率（%）"),
            C("gpu_mem_used_mb", "DOUBLE", "显存占用（MB）"),
            C("throughput_sample_per_sec", "DOUBLE", "吞吐（样本/秒）"),
            C("is_best", "BOOLEAN", "是否当前最优检查点"),
            C("log_time", "TIMESTAMP(3)", "指标上报时间"),
        ],
    ),
    TableSpec(
        name="ods_model_version",
        layer=Layer.ODS,
        name_omits_domain=True,
        domain=D,
        comment="模型版本",
        source_system="模型管理平台",
        bucket=4,
        primary_key=("model_version",),
        notes=(
            "表名不含数据域段（源文即如此）。主键取全局唯一的 model_version，"
            "让评测域/部署域按跨域公共键 model_version 做一次主键查询即可拿到模型身份"
        ),
        columns=[
            C(
                "model_version",
                "STRING",
                "模型版本号（全局唯一版本码），跨域公共键",
                nullable=False,
            ),
            C("model_name", "STRING", "模型名称"),
            C("model_type", "STRING", "模型类型：perception/prediction/planning"),
            C("model_arch", "STRING", "网络结构：BEVFormer/PointPillars 等"),
            C("base_model_version", "STRING", "父版本（迭代来源），用于版本树回溯"),
            C("training_task_id", "STRING", "产出该版本的训练任务 ID"),
            C("dataset_id", "STRING", "训练数据集 ID"),
            C("dataset_version", "STRING", "训练数据集版本"),
            C("framework", "STRING", "框架：pytorch/tensorflow"),
            C("quantization_type", "STRING", "量化方式：fp32/fp16/int8"),
            C("model_file_path", "STRING", "模型文件对象存储 key"),
            C("model_size_mb", "DOUBLE", "模型文件大小（MB）"),
            C("model_md5", "STRING", "模型文件校验和"),
            C("release_status", "STRING", "版本状态：draft/released/deprecated"),
            C("eval_pass_flag", "BOOLEAN", "是否通过评测准入"),
            C("publish_time", "TIMESTAMP(3)", "发布时间"),
            C("owner", "STRING", "版本负责人"),
        ],
    ),
    TableSpec(
        name="dwd_training_task_detail",
        layer=Layer.DWD,
        domain=D,
        comment="训练任务明细",
        bucket=4,
        primary_key=("training_task_id",),
        notes=(
            "训练是数据集级作业，无 clip 级 data_id；回溯 clip 走 "
            "dataset_id + dataset_version → dwd_dataset_data_relation → data_id。"
            "run_id 记录本次训练运行（stage=train），重跑产生新 run_id 但 training_task_id 不变。"
            "[a12] 第四章 Bucket 五档表点名本表作 4 档「中等体量 ODS/DWD」的代表表（与 "
            "ods_collect_task 并列）；PK(training_task_id) 是主键原则一「业务主键优先」的原文示例"
        ),
        columns=[
            C("training_task_id", "STRING", "训练任务 ID", nullable=False),
            C("run_id", "STRING", "三级 ID：本次训练运行（run_train_yyyyMMddHHmmss_seq）"),
            C("project_code", "STRING", "所属项目"),
            C("task_name", "STRING", "任务名称"),
            C("dataset_id", "STRING", "训练数据集 ID（下钻到 data_id 的入口）"),
            C("dataset_version", "STRING", "训练数据集版本"),
            C("sample_count", "BIGINT", "训练样本量（clip 数）"),
            C("base_model_version", "STRING", "基线模型版本"),
            C("model_version", "STRING", "产出模型版本"),
            C("model_type", "STRING", "模型类型：perception/prediction/planning"),
            C("gpu_type", "STRING", "GPU 型号"),
            C("gpu_card_num", "INT", "GPU 卡数"),
            C("epoch_num", "INT", "训练轮数"),
            C("queue_wait_min", "DOUBLE", "排队等待时长（分钟）"),
            C("train_duration_min", "DOUBLE", "训练耗时（分钟），闭环耗时的训练环节"),
            C("gpu_hours", "DOUBLE", "GPU 卡时消耗"),
            C("task_status", "STRING", "任务状态：success/failed/killed"),
            C("fail_reason", "STRING", "失败原因（标准化后）"),
            C("retry_count", "INT", "重试次数"),
            C("start_time", "TIMESTAMP(3)", "开始时间"),
            C("end_time", "TIMESTAMP(3)", "结束时间"),
        ],
    ),
    TableSpec(
        name="dwd_training_metric_detail",
        layer=Layer.DWD,
        domain=D,
        comment="训练指标明细",
        bucket=8,
        primary_key=("training_task_id", "metric_name", "step_no"),
        notes=(
            "大体量明细：任务数 × 指标数 × step 数，故 bucket=8。"
            "复合主键表达完整粒度；已冗余 model_version，便于按模型版本直接拉收敛曲线"
        ),
        columns=[
            C("training_task_id", "STRING", "训练任务 ID", nullable=False),
            C(
                "metric_name",
                "STRING",
                "指标名称：loss/mAP/precision/recall/miss_rate",
                nullable=False,
            ),
            C("step_no", "BIGINT", "全局训练步数", nullable=False),
            C("epoch_no", "INT", "训练轮次"),
            C("project_code", "STRING", "所属项目"),
            C("model_version", "STRING", "该任务产出的模型版本（冗余，免 JOIN）"),
            C("dataset_id", "STRING", "训练数据集 ID"),
            C("metric_type", "STRING", "指标口径：train/val/test"),
            C("metric_value", "DOUBLE", "指标值"),
            C("best_value", "DOUBLE", "截至当前 step 的历史最优值"),
            C("is_best", "BOOLEAN", "当前 step 是否刷新最优"),
            C("learning_rate", "DOUBLE", "该 step 的学习率"),
            C("gpu_util_pct", "DOUBLE", "GPU 利用率（%）"),
            C("throughput_sample_per_sec", "DOUBLE", "吞吐（样本/秒）"),
            C("converged_flag", "BOOLEAN", "是否已判定收敛"),
            C("train_elapsed_min", "DOUBLE", "距训练开始的已耗时（分钟）"),
            C("log_time", "TIMESTAMP(3)", "指标上报时间"),
        ],
    ),
    TableSpec(
        name="dws_training_efficiency_daily",
        layer=Layer.DWS,
        domain=D,
        comment="训练效率日指标",
        bucket=2,
        primary_key=("stat_date", "project_code", "model_type"),
        notes="口径固化：按「日期 × 项目 × 模型类型」预聚合，闭环效率表的训练环节直接取此表",
        columns=[
            C("stat_date", "DATE", "统计日期", nullable=False),
            C("project_code", "STRING", "项目代码", nullable=False),
            C("model_type", "STRING", "模型类型：perception/prediction/planning", nullable=False),
            C("train_task_cnt", "INT", "训练任务总数"),
            C("success_task_cnt", "INT", "成功任务数"),
            C("fail_task_cnt", "INT", "失败任务数"),
            C("retry_task_cnt", "INT", "发生重试的任务数"),
            C("task_success_rate", "DOUBLE", "任务成功率"),
            C("avg_queue_wait_min", "DOUBLE", "平均排队等待时长（分钟）"),
            C("avg_train_duration_min", "DOUBLE", "平均训练耗时（分钟）"),
            C("p90_train_duration_min", "DOUBLE", "训练耗时 P90（分钟）"),
            C("avg_epoch_num", "DOUBLE", "平均训练轮数"),
            C("total_sample_cnt", "BIGINT", "训练样本总量（clip 数）"),
            C("total_gpu_hours", "DOUBLE", "GPU 卡时消耗合计"),
            C("avg_gpu_util_pct", "DOUBLE", "平均 GPU 利用率（%）"),
            C("gpu_cost_amount", "DOUBLE", "算力成本（元）"),
            C("new_model_version_cnt", "INT", "当日新增模型版本数"),
            C("released_model_version_cnt", "INT", "当日通过评测准入并发布的版本数"),
        ],
    ),
    TableSpec(
        name="ads_model_version_comparison",
        layer=Layer.ADS,
        name_omits_domain=True,
        domain=D,
        comment="模型版本对比（服务评测平台 / 训练平台）",
        bucket=2,
        primary_key=(
            "model_version",
            "baseline_model_version",
            "dataset_id",
            "dataset_version",
            "scene_type",
        ),
        notes=(
            "表名不含数据域段（源文即如此）。零 JOIN：按「模型版本 × 评测数据集 × 场景类型」"
            "把新版本与基线的通过率/Badcase 率/平均指标分并排物化，"
            "regression_flag 为真即「带病上车」拦截点"
        ),
        columns=[
            C("model_version", "STRING", "待对比模型版本（新版本）", nullable=False),
            C("baseline_model_version", "STRING", "基线模型版本", nullable=False),
            C("dataset_id", "STRING", "评测数据集 ID", nullable=False),
            C("dataset_version", "STRING", "评测数据集版本", nullable=False),
            C("scene_type", "STRING", "场景类型：城区/高速/夜间/逆光…", nullable=False),
            C("project_code", "STRING", "所属项目"),
            C("model_type", "STRING", "模型类型：perception/prediction/planning"),
            C("evaluation_type", "STRING", "评测类型（跨域公共键）"),
            C("eval_case_cnt", "BIGINT", "参评用例数"),
            C("pass_rate", "DOUBLE", "新版本通过率"),
            C("baseline_pass_rate", "DOUBLE", "基线通过率"),
            C("pass_rate_diff_pp", "DOUBLE", "通过率差值（百分点，正为提升）"),
            C("badcase_cnt", "BIGINT", "新版本 Badcase 数"),
            C("badcase_rate", "DOUBLE", "新版本 Badcase 率"),
            C("baseline_badcase_rate", "DOUBLE", "基线 Badcase 率"),
            C("avg_metric_score", "DOUBLE", "新版本平均指标分"),
            C("baseline_avg_metric_score", "DOUBLE", "基线平均指标分"),
            C("regression_flag", "BOOLEAN", "是否回归项（该场景指标劣化）"),
            C("conclusion", "STRING", "对比结论：显著提升/持平/回归"),
            C("stat_date", "DATE", "统计日期（T+1 加工）"),
        ],
    ),
]
