-- =====================================================================
-- 变更原因：支持全景链路追踪（Observability & Trace Span），对嵌套调用和步骤进行父子节点层级计时审计。
-- 需求背景：联邦查询、链路追踪与数据透视画布集成开发
-- 创建时间：2026-06-20
-- 创建人：Antigravity Agent
-- 注意事项：该脚本仅做生成，不得由 Agent 自动执行，由用户手动导入更新到数据库。
-- =====================================================================

-- 为 ai_agent_execution_traces 表增加 span 级父子树层级字段及额外 JSON 元数据列
ALTER TABLE ai_agent_execution_traces 
ADD COLUMN span_id VARCHAR(64) NULL COMMENT '当前 Span 唯一标识',
ADD COLUMN parent_span_id VARCHAR(64) NULL COMMENT '父级 Span 唯一标识',
ADD COLUMN meta_info JSON NULL COMMENT 'Token数、模型、运行细节等配置明细 JSON';

-- 创建 span_id 与 parent_span_id 的索引以加速树形递归检索
CREATE INDEX idx_span_parent ON ai_agent_execution_traces (span_id, parent_span_id);
