import { useEffect, useRef } from 'react'
import Plotly from 'plotly.js-dist-min'
import { plotLayout } from './theme'

type L = Partial<Plotly.Layout>

function merge(layout: L = {}): L {
  const base = plotLayout as L
  return {
    ...base, margin: { t: 16, r: 16, b: 48, l: 56 }, ...layout,
    xaxis: { ...base.xaxis, ...(layout.xaxis || {}) }, yaxis: { ...base.yaxis, ...(layout.yaxis || {}) },
  }
}

export default function Plot({ data, layout, height = 380 }: { data: Plotly.Data[]; layout?: L; height?: number }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = ref.current
    if (el) Plotly.react(el, data, { height, ...merge(layout) }, { responsive: true, displaylogo: false, displayModeBar: false })
  }, [data, layout, height])
  useEffect(() => {
    const el = ref.current
    return () => { if (el) Plotly.purge(el) }
  }, [])
  return <div ref={ref} style={{ width: '100%' }} />
}
