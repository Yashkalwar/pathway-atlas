/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useMemo, useState } from 'react'
import Plot from './Plot'
import { fmt, get, post } from './api'
import { Alluvial, Figure, JourneyLine, Loading, PageHead, Seg, Stat, StatusStrip } from './components'
import { C, DRUG_COLORS } from './theme'

export function useGet<T = any>(url: string | null) {
  const [d, setD] = useState<T | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    if (!url) return
    let live = true
    setD(null)
    get<T>(url).then(x => live && setD(x)).catch(e => live && setErr(String(e)))
    return () => { live = false }
  }, [url])
  return { d, err }
}

const WINDOWS: [number, string][] = [[180, '6 mo'], [365, '12 mo'], [730, '24 mo']]

export function Overview({ go }: { go: (i: number) => void }) {
  const k = useGet('/api/kpis'), f = useGet('/api/funnel'), u = useGet('/api/unmet-need'), a = useGet('/api/attrition'), p = useGet('/api/pre-index')
  if (!k.d || !f.d || !u.d || !a.d || !p.d) return <Loading err={k.err || f.err} />
  const steps = [
    { label: 'to diagnosis', days: p.d.time_to_diagnosis_days.median },
    { label: 'to first DMARD', days: p.d.time_to_treatment_days.median },
    { label: 'to Therapy A', days: p.d.time_dx_to_therapy_a_days.median != null ? p.d.time_dx_to_therapy_a_days.median - p.d.time_to_treatment_days.median : null },
    { label: 'to switch', days: f.d.median_days_to_switch_among_switchers },
  ]
  const maxFlag = Math.max(...u.d.flags.map((x: any) => x.pct))
  return (<>
    <PageHead fig="Atlas 01 / The cohort" title={<>{fmt(k.d.index_patients)} patients started <em>adalimumab</em>. A year later, <span style={{ color: C.remain }}>{k.d.pct_persistent}%</span> are still on it.</>}
      lede={<>Longitudinal journeys of {fmt(k.d.ra_patients)} synthetic rheumatoid-arthritis patients, from first symptom to subsequent therapy, reconstructed from OMOP claims and EHR events.</>} />
    <Figure label="Fig. 1.1 - Status 12 months after initiating Therapy A" def={f.d.definition}>
      <StatusStrip f={f.d} />
      <button className="link" onClick={() => go(1)}>Trace the full flow &rarr;</button>
    </Figure>
    <Figure label="Fig. 1.2 - The typical journey, in median days" caption="Each segment is proportional to the median time between milestones. Medians are from different denominators, so the line is indicative, not additive.">
      <JourneyLine steps={steps} />
    </Figure>
    <div className="cols">
      <Figure label="Fig. 1.3 - Unmet-need signals" def={u.d.definition}>
        <ol className="signals">{u.d.flags.map((x: any, i: number) => (
          <li key={x.label}><span className="sn">{String(i + 1).padStart(2, '0')}</span>
            <div className="sbody"><div className="st">{x.label}</div>
              <div className="sbar"><i style={{ width: `${100 * x.pct / maxFlag}%` }} /></div></div>
            <div className="sv"><b>{x.pct}%</b><span className="mono">n={fmt(x.n)}</span></div></li>))}</ol>
      </Figure>
      <Figure label="Fig. 1.4 - Cohort ledger">
        <table className="ledger"><tbody>{a.d.steps.map((s: any) => <tr key={s.step} className={s.step.startsWith('Excluded') ? 'excl' : ''}><td>{s.step}</td><td className="num">{fmt(s.n)}</td></tr>)}</tbody></table>
        <div className="stats2">
          <Stat value={`${k.d.adherence_pct_pdc_80}%`} label="adherent (PDC >= 80%)" />
          <Stat value={`${k.d.pct_hospitalized}%`} label="hospitalized within 12 mo" tone={C.hosp} />
        </div>
      </Figure>
    </div>
  </>)
}

