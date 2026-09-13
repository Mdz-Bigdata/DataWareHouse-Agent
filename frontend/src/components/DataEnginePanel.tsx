import React, { useEffect, useMemo, useState } from "react";

type ToolSpec = {
  name: string;
  description: string;
  parameters: string[];
  mutating: boolean;
};

type Workbench = "understand" | "query" | "explore" | "modeling" | "tools";
type JsonObject = Record<string, unknown>;

const toolNames = [
  "health_check", "ontology_search", "ontology_traverse", "mql_validate", "mql_explain",
  "semantic_translate", "execute_sql", "metric_disambiguate", "term_normalize",
  "intent_classify", "metadata_search", "ddl_generate", "etl_generate", "modeling_plan",
  "ontology_register", "ontology_reload", "explore_validate", "explore_execute",
  "explore_promote", "scheduler_submit",
];

const defaultMql = JSON.stringify({
  metrics: [{ name: "gmv" }],
  dimensions: [{ name: "store_type" }],
  time_range: { start: "-7d", end: "today" },
}, null, 2);

const defaultExploreSql = `SELECT region_id, SUM(pay_amt) AS amount, COUNT(*) AS order_count
FROM dwd_ord_pay_di
WHERE dt >= strftime('%Y%m%d', 'now', '-7 day')
GROUP BY region_id
ORDER BY amount DESC
LIMIT 20`;

const defaultChanges = JSON.stringify([{
  type: "create",
  obj_name: "Order",
  layer: "dws",
  domain: "ord",
  subject: "order_weekly",
  metrics: ["gmv"],
  dimensions: ["store_type"],
}], null, 2);

function asObject(value: unknown): JsonObject {
  return value && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : {};
}

function resultObject(value: unknown): JsonObject {
  return asObject(asObject(value).result);
}

