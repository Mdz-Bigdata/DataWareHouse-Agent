-- ChatBI：约束 quick 追问建议必须位于回答末尾（图表之后）
-- 代码层已有 finalize_visible_reply 后处理；本脚本同步更新已发布 V8 prompt 文案。

UPDATE `ai_agent_versions`
SET `system_prompt` = REPLACE(
    `system_prompt`,
    '- **后续引导与建议 (MUST)**：在回答结束时，为了引导用户深入分析，**必须**给出 2-3 个相关的后续问题，并强制使用 `quick:` 协议 Markdown 格式输出。',
    '- **后续引导与建议 (MUST)**：在回答结束时，为了引导用户深入分析，**必须**给出 2-3 个相关的后续问题，并强制使用 `quick:` 协议 Markdown 格式输出；该区块必须位于全文最末尾，在图表与数据来源说明之后。'
),
`comment` = CONCAT(IFNULL(`comment`, ''), ' | V78: quick placement')
WHERE `agent_id` = 'sys-agent-chatbi'
  AND `status` = 'PUBLISHED'
  AND `system_prompt` LIKE '%后续引导与建议 (MUST)%'
  AND `system_prompt` NOT LIKE '%该区块必须位于全文最末尾%';

UPDATE `ai_agent_versions`
SET `system_prompt` = REPLACE(
    `system_prompt`,
    '- 是否给出了用户能理解和采取行动的业务结论。',
    '- 是否给出了用户能理解和采取行动的业务结论。\n- quick 追问按钮是否位于全文最后（图表之后）。'
),
`comment` = CONCAT(IFNULL(`comment`, ''), ' | V78: quick placement checklist')
WHERE `agent_id` = 'sys-agent-chatbi'
  AND `status` = 'PUBLISHED'
  AND `system_prompt` LIKE '%是否给出了用户能理解和采取行动的业务结论%'
  AND `system_prompt` NOT LIKE '%quick 追问按钮是否位于全文最后%';
