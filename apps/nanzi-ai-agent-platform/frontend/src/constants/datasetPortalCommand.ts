/** 数据门户系统 slash 指令（用户输入与 quick 按钮） */
export const DATASET_PORTAL_SLASH_COMMAND = "/dataset_portal";

/** 旧指令，兼容历史会话与已缓存门户 markdown */
export const DATASET_PORTAL_LEGACY_SLASH_COMMAND = "/dataset_menu";

export const DATASET_PORTAL_SYSTEM_COMMAND_ID = "sys_dataset_portal";

export function isDatasetPortalSlashCommand(cmd: string): boolean {
  const normalized = String(cmd || "").trim();
  return (
    normalized === DATASET_PORTAL_SLASH_COMMAND
    || normalized === DATASET_PORTAL_LEGACY_SLASH_COMMAND
  );
}

export const KNOWLEDGE_PORTAL_SLASH_COMMAND = "/knowledge_portal";
export const KNOWLEDGE_PORTAL_SYSTEM_COMMAND_ID = "sys_knowledge_portal";

export function isKnowledgePortalSlashCommand(cmd: string): boolean {
  const normalized = String(cmd || "").trim();
  return normalized === KNOWLEDGE_PORTAL_SLASH_COMMAND;
}

