import { useState } from "react";
import { ChatPanel } from "./components/ChatPanel";
import { DevPanel } from "./components/DevPanel";
import { LlmSettings } from "./components/LlmSettings";
import { FlywheelPanel } from "./components/FlywheelPanel";
import { PlatformPanel } from "./components/PlatformPanel";
import { DataEnginePanel } from "./components/DataEnginePanel";
import ThemeSwitcher from "./components/ThemeSwitcher";

function App() {
  const initialTab = new URLSearchParams(window.location.search).get("panel") === "data-engine" ? "data-engine" : "chat";
  const [activeTab, setActiveTab] = useState<"chat" | "flywheel" | "platform" | "data-engine" | "dev" | "llm">(initialTab);
  const [chatPresetQuestion, setChatPresetQuestion] = useState<string>("");

  const handleNavigateToChat = (presetQuestion?: string) => {
    if (presetQuestion) {
      setChatPresetQuestion(presetQuestion);
    }
    setActiveTab("chat");
  };

  return (
    <div className="app-shell flex flex-col min-h-screen text-slate-100">
      {/* 顶部 Header Navbar */}
      <header className="glass-card app-header rounded-none border-b border-b-slate-800/80 px-6 py-3.5 flex justify-between items-center sticky top-0 z-50 bg-[#070a13]/90 backdrop-blur-xl shadow-2xl">
        <div className="app-brand flex items-center gap-3">
          {/* Logo 效果 */}
          <div className="app-brand__mark w-9 h-9 rounded-xl bg-gradient-to-tr from-purple-600 via-indigo-600 to-cyan-400 flex items-center justify-center font-black text-white shadow-lg shadow-purple-500/25 tracking-wider text-sm">
            DW
          </div>
          <div>
            <h1 className="text-sm font-extrabold tracking-tight text-white flex items-center gap-2">
              DataWareHouse Agent
              <span className="text-[10px] bg-gradient-to-r from-purple-500/20 to-blue-500/20 border border-purple-500/30 text-purple-300 font-mono px-2 py-0.5 rounded-full">
                V2.0 PRO
              </span>
            </h1>
            <p className="text-[10px] text-gray-400">QwenPaw-Data + DSH/OAG + 听书问数 + 企业智能体统一平台</p>
          </div>
        </div>

        {/* Tab 控制器 */}
        <nav aria-label="主导航" className="app-navigation flex flex-wrap bg-slate-950/80 border border-slate-800/90 rounded-xl p-1 shadow-inner">
          <button
            onClick={() => setActiveTab("chat")}
            aria-pressed={activeTab === "chat"}
            className={`px-4 py-1.5 rounded-lg text-xs font-bold transition-all cursor-pointer ${
              activeTab === "chat"
                ? "bg-gradient-to-r from-purple-600 to-blue-600 text-white shadow-md shadow-purple-900/40"
                : "text-gray-400 hover:text-gray-200"
            }`}
          >
            💬 智能问数 (NL2SQL)
          </button>
          <button
            onClick={() => setActiveTab("flywheel")}
            aria-pressed={activeTab === "flywheel"}
            className={`px-4 py-1.5 rounded-lg text-xs font-bold transition-all cursor-pointer ${
              activeTab === "flywheel"
                ? "bg-gradient-to-r from-purple-600 to-blue-600 text-white shadow-md shadow-purple-900/40"
                : "text-gray-400 hover:text-gray-200"
            }`}
          >
            📊 数仓治理与飞轮
          </button>
          <button
            onClick={() => setActiveTab("platform")}
            aria-pressed={activeTab === "platform"}
            className={`px-4 py-1.5 rounded-lg text-xs font-bold transition-all cursor-pointer ${
              activeTab === "platform"
                ? "bg-gradient-to-r from-purple-600 to-blue-600 text-white shadow-md shadow-purple-900/40"
                : "text-gray-400 hover:text-gray-200"
            }`}
          >
            🧭 统一能力中心
          </button>
          <button
            onClick={() => setActiveTab("data-engine")}
            aria-pressed={activeTab === "data-engine"}
            className={`px-4 py-1.5 rounded-lg text-xs font-bold transition-all cursor-pointer ${
              activeTab === "data-engine"
                ? "bg-gradient-to-r from-cyan-600 to-blue-600 text-white shadow-md shadow-cyan-900/40"
                : "text-gray-400 hover:text-gray-200"
            }`}
          >
            🧩 确定性引擎
          </button>
          <button
            onClick={() => setActiveTab("dev")}
            aria-pressed={activeTab === "dev"}
            className={`px-4 py-1.5 rounded-lg text-xs font-bold transition-all cursor-pointer ${
              activeTab === "dev"
                ? "bg-gradient-to-r from-purple-600 to-blue-600 text-white shadow-md shadow-purple-900/40"
                : "text-gray-400 hover:text-gray-200"
            }`}
          >
            🤖 数仓开发 (DataArts)
          </button>
          <button
            onClick={() => setActiveTab("llm")}
            aria-pressed={activeTab === "llm"}
            className={`px-4 py-1.5 rounded-lg text-xs font-bold transition-all cursor-pointer ${
              activeTab === "llm"
                ? "bg-gradient-to-r from-purple-600 to-blue-600 text-white shadow-md shadow-purple-900/40"
                : "text-gray-400 hover:text-gray-200"
            }`}
          >
            ⚙️ 模型设置
          </button>
        </nav>

        {/* 右侧系统监控指示器 */}
        <div className="app-header__actions">
          <ThemeSwitcher />
        <div className="app-health hidden lg:flex items-center gap-3 text-xs font-mono">
          <div className="flex items-center gap-1.5 bg-slate-900/70 border border-slate-800 px-2.5 py-1 rounded-lg">
            <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
            <span className="text-gray-300">Doris/SQLite 引擎: 就绪</span>
          </div>
          <div className="flex items-center gap-1.5 bg-slate-900/70 border border-slate-800 px-2.5 py-1 rounded-lg">
            <span className="w-2 h-2 rounded-full bg-cyan-400"></span>
            <span className="text-gray-300">语义缓存: &lt;5ms</span>
          </div>
          <div className="flex items-center gap-1.5 bg-slate-900/70 border border-slate-800 px-2.5 py-1 rounded-lg">
            <span className="w-2 h-2 rounded-full bg-purple-400"></span>
            <span className="text-gray-300">AST 网闸: 运行中</span>
          </div>
        </div>
        </div>
      </header>

      {/* 主面板内容 */}
      <main className="flex-grow w-full max-w-7xl mx-auto flex flex-col items-center">
        {activeTab === "platform" && <PlatformPanel onNavigateToChat={handleNavigateToChat} onNavigateToEngine={() => setActiveTab("data-engine")} />}
        {activeTab === "data-engine" && <DataEnginePanel />}
        {activeTab === "chat" && <ChatPanel initialQuestion={chatPresetQuestion} />}
        {activeTab === "dev" && <DevPanel />}
        {activeTab === "llm" && <LlmSettings />}
        {activeTab === "flywheel" && <FlywheelPanel />}
      </main>

      {/* 底部 Footer */}
      <footer className="py-6 border-t border-slate-900 text-center text-xs text-gray-600">
        <p>© 2026 DataWareHouse-Agent. Built on React + TypeScript & FastAPI.</p>
        <p className="mt-1">符合多 Agent 契约协同及 SQLGlot 全链路 Guardrail 设计规范。</p>
      </footer>
    </div>
  );
}

export default App;
