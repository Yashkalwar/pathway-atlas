/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useState } from 'react'

const cache = new Map<string, any>()
const inflight = new Map<string, Promise<any>>()

export function peek<T = any>(url: string | null): T | null {
  return url && cache.has(url) ? (cache.get(url) as T) : null
}

export function useGet<T = any>(url: string | null) {
  const [state, setState] = useState<{ url: string | null; d: T | null; err: string | null }>(() => ({
    url, d: peek<T>(url), err: null,
  }))
  const cached = peek<T>(url)
  useEffect(() => {
    if (!url || peek<T>(url) !== null) return
    let live = true
    get<T>(url)
      .then(x => { if (live) setState({ url, d: x, err: null }) })
      .catch(e => { if (live) setState({ url, d: null, err: String(e) }) })
    return () => { live = false }
  }, [url])
  if (cached !== null) return { d: cached, err: null }
  if (state.url !== url) return { d: null, err: null }
  return { d: state.d, err: state.err }
}

export async function get<T = any>(url: string): Promise<T> {
  if (cache.has(url)) return cache.get(url) as T
  let p = inflight.get(url)
  if (!p) {
    p = fetch(url)
      .then(r => {
        if (!r.ok) throw new Error(`${url}: ${r.status}`)
        return r.json()
      })
      .then(d => {
        cache.set(url, d)
        inflight.delete(url)
        return d
      })
      .catch(e => {
        inflight.delete(url)
        throw e
      })
    inflight.set(url, p)
  }
  return p as Promise<T>
}

export async function post<T = any>(url: string, body: unknown): Promise<T> {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  if (!r.ok) throw new Error(`${url}: ${r.status}`)
  return r.json()
}

export function clearCache() {
  cache.clear()
  inflight.clear()
}

export const fmt = (n: number | null | undefined, d = 0) =>
  n === null || n === undefined ? '-' : n.toLocaleString(undefined, { maximumFractionDigits: d })
