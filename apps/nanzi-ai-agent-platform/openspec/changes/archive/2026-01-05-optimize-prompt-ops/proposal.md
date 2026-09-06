# Proposal: 统一 Prompt 管理平台 (Unified Prompt Ops)

## 背景 (Why)
目前的 Prompt 管理存在割裂和调试困难的问题：
1.  **存储分散**: 全局 Prompt（如意图识别、路由）存储在 `system_configs` 表，而 Agent Prompt 存储在 `ai_agent_versions` 表。
2.  **维护困难**: 没有统一的界面来查看和修改这些 Prompt，通常需要直接操作数据库或使用不同的脚本。
3.  **缺乏调试环境**: 修改 Prompt 后，无法快速验证效果。必须运行完整的对话流程才能看到变化，反馈周期长。
4.  **变量不可视**: Prompt 中包含的动态变量（如 `{agents_context}`, `{history_context}`）在静态文本中无法直观看到实际注入的内容。

## 目标 (Goals)
构建一个统一的 **Prompt Studio**（提示词工作室），实现 Prompt 的集中管理、在线编辑和即时调试。

## 变更内容 (What Changes)

### 1. 统一管理 API (Unified Prompt API)
创建一个新的 API 层，聚合不同来源的 Prompt，提供统一的读写接口。
- **Source A**: `system_configs` (Intent, Router)
- **Source B**: `ai_agent_versions` (Agents)
- **API**: `GET /api/portal/prompts`, `POST /api/portal/prompts/test`

### 2. Prompt Studio (Frontend)
在管理后台新增 "Prompt Engineering" 模块：
- **Prompt 列表**: 统一展示系统级和 Agent 级 Prompt。
- **在线编辑器**: 支持 Markdown 高亮，直接更新数据库。
- **变量自动识别**: 自动解析 Prompt 中的 `{variable}` 占位符。
- **Playground (调试器)**: 
    - 允许用户为 `{variable}` 输入测试数据（Mock Context）。
    - 实时调用 LLM 生成结果，对比 Prompt 修改前后的效果。

### 3. 系统级 Prompt 迁移 (Optional but Recommended)
虽然目前 `system_configs` 支持 Prompt，但为了长期维护，建议在 UI 上对其进行特殊标记，或者在未来统一迁移到更专业的配置表。本阶段暂保持存储结构不变，仅做 UI 聚合。

## 影响 (Impact)
- **Backend**: 新增 `app/api/portal/prompts.py` 及相关 Service 逻辑。
- **Frontend**: 新增 `views/PromptStudio.vue` 及相关组件。
- **Ops**: 极大降低 Prompt 调优的门槛，非研发人员也可参与调优。

## Affected Specs
- `specs/prompt-ops` (New Capability)
