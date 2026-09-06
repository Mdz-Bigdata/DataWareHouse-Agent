import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

// Compile the actual TSX component without introducing a browser test dependency.
const source = await readFile(new URL("../src/components/AttributionWidget.tsx", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { jsx: ts.JsxEmit.React, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText.replace(/from (["'])react\1/g, `from "${import.meta.resolve("react")}"`);
const { AttributionWidget } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

const sample = {
  analysis_type: "period_comparison",
  metric_name: "play_count",
  metric_display: "播放量",
  metric_unit: "次",
  dimension: "category_name",
  dimension_display: "分类",
  total_value: 120,
  current_value: 120,
  baseline_value: 100,
  total_change: 20,
  change_rate: 20,
  current_period: { start: "2026-09-04", end: "2026-09-04" },
  baseline_period: { start: "2026-09-03", end: "2026-09-03" },
  top_driver: "有声剧",
  top_driver_ratio: 150,
  waterfall_items: [
    { name: "有声剧", value: 30, ratio: 150, baseline_value: 70, current_value: 100 },
    { name: "历史", value: -10, ratio: -50, baseline_value: 30, current_value: 20 },
  ],
};
const render = data => renderToStaticMarkup(React.createElement(AttributionWidget, { data }));

test("comparison shows both measured periods, signed changes, and offsetting contributions", () => {
  const html = render(sample);
  for (const value of ["周期变动归因", "2026-09-04", "2026-09-03", "+20 次", "+30 次", "-10 次", "150%", "-50%", "不代表业务因果结论"]) {
    assert.ok(html.includes(value), `Missing ${value}`);
  }
  assert.equal(html.includes("¥"), false);
  const precise = render({ ...sample, top_driver_ratio: 66.8820224719101 });
  assert.ok(precise.includes("66.88%"));
  assert.equal(precise.includes("66.8820224719101%"), false);
});

test("zero net change leaves contributions undefined rather than claiming zero influence", () => {
  const html = render({
    ...sample, total_value: 100, current_value: 100, total_change: 0, change_rate: 0, top_driver_ratio: 0,
    waterfall_items: [
      { name: "有声剧", value: 10, ratio: 0, baseline_value: 70, current_value: 80 },
      { name: "历史", value: -10, ratio: 0, baseline_value: 30, current_value: 20 },
    ],
  });
  assert.ok(html.includes("总变动为零，不计算贡献率"));
  assert.ok(html.includes("贡献率不适用"));
  assert.equal(html.includes("基期为零"), false);
});

test("a zero baseline leaves the growth rate undefined", () => {
  const html = render({
    ...sample, baseline_value: 0, total_change: 120, change_rate: null, top_driver_ratio: 100,
    waterfall_items: [{ name: "有声剧", value: 120, ratio: 100, baseline_value: 0, current_value: 120 }],
  });
  assert.ok(html.includes("基期为零，变化率不适用"));
});

test("single-period breakdown remains explicitly described as a share rather than a change", () => {
  const html = render({ ...sample, analysis_type: "dimension_breakdown" });
  assert.ok(html.includes("指标分组占比"));
  assert.ok(html.includes("数值最高的分组"));
  assert.equal(html.includes("周期变动归因"), false);
  assert.equal(html.includes("基期："), false);
});
