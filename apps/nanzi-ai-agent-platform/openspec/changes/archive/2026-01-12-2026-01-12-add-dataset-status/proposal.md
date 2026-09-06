# 数据集启用/禁用状态管理 (Dataset Enable/Disable Status)

## 1. 背景与问题
目前数据集（Dataset）缺乏“启用/禁用”的状态控制。一旦创建，无论数据是否准备好或是否废弃，都会被智能体（Agent）检索到。用户希望能够手动控制数据集的可见性。

## 2. 目标
1.  **状态管理**：为数据集增加 `status` 字段（1=启用，0=禁用）。
2.  **检索过滤**：智能体在检索元数据（`get_dataset_schema`）时，自动忽略已禁用的数据集。
3.  **交互控制**：前端界面提供便捷的开关（药丸形状），允许管理员快速切换状态。

## 3. 核心变更
*   **Database**: `meta_datasets` 表新增 `status` 字段。
*   **Backend**: `MetadataService.search_datasets` 增加 `status=1` 过滤条件。
*   **Frontend**: 数据集卡片增加状态切换开关。
