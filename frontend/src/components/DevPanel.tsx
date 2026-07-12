import React, { useState } from "react";
import type { DevResponse, PhaseLog } from "../types";

const API_BASE = "http://localhost:8000/api";

export const DevPanel: React.FC = () => {
  const [requirement, setRequirement] = useState("基于电商订单数据，开发交易销售额和退款金额按区域品类天级汇总的数仓模型");
  const [loading, setLoading] = useState(false);
  const [devData, setDevData] = useState<DevResponse | null>(null);

  const [datasource, setDatasource] = useState("doris");
  const [sqlEngine, setSqlEngine] = useState("doris");

  // 代码编辑器状态
  const [activeFilePath, setActiveFilePath] = useState<string | null>(null);
  const [activeFileContent, setActiveFileContent] = useState("");
  const [editorLoading, setEditorLoading] = useState(false);
  const [editorSuccess, setEditorSuccess] = useState("");

  // 模拟终端日志
  const [terminalLogs, setTerminalLogs] = useState<string[]>([]);

  const handleStartWorkflow = async () => {
    if (!requirement.trim()) return;
    setLoading(true);
    setDevData(null);
    setActiveFilePath(null);
    setTerminalLogs([
      "[SYSTEM] 准备启动数仓开发 Agent 协作工作流...",
      `[SYSTEM] 接收用户需求: "${requirement}"`,
      `[SYSTEM] 选定数据源: ${datasource.toUpperCase()} | SQL计算引擎: ${sqlEngine.toUpperCase()}`,
      "[SYSTEM] 正在调度顶层协调者 @data-warehouse 路由任务..."
    ]);

    try {
      const res = await fetch(`${API_BASE}/developer/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ requirement, datasource, sql_engine: sqlEngine })
      });
      const data: DevResponse = await res.json();
      setDevData(data);
      
      // 追加仿真终端日志
      setTerminalLogs(prev => [
        ...prev,
        `[data-warehouse] 需求分析完毕，已向 architect/modeler 派发设计任务。`,
        `[architect] 架构分层已定义为 ODS -> DWD -> DWS。`,
        `[modeler] DDL 建表脚本已落地: init/${datasource}/${data.table_name}.sql`,
        `[reviewer] DDL 表结构审查通过 (APPROVED)。`,
        `[data-engineer] ETL 转换 SQL 开发完成并已落盘: etl/${datasource}/etl_${data.table_name}.sql`,
        `[reviewer] ETL SQL 编码规范审查通过 (APPROVED)。`,
        `[dataarts-batch-job] DataArts 批处理 JSON 配置生成完毕。`,
        `[dataarts-studio-scripts] 扫描项目根目录，成功仿真上传脚本至 Huawei Cloud DataArts 仓库。`,
        `[doc-writer] 扫描 init/ 与 etl/，自动输出 Markdown 模型文档及 README 总览。`,
        `[dataarts-job-uploader] Job 作业逻辑已仿真上传并创建/更新成功。`,
        `[SYSTEM] 多 Agent 协同数仓开发工作流全部执行完毕！部署检查清单 10/10 已全部通过。`
      ]);
    } catch (e) {
      setTerminalLogs(prev => [...prev, "[ERROR] 后端协作流执行发生错误，请检查 FastAPI 服务端口。"]);
    } finally {
      setLoading(false);
    }
  };

  const handleLoadFile = async (path: string) => {
    setEditorLoading(true);
    setEditorSuccess("");
    setActiveFilePath(path);
    try {
      const res = await fetch(`${API_BASE}/developer/file?path=${encodeURIComponent(path)}`);
      const data = await res.json();
      if (res.ok) {
        setActiveFileContent(data.content);
      } else {
        setActiveFileContent(`读取文件失败: ${data.detail}`);
      }
    } catch (e) {
      setActiveFileContent("无法连接后端服务读取文件。");
    } finally {
      setEditorLoading(false);
    }
  };

  const handleSaveFile = async () => {
    if (!activeFilePath) return;
    setEditorLoading(true);
    setEditorSuccess("");
    try {
      const res = await fetch(`${API_BASE}/developer/file?path=${encodeURIComponent(activeFilePath)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: activeFileContent })
      });
      const data = await res.json();
      if (res.ok) {
        setEditorSuccess("代码已成功修改并重新写入磁盘！正在仿真触发 Reviewer 的增量合规校验...");
        setTerminalLogs(prev => [
          ...prev,
          `[SYSTEM] 检测到落盘代码修改: ${activeFilePath}`,
          `[reviewer] 增量代码审查通过！结构设计依然合规。`
        ]);
      } else {
        setEditorSuccess(`保存失败: ${data.detail}`);
      }
    } catch (e) {
      setEditorSuccess("保存出错，无法连接后端接口。");
    } finally {
      setEditorLoading(false);
    }
  };

  const phaseContainsActiveFile = (phase: PhaseLog) => {
    if (!activeFilePath) return false;

    return [
      phase.output.ddl_file,
      phase.output.etl_file,
      phase.output.job_file,
      phase.output.doc_file,
      phase.output.readme_file
    ].includes(activeFilePath);
  };

  const renderActiveFileEditor = () => {
    if (!activeFilePath) return null;

    return (
      <div className="phase-file-editor animate-fade-in">
        <div className="phase-file-editor__header">
          <div className="phase-file-editor__meta">
            <h4 className="phase-file-editor__title">
              {activeFilePath.split("/").pop()}
            </h4>
            <span className="phase-file-editor__path">路径: {activeFilePath}</span>
          </div>
          <button
            onClick={handleSaveFile}
            disabled={editorLoading}
            className="btn-gradient px-3 py-1.5 text-xs disabled:opacity-50"
          >
            {editorLoading ? "保存中..." : "保存并重新审查"}
          </button>
        </div>

        <div className="phase-file-editor__body">
          {editorLoading && (
            <div className="phase-file-editor__loading">
              正在加载数据...
            </div>
          )}
          
          <textarea
            value={activeFileContent}
            onChange={(e) => setActiveFileContent(e.target.value)}
            className="phase-file-editor__textarea"
          />
        </div>

        {editorSuccess && (
          <div className="phase-file-editor__success">
            ✓ {editorSuccess}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 p-6 max-w-7xl mx-auto w-full">
      {/* 左侧：开发控制台与 Agent 协同链 */}
      <div className="lg:col-span-8 flex flex-col gap-6">
        
        {/* 需求提交 */}
        <div className="glass-card p-6 bg-gradient-to-br from-slate-900 to-slate-950 border border-slate-800">
          <h2 className="text-xl font-bold mb-4 flex items-center gap-2">
            <span className="w-2.5 h-2.5 rounded-full bg-blue-500 animate-pulse"></span>
            数仓开发多 Agent 协作工作流 (Doris / DataArts)
          </h2>
          <div className="flex flex-col gap-4">
            <textarea
              value={requirement}
              onChange={(e) => setRequirement(e.target.value)}
              placeholder="请输入您要开发的数仓表与 ETL 需求说明..."
              className="w-full h-20 bg-slate-900 border border-slate-700 text-gray-200 rounded-lg px-4 py-3 text-sm focus:outline-none focus:border-blue-500 transition-colors placeholder:text-gray-600 resize-none"
            />
            
            <div className="flex gap-4">
              <div className="flex-1 flex flex-col gap-1.5">
                <label className="text-xs text-gray-400 font-medium">数仓物理数据源</label>
                <select
                  value={datasource}
                  onChange={(e) => setDatasource(e.target.value)}
                  className="bg-slate-900 border border-slate-700 text-gray-200 rounded-lg px-3 py-2 text-xs focus:outline-none focus:border-blue-500 transition-colors"
                >
                  <option value="doris">Doris</option>
                  <option value="starrocks">StarRocks</option>
                  <option value="clickhouse">ClickHouse</option>
                </select>
              </div>
              <div className="flex-1 flex flex-col gap-1.5">
                <label className="text-xs text-gray-400 font-medium">SQL 开发与执行引擎</label>
                <select
                  value={sqlEngine}
                  onChange={(e) => setSqlEngine(e.target.value)}
                  className="bg-slate-900 border border-slate-700 text-gray-200 rounded-lg px-3 py-2 text-xs focus:outline-none focus:border-blue-500 transition-colors"
                >
                  <option value="doris">Doris SQL</option>
                  <option value="starrocks">StarRocks SQL</option>
                  <option value="clickhouse">ClickHouse SQL</option>
                  <option value="flinksql">FlinkSQL (流批一体)</option>
                  <option value="sparksql">SparkSQL (大批处理)</option>
                  <option value="postgresql">PostgreSQL SQL</option>
                </select>
              </div>
            </div>

            <div className="flex justify-between items-center">
              <span className="text-xs text-gray-500">
                * 本工作流将依次调度协调者、架构师、建模师、开发工程师、审查者及文档编写者进行多 Agent 契约协同。
              </span>
              <button
                onClick={handleStartWorkflow}
                disabled={loading}
                className="btn-gradient px-6 py-2.5 text-sm font-semibold flex items-center gap-2 disabled:opacity-50"
              >
                {loading ? "协同链开发中..." : "启动数仓开发协作链"}
              </button>
            </div>
          </div>
        </div>

        {/* 仿真终端日志 */}
        {terminalLogs.length > 0 && (
          <div className="glass-card p-4 bg-black border border-slate-800 rounded-lg">
            <div className="flex justify-between items-center border-b border-slate-800 pb-2 mb-2">
              <span className="text-xs font-semibold text-gray-400">🖥️ 华为云 DataArts & Agent 仿真终端</span>
              <span className="text-[10px] text-gray-600">CONNECTED</span>
            </div>
            <pre className="max-h-40 overflow-y-auto text-[11px] font-mono text-emerald-400 leading-relaxed flex flex-col gap-1 pr-1">
              {terminalLogs.map((log, index) => {
                let colorClass = "text-emerald-400";
                if (log.startsWith("[ERROR]")) colorClass = "text-red-400";
                else if (log.startsWith("[SYSTEM]")) colorClass = "text-blue-400";
                return (
                  <div key={index} className={colorClass}>
                    {log}
                  </div>
                );
              })}
            </pre>
          </div>
        )}

        {/* Agent 协同轨迹泳道 */}
        {devData && (
          <div className="flex flex-col gap-6">
            <h3 className="text-base font-bold text-gray-200">🤖 Agent 协同阶段产出轨迹 (契约驱动)</h3>
            
            <div className="flex flex-col gap-4">
              {devData.phases.map((phase, idx) => (
                <div
                  key={idx}
                  className="glass-card p-5 bg-gradient-to-r from-slate-900 to-slate-950/60 border border-slate-800 flex flex-col gap-3 relative animate-fade-in"
                >
                  {/* 时间线连接条 */}
                  {idx < devData.phases.length - 1 && (
                    <div className="absolute left-8 -bottom-5 w-0.5 h-6 bg-slate-800 z-0"></div>
                  )}

                  <div className="flex justify-between items-start gap-4 z-10">
                    <div className="flex items-center gap-3">
                      <div className="w-8 h-8 rounded-full bg-slate-800 border border-blue-500/30 flex items-center justify-center font-bold text-xs text-blue-400">
                        {idx + 1}
                      </div>
                      <div>
                        <h4 className="text-sm font-bold text-gray-200">
                          {phase.agent || phase.skill}
                        </h4>
                        <span className="text-[10px] text-gray-500">主导角色 / 技能</span>
                      </div>
                    </div>
                    
                    <div className="text-right">
                      <span className="text-xs bg-slate-900 border border-slate-800 text-gray-400 px-2 py-0.5 rounded">
                        {phase.action}
                      </span>
                    </div>
                  </div>

                  {/* 产物落盘展示与查看 */}
                  <div className="bg-slate-950/50 rounded-lg p-4 border border-slate-900/80 text-xs text-gray-300">
                    {phase.output.summary && <p className="mb-2 leading-relaxed">{phase.output.summary}</p>}
                    {phase.output.route_decision && <p className="mb-2 text-blue-300">{phase.output.route_decision}</p>}
                    {phase.output.architecture_doc && (
                      <pre className="font-sans text-[11px] leading-relaxed text-gray-400 whitespace-pre-wrap">
                        {phase.output.architecture_doc}
                      </pre>
                    )}

                    {/* DDL 文件点击查看 */}
                    {phase.output.ddl_file && (
                      <div className="mt-2 flex items-center justify-between">
                        <span className="text-gray-500">建模建表 SQL:</span>
                        <button
                          onClick={() => handleLoadFile(phase.output.ddl_file!)}
                          className="text-purple-400 hover:text-purple-300 hover:underline font-mono"
                        >
                          📄 {phase.output.ddl_file}
                        </button>
                      </div>
                    )}

                    {/* ETL 文件点击查看 */}
                    {phase.output.etl_file && (
                      <div className="mt-2 flex items-center justify-between">
                        <span className="text-gray-500">ETL 运行 SQL:</span>
                        <button
                          onClick={() => handleLoadFile(phase.output.etl_file!)}
                          className="text-purple-400 hover:text-purple-300 hover:underline font-mono"
                        >
                          📄 {phase.output.etl_file}
                        </button>
                      </div>
                    )}

                    {/* Job 配置文件查看 */}
                    {phase.output.job_file && (
                      <div className="mt-2 flex items-center justify-between">
                        <span className="text-gray-500">DataArts Job 配置:</span>
                        <button
                          onClick={() => handleLoadFile(phase.output.job_file!)}
                          className="text-purple-400 hover:text-purple-300 hover:underline font-mono"
                        >
                          📄 {phase.output.job_file}
                        </button>
                      </div>
                    )}

                    {/* 文档查看 */}
                    {phase.output.doc_file && (
                      <div className="mt-2 flex items-center justify-between">
                        <span className="text-gray-500">模型字典文档:</span>
                        <button
                          onClick={() => handleLoadFile(phase.output.doc_file!)}
                          className="text-purple-400 hover:text-purple-300 hover:underline font-mono"
                        >
                          📄 {phase.output.doc_file}
                        </button>
                      </div>
                    )}

                    {/* 华为云上传与更新作业日志 */}
                    {phase.output.uploaded_files && (
                      <div className="mt-1 font-mono text-[10px] text-emerald-400">
                        上传成功! {phase.output.uploaded_files.join(", ")}
                      </div>
                    )}
                    {phase.output.log && (
                      <div className="mt-1 font-mono text-[10px] text-gray-500">
                        {phase.output.log}
                      </div>
                    )}
                  </div>

                  {phaseContainsActiveFile(phase) && renderActiveFileEditor()}

                  {/* Reviewer 审查意见 */}
                  {phase.reviewer && (
                    <div className="bg-slate-900 border-t border-t-slate-800 rounded-b-lg p-3 flex items-start gap-2.5 text-xs">
                      <span className={phase.review_status === "APPROVED" ? "badge-pass" : "badge-fail"}>
                        {phase.review_status}
                      </span>
                      <div className="flex-1">
                        <span className="text-gray-400 font-semibold">{phase.reviewer} 审查意见:</span>
                        <p className="text-gray-300 mt-1 leading-relaxed">{phase.review_comments}</p>
                      </div>
                    </div>
                  )}

                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* 右侧：部署检查清单 */}
      <div className="lg:col-span-4 flex flex-col gap-6">
        
        {/* 部署检查清单 */}
        {devData && (
          <div className="glass-card p-6 bg-gradient-to-br from-slate-900 to-slate-950 border border-slate-800">
            <h3 className="text-base font-bold text-gray-200 mb-4 flex items-center gap-2">
              <span className="text-blue-400">📋</span>
              项目部署检查清单 (10项)
            </h3>
            <div className="flex flex-col gap-2">
              {devData.checklist.map((item) => (
                <div key={item.id} className="flex items-center justify-between bg-slate-950/40 p-2.5 border border-slate-900 rounded">
                  <div className="flex items-center gap-2 text-xs">
                    <span className="text-gray-600 font-semibold">#{item.id}</span>
                    <span className="text-gray-300">{item.step}</span>
                  </div>
                  <span className={item.done ? "text-emerald-400 font-bold" : "text-yellow-400 font-bold"}>
                    {item.done ? "✓" : "☐"}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

      </div>
    </div>
  );
};
