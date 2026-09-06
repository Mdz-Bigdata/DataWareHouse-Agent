import type { MessageWithToolLogs } from "./toolLogDisplay";

export type RequirementAnalysisIntent = {
  goal: string;
  metrics: string[];
};

export function extractRequirementAnalysisBullet(details: string, label: string): string {
  const lines = String(details || "").split("\n");
  for (const line of lines) {
    const match = line.match(new RegExp(`^-\\s*${label}\\s*[:：]\\s*(.+)$`));
    if (match?.[1]) return match[1].trim();
  }
  return "";
}

export function splitRequirementAnalysisMetrics(raw: string): string[] {
  const text = String(raw || "").trim();
  if (!text) return [];

  const seen = new Set<string>();
  const metrics: string[] = [];
  for (const part of text.split(/[、,，/|]/)) {
    const metric = part.trim();
    if (!metric || metric.length > 32 || seen.has(metric)) continue;
    seen.add(metric);
    metrics.push(metric);
  }
  return metrics.slice(0, 6);
}

export function parseRequirementAnalysisFromDetails(details: string): RequirementAnalysisIntent | null {
  const goal = extractRequirementAnalysisBullet(details, "业务目标");
  const metrics = splitRequirementAnalysisMetrics(extractRequirementAnalysisBullet(details, "指标"));
  if (!goal && metrics.length === 0) return null;
  return { goal, metrics };
}

/** 从 Agent 消息「用户需求分析」日志中提取结构化业务意图 */
export function parseRequirementAnalysisFromMessage(
  msg: MessageWithToolLogs | null | undefined,
): RequirementAnalysisIntent | null {
  const logs = msg?.logs;
  if (!logs?.length) return null;

  for (let i = logs.length - 1; i >= 0; i -= 1) {
    const log = logs[i];
    if (!log || log.status !== "success") continue;
    if (!/用户需求分析/.test(String(log.title || ""))) continue;
    const intent = parseRequirementAnalysisFromDetails(log.details || "");
    if (intent) return intent;
  }
  return null;
}

export function deriveSavedReportTagsInputFromQuery(query: string): string {
  let text = String(query || "").trim();
  if (!text) return "";
  text = text
    .replace(/[?？!！。；;：:“”"'「」『』【】()[\]{}]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  text = text
    .replace(/^(帮我|请|麻烦|能否|可以)?\s*(查询|统计|查看|分析|获取|看一下|看看|展示|列出|生成)\s*/, "")
    .replace(/最近\s*\d+\s*个?\s*(月|天|日|周|年|季度)/g, " ")
    .replace(/近\s*\d+\s*(月|天|日|周|年|季度)/g, " ")
    .replace(/(本|上|近)(月|周|年|季度)|今日|今天|昨日|昨天|当日/g, " ")
    .replace(/\s+/g, " ")
    .trim();

  const stopwords = new Set(["查询", "统计", "查看", "分析", "获取", "数据", "报表", "情况", "明细", "列表"]);
  const tags: string[] = [];
  const addTag = (value: string) => {
    const tag = value
      .replace(/^(查询|统计|查看|分析|获取)/, "")
      .replace(/(数据查询|报表|数据|情况|明细|列表)$/, "")
      .trim();
    if (tag.length < 2 || tag.length > 12 || stopwords.has(tag) || tags.includes(tag)) return;
    tags.push(tag);
  };

  for (const part of text.split(/[，,、\s]+|的|和|及|与|按|在|从|对|为/)) {
    addTag(part);
    if (tags.length >= 3) break;
  }
  if (text.includes("用户数") && text.includes("趋势")) addTag("用户数趋势");
  return tags.slice(0, 3).join(", ");
}

export function deriveSavedReportDescription(
  intent: RequirementAnalysisIntent | null,
  originalQuery: string,
): string {
  if (intent?.goal) return intent.goal;
  if (originalQuery) return `基于「${originalQuery.slice(0, 40)}」沉淀的黄金报表`;
  return "";
}

const SAVED_REPORT_TITLE_BASE_MAX_LEN = 28;
const SAVED_REPORT_TITLE_QUERY_MAX_LEN = 15;

function withReportTitleSuffix(base: string, maxLen: number): string {
  const trimmed = String(base || "").trim();
  if (!trimmed) return "";
  if (trimmed.endsWith("报表")) return trimmed.slice(0, 32);
  const truncated = trimmed.length > maxLen ? trimmed.slice(0, maxLen) : trimmed;
  return `${truncated}报表`;
}

/** 优先用业务目标生成报表名称，否则回退到原始提问 */
export function deriveSavedReportTitle(
  intent: RequirementAnalysisIntent | null,
  originalQuery: string,
): string {
  if (intent?.goal) {
    const title = withReportTitleSuffix(intent.goal, SAVED_REPORT_TITLE_BASE_MAX_LEN);
    if (title) return title;
  }
  if (originalQuery) {
    const title = withReportTitleSuffix(originalQuery, SAVED_REPORT_TITLE_QUERY_MAX_LEN);
    if (title) return title;
  }
  return "暂存报表";
}

export function deriveSavedReportTagsInput(
  intent: RequirementAnalysisIntent | null,
  originalQuery: string,
): string {
  if (intent?.metrics.length) {
    return intent.metrics.slice(0, 3).join(", ");
  }
  return deriveSavedReportTagsInputFromQuery(originalQuery);
}
