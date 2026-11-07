import { Component, useEffect, useState, type ReactNode } from 'react'

class Boundary extends Component<{ children: ReactNode }, { err: string | null }> {
  state = { err: null as string | null }
  static getDerivedStateFromError(e: Error) { return { err: e.message } }
  render() { return this.state.err ? <div className="loading">This view failed to render: {this.state.err}</div> : this.props.children }
}
import { Ask, Funnel, Journey, Overview, Pathways, Segments, Survival, useGet } from './pages'

const STAGES: { n: string; t: string; s: string }[] = [
  { n: '01', t: 'The cohort', s: 'overview' }, { n: '02', t: 'After initiation', s: 'flow & funnel' },
  { n: '03', t: 'Sequencing', s: 'lines of therapy' }, { n: '04', t: 'Persistence', s: 'time to event' },
  { n: '05', t: 'Who struggles', s: 'segments & cost' }, { n: '06', t: 'One journey', s: 'patient timeline' },
  { n: '07', t: 'Consult', s: 'ask the local LLM' },
]

export default function App() {
  const [tab, setTab] = useState(() => Math.max(0, STAGES.findIndex(s => '#' + s.n === location.hash)))
  useEffect(() => { history.replaceState(null, '', '#' + STAGES[tab].n); window.scrollTo(0, 0) }, [tab])
  const k = useGet('/api/kpis'), h = useGet('/api/health')
  const pages = [<Overview go={setTab} />, <Funnel />, <Pathways />, <Survival />, <Segments />, <Journey />, <Ask />]
  return (
    <div className="shell">
      <aside className="rail">
        <div className="brand">
          <svg viewBox="0 0 32 32" width="30" height="30"><rect width="32" height="32" rx="6" fill="#1B1F2A" /><path d="M6 10c8 0 10 12 20 12" stroke="#1F6F5C" strokeWidth="3.5" fill="none" strokeLinecap="round" /><path d="M6 10c8 0 10-2 20-2" stroke="#C8702A" strokeWidth="2" fill="none" strokeLinecap="round" /><circle cx="6" cy="10" r="3" fill="#F3EFE6" /></svg>
          <div><div className="bname">Pathway Atlas</div><div className="bsub">RA treatment journeys</div></div>
        </div>
        <nav className="stages">{STAGES.map((s, i) => (
          <button key={s.n} className={i === tab ? 'on' : ''} onClick={() => setTab(i)}>
            <span className="dot" /><span className="sn">{s.n}</span><span className="stx"><b>{s.t}</b><small>{s.s}</small></span>
          </button>))}</nav>
        <div className="prov-rail">
          <div className="synthetic">Synthetic data</div>
          <dl>
            <dt>Therapy A</dt><dd>adalimumab (TNFi)</dd>
            <dt>Model</dt><dd>OMOP CDM 5.4 subset</dd>
            <dt>Calibration</dt><dd>{k.d?.calibration_profile ?? '...'} / seed {k.d?.seed ?? ''}</dd>
            <dt>Local LLM</dt><dd>{h.d ? (h.d.llm_enabled ? h.d.llm_model : 'off') : '...'}</dd>
          </dl>
        </div>
      </aside>
      <main className="page" key={tab}><Boundary>{pages[tab]}</Boundary></main>
    </div>
  )
}
