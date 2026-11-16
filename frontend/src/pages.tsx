/* eslint-disable @typescript-eslint/no-explicit-any */
import { useMemo, useState } from 'react'
import Plot from './Plot'
import { fmt, post, useGet } from './api'
import { Alluvial, Figure, JourneyLine, Loading, PageHead, Seg, Stat, StatusStrip, Takeaway } from './components'
import { C, DRUG_COLORS } from './theme'

const WINDOWS: [number, string][] = [[180, '6 mo'], [365, '12 mo'], [730, '24 mo']]

const SLICES: [string, string][] = [
  ['', 'All initiators'],
  ['copay:>=$250 OOP', 'High OOP copay (>=$250)'],
  ['copay:<$250 OOP', 'Low OOP copay (<$250)'],
  ['age:60+', 'Age 60+'],
  ['age:<45', 'Age <45'],
  ['comorbidity:2+', 'Comorbidities 2+'],
  ['sex:Female', 'Female'],
  ['sex:Male', 'Male'],
]

export function Overview({ go }: { go: (i: number) => void }) {
  const k = useGet('/api/kpis'), f = useGet('/api/funnel'), u = useGet('/api/unmet-need'), a = useGet('/api/attrition'), p = useGet('/api/pre-index')
  if (!k.d || !f.d || !u.d || !a.d || !p.d) return <Loading err={k.err || f.err} />
  const steps = [
    { label: 'to diagnosis', days: p.d.time_to_diagnosis_days.median },
    { label: 'to first DMARD', days: p.d.time_to_treatment_days.median },
    { label: 'to Therapy A', days: p.d.time_dx_to_therapy_a_days.median != null ? p.d.time_dx_to_therapy_a_days.median - p.d.time_to_treatment_days.median : null },
    { label: 'to switch', days: f.d.median_days_to_switch_among_switchers },
  ]
  const maxFlag = Math.max(...u.d.flags.map((x: any) => x.pct), 1)
  return (<>
    <PageHead fig="Atlas 01 / The cohort" title={<>{fmt(k.d.index_patients)} patients started <em>adalimumab</em>. A year later, <span style={{ color: C.remain }}>{k.d.pct_persistent}%</span> are still on it.</>}
      lede={<>Longitudinal journeys of {fmt(k.d.ra_patients)} synthetic rheumatoid-arthritis patients reconstructed strictly from standard OMOP CDM 5.4 claims and EHR domains (visits, diagnoses, prescriptions, CRP labs, and patient copays).</>} />
    <Takeaway label="Executive synthesis">
      <b>Why patients leave matters as much as how many:</b> {k.d.top_switch_driver_pct}% of switches follow objective flare / CRP spikes, whereas discontinuations split across acute safety events and out-of-pocket copay barriers. Among switchers, swapping mechanism of action improves 12-month 2nd-line retention by <b>+{k.d.moa_swap_gain_pp} pp</b> over intra-class TNFi cycling.
    </Takeaway>
    <Figure label="Fig. 1.1 - Status 12 months after initiating Therapy A" def={f.d.definition}>
      <StatusStrip f={f.d} />
      <div style={{ display: 'flex', gap: 20 }}>
        <button className="link" onClick={() => go(2)}>Inspect why patients switch vs. discontinue &rarr;</button>
        <button className="link" onClick={() => go(3)}>Compare TNFi cycling vs. MoA swapping &rarr;</button>
      </div>
    </Figure>
    <Figure label="Fig. 1.2 - The typical journey, in median days" caption="Each segment is proportional to the median time between milestones. Medians are from different denominators, so the line is indicative, not additive.">
      <JourneyLine steps={steps} />
    </Figure>
    <div className="cols">
      <Figure label="Fig. 1.3 - Actionable unmet-need signals" def={u.d.definition}>
        <ol className="signals">{u.d.flags.map((x: any, i: number) => (
          <li key={x.label}><span className="sn">{String(i + 1).padStart(2, '0')}</span>
            <div className="sbody"><div className="st">{x.label}</div>
              <div className="sbar"><i style={{ width: `${100 * x.pct / maxFlag}%` }} /></div></div>
            <div className="sv"><b>{x.pct}%</b><span className="mono">n={fmt(x.n)}</span></div></li>))}</ol>
      </Figure>
      <Figure label="Fig. 1.4 - Cohort ledger">
        <table className="ledger"><tbody>{a.d.steps.map((s: any) => <tr key={s.step} className={s.step.startsWith('Excluded') ? 'excl' : ''}><td>{s.step}</td><td className="num">{fmt(s.n)}</td></tr>)}</tbody></table>
        <div className="stats2">
          <Stat value={`${k.d.active_pct_pdc_80 ?? k.d.adherence_pct_pdc_80}%`} label="on-therapy active PDC >= 80%" note={`${k.d.adherence_pct_pdc_80}% fixed 12m PDC`} tone={C.remain} />
          <Stat value={`${k.d.pct_hospitalized}%`} label="hospitalized within 12 mo" tone={C.hosp} />
        </div>
      </Figure>
    </div>
  </>)
}

