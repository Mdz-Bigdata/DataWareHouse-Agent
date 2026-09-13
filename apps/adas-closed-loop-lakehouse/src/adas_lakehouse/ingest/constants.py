"""入湖子系统的原文数字常量表：所有阈值 / 比例 / 参数 / TTL / 耗时 / 成本逐字登记。

出处标记约定：
  [a8]  系列二第 8 篇（收官）《采集数据的合规入湖链路》 2026-09-07
        https://mp.weixin.qq.com/s/qhddTZf_P_g81s5z1RkPPA
  [a5]  全景综述特辑《智驾数据闭环的湖仓架构全景》 2026-09-08
        https://mp.weixin.qq.com/s/UmHoxjBwRtZT0PgwjkL9DQ
  [a6]  系列二第 6 篇《数据质量门禁设计：智驾数据入湖的五步校验链路》 2026-09-05
        https://mp.weixin.qq.com/s/e7lf3LrjX9JMHMvVnu4Anw
        —— [a8] 第六章「命中拒绝规则的数据进入**第六篇讲过的**五步异常闭环」，
        本子系统落地那条闭环时用到的数字在此登记。

本模块只登记数字与原文措辞，不含逻辑。凡标注「⚠️ 原文未明确，本项目设计：」的
常量，是原文没有给出、由本项目为了工程可落地而补的参数，不要当成原文口径引用。
"""

from __future__ import annotations

from typing import Final

# ===========================================================================
# 一、[a8] 采集数据的合规入湖链路
# ===========================================================================

#: [a8] 本篇在系列二中的序号：「系列二 · 湖仓实战 | 共 8 篇 | 本篇第 8 篇（收官）」
SERIES2_TOTAL_ARTICLES: Final[int] = 8
SERIES2_ARTICLE_INDEX: Final[int] = 8

#: [a8] 「一个采集项目动辄数十 TB」——采集通道的体量级别（原文为量级措辞，不是精确值）
COLLECT_PROJECT_VOLUME_TEXT: Final[str] = "数十 TB"
#: [a8] 「单帧点云几十 MB」
LIDAR_FRAME_SIZE_TEXT: Final[str] = "几十 MB"
#: [a8] 「一个采集任务数百个文件」
FILES_PER_COLLECT_TASK_TEXT: Final[str] = "数百个文件"

#: [a8] 「采集大文件的五步合规入湖链路」——车端脱敏 / 合规室上传 / 合规脱密 / 合规数据分发 / 实时入湖
COMPLIANCE_CHAIN_STEPS: Final[int] = 5
#: [a8] 「前三步是『合规处理段』」，也即「文件本体经三段合规链路上传至智驾云 OSS」
COMPLIANCE_PROCESSING_SEGMENT_STEPS: Final[int] = 3
#: [a8] 「后两步是『智驾云内的分发与入湖』」
ADAS_CLOUD_SEGMENT_STEPS: Final[int] = 2

#: [a8] 「五步链路里有两次脱敏，分工完全不同」——车端简单脱敏 + 合规云复杂脱敏
REDACTION_STAGE_COUNT: Final[int] = 2

#: [a8] 「本通道有四项专属检查——其中两项是 P0 级」
OSS_CHANNEL_GATE_CHECKS: Final[int] = 4
OSS_CHANNEL_P0_CHECKS: Final[int] = 2
#: [a8] 剩余两项为 P1 级（文件本体可解码 / 元信息与 OSS 路径一致）
OSS_CHANNEL_P1_CHECKS: Final[int] = 2

#: [a8] 「命中拒绝规则的数据进入第六篇讲过的五步异常闭环（拦截 → 隔离 → 告警 → 分流处置 → 复验）」
#: [a5] 第八章同样表述为「五步异常闭环」
ANOMALY_CLOSED_LOOP_STEPS: Final[int] = 5

#: [a8] 「三个字段值得注意」：data_id / checksum / storage_class
OSS_META_HIGHLIGHT_FIELDS: Final[tuple[str, str, str]] = ("data_id", "checksum", "storage_class")

#: [a8] 「storage_class 对接存储生命周期管理——标准 / 低频 / 归档的降冷策略，
#: 正是系列一收官篇讲过的五级分层在文件域的应用」
FILE_STORAGE_CLASSES: Final[tuple[str, str, str]] = ("标准", "低频", "归档")
STORAGE_TIER_COUNT: Final[int] = 5

