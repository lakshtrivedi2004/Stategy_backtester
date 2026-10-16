import { useEffect, useRef } from 'react'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent, DataZoomComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsCoreOption } from 'echarts/core'
import { cssVar, money } from './lib'

echarts.use([LineChart, GridComponent, LegendComponent, TooltipComponent, DataZoomComponent, CanvasRenderer])

function useChart(option: () => EChartsCoreOption, deps: unknown[]) {
  const el = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!el.current) return
    const chart = echarts.init(el.current, undefined, { renderer: 'canvas' })
    chart.setOption(option())
    const ro = new ResizeObserver(() => chart.resize())
    ro.observe(el.current)
    return () => {
      ro.disconnect()
      chart.dispose()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return el
}

function chrome() {
  return {
    ink: cssVar('--ink'),
    ink2: cssVar('--ink-2'),
    muted: cssVar('--muted'),
    grid: cssVar('--grid'),
    axis: cssVar('--axis'),
    surface: cssVar('--surface'),
    line: cssVar('--line'),
  }
}

function fmtDate(ms: number, intraday: boolean, tz: string) {
  const d = new Date(ms)
  const opts: Intl.DateTimeFormatOptions = intraday
    ? { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: tz }
    : { year: 'numeric', month: 'short', day: 'numeric', timeZone: tz }
  return d.toLocaleString('en-GB', opts)
}

function tooltipBase(c: ReturnType<typeof chrome>) {
  return {
    trigger: 'axis' as const,
    axisPointer: { type: 'line' as const, lineStyle: { color: c.axis, width: 1 } },
    backgroundColor: c.surface,
    borderColor: c.line,
    borderWidth: 1,
    padding: [8, 10],
    textStyle: { color: c.ink, fontSize: 12 },
    extraCssText: 'box-shadow: 0 4px 16px rgba(0,0,0,.12); border-radius: 8px;',
  }
}

function axes(c: ReturnType<typeof chrome>, intraday: boolean, tz: string, yFormat: (v: number) => string, t: number[]) {
  const shortSpan = t.length > 1 && t[t.length - 1] - t[0] < 8 * 86_400_000
  return {
    xAxis: {
      type: 'time' as const,
      axisLine: { lineStyle: { color: c.axis } },
      axisTick: { show: false },
      axisLabel: { color: c.muted, fontSize: 11, hideOverlap: true,
        formatter: (v: number) => fmtDate(v, intraday && shortSpan, tz) },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value' as const,
      scale: true,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: c.muted, fontSize: 11, formatter: yFormat },
      splitLine: { lineStyle: { color: c.grid, width: 1 } },
    },
  }
}

const esc = (s: string) => s.replace(/[&<>"]/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[ch]!)

interface CurveProps {
  t: number[]
  equity: number[]
  buyHold: number[]
  currency: string
  intraday: boolean
  tz: string
  themeKey: string
}

export function EquityChart({ t, equity, buyHold, currency, intraday, tz, themeKey }: CurveProps) {
  const el = useChart(() => {
    const c = chrome()
    const blue = cssVar('--series-1')
    const gray = cssVar('--reference')
    const fmt = (v: number) => money(v, currency, Math.abs(v) >= 1000 ? 0 : 2)
    return {
      animation: false,
      grid: { left: 8, right: 96, top: 36, bottom: 8, containLabel: true },
      legend: {
        top: 0, left: 0, icon: 'path://M0,4 L24,4 L24,6 L0,6 Z', itemWidth: 18, itemHeight: 3,
        textStyle: { color: c.ink2, fontSize: 12 }, data: ['Strategy', 'Buy & hold'],
      },
      tooltip: {
        ...tooltipBase(c),
        formatter: (ps: { value: [number, number]; seriesName: string; color: string }[]) => {
          if (!ps.length) return ''
          const head = `<div style="color:${c.muted};font-size:11px;margin-bottom:4px">${esc(fmtDate(ps[0].value[0], intraday, tz))}</div>`
          return head + ps.map((p) =>
            `<div style="display:flex;align-items:center;gap:8px;line-height:1.6">` +
            `<span style="display:inline-block;width:12px;height:2px;background:${p.color}"></span>` +
            `<b style="font-variant-numeric:tabular-nums">${esc(fmt(p.value[1]))}</b>` +
            `<span style="color:${c.ink2}">${esc(p.seriesName)}</span></div>`).join('')
        },
      },
      ...axes(c, intraday, tz, fmt, t),
      series: [
        { name: 'Buy & hold', type: 'line', showSymbol: false, data: t.map((x, i) => [x, buyHold[i]]),
          lineStyle: { width: 1.5, color: gray }, itemStyle: { color: gray }, z: 1,
          endLabel: { show: true, formatter: 'Buy & hold', color: c.ink2, fontSize: 11 } },
        { name: 'Strategy', type: 'line', showSymbol: false, data: t.map((x, i) => [x, equity[i]]),
          lineStyle: { width: 2, color: blue }, itemStyle: { color: blue }, z: 2,
          endLabel: { show: true, formatter: 'Strategy', color: c.ink, fontSize: 11, fontWeight: 600 } },
      ],
    }
  }, [t, equity, buyHold, themeKey])
  return <div ref={el} className="h-[300px] w-full" role="img" aria-label="Equity curve of the strategy and buy and hold" />
}

export function DrawdownChart({ t, dd, intraday, tz, themeKey }: { t: number[]; dd: number[]; intraday: boolean; tz: string; themeKey: string }) {
  const el = useChart(() => {
    const c = chrome()
    const red = cssVar('--drawdown')
    const fmt = (v: number) => `${v.toFixed(Math.abs(v) < 10 ? 1 : 0)}%`
    return {
      animation: false,
      grid: { left: 8, right: 96, top: 8, bottom: 8, containLabel: true },
      tooltip: {
        ...tooltipBase(c),
        formatter: (ps: { value: [number, number] }[]) => ps.length
          ? `<div style="color:${c.muted};font-size:11px;margin-bottom:4px">${esc(fmtDate(ps[0].value[0], intraday, tz))}</div>` +
            `<b style="font-variant-numeric:tabular-nums">${ps[0].value[1].toFixed(2)}%</b> <span style="color:${c.ink2}">below peak</span>`
          : '',
      },
      ...axes(c, intraday, tz, fmt, t),
      yAxis: { ...axes(c, intraday, tz, fmt, t).yAxis, scale: false, max: 0, splitNumber: 3 },
      series: [{
        name: 'Drawdown', type: 'line', showSymbol: false, data: t.map((x, i) => [x, dd[i]]),
        lineStyle: { width: 1.5, color: red }, itemStyle: { color: red },
        areaStyle: { color: red, opacity: 0.14 },
      }],
    }
  }, [t, dd, themeKey])
  return <div ref={el} className="h-[150px] w-full" role="img" aria-label="Drawdown from peak equity" />
}
