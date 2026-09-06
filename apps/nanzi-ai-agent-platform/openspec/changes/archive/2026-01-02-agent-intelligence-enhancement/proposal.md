# Change: Agent Intelligence Enhancement

## Why
目前智能体在工具调用出错时（如 SQL 语法错误、表名猜错）会直接停止并向用户道歉，缺乏“尝试修复”的能力。同时，用户无法直观看到智能体当前“脑子里”记住了哪些关键实体（如机房名、时间范围），导致交互透明度不足。

**目标**：
1. 实现工具调用错误的自愈机制，允许 Agent 在报错后修正并重试。
2. 实现上下文状态的实时提取与可视化展示。

## What Changes

### 2.1 后端自愈逻辑 (app/services/ai/agent_service.py)
- **取消硬退出**：移除 `has_critical_error` 立即 `break` 的逻辑，转而将错误反馈给 LLM。
- **错误反馈引导**：
  - 如果是 SQL 错误，提示：“SQL 执行失败，错误信息：{error}。请检查表名、字段名是否正确，或重新调用 get_dataset_schema 确认结构。”
  - 限制最大重试步数（`MAX_STEPS`），防止死循环。
- **状态提取**：
  - 在 System Prompt 中增加指令，要求模型在思考过程中识别并输出当前的上下文实体（Entity Extraction）。
  - 在流式输出中增加 `type: "context"` 消息，包含 `entities: { "room": "华东一号", "metric": "PUE" }`。

### 2.2 前端可视化 (frontend/src/views/AgentDebug.vue & Playground)
- **上下文面板**：在聊天界面侧边增加一个名为 "Current Context" 的面板。
- **交互功能**：
  - 展示提取到的实体。
  - 提供“清除”按钮，允许用户重置某个实体的记忆。

## 3. 实现计划
1. **Phase 1**: 修改后端 `agent_service.py`，实现自愈循环。
2. **Phase 2**: 增强 System Prompt，支持实体提取。
3. **Phase 3**: 前端增加 Context 展示组件。
4. **Phase 4**: 联调与测试。

## 4. 风险控制
- **Token 消耗**：重试会增加 Token 消耗，需严格控制重试次数。
- **死循环**：确保 LLM 在无法解决问题时能优雅退出。
