# Design: Agent Orchestration Architecture

## Overview
采用 **"Master-Sub" (Router)** 架构，将现有的各个独立智能体转化为 Master Agent 可调用的“专家工具（Expert Tools）”。

## Technical Selection
### 1. 路由策略 (Routing Strategy)
- **Primary (Semantic Router)**: 使用 Vector Embedding 计算用户提问与 Agent Description 的相关性。适合单意图、高速度要求的场景。
- **Advanced (LLM Reasoning)**: 当语义路由信心值较低时，调用轻量级 LLM (如 GPT-4o-mini) 进行多意图拆解和任务指派。

### 2. 交互协议
子智能体将被封装为标准的 `langchain` 工具。Master Agent 通过 `tool_calling` 机制激活子智能体。
- **Input**: 用户原始问题 + 必要的上下文切换。
- **Output**: 子智能体的执行结果，由 Master Agent 进行润色或作为下一步输入。

## Architecture Components
- **AgentManager**: 负责维护可用路由表，从数据库加载各 Agent 的 `description` 并构建 Embeddings。
- **OrchestratorService**: 新的服务层，封装路由逻辑。
- **Context Manager**: 维护跨 Agent 调用时的会话状态。

## Trade-offs
- **Latency**: 增加一级路由会带来额外的延迟（Embedding ~50ms, LLM Router ~500ms）。
- **Cost**: 级联调用会消耗双倍或更多的 Token。
