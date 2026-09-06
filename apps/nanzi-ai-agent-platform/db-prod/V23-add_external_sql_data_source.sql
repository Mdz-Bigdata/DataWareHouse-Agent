-- V23: Add external_sql_data_source configuration (Corrected)
-- Date: 2026-01-08
-- Description: Adds the configuration key for the external SQL data source ID.

INSERT IGNORE INTO system_configs (`key`, `value`, `description`, `category`, `is_secret`)
VALUES ('external_sql_data_source', 'default_clickhouse', '外部 SQL 执行数据源 ID (Data Source ID)', 'data_api', 0);