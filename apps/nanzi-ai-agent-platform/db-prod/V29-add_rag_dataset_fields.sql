-- V29: 为 meta_datasets 表增加 RAGFlow 关联字段
-- Created at: 2026-01-19

ALTER TABLE `meta_datasets` 
ADD COLUMN `rag_dataset_id` VARCHAR(64) NULL DEFAULT NULL COMMENT 'RAGFlow 侧对应的 Dataset ID' AFTER `status`,
ADD COLUMN `rag_synced_at` DATETIME NULL DEFAULT NULL COMMENT '最后同步到 RAGFlow 的时间' AFTER `rag_dataset_id`,
ADD COLUMN `rag_sync_status` TINYINT(4) DEFAULT 0 COMMENT 'RAGFlow 同步状态 (0:未同步, 1:同步中, 2:已同步, -1:同步失败)' AFTER `rag_synced_at`,
ADD COLUMN `rag_sync_notes` TEXT NULL COMMENT 'RAGFlow 同步日志/错误信息' AFTER `rag_sync_status`;

-- 为新字段添加索引以优化查询
CREATE INDEX `idx_meta_rag_status` ON `meta_datasets` (`rag_sync_status`);
