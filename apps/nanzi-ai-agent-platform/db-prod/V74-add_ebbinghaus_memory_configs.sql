-- 需求：系统设置新增艾宾浩斯记忆与降噪配置。
-- 创建人：Antigravity
-- 创建时间：2026-06-12

INSERT IGNORE INTO `memory_service_configs` (`key`, `value`, `description`, `is_secret`) VALUES
('memory_base_half_life', '7.0', '艾宾浩斯记忆基础半衰期（天），值越大遗忘越慢', 0),
('memory_consolidation_threshold', '0.82', '凌晨记忆降噪的向量相似度聚类阈值（0.0 ~ 1.0）', 0);
