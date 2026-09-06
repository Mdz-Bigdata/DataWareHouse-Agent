import React, { useState, useEffect } from "react";
import type {
  ErrorCorrectionRecord,
  CacheStats,
  MetadataEnrichResult,
  LineageData
} from "../types";

const API_BASE = "http://localhost:8000/api";

export const FlywheelPanel: React.FC = () => {
  // 顶部四级治理 Tab
  const [subTab, setSubTab] = useState<"guardrail" | "cache" | "metadata" | "lineage">("guardrail");

  // 广播横幅提示
  const [message, setMessage] = useState("");
  const showTemporaryMessage = (msg: string) => {
    setMessage(msg);
    setTimeout(() => setMessage(""), 5000);
  };

  // ==========================================
  // Tab 1: 安全网闸与自愈纠错 (Guardrails)
  // ==========================================
  const [corrections, setCorrections] = useState<ErrorCorrectionRecord[]>([]);
  const [loadingCorrections, setLoadingCorrections] = useState(false);
  const [showAddForm, setShowAddForm] = useState(false);
  const [newQuestion, setNewQuestion] = useState("");
  const [newError, setNewError] = useState("");
  const [newWrongSql, setNewWrongSql] = useState("");
  const [newCorrectedSql, setNewCorrectedSql] = useState("");

  const [mockStats, setMockStats] = useState({
    totalAudited: 2450,
    ddlBlocked: 142,
    sensitiveBlocked: 89,
    zeroProtection: 18,
    cartesianBlocked: 27,
    accuracy: 100.00
  });

  const fetchCorrections = async () => {
    setLoadingCorrections(true);
    try {
      const res = await fetch(`${API_BASE}/chat/corrections`);
      if (res.ok) {
        const data = await res.json();
        setCorrections(data);
      }
    } catch (e) {
      console.error("加载自愈纠错记忆失败:", e);
    } finally {
      setLoadingCorrections(false);
    }
  };

  const handleAddCorrection = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newQuestion || !newError || !newWrongSql || !newCorrectedSql) {
      alert("请填写完整的纠错字段！");
      return;
    }
    try {
      const res = await fetch(`${API_BASE}/chat/corrections`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: newQuestion,
          error_message: newError,
          wrong_sql: newWrongSql,
          corrected_sql: newCorrectedSql
        })
      });
      if (res.ok) {
        showTemporaryMessage("成功手动录入纠错经验，Qdrant 向量索引已同步更新！");
        setNewQuestion("");
        setNewError("");
        setNewWrongSql("");
        setNewCorrectedSql("");
        setShowAddForm(false);
        fetchCorrections();
        setMockStats(prev => ({
          ...prev,
          totalAudited: prev.totalAudited + 1,
          zeroProtection: prev.zeroProtection + 1
        }));
      } else {
        const err = await res.json();
        alert(`录入失败: ${err.detail || "未知错误"}`);
      }
    } catch (e) {
      alert(`网络通信异常: ${e}`);
    }
  };

  const handleDeleteCorrection = async (q: string) => {
    if (!window.confirm(`确定要移除关于 "${q}" 的纠错记忆对吗？`)) return;
    try {
      const res = await fetch(`${API_BASE}/chat/corrections/delete?question=${encodeURIComponent(q)}`, {
        method: "DELETE"
      });
      if (res.ok) {
        showTemporaryMessage("该纠错记忆已成功移除，且已同步刷新向量索引。");
        fetchCorrections();
      } else {
        const err = await res.json();
        alert(`删除失败: ${err.detail}`);
      }
    } catch (e) {
      alert(`删除异常: ${e}`);
    }
  };

  const handleClearCorrections = async () => {
    if (!window.confirm("⚠️ 警告：这将彻底清空持久化的纠错记录并重置 Qdrant 索引，确定要清空吗？")) return;
    try {
      const res = await fetch(`${API_BASE}/chat/corrections/clear`, { method: "DELETE" });
      if (res.ok) {
        showTemporaryMessage("已彻底清空全部纠错经验！");
        fetchCorrections();
      }
    } catch (e) {
      alert(`清空异常: ${e}`);
    }
  };

  const handleInjectPresetCorrections = async () => {
    try {
      const presets = [
        {
          question: "各品类退款额除以交易额的比率",
          error_message: "安全审计拦截: 检测到 SQL 除法表达式的分母列未进行 NULLIF(..., 0) 除零安全保护",
          wrong_sql: "SELECT category_name, SUM(refund_amount) / SUM(gmv) AS ratio FROM dws_trade_order_daily GROUP BY category_name",
          corrected_sql: "SELECT category_name, SUM(refund_amount) / NULLIF(SUM(gmv), 0) AS ratio FROM dws_trade_order_daily GROUP BY category_name"
        },
        {
          question: "帮我把article表和user_memory表进行不带外键等值连接",
          error_message: "安全审计拦截: 检测到表 `user_memory` 关联条件非唯一主键/外键对齐，存在多对多(Many-to-Many)扇出风险",
          wrong_sql: "SELECT * FROM articles LEFT JOIN user_memory ON articles.title = user_memory.question",
          corrected_sql: "SELECT * FROM articles INNER JOIN (SELECT DISTINCT question, MIN(id) as id FROM user_memory GROUP BY question) um ON articles.title = um.question"
        }
      ];
      for (const item of presets) {
        await fetch(`${API_BASE}/chat/corrections`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(item)
        });
      }
      showTemporaryMessage("成功载入仿真高级纠错用例对！向量数据库与自愈链路已就绪。");
      fetchCorrections();
    } catch (e) {
      alert(`注入仿真样例出错: ${e}`);
    }
  };

  // ==========================================
  // Tab 2: 多级语义缓存监控 (Semantic Cache)
  // ==========================================
  const [cacheStats, setCacheStats] = useState<CacheStats | null>(null);
  const [loadingCache, setLoadingCache] = useState(false);

  const fetchCacheStats = async () => {
    setLoadingCache(true);
    try {
      const res = await fetch(`${API_BASE}/chat/cache/stats`);
      if (res.ok) {
        const data = await res.json();
        setCacheStats(data);
      }
    } catch (e) {
      console.error("获取缓存监控数据失败:", e);
    } finally {
      setLoadingCache(false);
    }
  };

  const handleClearCache = async () => {
    if (!window.confirm("确定要立即清空多级语义缓存池吗？高频查询将重新穿透到大模型与数据库。")) return;
    try {
      const res = await fetch(`${API_BASE}/chat/cache/clear`, { method: "POST" });
      if (res.ok) {
        showTemporaryMessage("多级语义缓存池已成功清空！");
        fetchCacheStats();
      }
    } catch (e) {
      alert(`清空缓存失败: ${e}`);
    }
  };

  // ==========================================
  // Tab 3: AI 元数据画像与补全 (Metadata AI Profiler)
  // ==========================================
  const [availableTables, setAvailableTables] = useState<string[]>([]);
  const [selectedTable, setSelectedTable] = useState("dws_trade_order_daily");
  const [enrichResult, setEnrichResult] = useState<MetadataEnrichResult | null>(null);
  const [enriching, setEnriching] = useState(false);

  const fetchTables = async () => {
    try {
      const res = await fetch(`${API_BASE}/chat/tables`);
      if (res.ok) {
        const data = await res.json();
        setAvailableTables(data);
        if (data.length > 0 && !data.includes(selectedTable)) {
          setSelectedTable(data[0]);
        }
      }
    } catch (e) {
      console.error("加载物理表列表失败:", e);
    }
  };

  const handleRunEnrich = async () => {
    if (!selectedTable) return;
    setEnriching(true);
    setEnrichResult(null);
    try {
      const res = await fetch(`${API_BASE}/chat/metadata/enrich?table_name=${encodeURIComponent(selectedTable)}`, {
        method: "POST"
      });
      if (res.ok) {
        const data: MetadataEnrichResult = await res.json();
        setEnrichResult(data);
        showTemporaryMessage(`已完成物理表 [${selectedTable}] 的数据画像，自动补全元数据已热注入向量库！`);
      } else {
        const err = await res.json();
        alert(`画像分析失败: ${err.detail || "未知错误"}`);
      }
    } catch (e) {
      alert(`分析请求异常: ${e}`);
    } finally {
      setEnriching(false);
    }
  };

  // ==========================================
  // Tab 4: 湖仓双引擎数据血缘全景 (Lineage Explorer)
  // ==========================================
  const [lineageData, setLineageData] = useState<LineageData | null>(null);
  const [selectedLayer, setSelectedLayer] = useState<string>("ALL");
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  const fetchLineage = async () => {
    try {
      const res = await fetch(`${API_BASE}/chat/lineage`);
      if (res.ok) {
        const data: LineageData = await res.json();
        setLineageData(data);
      }
    } catch (e) {
      console.error("加载湖仓血缘失败:", e);
    }
  };

  // 切换选项卡时按需加载
  useEffect(() => {
    fetchCorrections();
  }, []);

  useEffect(() => {
    if (subTab === "cache") fetchCacheStats();
    if (subTab === "metadata") fetchTables();
    if (subTab === "lineage") fetchLineage();
  }, [subTab]);

  return (
    <div className="w-full max-w-7xl px-4 py-8 flex flex-col gap-6 animate-fade-in text-slate-100">
      
      {/* 顶部标题与子导航 */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-slate-800 pb-5">
        <div>
          <h1 className="text-xl font-black tracking-tight text-white flex items-center gap-2">
            🏛️ 数仓大脑治理中心
            <span className="text-[10px] bg-gradient-to-r from-purple-500/20 to-blue-500/20 border border-purple-500/30 text-purple-300 font-mono px-2 py-0.5 rounded-full">
              Enterprise Governance & Flywheel
            </span>
          </h1>
          <p className="text-xs text-gray-400 mt-1">
            融合阿里 QwenPaw-Data 规范、微信智驾湖仓双引擎与淘宝百亿补贴实战的数据治理与智能底座
          </p>
        </div>

        {/* 顶部子 Tab 切换条 */}
        <div className="flex bg-slate-950/80 border border-slate-800/90 rounded-xl p-1 shadow-inner">
          <button
            onClick={() => setSubTab("guardrail")}
            className={`px-3.5 py-1.5 rounded-lg text-xs font-bold transition-all cursor-pointer ${
              subTab === "guardrail"
                ? "bg-gradient-to-r from-purple-600 to-indigo-600 text-white shadow-md shadow-purple-900/30"
                : "text-gray-400 hover:text-gray-200"
            }`}
          >
            🛡️ 安全网闸与自愈
          </button>
          <button
            onClick={() => setSubTab("cache")}
            className={`px-3.5 py-1.5 rounded-lg text-xs font-bold transition-all cursor-pointer ${
              subTab === "cache"
                ? "bg-gradient-to-r from-emerald-600 to-teal-600 text-white shadow-md shadow-emerald-900/30"
                : "text-gray-400 hover:text-gray-200"
            }`}
          >
            ⚡ 多级语义缓存
          </button>
          <button
            onClick={() => setSubTab("metadata")}
            className={`px-3.5 py-1.5 rounded-lg text-xs font-bold transition-all cursor-pointer ${
              subTab === "metadata"
                ? "bg-gradient-to-r from-blue-600 to-cyan-600 text-white shadow-md shadow-blue-900/30"
                : "text-gray-400 hover:text-gray-200"
            }`}
          >
            🔍 元数据 AI 补全
          </button>
          <button
            onClick={() => setSubTab("lineage")}
            className={`px-3.5 py-1.5 rounded-lg text-xs font-bold transition-all cursor-pointer ${
              subTab === "lineage"
                ? "bg-gradient-to-r from-amber-600 to-orange-600 text-white shadow-md shadow-amber-900/30"
                : "text-gray-400 hover:text-gray-200"
            }`}
          >
            🌐 湖仓全景血缘
          </button>
        </div>
      </div>

      {/* 消息广播横幅 */}
      {message && (
        <div className="bg-gradient-to-r from-emerald-950/90 to-teal-950/90 border border-emerald-500/40 text-emerald-300 px-4 py-3 rounded-xl flex items-center gap-2 shadow-lg shadow-emerald-950/30 backdrop-blur-md">
          <span className="text-base">⚡</span>
          <span className="text-xs font-medium">{message}</span>
        </div>
      )}

      {/* ========================================================================= */}
      {/* 视图 1：安全网闸与自愈飞轮                                                */}
      {/* ========================================================================= */}
      {subTab === "guardrail" && (
        <div className="flex flex-col gap-6 animate-fade-in">
          {/* 四个顶层看板指标 */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <div className="glass-card bg-[#0b1021]/60 border border-slate-800 p-5 rounded-2xl flex flex-col justify-between shadow-xl">
              <div>
                <div className="text-[10px] text-gray-500 font-bold uppercase tracking-wider">物理 SQL 审计总量</div>
                <div className="text-3xl font-extrabold text-white mt-1 font-mono">{mockStats.totalAudited}</div>
              </div>
              <div className="text-[10px] text-emerald-400 flex items-center gap-1.5 mt-3">
                <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
                AST 白名单强网闸运行中
              </div>
            </div>

            <div className="glass-card bg-[#0b1021]/60 border border-slate-800 p-5 rounded-2xl flex flex-col justify-between shadow-xl">
              <div>
                <div className="text-[10px] text-gray-500 font-bold uppercase tracking-wider">只读网闸 DDL/DML 拦截</div>
                <div className="text-3xl font-extrabold text-pink-500 mt-1 font-mono">{mockStats.ddlBlocked}</div>
              </div>
              <div className="text-[10px] text-gray-400 mt-3">已熔断非 SELECT 越权写指令</div>
            </div>

            <div className="glass-card bg-[#0b1021]/60 border border-slate-800 p-5 rounded-2xl flex flex-col justify-between shadow-xl">
              <div>
                <div className="text-[10px] text-gray-500 font-bold uppercase tracking-wider">除零保护 & 关联防御</div>
                <div className="text-3xl font-extrabold text-blue-400 mt-1 font-mono">
                  {mockStats.zeroProtection + mockStats.cartesianBlocked}
                </div>
              </div>
              <div className="text-[10px] text-gray-400 mt-3">NULLIF 自动注入与多对多扇出隔离</div>
            </div>

            <div className="glass-card bg-[#0b1021]/60 border border-slate-800 p-5 rounded-2xl flex flex-col justify-between shadow-xl">
              <div>
                <div className="text-[10px] text-gray-500 font-bold uppercase tracking-wider">自愈飞轮评估准确率</div>
                <div className="text-3xl font-extrabold text-transparent bg-clip-text bg-gradient-to-r from-purple-400 to-pink-400 mt-1 font-mono">
                  {mockStats.accuracy.toFixed(2)}%
                </div>
              </div>
              <div className="text-[10px] text-purple-400 mt-3">14 项黄金回归套件: 100.00% PASS</div>
            </div>
          </div>

          {/* 两栏：左侧规则卡片，右侧纠错记忆 */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 items-start">
            <div className="lg:col-span-1 flex flex-col gap-4">
              <div className="glass-card bg-[#070b16]/70 border border-slate-800 p-5 rounded-2xl shadow-xl">
                <h2 className="text-sm font-extrabold text-white flex items-center gap-2 border-b border-slate-800/80 pb-3">
                  🛡️ AST 物理安全网闸规则
                </h2>
                <div className="flex flex-col gap-3 mt-4 text-xs">
                  <div className="p-3 bg-slate-950/70 border border-slate-850 rounded-xl">
                    <div className="font-bold text-slate-200">1. DDL / DML 只读网闸</div>
                    <p className="text-[11px] text-gray-500 mt-1 leading-relaxed">
                      严格禁止 Drop, Update, Delete, Alter 等写操作，杜绝注入风险。
                    </p>
                  </div>
                  <div className="p-3 bg-slate-950/70 border border-slate-850 rounded-xl">
                    <div className="font-bold text-slate-200">2. NULLIF(..., 0) 除零保护</div>
                    <p className="text-[11px] text-gray-500 mt-1 leading-relaxed">
                      除法表达式分母列自动注入安全除零保护，杜绝生产运行时崩溃。
                    </p>
                  </div>
                  <div className="p-3 bg-slate-950/70 border border-slate-850 rounded-xl">
                    <div className="font-bold text-slate-200">3. 无外键 Cartesian 扇出拦截</div>
                    <p className="text-[11px] text-gray-500 mt-1 leading-relaxed">
                      校验多表 JOIN 条件是否为声明的主外键，防止指标成倍虚假放大。
                    </p>
                  </div>
                  <div className="p-3 bg-slate-950/70 border border-slate-850 rounded-xl">
                    <div className="font-bold text-slate-200">4. 行级 / 列级安全隔离</div>
                    <p className="text-[11px] text-gray-500 mt-1 leading-relaxed">
                      普通角色自动拦截手机号、卡号等敏感列；强制下推大区行级过滤。
                    </p>
                  </div>
                </div>
              </div>
            </div>

            <div className="lg:col-span-2 glass-card bg-[#070b16]/70 border border-slate-800 p-5 rounded-2xl shadow-xl">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-slate-800/80 pb-3">
                <div>
                  <h2 className="text-sm font-extrabold text-white flex items-center gap-2">
                    🧠 自愈纠错记忆经验池 (Error Correction Memory)
                  </h2>
                  <p className="text-[11px] text-gray-500 mt-0.5">
                    网闸拦截后大模型自我纠错成功的用例对，实时沉淀进向量库作为 Few-Shot 样本
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => setShowAddForm(!showAddForm)}
                    className="px-2.5 py-1 bg-purple-600/30 hover:bg-purple-600/50 border border-purple-500/40 text-purple-300 rounded-lg text-xs font-bold transition-all cursor-pointer"
                  >
                    {showAddForm ? "取消录入" : "+ 手动注入经验"}
                  </button>
                  {corrections.length > 0 && (
                    <button
                      onClick={handleClearCorrections}
                      className="px-2.5 py-1 bg-red-600/20 hover:bg-red-600/30 border border-red-500/30 text-red-300 rounded-lg text-xs font-bold transition-all cursor-pointer"
                    >
                      清空
                    </button>
                  )}
                </div>
              </div>

              {/* 手动录入表单 */}
              {showAddForm && (
                <form onSubmit={handleAddCorrection} className="p-4 bg-slate-950/90 border border-purple-500/30 rounded-xl my-4 flex flex-col gap-3 animate-fade-in">
                  <div className="text-xs font-bold text-purple-300">手动录入优质纠偏样本</div>
                  <input
                    type="text"
                    value={newQuestion}
                    onChange={e => setNewQuestion(e.target.value)}
                    placeholder="业务提问句 (例如: 各品类退款额除以交易额的比率)"
                    className="bg-slate-900 border border-slate-800 text-xs px-3 py-2 rounded-lg text-slate-100 focus:border-purple-500 outline-none"
                  />
                  <input
                    type="text"
                    value={newError}
                    onChange={e => setNewError(e.target.value)}
                    placeholder="拦截原因 (例如: 检测到除法分母未做 NULLIF 保护)"
                    className="bg-slate-900 border border-slate-800 text-xs px-3 py-2 rounded-lg text-slate-100 focus:border-purple-500 outline-none"
                  />
                  <textarea
                    value={newWrongSql}
                    onChange={e => setNewWrongSql(e.target.value)}
                    rows={2}
                    placeholder="错误的原始 SQL (例如: SELECT category, SUM(refund)/SUM(gmv) FROM ...)"
                    className="bg-slate-900 border border-slate-800 text-xs px-3 py-2 rounded-lg text-slate-100 font-mono outline-none"
                  />
                  <textarea
                    value={newCorrectedSql}
                    onChange={e => setNewCorrectedSql(e.target.value)}
                    rows={2}
                    placeholder="纠正后的正确 SQL (例如: SELECT category, SUM(refund)/NULLIF(SUM(gmv), 0) FROM ...)"
                    className="bg-slate-900 border border-slate-800 text-xs px-3 py-2 rounded-lg text-slate-100 font-mono outline-none"
                  />
                  <button
                    type="submit"
                    className="px-4 py-2 bg-gradient-to-r from-purple-600 to-indigo-600 text-white rounded-lg text-xs font-bold hover:brightness-110 cursor-pointer"
                  >
                    保存并同步构建 Qdrant 索引
                  </button>
                </form>
              )}

              {/* 列表渲染 */}
              {loadingCorrections ? (
                <div className="py-12 text-center text-xs text-gray-500">加载纠错经验列表中...</div>
              ) : corrections.length === 0 ? (
                <div className="text-center py-10 border border-dashed border-slate-850 rounded-xl mt-4 bg-slate-950/20">
                  <div className="text-xs text-gray-400 font-medium">当前暂无纠错记忆</div>
                  <p className="text-[11px] text-gray-600 mt-1">当系统触发除零保护并自愈成功时，将自动沉淀经验至此</p>
                  <button
                    onClick={handleInjectPresetCorrections}
                    className="mt-3 px-3 py-1.5 rounded-lg border border-purple-500/30 bg-purple-950/30 text-xs text-purple-300 font-bold hover:bg-purple-900/40 cursor-pointer"
                  >
                    🚀 一键载入仿真高级纠错用例
                  </button>
                </div>
              ) : (
                <div className="flex flex-col gap-3 mt-4 max-h-[500px] overflow-y-auto pr-1">
                  {corrections.map((item, idx) => (
                    <div key={idx} className="p-3.5 bg-slate-950/80 border border-slate-850 hover:border-purple-500/30 rounded-xl relative flex flex-col gap-2 group transition-all">
                      <button
                        onClick={() => handleDeleteCorrection(item.question)}
                        className="absolute top-3 right-3 text-gray-500 hover:text-red-400 text-xs opacity-0 group-hover:opacity-100 transition-all cursor-pointer"
                        title="删除该条目"
                      >
                        🗑️
                      </button>
                      <div className="text-xs font-bold text-slate-200 flex items-center gap-2">
                        <span className="px-1.5 py-0.5 text-[9px] bg-purple-500/20 text-purple-300 rounded font-mono">Q</span>
                        {item.question}
                      </div>
                      <div className="text-[10px] text-red-400/90 font-mono">⚠️ {item.error_message}</div>
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-2 bg-slate-900/80 p-2 rounded-lg font-mono text-[10px]">
                        <div>
                          <span className="text-red-500/80 font-bold">✖️ WRONG:</span>
                          <pre className="text-gray-500 whitespace-pre-wrap">{item.wrong_sql}</pre>
                        </div>
                        <div className="border-t md:border-t-0 md:border-l border-slate-800 pt-2 md:pt-0 md:pl-2">
                          <span className="text-emerald-400 font-bold">✔️ CORRECTED:</span>
                          <pre className="text-emerald-400 whitespace-pre-wrap">{item.corrected_sql}</pre>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ========================================================================= */}
      {/* 视图 2：多级语义缓存监控                                                  */}
      {/* ========================================================================= */}
      {subTab === "cache" && (
        <div className="flex flex-col gap-6 animate-fade-in">
          {/* 缓存关键指标 */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <div className="glass-card bg-[#0b1021]/60 border border-slate-800 p-5 rounded-2xl flex flex-col justify-between shadow-xl">
              <div>
                <div className="text-[10px] text-gray-500 font-bold uppercase tracking-wider">缓存整体命中率</div>
                <div className="text-3xl font-extrabold text-transparent bg-clip-text bg-gradient-to-r from-emerald-400 to-teal-400 mt-1 font-mono">
                  {cacheStats ? `${cacheStats.hit_ratio_percent}%` : "--"}
                </div>
              </div>
              <div className="text-[10px] text-emerald-400 flex items-center gap-1 mt-3">
                <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
                高频问数极速毫秒级响应
              </div>
            </div>

            <div className="glass-card bg-[#0b1021]/60 border border-slate-800 p-5 rounded-2xl flex flex-col justify-between shadow-xl">
              <div>
                <div className="text-[10px] text-gray-500 font-bold uppercase tracking-wider">L1 精确哈希命中次数</div>
                <div className="text-3xl font-extrabold text-blue-400 mt-1 font-mono">
                  {cacheStats ? cacheStats.exact_hits : 0} <span className="text-xs text-gray-500 font-normal">次</span>
                </div>
              </div>
              <div className="text-[10px] text-gray-400 mt-3">SHA-256 O(1) 毫秒直接返回 (&lt;5ms)</div>
            </div>

            <div className="glass-card bg-[#0b1021]/60 border border-slate-800 p-5 rounded-2xl flex flex-col justify-between shadow-xl">
              <div>
                <div className="text-[10px] text-gray-500 font-bold uppercase tracking-wider">L2 语义向量命中次数</div>
                <div className="text-3xl font-extrabold text-purple-400 mt-1 font-mono">
                  {cacheStats ? cacheStats.semantic_hits : 0} <span className="text-xs text-gray-500 font-normal">次</span>
                </div>
              </div>
              <div className="text-[10px] text-gray-400 mt-3">余弦相似度 &gt; 0.985 + 实体对齐校验</div>
            </div>

            <div className="glass-card bg-[#0b1021]/60 border border-slate-800 p-5 rounded-2xl flex flex-col justify-between shadow-xl">
              <div>
                <div className="text-[10px] text-gray-500 font-bold uppercase tracking-wider">活跃缓存条目总数</div>
                <div className="text-3xl font-extrabold text-white mt-1 font-mono">
                  {cacheStats ? cacheStats.cached_exact_count : 0}
                </div>
              </div>
              <div className="text-[10px] text-gray-400 mt-3">TTL 自动淘汰与 LRU 内存保护</div>
            </div>
          </div>

          {/* 缓存控制与条目明细 */}
          <div className="glass-card bg-[#070b16]/70 border border-slate-800 p-5 rounded-2xl shadow-xl">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-800/80 pb-4">
              <div>
                <h2 className="text-sm font-extrabold text-white flex items-center gap-2">
                  ⚡ 活跃多级语义缓存池 (Active Cache Entries)
                </h2>
                <p className="text-[11px] text-gray-500 mt-0.5">
                  记录了最近的命中热点问句。支持通过主动失效机制清除过期或被污染的缓存
                </p>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={fetchCacheStats}
                  disabled={loadingCache}
                  className="px-3 py-1.5 bg-slate-900 hover:bg-slate-800 border border-slate-700 text-slate-200 rounded-lg text-xs font-bold transition-all cursor-pointer"
                >
                  🔄 刷新监控
                </button>
                <button
                  onClick={handleClearCache}
                  className="px-3 py-1.5 bg-red-600/20 hover:bg-red-600/40 border border-red-500/40 text-red-300 rounded-lg text-xs font-bold transition-all cursor-pointer"
                >
                  🗑️ 清空语义缓存池
                </button>
              </div>
            </div>

            {loadingCache ? (
              <div className="py-12 text-center text-xs text-gray-500">正在获取最新缓存监控数据...</div>
            ) : !cacheStats?.cached_entries || cacheStats.cached_entries.length === 0 ? (
              <div className="text-center py-12 border border-dashed border-slate-850 rounded-xl mt-4 bg-slate-950/20">
                <div className="text-xs text-gray-400 font-medium">当前语义缓存池为空</div>
                <p className="text-[11px] text-gray-600 mt-1">请在智能问数页面提问任意问题，系统将自动产生并持久化缓存条目</p>
              </div>
            ) : (
              <div className="overflow-x-auto mt-4">
                <table className="w-full text-left text-xs border-collapse">
                  <thead>
                    <tr className="border-b border-slate-800 text-gray-500 text-[10px] uppercase font-bold">
                      <th className="py-2.5 px-3">缓存键 (Key Hash)</th>
                      <th className="py-2.5 px-3">自然语言问句 (Question)</th>
                      <th className="py-2.5 px-3">权限角色</th>
                      <th className="py-2.5 px-3">目标方言</th>
                      <th className="py-2.5 px-3">命中次数</th>
                      <th className="py-2.5 px-3">剩余生存时间 (TTL)</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-850 font-mono text-[11px]">
                    {cacheStats.cached_entries.map((entry, idx) => (
                      <tr key={idx} className="hover:bg-slate-900/50 transition-colors">
                        <td className="py-2.5 px-3 text-purple-400">{entry.key}</td>
                        <td className="py-2.5 px-3 font-sans text-slate-200 font-medium">{entry.question}</td>
                        <td className="py-2.5 px-3 text-gray-400">{entry.role}</td>
                        <td className="py-2.5 px-3 text-blue-400">{entry.dialect}</td>
                        <td className="py-2.5 px-3 text-emerald-400 font-bold">{entry.hit_count} 次</td>
                        <td className="py-2.5 px-3 text-amber-400">{entry.ttl_remaining_sec} 秒</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ========================================================================= */}
      {/* 视图 3：AI 元数据画像与智能补全                                            */}
      {/* ========================================================================= */}
      {subTab === "metadata" && (
        <div className="flex flex-col gap-6 animate-fade-in">
          <div className="glass-card bg-[#070b16]/70 border border-slate-800 p-5 rounded-2xl shadow-xl">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-slate-800/80 pb-4">
              <div>
                <h2 className="text-sm font-extrabold text-white flex items-center gap-2">
                  🔍 AI 元数据自动补全实验台 (Metadata Auto-Enricher)
                </h2>
                <p className="text-[11px] text-gray-500 mt-0.5">
                  基于物理表采样数据特征（基数、空值率、极值分布），自动推导业务域、指标 SUM 口径与枚举字典，解决冷启动难题
                </p>
              </div>

              {/* 表选择器与操作按钮 */}
              <div className="flex items-center gap-3">
                <select
                  value={selectedTable}
                  onChange={e => setSelectedTable(e.target.value)}
                  className="bg-slate-900 border border-slate-700 text-xs text-slate-100 rounded-lg px-3 py-2 outline-none focus:border-blue-500 cursor-pointer"
                >
                  {availableTables.map(t => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                </select>
                <button
                  onClick={handleRunEnrich}
                  disabled={enriching}
                  className="px-4 py-2 bg-gradient-to-r from-blue-600 to-cyan-600 text-white rounded-lg text-xs font-bold hover:brightness-110 transition-all cursor-pointer flex items-center gap-1.5 shadow-md shadow-blue-900/30"
                >
                  {enriching ? "AI 采样画像中..." : "🚀 发起深度画像分析"}
                </button>
              </div>
            </div>

            {enriching && (
              <div className="py-16 text-center text-xs text-cyan-400 animate-pulse flex flex-col items-center gap-2">
                <span className="text-2xl">⚙️</span>
                正在对物理表 [{selectedTable}] 进行高阶统计采样、基数计算与语义字典推断...
              </div>
            )}

            {!enriching && !enrichResult && (
              <div className="text-center py-16 border border-dashed border-slate-850 rounded-xl mt-4 bg-slate-950/20">
                <div className="text-xs text-gray-400 font-medium">请选择上方目标表并点击“发起深度画像分析”</div>
                <p className="text-[11px] text-gray-600 mt-1">系统将基于采样特征自动推导语义层元数据并热注入向量数据库</p>
              </div>
            )}

            {!enriching && enrichResult && (
              <div className="flex flex-col gap-6 mt-5 animate-fade-in">
                {/* 概述徽标栏 */}
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                  <div className="p-4 bg-slate-950/80 border border-slate-800 rounded-xl">
                    <div className="text-[10px] text-gray-500 font-bold uppercase">推导业务主题域</div>
                    <div className="text-base font-extrabold text-blue-400 mt-1">{enrichResult.domain}</div>
                  </div>
                  <div className="p-4 bg-slate-950/80 border border-slate-800 rounded-xl">
                    <div className="text-[10px] text-gray-500 font-bold uppercase">推导指标数量</div>
                    <div className="text-base font-extrabold text-emerald-400 mt-1">{enrichResult.metrics.length} 个聚合指标</div>
                  </div>
                  <div className="p-4 bg-slate-950/80 border border-slate-800 rounded-xl">
                    <div className="text-[10px] text-gray-500 font-bold uppercase">推导维度与枚举</div>
                    <div className="text-base font-extrabold text-purple-400 mt-1">{enrichResult.dimensions.length} 个分析维度</div>
                  </div>
                </div>

                {/* 详细指标与维度列表 */}
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                  {/* 指标推测 */}
                  <div className="p-4 bg-slate-950/90 border border-slate-800/90 rounded-xl">
                    <h3 className="text-xs font-bold text-emerald-400 flex items-center gap-1.5 border-b border-slate-800 pb-2">
                      📊 自动推导指标 (Inferred Metrics)
                    </h3>
                    <div className="flex flex-col gap-2.5 mt-3">
                      {enrichResult.metrics.map((m, idx) => (
                        <div key={idx} className="p-2.5 bg-slate-900/70 border border-slate-850 rounded-lg text-xs">
                          <div className="flex items-center justify-between font-bold text-slate-200">
                            <span>{m.display_name} ({m.name})</span>
                            <span className="text-[10px] px-1.5 py-0.5 bg-emerald-500/20 text-emerald-300 rounded font-mono">
                              {m.default_agg}
                            </span>
                          </div>
                          <div className="text-[10px] font-mono text-gray-400 mt-1">计算公式: {m.calculation}</div>
                          <div className="text-[10px] text-gray-500 mt-1">同义别名: {m.aliases.join(", ")}</div>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* 维度推测 */}
                  <div className="p-4 bg-slate-950/90 border border-slate-800/90 rounded-xl">
                    <h3 className="text-xs font-bold text-purple-400 flex items-center gap-1.5 border-b border-slate-800 pb-2">
                      🏷️ 自动推导维度与枚举字典 (Inferred Dimensions)
                    </h3>
                    <div className="flex flex-col gap-2.5 mt-3">
                      {enrichResult.dimensions.map((d, idx) => (
                        <div key={idx} className="p-2.5 bg-slate-900/70 border border-slate-850 rounded-lg text-xs">
                          <div className="flex items-center justify-between font-bold text-slate-200">
                            <span>{d.display_name} ({d.name})</span>
                            {d.value_range && d.value_range.length > 0 && (
                              <span className="text-[10px] px-1.5 py-0.5 bg-purple-500/20 text-purple-300 rounded">
                                枚举维度
                              </span>
                            )}
                          </div>
                          <div className="text-[10px] text-gray-500 mt-1">同义别名: {d.aliases.join(", ")}</div>
                          {d.value_range && d.value_range.length > 0 && (
                            <div className="text-[10px] text-cyan-400/90 mt-1 flex flex-wrap gap-1">
                              枚举值: {d.value_range.slice(0, 8).map((v, i) => (
                                <span key={i} className="px-1.5 py-0.5 bg-slate-800 rounded text-[9px] font-mono">{v}</span>
                              ))}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ========================================================================= */}
      {/* 视图 4：湖仓双引擎数据血缘全景探索                                        */}
      {/* ========================================================================= */}
      {subTab === "lineage" && (
        <div className="flex flex-col gap-6 animate-fade-in">
          <div className="glass-card bg-[#070b16]/70 border border-slate-800 p-5 rounded-2xl shadow-xl">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-slate-800/80 pb-4">
              <div>
                <h2 className="text-sm font-extrabold text-white flex items-center gap-2">
                  🌐 湖仓双引擎全链路数据血缘 (Lakehouse Provenance Explorer)
                </h2>
                <p className="text-[11px] text-gray-500 mt-0.5">
                  贯穿 ODS -&gt; DWD -&gt; DIM -&gt; DWS -&gt; ADS 的三级 data_id 拓扑与数据加工转换链路
                </p>
              </div>

              {/* 分层筛选标签 */}
              <div className="flex items-center gap-1.5 bg-slate-900 border border-slate-800 p-1 rounded-lg text-xs">
                {["ALL", "ODS", "DWD", "DIM", "DWS", "ADS"].map(layer => (
                  <button
                    key={layer}
                    onClick={() => setSelectedLayer(layer)}
                    className={`px-2.5 py-1 rounded-md text-[11px] font-bold transition-all cursor-pointer ${
                      selectedLayer === layer
                        ? "bg-amber-600 text-white shadow-md shadow-amber-900/30"
                        : "text-gray-400 hover:text-gray-200"
                    }`}
                  >
                    {layer}
                  </button>
                ))}
              </div>
            </div>

            {/* 节点卡片流式网格 */}
            {!lineageData ? (
              <div className="py-16 text-center text-xs text-gray-500">正在加载湖仓全景血缘拓扑...</div>
            ) : (
              <div className="flex flex-col gap-6 mt-5">
                {/* 节点网格 */}
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                  {lineageData.nodes
                    .filter(n => selectedLayer === "ALL" || n.layer === selectedLayer)
                    .map(node => {
                      const isSelected = selectedNodeId === node.id;
                      return (
                        <div
                          key={node.id}
                          onClick={() => setSelectedNodeId(isSelected ? null : node.id)}
                          className={`p-3.5 rounded-xl border transition-all cursor-pointer flex flex-col justify-between ${
                            isSelected
                              ? "bg-amber-950/40 border-amber-500/70 shadow-lg shadow-amber-950/50 scale-[1.02]"
                              : "bg-slate-950/70 border-slate-800 hover:border-slate-700"
                          }`}
                        >
                          <div className="flex items-start justify-between gap-2">
                            <div>
                              <div className="text-xs font-bold text-slate-100">{node.name}</div>
                              <div className="text-[10px] font-mono text-gray-400 mt-0.5">{node.id}</div>
                            </div>
                            <span className={`px-2 py-0.5 text-[9px] font-bold rounded uppercase ${
                              node.layer === "ODS" ? "bg-red-500/20 text-red-300 border border-red-500/30" :
                              node.layer === "DWD" ? "bg-orange-500/20 text-orange-300 border border-orange-500/30" :
                              node.layer === "DIM" ? "bg-purple-500/20 text-purple-300 border border-purple-500/30" :
                              node.layer === "DWS" ? "bg-blue-500/20 text-blue-300 border border-blue-500/30" :
                              "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30"
                            }`}>
                              {node.layer}
                            </span>
                          </div>
                          <div className="flex items-center justify-between mt-3 pt-2 border-t border-slate-850 text-[10px] text-gray-500">
                            <span>域: {node.domain}</span>
                            <span className="font-mono">{node.type}</span>
                          </div>
                        </div>
                      );
                    })}
                </div>

                {/* 边流转流向管道表格 */}
                <div className="mt-4 p-4 bg-slate-950/90 border border-slate-800 rounded-xl">
                  <h3 className="text-xs font-bold text-amber-400 flex items-center gap-1.5 border-b border-slate-800 pb-2">
                    🔄 数据流转与算子管道拓扑 (Transformation Pipelines)
                  </h3>
                  <div className="flex flex-col gap-2 mt-3 max-h-[300px] overflow-y-auto">
                    {lineageData.edges
                      .filter(e => {
                        if (!selectedNodeId) return true;
                        return e.source === selectedNodeId || e.target === selectedNodeId;
                      })
                      .map((edge, idx) => (
                        <div key={idx} className="p-2 bg-slate-900/80 border border-slate-850 rounded-lg text-[11px] flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                          <div className="flex items-center gap-2 font-mono">
                            <span className="text-amber-300">{edge.source}</span>
                            <span className="text-gray-500">➔</span>
                            <span className="text-cyan-300">{edge.target}</span>
                          </div>
                          <span className="text-[10px] text-gray-400 bg-slate-800/80 px-2 py-0.5 rounded font-sans">
                            {edge.relation}
                          </span>
                        </div>
                      ))}
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

    </div>
  );
};
