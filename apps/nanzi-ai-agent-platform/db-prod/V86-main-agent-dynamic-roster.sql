-- 主助手 Prompt：欢迎语专家清单改为运行时动态注入 {agent_roster}
-- 使用 LOCATE/CONCAT 替换整段能力列表，避免 SQL 字面量含 emoji 触发 utf8mb3/utf8mb4 转换错误 (#3854)
SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci;

UPDATE `ai_agent_versions`
SET `system_prompt` = CONCAT(
  SUBSTRING(`system_prompt`, 1, LOCATE('3. **能力全景展示**', `system_prompt`) - 1),
  '3. **能力全景展示**：用清晰的列表向用户介绍平台当前可调度专家智能体（**必须原样引用下方系统注入清单**，勿自行编造未列出的智能体），帮助用户快速定位需求：
{agent_roster}
',
  SUBSTRING(`system_prompt`, LOCATE('4. **引导提问**', `system_prompt`))
)
WHERE `agent_id` = 'sys-agent-chat'
  AND `system_prompt` LIKE '%3. **能力全景展示**%'
  AND `system_prompt` LIKE '%4. **引导提问**%'
  AND `system_prompt` NOT LIKE '%{agent_roster}%';
