-- 增加智能体版本表中的合成模型配置
ALTER TABLE ai_agent_versions 
ADD COLUMN synthesis_model_name VARCHAR(100) DEFAULT NULL COMMENT '独立合成模型名称',
ADD COLUMN synthesis_temperature FLOAT DEFAULT NULL COMMENT '独立合成模型温度';

-- 增加注释说明，主模型现在兼任编排模型
ALTER TABLE ai_agent_versions 
MODIFY COLUMN model_name VARCHAR(100) DEFAULT NULL COMMENT '主模型名称（兼任编排模型）';
