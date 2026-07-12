import React, { useState, useEffect } from "react";
import type { LLMConfig, TestConnectionResponse } from "../types";

const API_BASE = "http://localhost:8000/api";

export const LlmSettings: React.FC = () => {
  const [config, setConfig] = useState<LLMConfig | null>(null);
  const [activeVendor, setActiveVendor] = useState("openai");
  
  // 密码显示状态
  const [showApiKey, setShowApiKey] = useState(false);
  
  // 连接测试与保存提示状态
  const [loading, setLoading] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);
  const [saveSuccess, setSaveSuccess] = useState("");

  const vendorNames: Record<string, string> = {
    openai: "OpenAI",
    deepseek: "DeepSeek (深度求索)",
    qwen: "Qwen (通义千问/百炼)",
    volcengine: "Volcengine (火山引擎/豆包)",
    glm: "GLM (智谱清言)",
    gemini: "Gemini (谷歌)",
    custom: "自定义供应商 (Custom)"
  };

  const loadConfig = async () => {
    try {
      const res = await fetch(`${API_BASE}/llm/config`);
      const data = await res.json();
      if (res.ok) {
        setConfig(data);
        setActiveVendor(data.active_vendor);
      }
    } catch (e) {
      console.error("加载模型配置失败:", e);
    }
  };

  useEffect(() => {
    loadConfig();
  }, []);

  if (!config) {
    return (
      <div className="glass-card p-6 max-w-2xl mx-auto w-full text-center text-gray-400">
        正在加载大模型供应商配置...
      </div>
    );
  }

  const currentVendorInfo = config.vendors[activeVendor];

  const handleInputChange = (field: "api_key" | "base_url" | "active_text_model" | "active_multimodal_model", val: string) => {
    setConfig(prev => {
      if (!prev) return null;
      const updatedVendors = { ...prev.vendors };
      updatedVendors[activeVendor] = {
        ...updatedVendors[activeVendor],
        [field]: val
      };
      return {
        ...prev,
        vendors: updatedVendors
      };
    });
  };

  const handleTestConnection = async () => {
    setLoading(true);
    setTestResult(null);
    setSaveSuccess("");
    try {
      const res = await fetch(`${API_BASE}/llm/test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          vendor: activeVendor,
          api_key: currentVendorInfo.api_key,
          base_url: currentVendorInfo.base_url
        })
      });
      const data: TestConnectionResponse = await res.json();
      
      if (data.success) {
        setTestResult({ success: true, message: data.message });
        
        // 动态把拉取到的模型列表塞进配置里
        setConfig(prev => {
          if (!prev) return null;
          const updatedVendors = { ...prev.vendors };
          
          // 如果返回了新模型列表，合并或者覆盖已有的默认列表
          const existingText = updatedVendors[activeVendor].text_models;
          const text_models = data.text_models.length > 0 ? data.text_models : existingText;
          const multimodal_models = data.multimodal_models.length > 0 ? data.multimodal_models : updatedVendors[activeVendor].multimodal_models;
          
          updatedVendors[activeVendor] = {
            ...updatedVendors[activeVendor],
            text_models,
            multimodal_models,
            active_text_model: text_models[0] || updatedVendors[activeVendor].active_text_model,
            active_multimodal_model: multimodal_models[0] || updatedVendors[activeVendor].active_multimodal_model
          };
          return { ...prev, vendors: updatedVendors };
        });
      } else {
        setTestResult({ success: false, message: data.message });
      }
    } catch (e) {
      setTestResult({ success: false, message: "无法建立连接测试。请检查后端 FastAPI 状态。" });
    } finally {
      setLoading(false);
    }
  };

  const handleSaveConfig = async () => {
    setLoading(true);
    setSaveSuccess("");
    setTestResult(null);
    try {
      // 在保存前，把当前的 active_vendor 更新
      const updatedConfig = {
        ...config,
        active_vendor: activeVendor
      };
      
      const res = await fetch(`${API_BASE}/llm/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(updatedConfig)
      });
      
      if (res.ok) {
        setSaveSuccess("模型供应商配置已成功保存！后端 Agent 在处理对话及开发流时将自动拉取此配置。");
        setConfig(updatedConfig);
      } else {
        setSaveSuccess("保存失败，后端接口返回错误。");
      }
    } catch (e) {
      setSaveSuccess("保存失败，无法连接后端服务。");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="glass-card p-8 max-w-2xl mx-auto w-full bg-gradient-to-br from-slate-900 via-slate-950 to-slate-900 border border-slate-800">
      <h2 className="text-xl font-bold mb-6 flex items-center gap-2">
        <span className="text-purple-400">⚙️</span>
        模型供应商与推理设置 (LLM Engine Settings)
      </h2>

      <div className="flex flex-col gap-6 text-sm">
        {/* 选择活跃的供应商 */}
        <div className="flex flex-col gap-2">
          <label className="text-gray-400 font-medium">1. 选择默认激活的模型供应商</label>
          <select
            value={activeVendor}
            onChange={(e) => {
              setActiveVendor(e.target.value);
              setTestResult(null);
              setSaveSuccess("");
            }}
            className="bg-slate-900 border border-slate-700 text-gray-200 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:border-purple-500 transition-colors"
          >
            {Object.keys(vendorNames).map((vendorKey) => (
              <option key={vendorKey} value={vendorKey}>
                {vendorNames[vendorKey]} {activeVendor === vendorKey ? " (当前已选)" : ""}
              </option>
            ))}
          </select>
        </div>

        {/* 供应商对应的 Base URL 和 API Key */}
        <div className="flex flex-col gap-4 bg-slate-950/40 p-5 border border-slate-900 rounded-lg">
          <h3 className="font-bold text-gray-300 border-b border-slate-800 pb-2 mb-2">
            配置 {vendorNames[activeVendor]} 的认证信息
          </h3>

          <div className="flex flex-col gap-2">
            <label className="text-gray-400">接口服务 Base URL</label>
            <input
              type="text"
              value={currentVendorInfo.base_url}
              onChange={(e) => handleInputChange("base_url", e.target.value)}
              placeholder="请输入对应的 API Base URL 路径，如 https://api.openai.com/v1"
              className="bg-slate-900 border border-slate-700 text-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-purple-500 transition-colors font-mono"
            />
          </div>

          <div className="flex flex-col gap-2 relative">
            <label className="text-gray-400">供应商 API Key / Token</label>
            <div className="relative flex items-center">
              <input
                type={showApiKey ? "text" : "password"}
                value={currentVendorInfo.api_key}
                onChange={(e) => handleInputChange("api_key", e.target.value)}
                placeholder="请输入您的推理 API 授权 Key"
                className="w-full bg-slate-900 border border-slate-700 text-gray-200 rounded-lg pl-3 pr-10 py-2 text-sm focus:outline-none focus:border-purple-500 transition-colors font-mono"
              />
              <button
                type="button"
                onClick={() => setShowApiKey(!showApiKey)}
                className="absolute right-3 text-gray-500 hover:text-gray-300 text-xs focus:outline-none"
              >
                {showApiKey ? "🙈 隐藏" : "👁️ 显示"}
              </button>
            </div>
            <p className="text-[10px] text-gray-600 mt-1">
              * 密钥安全存储于本地后端配置文件中，仅供 Agent 发起接口调用，请放心填写。
            </p>
          </div>
        </div>

        {/* 测试连接按钮 */}
        <div className="flex justify-end gap-3">
          <button
            onClick={handleTestConnection}
            disabled={loading}
            className="bg-slate-900 hover:bg-slate-800 text-gray-300 border border-slate-700 px-4 py-2 text-xs rounded-lg transition-all disabled:opacity-50"
          >
            ⚡ 测试连接 & 拉取模型
          </button>
          
          <button
            onClick={handleSaveConfig}
            disabled={loading}
            className="btn-gradient px-5 py-2 text-xs rounded-lg disabled:opacity-50"
          >
            💾 保存当前配置
          </button>
        </div>

        {/* 测试及保存连接状态提醒 */}
        {testResult && (
          <div className={`p-3 border rounded text-xs leading-relaxed ${
            testResult.success 
              ? "bg-emerald-950/15 border-emerald-500/20 text-emerald-400" 
              : "bg-red-950/15 border-red-500/20 text-red-400"
          }`}>
            {testResult.success ? "✓ " : "✗ "} {testResult.message}
          </div>
        )}

        {saveSuccess && (
          <div className="p-3 bg-purple-950/15 border border-purple-500/20 text-purple-300 rounded text-xs leading-relaxed">
            ✓ {saveSuccess}
          </div>
        )}

        {/* 动态模型展示与选择 */}
        <div className="flex flex-col gap-4 bg-slate-950/40 p-5 border border-slate-900 rounded-lg mt-2">
          <h3 className="font-bold text-gray-300 border-b border-slate-800 pb-2 mb-2">
            2. 指定默认推理与多模态模型名称
          </h3>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="flex flex-col gap-2">
              <label className="text-gray-400">选择文本推理模型 (Reasoning Model)</label>
              {currentVendorInfo.text_models.length > 0 ? (
                <select
                  value={currentVendorInfo.active_text_model}
                  onChange={(e) => handleInputChange("active_text_model", e.target.value)}
                  className="bg-slate-900 border border-slate-700 text-gray-200 rounded-lg px-3 py-2 text-xs focus:outline-none focus:border-purple-500 transition-colors font-mono"
                >
                  {currentVendorInfo.text_models.map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
              ) : (
                <input
                  type="text"
                  value={currentVendorInfo.active_text_model}
                  onChange={(e) => handleInputChange("active_text_model", e.target.value)}
                  placeholder="手动填入推理模型名"
                  className="bg-slate-900 border border-slate-700 text-gray-200 rounded-lg px-3 py-2 text-xs focus:outline-none focus:border-purple-500 transition-colors font-mono"
                />
              )}
            </div>

            <div className="flex flex-col gap-2">
              <label className="text-gray-400">选择多模态模型 (Vision Model)</label>
              {currentVendorInfo.multimodal_models.length > 0 ? (
                <select
                  value={currentVendorInfo.active_multimodal_model}
                  onChange={(e) => handleInputChange("active_multimodal_model", e.target.value)}
                  className="bg-slate-900 border border-slate-700 text-gray-200 rounded-lg px-3 py-2 text-xs focus:outline-none focus:border-purple-500 transition-colors font-mono"
                >
                  {currentVendorInfo.multimodal_models.map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
              ) : (
                <input
                  type="text"
                  value={currentVendorInfo.active_multimodal_model}
                  onChange={(e) => handleInputChange("active_multimodal_model", e.target.value)}
                  placeholder="手动填入多模态模型名"
                  className="bg-slate-900 border border-slate-700 text-gray-200 rounded-lg px-3 py-2 text-xs focus:outline-none focus:border-purple-500 transition-colors font-mono"
                />
              )}
            </div>
          </div>
        </div>

      </div>
    </div>
  );
};
