import type { EChartsOption } from 'echarts'
import { useEffect, useMemo, useState } from 'react'
import Chart from './Chart'

interface OpticsData {
  generated_by: string
  required_osnr_db: number
  params: Record<string, number>
  curve: { osnr_db: number; q: number; ber: number; plr: number }[]
  links: Record<string, { osnr_db: number; margin_db: number; spans: number }>
  ramp_F12_span0: { extra_db: number; L12: Step; L13: Step }[]
}
interface Step {
  osnr_db: number
  margin_db: number
  plr: number
}

const FLOOR = 1e-15 // log axis can't show 0: anything below is "error-free"
const fmt = (x: number) => (x < 1e-12 ? '≈ 0' : x < 1e-3 ? x.toExponential(1) : `${(x * 100).toFixed(x < 0.01 ? 2 : 1)} %`)

function verdict(margin: number) {
  if (margin > 3) return { text: 'error-free, healthy margin', cls: 'text-emerald-300' }
  if (margin > 0) return { text: 'still error-free, but close to the cliff: drain NOW', cls: 'text-amber-300' }
  if (margin > -1.5) return { text: 'past the cliff: packets are being lost', cls: 'text-red-300' }
  return { text: 'unusable: BFD will tear the link down', cls: 'text-red-400' }
}

