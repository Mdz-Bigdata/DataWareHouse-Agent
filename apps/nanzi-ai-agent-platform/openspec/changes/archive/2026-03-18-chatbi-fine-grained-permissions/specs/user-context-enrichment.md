# 规范：用户上下文增强 (User Context Enrichment)

## 1. 核心目标
本规范定义了系统如何从 `ai_agent_users` 表中提取并向智能体传递用户的业务维度信息（如 `dept_id`, `org_path`），作为权限校验的输入。

## 2. 数据结构准则 (Data Structures)

### 2.1 用户表字段扩展 (ai_agent_users)
- **`dept_id`**: (Integer) 用户所属部门的唯一标识符。
- **`org_path`**: (String) 用户的组织结构全路径（如 `yovole/sh/dc1`），用于支持分级汇总查询（`LIKE 'yovole/sh/%'`）。

### 2.2 维度提取逻辑 (Dimension Fetching)
- 在每次会话（Session）或请求（Request）开始时，系统 **MUST** 从数据库中根据 `user_id` 预加载当前用户的维度数据。
- 维度数据 **MUST** 被封装到 `AgentContext.user_dimensions` 字典中。

## 3. 会话注入准则 (Session Injection)

### 3.1 变量映射 (Mapping)
- 系统 **MUST** 支持以下核心变量的动态注入：
    - `{user.id}`: `ai_agent_users.id`
    - `{user.dept_id}`: `ai_agent_users.dept_id`
    - `{user.org_path}`: `ai_agent_users.org_path`
    - `{user.role}`: `ai_agent_users.role` (admin/user)

### 3.2 变量缓存
- 为了性能，维度数据 **MAY** 在单个请求的生命周期内被缓存，但不应长期跨会话缓存，以防权限变更不及时生效。

## 4. 验收标准 (Acceptance Criteria)

### 场景 A：部门 ID 传递
- **GIVEN**: 用户 101 的 `dept_id` 为 500
- **WHEN**: 触发 ChatBI 请求
- **THEN**: `AgentContext` 中 **MUST** 包含 `user_dimensions: {"dept_id": 500}`

### 场景 B：管理员豁免
- **GIVEN**: 用户角色的 `role` 为 'admin'
- **WHEN**: 执行权限判定
- **THEN**: 系统 **MUST** 在 `user_dimensions` 中包含 `is_admin: true` 标记。