export function Funnel() {
  const [w, setW] = useState(365)
  const { d, err } = useGet(`/api/funnel?window=${w}`)
  if (!d) return <Loading err={err} />
  return (<>
    <PageHead fig="Atlas 02 / After initiation" title="What happens to patients after they initiate Therapy A?"
      lede={<>Of {fmt(d.evaluable_patients)} initiators with a known status at {d.window_days} days, <b style={{ color: C.remain }}>{fmt(d.remain_on_therapy)}</b> remain, <b style={{ color: C.disc }}>{fmt(d.discontinued)}</b> discontinue and <b style={{ color: C.switch }}>{fmt(d.switched)}</b> switch. Median time to switch: <b>{fmt(d.median_days_to_switch_among_switchers)} days</b>.</>}>
      <Seg value={w} options={WINDOWS} onChange={setW} />
    </PageHead>
    <Figure label={`Fig. 2.1 - Patient flow, index to ${d.window_days} days`} def={d.definition}
      caption={<>Band width is proportional to patients. Hatched segments are patients hospitalized within the window. {fmt(d.not_evaluable_censored)} initiators were censored before {d.window_days} days and are excluded.</>}>
      <Alluvial f={d} />
    </Figure>
    <div className="stats">
      <Stat value={fmt(d.median_days_to_switch_among_switchers)} label="median days to switch" note="among switchers" tone={C.switch} />
      <Stat value={fmt(d.median_days_to_discontinue_among_discontinuers)} label="median days to discontinue" tone={C.disc} />
      <Stat value={fmt(d.hospitalized_after_leaving_therapy)} label="admitted after leaving Therapy A" note="admission dated after switch / stop" tone={C.hosp} />
      <Stat value={`${d.adherence_pct_pdc_80}%`} label="adherent, PDC >= 80%" note={`mean PDC ${d.adherence_pdc_mean}`} tone={C.remain} />
    </div>
  </>)
}

export function Pathways() {
  const [depth, setDepth] = useState(3)
  const { d, err } = useGet(`/api/pathways?depth=${depth}`)
  const sankey = useMemo(() => d && [{
    type: 'sankey', arrangement: 'snap',
    node: { label: d.nodes, pad: 22, thickness: 12, line: { width: 0 },
      color: d.nodes.map((n: string) => n === 'Discontinued' ? C.disc : n.startsWith('Ongoing') ? '#B7AE9C' : DRUG_COLORS[n.split(': ')[1]] || C.neutral) },
    link: { source: d.links.map((l: any) => l.source), target: d.links.map((l: any) => l.target), value: d.links.map((l: any) => l.value), color: d.links.map((l: any) => {
      const t = d.nodes[l.target]
      return t === 'Discontinued' ? 'rgba(200,112,42,.28)' : t.startsWith('Ongoing') ? 'rgba(138,130,114,.18)' : 'rgba(58,79,163,.26)'
    }) },
  } as any], [d])
  if (!d || !sankey) return <Loading err={err} />
  const s = d.sunburst
  return (<>
    <PageHead fig="Atlas 03 / Sequencing" title="Which roads do patients take after Therapy A?"
      lede="Lines of therapy are derived from drug eras: a new advanced agent started within 90 days of the prior era ending is a switch; an overlapping agent is an add-on and does not start a new line.">
      <Seg value={depth} options={[[2, '2 lines'], [3, '3 lines'], [4, '4 lines']]} onChange={setDepth} />
    </PageHead>
    <Figure label="Fig. 3.1 - Treatment sequences (Sankey)" def={d.definition}>
      <Plot height={480} data={sankey} layout={{ margin: { t: 10, l: 10, r: 10, b: 10 }, font: { family: '"IBM Plex Sans"', size: 12, color: C.ink } }} />
    </Figure>
    <div className="cols">
      <Figure label="Fig. 3.2 - Most frequent pathways">
        <table className="ledger"><thead><tr><th>Pathway</th><th className="num">Patients</th><th className="num">Share</th></tr></thead>
          <tbody>{d.top_pathways.map((p: any) => <tr key={p.path}><td className="path">{p.path.split(' > ').map((x: string, i: number) => <span key={i} className="pill" style={{ borderColor: DRUG_COLORS[x] || C.rule, color: DRUG_COLORS[x] || C.ink2 }}>{x}</span>)}</td><td className="num">{fmt(p.n)}</td><td className="num">{p.pct}%</td></tr>)}</tbody></table>
      </Figure>
      <Figure label="Fig. 3.3 - Sequence sunburst">
        <Plot height={440} data={[{ type: 'sunburst', ids: s.ids, labels: s.labels, parents: s.parents, values: s.values, branchvalues: 'remainder',
          marker: { colors: s.labels.map((l: string) => l === 'Discontinued' ? C.disc : l.startsWith('Ongoing') ? '#CFC7B6' : l === 'Further lines' ? C.esc : DRUG_COLORS[l] || C.neutral), line: { color: C.paper, width: 1.5 } },
          insidetextfont: { color: '#fff', family: '"IBM Plex Sans"' }, textfont: { color: '#fff' } } as any]} layout={{ margin: { t: 0, l: 0, r: 0, b: 0 } }} />
      </Figure>
    </div>
  </>)
}

