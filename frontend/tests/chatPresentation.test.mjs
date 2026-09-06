import assert from "node:assert/strict";
import test from "node:test";

import { dataSourceLabel, hasQuerySql, normalizeDataSource, queryErrorTitle } from "../src/lib/chatPresentation.ts";

test("unknown metric is a configuration problem even when legacy text says audit blocked", () => {
  assert.equal(queryErrorTitle({ error: "语义审计拦截: 发现未注册的指标名 'total_refund_amount'" }), "数据配置需要完善");
  assert.equal(queryErrorTitle({ skill_type: "attribution", error: "当前数据源未注册与问题匹配的指标" }), "数据配置需要完善");
});

test("security failures and query failures remain distinguishable", () => {
  assert.equal(queryErrorTitle({ error: "Guardrail: 禁止执行 DELETE 操作" }), "查询未通过安全检查");
  assert.equal(queryErrorTitle({ error: "网络连接失败" }), "查询未完成");
  assert.equal(queryErrorTitle({ skill_type: "attribution", error: "没有可比较的两个周期" }), "归因分析未完成");
});

test("empty and comment-only failures do not display a misleading SQL panel", () => {
  for (const sql of [undefined, "", "  ", "-- [语义拦截]: 指标未注册", "/* 查询失败 */\n-- no SQL"]) {
    assert.equal(hasQuerySql(sql), false);
  }
  assert.equal(hasQuerySql("-- safe query\nSELECT SUM(refund_amount) FROM orders"), true);
});

test("demo and configured data sources have explicit distinct labels", () => {
  assert.equal(dataSourceLabel("demo"), "演示数仓");
  assert.equal(dataSourceLabel("configured"), "业务数据源");
});

test("PostgreSQL engine and migrated sample origin remain visible after a query", () => {
  const source = normalizeDataSource({mode: "configured", label: "PostgreSQL 数仓",
    engine: "postgresql", data_origin: "project_fixture",
    description: "初始数据由项目示例数据迁移。"});
  assert.equal(source.label, "PostgreSQL 数仓");
  assert.equal(source.data_origin, "project_fixture");
  assert.equal(source.description, "初始数据由项目示例数据迁移。");
});

test("incompatible metric dimensions are query adjustments, not security failures", () => {
  assert.equal(queryErrorTitle({error: "语义审计拦截: 指标 'articles_count' 与维度 'category_name' 不兼容"}), "查询条件需要调整");
});
