export const SQL_TOOL_RESULT_DELIMITER = "--- 结果 ---";
export const SQL_TOOL_ERROR_DELIMITER = "--- 错误 ---";

export type SqlToolLogBodyKind = "result" | "error";

export type SqlToolLogSections = {
  sqlPart: string;
  bodyPart: string;
  bodyKind: SqlToolLogBodyKind;
  trailingPart: string;
};

const SQL_TOOL_BODY_DELIMITERS: Array<{ delimiter: string; bodyKind: SqlToolLogBodyKind }> = [
  { delimiter: SQL_TOOL_RESULT_DELIMITER, bodyKind: "result" },
  { delimiter: SQL_TOOL_ERROR_DELIMITER, bodyKind: "error" },
];

/** 拆分 ChatBI execute_sql_query 日志：SQL / 结果或错误 / [系统检测] 后缀 */
export function splitSqlToolLogDetails(details: string): SqlToolLogSections | null {
  for (const { delimiter, bodyKind } of SQL_TOOL_BODY_DELIMITERS) {
    const delimIdx = details.indexOf(delimiter);
    if (delimIdx === -1) continue;

    const sqlPart = details.slice(0, delimIdx).trim();
    const after = details.slice(delimIdx + delimiter.length);
    const detectIdx = after.search(/\n\n\[系统检测\]/);

    if (detectIdx >= 0) {
      return {
        sqlPart,
        bodyPart: after.slice(0, detectIdx).trim(),
        bodyKind,
        trailingPart: after.slice(detectIdx).trim(),
      };
    }

    return {
      sqlPart,
      bodyPart: after.trim(),
      bodyKind,
      trailingPart: "",
    };
  }

  return null;
}

export function sqlToolLogBodyLabel(bodyKind: SqlToolLogBodyKind): string {
  return bodyKind === "error" ? "Error" : "Query Result";
}

export function isSqlLikeToolLogDetails(details: string): boolean {
  return (
    details.includes("SELECT ")
    || details.includes("[Executed SQL]:")
    || details.includes("SQL:")
  );
}

export type ToolLogLike = {
  name?: string;
  title?: string;
  details?: string;
  status?: string;
  rowFilterApplied?: boolean;
};

export type MessageWithToolLogs = {
  logs?: ToolLogLike[];
};

const SQL_RESULT_ROW_KEYS = new Set(["items", "rows", "data", "list", "result", "records"]);

function extractResultRowLists(parsed: unknown, depth = 0): unknown[][] {
  if (depth > 4) return [];
  if (Array.isArray(parsed)) return [parsed];
  if (!parsed || typeof parsed !== "object") return [];

  const rowLists: unknown[][] = [];
  for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
    if (!SQL_RESULT_ROW_KEYS.has(key)) continue;
    if (Array.isArray(value)) rowLists.push(value);
    else if (value && typeof value === "object") {
      rowLists.push(...extractResultRowLists(value, depth + 1));
    }
  }
  return rowLists;
}

/** SQL 工具日志的结果区是否包含至少一行数据 */
export function sqlToolLogResultHasDataRows(bodyPart: string): boolean {
  const text = String(bodyPart || "").trim();
  if (!text) return false;

  try {
    const parsed = JSON.parse(text) as unknown;
    const rowLists = extractResultRowLists(parsed);
    return rowLists.length > 0 && rowLists.some((rows) => rows.length > 0);
  } catch {
    return false;
  }
}

export function isExecuteSqlQueryLog(log: ToolLogLike): boolean {
  const label = `${log.name || ""} ${log.title || ""}`;
  return /execute_sql_query/i.test(label);
}

export function logHasRowFilterApplied(log: ToolLogLike): boolean {
  if (log.rowFilterApplied) return true;
  if (!isExecuteSqlQueryLog(log) || !log.details) return false;
  const sections = splitSqlToolLogDetails(log.details);
  if (!sections || sections.bodyKind !== "result") return false;
  try {
    const parsed = JSON.parse(sections.bodyPart) as { permission_notice?: { row_filter_applied?: boolean } };
    return parsed.permission_notice?.row_filter_applied === true;
  } catch {
    return false;
  }
}

function normalizeSavableSql(sqlPart: string): string {
  let sql = String(sqlPart || "").trim();
  if (sql.includes("[Executed SQL]:")) {
    sql = sql.replace(/\[Executed\s+SQL\]:\s*/i, "").trim();
  }
  return sql;
}

/** 从单条 execute_sql_query 日志解析可沉淀 SQL；无数据行时返回 null */
export function resolveSavableSqlFromLog(log: ToolLogLike | null | undefined): string | null {
  if (!log || log.status !== "success" || !isExecuteSqlQueryLog(log)) return null;

  const sections = splitSqlToolLogDetails(log.details || "");
  if (!sections || sections.bodyKind !== "result") return null;
  if (!sqlToolLogResultHasDataRows(sections.bodyPart)) return null;

  const sql = normalizeSavableSql(sections.sqlPart);
  return sql || null;
}

/** 从 Agent 消息日志中取最后一条「成功且有数据行」的 SQL */
export function resolveSavableSqlFromMessage(
  msg: MessageWithToolLogs | null | undefined,
): string | null {
  const logs = msg?.logs;
  if (!logs?.length) return null;

  for (let i = logs.length - 1; i >= 0; i -= 1) {
    const sql = resolveSavableSqlFromLog(logs[i]);
    if (sql) return sql;
  }
  return null;
}

export function canSaveGoldenReportFromMessage(
  msg: MessageWithToolLogs | null | undefined,
): boolean {
  return resolveSavableSqlFromMessage(msg) !== null;
}
