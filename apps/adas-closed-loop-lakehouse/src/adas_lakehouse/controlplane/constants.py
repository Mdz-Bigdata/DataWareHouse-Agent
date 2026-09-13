"""控制面/数据面分离的全部数值常量与名称常量的唯一出处。

来源：系列三「数据挖掘与 AI」第 1 篇《数据闭环数据挖掘平台架构设计：控制面/数据面
分离的工程实践》（公众号「小周谈智驾数据闭环」，2026-09-09，
https://mp.weixin.qq.com/s/bSekzM_WjtGtAd_1WdbBAQ）。以下简称「原文」。

原则与 quality/thresholds.py 一致：
  · 原文出现过的每一个数字都在这里逐字落地，并在注释里标注它在原文的位置；
  · 业务代码里不再写裸数字，一律引用本模块；
  · 原文没给的数字一律标注「⚠️ 原文未明确，本项目设计：」，不冒充原文方案。
"""

from __future__ import annotations

from typing import Final

# =========================================================================== 原文出处元信息

#: 原文所属系列（系列三「数据挖掘与 AI」）
SERIES_INDEX: Final[int] = 3
#: 系列三共 7 篇（原文导语「从今天起开启系列三「数据挖掘与 AI」，共 7 篇」）
SERIES_ARTICLE_COUNT: Final[int] = 7
#: 本篇为系列三第 1 篇（原文副标题「小周谈智驾数据闭环 · 系列三 · 数据挖掘与 AI 第 1 篇」）
ARTICLE_INDEX: Final[int] = 1
#: 下一篇编号（原文结尾预告「系列三第 2 篇 S3-02」）
NEXT_ARTICLE_CODE: Final[str] = "S3-02"
SOURCE_URL: Final[str] = "https://mp.weixin.qq.com/s/bSekzM_WjtGtAd_1WdbBAQ"
SOURCE_DATE: Final[str] = "2026-09-09"

# =========================================================================== 一、平台定位

#: 平台用四项核心能力完成「从海量数据中发现高价值场景」（原文第一章「它用四项核心能力完成这件事」）
CORE_CAPABILITY_COUNT: Final[int] = 4
#: 四项核心能力逐字清单（原文第一章）
CORE_CAPABILITIES: Final[tuple[str, ...]] = (
    "规则挖掘引擎",
    "VLM 推理挖掘引擎",
    "统一标签体系",
    "多模态语义检索",
)
#: 挖掘双出口（原文第一章「输出有两条路」）
MINING_EXITS: Final[tuple[str, ...]] = (
    "库内命中场景直接回补训练集，零采集成本",
    "未命中则下发定向采集需求，开启新一轮循环",
)
#: T+1 向量化流水线：滞后 1 天（原文第一章「T+1 向量化流水线构建图片与文本向量」）
VECTOR_PIPELINE_LAG_DAYS: Final[int] = 1

# =========================================================================== 二、第一设计原则

#: 第一设计原则（原文第二章，本子系统的全部约束都从这一句推导而来）
FIRST_PRINCIPLE: Final[str] = "平台不持有主数据"

#: 围绕第一设计原则的五条对齐约定（原文第二章表格，共 5 行）
ALIGNMENT_PRINCIPLE_COUNT: Final[int] = 5
ALIGNMENT_PRINCIPLES: Final[tuple[tuple[str, str], ...]] = (
    ("湖仓单一事实源", "抽帧元信息、标签、向量统一落 Paimon，平台自身仅存任务配置与运行态"),
    ("全局 ID 贯穿", "采集最小单元为 clip，统一用全局 data_id；image_id 内嵌 data_id，天然可回溯"),
    (
        "数仓命名规范",
        "新增表遵循 {层级}_{挖掘域}_{实体}_detail，共 11 张表（1 ODS + 8 DWD + 1 DWS + 1 ADS）",
    ),
    (
        "双路查询出口",
        "检索明细与向量走 External Catalog 查 Paimon；看板经 ADS 物化至 StarRocks 内表毫秒级直查",
    ),
    ("质量门禁与血缘", "标签入湖复用既有质量门禁（唯一性/有效性），抽帧产物登记血缘，全链路可追溯"),
)

#: 挖掘域新增表总数与分层拆分（原文第二章表格「共 11 张表（1 ODS + 8 DWD + 1 DWS + 1 ADS）」）
#: 注：表规格本身由 catalog/tables/ 下的挖掘域模块定义，本子系统只做数量对账，不定义表。
MINING_TABLE_COUNT: Final[int] = 11
MINING_ODS_TABLE_COUNT: Final[int] = 1
MINING_DWD_TABLE_COUNT: Final[int] = 8
MINING_DWS_TABLE_COUNT: Final[int] = 1
MINING_ADS_TABLE_COUNT: Final[int] = 1
#: 新增表命名公式（原文第二章表格）
MINING_TABLE_NAME_PATTERN: Final[str] = "{层级}_{挖掘域}_{实体}_detail"

