import type { EventType, IncidentEvent } from '../types'

const ICON: Record<EventType, string> = {
  detected: '⚠',
  located: '⌖',
  what_if_passed: '✓',
  what_if_failed: '✗',
  pr_opened: '⇄',
  ci_passed: '✓',
  ci_failed: '✗',
  approved: '👤',
  deployed: '⇪',
  verified: '★',
  revert_opened: '↶',
  work_order: '🛠',
  ticket: '🎫',
  closed: '■',
}

const LABEL: Record<EventType, string> = {
  detected: 'Detected',
  located: 'Fibre named (SRLG)',
  what_if_passed: 'What-if check passed',
  what_if_failed: 'What-if check failed',
  pr_opened: 'Pull request opened',
  ci_passed: 'CI passed',
  ci_failed: 'CI failed',
  approved: 'Human approved',
  deployed: 'Deployed (GitOps)',
  verified: 'Recovery verified',
  revert_opened: 'Revert PR opened',
  work_order: 'Work order',
  ticket: 'Ticket',
  closed: 'Closed',
}

interface Props {
  events: IncidentEvent[]
  now: string
  start: string
  onJump: (ts: string) => void
}

const secs = (a: string, b: string) => Math.round((Date.parse(b) - Date.parse(a)) / 1000)
const fmt = (s: number) => (s < 60 ? `${s}s` : `${Math.floor(s / 60)}m${String(s % 60).padStart(2, '0')}s`)

/** Vertical incident timeline. Steps light up as the replay reaches them. */
export default function Timeline({ events, now, start, onJump }: Props) {
  const nowMs = Date.parse(now)
  const first = events[0]?.ts
  return (
    <ol className="relative space-y-1" data-testid="timeline">
      {events.map((e, i) => {
        const reached = Date.parse(e.ts) <= nowMs
        const isLatest = reached && (i === events.length - 1 || Date.parse(events[i + 1].ts) > nowMs)
        return (
          <li key={`${e.incident_id}-${i}`}>
            <button
              type="button"
              onClick={() => onJump(e.ts)}
              className={`flex w-full items-start gap-3 rounded-md px-2 py-1.5 text-left transition ${
                isLatest ? 'bg-sky-500/15 ring-1 ring-sky-400/50' : 'hover:bg-slate-800/60'
              } ${reached ? 'opacity-100' : 'opacity-35'}`}
            >
              <span
                className={`mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full text-xs ${
                  reached ? 'bg-sky-500 text-slate-950' : 'bg-slate-700 text-slate-300'
                }`}
              >
                {ICON[e.type]}
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex flex-wrap items-baseline justify-between gap-x-2">
                  <span className="text-sm font-semibold">{LABEL[e.type]}</span>
                  <span className="font-mono text-[11px] text-slate-400">
                    T+{fmt(secs(start, e.ts))}
                    {first && i > 0 ? ` · +${fmt(secs(first, e.ts))} after detection` : ''}
                  </span>
                </span>
                <span className="block text-xs text-slate-300">{e.summary}</span>
                {e.url && (
                  <a className="text-xs text-sky-400 underline" href={e.url} target="_blank" rel="noreferrer">
                    open link
                  </a>
                )}
              </span>
            </button>
          </li>
        )
      })}
    </ol>
  )
}
