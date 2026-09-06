import React, { useState, useEffect, useCallback, useRef } from "react";
import { EChartWidget } from "./EChartWidget";
import { SqlCodeBlock } from "./SqlCodeBlock";
import { AttributionWidget } from "./AttributionWidget";
import { LineageGraphWidget } from "./LineageGraphWidget";
import { DataSourcePicker } from "./DataSourcePicker";
import type { AskResponse, DataSourceInfo, HistoryRecord, PreferenceProfile } from "../types";
import { dataSourceLabel, hasQuerySql, normalizeDataSource, queryErrorTitle } from "../lib/chatPresentation";

const API_BASE = "/api";
// 历史记录按可用高度分页：先把当前页铺满，再翻下一页。
const HISTORY_ITEM_GAP = 12; // 与列表的 gap-3 保持一致
const HISTORY_ITEM_FALLBACK_HEIGHT = 96; // 首条记录渲染前的估算行高

interface ChatPanelProps {
  initialQuestion?: string;
}

export const ChatPanel: React.FC<ChatPanelProps> = ({ initialQuestion }) => {
  const [question, setQuestion] = useState(initialQuestion || "");
  const [dialect, setDialect] = useState("postgres");
  const [user, setUser] = useState("anonymous"); // 默认模拟用户
  const [role, setRole] = useState("user");       // 默认角色权限

  const [sentQuestions, setSentQuestions] = useState<string[]>([]); // 发送的问题历史
  const [historyIndex, setHistoryIndex] = useState(-1);             // 当前历史翻阅索引

  const [loading, setLoading] = useState(false);
  const [loadingStep, setLoadingStep] = useState("");
  const [response, setResponse] = useState<AskResponse | null>(null);
  const [dataSource, setDataSource] = useState<DataSourceInfo | null>(null);
  const [dataSourceUnavailable, setDataSourceUnavailable] = useState(false);

  const [history, setHistory] = useState<HistoryRecord[]>([]);
  const [historyPage, setHistoryPage] = useState(1);
  const [historyPageSize, setHistoryPageSize] = useState(3);
  const historyListRef = useRef<HTMLDivElement>(null);
  const [preference, setPreference] = useState<PreferenceProfile | null>(null);
  const [recommendations, setRecommendations] = useState<string[]>([]);
  const [isEditingPreference, setIsEditingPreference] = useState(false);
  const [editTable, setEditTable] = useState("");
  const [editMetric, setEditMetric] = useState("");
  const [editDimension, setEditDimension] = useState("");
  const [editRange, setEditRange] = useState("");

  // 模拟 Agent 全链路执行思维链动效
  const steps = [
    "正在进行意图识别 (L2 画像匹配中)...",
    "正在检索 Schema (物理字段及汇总表过滤)...",
    "正在生成标准 SQL 并转译方言...",
    "正在运行 Guardrail (DDL/分区过滤检查/EXPLAIN预检)...",
    "正在执行查询并计算环比指标...",
    "正在生成商业洞察结论并选择可视化配置..."
  ];

  const fetchHistoryAndPreference = async () => {
    try {
      const histRes = await fetch(`${API_BASE}/chat/history?user=${user}`);
      const histData = await histRes.json();
      setHistory(histData);
      setHistoryPage(1);

      const prefRes = await fetch(`${API_BASE}/chat/preference?user=${user}`);
      const prefData = await prefRes.json();
      setPreference(prefData);

      const recRes = await fetch(`${API_BASE}/chat/recommendations?user=${user}`);
      const recData = await recRes.json();
      setRecommendations(recData);
    } catch (e) {
      console.error("加载历史与画像失败:", e);
    }
  };

  useEffect(() => {
    fetchHistoryAndPreference();
  }, [user]);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API_BASE}/chat/data-source`, { signal: controller.signal })
      .then(async res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (data.mode !== "demo" && data.mode !== "configured") throw new Error("Unknown data source");
        setDataSource(normalizeDataSource(data));
      })
      .catch(error => {
        if (!(error instanceof DOMException && error.name === "AbortError")) setDataSourceUnavailable(true);
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const totalPages = Math.max(1, Math.ceil(history.length / historyPageSize));
    if (historyPage > totalPages) {
      setHistoryPage(totalPages);
    }
  }, [history.length, historyPage, historyPageSize]);

  // 侧栏与左侧主区同高，可用高度随之变化；按实测行高重新计算每页条数。
  const measureHistoryPageSize = useCallback(() => {
    const container = historyListRef.current;
    if (!container) return;
    const available = container.clientHeight;
    if (available <= 0) return;
    const record = container.querySelector<HTMLElement>("[data-history-record]");
    const itemHeight = record?.offsetHeight || HISTORY_ITEM_FALLBACK_HEIGHT;
    const fits = Math.floor((available + HISTORY_ITEM_GAP) / (itemHeight + HISTORY_ITEM_GAP));
    setHistoryPageSize(previous => (previous === Math.max(1, fits) ? previous : Math.max(1, fits)));
  }, []);

  useEffect(() => {
    const container = historyListRef.current;
    if (!container) return;
    // Answer rendering is what changes the column height, so re-measure on it
    // directly: ResizeObserver callbacks are not delivered while a tab is hidden.
    measureHistoryPageSize();
    const observer = typeof ResizeObserver === "undefined"
      ? null : new ResizeObserver(measureHistoryPageSize);
    observer?.observe(container);
    window.addEventListener("resize", measureHistoryPageSize);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", measureHistoryPageSize);
    };
  }, [measureHistoryPageSize, history.length, response, loading, preference, recommendations]);

  useEffect(() => {
    if (initialQuestion) {
      setQuestion(initialQuestion);
      handleSend(initialQuestion);
    }
  }, [initialQuestion]);

  const handleSend = async (text: string) => {
    if (!text.trim() || loading) return;
    setLoading(true);
    setResponse(null);

    // 记录到发送问题历史数组中，防止连续重复添加
    setSentQuestions(prev => {
      if (prev.length > 0 && prev[prev.length - 1] === text) {
        return prev;
      }
      return [...prev, text];
    });
    setHistoryIndex(-1); // 重置翻页索引

    // 动态循环展示思维链步骤
    let stepIndex = 0;
    setLoadingStep(steps[0]);
    const stepInterval = setInterval(() => {
      stepIndex = (stepIndex + 1) % steps.length;
      setLoadingStep(steps[stepIndex]);
    }, 1200);

    try {
      const res = await fetch(`${API_BASE}/chat/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: text, dialect, user, role })
      });
      if (!res.ok) {
        let errStr = `HTTP Error ${res.status}`;
        try {
          const errData = await res.json();
          errStr = errData.detail || errData.error || errStr;
        } catch(e) {}
        setResponse({
          success: false,
          error: errStr
        });
        return;
      }
      const data: AskResponse = await res.json();
      setResponse(data);
      if (data.data_source_info) {
        setDataSource(normalizeDataSource(data.data_source_info));
        setDataSourceUnavailable(false);
      } else if (data.details?.data_source) {
        const mode = data.details.data_source;
        setDataSource(previous => previous?.mode === mode ? previous : normalizeDataSource({ mode, label: dataSourceLabel(mode) }));
        setDataSourceUnavailable(false);
      }
    } catch (e) {
      setResponse({
        success: false,
        error: "网络错误，无法连接后端服务。请检查后端 FastAPI 是否正常运行在 8000 端口。"
      });
    } finally {
      clearInterval(stepInterval);
      setLoading(false);
      setQuestion("");
      fetchHistoryAndPreference(); // 重新加载画像与历史
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      handleSend(question);
    } else if (e.key === "ArrowUp") {
      e.preventDefault(); // 阻止光标移动到首字符的默认行为
      if (sentQuestions.length > 0) {
        let newIndex = historyIndex;
        if (historyIndex === -1) {
          newIndex = sentQuestions.length - 1;
        } else if (historyIndex > 0) {
          newIndex = historyIndex - 1;
        }
        setHistoryIndex(newIndex);
        setQuestion(sentQuestions[newIndex]);
      }
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      if (sentQuestions.length > 0) {
        if (historyIndex !== -1) {
          if (historyIndex < sentQuestions.length - 1) {
            const newIndex = historyIndex + 1;
            setHistoryIndex(newIndex);
            setQuestion(sentQuestions[newIndex]);
          } else {
            // 翻到底了，清空输入框并重置索引
            setHistoryIndex(-1);
            setQuestion("");
          }
        }
      }
    }
  };

  const handleDeleteHistory = async (e: React.MouseEvent, id: number | string) => {
    e.stopPropagation();
    try {
      await fetch(`${API_BASE}/chat/history/${id}`, { method: 'DELETE' });
      setHistory(currentHistory => currentHistory.filter(h => h.id !== id));
    } catch (err) {
      console.error("Failed to delete history:", err);
    }
  };

  const handleRemoveRecommendation = (e: React.MouseEvent, index: number) => {
    e.stopPropagation();
    setRecommendations(currentRecommendations =>
      currentRecommendations.filter((_, recommendationIndex) => recommendationIndex !== index)
    );
  };

  const sortedHistory = [...history].sort((a, b) =>
    b.created_at.localeCompare(a.created_at)
  );
  const totalHistoryPages = Math.max(
    1,
    Math.ceil(sortedHistory.length / historyPageSize)
  );
  const currentHistoryPage = Math.min(historyPage, totalHistoryPages);
  const visibleHistory = sortedHistory.slice(
    (currentHistoryPage - 1) * historyPageSize,
    currentHistoryPage * historyPageSize
  );

  const startEditingPreference = () => {
    setEditTable(preference?.common_tables[0]?.table || "dws_trade_order_daily");
    setEditMetric(preference?.common_metrics[0]?.metric || "gmv");
    setEditDimension(preference?.common_dimensions[0]?.dimension || "region_name");
    setEditRange(preference?.common_time_ranges[0]?.range || "过去30天");
    setIsEditingPreference(true);
  };

  const handleSavePreference = async () => {
    try {
      const updatedProfile = {
        user,
        common_tables: [{ table: editTable, count: preference?.common_tables[0]?.count || 10 }],
        common_metrics: [{ metric: editMetric, count: preference?.common_metrics[0]?.count || 10 }],
        common_dimensions: [{ dimension: editDimension, count: preference?.common_dimensions[0]?.count || 10 }],
        common_time_ranges: [{ range: editRange, count: preference?.common_time_ranges[0]?.count || 10 }]
      };
      
      const res = await fetch(`${API_BASE}/chat/preference`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(updatedProfile)
      });
      if (res.ok) {
        const data = await res.json();
        setPreference(data);
        setIsEditingPreference(false);
      }
    } catch (e) {
      console.error("保存画像偏好失败:", e);
    }
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 p-6 max-w-7xl mx-auto w-full items-stretch min-h-[750px]">
      <div style={{ gridColumn: "1 / -1" }} className="flex flex-wrap items-center gap-3 text-xs" role="status" aria-live="polite">
        <DataSourcePicker
          apiBase={API_BASE}
          active={dataSource}
          unavailable={dataSourceUnavailable}
          onSwitched={(info, engineDialect) => {
            setDataSource(normalizeDataSource(info));
            setDataSourceUnavailable(false);
            // Keep the dialect selector aligned with the engine now answering.
            setDialect(engineDialect);
            // A different source models different tables, so cached answers and
            // suggestions from the previous one must not be shown as current.
            setResponse(null);
            fetchHistoryAndPreference();
          }}
        />
        {dataSource && <span className="text-gray-400">{dataSource.description}</span>}
      </div>
      {/* 左侧：输入框 + 结果展示区 */}
      <div className="lg:col-span-8 flex flex-col gap-6 justify-between h-full">
        <div className="flex flex-col gap-6 flex-1">
          {response && (
            <div className="flex flex-col gap-6 animate-fade-in flex-1 h-full">
              {/* 主动澄清交互界面 (LLM 自动识别) */}
              {response.clarification?.need_clarification && (
                <div className="glass-card p-6 border-l-4 border-l-amber-500 bg-gradient-to-r from-amber-950/10 to-slate-900/40 animate-fade-in">
                  <h3 className="text-amber-400 text-sm font-semibold mb-2 flex items-center gap-1.5">
                    <span>⚠️ 问数意图澄清建议 (大模型智能识别)</span>
                  </h3>
                  <p className="text-gray-100 text-sm mb-4 leading-relaxed">
                    {response.clarification.message}
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {response.clarification.options.map((opt, idx) => (
                      <button
                        key={idx}
                        onClick={() => {
                          setQuestion(opt.query);
                          handleSend(opt.query);
                        }}
                        className="px-3 py-1.5 bg-amber-950/30 hover:bg-amber-900/40 border border-amber-500/30 text-amber-200 rounded-lg text-xs transition-all active:scale-95 font-medium"
                      >
                        💡 {opt.label}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {response.success ? (
                <>
                  {/* 顶栏徽标：技能调度与缓存加速状态 */}
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <div className="flex items-center gap-2">
                      {response.skill_type === "attribution" && (
                        <span className="px-2.5 py-1 rounded-md text-xs font-semibold bg-gradient-to-r from-orange-500/20 to-rose-500/20 text-orange-300 border border-orange-500/30">
                          🎯 Skill-Hub: 多维异动归因下钻
                        </span>
                      )}
                      {response.skill_type === "lineage" && (
                        <span className="px-2.5 py-1 rounded-md text-xs font-semibold bg-gradient-to-r from-purple-500/20 to-indigo-500/20 text-purple-300 border border-purple-500/30">
                          🕸️ Skill-Hub: 湖图双引擎数据血缘
                        </span>
                      )}
                      {(!response.skill_type || response.skill_type === "query") && (
                        <span className="px-2.5 py-1 rounded-md text-xs font-semibold bg-gradient-to-r from-blue-500/20 to-cyan-500/20 text-blue-300 border border-blue-500/30">
                          ⚡ 语义层确定性 DSL 编译
                        </span>
                      )}
                    </div>
                    {response.cache_hit && (
                      <span className="px-2.5 py-1 rounded-md text-xs font-semibold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 flex items-center gap-1">
                        ⚡ 语义多级缓存已命中 ({response.cache_type === "exact" ? "L1 精确哈希" : "L2 向量语义"})
                      </span>
                    )}
                  </div>

                  {/* 第一层：结论摘要 */}
                  <div className="glass-card p-6 border-l-4 border-l-purple-500 bg-gradient-to-r from-purple-950/10 to-slate-900/40">
                    <h3 className="text-gray-400 text-xs font-semibold uppercase tracking-wider mb-2">
                      分析师结论 (必选)
                    </h3>
                    <p className="text-gray-100 text-base font-medium leading-relaxed">
                      {response.conclusion}
                    </p>
                  </div>

                  {/* 专项技能卡片渲染：异动归因诊断 */}
                  {response.attribution_data && (
                    <AttributionWidget data={response.attribution_data} />
                  )}

                  {/* 专项技能卡片渲染：湖图数据血缘拓扑 */}
                  {response.lineage_data && (
                    <LineageGraphWidget data={response.lineage_data} />
                  )}

                  {/* 第二层：智能可视化图表 */}
                  {response.chart && response.chart.type !== "table" && (
                    <EChartWidget
                      type={response.chart.type}
                      title={response.chart.title}
                      config={response.chart.config}
                    />
                  )}

                  {/* 数字卡片类型 */}
                  {response.chart && response.chart.type === "card" && (
                    <div className="glass-card p-8 flex flex-col items-center justify-center text-center gap-3 border border-purple-500/20 bg-gradient-to-br from-purple-950/10 to-slate-900/50">
                      <span className="text-4xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-purple-400 to-blue-400">
                        {response.chart.config.value}
                      </span>
                      <span className="text-gray-400 text-sm">{response.chart.config.label}</span>
                    </div>
                  )}

                  {/* 数据表格明细 */}
                  {response.data && response.data.length > 0 && (
                    <div className="glass-card p-6 overflow-hidden">
                      <h3 className="text-sm font-semibold text-gray-300 mb-4">明细数据表</h3>
                      <div className="overflow-x-auto">
                        <table className="min-w-full text-left border-collapse">
                          <thead>
                            <tr className="border-b border-slate-800 text-gray-500 text-xs font-semibold uppercase">
                              {Object.keys(response.data[0]).map((key) => (
                                <th key={key} className="py-2.5 px-4">{key}</th>
                              ))}
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-slate-800/50 text-sm text-gray-300">
                            {response.data.map((row, rIdx) => (
                              <tr key={rIdx} className="hover:bg-slate-900/30 transition-colors">
                                {Object.entries(row).map(([key, val], cIdx) => {
                                  if (typeof val === "number") {
                                    const colType = response.column_types ? response.column_types[key] : null;
                                    if (colType === "integer") {
                                      return (
                                        <td key={cIdx} className="py-2.5 px-4">
                                          {val.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
                                        </td>
                                      );
                                    } else if (colType === "decimal") {
                                      return (
                                        <td key={cIdx} className="py-2.5 px-4">
                                          {val.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                                        </td>
                                      );
                                    } else {
                                      const keyLower = key.toLowerCase();
                                      const isIntLike = /count|number|qty|quantity|times|pv|uv|id|rank|cnt/.test(keyLower);
                                      const isFloatLike = /amount|price|gmv|ratio|rate|pct|percent|avg|mean|cost|fee|val|value|revenue|profit|margin|discount|tax|salary|wage|bonus|commission|balance|turnover|arpu|arppu|ltv|cpc|cpm|ctr|cvr|roi|roas|score|index|coefficient|weight|proportion/.test(keyLower)
                                        || /[\u7387\u4ef7\u989d\u6bd4]$/.test(key);
                                      if (isIntLike && !isFloatLike) {
                                        return (
                                          <td key={cIdx} className="py-2.5 px-4">
                                            {val.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
                                          </td>
                                        );
                                      } else {
                                        return (
                                          <td key={cIdx} className="py-2.5 px-4">
                                            {val.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                                          </td>
                                        );
                                      }
                                    }
                                  }
                                  return (
                                    <td key={cIdx} className="py-2.5 px-4">
                                      {val === null || val === undefined ? "" : String(val)}
                                    </td>
                                  );
                                })}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}

                  {/* 第三层：可展开查询详情 */}
                  <details className="glass-card p-4 border border-slate-800/80 group">
                    <summary className="text-xs text-gray-500 font-semibold cursor-pointer select-none outline-none flex items-center justify-between group-open:mb-4">
                      <span>📋 查看查询详情 (包含数据源、关联条件、最终 SQL)</span>
                      <span className="text-gray-600 transition-transform duration-200 group-open:rotate-185">▼</span>
                    </summary>

                    {response.details && (
                      <div className="text-xs text-gray-400 flex flex-col gap-3 border-t border-slate-800/50 pt-4">
                        <div className="grid grid-cols-2 gap-4">
                          <div>
                            <span className="text-gray-500 font-medium">数据来源：</span>
                            <span className="text-purple-300">{response.details.source_desc}</span>
                          </div>
                          <div>
                            <span className="text-gray-500 font-medium">执行耗时：</span>
                            <span className="text-blue-300">{response.details.elapsed_time}</span>
                          </div>
                          <div>
                            <span className="text-gray-500 font-medium">涉及实体：</span>
                            <span className="text-emerald-300">{response.details.tables.join(", ")}</span>
                          </div>
                          <div>
                            <span className="text-gray-500 font-medium">安全审计扫描行数：</span>
                            <span className="text-yellow-400">{response.details.estimated_rows} 行</span>
                          </div>
                        </div>

                        <div>
                          <span className="text-gray-500 font-medium block mb-1.5">最终执行 SQL ({response.details.dialect})：</span>
                          <SqlCodeBlock sql={response.details.sql} />
                        </div>
                      </div>
                    )}
                  </details>
                </>
              ) : response.clarification?.need_clarification && !response.error ? null : (
                // Guardrail 拦截与错误展示
                <div className="glass-card p-6 border-l-4 border-l-red-500 bg-red-950/15 border-red-500/20">
                  <div className="flex items-start gap-3">
                    <div className="text-red-400 text-2xl font-bold leading-none">⚠️</div>
                    <div className="flex-1">
                      <h3 className="text-red-400 text-sm font-semibold mb-2">
                        {queryErrorTitle(response)}
                      </h3>
                      <p className="text-gray-300 text-sm font-medium leading-relaxed">
                        {response.error}
                      </p>

                      {response.details && hasQuerySql(response.details.sql) && (
                        <div className="mt-4 border-t border-red-500/10 pt-4">
                          <span className="text-gray-500 text-xs font-semibold block mb-1">查询 SQL ({response.details.dialect})：</span>
                          <SqlCodeBlock sql={response.details.sql} />
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}
        
          {/* Loading 思维链展示 */}
          {loading && (
            <div className="glass-card p-6 bg-slate-900/50 border border-purple-500/20 flex-1 flex flex-col items-center justify-center text-center gap-4 animate-pulse h-full min-h-[300px]">
              <div className="w-10 h-10 border-4 border-purple-500 border-t-transparent rounded-full animate-spin"></div>
              <p className="text-purple-400 font-medium text-sm transition-all duration-300">
                {loadingStep}
              </p>
              <div className="w-64 bg-slate-800 rounded-full h-1.5 overflow-hidden">
                <div className="bg-gradient-to-r from-purple-500 to-blue-500 h-1.5 rounded-full animate-infinite-loading" style={{ width: "60%" }}></div>
              </div>
            </div>
          )}
        </div>

        {/* 输入框 */}
        <div className="glass-card p-6 bg-gradient-to-br from-slate-900 to-slate-950 border border-slate-800 mt-auto">
          <div className="flex justify-between items-center mb-4">
            <h2 className="text-xl font-bold flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full bg-purple-500 animate-pulse"></span>
              智能查数问数 Agent
            </h2>
            {/* 用户与角色权限配置（防止硬编码） */}
            <div className="flex gap-4 items-center bg-slate-900/40 border border-slate-800 rounded-lg px-3 py-1.5 text-xs text-gray-400">
              <div className="flex items-center gap-1.5">
                <span>用户:</span>
                <input
                  type="text"
                  value={user}
                  onChange={(e) => setUser(e.target.value)}
                  placeholder="anonymous"
                  className="w-20 bg-slate-950 border border-slate-700 rounded px-1.5 py-0.5 text-gray-200 focus:outline-none focus:border-purple-500"
                />
              </div>
              <div className="flex items-center gap-1.5">
                <span>角色:</span>
                <select
                  value={role}
                  onChange={(e) => setRole(e.target.value)}
                  className="bg-slate-950 border border-slate-700 rounded px-1 py-0.5 text-gray-200 focus:outline-none focus:border-purple-500"
                >
                  <option value="user">普通用户 (user)</option>
                  <option value="analyst">分析师 (analyst)</option>
                  <option value="admin">管理员 (admin)</option>
                </select>
              </div>
            </div>
          </div>

          {/* 前沿特色能力快捷体验胶囊 */}
          <div className="mb-3 flex flex-wrap gap-1.5 items-center">
            <span className="text-[10px] text-gray-500 font-bold mr-1">前沿特性体验:</span>
            <button
              type="button"
              onClick={() => {
                setQuestion("华东区昨天的退款额是多少");
                handleSend("华东区昨天的退款额是多少");
              }}
              className="px-2.5 py-1 bg-slate-900/80 hover:bg-purple-950/40 border border-slate-700/80 hover:border-purple-500/40 rounded-lg text-[11px] text-slate-300 hover:text-purple-300 transition-all cursor-pointer flex items-center gap-1"
            >
              <span>💡</span> 基础指标问数
            </button>
            <button
              type="button"
              onClick={() => {
                setQuestion("华东区昨天的退款额是多少");
                handleSend("华东区昨天的退款额是多少");
              }}
              className="px-2.5 py-1 bg-emerald-950/30 hover:bg-emerald-950/60 border border-emerald-500/30 hover:border-emerald-500/60 rounded-lg text-[11px] text-emerald-300 transition-all cursor-pointer flex items-center gap-1"
              title="二次提问相同或相似语义问题，验证 <5ms 多级语义缓存极速命中"
            >
              <span>⚡</span> 语义缓存极速命中
            </button>
            <button
              type="button"
              onClick={() => {
                setQuestion("为什么华东区退款额上升");
                handleSend("为什么华东区退款额上升");
              }}
              className="px-2.5 py-1 bg-amber-950/30 hover:bg-amber-950/60 border border-amber-500/30 hover:border-amber-500/60 rounded-lg text-[11px] text-amber-300 transition-all cursor-pointer flex items-center gap-1"
              title="多维下钻切片与瀑布流贡献率分解"
            >
              <span>📊</span> 退款额异动归因
            </button>
            <button
              type="button"
              onClick={() => {
                setQuestion("GMV指标的数据血缘是怎样的");
                handleSend("GMV指标的数据血缘是怎样的");
              }}
              className="px-2.5 py-1 bg-blue-950/30 hover:bg-blue-950/60 border border-blue-500/30 hover:border-blue-500/60 rounded-lg text-[11px] text-blue-300 transition-all cursor-pointer flex items-center gap-1"
              title="ODS->DWD->DWS->ADS 全链路图谱"
            >
              <span>🌐</span> 湖仓双引擎血缘
            </button>
            <button
              type="button"
              onClick={() => {
                setQuestion("过去30天各品类退款额是多少");
                handleSend("过去30天各品类退款额是多少");
              }}
              className="px-2.5 py-1 bg-red-950/30 hover:bg-red-950/60 border border-red-500/30 hover:border-red-500/60 rounded-lg text-[11px] text-red-300 transition-all cursor-pointer flex items-center gap-1"
              title="按品类查询近30天退款金额"
            >
              <span>📋</span> 品类退款额
            </button>
            <button
              type="button"
              onClick={() => {
                setQuestion("昨天听书各分类播放量是多少");
                handleSend("昨天听书各分类播放量是多少");
              }}
              className="px-2.5 py-1 bg-cyan-950/30 hover:bg-cyan-950/60 border border-cyan-500/30 hover:border-cyan-500/60 rounded-lg text-[11px] text-cyan-300 transition-all cursor-pointer flex items-center gap-1"
              title="ListenBook 听书数仓业务问数"
            >
              <span>🎧</span> 听书业务问数
            </button>
            <button
              type="button"
              onClick={() => {
                setQuestion("为什么听书会员退款额上升");
                handleSend("为什么听书会员退款额上升");
              }}
              className="px-2.5 py-1 bg-indigo-950/30 hover:bg-indigo-950/60 border border-indigo-500/30 hover:border-indigo-500/60 rounded-lg text-[11px] text-indigo-300 transition-all cursor-pointer flex items-center gap-1"
              title="ListenBook 听书会员异动归因下钻"
            >
              <span>📉</span> 听书会员归因
            </button>
          </div>

          <div className="flex gap-3">
            <select
              value={dialect}
              onChange={(e) => setDialect(e.target.value)}
              className="bg-slate-900 border border-slate-700 text-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-purple-500 transition-colors cursor-pointer"
            >
              <option value="doris">Doris 方言</option>
              <option value="clickhouse">ClickHouse 方言</option>
              <option value="starrocks">StarRocks 方言</option>
              <option value="postgres">PostgreSQL 方言</option>
              <option value="mysql">MySQL 方言</option>
              <option value="duckdb">DuckDB 方言</option>
              <option value="sqlite">SQLite 方言</option>
            </select>

            <input
              type="text"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="请输入您要查询的业务数据问题，如: 过去30天华东区GMV是多少？"
              className="flex-1 bg-slate-900 border border-slate-700 text-gray-200 rounded-lg px-4 py-2 text-sm focus:outline-none focus:border-purple-500 transition-colors placeholder:text-gray-600"
              onKeyDown={handleKeyDown}
            />

            <button
              onClick={() => handleSend(question)}
              disabled={loading}
              className="btn-gradient px-5 py-2 text-sm flex items-center gap-1.5 disabled:opacity-50 cursor-pointer"
            >
              {loading ? "执行中..." : "发送"}
            </button>
          </div>

          {/* L3 主动建议推荐 */}
          {recommendations.length > 0 && (
            <div className="mt-4 flex flex-wrap gap-2 items-center">
              <span className="text-xs text-gray-500 font-medium">您可能想问:</span>
              {recommendations.map((rec, idx) => (
                <div key={`${rec}-${idx}`} className="recommendation-chip">
                  <button
                    onClick={() => handleSend(rec)}
                    className="recommendation-chip__action"
                  >
                    📌 {rec}
                  </button>
                  <button
                    type="button"
                    onClick={(e) => handleRemoveRecommendation(e, idx)}
                    className="icon-only-remove recommendation-chip__remove"
                    aria-label={`删除建议：${rec}`}
                    title="移除建议"
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

      </div>

      {/* 右侧：记忆系统与画像面板 */}
      <div className="lg:col-span-4 flex flex-col gap-6 h-full justify-between">

        {/* L2 偏好画像看板 */}
        {preference && (
          <div className="glass-card p-6 bg-gradient-to-br from-slate-900 to-slate-950 border border-slate-800">
            <div className="flex justify-between items-center mb-4">
              <h3 className="text-base font-bold text-gray-200 flex items-center gap-2">
                <span className="text-purple-400">🧠</span>
                用户偏好画像 (L2 记忆)
              </h3>
              {isEditingPreference ? (
                <div className="flex gap-2">
                  <button
                    onClick={handleSavePreference}
                    className="px-2.5 py-1 text-xs font-semibold bg-emerald-600 hover:bg-emerald-500 text-white rounded transition-colors"
                  >
                    保存
                  </button>
                  <button
                    onClick={() => setIsEditingPreference(false)}
                    className="px-2.5 py-1 text-xs font-semibold bg-slate-800 hover:bg-slate-700 text-gray-300 rounded transition-colors"
                  >
                    取消
                  </button>
                </div>
              ) : (
                <button
                  onClick={startEditingPreference}
                  className="px-2.5 py-1 text-xs font-semibold bg-purple-900/40 hover:bg-purple-800/40 border border-purple-500/20 text-purple-300 rounded transition-colors"
                >
                  编辑画像
                </button>
              )}
            </div>

            <div className="flex flex-col gap-4 text-xs">
              {/* 高频触达物理表 */}
              <div>
                <span className="text-gray-500 block mb-1">高频触达物理表 (优先 Schema 检索)</span>
                {isEditingPreference ? (
                  <div className="flex gap-2 items-center">
                    <input
                      type="text"
                      list="tables-list"
                      value={editTable}
                      onChange={(e) => setEditTable(e.target.value)}
                      placeholder="选择或输入物理表"
                      className="flex-1 bg-slate-900 border border-slate-700 text-gray-200 rounded px-2 py-1 text-xs outline-none focus:border-purple-500"
                    />
                    <datalist id="tables-list">
                      <option value="dws_trade_order_daily" />
                      <option value="dwd_trade_order_detail" />
                      <option value="ods_trade_orders" />
                      <option value="dim_region" />
                      <option value="dim_category" />
                      <option value="dim_user_info" />
                    </datalist>
                  </div>
                ) : (
                  preference.common_tables.map((t, idx) => (
                    <div key={idx} className="flex justify-between items-center bg-slate-900/50 border border-slate-800/80 rounded px-2.5 py-1.5 mb-1">
                      <span className="font-mono text-purple-300">{t.table}</span>
                      <span className="text-gray-500">权重: {t.count}</span>
                    </div>
                  ))
                )}
              </div>

              {/* 常用分析指标 */}
              <div>
                <span className="text-gray-500 block mb-1">常用分析指标 (自动语义对齐)</span>
                {isEditingPreference ? (
                  <div className="flex gap-2 items-center">
                    <input
                      type="text"
                      list="metrics-list"
                      value={editMetric}
                      onChange={(e) => setEditMetric(e.target.value)}
                      placeholder="选择或输入指标"
                      className="flex-1 bg-slate-900 border border-slate-700 text-gray-200 rounded px-2 py-1 text-xs outline-none focus:border-purple-500"
                    />
                    <datalist id="metrics-list">
                      <option value="gmv" />
                      <option value="refund_amount" />
                      <option value="order_count" />
                      <option value="user_count" />
                    </datalist>
                  </div>
                ) : (
                  <div className="flex flex-wrap gap-1.5">
                    {preference.common_metrics.map((m, idx) => (
                      <span key={idx} className="bg-blue-950/20 border border-blue-500/30 text-blue-300 px-2.5 py-1 rounded">
                        📊 {m.metric === "gmv" ? "GMV" : m.metric} ({m.count}次)
                      </span>
                    ))}
                  </div>
                )}
              </div>

              {/* 高频分组维度 */}
              <div>
                <span className="text-gray-500 block mb-1">高频分组维度</span>
                {isEditingPreference ? (
                  <div className="flex gap-2 items-center">
                    <input
                      type="text"
                      list="dimensions-list"
                      value={editDimension}
                      onChange={(e) => setEditDimension(e.target.value)}
                      placeholder="选择或输入维度"
                      className="flex-1 bg-slate-900 border border-slate-700 text-gray-200 rounded px-2 py-1 text-xs outline-none focus:border-purple-500"
                    />
                    <datalist id="dimensions-list">
                      <option value="region_name" />
                      <option value="category_name" />
                      <option value="dt" />
                    </datalist>
                  </div>
                ) : (
                  <div className="flex flex-wrap gap-1.5">
                    {preference.common_dimensions.map((d, idx) => (
                      <span key={idx} className="bg-emerald-950/20 border border-emerald-500/30 text-emerald-300 px-2.5 py-1 rounded">
                        🔍 {d.dimension === "region_name" ? "区域" : (d.dimension === "category_name" ? "品类" : d.dimension)} ({d.count}次)
                      </span>
                    ))}
                  </div>
                )}
              </div>

              {/* 偏好查询范围 */}
              <div>
                <span className="text-gray-500 block mb-1">偏好查询范围</span>
                {isEditingPreference ? (
                  <div className="flex gap-2 items-center">
                    <input
                      type="text"
                      list="ranges-list"
                      value={editRange}
                      onChange={(e) => setEditRange(e.target.value)}
                      placeholder="选择或输入范围"
                      className="flex-1 bg-slate-900 border border-slate-700 text-gray-200 rounded px-2 py-1 text-xs outline-none focus:border-purple-500"
                    />
                    <datalist id="ranges-list">
                      <option value="过去30天" />
                      <option value="趋势/近6月" />
                      <option value="今日" />
                      <option value="过去7天" />
                      <option value="过去90天" />
                    </datalist>
                  </div>
                ) : (
                  <div className="flex flex-wrap gap-1.5">
                    {preference.common_time_ranges.map((r, idx) => (
                      <span key={idx} className="bg-pink-950/20 border border-pink-500/30 text-pink-300 px-2.5 py-1 rounded">
                        🕒 {r.range} ({r.count}次)
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>

            <p className="text-[10px] text-gray-600 mt-4 text-center">
              * 画像每日离线更新，在问数时将辅助消歧和表权重排序
            </p>
          </div>
        )}

        {/* L1 历史记录与一键重跑 */}
        <div className="glass-card p-6 bg-gradient-to-br from-slate-900 to-slate-950 border border-slate-800 flex-1 min-h-0 flex flex-col mt-auto">
          <h3 className="text-base font-bold text-gray-200 mb-4 flex items-center gap-2">
            <span className="text-blue-400">🔄</span>
            历史查询与重跑 (L1 记忆)
          </h3>

          <div ref={historyListRef} className="flex flex-col gap-3 overflow-y-auto pr-1 flex-1 min-h-0">
            {history.length === 0 ? (
              <p className="text-xs text-gray-600 text-center py-6">暂无历史查询记录</p>
            ) : (
              visibleHistory.map((record) => (
                <div
                  key={record.id}
                  data-history-record=""
                  className="bg-slate-900/60 hover:bg-purple-950/10 border border-slate-800 hover:border-purple-500/20 rounded-lg p-3 cursor-pointer group transition-all relative"
                  onClick={() => handleSend(record.question)}
                >
                  <div className="flex justify-between items-start gap-2 mb-1.5">
                    <span className="history-record__title text-xs font-semibold text-gray-300 line-clamp-1 group-hover:text-purple-300 transition-colors">
                      {record.question}
                    </span>
                    <div className="history-record__actions">
                      <span className="text-[9px] bg-slate-800 text-gray-400 px-1.5 py-0.5 rounded uppercase font-mono">
                        {record.dialect}
                      </span>
                      <button 
                        type="button"
                        onClick={(e) => handleDeleteHistory(e, record.id)}
                        className="icon-only-remove history-record__remove"
                        aria-label={`删除历史记录：${record.question}`}
                        title="删除记录"
                      >
                        ×
                      </button>
                    </div>
                  </div>
                  <p className="text-[10px] text-gray-500 line-clamp-2 leading-relaxed">
                    {record.result_summary}
                  </p>
                  <div className="flex justify-between items-center mt-2 pt-2 border-t border-slate-800/40 text-[9px] text-gray-600">
                    <span>{record.created_at}</span>
                    <span className="text-purple-400 opacity-0 group-hover:opacity-100 transition-opacity">一键重跑 ↺</span>
                  </div>
                </div>
              ))
            )}
          </div>

          {totalHistoryPages > 1 && (
            <nav className="history-pagination mt-auto pt-3 border-t border-slate-800/50" aria-label="历史查询分页">
              <button
                type="button"
                className="history-pagination__button"
                onClick={() => setHistoryPage(page => Math.max(1, page - 1))}
                disabled={currentHistoryPage === 1}
              >
                上一页
              </button>
              <span className="history-pagination__status text-xs text-gray-400 font-medium">
                第 {currentHistoryPage} / {totalHistoryPages} 页
              </span>
              <button
                type="button"
                className="history-pagination__button"
                onClick={() => setHistoryPage(page => Math.min(totalHistoryPages, page + 1))}
                disabled={currentHistoryPage === totalHistoryPages}
              >
                下一页
              </button>
            </nav>
          )}
        </div>

      </div>
    </div>
  );
};
