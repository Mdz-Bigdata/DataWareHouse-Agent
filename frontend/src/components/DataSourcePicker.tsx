import React, { useEffect, useMemo, useRef, useState } from "react";
import type { DataSourceCatalog, DataSourceInfo, DataSourceOption } from "../types";

interface DataSourcePickerProps {
  active: DataSourceInfo | null;
  unavailable: boolean;
  onSwitched: (info: DataSourceInfo, dialect: string) => void;
  apiBase: string;
}

type Availability = "all" | "available" | "unconfigured";

const FILTERS: Array<{ key: Availability; label: string }> = [
  { key: "all", label: "全部" },
  { key: "available", label: "已就绪" },
  { key: "unconfigured", label: "待配置" },
];

export const DataSourcePicker: React.FC<DataSourcePickerProps> = ({
  active, unavailable, onSwitched, apiBase,
}) => {
  const [open, setOpen] = useState(false);
  const [catalog, setCatalog] = useState<DataSourceCatalog | null>(null);
  const [keyword, setKeyword] = useState("");
  const [availability, setAvailability] = useState<Availability>("all");
  const [switchingTo, setSwitchingTo] = useState<string | null>(null);
  const [error, setError] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);

  const loadCatalog = async () => {
    try {
      const response = await fetch(`${apiBase}/chat/data-sources`);
      if (!response.ok) throw new Error(String(response.status));
      setCatalog(await response.json());
      setError("");
    } catch {
      setError("无法读取数据源列表，请确认后端服务状态。");
    }
  };

  useEffect(() => {
    if (open) loadCatalog();
  }, [open]);

  // Clicking elsewhere or pressing Escape closes the picker without switching.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const visible = useMemo(() => {
    const term = keyword.trim().toLowerCase();
    return (catalog?.sources || []).filter(source => {
      if (availability === "available" && !source.available) return false;
      if (availability === "unconfigured" && source.available) return false;
      if (!term) return true;
      return [source.engine, source.engine_label, source.dialect, source.destination]
        .some(value => value.toLowerCase().includes(term));
    });
  }, [catalog, keyword, availability]);

  const select = async (source: DataSourceOption) => {
    if (!source.available || source.active || switchingTo) return;
    setSwitchingTo(source.id);
    setError("");
    try {
      const response = await fetch(`${apiBase}/chat/data-source`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: source.id }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail || "切换失败");
      onSwitched(payload as DataSourceInfo, source.dialect);
      await loadCatalog();
      setOpen(false);
    } catch (switchError) {
      setError(switchError instanceof Error ? switchError.message : "切换数据源失败");
    } finally {
      setSwitchingTo(null);
    }
  };

  const isDemo = active?.mode === "demo";
  const label = active?.label || (unavailable ? "暂时无法确认" : "正在确认");

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        onClick={() => setOpen(value => !value)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border font-semibold transition-colors ${
          isDemo
            ? "border-amber-500/40 bg-amber-950/30 text-amber-200 hover:border-amber-400/70"
            : "border-slate-700 bg-slate-900/70 text-slate-200 hover:border-purple-500/60"
        }`}
      >
        <span>当前数据源：{label}</span>
        <span className={`text-[10px] transition-transform ${open ? "rotate-180" : ""}`}>▼</span>
      </button>

      {open && (
        <div className="absolute z-30 mt-2 w-[26rem] max-w-[90vw] rounded-xl border border-slate-700 bg-slate-950/98 shadow-2xl shadow-black/60 p-3 backdrop-blur">
          <input
            type="search"
            value={keyword}
            onChange={event => setKeyword(event.target.value)}
            placeholder="筛选引擎，如 doris / clickhouse / duckdb"
            aria-label="筛选数据源"
            className="w-full bg-slate-900 border border-slate-700 text-gray-200 rounded-lg px-3 py-2 text-xs focus:outline-none focus:border-purple-500 placeholder:text-gray-600"
          />
          <div className="flex gap-1.5 mt-2 mb-2">
            {FILTERS.map(filter => (
              <button
                key={filter.key}
                type="button"
                onClick={() => setAvailability(filter.key)}
                className={`px-2 py-1 rounded-md text-[11px] border transition-colors ${
                  availability === filter.key
                    ? "border-purple-500/60 bg-purple-950/40 text-purple-200"
                    : "border-slate-800 bg-slate-900/60 text-gray-400 hover:text-gray-200"
                }`}
              >
                {filter.label}
              </button>
            ))}
          </div>

          {error && <p className="text-[11px] text-red-300 px-1 pb-2">{error}</p>}

          <div className="flex flex-col gap-1.5 max-h-72 overflow-y-auto pr-1">
            {visible.length === 0 && (
              <p className="text-[11px] text-gray-500 text-center py-4">没有匹配的数据源</p>
            )}
            {visible.map(source => (
              <button
                key={source.id}
                type="button"
                data-source-option={source.engine}
                disabled={!source.available || switchingTo !== null}
                onClick={() => select(source)}
                title={source.unavailable_reason || source.destination}
                className={`text-left rounded-lg border px-3 py-2 transition-colors ${
                  source.active
                    ? "border-purple-500/60 bg-purple-950/30"
                    : source.available
                      ? "border-slate-800 bg-slate-900/60 hover:border-purple-500/40 hover:bg-purple-950/10 cursor-pointer"
                      : "border-slate-800/60 bg-slate-900/30 opacity-60 cursor-not-allowed"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-semibold text-gray-200">{source.engine_label}</span>
                  <span className="flex items-center gap-1.5">
                    <span className="text-[9px] uppercase font-mono bg-slate-800 text-gray-400 px-1.5 py-0.5 rounded">
                      {source.dialect}
                    </span>
                    {source.active && <span className="text-[10px] text-purple-300">● 使用中</span>}
                    {!source.active && source.available && (
                      <span className="text-[10px] text-emerald-400">可切换</span>
                    )}
                    {!source.available && <span className="text-[10px] text-gray-500">待配置</span>}
                  </span>
                </div>
                <p className="text-[10px] text-gray-500 mt-1 leading-relaxed line-clamp-2">
                  {switchingTo === source.id
                    ? "正在切换并重建元数据……"
                    : source.destination || source.unavailable_reason || "内置演示数仓，无需配置"}
                </p>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};
