import { useEffect, useMemo, useState } from 'react'
import type { Health, Snapshot, Topology } from '../types'
import NetworkMap from './NetworkMap'
import type { LayerToggles, Selection } from './NetworkMap'

interface Scenario {
  id: string
  kind: 'cut' | 'drain'
  fibre: string | null
  links: string[]
  ok: boolean
  reasons: string[]
  max_util_pct: number
  per_link: Record<string, { util_pct: number; state: 'up' | 'down' | 'drained' }>
  paths: Record<string, string[]>
}
interface WhatIfData {
  generated_by: string
  limit_pct: number
  demands: { src: string; dst: string; mbps: number }[]
  srlg: Record<string, string[]>
  scenarios: Scenario[]
}

const LAYERS: LayerToggles = { fibres: true, ip: true, access: false }

/** Cut any set of fibres (or drain one) and see what the what-if engine decides. Data precomputed by
 *  analysis/make_portal_data.py with netgen.whatif, the same code CI and the analysis API run. */
export default function WhatIf({ topology }: { topology: Topology }) {
  const [data, setData] = useState<WhatIfData | null>(null)
  const [mode, setMode] = useState<'cut' | 'drain'>('cut')
  const [picked, setPicked] = useState<string[]>([])

  useEffect(() => {
    fetch(`${import.meta.env.BASE_URL}data/whatif.json`)
      .then((r) => r.json())
      .then(setData)
      .catch(() => setData(null))
  }, [])

  const sc = useMemo(() => {
    if (!data) return null
    if (!picked.length) return data.scenarios.find((s) => s.id === 'baseline') ?? null
    const id = mode === 'cut' ? `cut-${[...picked].sort().join('+')}` : `drain-${picked[0]}`
    return data.scenarios.find((s) => s.id === id) ?? null
  }, [data, picked, mode])

  const snapshot: Snapshot | null = useMemo(() => {
    if (!sc || !data) return null
    const links: Snapshot['links'] = {}
    for (const l of topology.ip_links) {
      const p = sc.per_link[l.id]
      let health: Health = 'ok'
      if (p?.state === 'down') health = 'down'
      else if (p?.state === 'drained') health = 'drained'
      else if (p && p.util_pct > data.limit_pct) health = 'crit'
      else if (p && p.util_pct > data.limit_pct * 0.75) health = 'warn'
      links[l.id] = { health, util_pct: p?.util_pct ?? 0 }
    }
    const fibres: Snapshot['fibres'] = {}
    for (const f of topology.fibres) fibres[f.id] = { health: picked.includes(f.id) ? (mode === 'cut' ? 'down' : 'warn') : 'ok' }
    return { ts: '', links, fibres, paths: sc.paths }
  }, [sc, data, topology, picked, mode])

  const onSelect = (s: Selection) => {
    if (s?.kind !== 'fibre') return
    if (mode === 'drain') setPicked([s.id])
    else setPicked((p) => (p.includes(s.id) ? p.filter((x) => x !== s.id) : [...p, s.id]))
  }

  if (!data || !snapshot || !sc) return <p className="text-sm text-slate-400">Loading what-if data…</p>

  return (
    <div className="grid gap-3 lg:grid-cols-[1fr_380px]" data-testid="whatif">
      <section className="relative h-[460px] overflow-hidden rounded-lg border border-slate-800 bg-slate-950">
        <NetworkMap topology={topology} snapshot={snapshot} layers={LAYERS} selection={null} onSelect={onSelect} />
        <div className="absolute left-2 top-2 rounded-md bg-slate-900/85 px-2 py-1 text-xs text-slate-200">
          Click {mode === 'cut' ? 'fibres to cut them (several = multiple failure)' : 'one fibre to drain its links'}
        </div>
      </section>
      <aside className="flex flex-col gap-3">
        <div className="flex gap-1">
          {(['cut', 'drain'] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => {
                setMode(m)
                setPicked([])
              }}
              className={`flex-1 rounded-md px-3 py-1.5 text-sm ${mode === m ? 'bg-sky-500 text-slate-950' : 'bg-slate-800'}`}
            >
              {m === 'cut' ? 'Cut fibres' : 'Drain a fibre'}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap gap-1">
          {Object.keys(data.srlg).sort().map((f) => (
            <button
              key={f}
              type="button"
              data-testid={`fibre-${f}`}
              onClick={() => onSelect({ kind: 'fibre', id: f })}
              className={`rounded px-2 py-1 font-mono text-xs ${picked.includes(f) ? 'bg-red-500 text-white' : 'bg-slate-800 text-slate-200'}`}
              title={`carries ${data.srlg[f].join(', ')}`}
            >
              {f}
            </button>
          ))}
          {picked.length > 0 && (
            <button type="button" onClick={() => setPicked([])} className="rounded bg-slate-700 px-2 py-1 text-xs">
              reset
            </button>
          )}
        </div>
        <section
          className={`rounded-lg border p-3 ${sc.ok ? 'border-emerald-500/50 bg-emerald-500/10' : 'border-red-500/50 bg-red-500/10'}`}
          data-testid="verdict"
        >
          <div className="text-sm font-semibold">
            {picked.length === 0 ? 'Normal network' : sc.ok ? '✓ Safe: every demand still served' : '✗ Not safe'}
          </div>
          {picked.length > 0 && (
            <div className="mt-1 text-xs text-slate-300">
              {mode === 'cut' ? 'Cut' : 'Drain'} {picked.join(' + ')} → links {sc.links.join(', ')} {mode === 'cut' ? 'down' : 'drained'} (shared-risk group)
            </div>
          )}
          <ul className="mt-1 list-disc pl-4 text-xs text-slate-200">
            {sc.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </section>
        <section className="rounded-lg border border-slate-800 bg-slate-900/60 p-3">
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">Link utilisation after</h3>
          {topology.ip_links.map((l) => {
            const p = sc.per_link[l.id]
            const u = p?.util_pct ?? 0
            const col = p?.state !== 'up' ? '#64748b' : u > data.limit_pct ? '#ef4444' : u > data.limit_pct * 0.75 ? '#f59e0b' : '#22c55e'
            return (
              <div key={l.id} className="mb-1 flex items-center gap-2 text-xs">
                <span className="w-9 font-mono">{l.id}</span>
                <div className="relative h-2 flex-1 rounded bg-slate-800">
                  <div className="h-2 rounded" style={{ width: `${Math.min(100, u)}%`, background: col }} />
                  <div className="absolute top-[-2px] h-3 w-px bg-red-400" style={{ left: `${data.limit_pct}%` }} />
                </div>
                <span className="w-16 text-right font-mono">{p?.state === 'up' ? `${u.toFixed(0)}%` : p?.state}</span>
              </div>
            )
          })}
        </section>
        <section className="rounded-lg border border-slate-800 bg-slate-900/60 p-3 text-xs">
          <h3 className="mb-1 font-semibold uppercase tracking-wide text-slate-400">Paths (OSPF shortest)</h3>
          {Object.entries(sc.paths).map(([d, p]) => (
            <div key={d} className="font-mono">
              {d}: {p.length ? p.join(' → ') : <span className="text-red-400">unreachable</span>}
            </div>
          ))}
        </section>
        <p className="text-[11px] text-slate-500">
          Same engine as CI and the analysis API ({data.generated_by}). Demands:{' '}
          {data.demands.map((d) => `${d.src}→${d.dst} ${d.mbps} Mb/s`).join(', ')}.
        </p>
      </aside>
    </div>
  )
}