export function Survival() {
  const [ev, setEv] = useState('persistence')
  const [grp, setGrp] = useState('')
  const { d, err } = useGet(`/api/survival?event=${ev}${grp ? `&group=${grp}` : ''}`)
  if (!d) return <Loading err={err} />
  const curves: Record<string, any> = d.overall ? { 'All initiators': d.overall } : d.groups
  const tone = ev === 'persistence' ? C.remain : ev === 'switch' ? C.switch : C.disc
  const traces: any[] = []
  Object.entries(curves).forEach(([k, c]: any, i) => {
    if (c.lo) traces.push({ x: [...c.x, ...[...c.x].reverse()], y: [...c.hi, ...[...c.lo].reverse()], fill: 'toself', fillcolor: 'rgba(31,111,92,.12)', line: { width: 0 }, hoverinfo: 'skip', showlegend: false, type: 'scatter' })
    traces.push({ x: c.x, y: c.y, name: k, type: 'scatter', mode: 'lines', line: { shape: 'hv', width: 2.5, color: d.overall ? tone : undefined, dash: i > 2 ? 'dot' : undefined } })
  })
  return (<>
    <PageHead fig="Atlas 04 / Persistence" title={ev === 'persistence' ? 'How long do patients stay on Therapy A?' : ev === 'switch' ? 'When do patients switch?' : 'When do patients stop?'} lede={d.definition}>
      <Seg value={ev} options={[['persistence', 'Persistence'], ['switch', 'Switch'], ['discontinuation', 'Discontinuation']]} onChange={setEv} />
      <Seg value={grp} options={[['', 'All'], ['age', 'Age'], ['sex', 'Sex'], ['comorbidity', 'Comorbidity']]} onChange={setGrp} />
    </PageHead>
    <Figure label={`Fig. 4.1 - ${d.curve === 'survival' ? 'Kaplan-Meier persistence (95% CI)' : 'Cumulative incidence (Aalen-Johansen, competing risks)'}`}>
      <Plot height={420} data={traces} layout={{ xaxis: { title: { text: 'days since index' }, dtick: 90 }, yaxis: { title: { text: d.curve === 'survival' ? 'on therapy' : 'cumulative incidence' }, tickformat: '.0%', rangemode: 'tozero' },
        shapes: [180, 365, 730].map(x => ({ type: 'line', x0: x, x1: x, y0: 0, y1: 1, yref: 'paper', line: { color: C.rule, width: 1, dash: 'dot' } })) as any }} />
    </Figure>
    <Figure label="Table 4.2 - Milestones">
      <table className="ledger"><thead><tr><th>Group</th><th className="num">6 mo</th><th className="num">12 mo</th><th className="num">24 mo</th><th className="num">Patients</th><th className="num">Events</th></tr></thead>
        <tbody>{Object.entries(curves).map(([k, c]: any) => <tr key={k}><td>{k}</td><td className="num">{(100 * c.at_180).toFixed(1)}%</td><td className="num">{(100 * c.at_365).toFixed(1)}%</td><td className="num">{(100 * c.at_730).toFixed(1)}%</td><td className="num">{fmt(c.n)}</td><td className="num">{fmt(c.events)}</td></tr>)}</tbody></table>
    </Figure>
  </>)
}

