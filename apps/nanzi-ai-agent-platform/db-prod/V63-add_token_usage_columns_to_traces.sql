-- 需求描述：在系统执行 AI 调用（单智能体交互、ReAct 思考循环、多智能体协作等模式）时，全面审计记录输入、输出以及总 Token 消耗。
-- 变更目的：为步骤详情日志表（ai_agent_execution_traces）新增 prompt_tokens、completion_tokens、total_tokens 字段，支持步骤级的微观 Token 消耗统计，并配合前端图表（日趋势、智能体占比分布、用户算力账单等）展示。
-- 创 建 人：Antigravity
-- 创建时间：2026-05-28

ALTER TABLE `ai_agent_execution_traces` 
ADD COLUMN `prompt_tokens` INT(11) DEFAULT 0 COMMENT '输入 Token 消耗数' AFTER `temperature`,
ADD COLUMN `completion_tokens` INT(11) DEFAULT 0 COMMENT '输出 Token 消耗数' AFTER `prompt_tokens`,
ADD COLUMN `total_tokens` INT(11) DEFAULT 0 COMMENT '总 Token 消耗数' AFTER `completion_tokens`;