#: [a8] 「三通道入湖架构」——CDC / Kafka / OSS 合规上传
INGEST_CHANNEL_COUNT: Final[int] = 3

#: [a8] 「采集数据无法走前两条通道，原因有三」：体量 / 敏感度 / 介质
OSS_CHANNEL_RATIONALE_COUNT: Final[int] = 3

#: [a8] 「数据到达智驾云后，入湖遵循三条原则」：文件本体存 OSS / 元信息经 Kafka 实时写入 / 元信息写入即可用
LAKE_ENTRY_PRINCIPLES: Final[int] = 3

#: [a8] 合规云的三条架构约束：独立云 / 同一云端 VPC / 不对外暴露
COMPLIANCE_CLOUD_CONSTRAINTS: Final[int] = 3

# ===========================================================================
# 二、[a5] 第六章「数据进得来：三通道统一入湖」
# ===========================================================================

#: [a5] 「8 类数据源形态各异」——产线 / 标注 / 质检 / 训练 / 评测 / 仿真 / 回传 / 挖掘
SOURCE_SYSTEM_CATEGORIES: Final[int] = 8
#: [a5] 「产线 / 标注 / 质检 / 训练 / 评测 / 仿真 / 回传 / 挖掘 8 类数据各有一张原样入湖的 ODS 表」
ODS_ONE_TO_ONE_SOURCES: Final[int] = 8

#: [a5] CDC 通道「按『全量快照 → 增量 binlog → 断点续传』三阶段运行」
CDC_PHASE_COUNT: Final[int] = 3
#: [a5] 「业务库零侵入、不改代码，同步延迟秒级」
CDC_SYNC_LATENCY_TEXT: Final[str] = "秒级"

#: [a5] 「几十 TB 的采集大文件」（第六章导语）
COLLECT_FILE_VOLUME_TEXT: Final[str] = "几十 TB"
#: [a5] 「图像、点云这类几十 GB 的文件从不进湖」
BIG_FILE_SIZE_TEXT: Final[str] = "几十 GB"

#: [a5] 大文件外置时湖仓保留的元信息四项：「文件大小、脱敏标记、校验和、归属 data_id」
EXTERNALIZED_FILE_META_FIELDS: Final[tuple[str, str, str, str]] = (
    "文件大小",
    "脱敏标记",
    "校验和",
    "归属 data_id",
)

#: [a5] 质量门禁「六维检查框架（完整性 / 准确性 / 一致性 / 唯一性 / 有效性 / 及时性）」
QUALITY_DIMENSION_COUNT: Final[int] = 6
#: [a5] 「ERROR 拒绝、WARNING 带标放行的三分支处置」
QUALITY_BRANCH_COUNT: Final[int] = 3
#: [a5] 第八章「异常数据进隔离表、按 P0~P3 分级告警」
ALERT_LEVELS: Final[tuple[str, str, str, str]] = ("P0", "P1", "P2", "P3")

# ===========================================================================
# 二之二、[a6] 五步异常闭环里、由本子系统执行的那几个数字
#     规则引擎与 SLA 归 ``adas_lakehouse.quality``；下面两个数字是入湖通道自己要用的，
#     逐字登记在这里，取值与 ``quality.thresholds`` 同名常量必须一致——
#     一致性由 tests/deep/test_ingest.py::test_a6_numbers_do_not_drift_from_quality 守住
#     （本包对 quality 一律延迟 import，不在 import 期为整套规则集付代价）。
# ===========================================================================

#: [a6] 第五章第 ⑤ 步：「重新执行全部门禁规则，通过则写入 ODS 并回填处理状态；
#: 不通过退回隔离，**超 3 轮升级 P0**」。见 ``gate.AnomalyClosedLoop.recheck``。
MAX_RECHECK_ROUNDS: Final[int] = 3

#: [a6] 4.2 Kafka 消息流「重复率监控」：「事件 ID 幂等去重（Paimon 主键 Upsert），
#: **重复率 > 5% 告警**」——处理方式是「自动去重 + 超限告警」，两件事缺一不可。
#: 见 ``channels.IngestChannel.run``（自动去重）与 ``IngestReport.duplicate_rate``（超限告警）。
DUPLICATE_RATE_ALERT_THRESHOLD: Final[float] = 0.05

