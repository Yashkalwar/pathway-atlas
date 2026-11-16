import { useEffect, useRef } from 'react'
import type PlotlyType from 'plotly.js-dist-min'
import { plotLayout } from './theme'

type L = Partial<PlotlyType.Layout>

let plotlyPromise: Promise<typeof PlotlyType> | null = null
function loadPlotly(): Promise<typeof PlotlyType> {
  if (!plotlyPromise) {
    plotlyPromise = import('plotly.js-dist-min').then((m: unknown) => {
      const mod = m as { default?: typeof PlotlyType } & typeof PlotlyType
      return mod.default ?? mod
    })
  }
  return plotlyPromise
}

function merge(layout: L = {}): L {
  const base = plotLayout as L
  return {
    ...base, margin: { t: 16, r: 16, b: 48, l: 56 }, ...layout,
    xaxis: { ...base.xaxis, ...(layout.xaxis || {}) }, yaxis: { ...base.yaxis, ...(layout.yaxis || {}) },
  }
}

export default function Plot({ data, layout, height = 380 }: { data: PlotlyType.Data[]; layout?: L; height?: number }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    let cancelled = false
    const el = ref.current
    if (el) {
      loadPlotly().then(P => {
        if (!cancelled && ref.current) {
          P.react(ref.current, data, { height, ...merge(layout) }, { responsive: true, displaylogo: false, displayModeBar: false })
        }
      })
    }
    return () => { cancelled = true }
  }, [data, layout, height])
  useEffect(() => {
    const el = ref.current
    return () => {
      if (el && plotlyPromise) {
        plotlyPromise.then(P => P.purge(el))
      }
    }
  }, [])
  return <div ref={ref} style={{ width: '100%', minHeight: height }} />
}
