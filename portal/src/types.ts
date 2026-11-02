// TypeScript mirror of contracts/*.schema.json (data contract v0).
// If a schema changes, change this file in the same PR.

export type Health = 'ok' | 'warn' | 'crit' | 'down' | 'drained'
export type Role = 'core' | 'edge' | 'upstream' | 'olt' | 'splitter' | 'ont'

export interface Node {
  id: string
  role: Role
  label: string
  site?: string
  loopback?: string
  pos?: { x: number; y: number }
}

export interface Fibre {
  id: string
  a: string
  b: string
  length_km: number
  spans: number
}

export interface Lightpath {
  id: string
  fibres: string[]
  wavelength: number
  ip_link: string
}

export interface IpLink {
  id: string
  a: string
  a_if: string
  b: string
  b_if: string
  capacity_mbps: number
  ospf_cost: number
  srlgs: string[]
}

export interface AccessLink {
  id: string
  a: string
  b: string
  segment: 'uplink' | 'feeder' | 'drop'
  length_km?: number
}

export interface Topology {
  contract_version: '0.1'
  nodes: Node[]
  fibres: Fibre[]
  lightpaths: Lightpath[]
  ip_links: IpLink[]
  access_links?: AccessLink[]
}

export interface LinkState {
  health: Health
  util_pct?: number
  loss_pct?: number
  latency_ms?: number
  osnr_margin_db?: number
  ospf_cost?: number
}

export interface FibreState {
  health: Health
  extra_loss_db?: number
}

export interface OntState {
  health: Health
  rx_down_dbm?: number
  rx_up_dbm?: number
  margin_db?: number
}

export interface Snapshot {
  ts: string
  links: Record<string, LinkState>
  fibres?: Record<string, FibreState>
  onts?: Record<string, OntState>
  paths?: Record<string, string[]>
}

export type EventType =
  | 'detected' | 'located' | 'what_if_passed' | 'what_if_failed' | 'pr_opened'
  | 'ci_passed' | 'ci_failed' | 'approved' | 'deployed' | 'verified'
  | 'revert_opened' | 'work_order' | 'ticket' | 'closed'

export interface IncidentEvent {
  ts: string
  incident_id: string
  type: EventType
  fault_class: 'F1' | 'F1b' | 'F2' | 'F3' | 'F4' | 'F5' | 'F6'
  element?: string
  summary: string
  url?: string
}

export interface ReplayBundle {
  meta: { scenario: string; source: 'recorded' | 'synthetic'; recorded_at?: string; notes?: string }
  topology: Topology
  frames: Snapshot[]
  events: IncidentEvent[]
}

export const HEALTH_COLOUR: Record<Health, string> = {
  ok: '#22c55e',
  warn: '#f59e0b',
  crit: '#ef4444',
  down: '#991b1b',
  drained: '#64748b',
}