#: [a6] 第五章第 ③ 步的告警对象：「通知数据 owner + 平台值班」。
ALERT_NOTIFY_TARGETS: Final[tuple[str, str]] = ("数据 owner", "平台值班")

#: [a6] 第五章第 ④ 步分流处置的三个分支：A 自动修复 / B 人工修复 / C 弃置归档。
TRIAGE_BRANCH_COUNT: Final[int] = 3

# ===========================================================================
# 三、[a5] 与入湖相邻的治理数字（存储生命周期由 lifecycle 子系统落地，此处登记出处）
# ===========================================================================

#: [a5] 「NAS/CPFS 单价约为 OSS 标准存储的 8~10 倍」
NAS_PRICE_MULTIPLIER_MIN: Final[int] = 8
NAS_PRICE_MULTIPLIER_MAX: Final[int] = 10
#: [a5] 热层：「当期训练任务预热中，训练结束 7 天缓冲后淘汰」
HOT_TIER_BUFFER_DAYS: Final[int] = 7
#: [a5] 温层：「创建 30 天内，或 30 天内有访问」
WARM_TIER_DAYS: Final[int] = 30
#: [a5] 冷层：「连续 90 天无访问」，OSS 低频存储「约 0.5x 成本」
COLD_TIER_NO_ACCESS_DAYS: Final[int] = 90
COLD_TIER_COST_FACTOR: Final[float] = 0.5
#: [a5] 归档层：「连续 180 天无访问且过保留策略阈值」，OSS 归档「约 0.15x 成本」「取回 ≤4 小时」
ARCHIVE_TIER_NO_ACCESS_DAYS: Final[int] = 180
ARCHIVE_TIER_COST_FACTOR: Final[float] = 0.15
ARCHIVE_RESTORE_MAX_HOURS: Final[int] = 4
#: [a5] 成本样例：「一份 240GB 的雨夜城区采集数据」「年成本约 ¥3,226」→「压到 ¥203」「降幅约 94%」
#: 「预热上 NAS 仅占 9 天」「这份数据过 365 天保留期时……被删除三重确认拦截」
COST_CASE_SIZE_GB: Final[int] = 240
COST_CASE_BEFORE_CNY: Final[int] = 3226
COST_CASE_AFTER_CNY: Final[int] = 203
COST_CASE_REDUCTION_PCT: Final[int] = 94
COST_CASE_NAS_PREHEAT_DAYS: Final[int] = 9
RETENTION_POLICY_DAYS: Final[int] = 365
#: [a5] 「成本环比增长超 10% 自动告警」
COST_MOM_ALERT_PCT: Final[int] = 10
#: [a5] 删除「三重确认」：过保留期 + 血缘零引用 + 白名单校验
DELETE_TRIPLE_CONFIRMATION: Final[int] = 3

# ===========================================================================
# 四、[a5] 全景数字登记表
#     这些数字属于其他子系统（建模 / 向量 / 血缘 / 服务层 / 行业对标）的落地口径，
#     本子系统不使用，仅逐字登记以便全量对账。
# ===========================================================================

