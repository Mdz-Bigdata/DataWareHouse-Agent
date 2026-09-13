"""ADS 数据产品矩阵服务层的全部数值常量，唯一出处。

来源（两篇原文，下文注释里以 [S1-05] / [S1-全景] 标注章节）：
  [S1-05]  《11 张 ADS 数据闭环表开箱即用：智驾数据产品矩阵全览》
           公众号「小周谈智驾数据闭环」2026-08-28
           https://mp.weixin.qq.com/s/c2IZlxJvV8XVBMNUQyGH0Q
  [S1-全景]《智驾数据闭环的湖仓架构全景：8 环节闭环 × 11 数据域 × 79+ 张表 × 6 大场景》
           公众号「小周谈智驾数据闭环」2026-09-08
           https://mp.weixin.qq.com/s/UmHoxjBwRtZT0PgwjkL9DQ

纪律（与 quality.thresholds 一致）：
  · 原文出现过的每一个数字在这里逐字落地，注释标注它在原文的位置，不四舍五入、不改写；
  · 业务代码里不写裸数字，一律引用本模块；
  · 原文没给、本项目补的数字，一律以「⚠️ 原文未明确，本项目设计：」开头说明，不冒充原文方案。

原文里的业务案例数字（3,200 个 Badcase、87.5% 采纳率……）是「演示数据取自业务场景举例」
（[S1-05] 各图注原话）。它们在本项目里的用途有两个：一是作为产品矩阵文档的场景说明，
二是作为演示数据源（DemoRowSource）与看板阈值的标定基准，**不是**生产环境的真实统计值。
"""

from __future__ import annotations

from typing import Final

# ===========================================================================
# 一、矩阵规模 —— [S1-05] 全文 / [S1-全景] 第四章
# ===========================================================================

#: ADS 表张数：11 张开箱即用的数据产品表（[S1-05] 标题与第二章全景表）
ADS_TABLE_COUNT: Final[int] = 11
#: 六大业务主题（[S1-05] 第二章「按业务主题归成六组」）
ADS_THEME_COUNT: Final[int] = 6
#: 六大业务平台（[S1-05] 第二章图注「11 张 ADS 表：六大业务主题 × 六大业务平台」）
ADS_SERVED_PLATFORM_COUNT: Final[int] = 6
#: 应用层平台总数：9 大平台（数据管理/标注/训练/评测/仿真/问题分析/数据挖掘/车云）+ 监控大屏
#: （[S1-全景] 第二章分层表「应用层」）
APPLICATION_PLATFORM_COUNT: Final[int] = 9
#: 六项闭环业务服务（[S1-全景] 第九章服务表）
CLOSED_LOOP_SERVICE_COUNT: Final[int] = 6
#: 六大核心场景（[S1-全景] 第五章）
CORE_SCENARIO_COUNT: Final[int] = 6
#: 闭环 8 环节（[S1-全景] 标题与第一章）
CLOSED_LOOP_STAGE_COUNT: Final[int] = 8
#: 11 个数据域（[S1-全景] 第四章）
DATA_DOMAIN_COUNT: Final[int] = 11

# 全湖分层表数（[S1-全景] 第四章：「全湖 89 张表：ODS 33 张 · DWD 28 张 · DWS 14 张 ·
# ADS 11 张，另有 3 张血缘关系表」）。注意与 catalog.registry 的 88 张口径存在偏差，
# 见 registry 模块 docstring 与 docs/source-deviations.md——本模块只负责如实记录原文数字。
LAKE_TABLE_COUNT_TOTAL: Final[int] = 89
LAKE_TABLE_COUNT_ODS: Final[int] = 33
LAKE_TABLE_COUNT_DWD: Final[int] = 28
LAKE_TABLE_COUNT_DWS: Final[int] = 14
LAKE_TABLE_COUNT_ADS: Final[int] = 11
LAKE_LINEAGE_RELATION_TABLE_COUNT: Final[int] = 3
#: 原文正文另一处口径「79+ 张表」（[S1-全景] 标题）
LAKE_TABLE_COUNT_HEADLINE: Final[int] = 79

