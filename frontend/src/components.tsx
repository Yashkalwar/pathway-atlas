/* eslint-disable @typescript-eslint/no-explicit-any */
import type { ReactNode } from 'react'
import { fmt } from './api'
import { C, DRUG_COLORS } from './theme'

export function PageHead({ fig, title, lede, children }: { fig: string; title: ReactNode; lede?: ReactNode; children?: ReactNode }) {
  return (
    <header className="pagehead">
      <div className="kicker">{fig}</div>
      <h1>{title}</h1>
      {lede && <p className="lede">{lede}</p>}
      {children && <div className="controls">{children}</div>}
    </header>
  )
}

export function Figure({ label, caption, def, children, className = '' }: { label: string; caption?: ReactNode; def?: string; children: ReactNode; className?: string }) {
  return (
    <section className={`figure ${className}`}>
      <div className="figlabel">{label}</div>
      {children}
      {caption && <p className="caption">{caption}</p>}
      {def && <details className="def"><summary>Method &amp; denominator</summary><p>{def}</p></details>}
    </section>
  )
}

export function Stat({ value, label, note, tone }: { value: ReactNode; label: string; note?: ReactNode; tone?: string }) {
  return (
    <div className="stat" style={tone ? { borderTopColor: tone } : undefined}>
      <div className="statv" style={tone ? { color: tone } : undefined}>{value}</div>
      <div className="statl">{label}</div>
      {note && <div className="statn">{note}</div>}
    </div>
  )
}

export function Seg({ value, options, onChange }: { value: string | number; options: [string | number, string][]; onChange: (v: any) => void }) {
  return <div className="seg">{options.map(([v, l]) => <button key={v} className={v === value ? 'on' : ''} onClick={() => onChange(v)}>{l}</button>)}</div>
}

export const Loading = ({ err }: { err?: string | null }) => <div className="loading">{err ? `Could not load: ${err}` : <span className="pulse">Reading the warehouse...</span>}</div>

/** 100% status strip: remain / discontinue / switch, with hospitalised overlay ticks. */
export function StatusStrip({ f }: { f: any }) {
  const n = f.evaluable_patients
  const parts = [
    { k: 'Remain on Therapy A', v: f.remain_on_therapy, c: C.remain, h: f.hospitalized_among_persistent },
    { k: 'Discontinue', v: f.discontinued, c: C.disc, h: f.hospitalized_among_discontinued },
    { k: 'Switch', v: f.switched, c: C.switch, h: f.hospitalized_among_switched },
  ]
  return (
    <div className="strip">
      <div className="stripbar">{parts.map(p => (
        <div key={p.k} style={{ width: `${100 * p.v / n}%`, background: p.c }} title={`${p.k}: ${fmt(p.v)}`}>
          <i style={{ width: `${100 * p.h / Math.max(p.v, 1)}%` }} />
        </div>))}</div>
      <div className="stripkey">{parts.map(p => (
        <div key={p.k}><span className="sw" style={{ background: p.c }} /><b>{(100 * p.v / n).toFixed(1)}%</b> {p.k} <span className="mono">n={fmt(p.v)}</span></div>))}
        <div><span className="sw hatch" /> hospitalized (within each group)</div></div>
    </div>
  )
}

/** Proportional patient-journey timeline built from median day gaps. */
export function JourneyLine({ steps }: { steps: { label: string; days: number | null; note?: string }[] }) {
  const total = steps.reduce((s, x) => s + (x.days || 0), 0) || 1
  const names = ['Symptom onset', 'RA diagnosis', 'First DMARD', 'Therapy A', 'Switch (if any)']
  return (
    <div className="jline">
      <div className="jtrack">{steps.map((s, i) => (
        <div key={s.label} className="jseg" style={{ flex: Math.max((s.days || 0) / total, 0.08) }}>
          <div className="jbar" style={{ background: [C.muted, C.ink2, C.remain, C.switch][i] }} />
          <div className="jdays"><b>{fmt(s.days)}</b> days</div>
          <div className="jlab">{s.label}</div>
        </div>))}</div>
      <div className="jnodes">{names.slice(0, steps.length + 1).map(n => <span key={n}>{n}</span>)}</div>
    </div>
  )
}