#: 原文点名的表。其余 7 张 DWD 由挖掘域表模块定义，此处不猜。
#: clip 元数据不新建表——直接复用采集域既有的 dwd_collect_clip_detail（原文第二章末段）。
TABLE_CLIP_DETAIL: Final[str] = "dwd_collect_clip_detail"
#: 抽帧结果表（原文第三章「抽帧结果写入 dwd_mining_image_frame_detail」）
TABLE_IMAGE_FRAME_DETAIL: Final[str] = "dwd_mining_image_frame_detail"
#: 规则配置经 Flink CDC 同步入湖的落点（原文第四章「控制面回流数据面」）
TABLE_RULE_CONFIG: Final[str] = "ods_mining_rule_config"
#: 任务与审核动作定期回写的落点（原文第四章「控制面回流数据面」）
TABLE_TASK_DETAIL: Final[str] = "dwd_mining_task_detail"
#: 向量明细表（唯一按 dt 分区的挖掘域表，见系列二分区全景）
TABLE_IMAGE_VECTOR_DETAIL: Final[str] = "dwd_mining_image_vector_detail"
#: 看板出口表（「双路查询出口」的 ADS 那一路：经 ADS 物化至 StarRocks 内表毫秒级直查）。
#: 挖掘域在 catalog 里只有这一张 ADS 表，看板查询一律打在它上面，不要在别处写字面量。
TABLE_MINING_DASHBOARD: Final[str] = "ads_mining_tag_dashboard"

# =========================================================================== 三、五层应用架构

#: 平台自身按五层组织（原文第三章「平台自身按五层组织，每一层职责单一、伸缩独立」）
APP_ARCH_LAYER_COUNT: Final[int] = 5
#: 五层逐字清单：(层级, 核心组件, 设计要点)（原文第三章表格）
APP_ARCH_LAYERS: Final[tuple[tuple[str, str, str], ...]] = (
    ("接入层", "Web 控制台 + OpenAPI 网关", "统一入口，认证 / 限流 / 审计，OpenAPI 为唯一对外通道"),
    (
        "应用服务层",
        "统一检索 / 任务管理 / 统一标签 / 圈选导出 / 审核流，五个无状态微服务",
        "全部无状态可水平扩展，随检索 QPS 用 HPA 扩缩",
    ),
    (
        "核心引擎层",
        "抽帧（K8s Job）/ 规则挖掘（Spark 批 + Flink 流）/ VLM 推理（Ray + GPU）/ Embedding 流水线",
        "计算密集，各引擎独立调度独立扩容，互不阻塞",
    ),
    (
        "平台支撑层",
        "MySQL / Redis / Kafka / OSS / 模型注册表",
        "只存平台自身运行态，主数据一律不进",
    ),
    (
        "外部依赖",
        "DLF + Paimon 湖仓 / StarRocks / DolphinScheduler / GPU 资源池",
        "数据面全部外部化，平台无状态可随时重建",
    ),
)

#: 应用服务层五个无状态微服务（原文第三章表格）
APP_SERVICE_COUNT: Final[int] = 5
APP_SERVICES: Final[tuple[str, ...]] = ("统一检索", "任务管理", "统一标签", "圈选导出", "审核流")

# =========================================================================== 四、控制面/数据面分离

#: 控制面持有的三类运行态（原文第四章「MySQL 存规则配置、任务配置与执行状态、审核流状态」）
CONTROL_PLANE_MYSQL_STATE: Final[tuple[str, ...]] = ("规则配置", "任务配置与执行状态", "审核流状态")
#: Redis 持有的两类热缓存（原文第四章「Redis 存检索热点与字典热缓存」）
CONTROL_PLANE_REDIS_STATE: Final[tuple[str, ...]] = ("检索热点", "字典热缓存")
#: 数据面持有的四类主数据（原文第四章「clip / 图片 / 标签 / 向量全部在 Paimon」）
DATA_PLANE_MASTER_DATA: Final[tuple[str, ...]] = ("clip", "图片", "标签", "向量")

#: 分离的健康判据（原文第四章末的 💡 判断标准，逐字）
HEALTH_CRITERION: Final[str] = "把平台的数据库清空重建，业务数据是否完好？"

# =========================================================================== 五、部署与资源

#: K8s 三区（原文第五章「平台部署于 K8s，按「三区 + 托管依赖」组织」）
DEPLOY_ZONE_COUNT: Final[int] = 3
DEPLOY_ZONES: Final[tuple[tuple[str, str, str], ...]] = (
    (
        "在线服务区",
        "控制台、网关、检索 / 任务 / 标签 / 审核服务",
        "每服务 4C8G × 2 起，无状态 + HPA 随检索 QPS 扩缩",
    ),
    ("计算引擎区", "Spark / Flink 执行器池、抽帧作业", "批处理窗口期弹性扩容，跑完即释放"),
    ("GPU 资源池", "Ray 集群（VLM 推理 vLLM / Embedding）", "分时复用 + 优先级队列 + 弹性扩缩"),
)