# ===========================================================================
# 二、闭环健康度（表 1 · 表 2）—— [S1-05] 第一/三章
# ===========================================================================

#: 待回答的存储规模：「1.8PB 存储每月花多少钱？」（[S1-05] 第一章业务问题三）
LAKEHOUSE_STORAGE_PB: Final[float] = 1.8
#: 优化前的闭环平均耗时（小时）：「大盘发现 216 小时」（[S1-05] 第六章设计启示）
CLOSED_LOOP_BASELINE_HOURS: Final[int] = 216
#: 优化后的闭环平均耗时（小时）：「一周优化到 96 小时」（[S1-05] 第六章设计启示 / 要点③）
CLOSED_LOOP_TARGET_HOURS: Final[int] = 96
#: 从定位瓶颈到达成 96 小时所用的时间：一周（[S1-05] 第六章「一周优化到 96 小时」）
CLOSED_LOOP_OPTIMIZATION_WEEKS: Final[int] = 1
#: 产线环节四段：上云 / 前处理 / 标注 / 质检（[S1-05] 第三章表 2 口径）
PRODUCTION_STAGE_NAMES: Final[tuple[str, ...]] = ("上云", "前处理", "标注", "质检")
#: 生产链路全环节数：采集 → 上云 → 打标签 → 前处理 → 标注 → 质检 → 后处理 → 交付共 14 个环节
#: （[S1-全景] 第五章场景①）
PRODUCTION_CHAIN_STAGE_COUNT: Final[int] = 14
#: 场景①案例：某项目批次 3,200 条 clip 完成上云（[S1-全景] 第五章场景①）
SCENARIO_BATCH_CLIP_COUNT: Final[int] = 3_200
#: 场景①案例：其中 120 条在质检环节停留超 48 小时（[S1-全景] 第五章场景①）
SCENARIO_BLOCKED_CLIP_COUNT: Final[int] = 120
#: 「停留超 N 小时」的积压判定阈值（小时）——原文以 48 小时作为质检积压的观察口径
#: （[S1-全景] 第五章场景①），ADS 表 ads_production_bottleneck_analysis 的
#: blocked_over_48h_count 即按此口径统计
PRODUCTION_BLOCKED_ALERT_HOURS: Final[int] = 48

#: ⚠️ 原文未明确，本项目设计：[S1-05] 表 2 只说「瓶颈环节标识（耗时占比超阈值或环比恶化
#: 自动标记）」，没给「阈值」的数值。本项目取 30%——单环节耗时占全链路 30% 以上即标记为
#: 瓶颈候选，可按项目在配置中覆盖。
BOTTLENECK_DURATION_RATIO_THRESHOLD: Final[float] = 0.30
#: ⚠️ 原文未明确，本项目设计：「环比恶化」的判定阈值，取 +10%（环比耗时增长超过 10%）。
BOTTLENECK_MOM_DETERIORATION_THRESHOLD: Final[float] = 0.10

# ===========================================================================
# 三、Badcase 即资产（表 3 · 表 4 · 表 5）—— [S1-05] 第四章
# ===========================================================================

# --- 表 3 根因分布：模型 v3.2 评测案例
#: 模型 v3.2 评测产出 3,200 个 Badcase（[S1-05] 第四章 1）
BADCASE_DEMO_TOTAL_COUNT: Final[int] = 3_200
#: 分布显示感知漏检占 45%（[S1-05] 第四章 1）
BADCASE_DEMO_PERCEPTION_MISS_RATIO: Final[float] = 0.45
#: 其中夜间行人占 28% 且趋势上升（[S1-05] 第四章 1）
BADCASE_DEMO_NIGHT_PEDESTRIAN_RATIO: Final[float] = 0.28
#: 定向补采 2,000 条重训（[S1-05] 第四章 1）
BADCASE_DEMO_TARGETED_COLLECT_COUNT: Final[int] = 2_000
#: 该类漏检下降 60%（[S1-05] 第四章 1）
BADCASE_DEMO_MISS_RATE_DROP_RATIO: Final[float] = 0.60
#: 案例中的被测模型版本（[S1-05] 第四章 1）
BADCASE_DEMO_MODEL_VERSION: Final[str] = "v3.2"

