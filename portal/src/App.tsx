import { useMemo, useState } from 'react'
import AccessPanel from './components/AccessPanel'
import Inspector from './components/Inspector'
import LinkCharts from './components/LinkCharts'
import NetworkMap from './components/NetworkMap'
import type { LayerToggles, Selection } from './components/NetworkMap'
import Timeline from './components/Timeline'
import { useReplay } from './useReplay'

type Tab = 'map' | 'access' | 'whatif' | 'optical'

const TABS: { id: Tab; label: string; ready: boolean }[] = [
  { id: 'map', label: 'Network map', ready: true },
  { id: 'access', label: 'Access', ready: true },
  { id: 'whatif', label: 'What-if', ready: false },
  { id: 'optical', label: 'Optical cliff', ready: false },
]

// ?replay=<name> picks a bundle in public/replay/; default = the recorded golden-path run
const REPLAY_NAME = new URLSearchParams(window.location.search).get('replay') ?? 'f1-recorded'
const REPLAY_URL = `${import.meta.env.BASE_URL}replay/${REPLAY_NAME.replace(/[^a-z0-9-]/gi, '')}.json`

export default function App() {
  const r = useReplay(REPLAY_URL)
  const [tab, setTab] = useState<Tab>('map')
  const [selection, setSelection] = useState<Selection>({ kind: 'fibre', id: 'F12' })
  const [layers, setLayers] = useState<LayerToggles>({ fibres: true, ip: true, access: true })

  const snapshot = r.bundle?.frames[r.frame]
  const jumpTo = useMemo(
    () => (ts: string) => {
      if (!r.bundle) return
      const i = r.bundle.frames.findIndex((f) => Date.parse(f.ts) >= Date.parse(ts))
      r.setFrame(i < 0 ? r.last : i)
    },
    [r],
  )

  if (r.error) return <p className="p-6 text-red-400">Could not load the replay: {r.error}</p>
  if (!r.bundle || !snapshot) return <p className="p-6 text-slate-400">Loading replay…</p>

  const { bundle } = r
  const start = bundle.frames[0].ts
  const elapsed = Math.round((Date.parse(snapshot.ts) - Date.parse(start)) / 1000)

  return (
    <div className="mx-auto flex min-h-full max-w-7xl flex-col gap-3 p-3 sm:p-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight">
            FiberTwin <span className="text-sky-400">Portal</span>
          </h1>
          <p className="text-xs text-slate-400">{bundle.meta.scenario}</p>
        </div>
        <nav className="flex flex-wrap gap-1" aria-label="Screens">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              disabled={!t.ready}
              onClick={() => setTab(t.id)}
              className={`rounded-md px-3 py-1.5 text-sm ${
                tab === t.id ? 'bg-sky-500 text-slate-950' : 'bg-slate-800 text-slate-200 hover:bg-slate-700'
              } disabled:cursor-not-allowed disabled:opacity-40`}
              title={t.ready ? '' : 'Needs the analysis API (Slice 1e)'}
            >
              {t.label}
            </button>
          ))}
        </nav>
      </header>

      {bundle.meta.source === 'recorded' && (
        <div className="rounded-md border border-emerald-500/40 bg-emerald-500/10 px-3 py-1.5 text-xs text-emerald-200" data-testid="recorded-banner">
          Replay of a recorded run of the twin{bundle.meta.recorded_at ? ` (${bundle.meta.recorded_at.slice(0, 10)})` : ''}. {bundle.meta.notes}
        </div>
      )}

      {bundle.meta.source === 'synthetic' && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-1.5 text-xs text-amber-200" data-testid="synthetic-banner">
          Synthetic development data: shows how the portal works, not a measurement. Recorded runs of the twin replace it.
        </div>
      )}

      {/* replay controls */}
      <section className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2">
        <button type="button" onClick={r.toggle} className="rounded-md bg-sky-500 px-3 py-1 text-sm font-semibold text-slate-950" data-testid="play">
          {r.playing ? '❚❚ Pause' : '▶ Play'}
        </button>
        <button type="button" onClick={r.cycleSpeed} className="rounded-md bg-slate-800 px-2 py-1 font-mono text-xs">
          ×{r.speed}
        </button>
        <input
          type="range"
          min={0}
          max={r.last}
          value={r.frame}
          onChange={(e) => r.setFrame(Number(e.target.value))}
          className="min-w-40 flex-1 accent-sky-400"
          aria-label="Replay position"
        />
        <span className="font-mono text-xs text-slate-300">
          {snapshot.ts.slice(11, 19)} UTC · T+{Math.floor(elapsed / 60)}m{String(elapsed % 60).padStart(2, '0')}s
        </span>
      </section>

      {tab === 'map' && (
        <>
          <div className="grid gap-3 lg:grid-cols-[1fr_340px]">
            <section className="relative h-[420px] overflow-hidden rounded-lg border border-slate-800 bg-slate-950 sm:h-[480px]">
              <NetworkMap topology={bundle.topology} snapshot={snapshot} layers={layers} selection={selection} onSelect={setSelection} />
              <div className="absolute left-2 top-2 flex flex-wrap gap-2 rounded-md bg-slate-900/85 px-2 py-1 text-xs">
                {(
                  [
                    ['fibres', 'Fibres (L0)'],
                    ['ip', 'IP links (L3)'],
                    ['access', 'Access'],
                  ] as const
                ).map(([k, label]) => (
                  <label key={k} className="flex items-center gap-1">
                    <input type="checkbox" checked={layers[k]} onChange={(e) => setLayers({ ...layers, [k]: e.target.checked })} />
                    {label}
                  </label>
                ))}
              </div>
              <div className="absolute bottom-2 left-2 flex flex-wrap gap-2 rounded-md bg-slate-900/85 px-2 py-1 text-[11px]">
                <Legend colour="#22c55e" text="ok" />
                <Legend colour="#f59e0b" text="warning" />
                <Legend colour="#ef4444" text="critical" />
                <Legend colour="#64748b" text="drained" dashed />
                <Legend colour="#f8fafc" text="traffic path" glow />
              </div>
            </section>
            <aside className="flex flex-col gap-3">
              <section className="rounded-lg border border-slate-800 bg-slate-900/60 p-3" data-testid="inspector">
                <Inspector selection={selection} topology={bundle.topology} snapshot={snapshot} />
              </section>
              <section className="rounded-lg border border-slate-800 bg-slate-900/60 p-2">
                <h2 className="px-2 pb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">Incident timeline</h2>
                <Timeline events={bundle.events} now={snapshot.ts} start={start} onJump={jumpTo} />
              </section>
            </aside>
          </div>
          <LinkCharts bundle={bundle} frame={r.frame} />
        </>
      )}

      {tab === 'access' && <AccessPanel topology={bundle.topology} snapshot={snapshot} />}

      <footer className="pt-2 text-center text-[11px] text-slate-500">
        Read-only view. Every network change goes through a pull request, CI and human approval.
      </footer>
    </div>
  )
}

function Legend({ colour, text, dashed, glow }: { colour: string; text: string; dashed?: boolean; glow?: boolean }) {
  return (
    <span className="flex items-center gap-1">
      <span
        className="inline-block h-0 w-5"
        style={{ borderTop: `3px ${dashed ? 'dashed' : 'solid'} ${colour}`, boxShadow: glow ? `0 0 6px 2px ${colour}55` : undefined }}
      />
      {text}
    </span>
  )
}
