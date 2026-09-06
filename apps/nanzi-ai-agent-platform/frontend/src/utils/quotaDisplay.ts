import {
  formatTokenCompact,
  formatTokenFull,
  shouldShowTokenFullHint,
} from '@/utils/tokenFormat'

export interface QuotaStatusLike {
  period_start: string
  period_end: string
  used_tokens: number
  limit_tokens: number | null
  remaining_tokens: number | null
  source_label?: string | null
  is_admin_bypass: boolean
}

function tokenLine(label: string, value: number, unlimited = false): string {
  if (unlimited) return `- ${label}：不限`
  const compact = formatTokenCompact(value)
  const full = formatTokenFull(value)
  if (shouldShowTokenFullHint(value) && compact !== full) {
    return `- ${label}：**${compact}**（${full}）`
  }
  return `- ${label}：**${compact}**`
}

/** 对话内展示本月额度（Markdown） */
export function buildQuotaStatusMarkdown(status: QuotaStatusLike | null): string {
  if (!status) {
    return '❌ 暂时无法获取额度信息，请稍后重试，或前往个人中心查看。'
  }

  const period = `${status.period_start} ~ ${status.period_end}`

  if (status.is_admin_bypass) {
    return [
      '✅ **本月 Token 额度：管理员豁免**',
      '',
      `- 统计周期：${period}`,
      `- 已用：${formatTokenCompact(status.used_tokens)}`,
      '',
      '> 系统管理员不受月 Token 上限限制。',
    ].join('\n')
  }

  if (status.limit_tokens == null) {
    return [
      '📊 **本月 Token 用量**',
      '',
      `- 统计周期：${period}`,
      tokenLine('已用', status.used_tokens),
      '- 上限：不限额',
      status.source_label ? `\n> 策略来源：${status.source_label}` : '',
    ].filter(Boolean).join('\n')
  }

  const used = status.used_tokens
  const limit = status.limit_tokens
  const remaining = status.remaining_tokens ?? Math.max(limit - used, 0)
  const percent = Math.min(100, Math.round((used / limit) * 100))
  const header =
    percent >= 100 ? '🚫 **本月 Token 额度已用尽**' :
    percent >= 80 ? '⚠️ **本月 Token 额度即将用尽**' :
    '📊 **本月 Token 额度**'

  return [
    header,
    '',
    `- 统计周期：${period}`,
    tokenLine('已用', used),
    tokenLine('上限', limit),
    tokenLine('剩余', remaining),
    `- 已使用：**${percent}%**`,
    status.source_label ? `\n> 策略来源：${status.source_label}` : '',
    percent >= 100 ? '\n> 请联系管理员调整额度，或前往 **个人中心 → 我的 Token 消耗** 查看明细。' : '',
  ].filter(Boolean).join('\n')
}