# --- 表 4 场景库：施工区域缺口案例
#: 场景库定义 1,200 个标签（[S1-05] 第四章 2）
SCENE_LIBRARY_TAG_COUNT: Final[int] = 1_200
#: 场景库覆盖度 82%（[S1-05] 第四章 2）
SCENE_LIBRARY_COVERAGE_RATE: Final[float] = 0.82
#: 「施工区域」仅 150 条（[S1-05] 第四章 2）
SCENE_GAP_DEMO_CURRENT_COUNT: Final[int] = 150
#: 达标线 2,000 条（[S1-05] 第四章 2）
SCENE_GAP_DEMO_TARGET_COUNT: Final[int] = 2_000
#: 定向补采两周后达标、状态流转 COVERED（[S1-05] 第四章 2）
SCENE_GAP_DEMO_SUPPLEMENT_WEEKS: Final[int] = 2
#: 覆盖状态机三态（[S1-05] 第四章 2「状态流转 COVERED」+ dws_scene_distribution 口径）
SCENE_COVERAGE_STATUS_FLOW: Final[tuple[str, ...]] = ("GAP", "FILLING", "COVERED")
#: 场景⑤案例：「隧道内逆光+大车遮挡」覆盖率仅 0.2% 但 Badcase 率最高
#: （[S1-全景] 第五章场景④⑤）
SCENE_GAP_TUNNEL_BACKLIT_COVERAGE_RATE: Final[float] = 0.002

# --- 表 5 难例库：v3.2 → v3.3 难例闭环案例
#: v3.2 评测挖出 3,200 条难例（[S1-05] 第四章 3）
HARD_CASE_DEMO_TOTAL_COUNT: Final[int] = 3_200
#: 其中夜间行人 900 条（[S1-05] 第四章 3）
HARD_CASE_DEMO_NIGHT_PEDESTRIAN_COUNT: Final[int] = 900
#: 其中逆光车辆 700 条（[S1-05] 第四章 3）
HARD_CASE_DEMO_BACKLIT_VEHICLE_COUNT: Final[int] = 700
#: 训练平台采纳 2,800 条（[S1-05] 第四章 3）
HARD_CASE_DEMO_ADOPTED_COUNT: Final[int] = 2_800
#: 采纳率 87.5%（[S1-05] 第四章 3）
HARD_CASE_DEMO_ADOPTION_RATE: Final[float] = 0.875
#: 混入 v3.3 训练集，重训后夜间行人漏检率下降 60%（[S1-05] 第四章 3）
HARD_CASE_DEMO_MISS_RATE_DROP_RATIO: Final[float] = 0.60
#: 难例混入的训练模型版本（[S1-05] 第四章 3）
HARD_CASE_DEMO_RETRAIN_MODEL_VERSION: Final[str] = "v3.3"

# ===========================================================================
# 四、迭代与上车（表 7 · 表 8 · 表 9）—— [S1-05] 第五章
# ===========================================================================

# --- 表 7 模型版本对比：v3.3 vs v3.2
#: 新版本（[S1-05] 第五章表 7 案例）
MODEL_COMPARE_DEMO_MODEL_VERSION: Final[str] = "v3.3"
#: 基线版本（[S1-05] 第五章表 7 案例）
MODEL_COMPARE_DEMO_BASELINE_VERSION: Final[str] = "v3.2"
#: 评测数据集：「城区 NOA 评测集 v5」（[S1-05] 第五章表 7 案例）
MODEL_COMPARE_DEMO_DATASET_NAME: Final[str] = "城区 NOA 评测集 v5"
#: 总体通过率 91.2%（新版本）（[S1-05] 第五章表 7 案例）
MODEL_COMPARE_DEMO_PASS_RATE: Final[float] = 0.912
#: 总体通过率 87.5%（基线）（[S1-05] 第五章表 7 案例）
MODEL_COMPARE_DEMO_BASELINE_PASS_RATE: Final[float] = 0.875
#: 夜间场景显著提升 +8.3pp（[S1-05] 第五章表 7 案例）
MODEL_COMPARE_DEMO_NIGHT_GAIN_PP: Final[float] = 8.3
#: 高速场景回归 -1.2pp（[S1-05] 第五章表 7 案例）
MODEL_COMPARE_DEMO_HIGHWAY_REGRESSION_PP: Final[float] = -1.2
#: ⚠️ 原文未明确，本项目设计：原文只说「标记回归项」「修复后再 OTA」，没给「多少 pp 算回归」。
#: 本项目取 0.0 pp 作为默认门槛——通过率差值为负即判回归，宁可多拦一次也不放带病模型上车。
#: 各项目可通过 OtaGateDecision 的 tolerance_pp 参数放宽。
MODEL_REGRESSION_TOLERANCE_PP_DEFAULT: Final[float] = 0.0

