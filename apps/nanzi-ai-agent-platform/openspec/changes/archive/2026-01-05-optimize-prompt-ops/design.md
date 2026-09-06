# 设计文档: 统一 Prompt 管理平台 (Unified Prompt Ops Design)

## Context (背景)
目前 Prompt 分散在 `system_configs` (单版本, Key-Value) 和 `ai_agent_versions` (多版本, 关联 Agent) 中。用户希望能在一个统一的界面管理它们，并关心如何区分来源以及版本控制机制。

## Architecture (架构)

### 1. 统一数据模型 (Unified Data Model)
后端 `PromptService` 将屏蔽底层存储差异，向前端提供统一的 `PromptItem` 结构：

```python
class PromptSource(Enum):
    SYSTEM_CONFIG = "system_config"  # 来源: system_configs 表
    AGENT = "agent"                  # 来源: ai_agent_versions 表

class PromptMetadata(BaseModel):
    id: str                 # 唯一标识 (config_key 或 agent_id)
    name: str               # 显示名称 (如 "Intent Engine" 或 "ChatBI Agent")
    source: PromptSource    # 来源类型
    category: str           # 分组 (System / Agent)
    description: str
    
    # 关联信息
    target_key: Optional[str] = None   # if source=system_config
    agent_id: Optional[str] = None     # if source=agent

    # 版本信息
    versions: List[PromptVersionSummary] # 版本列表
```

### 2. 关联机制 (Association Strategy)
系统通过**注册表模式 (Registry Pattern)** 来维护 Prompt 与实际业务实体的关联。

*   **System Prompts (系统级)**:
    *   通过硬编码的映射表 (Registry) 关联。
    *   例如: `intent_recognition` -> `system_configs.intent_recognition_prompt`。
    *   在代码中维护这个 Registry，确保新增系统配置时能自动出现在列表中。

*   **Agent Prompts (智能体级)**:
    *   动态查询 `ai_agents` 表。
    *   每个 Agent 自动对应一个 Prompt 条目。

### 3. 版本控制策略 (Versioning Strategy)

由于底层存储机制不同，V1 版本将采用差异化处理：

*   **智能体 Prompt (Agent Prompts)**:
    *   **完全支持版本控制**。
    *   利用现有的 `ai_agent_versions` 表。
    *   支持 `DRAFT` (草稿) 和 `PUBLISHED` (发布) 状态。
    *   UI 操作: 可以 "Save as New Version" 或 "Update Draft"。

*   **系统级 Prompt (System Prompts)**:
    *   **单版本 (Current Only)**。
    *   直接读写 `system_configs` 表。
    *   *Future Work*: 未来可引入 `system_config_history` 表来实现版本回溯，但本期暂不涉及 Schema 变更。

## API Design
- `GET /api/portal/prompts`: 获取所有 Prompt 元数据列表。
- `GET /api/portal/prompts/{id}/versions`: 获取特定 Prompt 的所有版本。
- `GET /api/portal/prompts/{id}/content?version=...`: 获取具体内容。
- `POST /api/portal/prompts/{id}/test`: 运行 Playground 测试。
- `POST /api/portal/prompts/{id}/save`: 保存 (System直接更新, Agent可选择更新草稿或发布新版)。