export const DataEnginePanel: React.FC = () => {
  const [active, setActive] = useState<Workbench>("understand");
  const [status, setStatus] = useState<"checking" | "ready" | "offline">("checking");
  const [tools, setTools] = useState<ToolSpec[]>(toolNames.map(name => ({
    name, description: "", parameters: [], mutating: ["ontology_register", "scheduler_submit"].includes(name),
  })));
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [output, setOutput] = useState<unknown>({ note: "选择一个流程并执行，结果与安全令牌会显示在这里。" });
  const [question, setQuestion] = useState("昨天的 GMV 是多少？");
  const [mqlText, setMqlText] = useState(defaultMql);
  const [confirmToken, setConfirmToken] = useState("");
  const [queryToken, setQueryToken] = useState("");
  const [translatedSql, setTranslatedSql] = useState("");
  const [exploreSql, setExploreSql] = useState(defaultExploreSql);
  const [exploreToken, setExploreToken] = useState("");
  const [changesText, setChangesText] = useState(defaultChanges);
  const [selectedTool, setSelectedTool] = useState("health_check");
  const [argumentsText, setArgumentsText] = useState("{}");

  const selectedSpec = useMemo(() => tools.find(item => item.name === selectedTool), [tools, selectedTool]);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      fetch("/api/platform/data-engine/health", { signal: controller.signal }),
      fetch("/api/platform/data-engine/api/tools", { signal: controller.signal }),
    ]).then(async ([health, catalog]) => {
      if (!health.ok || !catalog.ok) throw new Error("Data Engine 服务未就绪");
      const payload = await catalog.json();
      if (Array.isArray(payload.items)) setTools(payload.items);
      setStatus("ready");
    }).catch(cause => {
      if (cause instanceof DOMException && cause.name === "AbortError") return;
      setStatus("offline");
    });
    return () => controller.abort();
  }, []);

  const callTool = async (name: string, args: JsonObject = {}) => {
    setBusy(name);
    setError("");
    try {
      const response = await fetch(`/api/platform/data-engine/api/tools/${encodeURIComponent(name)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ arguments: args }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(asObject(payload).detail as string || `HTTP ${response.status}`);
      setOutput(payload);
      return payload;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "请求失败";
      setError(message);
      setOutput({ error: message, tool: name });
      return null;
    } finally {
      setBusy("");
    }
  };

  const parseJson = (value: string, label: string): unknown => {
    try {
      return JSON.parse(value);
    } catch {
      throw new Error(`${label} 不是有效 JSON`);
    }
  };

  const withParsed = async (action: () => Promise<void>) => {
    setError("");
    try { await action(); } catch (cause) {
      const message = cause instanceof Error ? cause.message : "输入格式错误";
      setError(message);
      setOutput({ error: message });
    }
  };

  const explainMql = () => withParsed(async () => {
    const payload = await callTool("mql_explain", { mql: parseJson(mqlText, "MQL") });
    const token = resultObject(payload).confirm_token;
    setConfirmToken(typeof token === "string" ? token : "");
    setQueryToken("");
    setTranslatedSql("");
  });

  const translateMql = () => withParsed(async () => {
    if (!confirmToken) throw new Error("请先执行“生成确认清单”，确认当前 MQL 后才能翻译");
    const payload = await callTool("semantic_translate", {
      mql: parseJson(mqlText, "MQL"), confirm_token: confirmToken, dialect: "sqlite",
    });
    const result = resultObject(payload);
    setQueryToken(typeof result.query_token === "string" ? result.query_token : "");
    setTranslatedSql(typeof result.sql === "string" ? result.sql : "");
  });

  const executeQuery = async () => {
    if (!queryToken || !translatedSql) {
      setError("请先完成确认与确定性翻译");
      return;
    }
    await callTool("execute_sql", { sql: translatedSql, query_token: queryToken });
  };

  const validateExplore = async () => {
    const payload = await callTool("explore_validate", { sql: exploreSql });
    const token = resultObject(payload).explore_token;
    setExploreToken(typeof token === "string" ? token : "");
  };

  const executeExplore = async () => {
    if (!exploreToken) {
      setError("请先校验 SQL 并取得探索令牌");
      return;
    }
    await callTool("explore_execute", { sql: exploreSql, explore_token: exploreToken });
  };

  const tabs: Array<[Workbench, string]> = [
    ["understand", "意图与元数据"], ["query", "安全取数"], ["explore", "探索取数"],
    ["modeling", "建模与调度"], ["tools", "全部 20 工具"],
  ];

  return (
    <section className="w-full max-w-7xl px-4 py-8 flex flex-col gap-6 animate-fade-in text-slate-100">
      <div className="glass-card p-6 border border-cyan-500/30 bg-[#081321]/80 flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-xl font-black text-white">🧩 确定性 Data Agent 工作台</h2>
            <span className="text-[10px] px-2 py-0.5 rounded-full border border-cyan-500/30 text-cyan-300">DSH · OAG · MCP</span>
          </div>
          <p className="text-xs text-gray-400 mt-2">生产数 → 取到数 → 分析数；LLM 负责理解，Ontology、MQL 和只读执行引擎负责确定性落地。</p>
        </div>
        <span className={`px-3 py-1.5 rounded-lg border text-xs font-mono ${status === "ready" ? "text-emerald-400 border-emerald-500/40" : status === "offline" ? "text-red-400 border-red-500/40" : "text-amber-300 border-amber-500/40"}`}>
          ● {status === "ready" ? "20/20 工具就绪" : status === "offline" ? "服务未连接" : "正在自检"}
        </span>
      </div>

      <div className="flex flex-wrap gap-2">
        {tabs.map(([key, label]) => (
          <button key={key} onClick={() => setActive(key)} className={`px-4 py-2 rounded-lg text-xs font-bold border cursor-pointer ${active === key ? "bg-cyan-600/30 border-cyan-400/50 text-cyan-200" : "bg-slate-900/70 border-slate-800 text-gray-400"}`}>
            {label}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        <div className="lg:col-span-8 glass-card p-6 border border-slate-800/90 bg-[#080d19]/80 min-h-[520px]">
          {active === "understand" && <div className="flex flex-col gap-4">
            <h3 className="text-sm font-bold text-cyan-300">OAG 理解层与元数据检索</h3>
            <textarea value={question} onChange={event => setQuestion(event.target.value)} className="w-full h-24 bg-slate-950 border border-slate-700 rounded-lg p-3 text-sm text-slate-200 resize-none" />
            <div className="flex flex-wrap gap-2">
              <button className="btn-gradient text-xs" onClick={() => callTool("intent_classify", { text: question })}>意图路由</button>
              <button className="btn-gradient text-xs" onClick={() => callTool("term_normalize", { text: question })}>黑话归一</button>
              <button className="btn-gradient text-xs" onClick={() => callTool("metric_disambiguate", { query: question })}>指标口径消歧</button>
              <button className="btn-gradient text-xs" onClick={() => callTool("metadata_search", { query: question })}>查元数据/血缘/就绪</button>
              <button className="btn-gradient text-xs" onClick={() => callTool("ontology_search", { query: question })}>搜索 Ontology</button>
            </div>
            <p className="text-xs text-gray-500">先用确定性意图分类选择 A（正式取数）、D（建模）、E（探索）或元数据路径；未命中时返回澄清证据，不臆造表与指标。</p>
          </div>}

          {active === "query" && <div className="flex flex-col gap-4">
            <h3 className="text-sm font-bold text-purple-300">MQL → 确认令牌 → SQL → 查询令牌 → 只读执行</h3>
            <textarea value={mqlText} onChange={event => { setMqlText(event.target.value); setConfirmToken(""); setQueryToken(""); }} className="w-full h-72 bg-slate-950 border border-slate-700 rounded-lg p-3 text-xs font-mono text-emerald-300 resize-y" spellCheck={false} />
            <div className="flex flex-wrap gap-2">
              <button className="btn-gradient text-xs" onClick={() => withParsed(async () => { await callTool("mql_validate", { mql: parseJson(mqlText, "MQL") }); })}>1. 校验 MQL</button>
              <button className="btn-gradient text-xs" onClick={explainMql}>2. 生成确认清单</button>
              <button className="btn-gradient text-xs" disabled={!confirmToken} onClick={translateMql}>3. 已确认，确定性翻译</button>
              <button className="btn-gradient text-xs" disabled={!queryToken} onClick={executeQuery}>4. 只读执行</button>
            </div>
            {translatedSql && <pre className="p-3 rounded-lg bg-black border border-emerald-500/20 text-[11px] text-emerald-300 whitespace-pre-wrap">{translatedSql}</pre>}
          </div>}

          {active === "explore" && <div className="flex flex-col gap-4">
            <h3 className="text-sm font-bold text-amber-300">路径 E：受控探索与指标固化</h3>
            <textarea value={exploreSql} onChange={event => { setExploreSql(event.target.value); setExploreToken(""); }} className="w-full h-72 bg-slate-950 border border-slate-700 rounded-lg p-3 text-xs font-mono text-amber-200 resize-y" spellCheck={false} />
            <div className="flex flex-wrap gap-2">
              <button className="btn-gradient text-xs" onClick={validateExplore}>1. 白名单/只读/分区校验</button>
              <button className="btn-gradient text-xs" disabled={!exploreToken} onClick={executeExplore}>2. 携探索令牌执行</button>
              <button className="btn-gradient text-xs" onClick={() => callTool("explore_promote", { sql: exploreSql })}>3. 生成固化草稿</button>
            </div>
            <p className="text-xs text-gray-500">探索令牌与 SQL 指纹绑定；写操作、未注册表和缺失分区条件会被拒绝。</p>
          </div>}

          {active === "modeling" && <div className="flex flex-col gap-4">
            <h3 className="text-sm font-bold text-blue-300">路径 D：DDL + ETL 配对建模与调度</h3>
            <textarea value={changesText} onChange={event => setChangesText(event.target.value)} className="w-full h-72 bg-slate-950 border border-slate-700 rounded-lg p-3 text-xs font-mono text-blue-200 resize-y" spellCheck={false} />
            <div className="flex flex-wrap gap-2">
              <button className="btn-gradient text-xs" onClick={() => withParsed(async () => { await callTool("modeling_plan", { changes: parseJson(changesText, "变更清单") }); })}>生成配对建模方案</button>
              <button className="btn-gradient text-xs" onClick={() => { setSelectedTool("ontology_register"); setArgumentsText(JSON.stringify({ kind: "function", entry: {} }, null, 2)); setActive("tools"); }}>打开本体注册</button>
              <button className="btn-gradient text-xs" onClick={() => { setSelectedTool("scheduler_submit"); setArgumentsText(JSON.stringify({ task_spec: { name: "每日数据任务", agent_id: "data-assistant", cron_expr: "0 8 * * *", prompt: "执行已确认的数据建模任务", dependencies: [], alert_channels: ["inbox"], idempotency_key: "data-modeling-v1" } }, null, 2)); setActive("tools"); }}>提交定时任务</button>
            </div>
            <p className="text-xs text-gray-500">完整流程覆盖需求、方案、DDL/ETL、测试、上线、调度、SLA/DQC；调度提交桥接到已集成的 NanZi 任务中心。写操作默认关闭，需由运维显式启用。</p>
          </div>}

          {active === "tools" && <div className="flex flex-col gap-4">
            <h3 className="text-sm font-bold text-emerald-300">原生 MCP 工具调试器</h3>
            <select value={selectedTool} onChange={event => setSelectedTool(event.target.value)} className="w-full bg-slate-950 border border-slate-700 rounded-lg p-3 text-sm text-slate-200">
              {tools.map(item => <option key={item.name} value={item.name}>{item.name}{item.mutating ? "（写操作）" : ""}</option>)}
            </select>
            <p className="text-xs text-gray-400">参数：{selectedSpec?.parameters.join(", ") || "无"}</p>
            {selectedSpec?.description && <p className="text-xs text-gray-500 whitespace-pre-wrap max-h-24 overflow-y-auto">{selectedSpec.description}</p>}
            <textarea value={argumentsText} onChange={event => setArgumentsText(event.target.value)} className="w-full h-56 bg-slate-950 border border-slate-700 rounded-lg p-3 text-xs font-mono text-emerald-200 resize-y" spellCheck={false} />
            <button className="btn-gradient text-xs self-start" onClick={() => withParsed(async () => { await callTool(selectedTool, asObject(parseJson(argumentsText, "工具参数"))); })}>
              执行 {selectedTool}
            </button>
          </div>}
        </div>

        <aside className="lg:col-span-4 glass-card p-5 border border-slate-800/90 bg-black/40 sticky top-6">
          <div className="flex items-center justify-between gap-2 mb-3">
            <h3 className="text-sm font-bold text-white">执行结果</h3>
            {busy && <span className="text-[10px] text-cyan-400 animate-pulse">{busy}…</span>}
          </div>
          {error && <div className="mb-3 p-3 rounded-lg border border-red-500/30 bg-red-950/30 text-xs text-red-300">{error}</div>}
          <pre className="max-h-[620px] overflow-auto whitespace-pre-wrap break-words text-[11px] leading-relaxed text-emerald-300 font-mono">{JSON.stringify(output, null, 2)}</pre>
          <div className="mt-4 pt-3 border-t border-slate-800 text-[10px] text-gray-500">服务令牌只存在于网关与引擎之间；浏览器无法读取。确认、查询和探索令牌均由引擎签发并绑定请求指纹。</div>
        </aside>
      </div>
    </section>
  );
};