# --- 表 8 OTA 部署汇总：v3.3 灰度推送
#: 灰度推送 500 台车（[S1-05] 第五章表 8 案例）
OTA_DEMO_GREY_VEHICLE_COUNT: Final[int] = 500
#: 升级成功率 99.2%（[S1-05] 第五章表 8 案例）
OTA_DEMO_SUCCESS_RATE: Final[float] = 0.992
#: 发布后一周回传触发量环比增长 30%（[S1-05] 第五章表 8 案例）
OTA_DEMO_TRIGGER_GROWTH_RATE: Final[float] = 0.30
#: 「发布后一周」的观察窗口（天）（[S1-05] 第五章表 8 案例）
OTA_POST_RELEASE_OBSERVE_DAYS: Final[int] = 7
#: 安全相关问题 0 起 → 确认全量推送（[S1-05] 第五章表 8 案例）
OTA_DEMO_SAFETY_ISSUE_COUNT: Final[int] = 0
#: 发布通道三档（catalog ads_ota_deployment_summary.release_channel 口径）
OTA_RELEASE_CHANNELS: Final[tuple[str, ...]] = ("internal", "grey", "full")

# --- 表 8 的「灰度 → 全量」三条件放行门
# 原文把三个观察值并列之后才给出结论（[S1-05] 第五章表 8 原话）：
#   「v3.3 灰度推送 500 台车：升级成功率 99.2%，发布后一周回传触发量环比增长 30%
#     （新版本主动采集策略生效），安全相关问题 0 起 → 确认全量推送」
# 三者是**与**关系：三条同时成立才「确认全量推送」，缺一条就不放行。
# 门槛值不另写数字，直接引用上面的案例常量，杜绝同一个数在两处漂移。
#: 条件①：升级成功率 ≥ 99.2%
OTA_GATE_MIN_SUCCESS_RATE: Final[float] = OTA_DEMO_SUCCESS_RATE
#: 条件②：发布后一周回传触发量环比增长 ≥ +30%
OTA_GATE_MIN_TRIGGER_GROWTH_RATE: Final[float] = OTA_DEMO_TRIGGER_GROWTH_RATE
#: 条件③：安全相关问题 = 0 起
OTA_GATE_MAX_SAFETY_ISSUE_COUNT: Final[int] = OTA_DEMO_SAFETY_ISSUE_COUNT
#: 三条件（[S1-05] 第五章表 8），放行判定按「与」聚合
OTA_GATE_CONDITION_COUNT: Final[int] = 3
#: ⚠️ 原文未明确，本项目设计：比较率值时吸收 IEEE-754 表示误差的容差。
#: 它只抵消浮点表示误差（1e-9 折算成百分点是 1e-7 pp），**不放宽任何一条门槛**：
#: 真实低于门槛的成功率（哪怕 99.1999%）仍然判不通过。
OTA_GATE_FLOAT_EPSILON: Final[float] = 1e-9
#: 放行结论两态（本项目设计的枚举值，语义取自原文「确认全量推送」/「修复后再 OTA」）
OTA_GATE_DECISION_PASS: Final[str] = "full_rollout"
OTA_GATE_DECISION_HOLD: Final[str] = "hold"

