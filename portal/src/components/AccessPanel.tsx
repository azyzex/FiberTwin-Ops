import { HEALTH_COLOUR } from '../types'
import type { Snapshot, Topology } from '../types'

const SENSITIVITY_DBM = -27 // ONT receiver sensitivity used by the budget model (plan §6.2)

/** Per-ONT optical budget: received power and margin to sensitivity, per direction. */
export default function AccessPanel({ topology, snapshot }: { topology: Topology; snapshot: Snapshot }) {
  const onts = topology.nodes.filter((n) => n.role === 'ont')
  return (
    <div className="space-y-3" data-testid="access-panel">
      <p className="text-sm text-slate-300">
        Modelled GPON-style budget for each ONT. A margin falling on <b>all</b> ONTs points at the feeder; on <b>one</b> ONT, at its drop.
      </p>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {onts.map((o) => {
          const s = snapshot.onts?.[o.id]
          const margin = s?.margin_db ?? 0
          const pct = Math.max(0, Math.min(100, (margin / 10) * 100))
          const colour = s ? HEALTH_COLOUR[s.health] : '#475569'
          return (
            <div key={o.id} className="rounded-lg border border-slate-800 bg-slate-900/60 p-3">
              <div className="flex items-center justify-between">
                <span className="font-semibold">{o.label}</span>
                <span className="text-xs uppercase" style={{ color: colour }}>
                  {s?.health ?? 'n/a'}
                </span>
              </div>
              <div className="mt-2 text-xs text-slate-400">Margin to sensitivity ({SENSITIVITY_DBM} dBm)</div>
              <div className="mt-1 h-2 rounded bg-slate-800">
                <div className="h-2 rounded" style={{ width: `${pct}%`, background: colour }} />
              </div>
              <div className="mt-1 font-mono text-sm">{margin.toFixed(1)} dB</div>
              <div className="mt-2 grid grid-cols-2 gap-1 font-mono text-[11px] text-slate-300">
                <span>↓ {s?.rx_down_dbm?.toFixed(1) ?? '–'} dBm</span>
                <span>↑ {s?.rx_up_dbm?.toFixed(1) ?? '–'} dBm</span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