export function Segments() {
  const [by, setBy] = useState('age')
  const sg = useGet(`/api/subgroups?by=${by}`), ut = useGet('/api/utilization'), pre = useGet('/api/pre-index'), es = useGet('/api/escalation')
  if (!sg.d || !ut.d || !pre.d || !es.d) return <Loading err={sg.err} />
  const g = sg.d.groups
  const q = (o: any) => <>{fmt(o.median)}<span className="iqr">IQR {fmt(o.p25)}-{fmt(o.p75)}</span></>
  const worst = [...g].sort((a: any, b: any) => a.pct_remain - b.pct_remain)[0]
  return (<>
    <PageHead fig="Atlas 05 / Who struggles" title={<><em>{by === 'age' ? `Patients aged ${worst.group}` : by === 'sex' ? `${worst.group} patients` : `Patients with ${worst.group} comorbidities`}</em> fare worst: only {worst.pct_remain}% remain on Therapy A at a year.</>}
      lede="Segment outcomes, delays before treatment, escalation and healthcare utilization.">
      <Seg value={by} options={[['age', 'Age'], ['sex', 'Sex'], ['comorbidity', 'Comorbidity']]} onChange={setBy} />
    </PageHead>
    <Figure label={`Fig. 5.1 - Outcomes at ${sg.d.window_days} days by ${by}`}>
      <Plot height={320} data={[
        { type: 'bar', name: 'Discontinued', x: g.map((r: any) => r.group), y: g.map((r: any) => r.pct_discontinued), marker: { color: C.disc } },
        { type: 'bar', name: 'Switched', x: g.map((r: any) => r.group), y: g.map((r: any) => r.pct_switched), marker: { color: C.switch } },
        { type: 'bar', name: 'Hospitalized', x: g.map((r: any) => r.group), y: g.map((r: any) => r.pct_hospitalized), marker: { color: C.hosp } }]}
        layout={{ barmode: 'group', bargap: 0.35, yaxis: { ticksuffix: '%' } }} />
    </Figure>
    <Figure label="Fig. 5.2 - Before Therapy A: delays (median days, all RA patients)">
      <div className="stats">
        <Stat value={q(pre.d.time_to_diagnosis_days)} label="symptom onset to diagnosis" />
        <Stat value={q(pre.d.time_to_specialist_days)} label="first visit to rheumatologist" />
        <Stat value={q(pre.d.time_to_treatment_days)} label="diagnosis to first DMARD" />
        <Stat value={`${pre.d.treatment_initiation_rate.pct_within_window}%`} label={`start a DMARD within ${pre.d.treatment_initiation_rate.window_days} days`} note={`${pre.d.treatment_initiation_rate.pct_ever}% ever`} tone={C.remain} />
      </div>
    </Figure>
    <div className="cols">
      <Figure label="Fig. 5.3 - Escalation while on Therapy A" def={es.d.definition}>
        <div className="bigline"><span style={{ color: C.esc }}>{es.d.pct}%</span> escalate within {es.d.window_days} days<span className="mono"> n={fmt(es.d.escalated)} / {fmt(es.d.n)} | median {fmt(es.d.median_days_to_escalation)} d</span></div>
        <Plot height={180} data={[{ type: 'bar', orientation: 'h', y: Object.keys(es.d.agents_added), x: Object.values(es.d.agents_added) as number[], marker: { color: C.esc } }]} layout={{ margin: { l: 100, t: 6, r: 10, b: 30 } }} />
      </Figure>
      <Figure label="Table 5.4 - Utilization, index to censoring" def={ut.d.definition}>
        <table className="ledger"><thead><tr><th>Status</th><th className="num">Pts</th><th className="num">Hosp/100PY</th><th className="num">ER/100PY</th><th className="num">Cost/PY</th></tr></thead>
          <tbody>{Object.entries(ut.d.by_status).map(([k, v]: any) => <tr key={k}><td>{k}</td><td className="num">{fmt(v.patients)}</td><td className="num">{v.hospitalizations_per_100py}</td><td className="num">{v.er_per_100py}</td><td className="num">${fmt(v.cost_per_patient_year)}</td></tr>)}
            <tr className="total"><td>All</td><td className="num">{fmt(ut.d.patients)}</td><td className="num">{ut.d.hospitalizations_per_100py}</td><td className="num">{ut.d.er_per_100py}</td><td className="num">${fmt(ut.d.mean_cost_per_patient_year)}</td></tr></tbody></table>
      </Figure>
    </div>
  </>)
}

