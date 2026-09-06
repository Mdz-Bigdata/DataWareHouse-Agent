# metadata-changelog Specification

## Purpose
为元数据管理系统提供完整的变更追踪和审计能力，记录所有元数据对象的创建、更新、删除操作，支持变更前后的对比查看，确保数据变更加可追溯和透明。

## Requirements

### Requirement: 变更日志数据模型 (Changelog Data Model)
系统 MUST 提供完整的变更日志存储模型，支持所有元数据类型的变更记录。

#### Scenario: 记录数据集创建
- **WHEN** 管理员创建新的数据集 "用户分析数据集"
- **THEN** 系统在 `meta_changelog` 表中记录一条创建日志
- **AND** 记录包含操作类型 "create"、资源类型 "dataset"、资源ID、操作用户、时间戳
- **AND** 记录完整的创建内容（name、description、tags等）

#### Scenario: 记录表字段更新
- **WHEN** 管理员修改表 "用户表" 的业务术语从 "用户表" 改为 "用户信息表"
- **THEN** 系统记录更新日志
- **AND** 记录包含变更前的旧值 "用户表" 和变更后的新值 "用户信息表"
- **AND** 明确标记变更字段为 "term"

#### Scenario: 记录指标删除
- **WHEN** 管理员删除指标 "日活用户数"
- **THEN** 系统记录删除日志
- **AND** 保存被删除指标的完整信息（name、display_name、calculation_logic等）
- **AND** 标记操作类型为 "delete"

### Requirement: 变更内容对比 (Change Comparison)
系统 MUST 提供变更前后的数据对比功能，支持字段级别的差异展示。

#### Scenario: 字段级变更对比
- **WHEN** 用户查看表 "用户表" 的变更历史
- **THEN** 系统显示每次变更的详细对比
- **AND** 对于更新操作，高亮显示具体变更的字段（如 description 从 "基础用户信息" 改为 "用户基本信息"）
- **AND** 对于JSON字段（如tags、enums），展示数组内容的增删变化

#### Scenario: 复杂对象变更对比
- **WHEN** 用户查看数据集的标签变更
- **THEN** 系统对比 tags 数组的前后差异
- **AND** 清晰展示新增的标签（如 "敏感数据"）和删除的标签（如 "测试数据"）

### Requirement: 变更日志查询API (Changelog Query API)
系统 MUST 提供灵活的变更日志查询接口，支持多维度筛选和分页。

#### Scenario: 按资源查询变更历史
- **WHEN** 用户请求 `GET /api/portal/metadata/datasets/{id}/changelog`
- **THEN** 系统返回该数据集及其所有子对象（表、字段、指标、关系）的变更历史
- **AND** 按时间倒序排列，最新变更在前
- **AND** 支持分页，每页默认20条记录

#### Scenario: 按时间范围筛选
- **WHEN** 用户请求查询最近7天的变更记录
- **THEN** 系统根据 created_at 字段筛选指定时间范围内的变更
- **AND** 支持相对时间（如 "7d"）和绝对时间（如 "2024-01-01"）查询

#### Scenario: 按操作类型筛选
- **WHEN** 用户只想查看删除操作记录
- **THEN** 系统支持 operation 参数筛选（create/update/delete）
- **AND** 返回仅包含指定操作类型的变更记录

#### Scenario: 按用户筛选
- **WHEN** 管理员想查看特定用户 "张三" 的所有操作
- **THEN** 系统支持 user_id 或 user_name 参数筛选
- **AND** 返回该用户执行的所有元数据变更记录

### Requirement: 前端变更日志界面 (Changelog UI)
系统 MUST 在数据集详情页面提供变更日志标签页，提供直观的变更历史查看体验。

#### Scenario: 变更日志标签页
- **WHEN** 用户进入数据集详情页面
- **THEN** 页面显示 "基本信息"、"表和字段"、"指标"、"关系"、"变更日志" 等标签页
- **AND** "变更日志" 标签页显示该数据集相关的所有变更记录

#### Scenario: 变更记录列表展示
- **WHEN** 用户点击 "变更日志" 标签页
- **THEN** 系统显示变更记录列表
- **AND** 每条记录显示：操作时间、操作类型（创建/更新/删除）、资源类型、资源名称、操作用户
- **AND** 使用不同颜色标识操作类型（绿色=创建、蓝色=更新、红色=删除）

#### Scenario: 变更详情弹窗
- **WHEN** 用户点击某条变更记录
- **THEN** 系统弹出详情对话框
- **AND** 显示变更前后的完整数据对比
- **AND** 对于更新操作，高亮显示差异字段
- **AND** 提供变更原因（如果记录了的话）

#### Scenario: JSON字段对比展示
- **WHEN** 变更涉及JSON字段（如tags、enums、synonyms）
- **THEN** 系统使用专门的JSON对比组件
- **AND** 清晰展示数组元素的增删变化
- **AND** 对于嵌套对象，展开显示层级结构差异

### Requirement: 变更原因记录 (Change Reason Tracking)
系统 SHOULD 支持记录变更原因，提供更完整的变更上下文。

#### Scenario: 记录变更原因
- **WHEN** 管理员在删除重要指标前
- **THEN** 系统提供可选的 "变更原因" 输入框
- **AND** 管理员输入 "指标定义有误，需要重新定义" 后，该原因被保存到变更日志中
- **AND** 在查看变更历史时，其他用户能看到该变更原因

### Requirement: 变更日志数据清理 (Log Data Cleanup)
系统 SHOULD 提供变更日志的定期清理机制，避免数据无限增长。

#### Scenario: 自动清理旧日志
- **WHEN** 系统运行超过1年
- **THEN** 系统自动清理1年前的变更日志
- **AND** 保留最近3个月的详细日志，3-12个月的日志只保留摘要信息
- **AND** 提供配置参数，允许调整清理策略

#### Scenario: 手动导出日志
- **WHEN** 管理员需要永久保存某些重要变更记录
- **THEN** 系统提供日志导出功能
- **AND** 支持导出为CSV或JSON格式
- **AND** 导出内容包含完整的变更详情

## Technical Notes

### 数据模型设计
```sql
CREATE TABLE meta_changelog (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    resource_type VARCHAR(20) NOT NULL COMMENT '资源类型: dataset/table/column/metric/relationship',
    resource_id VARCHAR(50) NOT NULL COMMENT '资源ID',
    operation VARCHAR(20) NOT NULL COMMENT '操作类型: create/update/delete',
    old_data JSON COMMENT '变更前数据',
    new_data JSON COMMENT '变更后数据',
    changed_fields JSON COMMENT '变更字段列表（仅update操作）',
    user_id INT COMMENT '操作用户ID',
    user_name VARCHAR(64) COMMENT '操作用户名',
    reason TEXT COMMENT '变更原因',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_resource (resource_type, resource_id),
    INDEX idx_user (user_id),
    INDEX idx_created (created_at),
    INDEX idx_operation (operation)
);
```

### 集成点
- 在 `MetadataService` 的所有CRUD方法中添加变更日志记录
- 使用数据库事务确保数据变更和日志记录的一致性
- 提供异步日志记录机制，避免影响主业务性能

### 安全考虑
- 变更日志记录本身不应被普通用户修改或删除
- 敏感字段的变更需要特殊处理（如脱敏显示）
- 提供变更日志的访问权限控制
