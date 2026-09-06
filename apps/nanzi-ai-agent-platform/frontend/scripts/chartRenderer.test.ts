import assert from "node:assert/strict";
import {
  buildChartTableRows,
  createSseLineParser,
  mergeChartDefaults,
  parseChartOptions,
} from "../src/utils/chartRenderer.ts";

const parsed = parseChartOptions(`
{
  xAxis: { type: "category", data: ["A"] },
  yAxis: { type: "value" },
  series: [{ type: "bar", data: [1] }]
}
`);
assert.equal(parsed.ok, true);
if (parsed.ok) {
  assert.equal(parsed.option.series[0].type, "bar");
}

const echartsFencePattern = /(?:<thought>([\s\S]*?)<\/thought>)|(?:<chart>([\s\S]*?)<\/chart>)|(?:```\s*(?:chart|echarts|json)\s*([\s\S]*?)```)|(?:```\s*mermaid\s*([\s\S]*?)```)|(?::::analysis\s*([^\n]*)\n([\s\S]*?)\n:::)/gi;
const echartsFence = '```echarts\n{"series":[{"type":"bar","data":[1]}]}\n```';
const echartsMatch = echartsFencePattern.exec(echartsFence);
assert.equal(echartsMatch?.[3]?.includes('"series"'), true);

const executable = parseChartOptions(`
{
  series: [{
    type: "bar",
    data: [globalThis.__chartRendererExecuted = true]
  }]
}
`);
assert.equal(executable.ok, false);
assert.equal((globalThis as any).__chartRendererExecuted, undefined);

const pie = mergeChartDefaults({
  series: [{ type: "pie", data: [{ name: "A", value: 1 }] }],
});
assert.equal("xAxis" in pie, false);
assert.equal("yAxis" in pie, false);
assert.equal("grid" in pie, false);
assert.equal(pie.tooltip.trigger, "item");

const bar = mergeChartDefaults({
  xAxis: { data: ["A"] },
  series: [{ type: "bar", data: [1] }],
});
assert.equal("xAxis" in bar, true);
assert.equal("yAxis" in bar, true);
assert.equal("grid" in bar, true);
assert.equal(bar.xAxis.axisLabel.color, "#6b7280");

const lightAxis = mergeChartDefaults({
  xAxis: {
    data: ["2026-06-11"],
    axisLabel: { color: "#f3f4f6" },
  },
  series: [{ type: "line", data: [1] }],
});
assert.equal(lightAxis.xAxis.axisLabel.color, "#6b7280");

const nestedTextStyle = mergeChartDefaults({
  xAxis: {
    data: ["A"],
    axisLabel: { textStyle: { color: "#eeeeee" } },
  },
  series: [{ type: "line", data: [1] }],
});
assert.equal(nestedTextStyle.xAxis.axisLabel.textStyle.color, "#6b7280");

const cartesianTable = buildChartTableRows({
  xAxis: { data: ["06-01", "06-02"] },
  series: [
    { name: "新增用户", type: "line", data: [3, 5] },
    { name: "活跃用户", type: "bar", data: [{ value: 7 }, 9] },
  ],
});
assert.deepEqual(cartesianTable.columns, ["维度", "新增用户", "活跃用户"]);
assert.deepEqual(cartesianTable.rows, [
  ["06-01", 3, 7],
  ["06-02", 5, 9],
]);

const pieTable = buildChartTableRows({
  series: [
    {
      type: "pie",
      data: [
        { name: "华东", value: 12 },
        { name: "华南", value: 8 },
      ],
    },
  ],
});
assert.deepEqual(pieTable.columns, ["名称", "数值"]);
assert.deepEqual(pieTable.rows, [
  ["华东", 12],
  ["华南", 8],
]);

const parseSse = createSseLineParser();
assert.deepEqual(parseSse.feed("data: {\"content\":\"hel"), []);
assert.deepEqual(parseSse.feed("lo\"}\n\ndata: [DONE]\n"), [
  '{"content":"hello"}',
  "[DONE]",
]);
assert.deepEqual(parseSse.flush(), []);
