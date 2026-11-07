/* eslint-disable @typescript-eslint/no-explicit-any */
export async function get<T = any>(url: string): Promise<T> {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`${url}: ${r.status}`)
  return r.json()
}

export async function post<T = any>(url: string, body: unknown): Promise<T> {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  if (!r.ok) throw new Error(`${url}: ${r.status}`)
  return r.json()
}

export const fmt = (n: number | null | undefined, d = 0) =>
  n === null || n === undefined ? '-' : n.toLocaleString(undefined, { maximumFractionDigits: d })