export function Funnel() {
  const [w, setW] = useState(365)
  const [slice, setSlice] = useState('')
  const sliceParams = useMemo(() => {
    if (!slice) return ''
    const [by, val] = slice.split(':')
    return `&group_by=${encodeURIComponent(by)}&group_val=${encodeURIComponent(val)}`
  }, [slice])
  const { d, err } = useGet(`/api/funnel?window=${w}${sliceParams}`)
  const ld = useGet(`/api/leave-drivers?window=${w}`)
  if (!d || !ld.d) return <Loading err={err || ld.err} />
  const maxProxy = Math.max(...ld.d.proxy_categories.flatMap((c: any) => [c.switch_pct, c.discontinue_pct]), 1)
  const maxFate = Math.max(...ld.d.post_discontinuation_fate.map((x: any) => x.pct), 1)
  const adh = ld.d.adherence_contrast
  return (<>
    <PageHead fig="Atlas 02 / After initiation" title="What happens after Therapy A — and why do patients leave?"
      lede={<>Of {fmt(d.evaluable_patients)} initiators evaluable at {d.window_days} days{d.group ? ` (${d.group.by}: ${d.group.value})` : ''}, <b style={{ color: C.remain }}>{fmt(d.remain_on_therapy)}</b> remain, <b style={{ color: C.disc }}>{fmt(d.discontinued)}</b> discontinue and <b style={{ color: C.switch }}>{fmt(d.switched)}</b> switch. Median time to switch: <b>{fmt(d.median_days_to_switch_among_switchers)} days</b>.</>}>
      <Seg value={w} options={WINDOWS} onChange={setW} />
      <select value={slice} onChange={e => setSlice(e.target.value)} aria-label="Filter subgroup">
        {SLICES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </PageHead>
    <Takeaway>{ld.d.takeaway}</Takeaway>
    <Figure label={`Fig. 2.1 - Patient flow, index to ${d.window_days} days${d.group ? ` (${d.group.value})` : ''}`} def={d.definition}
      caption={<>Band width is proportional to patients. Hatched segments are patients hospitalized within the window. {fmt(d.not_evaluable_censored)} initiators were censored before {d.window_days} days and are excluded.</>}>
      <Alluvial f={d} />
    </Figure>
    <div className="stats">
      <Stat value={fmt(d.median_days_to_switch_among_switchers)} label="median days to switch" note="among switchers" tone={C.switch} />
      <Stat value={fmt(d.median_days_to_discontinue_among_discontinuers)} label="median days to discontinue" note="among discontinuers" tone={C.disc} />
      <Stat value={fmt(d.hospitalized_after_leaving_therapy)} label="admitted after leaving Therapy A" note="admission dated after switch / stop" tone={C.hosp} />
      <Stat value={`${d.active_pct_pdc_80 ?? d.adherence_pct_pdc_80}%`} label="active on-therapy PDC >= 80%" note={`fixed 12m PDC >=80%: ${d.adherence_pct_pdc_80}%`} tone={C.remain} />
    </div>
    <Figure label={`Fig. 2.2 - Why patients leave: preceding ${ld.d.lookback_days}-day OMOP clinical & claims proxy phenotypes`} def={ld.d.definition}
      caption={<>Comparison of <b style={{ color: C.switch }}>Switchers (n={fmt(ld.d.n_switched)})</b> vs. <b style={{ color: C.disc }}>Discontinuers (n={fmt(ld.d.n_discontinued)})</b> at {ld.d.window_days} days. Derived strictly from OMOP visits, diagnoses, CRP labs, rescue fills, and patient OOP copays — no fabricated stop_reason column.</>}>
      <div className="proxy-bars">
        {ld.d.proxy_categories.map((c: any) => (
          <div key={c.category} className="p-row">
            <div><b>{c.category}</b></div>
            <div className="p-tracks">
              <div className="p-track" title={`Switchers: ${c.switch_pct}% (n=${fmt(c.switch_n)})`}>
                <i style={{ width: `${100 * c.switch_pct / maxProxy}%`, background: C.switch }} />
              </div>
              <div className="p-track" title={`Discontinuers: ${c.discontinue_pct}% (n=${fmt(c.discontinue_n)})`}>
                <i style={{ width: `${100 * c.discontinue_pct / maxProxy}%`, background: C.disc }} />
              </div>
            </div>
            <div className="p-nums">
              <div style={{ color: C.switch }}>Sw: <b>{c.switch_pct}%</b> ({fmt(c.switch_n)})</div>
              <div style={{ color: C.disc }}>Dc: <b>{c.discontinue_pct}%</b> ({fmt(c.discontinue_n)})</div>
            </div>
          </div>
        ))}
      </div>
    </Figure>
    <div className="cols">
      <Figure label={`Fig. 2.3 - After discontinuation: where the ${fmt(ld.d.n_discontinued)} discontinuers go next`}
        caption="Separates benign step-down or temporary drug holidays from complete care drop-off.">
        <ol className="signals">{ld.d.post_discontinuation_fate.map((x: any, i: number) => (
          <li key={x.fate}><span className="sn">{String(i + 1).padStart(2, '0')}</span>
            <div className="sbody"><div className="st">{x.fate}</div>
              <div className="sbar"><i style={{ width: `${100 * x.pct / maxFate}%`, background: x.fate.startsWith('Untreated') ? C.hosp : C.disc }} /></div></div>
            <div className="sv"><b>{x.pct}%</b><span className="mono">n={fmt(x.n)}</span></div></li>))}</ol>
      </Figure>
      <Figure label="Table 2.4 - Active refill regularity vs. fixed 12m PDC & copay"
        caption="Fixed 12m PDC drops mechanically after stopping; Active PDC isolates dose-taking regularity while on Therapy A.">
        <table className="ledger">
          <thead><tr><th>Cohort status</th><th className="num">Pts</th><th className="num">Active PDC</th><th className="num">Fixed 12m PDC</th><th className="num">Mean OOP/fill</th></tr></thead>
          <tbody>
            {([['Persistent', adh.persistent], ['Switched', adh.switched], ['Discontinued', adh.discontinued]] as const).map(([label, row]: any) => (
              <tr key={label}>
                <td>{label}</td>
                <td className="num">{fmt(row.n)}</td>
                <td className="num">{row.active_pdc_mean != null ? `${(100 * row.active_pdc_mean).toFixed(1)}%` : '-'}</td>
                <td className="num">{row.fixed_pdc_12m_mean != null ? `${(100 * row.fixed_pdc_12m_mean).toFixed(1)}%` : '-'}</td>
                <td className="num">{row.mean_oop_copay != null ? `$${fmt(row.mean_oop_copay)}` : '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Figure>
    </div>
  </>)
}

export function Pathways() {
  const [depth, setDepth] = useState(3)
  const { d, err } = useGet(`/api/pathways?depth=${depth}`)
  const sl = useGet('/api/second-line')
  const sankey = useMemo(() => d && [{
    type: 'sankey', arrangement: 'snap',
    node: { label: d.nodes, pad: 22, thickness: 12, line: { width: 0 },
      color: d.nodes.map((n: string) => n === 'Discontinued' ? C.disc : n.startsWith('Ongoing') ? '#B7AE9C' : DRUG_COLORS[n.split(': ')[1]] || C.neutral) },
    link: { source: d.links.map((l: any) => l.source), target: d.links.map((l: any) => l.target), value: d.links.map((l: any) => l.value), color: d.links.map((l: any) => {
      const t = d.nodes[l.target]
      return t === 'Discontinued' ? 'rgba(200,112,42,.28)' : t.startsWith('Ongoing') ? 'rgba(138,130,114,.18)' : 'rgba(58,79,163,.26)'
    }) },
  } as any], [d])
  const l2Traces = useMemo(() => {
    if (!sl.d) return null
    const out: any[] = []
    Object.entries(sl.d.modalities).forEach(([label, c]: any, idx) => {
      if (!c) return
      const col = idx === 0 ? C.remain : C.disc
      const fill = idx === 0 ? 'rgba(31,111,92,.12)' : 'rgba(200,112,42,.12)'
      out.push({ x: [...c.x, ...[...c.x].reverse()], y: [...c.hi, ...[...c.lo].reverse()], fill: 'toself', fillcolor: fill, line: { width: 0 }, hoverinfo: 'skip', showlegend: false, type: 'scatter' })
      out.push({ x: c.x, y: c.y, name: label, type: 'scatter', mode: 'lines', line: { shape: 'hv', width: 2.6, color: col } })
    })
    return out
  }, [sl.d])
  if (!d || !sankey || !sl.d || !l2Traces) return <Loading err={err || sl.err} />
  const s = d.sunburst
  return (<>
    <PageHead fig="Atlas 03 / Sequencing" title="Which roads do patients take — and does swapping MoA beat cycling TNFi?"
      lede="Lines of therapy are derived from drug eras: a new advanced agent within 90 days of the prior era ending is a switch; an overlapping agent is an add-on.">
      <Seg value={depth} options={[[2, '2 lines'], [3, '3 lines'], [4, '4 lines']]} onChange={setDepth} />
    </PageHead>
    {sl.d.takeaway && <Takeaway>{sl.d.takeaway}</Takeaway>}
    <div className="cols">
      <Figure label="Fig. 3.1 - 2nd-line persistence: MoA swapping vs. TNFi cycling (95% CI)" def={sl.d.definition}>
        <Plot height={340} data={l2Traces} layout={{ xaxis: { title: { text: 'days since 2nd-line switch' }, dtick: 90 }, yaxis: { title: { text: 'still on 2nd-line' }, tickformat: '.0%', rangemode: 'tozero' } }} />
      </Figure>
      <Figure label="Table 3.2 - Retention by 2nd-line destination & mechanism">
        <table className="ledger">
          <thead><tr><th>2nd-line agent</th><th>Mechanism</th><th className="num">Pts</th><th className="num">6 mo</th><th className="num">12 mo</th></tr></thead>
          <tbody>
            {sl.d.by_agent.map((r: any) => (
              <tr key={r.agent}>
                <td><span className="pill" style={{ borderColor: DRUG_COLORS[r.agent] || C.rule, color: DRUG_COLORS[r.agent] || C.ink }}>{r.agent}</span></td>
                <td><span className="mono">{r.moa}</span></td>
                <td className="num">{fmt(r.n)}</td>
                <td className="num">{r.persist_180}%</td>
                <td className="num"><b>{r.persist_365}%</b></td>
              </tr>
            ))}
          </tbody>
        </table>
      </Figure>
    </div>
    <Figure label="Fig. 3.3 - Full treatment sequences (Sankey)" def={d.definition}>
      <Plot height={440} data={sankey} layout={{ margin: { t: 10, l: 10, r: 10, b: 10 }, font: { family: '"IBM Plex Sans"', size: 12, color: C.ink } }} />
    </Figure>
    <div className="cols">
      <Figure label="Fig. 3.4 - Most frequent pathways">
        <table className="ledger"><thead><tr><th>Pathway</th><th className="num">Patients</th><th className="num">Share</th></tr></thead>
          <tbody>{d.top_pathways.map((p: any) => <tr key={p.path}><td className="path">{p.path.split(' > ').map((x: string, i: number) => <span key={i} className="pill" style={{ borderColor: DRUG_COLORS[x] || C.rule, color: DRUG_COLORS[x] || C.ink2 }}>{x}</span>)}</td><td className="num">{fmt(p.n)}</td><td className="num">{p.pct}%</td></tr>)}</tbody></table>
      </Figure>
      <Figure label="Fig. 3.5 - Sequence sunburst">
        <Plot height={420} data={[{ type: 'sunburst', ids: s.ids, labels: s.labels, parents: s.parents, values: s.values, branchvalues: 'remainder',
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
      <Seg value={grp} options={[['', 'All'], ['age', 'Age'], ['sex', 'Sex'], ['comorbidity', 'Comorbidity'], ['copay', 'OOP Copay']]} onChange={setGrp} />
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
  const [by, setBy] = useState('copay')
  const [coxOutcome, setCoxOutcome] = useState<'discontinuation' | 'switch'>('discontinuation')
  const sg = useGet(`/api/subgroups?by=${by}`), ut = useGet('/api/utilization'), pre = useGet('/api/pre-index'), es = useGet('/api/escalation'), rd = useGet('/api/risk-drivers')
  if (!sg.d || !ut.d || !pre.d || !es.d || !rd.d) return <Loading err={sg.err || rd.err} />
  const g = sg.d.groups
  const q = (o: any) => <>{fmt(o.median)}<span className="iqr">IQR {fmt(o.p25)}-{fmt(o.p75)}</span></>
  const worst = [...g].sort((a: any, b: any) => a.pct_remain - b.pct_remain)[0]
  const worstLabel = by === 'age' ? `Patients aged ${worst.group}` : by === 'sex' ? `${worst.group} patients` : by === 'copay' ? `Patients with ${worst.group}` : `Patients with ${worst.group} comorbidities`
  const coxRows = coxOutcome === 'discontinuation' ? rd.d.discontinuation_cox : rd.d.switch_cox
  const coxColor = coxOutcome === 'discontinuation' ? C.disc : C.switch
  return (<>
    <PageHead fig="Atlas 05 / Who struggles" title={<><em>{worstLabel}</em> fare worst: only {worst.pct_remain}% remain on Therapy A at a year.</>}
      lede="Multivariable Cox hazard ratios, subgroup outcomes, pre-treatment delays, escalation and utilization.">
      <Seg value={by} options={[['copay', 'OOP Copay'], ['comorbidity', 'Comorbidity'], ['age', 'Age'], ['sex', 'Sex']]} onChange={setBy} />
    </PageHead>
    <Takeaway>{rd.d.takeaway}</Takeaway>
    <div className="cols">
      <Figure label={`Fig. 5.1 - Multivariable Cox hazard ratios (${coxOutcome})`} def={rd.d.definition}>
        <div style={{ marginBottom: 10 }}>
          <Seg value={coxOutcome} options={[['discontinuation', 'Discontinuation HR'], ['switch', 'Switch HR']]} onChange={setCoxOutcome} />
        </div>
        <Plot height={260} data={[{
          type: 'scatter', mode: 'markers',
          y: coxRows.map((r: any) => r.factor),
          x: coxRows.map((r: any) => r.hr),
          marker: { size: 10, color: coxColor },
          error_x: {
            type: 'data', symmetric: false,
            array: coxRows.map((r: any) => r.ci_hi - r.hr),
            arrayminus: coxRows.map((r: any) => r.hr - r.ci_lo),
            color: coxColor, thickness: 2, width: 5,
          },
          hovertemplate: '%{y}: HR %{x:.2f}<extra></extra>',
        } as any]} layout={{
          margin: { l: 225, t: 10, r: 20, b: 40 },
          xaxis: { title: { text: 'Adjusted Hazard Ratio (95% CI)' } },
          yaxis: { autorange: 'reversed' },
          shapes: [{ type: 'line', x0: 1, x1: 1, y0: 0, y1: 1, yref: 'paper', line: { color: C.ink, width: 1.2, dash: 'dash' } }] as any,
        }} />
      </Figure>
      <Figure label="Table 5.2 - Adjusted HRs & inpatient rate ratios">
        <table className="ledger">
          <thead><tr><th>Predictor ({coxOutcome})</th><th className="num">HR (95% CI)</th><th className="num">p-val</th></tr></thead>
          <tbody>
            {coxRows.map((r: any) => (
              <tr key={r.key}>
                <td>{r.factor}</td>
                <td className="num"><b>{r.hr.toFixed(2)}</b> ({r.ci_lo.toFixed(2)}-{r.ci_hi.toFixed(2)})</td>
                <td className="num">{r.p_value < 0.001 ? '<0.001' : r.p_value.toFixed(3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div style={{ marginTop: 14, paddingTop: 10, borderTop: `1px solid ${C.rule}` }}>
          <div className="mono" style={{ marginBottom: 6 }}>INPATIENT ADMISSION RATE RATIOS (PER 100 PY)</div>
          {rd.d.hospitalization_rr.map((h: any) => (
            <div key={h.comparison} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, padding: '3px 0' }}>
              <span>{h.comparison}</span>
              <span className="mono"><b style={{ color: C.hosp }}>{h.rate_ratio}x</b> ({h.rate_exposed_100py} vs {h.rate_ref_100py})</span>
            </div>
          ))}
        </div>
      </Figure>
    </div>
    <Figure label={`Fig. 5.3 - Outcomes at ${sg.d.window_days} days by ${by}`}>
      <Plot height={300} data={[
        { type: 'bar', name: 'Discontinued', x: g.map((r: any) => r.group), y: g.map((r: any) => r.pct_discontinued), marker: { color: C.disc } },
        { type: 'bar', name: 'Switched', x: g.map((r: any) => r.group), y: g.map((r: any) => r.pct_switched), marker: { color: C.switch } },
        { type: 'bar', name: 'Hospitalized', x: g.map((r: any) => r.group), y: g.map((r: any) => r.pct_hospitalized), marker: { color: C.hosp } }]}
        layout={{ barmode: 'group', bargap: 0.35, yaxis: { ticksuffix: '%' } }} />
    </Figure>
    <Figure label="Fig. 5.4 - Before Therapy A: delays (median days, all RA patients)">
      <div className="stats">
        <Stat value={q(pre.d.time_to_diagnosis_days)} label="symptom onset to diagnosis" />
        <Stat value={q(pre.d.time_to_specialist_days)} label="first visit to rheumatologist" />
        <Stat value={q(pre.d.time_to_treatment_days)} label="diagnosis to first DMARD" />
        <Stat value={`${pre.d.treatment_initiation_rate.pct_within_window}%`} label={`start a DMARD within ${pre.d.treatment_initiation_rate.window_days} days`} note={`${pre.d.treatment_initiation_rate.pct_ever}% ever`} tone={C.remain} />
      </div>
    </Figure>
    <div className="cols">
      <Figure label="Fig. 5.5 - Escalation while on Therapy A" def={es.d.definition}>
        <div className="bigline"><span style={{ color: C.esc }}>{es.d.pct}%</span> escalate within {es.d.window_days} days<span className="mono"> n={fmt(es.d.escalated)} / {fmt(es.d.n)} | median {fmt(es.d.median_days_to_escalation)} d</span></div>
        <Plot height={180} data={[{ type: 'bar', orientation: 'h', y: Object.keys(es.d.agents_added), x: Object.values(es.d.agents_added) as number[], marker: { color: C.esc } }]} layout={{ margin: { l: 100, t: 6, r: 10, b: 30 } }} />
      </Figure>
      <Figure label="Table 5.6 - Utilization, index to censoring" def={ut.d.definition}>
        <table className="ledger"><thead><tr><th>Status</th><th className="num">Pts</th><th className="num">Hosp/100PY</th><th className="num">ER/100PY</th><th className="num">Cost/PY</th></tr></thead>
          <tbody>{Object.entries(ut.d.by_status).map(([k, v]: any) => <tr key={k}><td>{k}</td><td className="num">{fmt(v.patients)}</td><td className="num">{v.hospitalizations_per_100py}</td><td className="num">{v.er_per_100py}</td><td className="num">${fmt(v.cost_per_patient_year)}</td></tr>)}
            <tr className="total"><td>All</td><td className="num">{fmt(ut.d.patients)}</td><td className="num">{ut.d.hospitalizations_per_100py}</td><td className="num">{ut.d.er_per_100py}</td><td className="num">${fmt(ut.d.mean_cost_per_patient_year)}</td></tr></tbody></table>
      </Figure>
    </div>
  </>)
}

export function Journey() {
  const [kind, setKind] = useState('switch')
  const smp = useGet(`/api/patients/sample?kind=${kind}&n=10`)
  const [selectedPid, setSelectedPid] = useState<number | null>(null)
  const validPids = useMemo(() => new Set((smp.d || []).map((p: any) => p.person_id)), [smp.d])
  const pid: number | null = selectedPid !== null && validPids.has(selectedPid) ? selectedPid : (smp.d?.[0]?.person_id ?? null)
  const j = useGet(pid ? `/api/patients/${pid}/journey` : null)
  if (!smp.d) return <Loading err={smp.err} />
  const tl = j.d?.timeline
  const sm = j.d?.summary
  const labs = j.d?.labs || []
  const isEv = (e: any) => /switch|discontinue|add_on|index|restart|new_line/.test(e.type)
  return (<>
    <PageHead fig="Atlas 06 / One journey" title={sm ? <>Patient #{pid}: <span className="mono-h">{sm.sequence}</span></> : 'Patient journey'}
      lede="Every raw OMOP claim, CRP lab, copay, and encounter for one synthetic patient — alongside the pathway events and 60-day proxy signal derived from them.">
      <Seg value={kind} options={[['switch', 'Switchers'], ['discontinue', 'Discontinuers'], ['persistent', 'Persistent']]} onChange={setKind} />
      <select value={pid ?? ''} onChange={e => setSelectedPid(+e.target.value)}>
        {smp.d.map((p: any) => <option key={p.person_id} value={p.person_id}>#{p.person_id} {p.leave_proxy ? `— ${p.leave_proxy}` : ''}</option>)}
      </select>
    </PageHead>
    {sm && (
      <div className="badges">
        <div className="badge-kv"><span>Status</span><b>{sm.first_leave}</b></div>
        {sm.leave_proxy && <div className="badge-kv"><span>60d OMOP proxy</span><b>{sm.leave_proxy}</b></div>}
        {sm.post_disc_fate && <div className="badge-kv"><span>Post-stop fate</span><b>{sm.post_disc_fate}</b></div>}
        {sm.switch_modality && <div className="badge-kv"><span>2nd-line strategy</span><b>{sm.switch_modality === 'moa_swapping' ? 'MoA swap' : 'TNFi cycle'}</b></div>}
        {sm.active_pdc != null && <div className="badge-kv"><span>Active PDC</span><b>{(100 * sm.active_pdc).toFixed(0)}%</b></div>}
        {sm.mean_oop_copay != null && <div className="badge-kv"><span>Mean OOP/fill</span><b>${fmt(sm.mean_oop_copay)}</b></div>}
        {sm.baseline_crp != null && <div className="badge-kv"><span>Baseline CRP</span><b>{sm.baseline_crp} mg/L</b></div>}
      </div>
    )}
    {tl && j.d.drug_spans && (<>
      <Figure label="Fig. 6.1 - Therapy exposure, copays and clinical encounters">
        <Plot height={360} data={[
          ...j.d.drug_spans.map((s: any) => ({ type: 'scatter', mode: 'lines', x: [s.start, s.end], y: [s.drug, s.drug], line: { width: 16, color: DRUG_COLORS[s.drug] || C.neutral }, hovertemplate: `${s.drug}: ${s.fills} fills (mean OOP $${s.mean_oop}/fill)<extra></extra>` })),
          ...['Outpatient', 'ER', 'Hospitalization'].map(t => ({ type: 'scatter', mode: 'markers', x: tl.filter((e: any) => e.type === t).map((e: any) => e.date), y: tl.filter((e: any) => e.type === t).map(() => t.toLowerCase()),
            marker: { size: t === 'Hospitalization' ? 13 : 7, symbol: t === 'Hospitalization' ? 'diamond' : 'line-ns-open', color: t === 'Hospitalization' ? C.hosp : t === 'ER' ? C.disc : C.muted, line: { width: 2 } }, hovertemplate: `${t} %{x}<extra></extra>` })),
          { type: 'scatter', mode: 'markers+text', x: tl.filter(isEv).map((e: any) => e.date), y: tl.filter(isEv).map(() => 'pathway'),
            text: tl.filter(isEv).map((e: any) => e.type.replace('Event:', '').replace(/_/g, ' ')), textposition: 'top center', textfont: { family: '"IBM Plex Mono"', size: 10 }, marker: { size: 10, color: C.ink, symbol: 'circle' } }] as any}
          layout={{ margin: { l: 120, t: 20, r: 20, b: 40 }, showlegend: false }} />
      </Figure>
      {labs.length > 0 && (
        <Figure label="Fig. 6.2 - Longitudinal C-reactive protein (CRP, mg/L) from OMOP measurement"
          caption="Dashed threshold = 10 mg/L upper limit of normal. Spikes preceding a switch/stop trigger the flare/inflammation proxy.">
          <Plot height={190} data={[{
            type: 'scatter', mode: 'lines+markers',
            x: labs.map((l: any) => l.date), y: labs.map((l: any) => l.crp),
            line: { color: C.hosp, width: 2 }, marker: { size: 7, color: labs.map((l: any) => l.crp > 10 ? C.hosp : C.remain) },
            hovertemplate: 'CRP %{y} mg/L (%{x})<extra></extra>',
          } as any]} layout={{
            margin: { l: 60, t: 10, r: 20, b: 36 }, showlegend: false,
            yaxis: { title: { text: 'CRP mg/L' }, rangemode: 'tozero' },
            shapes: [{ type: 'line', x0: 0, x1: 1, xref: 'paper', y0: 10, y1: 10, line: { color: C.disc, width: 1.2, dash: 'dash' } }] as any,
          }} />
        </Figure>
      )}
      <Figure label="Table 6.3 - Derived pathway events & acute visits">
        <table className="ledger"><thead><tr><th>Date</th><th>Event</th><th>Detail</th></tr></thead>
          <tbody>{tl.filter((e: any) => e.type.startsWith('Event') || e.type === 'Hospitalization' || e.type === 'ER' || /flare|adverse/.test(e.detail || '')).map((e: any, i: number) => <tr key={i}><td className="mono">{e.date}</td><td>{e.type.replace('Event:', '').replace(/_/g, ' ')}</td><td>{e.detail}</td></tr>)}</tbody></table>
      </Figure></>)}
  </>)
}

type Msg = { role: 'user' | 'bot'; text: string; meta?: any }
export function Ask({ llmEnabled = false }: { llmEnabled?: boolean }) {
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
    <PageHead fig="Atlas 07 / Consult" title="Ask the atlas."
      lede={llmEnabled
        ? 'A local model routes your question to twelve vetted OMOP analyses and writes a brief. It never writes SQL, and any sentence citing an unverified number falls back to a deterministic summary.'
        : 'Guided analytics router maps your question to twelve vetted OMOP analyses (including stop/switch proxy drivers, MoA swapping, and Cox risk ratios) and returns verified summaries with inline charts.'} />
    <form onSubmit={e => { e.preventDefault(); send(q) }} className="askbox">
      <input value={q} onChange={e => setQ(e.target.value)} placeholder="Why do patients discontinue or switch from Therapy A?" />
      <button disabled={busy}>{busy ? 'Working' : 'Ask'}</button>
    </form>
    {!msgs.length && <div className="chips">{sg.d?.map(s => <button key={s} className="chip" onClick={() => send(s)}>{s}</button>)}</div>}
    <div className="briefs">{[...msgs].reverse().map((m, i) => m.role === 'user'
      ? <div key={i} className="q">&ldquo;{m.text}&rdquo;</div>
      : (<article key={i} className="brief"><p>{m.text}</p>
        {m.meta && <><Viz m={m.meta} /><footer className="prov">
          <span>analysis <b>{m.meta.tool}</b></span>
          {Object.keys(m.meta.args || {}).length > 0 && <span>args <b>{JSON.stringify(m.meta.args)}</b></span>}
          <span>routing <b>{m.meta.engine}</b></span>
          {m.meta.model && <span>model <b>{m.meta.model}</b></span>}
          <span className="ok">{m.meta.narration === 'llm' ? 'LLM brief, numbers verified' : 'verified deterministic brief'}</span>
        </footer></>}</article>))}
      {busy && <div className="brief pulse">{llmEnabled ? 'Running locally on CPU, this can take 10-40 seconds...' : 'Computing verified summary...'}</div>}</div>
  </>)
}

function Viz({ m }: { m: any }) {
  const d = m.data
  if (m.tool === 'get_funnel') return <StatusStrip f={d} />
  if (m.tool === 'get_leave_drivers') {
    const cats = d.proxy_categories
    return <Plot height={220} data={[
      { type: 'bar', name: 'Switchers %', x: cats.map((c: any) => c.category), y: cats.map((c: any) => c.switch_pct), marker: { color: C.switch } },
      { type: 'bar', name: 'Discontinuers %', x: cats.map((c: any) => c.category), y: cats.map((c: any) => c.discontinue_pct), marker: { color: C.disc } },
    ]} layout={{ barmode: 'group', margin: { t: 8, l: 45, r: 10, b: 65 }, yaxis: { ticksuffix: '%' } }} />
  }
  if (m.tool === 'get_second_line_outcomes') {
    const traces = Object.entries(d.modalities).map(([k, c]: any, i) => ({
      type: 'scatter', mode: 'lines', name: k, x: c.x, y: c.y,
      line: { shape: 'hv', width: 2.5, color: i === 0 ? C.remain : C.disc },
    }))
    return <Plot height={220} data={traces as any} layout={{ yaxis: { tickformat: '.0%' }, margin: { t: 8, l: 50, r: 10, b: 45 } }} />
  }
  if (m.tool === 'get_risk_factors') {
    const rows = d.discontinuation_cox
    return <Plot height={210} data={[{
      type: 'bar', orientation: 'h', y: rows.map((r: any) => r.factor), x: rows.map((r: any) => r.hr), marker: { color: C.disc },
    }]} layout={{ yaxis: { autorange: 'reversed' }, xaxis: { title: { text: 'Discontinuation HR' } }, margin: { l: 230, t: 8, r: 15, b: 36 } }} />
  }
  if (m.tool === 'get_pathways') return <Plot height={220} data={[{ type: 'bar', orientation: 'h', y: d.top_pathways.slice(0, 6).map((p: any) => p.path), x: d.top_pathways.slice(0, 6).map((p: any) => p.n), marker: { color: C.switch } }]} layout={{ yaxis: { autorange: 'reversed' }, margin: { l: 320, t: 6, r: 10, b: 30 } }} />
  if (m.tool === 'get_survival' && d.overall) return <Plot height={200} data={[{ type: 'scatter', x: d.overall.x, y: d.overall.y, line: { shape: 'hv', color: C.remain, width: 2.5 } }]} layout={{ yaxis: { tickformat: '.0%' }, margin: { t: 6, l: 50, r: 10, b: 30 } }} />
  if (m.tool === 'get_subgroups') return <Plot height={200} data={[{ type: 'bar', name: 'Discontinued %', x: d.groups.map((g: any) => g.group), y: d.groups.map((g: any) => g.pct_discontinued), marker: { color: C.disc } }, { type: 'bar', name: 'Switched %', x: d.groups.map((g: any) => g.group), y: d.groups.map((g: any) => g.pct_switched), marker: { color: C.switch } }]} layout={{ barmode: 'group', margin: { t: 6, l: 40, r: 10, b: 30 } }} />
  return null
}
