# 设计文档：集成 RAGFlow 双模支持 (Design: RAGFlow Dual-Mode)

## 1. 数据库设计 (Database Schema)

### 1.1 `agents` 表扩展
为了支持“代理模式”，需要区分 Agent 的执行引擎。
- `engine_type`: `VARCHAR(20)`，默认为 `'LOCAL'`。可选值：`'LOCAL'` (现有逻辑), `'RAGFLOW'` (直接转发逻辑)。
- `engine_config`: `JSON`，用于存储特定引擎的配置（如 RAGFlow 的 `app_id`, `dataset_ids`）。

**迁移脚本**: `db-prod/V21-add_ragflow_agent_fields.sql`

## 2. 后端逻辑设计 (Backend Logic)

### 2.1 基础适配器 (`RagFlowClient`)
- **位置**: `app/services/ai/ragflow_client.py` (改为放在 ai 目录下更合适)
- **核心功能**:
    - 封装 HTTP 请求。
    - 处理流式输出 (SSE)。
    - 解析 RAGFlow 的引用格式 (`citation`)。

### 2.2 工具实现 (`KnowledgeSearchTool`)
- **位置**: `app/services/ai/tools/knowledge_search_tool.py`
- **逻辑**:
    1. 从系统配置读取 API Key 和 URL。
    2. 调用 RAGFlow 的 `retrieval` 接口。
    3. 将结果格式化为 Markdown 列表，包含来源。

### 2.3 执行器改造 (`AgentExecutor`)
- **位置**: `app/services/ai/chat_service.py` (或相关执行类)
- **逻辑**:
    ```python
    if agent.engine_type == 'RAGFLOW':
        return await ragflow_client.chat(query, history, config=agent.engine_config)
    else:
        return await self._execute_local_chain(agent, query, history)
    ```

## 3. 前端交互设计 (Frontend UX)

### 3.1 引用展示 (Citations)
- **组件**: 在 `AgentDebug.vue` 的消息列表项中，检测消息是否含有 `citations` 数据。
- **展示形式**: 
    - 文本中使用 `[1]` 标注。
    - 底部展示“📚 参考资料”卡片，包含文档标题和匹配度。

### 3.2 智能体管理界面
- 在“编辑智能体”弹窗中，增加“执行引擎”下拉框。
- 当选择 `RAGFLOW` 时，显示“RAGFlow App ID”和“Dataset IDs”输入框。

## 4. 路由逻辑 (Router)
- 在 `RouterService` 的意图列表中增加 `knowledge_qa`。
- 更新 Prompt，使 Router 能够识别并分发到特定的知识库 Agent。
