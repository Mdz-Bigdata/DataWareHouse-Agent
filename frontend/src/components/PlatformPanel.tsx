import React, { useEffect, useState } from "react";
import { resolvePlatformUiUrl } from "../lib/platformLinks";

type Capability = {
  slug: string;
  name: string;
  route_prefix: string;
  ui_url: string;
  ui_port?: number;
  enabled: boolean;
  description: string;
  badge: string;
  features: string[];
};

const fallbackCapabilities: Capability[] = [
  {
    slug: "core",
    name: "DataWareHouse Agent (核心数仓)",
    route_prefix: "/platform/core",
    ui_url: "http://localhost:3000",
    enabled: true,
    badge: "已深度集成",
    description: "基于阿里 QwenPaw-Data 体系的四层知识底座、DSL 语义建模、AST 安全网闸与多级语义缓存中心",
    features: ["QwenPaw-Data 证据底座", "L1/L2 毫秒级语义缓存", "14项金融黄金回归 100% PASS", "AST 只读与除零自愈网闸"]
  },
  {
    slug: "audio",
    name: "听书问数 Agent (ListenBook Data)",
    route_prefix: "/platform/audio",
    ui_url: "http://localhost:8040",
    enabled: true,
    badge: "业务模型内置",
    description: "面向有声书/音频垂直领域的智能问数代理，涵盖专辑播放、完播率、VIP会员订阅与主播排行榜",
    features: ["54张听书核心事实表与维表", "45个业务核心指标", "听书会员异动归因下钻", "有声剧播放全链路湖仓血缘"]
  },
  {
    slug: "data-api",
    name: "NanZi 数据服务平台",
    route_prefix: "/platform/data-api",
    ui_url: "",
    enabled: true,
    badge: "微服务网关",
    description: "企业级数据服务开放平台，提供 SQL Lab、元数据治理、资产目录、数据 API 发布与 RBAC 审计",
    features: ["数据服务 API 极速发布", "SQL 交互式工作台", "资产目录与标签体系", "细粒度权限控制与审计"]
  },
  {
    slug: "agents",
    name: "NanZi 智能体平台",
    route_prefix: "/platform/agents",
    ui_url: "",
    enabled: true,
    badge: "生态协同",
    description: "企业级多智能体协同与自动化执行中枢，集成 ChatBI、工具库、行业知识库与定时调度任务",
    features: ["多 Agent 协同工作流", "ChatBI 自动看板生成", "企业专属 RAG 知识库", "自动化定时调度执行器"]
  }
];

interface PlatformPanelProps {
  onNavigateToChat?: (presetQuestion?: string) => void;
}