PANORAMA_FIGURES: Final[dict[str, str]] = {
    "闭环环节数": "8 环节闭环",
    "数据域": "11 个数据域：10 个业务过程域 + 1 个跨域闭环域",
    "标题表数": "79+ 张表",
    "核心场景": "6 大场景",
    "全湖表数": "89 张表：ODS 33 张 · DWD 28 张 · DWS 14 张 · ADS 11 张，另有 3 张血缘关系表",
    "L4 日产数据": "一家 L4 级自动驾驶公司每天产生的原始数据超过 10TB",
    "数据利用率": "真正被用于模型迭代的数据不到 2%",
    "单车日产": "TB 级",
    "车队总量": "PB 级",
    "FSD 里程": "特斯拉 FSD 累计行驶里程已突破 167 亿公里",
    "车队规模门槛": "至少需要百万辆级智驾车队",
    "场景数据效应": "每增加 1000 万公里针对性场景数据，该场景下的接管率可降低 15%~25%",
    "全文篇幅": "全文约 1.2 万字，11 章干货",
    "系列篇数": "系列一二共 16 篇精华收拢（系列一 8 篇 + 系列二 8 篇）",
    "应用层平台": "9 大平台",
    "生产链路环节": "采集 → 上云 → 打标签 → 前处理 → 标注 → 质检 → 后处理 → 交付共 14 个环节",
    "场景①批次": "某项目批次 3,200 条 clip 完成上云，其中 120 条在质检环节停留超 48 小时",
    "场景③ Badcase": "某版本模型在高速匝道场景 Badcase 率 0.8%，高于基线的 0.3%",
    "场景④触发": "量产车在「夜间无灯路口右转」场景一个月触发 1,200 次接管",
    "场景⑤覆盖率": "「隧道内逆光+大车遮挡」覆盖率仅 0.2% 但 Badcase 率最高",
    "HNSW 索引": "cosine 距离、M=16、efConstruction=200",
    "向量检索验收线": "P95 延迟 ≤2s",
    "图库遍历深度": "多跳遍历建议限定 3-5 跳",
    "特斯拉": "标注自动化率约 95%，迭代周期约 2 周",
    "Momenta": "实车里程超百亿公里",
    "小鹏": "小鹏智驾里程占比破 50% 后百公里接管降 26%",
    "Waymo": "每周数千万英里仿真验证",
    "数据主权": "国内道路采集数据 100% 境内存储不得出境",
    "闭环业务服务": "六项闭环业务服务",
    "血缘查询方向": "四个查询方向（正向追踪 / 反向追溯 / 版本分支对比 / 影响分析）",
}

# ===========================================================================
# 五、本项目补充的工程参数
# ===========================================================================

#: ⚠️ 原文未明确，本项目设计：可解码性探针读取的文件头字节数。
#: 原文只说「图像 / 点云文件完整性与可解码性校验」，未给实现方式与读取长度。
#: 取 32 字节足以覆盖 JPEG/PNG/PCD/LAS 的魔数与版本头，且对象存储 Range 读代价可忽略。
DECODE_PROBE_BYTES: Final[int] = 32

#: ⚠️ 原文未明确，本项目设计：Flink SQL Gateway REST 调用超时（秒）。
SQL_GATEWAY_TIMEOUT_SEC: Final[int] = 30

#: ⚠️ 原文未明确，本项目设计：Kafka 通道「时空合理」的事件时间容忍窗口。
#: [a6] 4.2 只说「不早于车辆出厂时间、不晚于服务器时间 + 容忍窗口（防车端时钟漂移）」，
#: 没给窗口大小。未来侧取 300 秒（与 quality.thresholds.CLOCK_DRIFT_TOLERANCE_SECONDS
#: 同口径），滞后侧取 30 天（覆盖车端离线缓存后补传）。
#: **Python 侧（channels.KafkaChannel）与 SQL 侧（sql.render_kafka_pipeline）共用这两个值**，
#: 各写各的会让同一条事件在流作业里被放行、在补数脚本里被拒——两处必须同源。
KAFKA_FUTURE_TOLERANCE_SEC: Final[int] = 300
KAFKA_LAG_TOLERANCE_DAYS: Final[int] = 30

#: ⚠️ 原文未明确，本项目设计：本地回放 / 补数模式下单批最大记录数。
#: 与 [a8]「一个采集任务数百个文件」同量级，保证一个采集任务可一批灌完。
LOCAL_REPLAY_BATCH_SIZE: Final[int] = 500

#: ⚠️ 原文未明确，本项目设计：Python 侧写 ODS 的最大尝试次数（首次 + 重试）。
#: 原文给的是语义要求而不是次数——[a5] 第六章 Kafka 通道「消费失败可从上次位点
#: 重新消费，不丢事件」，第八章五步异常闭环第 ④ 步「自动修复（重传 / 幂等重放 /
#: 断点续传）」。取 3 与 quality 子系统「复验不通过退回隔离，超 3 轮升级 P0」
#: （thresholds.MAX_RECHECK_ROUNDS）同口径，避免同一条数据在两处被按不同轮次对待。
#: 重试之所以安全，前提是写入幂等：三张目标表都是 Paimon 主键表（upsert），
#: 且通道侧按主键做了批内去重，见 ``channels.IngestChannel.idempotency_key``。
ODS_WRITE_MAX_ATTEMPTS: Final[int] = 3
