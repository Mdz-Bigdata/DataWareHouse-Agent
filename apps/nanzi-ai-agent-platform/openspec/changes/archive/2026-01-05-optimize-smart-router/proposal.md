# 提案：智能路由优化 (Smart Router Optimization)

## 背景 (Context)
目前的 `RouterService` 主要基于当前单条用户输入进行意图识别和分发。然而，在多轮对话场景中，用户的意图往往依赖于上下文（例如“它具体是指什么？”）。缺乏上下文感知导致路由准确率在复杂会话中下降。

## 目标 (Goals)
增强 `RouterService` 的语义理解能力，使其能够结合历史对话上下文（Chat History）进行更精准的任务分发。

## 变更内容 (What Changes)
1.  **接口变更**: `RouterService.route()` 方法增加 `history` 参数。
2.  **Prompt 优化**: 更新路由 Prompt，使其包含最近 N 轮对话历史。
3.  **调用方更新**: `ChatService` 在调用路由时传入当前会话的历史记录。

## 影响 (Impact)
- **后端**: `app/services/ai/router_service.py`, `app/services/ai/chat_service.py`
- **Prompt**: `architech/prompts/orchestration_router.md`
