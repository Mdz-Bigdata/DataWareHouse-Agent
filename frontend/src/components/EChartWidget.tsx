import React, { useEffect, useRef } from "react";
import * as echarts from "echarts";

interface EChartWidgetProps {
  type: string;
  title: string;
  config: any;
}

export const EChartWidget: React.FC<EChartWidgetProps> = ({ type, title, config }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartInstanceRef = useRef<echarts.ECharts | null>(null);

  // 如果是数字卡片，在 React 里用高亮文本渲染，比用 ECharts 效果好很多
  if (type === "card") {
    return (
      <div className="glass-card p-6 flex flex-col items-center justify-center text-center h-48 border border-purple-500/20 bg-gradient-to-br from-purple-950/20 to-blue-950/20">
        <h4 className="text-gray-400 text-sm font-medium mb-2">{title}</h4>
        <div className="text-4xl font-extrabold text-transparent bg-clip-text bg-gradient-to-r from-purple-400 to-pink-400 tracking-tight">
          {config.value}
        </div>
        <p className="text-gray-500 text-xs mt-3">指标名称: {config.label}</p>
      </div>
    );
  }

  useEffect(() => {
    if (!containerRef.current) return;

    // 如果已存在实例，先销毁
    if (chartInstanceRef.current) {
      chartInstanceRef.current.dispose();
    }

    // 初始化 ECharts 暗黑主题风
    const chart = echarts.init(containerRef.current, "dark", {
      renderer: "svg" // 渲染 svg 性能高，在高分屏下也清晰
    });
    chartInstanceRef.current = chart;

    let option: echarts.EChartsOption = {};

    const textStyle = { color: "rgba(255, 255, 255, 0.7)", fontFamily: "Inter, sans-serif" };
    const lineStyle = { color: "rgba(255, 255, 255, 0.08)" };

    // 1. 折线图 (line)
    if (type === "line") {
      option = {
        backgroundColor: "transparent",
        title: { text: title, textStyle: { fontSize: 14, color: "#fff", fontWeight: "normal" }, left: "center" },
        tooltip: { trigger: "axis", backgroundColor: "#1e293b", borderColor: "#475569", textStyle: { color: "#fff" } },
        grid: { left: "3%", right: "4%", bottom: "3%", containLabel: true },
        xAxis: {
          type: "category",
          data: config.xAxis?.data || [],
          axisLine: { lineStyle: { color: "rgba(255,255,255,0.2)" } },
          axisLabel: { color: "rgba(255, 255, 255, 0.7)", fontFamily: "Inter, sans-serif" }
        },
        yAxis: {
          type: "value",
          splitLine: { lineStyle },
          axisLabel: { color: "rgba(255, 255, 255, 0.7)", fontFamily: "Inter, sans-serif" }
        },
        series: (config.series || []).map((s: any, idx: number) => ({
          name: s.name,
          type: "line",
          data: s.data,
          smooth: true,
          showSymbol: true,
          symbolSize: 6,
          lineStyle: {
            width: 3,
            color: idx === 0 ? "#8b5cf6" : "#3b82f6" // 紫色和蓝色
          },
          itemStyle: {
            color: idx === 0 ? "#a78bfa" : "#60a5fa"
          },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: idx === 0 ? "rgba(139, 92, 246, 0.3)" : "rgba(59, 130, 246, 0.3)" },
              { offset: 1, color: "rgba(0, 0, 0, 0)" }
            ])
          }
        }))
      };
    }
    // 2. 柱状图 (bar / bar_group)
    else if (type === "bar" || type === "bar_group") {
      option = {
        backgroundColor: "transparent",
        title: { text: title, textStyle: { fontSize: 14, color: "#fff", fontWeight: "normal" }, left: "center" },
        tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, backgroundColor: "#1e293b", borderColor: "#475569", textStyle: { color: "#fff" } },
        legend: { top: "8%", textStyle },
        grid: { left: "3%", right: "4%", bottom: "3%", containLabel: true },
        xAxis: {
          type: "category",
          data: config.xAxis?.data || [],
          axisLine: { lineStyle: { color: "rgba(255,255,255,0.2)" } },
          axisLabel: { color: "rgba(255, 255, 255, 0.7)", fontFamily: "Inter, sans-serif" }
        },
        yAxis: {
          type: "value",
          splitLine: { lineStyle },
          axisLabel: { color: "rgba(255, 255, 255, 0.7)", fontFamily: "Inter, sans-serif" }
        },
        series: (config.series || []).map((s: any, idx: number) => ({
          name: s.name,
          type: "bar",
          data: s.data,
          barMaxWidth: 24,
          itemStyle: {
            borderRadius: [4, 4, 0, 0],
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: idx === 0 ? "#8b5cf6" : (idx === 1 ? "#3b82f6" : "#ec4899") },
              { offset: 1, color: idx === 0 ? "#4c1d95" : (idx === 1 ? "#1e3a8a" : "#831843") }
            ])
          }
        }))
      };
    }
    // 3. 饼图 (pie)
    else if (type === "pie") {
      const pieData = config.series?.[0]?.data || [];
      option = {
        backgroundColor: "transparent",
        title: { text: title, textStyle: { fontSize: 14, color: "#fff", fontWeight: "normal" }, left: "center" },
        tooltip: { trigger: "item", formatter: "{a} <br/>{b} : {c} ({d}%)", backgroundColor: "#1e293b", borderColor: "#475569", textStyle: { color: "#fff" } },
        legend: { bottom: "0%", left: "center", textStyle, itemWidth: 10, itemHeight: 10 },
        series: [
          {
            name: title,
            type: "pie",
            radius: ["40%", "70%"],
            avoidLabelOverlap: false,
            itemStyle: {
              borderRadius: 6,
              borderColor: "#0f172a",
              borderWidth: 2
            },
            label: {
              show: false,
              position: "center"
            },
            emphasis: {
              label: {
                show: true,
                fontSize: 16,
                fontWeight: "bold",
                color: "#fff"
              }
            },
            labelLine: {
              show: false
            },
            data: pieData
          }
        ],
        color: ["#7c3aed", "#2563eb", "#db2777", "#10b981", "#f59e0b"]
      };
    }

    chart.setOption(option);

    // 窗口缩放自适应
    const handleResize = () => {
      chart.resize();
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.dispose();
    };
  }, [type, title, config]);

  return (
    <div className="glass-card p-4 flex flex-col h-72">
      <div ref={containerRef} className="w-full h-full" />
    </div>
  );
};
