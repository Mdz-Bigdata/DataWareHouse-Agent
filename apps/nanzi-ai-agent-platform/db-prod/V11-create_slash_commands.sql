CREATE TABLE IF NOT EXISTS `slash_commands` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `label` VARCHAR(50) NOT NULL COMMENT '显示名称 (e.g., ⚠️ 异常告警)',
    `command` TEXT NOT NULL COMMENT '执行指令内容',
    `sort_order` INT DEFAULT 0 COMMENT '排序权重',
    `created_by` VARCHAR(64) DEFAULT 'system',
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='调试快捷指令表';

-- 预置初始数据
INSERT INTO `slash_commands` (`label`, `command`, `sort_order`) VALUES
('🏢 机房列表', '查一下所有机房的列表', 10),
('⚠️ 异常告警', '最近24小时有哪些异常告警？', 20),
('📊 PUE统计', '统计华东一号机房昨天的 PUE 指标', 30),
('📈 事件对比', '对比华东和华南机房最近的事件数量', 40),
('请假申请', '我要请假', 50);