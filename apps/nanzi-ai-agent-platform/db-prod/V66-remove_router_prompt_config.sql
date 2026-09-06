-- V66: 移除数据库中的智能路由提示词配置 (router_system_prompt)
-- Date: 2026-05-30
-- Purpose:
-- 路由提示词已内置到代码 RouterService.DEFAULT_SYSTEM_PROMPT，运行时不再从数据库读取，
-- 也不再在"提示词管理"中暴露。此处清理历史遗留的 system_configs 配置及其变更历史，
-- 避免运营在配置页误改导致路由失准。
-- 影响: 删除后路由完全使用代码内置提示词；V15/V1 等历史迁移保留不动（幂等，本迁移负责收尾清理）。

DELETE FROM `system_configs` WHERE `key` = 'router_system_prompt';
DELETE FROM `system_config_history` WHERE `config_key` = 'router_system_prompt';
