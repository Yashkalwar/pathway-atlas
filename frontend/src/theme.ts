export const C = {
  paper: '#F3EFE6', ink: '#1B1F2A', ink2: '#4A4F5C', rule: '#D8D1C2', muted: '#8A8272',
  remain: '#1F6F5C', disc: '#C8702A', switch: '#3A4FA3', hosp: '#A3283A', esc: '#7A5C99', neutral: '#5B6170',
}

export const SERIES = [C.switch, C.remain, C.disc, C.esc, C.hosp, C.neutral, '#2F8F9D', '#B08D3C']

export const DRUG_COLORS: Record<string, string> = {
  adalimumab: C.remain, etanercept: C.switch, tocilizumab: '#2F8F9D', tofacitinib: C.esc,
  abatacept: '#B08D3C', rituximab: C.neutral, methotrexate: '#9C9483', leflunomide: '#B9A27A', prednisone: C.disc,
}

export const plotLayout = {
  paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
  font: { family: '"IBM Plex Sans", system-ui, sans-serif', size: 12, color: C.ink2 },
  colorway: SERIES,
  xaxis: { gridcolor: '#E6E0D3', zerolinecolor: C.rule, linecolor: C.rule, tickfont: { family: '"IBM Plex Mono", monospace', size: 11 } },
  yaxis: { gridcolor: '#E6E0D3', zerolinecolor: C.rule, linecolor: C.rule, tickfont: { family: '"IBM Plex Mono", monospace', size: 11 } },
  legend: { orientation: 'h' as const, y: -0.18, font: { size: 12 } },
  hoverlabel: { bgcolor: C.ink, bordercolor: C.ink, font: { color: '#fff', family: '"IBM Plex Sans", sans-serif' } },
}