# --- 表 9 触发事件热力图
#: 某区域「AEB 误触发」周环比上升 45%（[S1-05] 第五章表 9 案例）
TRIGGER_HEATMAP_DEMO_AEB_WOW_RISE: Final[float] = 0.45
#: 触发集中在城区晚高峰路口（[S1-05] 第五章表 9 案例）
TRIGGER_HEATMAP_DEMO_HOTSPOT: Final[str] = "城区晚高峰路口"
#: ⚠️ 原文未明确，本项目设计：热力图「周环比异常」的告警阈值。原文只给了 45% 这个案例值，
#: 没给触发告警的阈值线。本项目取 30%——周环比涨幅超过 30% 即进异常清单，
#: 保证 45% 的案例能被捞出来，同时不至于把正常波动全报出来。
TRIGGER_WOW_ANOMALY_THRESHOLD: Final[float] = 0.30
#: 「周环比」的窗口长度（天）——原文表 9 的「周环比上升 45%」与表 8 的「发布后一周」
#: 是同一个 7 天口径（[S1-05] 第五章表 8 / 表 9）。
TRIGGER_WOW_WINDOW_DAYS: Final[int] = 7
#: 热力等级档数：heat_level 1~5（catalog ads_trigger_heatmap.heat_level 口径）
TRIGGER_HEAT_LEVEL_MAX: Final[int] = 5
#: 热力等级最低档
TRIGGER_HEAT_LEVEL_MIN: Final[int] = 1
#: ⚠️ 原文未明确，本项目设计：原文只说热力图「按地理网格统计触发总量」，
#: catalog 只说 heat_level 是 1~5，都没给分档阈值。本项目按网格内触发次数
#: 5 / 20 / 50 / 100 分五档（第 k 档的下界即第 k-1 个阈值）：
#: <5 → 1，[5,20) → 2，[20,50) → 3，[50,100) → 4，≥100 → 5。
#: 这一份阈值同时喂给 Flink 批作业（ads.geo.heat_level_sql）与服务层
#: （ads.geo.heat_level），两边永远同档。
TRIGGER_HEAT_LEVEL_THRESHOLDS: Final[tuple[int, ...]] = (5, 20, 50, 100)
#: ⚠️ 原文未明确，本项目设计：catalog 把 ads_trigger_heatmap.geo_grid_id 注释为 GeoHash，
#: 但上游 dwd_vehicle_trigger_detail 只落了经纬度，没有 GeoHash 编码列。
#: 本项目先用「经纬度按 0.01 度取整」作为网格键（0.01 度纬向约 1.1 km，
#: 是城市路口级热点的合适粒度），接入真实 GeoHash 后替换 ads.geo 一处即可。
GEO_GRID_PRECISION_DEGREES: Final[float] = 0.01
#: 网格键的小数位数——必须与 GEO_GRID_PRECISION_DEGREES 对齐（0.01 度 → 2 位）。
GEO_GRID_ID_DECIMALS: Final[int] = 2
#: 网格键的分隔符（``"31.23_121.47"``）
GEO_GRID_ID_SEPARATOR: Final[str] = "_"
#: 经纬度合法区间（WGS-84），越界即拒绝编码——脏 GPS 不进热力图
GEO_LAT_RANGE: Final[tuple[float, float]] = (-90.0, 90.0)
GEO_LON_RANGE: Final[tuple[float, float]] = (-180.0, 180.0)
#: 场景④案例：量产车在「夜间无灯路口右转」场景一个月触发 1,200 次接管（[S1-全景] 第五章）
TRIGGER_DEMO_MONTHLY_TAKEOVER_COUNT: Final[int] = 1_200

# ===========================================================================
# 五、运营底盘（表 6 · 表 10 · 表 11）—— [S1-05] 第六章 / [S1-全景] 第八章
# ===========================================================================