export function Journey() {
  const [kind, setKind] = useState('switch')
  const smp = useGet(`/api/patients/sample?kind=${kind}&n=8`)
  const [pid, setPid] = useState<number | null>(null)
  useEffect(() => { if (smp.d?.length) setPid(smp.d[0].person_id) }, [smp.d])
  const j = useGet(pid ? `/api/patients/${pid}/journey` : null)
  if (!smp.d) return <Loading err={smp.err} />
  const tl = j.d?.timeline
  const isEv = (e: any) => /switch|discontinue|add_on|index|restart/.test(e.type)
  return (<>
    <PageHead fig="Atlas 06 / One journey" title={j.d?.summary ? <>Patient #{pid}: <span className="mono-h">{j.d.summary.sequence}</span></> : 'Patient journey'}
      lede="Every claim and encounter for one synthetic patient, with the pathway events the engine derived from them.">
      <Seg value={kind} options={[['switch', 'Switchers'], ['discontinue', 'Discontinuers'], ['persistent', 'Persistent']]} onChange={setKind} />
      <select value={pid ?? ''} onChange={e => setPid(+e.target.value)}>{smp.d.map((p: any) => <option key={p.person_id} value={p.person_id}>#{p.person_id}</option>)}</select>
    </PageHead>
    {tl && j.d.drug_spans && (<>
      <Figure label="Fig. 6.1 - Therapy exposure and encounters">
        <Plot height={400} data={[
          ...j.d.drug_spans.map((s: any) => ({ type: 'scatter', mode: 'lines', x: [s.start, s.end], y: [s.drug, s.drug], line: { width: 16, color: DRUG_COLORS[s.drug] || C.neutral }, hovertemplate: `${s.drug}: ${s.fills} fills<extra></extra>` })),
          ...['Outpatient', 'ER', 'Hospitalization'].map(t => ({ type: 'scatter', mode: 'markers', x: tl.filter((e: any) => e.type === t).map((e: any) => e.date), y: tl.filter((e: any) => e.type === t).map(() => t.toLowerCase()),
            marker: { size: t === 'Hospitalization' ? 13 : 7, symbol: t === 'Hospitalization' ? 'diamond' : 'line-ns-open', color: t === 'Hospitalization' ? C.hosp : t === 'ER' ? C.disc : C.muted, line: { width: 2 } }, hovertemplate: `${t} %{x}<extra></extra>` })),
          { type: 'scatter', mode: 'markers+text', x: tl.filter(isEv).map((e: any) => e.date), y: tl.filter(isEv).map(() => 'pathway'),
            text: tl.filter(isEv).map((e: any) => e.type.replace('Event:', '').replace(/_/g, ' ')), textposition: 'top center', textfont: { family: '"IBM Plex Mono"', size: 10 }, marker: { size: 10, color: C.ink, symbol: 'circle' } }] as any}
          layout={{ margin: { l: 110, t: 20, r: 20, b: 40 }, showlegend: false }} />
      </Figure>
      <Figure label="Table 6.2 - Derived events">
        <table className="ledger"><thead><tr><th>Date</th><th>Event</th><th>Detail</th></tr></thead>
          <tbody>{tl.filter((e: any) => e.type.startsWith('Event') || e.type === 'Hospitalization').map((e: any, i: number) => <tr key={i}><td className="mono">{e.date}</td><td>{e.type.replace('Event:', '').replace(/_/g, ' ')}</td><td>{e.detail}</td></tr>)}</tbody></table>
      </Figure></>)}
  </>)
}

type Msg = { role: 'user' | 'bot'; text: string; meta?: any }
export function Ask() {
  const sg = useGet<string[]>('/api/chat/suggestions')
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const send = async (text: string) => {
    if (!text.trim() || busy) return
    setMsgs(m => [...m, { role: 'user', text }]); setQ(''); setBusy(true)
    try { const r = await post('/api/chat', { question: text }); setMsgs(m => [...m, { role: 'bot', text: r.answer, meta: r }]) }
    catch (e) { setMsgs(m => [...m, { role: 'bot', text: `Error: ${e}` }]) }
    setBusy(false)
  }
  return (<>
    <PageHead fig="Atlas 07 / Consult" title="Ask the atlas." lede="A local model on this machine routes your question to one of nine vetted analyses and writes a brief. It never writes SQL, and any sentence citing a number not in the result is replaced by a deterministic summary." />
    <form onSubmit={e => { e.preventDefault(); send(q) }} className="askbox">
      <input value={q} onChange={e => setQ(e.target.value)} placeholder="What happens to patients after they initiate Therapy A?" />
      <button disabled={busy}>{busy ? 'Thinking' : 'Ask'}</button>
    </form>
    {!msgs.length && <div className="chips">{sg.d?.map(s => <button key={s} className="chip" onClick={() => send(s)}>{s}</button>)}</div>}
    <div className="briefs">{[...msgs].reverse().map((m, i) => m.role === 'user'
      ? <div key={i} className="q">&ldquo;{m.text}&rdquo;</div>
      : (<article key={i} className="brief"><p>{m.text}</p>
        {m.meta && <><Viz m={m.meta} /><footer className="prov">
          <span>analysis <b>{m.meta.tool}</b></span>
          <span>args <b>{JSON.stringify(m.meta.args)}</b></span>
          <span>routing <b>{m.meta.engine}</b></span>
          <span>model <b>{m.meta.model || 'none'}</b></span>
          <span className={m.meta.narration === 'llm' ? 'ok' : 'tmpl'}>{m.meta.narration === 'llm' ? 'LLM brief, numbers verified' : 'deterministic summary'}</span>
        </footer></>}</article>))}
      {busy && <div className="brief pulse">Running locally on CPU, this can take 10-40 seconds...</div>}</div>
  </>)
}

function Viz({ m }: { m: any }) {
  const d = m.data
  if (m.tool === 'get_funnel') return <StatusStrip f={d} />
  if (m.tool === 'get_pathways') return <Plot height={220} data={[{ type: 'bar', orientation: 'h', y: d.top_pathways.slice(0, 6).map((p: any) => p.path), x: d.top_pathways.slice(0, 6).map((p: any) => p.n), marker: { color: C.switch } }]} layout={{ yaxis: { autorange: 'reversed' }, margin: { l: 320, t: 6, r: 10, b: 30 } }} />
  if (m.tool === 'get_survival' && d.overall) return <Plot height={200} data={[{ type: 'scatter', x: d.overall.x, y: d.overall.y, line: { shape: 'hv', color: C.remain, width: 2.5 } }]} layout={{ yaxis: { tickformat: '.0%' }, margin: { t: 6, l: 50, r: 10, b: 30 } }} />
  if (m.tool === 'get_subgroups') return <Plot height={200} data={[{ type: 'bar', name: 'Discontinued %', x: d.groups.map((g: any) => g.group), y: d.groups.map((g: any) => g.pct_discontinued), marker: { color: C.disc } }, { type: 'bar', name: 'Switched %', x: d.groups.map((g: any) => g.group), y: d.groups.map((g: any) => g.pct_switched), marker: { color: C.switch } }]} layout={{ barmode: 'group', margin: { t: 6, l: 40, r: 10, b: 30 } }} />
  return null
}
