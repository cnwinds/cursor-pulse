import { formatSpend, formatTokens } from '@/utils/usage'

export type DailyTrendPoint = {
  date: string
  tokens_total: number
  tokens_input?: number
  tokens_output?: number
  tokens_cache_read?: number
  cost_usd: number
}

export const DAILY_TREND_TOKEN_NAMES = ['输入', '输出', '输入(cache)'] as const

type TooltipParam = {
  seriesName: string
  dataIndex: number
  marker: string
}

function tokenParts(day: DailyTrendPoint) {
  const input = Number(day.tokens_input) || 0
  const output = Number(day.tokens_output) || 0
  const cache = Number(day.tokens_cache_read) || 0
  if (input || output || cache) return { input, output, cache }
  // 旧数据只有总额时，仍画出与 tokens_total 等高的柱
  return { input: 0, output: 0, cache: Number(day.tokens_total) || 0 }
}

/** 概览 / 用量分析共用：堆叠柱（输入+输出+cache = 当日总 Token）+ 花费折线。 */
export function dailyTrendChartOption(days: DailyTrendPoint[]) {
  const labels = days.map((d) => d.date.slice(5))
  const parts = days.map(tokenParts)
  return {
    color: ['#2563eb', '#0d9488', '#8b5cf6', '#f59e0b'],
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params: TooltipParam[]) => {
        if (!params?.length) return ''
        const day = days[params[0].dataIndex]
        if (!day) return ''
        const mark = (name: string) => params.find((p) => p.seriesName === name)?.marker ?? ''
        return [
          day.date.slice(5),
          `总 Token：${formatTokens(day.tokens_total)}`,
          `${mark('输入')}输入：${formatTokens(tokenParts(day).input)}`,
          `${mark('输出')}输出：${formatTokens(tokenParts(day).output)}`,
          `${mark('输入(cache)')}输入(cache)：${formatTokens(tokenParts(day).cache)}`,
          `${mark('花费')}花费：${formatSpend(day.cost_usd)}`,
        ].join('<br/>')
      },
    },
    // ECharts 6 默认 legend.bottom=15，会叠在 x 轴日期和矮柱上；钉到顶部并清掉 bottom。
    legend: {
      data: [...DAILY_TREND_TOKEN_NAMES, '花费'],
      top: 0,
      left: 'center',
      itemGap: 16,
      itemWidth: 18,
      itemHeight: 10,
      textStyle: { fontSize: 12 },
    },
    grid: { left: 12, right: 12, top: 52, bottom: 8, containLabel: true },
    xAxis: { type: 'category', data: labels, boundaryGap: true },
    yAxis: [
      {
        type: 'value',
        name: 'Token',
        axisLabel: { formatter: (v: number) => (v ? formatTokens(v) : '0') },
      },
      {
        type: 'value',
        name: '花费 $',
        axisLabel: { formatter: (v: number) => `$${v}` },
      },
    ],
    series: [
      {
        name: '输入',
        type: 'bar',
        stack: 'tokens',
        barMaxWidth: 28,
        data: parts.map((p) => p.input),
      },
      {
        name: '输出',
        type: 'bar',
        stack: 'tokens',
        barMaxWidth: 28,
        data: parts.map((p) => p.output),
      },
      {
        name: '输入(cache)',
        type: 'bar',
        stack: 'tokens',
        barMaxWidth: 28,
        data: parts.map((p) => p.cache),
      },
      {
        name: '花费',
        type: 'line',
        yAxisIndex: 1,
        showSymbol: true,
        symbolSize: 6,
        data: days.map((d) => d.cost_usd),
      },
    ],
  }
}
