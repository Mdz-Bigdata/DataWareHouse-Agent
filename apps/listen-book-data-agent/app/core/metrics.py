"""Prometheus 指标定义（Phase 0.6）。

集中声明业务指标，各业务代码 import 后调用 .labels().inc()/.observe() 即可。
通过 app/api/routers/metrics_router.py 暴露 /metrics 端点供 Prometheus 抓取。

指标说明：
- query_requests_total：查询请求总数（按结果状态 success/error 统计）
- query_phase_duration_seconds：查询各阶段耗时（plan/recall/merge/generate/validate/execute/answer）
- llm_calls_total：LLM 调用次数（按用途 generate/correct 与结果状态）
- retrieval_hits：召回命中数（按类型 column/metric/value）
- active_build_info：当前活跃知识构建版本信息（Gauge，info 模式）
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# 查询请求总数：标签 result=success|error
query_requests_total = Counter(
    "query_requests_total",
    "自然语言查询请求总数",
    ["result"],
)

# 查询阶段耗时分布（秒）：标签 phase=节点名
query_phase_duration_seconds = Histogram(
    "query_phase_duration_seconds",
    "查询各阶段耗时",
    ["phase"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)

# LLM 调用次数：标签 purpose=generate|correct, result=success|error
llm_calls_total = Counter(
    "llm_calls_total",
    "LLM 调用次数",
    ["purpose", "result"],
)

# 召回命中数：标签 kind=column|metric|value
retrieval_hits = Counter(
    "retrieval_hits_total",
    "召回命中条目数",
    ["kind"],
)

# 应用信息（info gauge），常量标签便于在 Prometheus 里区分实例/版本
app_info = Gauge(
    "app_info",
    "应用基本信息",
    ["name", "environment"],
)

# 当前活跃知识构建版本（值恒为 1，用标签携带 build_id）
active_build_info = Gauge(
    "active_build_info",
    "当前活跃知识构建版本",
    ["build_id"],
)
