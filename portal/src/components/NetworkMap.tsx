import cytoscape from 'cytoscape'
import type { Core, ElementDefinition, StylesheetJson } from 'cytoscape'
import { useEffect, useRef } from 'react'
import { HEALTH_COLOUR } from '../types'
import type { Snapshot, Topology } from '../types'

export type Selection = { kind: 'fibre' | 'link' | 'node' | 'access'; id: string } | null
export interface LayerToggles {
  fibres: boolean
  ip: boolean
  access: boolean
}

interface Props {
  topology: Topology
  snapshot: Snapshot
  layers: LayerToggles
  selection: Selection
  onSelect: (s: Selection) => void
}

const FIBRE = 'fib-'
const IP = 'ip-'
const ACC = 'acc-'

function buildElements(t: Topology): ElementDefinition[] {
  const els: ElementDefinition[] = []
  for (const n of t.nodes) {
    els.push({ group: 'nodes', data: { id: n.id, label: n.label, role: n.role }, position: n.pos ?? { x: 0, y: 0 }, classes: n.role })
  }
  for (const f of t.fibres) {
    els.push({ group: 'edges', data: { id: FIBRE + f.id, source: f.a, target: f.b, label: f.id, colour: '#38bdf8' }, classes: 'fibre' })
  }
  for (const l of t.ip_links) {
    els.push({ group: 'edges', data: { id: IP + l.id, source: l.a, target: l.b, label: l.id, colour: HEALTH_COLOUR.ok }, classes: 'iplink' })
  }
  for (const a of t.access_links ?? []) {
    els.push({ group: 'edges', data: { id: ACC + a.id, source: a.a, target: a.b, label: '' }, classes: `access ${a.segment}` })
  }
  // upstream peering is an IP link without a fibre in this contract version: draw it as access-style
  return els
}

const style: StylesheetJson = [
  {
    selector: 'node',
    style: {
      label: 'data(label)',
      color: '#e2e8f0',
      'font-size': 13,
      'font-weight': 600,
      'text-valign': 'center',
      'text-halign': 'center',
      'background-color': '#1e293b',
      'border-width': 2,
      'border-color': '#38bdf8',
      width: 64,
      height: 40,
      shape: 'round-rectangle',
    },
  },
  { selector: 'node.upstream', style: { shape: 'diamond', width: 70, height: 50, 'border-color': '#a78bfa', 'font-size': 10 } },
  { selector: 'node.olt', style: { shape: 'hexagon', 'border-color': '#f59e0b' } },
  { selector: 'node.splitter', style: { shape: 'triangle', width: 46, height: 40, 'border-color': '#f59e0b', 'font-size': 9, 'text-valign': 'bottom', 'text-margin-y': 6 } },
  { selector: 'node.ont', style: { shape: 'ellipse', width: 54, height: 30, 'border-color': '#f59e0b', 'font-size': 10 } },
  {
    selector: 'edge',
    style: { 'curve-style': 'straight', label: 'data(label)', color: '#cbd5e1', 'font-size': 10, 'text-rotation': 'autorotate' },
  },
  {
    selector: 'edge.fibre',
    style: {
      width: 22,
      'line-color': 'data(colour)',
      opacity: 0.22,
      'line-cap': 'round',
      'text-margin-y': -18,
      color: '#7dd3fc',
      'font-weight': 700,
      'text-opacity': 1,
    },
  },
  {
    selector: 'edge.iplink',
    style: {
      width: 5,
      'line-color': 'data(colour)',
      'text-background-color': '#0b1220',
      'text-background-opacity': 0.85,
      'text-background-padding': '2px',
    },
  },
  { selector: 'edge.drained', style: { 'line-style': 'dashed', 'line-dash-pattern': [8, 6] } },
  { selector: 'edge.access', style: { width: 2, 'line-color': '#94a3b8', label: '' } },
  { selector: 'edge.feeder', style: { width: 4 } },
  { selector: 'edge.uplink', style: { width: 4, 'line-color': '#475569' } },
  {
    selector: 'edge.onpath',
    style: { width: 7, 'line-outline-width': 3, 'line-outline-color': '#f8fafc' },
  },
  { selector: 'edge.fibre.srlg', style: { opacity: 0.55, 'line-color': '#fde047' } },
  { selector: 'edge.iplink.srlg', style: { 'underlay-color': '#fde047', 'underlay-padding': 7, 'underlay-opacity': 0.55 } },
  { selector: '.picked', style: { 'overlay-color': '#fde047', 'overlay-opacity': 0.18, 'overlay-padding': 6 } },
  { selector: '.hidden', style: { display: 'none' } },
]

