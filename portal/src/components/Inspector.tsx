import type { ReactNode } from 'react'
import { HEALTH_COLOUR } from '../types'
import type { Snapshot, Topology } from '../types'
import type { Selection } from './NetworkMap'

function Row({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="flex justify-between gap-4 text-xs">
      <span className="text-slate-400">{k}</span>
      <span className="font-mono text-slate-100">{v}</span>
    </div>
  )
}

function Badge({ health }: { health: keyof typeof HEALTH_COLOUR }) {
  return (
    <span className="rounded px-1.5 py-0.5 text-[10px] font-bold uppercase text-slate-950" style={{ background: HEALTH_COLOUR[health] }}>
      {health}
    </span>
  )
}

/** Details panel for whatever is clicked on the map. */
export default function Inspector({ selection, topology, snapshot }: { selection: Selection; topology: Topology; snapshot: Snapshot }) {
  if (!selection) {
    return <p className="text-xs text-slate-400">Click a fibre, an IP link or a router on the map.</p>
  }

  if (selection.kind === 'fibre') {
    const f = topology.fibres.find((x) => x.id === selection.id)
    if (!f) return null
    const riders = topology.ip_links.filter((l) => l.srlgs.includes(f.id))
    const s = snapshot.fibres?.[f.id]
    return (
      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold">Fibre {f.id}</h3>
          {s && <Badge health={s.health} />}
        </div>
        <Row k="Between" v={`${f.a} ↔ ${f.b}`} />
        <Row k="Length" v={`${f.length_km} km`} />
        <Row k="Amplified spans" v={f.spans} />
        <Row k="Extra loss (modelled)" v={`${(s?.extra_loss_db ?? 0).toFixed(2)} dB`} />
        <div className="pt-1 text-xs text-slate-300">
          Carries <b>{riders.length}</b> IP link{riders.length === 1 ? '' : 's'}: {riders.map((r) => r.id).join(', ')}
          {riders.length > 1 && <span className="block text-amber-300">Shared-risk group (SRLG): these links fail together.</span>}
        </div>
      </div>
    )
  }

  if (selection.kind === 'link') {
    const l = topology.ip_links.find((x) => x.id === selection.id)
    if (!l) return null
    const s = snapshot.links[l.id]
    const lps = topology.lightpaths.filter((p) => p.ip_link === l.id)
    return (
      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold">IP link {l.id}</h3>
          {s && <Badge health={s.health} />}
        </div>
        <Row k="Endpoints" v={`${l.a}:${l.a_if} ↔ ${l.b}:${l.b_if}`} />
        <Row k="Capacity (scaled)" v={`${l.capacity_mbps} Mb/s`} />
        <Row k="OSPF cost" v={s?.ospf_cost ?? l.ospf_cost} />
        <Row k="OSNR margin" v={s?.osnr_margin_db !== undefined ? `${s.osnr_margin_db.toFixed(2)} dB` : '–'} />
        <Row k="Utilisation" v={s?.util_pct !== undefined ? `${s.util_pct}%` : '–'} />
        <Row k="Packet loss" v={s?.loss_pct !== undefined ? `${s.loss_pct}%` : '–'} />
        <Row k="Latency" v={s?.latency_ms !== undefined ? `${s.latency_ms} ms` : '–'} />
        <Row k="Rides fibres" v={l.srlgs.join(' + ') || '–'} />
        <Row k="Lightpath" v={lps.map((p) => `${p.id} (λ${p.wavelength})`).join(', ') || '–'} />
      </div>
    )
  }

  const n = topology.nodes.find((x) => x.id === selection.id)
  if (selection.kind === 'node' && n) {
    return (
      <div className="space-y-1.5">
        <h3 className="font-semibold">{n.label}</h3>
        <Row k="Role" v={n.role} />
        {n.site && <Row k="Site" v={n.site} />}
        {n.loopback && <Row k="Loopback" v={n.loopback} />}
      </div>
    )
  }

  const a = topology.access_links?.find((x) => x.id === selection.id)
  if (!a) return null
  return (
    <div className="space-y-1.5">
      <h3 className="font-semibold">Access segment {a.id}</h3>
      <Row k="Type" v={a.segment} />
      <Row k="Between" v={`${a.a} → ${a.b}`} />
      {a.length_km !== undefined && <Row k="Length" v={`${a.length_km} km`} />}
    </div>
  )
}
