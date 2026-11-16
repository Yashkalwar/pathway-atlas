import { Component, useEffect, useState, type ReactNode } from 'react'
import { useGet } from './api'
import { Ask, Funnel, Journey, Overview, Pathways, Segments, Survival } from './pages'
import { StudyDesigner } from './study'

class Boundary extends Component<{ children: ReactNode }, { err: string | null }> {
  state = { err: null as string | null }
  static getDerivedStateFromError(e: Error) { return { err: e.message } }
  render() { return this.state.err ? <div className="loading">This view failed to render: {this.state.err}</div> : this.props.children }
}

const STAGES: { n: string; t: string; s: string }[] = [
  { n: '00', t: 'Study design', s: 'client definitions' },
  { n: '01', t: 'The cohort', s: 'overview & takeaways' },
  { n: '02', t: 'After initiation', s: 'flow & why they leave' },
  { n: '03', t: 'Sequencing', s: 'lines & MoA swap' },
  { n: '04', t: 'Persistence', s: 'time to event' },
  { n: '05', t: 'Who struggles', s: 'risk ratios & cost' },
  { n: '06', t: 'One journey', s: 'claims, labs & events' },
  { n: '07', t: 'Consult', s: 'guided Q&A' },
]

export default function App() {
  const [tab, setTab] = useState(() => Math.max(0, STAGES.findIndex(s => '#' + s.n === location.hash)))
  const [ver, setVer] = useState(0)
  useEffect(() => { history.replaceState(null, '', '#' + STAGES[tab].n); window.scrollTo(0, 0) }, [tab])
  const k = useGet('/api/kpis?v=' + ver), h = useGet('/api/health')
  const llmOn = h.d ? Boolean(h.d.llm_enabled) : false
  const view = tab === 0 ? <StudyDesigner onApplied={() => setVer(v => v + 1)} />
    : tab === 1 ? <Overview go={setTab} />
    : tab === 2 ? <Funnel />
    : tab === 3 ? <Pathways />
    : tab === 4 ? <Survival />
    : tab === 5 ? <Segments />
    : tab === 6 ? <Journey />
    : <Ask llmEnabled={llmOn} />
  return (
    <div className="shell">
      <aside className="rail">
        <div className="brand">
          <svg viewBox="0 0 32 32" width="30" height="30"><rect width="32" height="32" rx="6" fill="#1B1F2A" /><path d="M6 10c8 0 10 12 20 12" stroke="#1F6F5C" strokeWidth="3.5" fill="none" strokeLinecap="round" /><path d="M6 10c8 0 10-2 20-2" stroke="#C8702A" strokeWidth="2" fill="none" strokeLinecap="round" /><circle cx="6" cy="10" r="3" fill="#F3EFE6" /></svg>
          <div><div className="bname">Pathway Atlas</div><div className="bsub">RA treatment journeys</div></div>
        </div>
        <nav className="stages">{STAGES.map((s, i) => (
          <button key={s.n} className={i === tab ? 'on' : ''} onClick={() => setTab(i)}>
            <span className="dot" /><span className="sn">{s.n}</span><span className="stx"><b>{s.t}</b><small>{s.n === '07' && llmOn ? 'ask the local LLM' : s.s}</small></span>
          </button>))}</nav>
        <div className="prov-rail">
          <div className="synthetic">Synthetic OMOP data</div>
          <dl>
            <dt>Therapy A</dt><dd>adalimumab (TNFi)</dd>
            <dt>Model</dt><dd>OMOP CDM 5.4 + CRP/OOP</dd>
            <dt>Calibration</dt><dd>{k.d?.calibration_profile ?? '...'} / seed {k.d?.seed ?? ''}</dd>
            <dt>Study spec</dt><dd>{k.d?.study_name === 'default' ? 'default rules' : k.d?.study_name ?? '...'}</dd>
            <dt>Q&amp;A engine</dt><dd>{h.d ? (h.d.llm_enabled ? h.d.llm_model : 'deterministic router') : '...'}</dd>
          </dl>
        </div>
      </aside>
      <main className="page" key={tab === 0 ? 'study' : `${tab}:${ver}`}><Boundary>{view}</Boundary></main>
    </div>
  )
}
