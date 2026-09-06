// -*- coding: utf-8 -*-
import React from 'react';
import type { AttributionData } from '../types';

interface AttributionWidgetProps {
  data: AttributionData;
}

/** Show measured period changes separately from a single-period dimension breakdown. */
export const AttributionWidget: React.FC<AttributionWidgetProps> = ({ data }) => {
  const {
    analysis_type,
    metric_unit,
    metric_display,
    dimension_display,
    total_value,
    top_driver,
    top_driver_ratio,
    waterfall_items
  } = data;
  const isComparison = analysis_type === 'period_comparison';
  const isDimensionBreakdown = analysis_type === 'dimension_breakdown';
  const formatValue = (value?: number) => {
    if (value == null) return '—';
    const formatted = value.toLocaleString(undefined, { maximumFractionDigits: 2 });
    if (metric_unit !== undefined || isDimensionBreakdown || isComparison) {
      return metric_unit ? `${formatted} ${metric_unit}` : formatted;
    }
    return `¥${formatted}`;
  };
  const formatChange = (value?: number) => value == null ? '—' : `${value > 0 ? '+' : ''}${formatValue(value)}`;
  const formatRatio = (value: number) => `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}%`;
  const maxChange = Math.max(...waterfall_items.map(item => Math.abs(item.value)), Number.EPSILON);
  const zeroChange = isComparison && data.total_change === 0;

  return (
    <div style={{
      background: 'linear-gradient(135deg, rgba(30, 41, 59, 0.7) 0%, rgba(15, 23, 42, 0.8) 100%)',
      border: '1px solid rgba(59, 130, 246, 0.3)',
      borderRadius: '12px',
      padding: '16px 20px',
      marginTop: '12px',
      marginBottom: '12px',
      boxShadow: '0 8px 24px -4px rgba(0, 0, 0, 0.3)'
    }}>
      {/* 顶部标题与核心驱动结论 */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{
            background: 'linear-gradient(90deg, #3b82f6, #60a5fa)',
            color: '#fff',
            padding: '3px 8px',
            borderRadius: '6px',
            fontSize: '12px',
            fontWeight: 600
          }}>
            {isComparison ? '周期变动归因' : isDimensionBreakdown ? '指标分组占比' : '异动归因诊断'}
          </span>
          <span style={{ color: '#f1f5f9', fontWeight: 600, fontSize: '15px' }}>
            {metric_display} · 按 {dimension_display} {isDimensionBreakdown ? '分组占比' : '贡献度分解'}
          </span>
        </div>
        <div style={{ color: '#94a3b8', fontSize: '13px' }}>
          {isComparison ? '现期总计' : '指标总计'}: <span style={{ color: '#38bdf8', fontWeight: 600 }}>{formatValue(total_value)}</span>
        </div>
      </div>

      {isComparison && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(145px, 1fr))', gap: '12px', marginBottom: '16px', fontSize: '12px', color: '#94a3b8' }}>
          <div>
            <div>基期：{data.baseline_period?.start} 至 {data.baseline_period?.end}</div>
            <strong style={{ display: 'block', color: '#e2e8f0', fontSize: '17px', marginTop: '4px' }}>{formatValue(data.baseline_value)}</strong>
          </div>
          <div>
            <div>现期：{data.current_period?.start} 至 {data.current_period?.end}</div>
            <strong style={{ display: 'block', color: '#e2e8f0', fontSize: '17px', marginTop: '4px' }}>{formatValue(data.current_value ?? total_value)}</strong>
          </div>
          <div>
            <div>变动量 · 相对基期</div>
            <strong style={{ display: 'block', color: '#38bdf8', fontSize: '17px', marginTop: '4px' }}>{formatChange(data.total_change)}</strong>
            <span>{data.change_rate == null ? '基期为零，变化率不适用' : `${data.change_rate > 0 ? '+' : ''}${data.change_rate.toLocaleString(undefined, { maximumFractionDigits: 2 })}%`}</span>
          </div>
        </div>
      )}

      {/* 核心驱动源高亮卡片 */}
      <div style={{
        background: 'rgba(59, 130, 246, 0.12)',
        border: '1px solid rgba(96, 165, 250, 0.25)',
        borderRadius: '8px',
        padding: '10px 14px',
        marginBottom: '16px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between'
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{ fontSize: '18px' }}>🎯</span>
          <div>
            <div style={{ color: '#93c5fd', fontSize: '12px' }}>{isDimensionBreakdown ? '数值最高的分组' : '变动贡献最大的分组'}</div>
            <div style={{ color: '#ffffff', fontWeight: 600, fontSize: '14px' }}>
              {top_driver || '暂无分组数据'}
            </div>
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <span style={{ color: '#93c5fd', fontSize: '12px' }}>{isDimensionBreakdown ? '分组占比' : '占总变动的比例'}</span>
          <div style={{ color: '#60a5fa', fontWeight: 700, fontSize: '18px' }}>
            {zeroChange ? '不适用' : formatRatio(top_driver_ratio)}
          </div>
        </div>
      </div>

      {isComparison && <p style={{ color: '#94a3b8', fontSize: '12px', lineHeight: 1.6, marginBottom: '12px' }}>
        分组变动 = 现期 − 基期；贡献率 = 分组变动 ÷ 总变动。反向变动可能产生负贡献，比例也可能超过 100%。
        {zeroChange && ' 本次总变动为零，不计算贡献率。'}
        数据拆解展示变动来源，不代表业务因果结论。
      </p>}

      {/* 瀑布分解贡献条形进度 */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
        {waterfall_items.map((item, idx) => (
          <div key={item.name + idx} style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
              <span style={{ color: '#e2e8f0', fontWeight: 500 }}>
                {idx + 1}. {item.name}
              </span>
              <span style={{ color: '#94a3b8' }}>
                {isComparison ? formatChange(item.value) : formatValue(item.value)} <strong style={{ color: '#38bdf8', marginLeft: '6px' }}>({zeroChange ? '贡献率不适用' : formatRatio(item.ratio)})</strong>
              </span>
            </div>
            {isComparison && <div style={{ color: '#94a3b8', fontSize: '12px' }}>基期 {formatValue(item.baseline_value)} → 现期 {formatValue(item.current_value)}</div>}
            {/* 进度条底槽 */}
            <div style={{
              height: '8px',
              width: '100%',
              background: 'rgba(51, 65, 85, 0.6)',
              borderRadius: '999px',
              overflow: 'hidden',
              position: 'relative'
            }}>
              {isComparison && <div style={{ position: 'absolute', left: '50%', top: 0, height: '100%', width: '1px', background: '#94a3b8', zIndex: 1 }} />}
              <div style={{
                height: '100%',
                position: isComparison ? 'absolute' : undefined,
                left: isComparison ? `${item.value < 0 ? 50 - Math.abs(item.value) / maxChange * 50 : 50}%` : undefined,
                width: `${isComparison ? Math.abs(item.value) / maxChange * 50 : Math.min(100, Math.max(0, item.ratio))}%`,
                background: isComparison ? (item.value < 0 ? '#38bdf8' : '#f59e0b') : idx === 0
                  ? 'linear-gradient(90deg, #3b82f6, #06b6d4)'
                  : 'linear-gradient(90deg, #64748b, #94a3b8)',
                borderRadius: '999px',
                transition: 'width 0.4s ease'
              }} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};
