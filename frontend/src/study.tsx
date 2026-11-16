/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useState } from 'react'
import { clearCache, fmt, post, useGet } from './api'
import { Figure, Loading, PageHead } from './components'

let lastApplied: any = null

function Field({ f, value, defaultValue, onChange }: any) {
  const mod = value !== defaultValue
  const input =
    f.type === 'bool' ? (
      <button type="button" className={`toggle ${value ? 'on' : ''}`} onClick={() => onChange(!value)} aria-pressed={value}>
        <i /><span>{value ? 'ON' : 'OFF'}</span>
      </button>
    ) : f.type === 'enum' ? (
      <select value={value} onChange={e => onChange(e.target.value)}>
        {f.options.map((o: string) => <option key={o} value={o}>{o === 'supply_end' ? 'Last supply end' : 'Expected refill date'}</option>)}
      </select>
    ) : (
      <span className="num-in">
        <input type="number" value={value} min={f.min} max={f.max} step={f.type === 'float' ? 0.05 : 1}
          onChange={e => onChange(f.type === 'float' ? parseFloat(e.target.value) : parseInt(e.target.value || '0', 10))} />
        {f.unit && <em>{f.unit}</em>}
      </span>
    )
  return (
    <div className={`field ${mod ? 'mod' : ''}`}>
      <div className="ftxt">
        <div className="flabel">{f.label}{mod && <span className="modtag">modified</span>}</div>
        <div className="fhelp">{f.help}</div>
      </div>
      {input}
    </div>
  )
}

export function StudyDesigner({ onApplied }: { onApplied: () => void }) {
  const s = useGet('/api/study')
  const [form, setForm] = useState<Record<string, any> | null>(null)
  const [name, setName] = useState('client-spec')
  const [busy, setBusy] = useState<'' | 'apply' | 'reset'>('')
  const [result, setResult] = useState<any>(lastApplied)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    if (s.d && form === null) {
      setForm({ ...s.d.defaults, ...(s.d.active?.rules || {}) })
      if (s.d.active?.name && s.d.active.name !== 'default') setName(s.d.active.name)
    }
  }, [s.d, form])
  if (!s.d || !form) return <Loading err={s.err} />

  const defaults = s.d.defaults
  const overrides = Object.fromEntries(Object.entries(form).filter(([k, v]) => v !== defaults[k]))
  const nMod = Object.keys(overrides).length

  const run = async (action: 'apply' | 'reset') => {
    setBusy(action); setErr(null)
    try {
      const r = action === 'apply'
        ? await post('/api/study', { name, rules: overrides })
        : await post('/api/study/reset', {})
      lastApplied = r
      setResult(r)
      clearCache()
      onApplied()
      setForm({ ...s.d.defaults, ...r.overrides })
    } catch (e) {
      setErr(String(e))
    } finally {
      setBusy('')
    }
  }

  const preset = (p: any) => { setForm({ ...defaults, ...p.rules }); setName(p.name) }

  return (<>
    <PageHead fig="Atlas 00 / Study design" title="Define the study — the cohort rebuilds itself."
      lede={<>These are the epidemiological definitions a client specifies in a pathway protocol. Applying them re-derives the index cohort, drug eras, lines and every downstream metric from the raw OMOP events — no re-simulation, same patients.</>} />

    <Figure label="Presets" caption="One-click sensitivity analyses: the same 10,000+ patients scored under different definitions.">
      <div className="presets">
        {s.d.presets.map((p: any) => (
          <button key={p.name} className="preset" onClick={() => preset(p)}>
            <b>{p.label}</b><span>{p.description}</span>
          </button>
        ))}
      </div>
    </Figure>

    <div className="protocol">
      {s.d.sections.map((sec: any) => (
        <section key={sec.id} className="psec">
          <header><h3>{sec.title}</h3><p>{sec.blurb}</p></header>
          {sec.fields.map((f: any) => (
            <Field key={f.key} f={f} value={form[f.key]} defaultValue={defaults[f.key]} onChange={(v: any) => setForm({ ...form, [f.key]: v })} />
          ))}
        </section>
      ))}
    </div>

    <div className="applybar">
      <div className="applyname">
        <label>Spec name<input value={name} onChange={e => setName(e.target.value)} /></label>
        <span className="mono">{nMod} parameter{nMod === 1 ? '' : 's'} differ from default</span>
      </div>
      <button className="primary" disabled={busy !== ''} onClick={() => run('apply')}>
        {busy === 'apply' ? 'Re-deriving cohort…' : 'Apply study design'}
      </button>
      <button className="ghost" disabled={busy !== ''} onClick={() => run('reset')}>
        {busy === 'reset' ? 'Resetting…' : 'Reset to defaults'}
      </button>
    </div>
    {err && <div className="formerr">Rejected: {err}</div>}

    {result && (
      <Figure label={`Applied spec “${result.name}” — cohort re-derived in ${result.seconds}s`} className="applied">
        <table className="ledger"><tbody>
          {(result.attrition || []).map((st: any) => {
            const [step, n] = Array.isArray(st) ? st : [st.step, st.n]
            return <tr key={step} className={String(step).startsWith('Excluded') ? 'excl' : ''}>
              <td>{step}</td><td className="num">{fmt(n)}</td>
            </tr>
          })}
        </tbody></table>
        <p className="caption">Every page in the atlas now reports this cohort. Patients, events and raw OMOP tables are unchanged — only the definitions changed.</p>
      </Figure>
    )}
  </>)
}
