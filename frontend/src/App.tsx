import { useState, useEffect } from "react";
import { ChatPanel } from "./components/ChatPanel";
import { DevPanel } from "./components/DevPanel";
import { LlmSettings } from "./components/LlmSettings";

function App() {
  const [activeTab, setActiveTab] = useState<"chat" | "dev" | "llm">("chat");
  const [bgTheme, setBgTheme] = useState<"dark" | "pink">("pink");

  useEffect(() => {
    const root = document.documentElement;
    if (bgTheme === "pink") {
      root.style.setProperty("--page-bg-magenta", "#ff99ff");
    } else {
      root.style.setProperty("--page-bg-magenta", "#0b0f19");
    }
  }, [bgTheme]);

  const toggleTheme = () => {
    setBgTheme(prev => prev === "pink" ? "dark" : "pink");
  };

  return (
    <div className="flex flex-col min-h-screen">
      {/* 顶部 Header Navbar */}
      <header className="glass-card app-header rounded-none border-b border-b-slate-800/80 px-6 py-4 flex justify-between items-center sticky top-0 z-50 bg-[#070a13]/80 backdrop-blur-md">
        <div className="flex items-center gap-3">
          {/* Logo 效果 */}
          <div className="w-9 h-9 rounded-lg bg-gradient-to-tr from-purple-600 via-blue-600 to-pink-500 flex items-center justify-center font-bold text-white shadow-lg shadow-purple-500/20">
            DG
          </div>
          <div>
            <h1 className="text-base font-extrabold tracking-tight text-white flex items-center gap-1.5">
              DataWareHouse Agent
              <span className="text-[10px] bg-purple-500/10 border border-purple-500/20 text-purple-400 font-mono px-1.5 py-0.5 rounded">
                V1.0
              </span>
            </h1>
            <p className="text-[10px] text-gray-500">多 Agent 协同数仓与智能问数决策引擎</p>
          </div>
        </div>

        {/* Tab 控制器 */}
        <div className="flex bg-slate-950/60 border border-slate-800 rounded-lg p-1">
          <button
            onClick={() => setActiveTab("chat")}
            className={`px-4 py-1.5 rounded-md text-xs font-semibold transition-all ${
              activeTab === "chat"
                ? "bg-gradient-to-r from-purple-600 to-blue-600 text-white shadow-md"
                : "text-gray-400 hover:text-gray-200"
            }`}
          >
            💬 智能问数系统 (NL2SQL)
          </button>
          <button
            onClick={() => setActiveTab("dev")}
            className={`px-4 py-1.5 rounded-md text-xs font-semibold transition-all ${
              activeTab === "dev"
                ? "bg-gradient-to-r from-purple-600 to-blue-600 text-white shadow-md"
                : "text-gray-400 hover:text-gray-200"
            }`}
          >
            🤖 数仓开发协作流 (DataArts)
          </button>
          <button
            onClick={() => setActiveTab("llm")}
            className={`px-4 py-1.5 rounded-md text-xs font-semibold transition-all ${
              activeTab === "llm"
                ? "bg-gradient-to-r from-purple-600 to-blue-600 text-white shadow-md"
                : "text-gray-400 hover:text-gray-200"
            }`}
          >
            ⚙️ 模型服务设置
          </button>
        </div>

        {/* 右侧系统监控仿真指示器与主题切换 */}
        <div className="hidden md:flex items-center gap-4 text-xs">
          <button
            onClick={toggleTheme}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-700 bg-slate-900/50 text-gray-300 hover:text-white hover:border-purple-500/50 transition-all cursor-pointer font-medium"
          >
            🎨 {bgTheme === "pink" ? "切换至科技暗" : "切换至淡粉红"}
          </button>
          <div className="flex items-center gap-1.5 border-l border-slate-800 pl-4">
            <span className="w-2 h-2 rounded-full bg-emerald-500"></span>
            <span className="text-gray-400">SQLite 仿真库: ONLINE</span>
          </div>
          <div className="flex items-center gap-1.5 border-l border-slate-800 pl-4">
            <span className="w-2 h-2 rounded-full bg-purple-500"></span>
            <span className="text-gray-400">Guardrail 模块: ACTIVE</span>
          </div>
        </div>
      </header>

      {/* 主面板内容 */}
      <main className="flex-1 flex flex-col items-center">
        {activeTab === "chat" ? <ChatPanel /> : (activeTab === "dev" ? <DevPanel /> : <LlmSettings />)}
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
