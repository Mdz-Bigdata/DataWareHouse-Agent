# 配置编辑器 AI 提示词润色设计

## 目标

在新建智能体、新建配置、编辑草稿配置的 System Prompt 编辑区提供与「提示词工坊」一致的「AI 润色」能力。

## 方案

- 复用既有接口 `POST /api/portal/prompts/optimize` 与权限 `element:prompts:optimize`。
- 抽出 `PromptAiOptimize.vue`：确认 → 调用 → 多方案预览 → 应用。
- `MarkdownEditor` 增加可选 `enableOptimize`；智能体版本编辑抽屉开启该开关。
- 应用结果写回当前 `system_prompt` 草稿，不自动发布。

## 非目标

- 不改动润色后端算法。
- 本次不强制重构 PromptStudio 复用同一组件（可后续收敛）。