#: 在线服务区规格「每服务 4C8G × 2 起」（原文第五章表格）
ONLINE_SERVICE_CPU_CORES: Final[int] = 4
ONLINE_SERVICE_MEMORY_GB: Final[int] = 8
ONLINE_SERVICE_MIN_REPLICAS: Final[int] = 2

#: Embedding 走凌晨窗口，凌晨 6 点前完成（原文第五章「Embedding 走凌晨窗口（凌晨 6 点前完成）」）
EMBEDDING_WINDOW_DEADLINE_HOUR: Final[int] = 6

# =========================================================================== 六、OpenAPI 出口

#: 接口分四组（原文第六章「接口分四组，每组都对应一个明确的业务动作」）
OPENAPI_GROUP_COUNT: Final[int] = 4
#: 对外接口版本（原文给出的路径均为 /api/v1/...）
API_VERSION: Final[str] = "v1"
API_PREFIX: Final[str] = "/api/v1"

#: 原文逐字给出的接口路径（第六章表格）
PATH_SEMANTIC_SEARCH: Final[str] = "/api/v1/scene/semantic-search"
PATH_TAG_COVERAGE: Final[str] = "/api/v1/scene/tag-coverage"
PATH_RULE_JOBS: Final[str] = "/api/v1/mining/rule-jobs"
PATH_JOB_PROGRESS: Final[str] = "/jobs/{jobId}/progress"
PATH_SCENE_CURATE: Final[str] = "/api/v1/scene/curate"
PATH_DATASET_EXPORT: Final[str] = "/datasets/{id}/export"

#: 权限复用湖仓四级管控（原文第六章「统一登录，权限复用湖仓四级管控」）
LAKEHOUSE_PERMISSION_LEVELS: Final[int] = 4

#: 命中结果凭 backfill_dataset_id 回补（原文第六章系统集成段，字段名逐字）
BACKFILL_DATASET_ID_FIELD: Final[str] = "backfill_dataset_id"

# =========================================================================== ⚠️ 本项目补充的数值

# 以下数字原文完全没有给出，是让这套控制面真正跑起来所必需的工程参数。
# 全部标注「⚠️ 原文未明确，本项目设计」，不要把它们当成原文方案引用。

#: ⚠️ 原文未明确，本项目设计：幂等键（原文只说「幂等键防重复提交」，没给保留期）保留 7 天。
#: 取 7 天是因为批处理任务的重试与人工复核窗口通常落在一周内。
IDEMPOTENCY_KEY_TTL_SECONDS: Final[int] = 7 * 24 * 3600

#: ⚠️ 原文未明确，本项目设计：Redis 检索热点缓存 TTL 300 秒。
SEARCH_HOTSPOT_CACHE_TTL_SECONDS: Final[int] = 300
#: ⚠️ 原文未明确，本项目设计：Redis 标签字典热缓存 TTL 600 秒（字典变更频率远低于检索）。
TAG_DICT_CACHE_TTL_SECONDS: Final[int] = 600

#: ⚠️ 原文未明确，本项目设计：控制面轮询数据面作业状态的间隔（秒）。
POLL_INTERVAL_SECONDS: Final[int] = 30
#: ⚠️ 原文未明确，本项目设计：单次调度循环最多下发的任务数，防止打爆引擎队列。
DISPATCH_BATCH_SIZE: Final[int] = 50
#: ⚠️ 原文未明确，本项目设计：失败任务最大自动重试次数。
MAX_AUTO_RETRY: Final[int] = 3
#: ⚠️ 原文未明确，本项目设计：任务在 RUNNING 状态的超时上限（秒），超时置 FAILED 等待重试。
TASK_TIMEOUT_SECONDS: Final[int] = 6 * 3600

#: ⚠️ 原文未明确，本项目设计：任务与审核动作「定期回写」的周期（原文只说「定期」）。
#: 取 300 秒——既让血缘接近实时，又不至于把湖仓小文件打碎。
WRITEBACK_INTERVAL_SECONDS: Final[int] = 300
#: ⚠️ 原文未明确，本项目设计：单批回写的最大行数。
WRITEBACK_BATCH_ROWS: Final[int] = 1000

#: ⚠️ 原文未明确，本项目设计：OpenAPI 网关限流（原文只说网关做「认证 / 限流 / 审计」）。
GATEWAY_RATE_LIMIT_QPS: Final[int] = 200
#: ⚠️ 原文未明确，本项目设计：单次检索返回的最大条数。
SEARCH_MAX_TOP_K: Final[int] = 1000

#: ⚠️ 原文未明确，本项目设计：任务优先级取值区间（数值越小越优先），供 GPU 优先级队列复用。
PRIORITY_HIGHEST: Final[int] = 0
PRIORITY_DEFAULT: Final[int] = 5
PRIORITY_LOWEST: Final[int] = 9


__all__ = [name for name in dir() if name.isupper()]
