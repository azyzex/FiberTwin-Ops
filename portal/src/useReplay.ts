import { useCallback, useEffect, useRef, useState } from 'react'
import type { ReplayBundle } from './types'

const SPEEDS = [1, 2, 4, 8] as const
export type Speed = (typeof SPEEDS)[number]

/** Loads a replay bundle and drives a frame cursor (play / pause / seek / speed). */
export function useReplay(url: string) {
  const [bundle, setBundle] = useState<ReplayBundle | null>(null)
  const [error, setError] = useState<string | null>(null)
  // ?frame=N opens the replay at a given moment (handy for sharing a link to one step)
  const [frame, setFrame] = useState(() => Number(new URLSearchParams(window.location.search).get('frame')) || 0)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState<Speed>(2)
  const timer = useRef<number | undefined>(undefined)

  useEffect(() => {
    let cancelled = false
    fetch(url)
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
        return r.json() as Promise<ReplayBundle>
      })
      .then((b) => !cancelled && setBundle(b))
      .catch((e: unknown) => !cancelled && setError(String(e)))
    return () => {
      cancelled = true
    }
  }, [url])

  const last = bundle ? bundle.frames.length - 1 : 0

  useEffect(() => {
    if (!playing || !bundle) return
    // one replay frame = 15 s of real time; at speed 1 we show one frame per second
    timer.current = window.setInterval(() => {
      setFrame((f) => {
        if (f >= last) {
          setPlaying(false)
          return f
        }
        return f + 1
      })
    }, 1000 / speed)
    return () => window.clearInterval(timer.current)
  }, [playing, speed, bundle, last])

  const toggle = useCallback(() => {
    setPlaying((p) => {
      if (!p && frame >= last) setFrame(0)
      return !p
    })
  }, [frame, last])

  const cycleSpeed = useCallback(() => {
    setSpeed((s) => SPEEDS[(SPEEDS.indexOf(s) + 1) % SPEEDS.length])
  }, [])

  return { bundle, error, frame, setFrame, playing, toggle, speed, cycleSpeed, last }
}