# --- 表 6 数据资产目录
#: 「城区 NOA 主数据集 v12」本季度被 12 个训练任务引用（[S1-05] 第六章表 6 案例）
ASSET_DEMO_REF_COUNT_90D: Final[int] = 12
#: 质量评分 4.6（[S1-05] 第六章表 6 案例）
ASSET_DEMO_QUALITY_SCORE: Final[float] = 4.6
#: 同期 3 个低分老旧数据集长期无人使用 → 标记归档（[S1-05] 第六章表 6 案例）
ASSET_DEMO_IDLE_ASSET_COUNT: Final[int] = 3
#: 案例资产名（[S1-05] 第六章表 6）
ASSET_DEMO_NAME: Final[str] = "城区NOA主数据集 v12"
#: 「本季度」窗口（天）——资产热度统计窗口，对应 ads_data_asset_catalog.ref_count_90d
ASSET_HOT_WINDOW_DAYS: Final[int] = 90
#: 质量评分满分（catalog ads_data_asset_catalog.quality_score「（0-5）」口径）
ASSET_QUALITY_SCORE_MAX: Final[float] = 5.0
#: ⚠️ 原文未明确，本项目设计：「低分」的判定线。原文只说「3 个低分老旧数据集长期无人使用」，
#: 没给分数线。本项目取 3.0 分（满分 5 分）作为低分线，配合「近 90 天引用次数 = 0」
#: 共同构成归档建议条件。
ASSET_LOW_QUALITY_SCORE_THRESHOLD: Final[float] = 3.0
#: ⚠️ 原文未明确，本项目设计：「长期无人使用」的天数线，取 90 天（与本季度热度窗口同宽）。
ASSET_IDLE_DAYS_THRESHOLD: Final[int] = 90

# --- 表 10 存储成本看板（数字全部来自 [S1-全景] 第八章③存储生命周期）
#: NAS/CPFS 单价约为 OSS 标准存储的 8~10 倍（下界）
NAS_PRICE_MULTIPLIER_MIN: Final[int] = 8
#: NAS/CPFS 单价约为 OSS 标准存储的 8~10 倍（上界）
NAS_PRICE_MULTIPLIER_MAX: Final[int] = 10
#: OSS 低频存储约 0.5x 成本
OSS_IA_COST_MULTIPLIER: Final[float] = 0.5
#: OSS 归档约 0.15x 成本
OSS_ARCHIVE_COST_MULTIPLIER: Final[float] = 0.15
#: 归档取回 ≤4 小时
ARCHIVE_RESTORE_MAX_HOURS: Final[int] = 4
#: 热层：训练结束 7 天缓冲后淘汰
HOT_TIER_BUFFER_DAYS: Final[int] = 7
#: 温层：创建 30 天内，或 30 天内有访问
WARM_TIER_DAYS: Final[int] = 30
#: 冷层：连续 90 天无访问
COLD_TIER_NO_ACCESS_DAYS: Final[int] = 90
#: 归档层：连续 180 天无访问且过保留策略阈值
ARCHIVE_TIER_NO_ACCESS_DAYS: Final[int] = 180
#: 删除三重确认里的保留期：一份数据过 365 天保留期时被血缘引用拦截
RETENTION_DAYS: Final[int] = 365
#: 成本账案例：一份 240GB 的雨夜城区采集数据
STORAGE_COST_DEMO_VOLUME_GB: Final[int] = 240
#: 常年驻留 NAS + OSS 标准，年成本约 ¥3,226
STORAGE_COST_DEMO_UNGOVERNED_YEARLY_YUAN: Final[int] = 3_226
#: 走治理流程后年成本压到 ¥203
STORAGE_COST_DEMO_GOVERNED_YEARLY_YUAN: Final[int] = 203
#: 降幅约 94%
STORAGE_COST_DEMO_SAVING_RATIO: Final[float] = 0.94
#: 治理流程中预热上 NAS 仅占 9 天
STORAGE_COST_DEMO_PREHEAT_DAYS: Final[int] = 9
#: 成本环比增长超 10% 自动告警
STORAGE_COST_MOM_ALERT_THRESHOLD: Final[float] = 0.10
#: NAS 峰值使用率 >80% 触发水位淘汰与告警
#: （catalog dws_closed_loop_storage_cost_daily.nas_peak_usage 口径，与本项目成本看板一致）
NAS_PEAK_USAGE_ALERT_THRESHOLD: Final[float] = 0.80
#: 存储五级分层的**档位**（[S1-全景] 第八章③分层表 / [a14] 第二章：热 / 温 / 冷 / 归档 / 删除）。
#: ⚠️ 这是「生命周期档位」，**不是** `dwd_closed_loop_storage_lifecycle.lifecycle_stage`
#: 字段的取值域——该字段是六值 `hot/warm/cold/archive/pending_delete/deleted`
#: （删除档在字段上拆成待删与已删两态），权威定义在 `lifecycle.tiers.LifecycleStage`；
#: 介质枚举又是第三个维度（五值，见 `lifecycle.tiers.StorageMedia`）。
#: 本常量只用于看板文案与档位遍历，**不要拿它校验字段值**。
STORAGE_LIFECYCLE_TIERS: Final[tuple[str, ...]] = ("hot", "warm", "cold", "archive", "delete")
#: 旧名，保留向后兼容；新代码请用 STORAGE_LIFECYCLE_TIERS。
STORAGE_LIFECYCLE_STAGES: Final[tuple[str, ...]] = STORAGE_LIFECYCLE_TIERS

