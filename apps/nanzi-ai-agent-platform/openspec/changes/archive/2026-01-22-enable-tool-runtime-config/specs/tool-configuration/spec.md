# 功能规范：工具配置 (Tool Configuration)

## 新增需求

### 运行时模型覆盖 (Runtime Model Override)

智能体中的每个工具都可以独立配置其运行时的 LLM 模型和温度，如果未配置，则默认继承智能体的主配置。

#### 场景：SQL 生成工具使用专用模型
*   **假设**：智能体主模型配置为 `DeepSeek-V3`（擅长对话），但关联了一个 `execute_sql_query` 工具。
*   **当**：用户在工具配置界面为 `execute_sql_query` 指定模型为 `DeepSeek-Coder`。
*   **那么**：当智能体决定调用 `execute_sql_query` 时，该工具内部的逻辑（如果是 LLM 驱动的）应使用 `DeepSeek-Coder` 执行，而智能体的其他思考和总结过程仍使用 `DeepSeek-V3`。

### 统一配置界面 (Unified Configuration UI)
工具配置界面应整合业务参数（如知识库 ID）和运行时参数（模型、温度）。

#### 场景：配置知识库搜索工具
*   **当**：用户点击知识库搜索工具的“配置”按钮。
*   **那么**：弹出的模态框应包含两个标签页：
    1.  **业务参数**：设置 Dataset ID, Top K, Threshold 等。
    2.  **运行时配置**：设置执行模型和温度。
