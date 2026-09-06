-- V49: 修改 meta_datasets.status 列的默认值为 0 (禁用)
-- 说明：新建数据集默认为禁用状态，需管理员手动启用。
-- 注意：仅修改列 DEFAULT 值，不影响已有数据集的 status 字段值。
ALTER TABLE meta_datasets MODIFY COLUMN status TINYINT DEFAULT 0 COMMENT '1:启用, 0:禁用';
