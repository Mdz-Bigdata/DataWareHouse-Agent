// -*- coding: utf-8 -*-
import React, { useState } from 'react';
import type { LineageData } from '../types';

interface LineageGraphWidgetProps {
  data: LineageData;
}

/**
 * 湖仓数据分层血缘拓扑可视化组件
 * 对应《智驾数据闭环湖仓实战：Paimon + Neo4j 湖图双引擎数据血缘追溯系统》：
 * 展示 ODS -> DWD -> DWS -> ADS 全链路数仓分层拓扑与加工转换关系
 */
export const LineageGraphWidget: React.FC<LineageGraphWidgetProps> = ({ data }) => {
  const [selectedLayer, setSelectedLayer] = useState<string>('ALL');

  const layers = ['ALL', 'ODS', 'DWD', 'DIM', 'DWS', 'ADS'];

  const layerColorMap: Record<string, { bg: string; text: string; border: string }> = {
    ODS: { bg: 'rgba(239, 68, 68, 0.15)', text: '#fca5a5', border: 'rgba(239, 68, 68, 0.3)' },
    DWD: { bg: 'rgba(245, 158, 11, 0.15)', text: '#fcd34d', border: 'rgba(245, 158, 11, 0.3)' },
    DIM: { bg: 'rgba(16, 185, 129, 0.15)', text: '#6ee7b7', border: 'rgba(16, 185, 129, 0.3)' },
    DWS: { bg: 'rgba(59, 130, 246, 0.15)', text: '#93c5fd', border: 'rgba(59, 130, 246, 0.3)' },
    ADS: { bg: 'rgba(168, 85, 247, 0.15)', text: '#d8b4fe', border: 'rgba(168, 85, 247, 0.3)' },
    ALL: { bg: 'rgba(100, 116, 139, 0.15)', text: '#cbd5e1', border: 'rgba(100, 116, 139, 0.3)' }
  };

  const filteredNodes = selectedLayer === 'ALL'
    ? data.nodes
    : data.nodes.filter(n => n.layer === selectedLayer);

  return (
    <div style={{
      background: 'linear-gradient(135deg, rgba(15, 23, 42, 0.85) 0%, rgba(30, 41, 59, 0.8) 100%)',
      border: '1px solid rgba(148, 163, 184, 0.2)',
      borderRadius: '12px',
      padding: '16px 20px',
      marginTop: '12px',
      marginBottom: '12px'
    }}>
      {/* 顶部标题与分层筛选 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '14px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{
            background: 'linear-gradient(90deg, #8b5cf6, #ec4899)',
            color: '#fff',
            padding: '3px 8px',
            borderRadius: '6px',
            fontSize: '12px',
            fontWeight: 600
          }}>
            湖图双引擎血缘
          </span>
          <span style={{ color: '#f1f5f9', fontWeight: 600, fontSize: '15px' }}>
            数仓端到端链路拓扑追溯
          </span>
        </div>
        {/* 分层标签过滤 */}
        <div style={{ display: 'flex', gap: '6px' }}>
          {layers.map(layer => (
            <button
              key={layer}
              onClick={() => setSelectedLayer(layer)}
              style={{
                background: selectedLayer === layer ? '#3b82f6' : 'rgba(51, 65, 85, 0.5)',
                color: selectedLayer === layer ? '#fff' : '#94a3b8',
                border: 'none',
                padding: '3px 8px',
                borderRadius: '4px',
                fontSize: '11px',
                cursor: 'pointer',
                transition: 'all 0.2s'
              }}
            >
              {layer}
            </button>
          ))}
        </div>
      </div>

      {/* 节点拓扑网格 */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
        gap: '10px',
        marginBottom: '14px'
      }}>
        {filteredNodes.map(node => {
          const colors = layerColorMap[node.layer] || layerColorMap['ALL'];
          return (
            <div
              key={node.id}
              style={{
                background: colors.bg,
                border: `1px solid ${colors.border}`,
                borderRadius: '8px',
                padding: '10px',
                display: 'flex',
                flexDirection: 'column',
                gap: '4px'
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{
                  color: colors.text,
                  fontWeight: 700,
                  fontSize: '11px',
                  border: `1px solid ${colors.border}`,
                  padding: '1px 5px',
                  borderRadius: '3px'
                }}>
                  {node.layer}
                </span>
                <span style={{ color: '#94a3b8', fontSize: '11px' }}>{node.domain}</span>
              </div>
              <div style={{ color: '#f8fafc', fontWeight: 600, fontSize: '13px', marginTop: '2px' }}>
                {node.name}
              </div>
              <div style={{ color: '#64748b', fontSize: '11px', fontFamily: 'monospace' }}>
                {node.id}
              </div>
            </div>
          );
        })}
      </div>

      {/* 关键流转加工关系列表 */}
      <div style={{
        background: 'rgba(15, 23, 42, 0.6)',
        borderRadius: '8px',
        padding: '10px 12px',
        border: '1px solid rgba(51, 65, 85, 0.5)'
      }}>
        <div style={{ color: '#cbd5e1', fontSize: '12px', fontWeight: 600, marginBottom: '8px' }}>
          🔗 数据流转与转换加工规则 (Pipeline Lineage):
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
          {data.edges.slice(0, 4).map((edge, idx) => (
            <div
              key={idx}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                fontSize: '12px',
                color: '#94a3b8'
              }}
            >
              <span style={{ color: '#60a5fa', fontFamily: 'monospace' }}>{edge.source}</span>
              <span style={{ color: '#e2e8f0' }}>➔</span>
              <span style={{ color: '#a78bfa', fontFamily: 'monospace' }}>{edge.target}</span>
              <span style={{
                color: '#64748b',
                background: 'rgba(30, 41, 59, 0.8)',
                padding: '2px 6px',
                borderRadius: '4px',
                fontSize: '11px'
              }}>
                {edge.relation}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