export const PlatformPanel: React.FC<PlatformPanelProps> = ({ onNavigateToChat }) => {
  const [capabilities, setCapabilities] = useState(fallbackCapabilities);
  const [gatewayStatus, setGatewayStatus] = useState<"loading" | "online" | "offline">("loading");
  const [readiness, setReadiness] = useState<Record<string, boolean>>({});
  const gatewayOnline = gatewayStatus === "online";

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/platform/capabilities", { signal: controller.signal })
      .then(async response => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then((payload: { items?: unknown }) => {
        if (!Array.isArray(payload.items)) throw new Error("Invalid capabilities response");
        const items = payload.items;
        setCapabilities(fallbackCapabilities.map(capability => {
          const registered = items.find(item => item && item.slug === capability.slug);
          if (!registered) return capability;
          return {
            ...capability,
            ui_url: typeof registered.ui_url === "string" ? registered.ui_url : capability.ui_url,
            ui_port: typeof registered.ui_port === "number" ? registered.ui_port : capability.ui_port,
            route_prefix: typeof registered.route_prefix === "string" ? registered.route_prefix : capability.route_prefix,
            enabled: typeof registered.enabled === "boolean" ? registered.enabled : capability.enabled,
          };
        }));
        setGatewayStatus("online");
      })
      .catch(error => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setGatewayStatus("offline");
      });

    // Gateway connectivity and each application's readiness are separate states.
    fetch("/api/platform/ready", { signal: controller.signal })
      .then(async response => {
        if (!response.ok && response.status !== 503) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then((payload: { subsystems?: unknown }) => {
        if (!Array.isArray(payload.subsystems)) return;
        const states: Record<string, boolean> = {};
        for (const item of payload.subsystems) {
          if (item && typeof item.slug === "string" && typeof item.ready === "boolean") {
            states[item.slug] = item.ready;
          }
        }
        setReadiness(states);
      })
      .catch(() => {
        // Unknown readiness must not prevent direct access to an independent app.
      });
    return () => controller.abort();
  }, []);

  return (
    <section className="w-full max-w-7xl px-4 py-8 flex flex-col gap-8 animate-fade-in text-slate-100">
      
      {/* 顶部横幅 */}
      <div className="glass-card p-6 border border-slate-800/80 bg-[#0b1021]/60 flex flex-col md:flex-row md:items-center justify-between gap-4 shadow-2xl">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-xl font-black tracking-tight text-white flex items-center gap-2">
              🧭 统一能力中枢与架构拓扑
            </h2>
            <span className="text-[10px] bg-purple-500/20 text-purple-300 border border-purple-500/30 px-2 py-0.5 rounded-full font-mono">
              Microservice Matrix
            </span>
          </div>
          <p className="text-xs text-gray-400 mt-1.5 leading-relaxed">
            四大子系统既保持各自独立的垂直业务深度，又通过统一语义层、AST 物理网闸与同源网关形成三位一体的工业级智能底座。
          </p>
        </div>
        
        <div className="flex items-center gap-3">
          <div className={`px-3 py-1.5 rounded-lg border text-xs font-mono font-bold flex items-center gap-2 ${
            gatewayOnline 
              ? "bg-emerald-950/40 border-emerald-500/40 text-emerald-400 shadow-lg shadow-emerald-950/30" 
              : "bg-slate-900/80 border-slate-700/80 text-gray-400"
          }`}>
            <span className={`w-2 h-2 rounded-full ${gatewayOnline ? "bg-emerald-500 animate-pulse" : "bg-gray-500"}`}></span>
            同源统一网关: {gatewayOnline ? "已连接" : gatewayStatus === "loading" ? "连接中" : "未连接"}
          </div>
        </div>
      </div>

      {/* 四大核心能力网格卡片 */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {capabilities.map((capability) => {
          const isCore = capability.slug === "core";
          const isAudio = capability.slug === "audio";
          const uiUrl = resolvePlatformUiUrl(capability.slug, capability.ui_url, window.location.href, capability.ui_port);

          return (
            <article
              key={capability.slug}
              className={`glass-card p-6 rounded-2xl border transition-all flex flex-col justify-between relative overflow-hidden group ${
                isCore ? "border-purple-500/40 bg-gradient-to-br from-[#0c1024]/80 to-[#070b16]/80 hover:border-purple-500/70 shadow-purple-950/20" :
                isAudio ? "border-cyan-500/40 bg-gradient-to-br from-[#0a1526]/80 to-[#070b16]/80 hover:border-cyan-500/70 shadow-cyan-950/20" :
                "border-slate-800/90 bg-[#070b16]/70 hover:border-slate-700"
              }`}
            >
              {/* 背景装饰光晕 */}
              <div className={`absolute -top-16 -right-16 w-36 h-36 rounded-full blur-3xl opacity-20 pointer-events-none ${
                isCore ? "bg-purple-500" : isAudio ? "bg-cyan-500" : "bg-blue-500"
              }`}></div>

              <div>
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h3 className="text-base font-bold text-white flex items-center gap-2">
                      {capability.name}
                    </h3>
                    <span className="text-[10px] font-mono text-gray-400 mt-0.5 block">
                      Route: {capability.route_prefix}
                    </span>
                  </div>
                  <span className={`px-2.5 py-1 text-[10px] font-bold rounded-lg border uppercase tracking-wider ${
                    isCore ? "bg-purple-500/20 text-purple-300 border-purple-500/30" :
                    isAudio ? "bg-cyan-500/20 text-cyan-300 border-cyan-500/30" :
                    "bg-blue-500/20 text-blue-300 border-blue-500/30"
                  }`}>
                    {capability.badge}
                  </span>
                </div>

                <p className="text-xs text-gray-300 mt-3 leading-relaxed">
                  {capability.description}
                </p>

                {/* 特性徽标列表 */}
                <div className="grid grid-cols-2 gap-2 mt-4 pt-4 border-t border-slate-800/80">
                  {capability.features.map((feat, idx) => (
                    <div key={idx} className="flex items-center gap-1.5 text-[11px] text-gray-400">
                      <span className="text-purple-400 text-xs font-bold">✓</span>
                      <span className="truncate">{feat}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* 底部操作区 */}
              <div className="mt-6 pt-4 border-t border-slate-800/80 flex items-center justify-between gap-3">
                <span className="text-[10px] text-gray-500 font-mono">
                  {isAudio ? "内置听书问数 · 由核心数仓提供" : !capability.enabled ? "已停用" : isCore ? "内嵌原生驱动" :
                    readiness[capability.slug] === true ? "完整应用 · 服务已就绪" :
                    readiness[capability.slug] === false ? "完整应用 · 服务暂不可用" : "完整应用 · 就绪状态未知"}
                </span>

                {isCore && (
                  <button
                    disabled={!capability.enabled}
                    onClick={() => onNavigateToChat && onNavigateToChat()}
                    className="btn-gradient px-4 py-1.5 text-xs font-bold flex items-center gap-1.5 cursor-pointer shadow-lg shadow-purple-900/30 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    💬 进入通用数仓问数
                  </button>
                )}

                {isAudio && (
                  <button
                    onClick={() => onNavigateToChat && onNavigateToChat("昨天听书各分类播放量是多少")}
                    className="px-4 py-1.5 bg-gradient-to-r from-cyan-600 to-blue-600 hover:brightness-110 text-white rounded-lg text-xs font-bold flex items-center gap-1.5 cursor-pointer transition-all shadow-lg shadow-cyan-900/30 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    🎧 体验听书业务问数
                  </button>
                )}

                {!isCore && !isAudio && (capability.enabled && uiUrl ? (
                  <a
                    href={uiUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="px-4 py-1.5 bg-slate-800/90 hover:bg-slate-700 text-slate-200 border border-slate-700 rounded-lg text-xs font-bold transition-all cursor-pointer"
                  >
                    外部面板 ↗
                  </a>
                ) : (
                  <button
                    disabled
                    title={capability.enabled ? "应用地址配置无效，请检查平台配置" : "该平台已停用"}
                    className="px-4 py-1.5 bg-slate-800/50 text-gray-500 border border-slate-700 rounded-lg text-xs font-bold cursor-not-allowed"
                  >
                    外部面板 ↗
                  </button>
                ))}
              </div>
            </article>
          );
        })}
      </div>

      {/* 底部多 Agent 协同全景拓扑图 */}
      <div className="glass-card p-6 rounded-2xl border border-slate-800/80 bg-[#070b16]/70 shadow-2xl">
        <h3 className="text-sm font-extrabold text-white flex items-center gap-2 border-b border-slate-800/80 pb-3">
          🌐 多 Agent 协同与同源数据网关拓扑体系 (Multi-Agent Unified Architecture)
        </h3>
        
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mt-5 text-center">
          <div className="p-4 bg-slate-950/80 border border-slate-800 rounded-xl flex flex-col items-center">
            <span className="text-2xl mb-1">👤</span>
            <div className="text-xs font-bold text-white">用户自然语言问数</div>
            <div className="text-[10px] text-gray-500 mt-1">Web UI / API / SSE流式</div>
          </div>

          <div className="p-4 bg-slate-950/80 border border-purple-500/30 rounded-xl flex flex-col items-center">
            <span className="text-2xl mb-1">⚡</span>
            <div className="text-xs font-bold text-purple-300">多级语义缓存网关</div>
            <div className="text-[10px] text-gray-500 mt-1">L1 Exact &lt;5ms / L2 Vector</div>
          </div>

          <div className="p-4 bg-slate-950/80 border border-blue-500/30 rounded-xl flex flex-col items-center">
            <span className="text-2xl mb-1">🤖</span>
            <div className="text-xs font-bold text-blue-300">Skill-Hub 动态调度</div>
            <div className="text-[10px] text-gray-500 mt-1">问数 / 异动归因 / 湖图血缘</div>
          </div>

          <div className="p-4 bg-slate-950/80 border border-emerald-500/30 rounded-xl flex flex-col items-center">
            <span className="text-2xl mb-1">🛡️</span>
            <div className="text-xs font-bold text-emerald-300">AST 只读安全网闸</div>
            <div className="text-[10px] text-gray-500 mt-1">NULLIF 除零 / 行列隔离 / 飞轮</div>
          </div>
        </div>

        <div className="mt-4 p-3 bg-slate-900/50 border border-slate-850 rounded-lg text-center font-mono text-[11px] text-gray-400">
          物理执行层支持: <span className="text-emerald-400">Doris</span> · <span className="text-blue-400">StarRocks</span> · <span className="text-amber-400">ClickHouse</span> · <span className="text-cyan-400">DuckDB</span> · <span className="text-purple-400">PostgreSQL</span> · <span className="text-slate-300">SQLite (内存仿真)</span>
        </div>
      </div>

    </section>
  );
};
