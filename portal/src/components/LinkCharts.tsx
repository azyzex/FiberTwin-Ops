import type { EChartsOption } from 'echarts'
import { useMemo } from 'react'
import type { IncidentEvent, ReplayBundle } from '../types'
import Chart from './Chart'

const PALETTE = ['#38bdf8', '#f472b6', '#a3e635', '#fbbf24', '#c084fc', '#2dd4bf']

const hhmmss = (iso: string) => iso.slice(11, 19)

function eventLines(events: IncidentEvent[]) {
  return events.map((e) => ({ xAxis: hhmmss(e.ts), label: { formatter: e.type.replace(/_/g, ' '), color: '#94a3b8', fontSize: 9 } }))
}

function base(x: string[], title: string, unit: string): EChartsOption {
  return {
    backgroundColor: 'transparent',
    title: { text: title, left: 8, top: 4, textStyle: { fontSize: 12, color: '#cbd5e1' } },
    grid: { left: 44, right: 14, top: 52, bottom: 28 },
    tooltip: { trigger: 'axis', valueFormatter: (v) => `${v} ${unit}` },
    legend: { top: 24, left: 8, textStyle: { fontSize: 10 }, itemWidth: 12, itemHeight: 8 },
    xAxis: { type: 'category', data: x, axisLabel: { fontSize: 9 } },
    animation: false,
  }
}

/** OSNR margin and utilisation per IP link over the whole replay, with a cursor at the current frame. */
export default function LinkCharts({ bundle, frame }: { bundle: ReplayBundle; frame: number }) {
  const x = useMemo(() => bundle.frames.map((f) => hhmmss(f.ts)), [bundle])
  const ids = useMemo(() => bundle.topology.ip_links.map((l) => l.id), [bundle])
  const cursor = x[frame]

  const margin: EChartsOption = useMemo(
    () => ({
      ...base(x, 'Modelled OSNR margin per link', 'dB'),
      yAxis: { type: 'value', name: 'dB', nameTextStyle: { fontSize: 9 }, axisLabel: { fontSize: 9 } },
      series: ids.map((id, i) => ({
        name: id,
        type: 'line',
        showSymbol: false,
        lineStyle: { width: 2 },
        color: PALETTE[i % PALETTE.length],
        data: bundle.frames.map((f) => f.links[id]?.osnr_margin_db ?? null),
        ...(i === 0
          ? {
              markLine: {
                symbol: 'none',
                silent: true,
                data: [
                  { yAxis: 3, lineStyle: { color: '#f59e0b', type: 'dashed' }, label: { formatter: 'warning', position: 'insideStartTop', color: '#f59e0b', fontSize: 9 } },
                  { yAxis: 0, lineStyle: { color: '#ef4444', type: 'dashed' }, label: { formatter: 'FEC cliff', position: 'insideStartTop', color: '#ef4444', fontSize: 9 } },
                  { xAxis: cursor, lineStyle: { color: '#f8fafc', width: 1.5, type: 'solid' }, label: { show: false } },
                ],
              },
            }
          : {}),
      })),
    }),
    [bundle, ids, cursor, x],
  )

  const util: EChartsOption = useMemo(
    () => ({
      ...base(x, 'Utilisation per link', '%'),
      yAxis: { type: 'value', max: 100, name: '%', nameTextStyle: { fontSize: 9 }, axisLabel: { fontSize: 9 } },
      series: ids.map((id, i) => ({
        name: id,
        type: 'line',
        showSymbol: false,
        step: 'end',
        lineStyle: { width: 2 },
        color: PALETTE[i % PALETTE.length],
        data: bundle.frames.map((f) => f.links[id]?.util_pct ?? null),
        ...(i === 0
          ? {
              markLine: {
                symbol: 'none',
                silent: true,
                data: [
                  { yAxis: 80, lineStyle: { color: '#ef4444', type: 'dashed' }, label: { formatter: '80% limit', position: 'insideStartTop', color: '#ef4444', fontSize: 9 } },
                  ...eventLines(bundle.events.filter((e) => e.type === 'deployed')).map((d) => ({ ...d, lineStyle: { color: '#64748b' } })),
                  { xAxis: cursor, lineStyle: { color: '#f8fafc', width: 1.5, type: 'solid' }, label: { show: false } },
                ],
              },
            }
          : {}),
      })),
    }),
    [bundle, ids, cursor, x],
  )

  return (
    <div className="grid gap-3 md:grid-cols-2">
      <Chart option={margin} className="h-56 rounded-lg border border-slate-800 bg-slate-900/60" testId="chart-margin" />
      <Chart option={util} className="h-56 rounded-lg border border-slate-800 bg-slate-900/60" testId="chart-util" />
    </div>
  )
}