/** Custom SVG alluvial: initiated -> status at window -> switch destination. */
export function Alluvial({ f }: { f: any }) {
  const W = 960, H = 420, top = 20, bottom = 20, nodeW = 12, gap = 18
  const x0 = 150, x1 = 520, x2 = 800
  const n = f.evaluable_patients
  const plotH = H - top - bottom
  const s = (plotH - 2 * gap) / n
  const groups = [
    { k: 'remain', label: 'Remain on Therapy A', v: f.remain_on_therapy, c: C.remain, h_count: f.hospitalized_among_persistent },
    { k: 'disc', label: 'Discontinue', v: f.discontinued, c: C.disc, h_count: f.hospitalized_among_discontinued },
    { k: 'switch', label: 'Switch therapy', v: f.switched, c: C.switch, h_count: f.hospitalized_among_switched },
  ]
  let ly = top, ry = top
  const flows = groups.map(g => {
    const h = g.v * s
    const o = { ...g, h, ly, ry }
    ly += h; ry += h + gap
    return o
  })
  const sw = flows[2]
  const dests = Object.entries(f.switch_destinations as Record<string, number>)
  const dgap = 8
  const dTotal = dests.reduce((a, [, v]) => a + v, 0)
  const gaps = dgap * Math.max(dests.length - 1, 0)
  const dScale = Math.min(s * 1.8, (plotH * 0.72 - gaps) / Math.max(dTotal, 1))
  const colH = dTotal * dScale + gaps
  let dy = Math.min(Math.max(top + 24, sw.ry + sw.h / 2 - colH / 2), top + plotH - colH)
  const dTop = dy
  let sy = sw.ry
  const dflows = dests.map(([k, v]) => {
    const hs = v * s, hd = v * dScale
    const o = { k, v, hs, hd, sy, dy }
    sy += hs; dy += hd + dgap
    return o
  })
  const band = (xa: number, ya: number, ha: number, xb: number, yb: number, hb: number) => {
    const m = (xa + xb) / 2
    return `M${xa},${ya} C${m},${ya} ${m},${yb} ${xb},${yb} L${xb},${yb + hb} C${m},${yb + hb} ${m},${ya + ha} ${xa},${ya + ha} Z`
  }
  return (
    <svg className="alluvial" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Patient flow after initiating Therapy A">
      <defs>
        <pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="6" height="6" fill={C.hosp} opacity=".25" /><line x1="0" y1="0" x2="0" y2="6" stroke={C.hosp} strokeWidth="3" />
        </pattern>
      </defs>
      <text x={x0 - 14} y={top + plotH / 2 - 26} textAnchor="end" className="al-big">{fmt(n)}</text>
      <text x={x0 - 14} y={top + plotH / 2 - 6} textAnchor="end" className="al-lab">initiated {f.therapy}</text>
      <text x={x0 - 14} y={top + plotH / 2 + 12} textAnchor="end" className="al-mono">evaluable at {f.window_days}d</text>
      <rect x={x0} y={top} width={nodeW} height={n * s} fill={C.ink} />
      {flows.map((g, i) => (
        <g key={g.k} className="al-flow" style={{ animationDelay: `${i * 120}ms` }}>
          <path d={band(x0 + nodeW, g.ly, g.h, x1, g.ry, g.h)} fill={g.c} opacity=".28" />
          <rect x={x1} y={g.ry} width={nodeW} height={Math.max(g.h, 1)} fill={g.c} />
        </g>))}
      {flows.map(g => {
        const hh = g.v ? g.h * (g.h_count / g.v) : 0
        return <rect key={g.k + 'h'} x={x1} y={g.ry + g.h - hh} width={nodeW} height={hh} fill="url(#hatch)" />
      })}
      {flows.map(g => (
        <g key={g.k + 't'} className="al-halo" transform={`translate(${x1 + nodeW + 12}, ${g.ry + Math.min(Math.max(g.h / 2 - 14, 14), 40)})`}>
          <text className="al-num" fill={g.c}>{fmt(g.v)} <tspan className="al-pct">{(100 * g.v / n).toFixed(1)}%</tspan></text>
          <text y={18} className="al-lab">{g.label}</text>
          <text y={34} className="al-mono">{fmt(g.h_count)} hospitalized</text>
        </g>))}
      {dflows.map((d, i) => (
        <g key={d.k} className="al-flow" style={{ animationDelay: `${400 + i * 80}ms` }}>
          <path d={band(x1 + nodeW, d.sy, d.hs, x2, d.dy, d.hd)} fill={DRUG_COLORS[d.k] || C.switch} opacity=".32" />
          <rect x={x2} y={d.dy} width={nodeW} height={Math.max(d.hd, 1)} fill={DRUG_COLORS[d.k] || C.switch} />
          <text x={x2 + nodeW + 10} y={d.dy + d.hd / 2 + 4} className="al-lab"><tspan fontWeight={600}>{d.k}</tspan> <tspan className="al-mono">{fmt(d.v)}</tspan></text>
        </g>))}
      {dests.length > 0 && <text x={x2} y={dTop - 10} className="al-mono">SWITCHED TO</text>}
    </svg>
  )
}
