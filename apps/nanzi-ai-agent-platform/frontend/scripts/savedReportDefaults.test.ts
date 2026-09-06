import assert from "node:assert/strict";
import {
  deriveSavedReportDescription,
  deriveSavedReportTagsInput,
  deriveSavedReportTitle,
  parseRequirementAnalysisFromDetails,
  parseRequirementAnalysisFromMessage,
} from "../src/utils/savedReportDefaults.ts";

const sampleDetails = `已完成用户需求分析，并生成问题关键词。
问题关键词: 智能体用户表 ai_agent_users 去重 用户数 2026-06-01 2026-06-27

【结构化业务意图（用于字段绑定自检，不是 SQL 结果）】
- 业务目标: 统计指定时间段内智能体用户表中的去重用户总数
- 指标: 去重用户数
- 时间范围: 2026-06-01 至 2026-06-27
- 粒度: 明细粒度
注意：本意图帧不是已确认物理表名或字段名来源；`;

const intent = parseRequirementAnalysisFromDetails(sampleDetails);
assert.ok(intent);
assert.equal(intent?.goal, "统计指定时间段内智能体用户表中的去重用户总数");
assert.deepEqual(intent?.metrics, ["去重用户数"]);

assert.equal(
  deriveSavedReportDescription(intent, "查一下用户数"),
  "统计指定时间段内智能体用户表中的去重用户总数",
);
assert.equal(deriveSavedReportTagsInput(intent, "查一下用户数"), "去重用户数");
assert.equal(
  deriveSavedReportTitle(intent, "查一下用户数"),
  "统计指定时间段内智能体用户表中的去重用户总数报表",
);
assert.equal(deriveSavedReportTitle(null, "查一下最近7天的订单量"), "查一下最近7天的订单量报表");
assert.equal(deriveSavedReportTitle(null, ""), "暂存报表");
assert.equal(
  deriveSavedReportDescription(null, "查一下用户数"),
  "基于「查一下用户数」沉淀的黄金报表",
);

const fromMessage = parseRequirementAnalysisFromMessage({
  logs: [
    { title: "用户需求分析", status: "pending", details: "分析中..." },
    { title: "用户需求分析", status: "success", details: sampleDetails },
  ],
});
assert.equal(fromMessage?.goal, intent?.goal);

console.log("savedReportDefaults.test.ts passed");