# --- 表 11 挖掘标签分布看板
#: 标签三来源：采集 / 规则 / 模型（[S1-05] 第六章表 11「三来源（采集/规则/模型）标签量构成」）
MINING_TAG_SOURCES: Final[tuple[str, ...]] = ("collect", "rule", "vlm")
#: ⚠️ 原文未明确，本项目设计：表 11 只说「盯住标签资产的健康度：…clip 覆盖率、向量化率、
#: 检索热度与候选池待审量」，没给任何阈值。本项目给出三条默认健康线：
#: clip 覆盖率低于 60% 视为标签覆盖不足。
MINING_TAG_COVERAGE_WARN_THRESHOLD: Final[float] = 0.60
#: ⚠️ 原文未明确，本项目设计：候选池待审量超过 500 条视为审核积压。
MINING_TAG_PENDING_REVIEW_WARN_COUNT: Final[int] = 500

# ===========================================================================
# 六、查询双路与服务层 —— [S1-全景] 第七/九章
# ===========================================================================

#: ADS 内表刷新节奏：T+1 批加工（[S1-05] 第一章「T+1 批加工、毫秒级直查」）
ADS_REFRESH_MODE: Final[str] = "T+1"
#: 语义检索 / 相似样本圈选的验收线：P95 延迟 ≤2s（[S1-全景] 第七章）
VECTOR_SEARCH_P95_LATENCY_SECONDS: Final[float] = 2.0
#: HNSW 索引参数（[S1-全景] 第七章）——本模块不实现向量检索，仅在选路说明里引用
HNSW_M: Final[int] = 16
HNSW_EF_CONSTRUCTION: Final[int] = 200
HNSW_METRIC: Final[str] = "cosine"
#: 血缘多跳遍历建议限定 3-5 跳防止扇出爆炸（[S1-全景] 第八章② / config.Neo4jConfig）
LINEAGE_MIN_TRAVERSAL_DEPTH: Final[int] = 3
LINEAGE_MAX_TRAVERSAL_DEPTH: Final[int] = 5
#: 血缘四个查询方向（[S1-全景] 第八章②表）
LINEAGE_QUERY_DIRECTIONS: Final[tuple[str, ...]] = (
    "forward_trace",
    "backward_trace",
    "version_branch_compare",
    "impact_analysis",
)

