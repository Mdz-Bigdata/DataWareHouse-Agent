# 任务列表：支持工具级运行时配置

- [ ] **后端：模型更新 (Schemas)**
    - [ ] 在 `app/schemas/agent.py` 中定义 `ToolConfigItem`。
    - [ ] 修改 `AIAgentVersionBase` 和 `ChatConfig` 中的 `tools` 类型，支持 `Union[str, ToolConfigItem]`。
- [ ] **后端：逻辑实现 (Registry & Executor)**
    - [ ] 更新 `app/services/ai/tools/registry.py` 中的 `get_tools` 方法，支持解析 `ToolConfigItem`。
    - [ ] 实现工具配置注入逻辑，使得工具实例能获取到覆盖的模型配置。
    - [ ] 在 `AgentConfigProvider` 中支持传入特定的覆盖参数来获取 LLM 实例。
- [ ] **前端：UI 开发 (Vue)**
    - [ ] 修改智能体编辑页面的工具列表组件。
    - [ ] 更新工具配置弹窗，添加“运行时配置”标签页（模型选择、温度调节）。
    - [ ] 确保前端能正确序列化并保存 `ToolConfigItem` 格式的数据。
- [ ] **测试与验证**
    - [ ] 编写测试用例验证带有模型覆盖的工具调用。
    - [ ] 验证未配置覆盖时工具仍使用智能体默认模型。
- [ ] **文档与清理**
    - [ ] 更新 `tests/CHECKLIST.md` 中的自动化测试清单。
