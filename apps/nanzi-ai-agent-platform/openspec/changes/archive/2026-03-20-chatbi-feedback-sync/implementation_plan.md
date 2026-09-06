# Implementation Plan: ChatBI 反馈驱动优化系统 (Phase 1)

## 1. 数据库初始化 (V50)
- 执行 `db-prod/V50-init_chatbi_feedback_system.sql`：
  - 创建 `ai_chatbi_examples` (唯一索引 `trace_id`)。
  - 创建 `ai_chatbi_example_usages`。
  - 修改 `ai_agent_execution_history` 增加 `feedback` 字段。
  - 注册系统配置 `chatbi_sample_knowledge_base`。
  - 注册权限资源：`menu:chatbi_examples`, `element:chatbi_example:audit`, `element:chatbi_example:sync`, `element:chatbi_example:delete`。

## 2. 后端开发
### 2.1 数据模型
- `app/models/chatbi_example.py`:
  - `ChatBIExample`: 存储问题、SQL、回答、反馈类型、同步状态。
  - `ChatBIExampleUsage`: 存储引用流水。

### 2.2 核心服务层 (`ExampleService`)
- 位于 `app/services/chatbi_example_service.py`。
- `create_from_feedback(trace_id, feedback_type, user_id)`:
  - 从 `ai_agent_execution_traces` 提取 `tool_name='execute_sql_query'` 且 `status='success'` 的记录。
  - 从 `tool_input` 解析 `sql`, `dataset_name`, `data_source`。
  - 根据 `dataset_name` 查找 `dataset_id`。
  - 记录不存在则创建，存在则更新。
- `sync_to_ragflow(example_id)`:
  - 格式化 Markdown。
  - 调用 `RagFlowClient.upload_document`。
- `audit_example(example_id, status)`: 状态流转逻辑。

### 2.3 API 端点
- `app/api/portal/endpoints/chat_feedback.py`: 收集反馈。
- `app/api/portal/endpoints/chatbi_examples.py`: 管理列表、审核、手动同步。
- 挂载路由到 `app/api/portal/api.py`。

## 3. 前端开发
### 3.1 菜单与路由
- `frontend/src/views/Dashboard.vue`: 在 `menuItems` 数组中插入「反馈管理」。
- `frontend/src/router/index.ts`: 注册 `/dashboard/chatbi-examples`。

### 3.2 页面实现
- `frontend/src/views/ChatBIExampleManagement.vue`:
  - 列表展示、多条件过滤。
  - 权限控制按钮显示 (`v-if="hasPerm(...)"`)。
  - SQL 语法高亮预览弹窗。

## 4. 自动化测试
- 编写 `tests/test_chatbi_feedback.py`: 模拟反馈点击 -> SQL 提取验证 -> 状态同步验证。
