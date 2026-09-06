# 规范：LLM 提供商能力 (LLM Provider Capability)

## ADDED Requirements

### Requirement: 异步调用支持 (Async Injection)
LLM 客户端 **MUST** 支持异步 (Async/Await) 调用，并兼容 OpenAI Chat Completions 协议。

#### Scenario: 高并发请求处理
系统在高并发环境下调用 LLM 时，不应由于模型响应缓慢导致整个 API 服务夯住。

### Requirement: 鲁棒性与失败处理 (Robustness)
系统 **MUST** 实现针对 LLM Gateway 的超时 (Timeout) 和重试 (Retry) 机制。

#### Scenario: 网络抖动自动恢复
当 LLM Gateway 物理抖动时，系统应自动进行最多 3 次指数退避重试。

### Requirement: 环境参数管理 (Environment Configuration)
模型参数 **MUST** 支持通过环境变量或后端配置项进行管理。

#### Scenario: 多环境参数覆盖
部署在测试环境和生产环境时，应能够通过不同的 `LLM_API_KEY` 访问对应的模型实例。