/** OSNR -> packet loss, from the same model the optical agent runs (optics/model.py, plan §6.1). */
export default function OpticalCliff() {
  const [d, setD] = useState<OpticsData | null>(null)
  const [extra, setExtra] = useState(0)

  useEffect(() => {
    fetch(`${import.meta.env.BASE_URL}data/optics.json`)
      .then((r) => r.json())
      .then(setD)
      .catch(() => setD(null))
  }, [])

  const step = useMemo(() => {
    if (!d) return null
    return d.ramp_F12_span0.reduce((best, s) => (Math.abs(s.extra_db - extra) < Math.abs(best.extra_db - extra) ? s : best))
  }, [d, extra])

  const curve: EChartsOption | null = useMemo(() => {
    if (!d || !step) return null
    return {
      backgroundColor: 'transparent',
      animation: false,
      title: { text: 'Packet loss ratio vs OSNR (log scale; green = links as built)', left: 8, top: 4, textStyle: { fontSize: 12, color: '#cbd5e1' } },
      grid: { left: 60, right: 20, top: 48, bottom: 40 },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'value', name: 'OSNR (dB, 0.1 nm)', nameLocation: 'middle', nameGap: 26, min: 5, max: 43 },
      yAxis: { type: 'log', min: FLOOR, max: 1, axisLabel: { formatter: (v: number) => v.toExponential(0) } },
      series: [
        {
          type: 'line',
          name: 'model',
          showSymbol: false,
          color: '#38bdf8',
          data: d.curve.map((c) => [c.osnr_db, Math.max(c.plr, FLOOR)]),
          markLine: {
            symbol: 'none',
            data: [
              { xAxis: d.required_osnr_db, label: { formatter: `margin 0 (PLR 1e-6): ${d.required_osnr_db} dB`, color: '#f87171' }, lineStyle: { color: '#f87171', type: 'dashed' } },
            ],
          },
          markArea: { silent: true, itemStyle: { color: 'rgba(239,68,68,0.08)' }, data: [[{ xAxis: 5 }, { xAxis: d.required_osnr_db }]] },
        },
        {
          type: 'scatter',
          name: 'links as built',
          color: '#22c55e',
          symbolSize: 9,
          label: { show: true, formatter: (p: { data: unknown }) => String((p.data as [number, number, string])[2]), position: 'top', color: '#a7f3d0', fontSize: 10 },
          data: Object.entries(d.links).map(([l, v]) => [v.osnr_db, FLOOR, l]),
        },
        {
          type: 'scatter',
          name: 'L12 now (slider)',
          color: '#f59e0b',
          symbolSize: 14,
          data: [[step.L12.osnr_db, Math.max(step.L12.plr, FLOOR)]],
        },
      ],
    } as EChartsOption
  }, [d, step])

  const ramp: EChartsOption | null = useMemo(() => {
    if (!d) return null
    return {
      backgroundColor: 'transparent',
      animation: false,
      title: { text: 'Fibre F12 degrading: OSNR margin of its two links', left: 8, top: 4, textStyle: { fontSize: 12, color: '#cbd5e1' } },
      grid: { left: 56, right: 20, top: 48, bottom: 40 },
      tooltip: { trigger: 'axis' },
      legend: { top: 24, right: 10, textStyle: { fontSize: 10 } },
      xAxis: { type: 'value', name: 'extra loss inside span 0 of F12 (dB)', nameLocation: 'middle', nameGap: 26, max: 32 },
      yAxis: { type: 'value', axisLabel: { formatter: '{value} dB' } },
      series: [
        { type: 'line', name: 'L12', showSymbol: false, color: '#38bdf8', data: d.ramp_F12_span0.map((s) => [s.extra_db, s.L12.margin_db]),
          markLine: { symbol: 'none', data: [
            { yAxis: 3, lineStyle: { color: '#f59e0b', type: 'dashed' }, label: { formatter: 'warning', position: 'insideStartTop', color: '#f59e0b' } },
            { yAxis: 0, lineStyle: { color: '#ef4444', type: 'dashed' }, label: { formatter: 'cliff', position: 'insideStartTop', color: '#ef4444' } },
            { xAxis: extra, lineStyle: { color: '#f8fafc' }, label: { show: false } }] } },
        { type: 'line', name: 'L13 (chord, also on F12)', showSymbol: false, color: '#c084fc', data: d.ramp_F12_span0.map((s) => [s.extra_db, s.L13.margin_db]) },
      ],
    } as EChartsOption
  }, [d, extra])

  if (!d || !step || !curve || !ramp) return <p className="text-sm text-slate-400">Loading optical model…</p>
  const v = verdict(step.L12.margin_db)

  return (
    <div className="space-y-3" data-testid="optical-cliff">
      <section className="rounded-lg border border-slate-800 bg-slate-900/60 p-3">
        <label className="flex flex-wrap items-center gap-3 text-sm">
          <span>Extra loss inside span 0 of fibre F12:</span>
          <input type="range" min={0} max={32} step={0.1} value={extra} onChange={(e) => setExtra(Number(e.target.value))}
                 className="min-w-48 flex-1 accent-amber-400" aria-label="Extra span loss" />
          <span className="w-16 font-mono">{extra.toFixed(1)} dB</span>
        </label>
        <div className="mt-2 grid gap-2 text-xs sm:grid-cols-4">
          <Stat k="L12 OSNR" v={`${step.L12.osnr_db.toFixed(1)} dB`} />
          <Stat k="L12 margin" v={`${step.L12.margin_db.toFixed(1)} dB`} />
          <Stat k="L12 packet loss" v={fmt(step.L12.plr)} />
          <Stat k="L13 packet loss" v={fmt(step.L13.plr)} />
        </div>
        <p className={`mt-2 text-sm font-semibold ${v.cls}`} data-testid="cliff-verdict">{v.text}</p>
      </section>
      <div className="grid gap-3 lg:grid-cols-2">
        <Chart option={curve} className="h-80 rounded-lg border border-slate-800 bg-slate-900/60" />
        <Chart option={ramp} className="h-80 rounded-lg border border-slate-800 bg-slate-900/60" />
      </div>
      <p className="text-xs text-slate-400">
        Chain (plan §6.1): OSNR per amplified span → 1/OSNR<sub>tot</sub> = Σ 1/OSNR<sub>i</sub> → Q (NRZ-OOK, ASE-limited, {d.params.penalty_db} dB
        implementation penalty) → BER = ½erfc(Q/√2) → RS(255,239) codeword failure → packet loss (1500 B ≈ 7 codewords). A first-order,
        labelled approximation: no nonlinearities, dispersion or PMD. The cliff is ~2 dB wide, which is why FiberTwin acts on the margin trend,
        not on packet loss. Generated by {d.generated_by}.
      </p>
    </div>
  )
}

function Stat({ k, v }: { k: string; v: string }) {
  return (
    <div className="rounded bg-slate-800/70 px-2 py-1">
      <div className="text-slate-400">{k}</div>
      <div className="font-mono text-sm text-slate-100">{v}</div>
    </div>
  )
}
