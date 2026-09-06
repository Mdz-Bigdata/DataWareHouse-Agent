-- 需求：参数设置新增 ChatBI 案例库检索 Top K 数量限制，分类为 data_api
-- 创建人：Antigravity
-- 创建时间：2026-06-13

INSERT IGNORE INTO `system_configs` (`key`, `value`, `description`, `category`, `is_secret`) VALUES
('chatbi_sample_top_k', '5', 'ChatBI 优质案例相似度检索返回的最大条数', 'data_api', 0);
