"""门禁全部数值常量的唯一出处。

来源：系列二 · 湖仓实战 第 6 篇《数据质量门禁设计：智驾数据入湖的五步校验链路》
（公众号「小周谈智驾数据闭环」，2026-09-05，
https://mp.weixin.qq.com/s/e7lf3LrjX9JMHMvVnu4Anw）。

原则：原文出现过的每一个数字都在这里逐字落地，并在注释里标注它在原文的位置，
禁止在业务代码里再写裸数字。原文没给的数字一律标注
「⚠️ 原文未明确，本项目设计：」，不冒充原文方案。
"""

from __future__ import annotations

from typing import Final

# --------------------------------------------------------------------------- 原文数字
# 六、门禁运维 · 「两类核心指标」表
#: rejected_records_count：被拒绝的记录数，> 100 触发 WARNING（原文第六章指标表）
REJECTED_RECORDS_COUNT_THRESHOLD: Final[int] = 100
#: quality_check_duration：质量检查耗时（毫秒），> 1000 触发 WARNING（原文第六章指标表）
QUALITY_CHECK_DURATION_MS_THRESHOLD: Final[int] = 1000

# 四、分源规则细化 · 4.2 Kafka 消息流
#: 事件 ID 幂等去重后，重复率 > 5% 告警（原文 4.2「重复率监控」）
DUPLICATE_RATE_ALERT_THRESHOLD: Final[float] = 0.05

# 四、分源规则细化 · 4.3 OSS 采集文件
#: 同一帧组内多模态文件齐全；连续丢帧率 > 1% 升级告警（原文 4.3「多模态完整性」）
CONTINUOUS_FRAME_LOSS_RATE_ESCALATE_THRESHOLD: Final[float] = 0.01
#: 采集车硬件同步 ≤ ±10ms（原文 4.3「时间同步」）
COLLECT_VEHICLE_SYNC_TOLERANCE_MS: Final[int] = 10
#: 量产车软同步 ≤ ±50ms（原文 4.3「时间同步」）
PRODUCTION_VEHICLE_SYNC_TOLERANCE_MS: Final[int] = 50

# 五、五步异常闭环 · ⑤ 复验重入湖
#: 复验不通过退回隔离，超 3 轮升级 P0（原文第五章第 ⑤ 步）
MAX_RECHECK_ROUNDS: Final[int] = 3

# 五、五步异常闭环 · 四档响应 SLA 表
#: P0 合规级：电话 + 钉钉，30 分钟内响应
P0_RESPONSE_MINUTES: Final[int] = 30
#: P1 严重：钉钉 + 工单，2 小时内响应，当日修复
P1_RESPONSE_HOURS: Final[int] = 2
#: P1 严重：当日修复（0 = 当天之内，不跨日）
P1_FIX_WITHIN_DAYS: Final[int] = 0
#: P2 一般：日报汇总，3 个工作日内闭环
P2_CLOSE_BUSINESS_DAYS: Final[int] = 3
#: P3 观察：周报汇总，连续两周超标升级 P2
P3_CONSECUTIVE_WEEKS_TO_ESCALATE: Final[int] = 2

# --------------------------------------------------------------------------- 本项目补充
# 以下阈值原文只给了占位符或定性描述，本项目给出可配置默认值，全部可被 YAML 覆盖。

#: ⚠️ 原文未明确，本项目设计：原文 4.2「片段截断」写的是「触发前 ≥ N 秒 / 后 ≥ M 秒」，
#: N/M 是占位符没有给数。这里取 5.0 秒作为可配置默认值，真实取值应由各项目在
#: quality_rules.yaml 里按触发类型覆盖。
TRIGGER_PRE_SECONDS_DEFAULT: Final[float] = 5.0
#: ⚠️ 原文未明确，本项目设计：同上，M 的默认值。
TRIGGER_POST_SECONDS_DEFAULT: Final[float] = 5.0