export default function NetworkMap({ topology, snapshot, layers, selection, onSelect }: Props) {
  const box = useRef<HTMLDivElement>(null)
  const cyRef = useRef<Core | null>(null)
  const onSelectRef = useRef(onSelect)
  onSelectRef.current = onSelect

  // create the graph once per topology
  useEffect(() => {
    if (!box.current) return
    const cy = cytoscape({
      container: box.current,
      elements: buildElements(topology),
      style,
      layout: { name: 'preset', padding: 30 },
      minZoom: 0.3,
      maxZoom: 3,
      wheelSensitivity: 0.3,
      boxSelectionEnabled: false,
      autoungrabify: true,
    })
    cy.on('tap', 'edge, node', (e) => {
      const id: string = e.target.id()
      if (id.startsWith(FIBRE)) onSelectRef.current({ kind: 'fibre', id: id.slice(FIBRE.length) })
      else if (id.startsWith(IP)) onSelectRef.current({ kind: 'link', id: id.slice(IP.length) })
      else if (id.startsWith(ACC)) onSelectRef.current({ kind: 'access', id: id.slice(ACC.length) })
      else onSelectRef.current({ kind: 'node', id })
    })
    cy.on('tap', (e) => {
      if (e.target === cy) onSelectRef.current(null)
    })
    const ro = new ResizeObserver(() => {
      cy.resize()
      cy.fit(undefined, 30)
    })
    ro.observe(box.current)
    cyRef.current = cy
    return () => {
      ro.disconnect()
      cy.destroy()
      cyRef.current = null
    }
  }, [topology])

  // repaint on every frame / selection / layer change
  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    cy.batch(() => {
      for (const l of topology.ip_links) {
        const s = snapshot.links[l.id]
        const e = cy.getElementById(IP + l.id)
        if (!s) continue
        e.data('colour', HEALTH_COLOUR[s.health])
        const margin = s.osnr_margin_db !== undefined ? ` · ${s.osnr_margin_db.toFixed(1)} dB` : ''
        e.data('label', `${l.id}${margin}`)
        e.toggleClass('drained', s.health === 'drained')
      }
      for (const f of topology.fibres) {
        const s = snapshot.fibres?.[f.id]
        const e = cy.getElementById(FIBRE + f.id)
        e.data('colour', s && s.health !== 'ok' ? HEALTH_COLOUR[s.health] : '#38bdf8')
      }

      // traffic paths
      cy.edges().removeClass('onpath')
      for (const hops of Object.values(snapshot.paths ?? {})) {
        for (let i = 0; i < hops.length - 1; i++) {
          const [a, b] = [hops[i], hops[i + 1]]
          const between = cy.edges(`.iplink, .access`).filter((e) => {
            const s = e.source().id(), t = e.target().id()
            return ((s === a && t === b) || (s === b && t === a)) && !e.hasClass('drained')
          })
          between.first().addClass('onpath')
        }
      }

      // SRLG highlight for the current selection
      cy.elements().removeClass('srlg picked')
      if (selection?.kind === 'fibre') {
        cy.getElementById(FIBRE + selection.id).addClass('srlg picked')
        for (const l of topology.ip_links) if (l.srlgs.includes(selection.id)) cy.getElementById(IP + l.id).addClass('srlg')
      } else if (selection?.kind === 'link') {
        cy.getElementById(IP + selection.id).addClass('picked')
        const l = topology.ip_links.find((x) => x.id === selection.id)
        for (const f of l?.srlgs ?? []) cy.getElementById(FIBRE + f).addClass('srlg')
      } else if (selection) {
        cy.getElementById(selection.kind === 'access' ? ACC + selection.id : selection.id).addClass('picked')
      }

      cy.edges('.fibre').toggleClass('hidden', !layers.fibres)
      cy.edges('.iplink').toggleClass('hidden', !layers.ip)
      cy.edges('.access').toggleClass('hidden', !layers.access)
      cy.nodes('.olt, .splitter, .ont').toggleClass('hidden', !layers.access)
    })
  }, [topology, snapshot, selection, layers])

  return <div ref={box} className="h-full w-full" data-testid="network-map" />
}