#: ⚠️ 原文未明确，本项目设计：[S1-05] 反复强调 ADS 内表是「毫秒级直查」，但没给具体毫秒数。
#: 本项目取 200 毫秒作为 ADS 内表查询的 SLO 告警线（超过即打 slow_query 审计标记），
#: 仅用于自监控，不改变查询语义。
ADS_QUERY_SLO_MS: Final[int] = 200
#: ⚠️ 原文未明确，本项目设计：单次 ADS 查询默认返回行数上限。大屏一屏的数据量级，
#: 超出应分页而不是一次拉全表。
ADS_QUERY_DEFAULT_LIMIT: Final[int] = 500
#: ⚠️ 原文未明确，本项目设计：单次 ADS 查询允许的最大行数上限（分页上限）。
ADS_QUERY_MAX_LIMIT: Final[int] = 10_000
#: ⚠️ 原文未明确，本项目设计：聚合类接口（热力图求和、周环比对比）翻页取数的总行数上限。
#: 超过即报错而不是截断——少算的总量会直接污染 OTA 放行结论与热点排名。
ADS_AGGREGATION_MAX_ROWS: Final[int] = 200_000
#: ⚠️ 原文未明确，本项目设计：查询超时（秒）。ADS 内表是毫秒级负载，10 秒仍未返回
#: 说明选错了路（该走 Paimon 外部表即席查）。
ADS_QUERY_TIMEOUT_SECONDS: Final[int] = 10
#: ⚠️ 原文未明确，本项目设计：网关默认 QPS 配额（每个调用方每秒请求数）。
GATEWAY_DEFAULT_QPS: Final[int] = 50
#: ⚠️ 原文未明确，本项目设计：网关令牌桶突发容量。
GATEWAY_BURST_CAPACITY: Final[int] = 100
#: ⚠️ 原文未明确，本项目设计：网关审计日志在内存中保留的最近条数（落库由接入方自理）。
GATEWAY_AUDIT_RING_SIZE: Final[int] = 1_000
#: ⚠️ 原文未明确，本项目设计：大屏结果缓存 TTL（秒）。T+1 物化的数据一天只变一次，
#: 60 秒缓存足以削掉大屏轮询的绝大部分压力，又不至于让手工重刷后的结果长时间不可见。
ADS_RESULT_CACHE_TTL_SECONDS: Final[int] = 60

# ===========================================================================
# 七、行业背景数字（[S1-全景] 第一/十章）—— 供大盘文案与对标视图引用
# ===========================================================================

#: 一家 L4 级自动驾驶公司每天产生的原始数据超过 10TB（[S1-全景] 第一章）
INDUSTRY_L4_DAILY_RAW_DATA_TB: Final[int] = 10
#: 真正被用于模型迭代的数据不到 2%——其余成为「数据暗物质」（[S1-全景] 第一章）
INDUSTRY_DATA_UTILIZATION_RATIO: Final[float] = 0.02
#: 特斯拉 FSD 累计行驶里程已突破 167 亿公里（[S1-全景] 第一章）。
#: 单位是**亿公里**（10⁸ km），不是 billion（10⁹ km）——旧名 ``..._BILLION_KM``
#: 把量级说大了 10 倍，照着它换算会得出「1670 亿公里」。值仍逐字取原文的 167。
INDUSTRY_TESLA_FSD_HUNDRED_MILLION_KM: Final[float] = 167.0
#: 同一个数字换算成公里的绝对值，避免调用方自己乘错量级。
INDUSTRY_TESLA_FSD_KM: Final[float] = INDUSTRY_TESLA_FSD_HUNDRED_MILLION_KM * 1e8
#: 每增加 1000 万公里针对性场景数据，该场景下的接管率可降低 15%~25%（[S1-全景] 第一章）
INDUSTRY_TARGETED_KM_PER_GAIN: Final[int] = 10_000_000
INDUSTRY_TAKEOVER_DROP_MIN: Final[float] = 0.15
INDUSTRY_TAKEOVER_DROP_MAX: Final[float] = 0.25
#: 特斯拉 Data Engine：标注自动化率约 95%，迭代周期约 2 周（[S1-全景] 第十章）
INDUSTRY_TESLA_AUTO_LABEL_RATE: Final[float] = 0.95
INDUSTRY_TESLA_ITERATION_WEEKS: Final[int] = 2
#: 小鹏智驾里程占比破 50% 后百公里接管降 26%（[S1-全景] 第十章）
INDUSTRY_XPENG_MILEAGE_RATIO: Final[float] = 0.50
INDUSTRY_XPENG_TAKEOVER_DROP: Final[float] = 0.26
#: 场景③案例：某版本模型在高速匝道场景 Badcase 率 0.8%，高于基线的 0.3%（[S1-全景] 第五章）
SCENARIO_RAMP_BADCASE_RATE: Final[float] = 0.008
SCENARIO_RAMP_BASELINE_BADCASE_RATE: Final[float] = 0.003
#: 国内道路采集数据 100% 境内存储不得出境（[S1-全景] 第十章）
DATA_SOVEREIGNTY_DOMESTIC_RATIO: Final[float] = 1.00


__all__ = [name for name in dir() if name.isupper()]
