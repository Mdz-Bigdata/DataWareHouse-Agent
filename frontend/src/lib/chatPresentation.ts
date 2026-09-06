import type { AskResponse, DataSourceInfo } from "../types";

/** Configuration and missing-data problems are distinct from security policy rejections. */
export function queryErrorTitle(response: Pick<AskResponse, "error" | "skill_type">): string {
  const message = response.error || "";
  if (/不兼容|分解维度|不支持.*维度/.test(message)) {
    return "查询条件需要调整";
  }
  if (/未注册|未发现|缺少|尚未发现|表结构|数据源|指标治理|指标配置/.test(message)) {
    return "数据配置需要完善";
  }
  if (/Guardrail|拦截|熔断|安全防护|权限不足|只读|禁止/.test(message)) {
    return "查询未通过安全检查";
  }
  return response.skill_type === "attribution" ? "归因分析未完成" : "查询未完成";
}

/** Error comments are explanations, not a SQL statement to show in a code panel. */
export function hasQuerySql(sql?: string): boolean {
  return Boolean(sql?.replace(/\/\*[\s\S]*?\*\//g, "").replace(/--[^\r\n]*/g, "").trim());
}

export function dataSourceLabel(mode: "demo" | "configured"): string {
  return mode === "demo" ? "演示数仓" : "业务数据源";
}

export function normalizeDataSource(source: DataSourceInfo): DataSourceInfo {
  return {
    ...source,
    label: source.label || dataSourceLabel(source.mode),
    description: source.description || (source.mode === "demo"
      ? "以下结果来自项目自带示例数据。"
      : "以下结果来自已连接的数据库。"),
  };
}