#: ⚠️ 原文未明确，本项目设计：原文 4.2「时间戳合理性」只说「不晚于服务器时间 + 容忍窗口
#: （防车端时钟漂移）」，没给窗口大小。取 300 秒（5 分钟）作为默认容忍窗口。
CLOCK_DRIFT_TOLERANCE_SECONDS: Final[float] = 300.0

#: ⚠️ 原文未明确，本项目设计：原文第六章「按表灰度发布新规则：新规则先在单表小流量
#: 试运行」，没给「小流量」的比例。取 1% 作为灰度默认抽样比例。
GREY_SAMPLE_RATIO_DEFAULT: Final[float] = 0.01
#: ⚠️ 原文未明确，本项目设计：灰度期「监控误杀率」的转正门槛，取 1%——
#: 灰度窗口内误杀率高于此值不允许全量发布。
GREY_MISFIRE_RATE_THRESHOLD: Final[float] = 0.01
#: ⚠️ 原文未明确，本项目设计：灰度转正前至少要积累的样本数，避免 0/1 样本就下结论。
GREY_MIN_SAMPLES_BEFORE_PROMOTION: Final[int] = 100

#: ⚠️ 原文未明确，本项目设计：一个工作日按 8 小时折算，用于 P2「3 个工作日内闭环」
#: 的 SLA 到期时间计算（本实现只做自然日近似，不接企业节假日日历）。
BUSINESS_HOURS_PER_DAY: Final[int] = 8

#: ⚠️ 原文未明确，本项目设计：近重复抑制的相似度阈值（原文 L2「近重复抑制」、
#: L5「近重复」只给了动作没给阈值）。0.98 指向「几乎同一帧」。
NEAR_DUPLICATE_SIMILARITY_THRESHOLD: Final[float] = 0.98

#: ⚠️ 原文未明确，本项目设计：及时性维度「入湖延迟」的默认告警阈值（秒）。
#: 原文第二章把及时性定为「监控告警（不拦截）」但未给数字。
INGEST_LATENCY_ALERT_SECONDS: Final[float] = 900.0

#: 隔离表原始报文的截断长度（字节）。⚠️ 原文未明确，本项目设计：
#: 原始报文必须完整保留才能重放，但超大文件型报文只存元信息 + 对象存储 key。
RAW_PAYLOAD_INLINE_MAX_BYTES: Final[int] = 1_048_576  # 1 MiB


__all__ = [
    "REJECTED_RECORDS_COUNT_THRESHOLD",
    "QUALITY_CHECK_DURATION_MS_THRESHOLD",
    "DUPLICATE_RATE_ALERT_THRESHOLD",
    "CONTINUOUS_FRAME_LOSS_RATE_ESCALATE_THRESHOLD",
    "COLLECT_VEHICLE_SYNC_TOLERANCE_MS",
    "PRODUCTION_VEHICLE_SYNC_TOLERANCE_MS",
    "MAX_RECHECK_ROUNDS",
    "P0_RESPONSE_MINUTES",
    "P1_RESPONSE_HOURS",
    "P1_FIX_WITHIN_DAYS",
    "P2_CLOSE_BUSINESS_DAYS",
    "P3_CONSECUTIVE_WEEKS_TO_ESCALATE",
    "TRIGGER_PRE_SECONDS_DEFAULT",
    "TRIGGER_POST_SECONDS_DEFAULT",
    "CLOCK_DRIFT_TOLERANCE_SECONDS",
    "GREY_SAMPLE_RATIO_DEFAULT",
    "GREY_MISFIRE_RATE_THRESHOLD",
    "GREY_MIN_SAMPLES_BEFORE_PROMOTION",
    "BUSINESS_HOURS_PER_DAY",
    "NEAR_DUPLICATE_SIMILARITY_THRESHOLD",
    "INGEST_LATENCY_ALERT_SECONDS",
    "RAW_PAYLOAD_INLINE_MAX_BYTES",
]
