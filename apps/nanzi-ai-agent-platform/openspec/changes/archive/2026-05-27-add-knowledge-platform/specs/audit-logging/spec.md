## ADDED Requirements

### Requirement: 知识库操作审计

系统 SHALL 对知识库开发平台中的关键操作记录审计日志，确保知识库内容变更和检索测试可追溯。

#### Scenario: 记录知识库创建审计

- **WHEN** 有权限的用户创建知识库
- **THEN** 系统记录审计日志
- **AND** 日志包含操作者、操作类型、知识库名称、RAGFlow Dataset ID、结果状态和时间

#### Scenario: 记录知识库删除审计

- **WHEN** 有权限的用户删除知识库
- **THEN** 系统记录审计日志
- **AND** 日志包含操作者、操作类型、目标 RAGFlow Dataset ID、结果状态和时间

#### Scenario: 记录文档管理审计

- **WHEN** 用户上传文档、删除文档或触发文档解析
- **THEN** 系统记录审计日志
- **AND** 日志包含操作者、操作类型、目标 Dataset ID、目标 Document ID 或文件名、结果状态和时间

#### Scenario: 记录检索测试审计

- **WHEN** 用户执行知识库检索测试
- **THEN** 系统记录审计日志
- **AND** 日志包含操作者、Dataset ID 列表、检索参数、query 摘要、命中数量、结果状态和时间

#### Scenario: 审计日志脱敏

- **WHEN** 知识库操作请求包含 API Key、密钥或其他敏感字段
- **THEN** 系统在审计日志中隐藏敏感字段
- **AND** RAGFlow API Key 不得被写入审计日志
