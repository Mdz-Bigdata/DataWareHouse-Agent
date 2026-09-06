-- 需求描述：会话历史表记录单次对话的输入/输出 Token，供看板按 history 聚合统计。
-- 变更目的：为 ai_agent_execution_history 新增 prompt_tokens、completion_tokens，与 traces 步骤级字段语义一致；历史行默认为 0，不做回填。
-- 创建时间：2026-05-31

ALTER TABLE `ai_agent_execution_history`
ADD COLUMN `prompt_tokens` INT(11) NOT NULL DEFAULT 0 COMMENT '输入 Token 消耗数' AFTER `summary`,
ADD COLUMN `completion_tokens` INT(11) NOT NULL DEFAULT 0 COMMENT '输出 Token 消耗数' AFTER `prompt_tokens`;
