import { useEffect, useState } from 'react'
import { Check, CircleAlert, LineChart, Loader2, Moon, Sun } from 'lucide-react'
import { api, ApiError } from './lib'
import type { JobView, Options, Report as ReportT } from './types'
import { Setup, type PickedFile } from './Setup'
import { Report } from './Report'

type Theme = 'light' | 'dark'

function systemTheme(): Theme {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function useTheme() {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      const saved = localStorage.getItem('theme')
      if (saved === 'light' || saved === 'dark') return saved
    } catch { /* storage unavailable */ }
    return systemTheme()
  })
  useEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])
  const toggle = () => {
    const next = theme === 'dark' ? 'light' : 'dark'
    setTheme(next)
    try { localStorage.setItem('theme', next) } catch { /* ignore */ }
  }
  return { theme, toggle }
}

export default function App() {
  const { theme, toggle } = useTheme()
  const [options, setOptions] = useState<Options | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [market, setMarket] = useState<string | null>(null)
  const [period, setPeriod] = useState<string | null>(null)
  const [picked, setPicked] = useState<PickedFile | null>(null)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<{ message: string; line: number | null } | null>(null)
  const [job, setJob] = useState<JobView | null>(null)
  const [report, setReport] = useState<ReportT | null>(null)

  useEffect(() => {
    api.options().then(setOptions).catch((e) => setLoadError(String(e.message ?? e)))
  }, [])

  useEffect(() => {
    if (!job || job.status !== 'running') return
    const t = setTimeout(async () => {
      try {
        const v = await api.job(job.id)
        if (v.status === 'running') setJob(v)
        else if (v.report && v.status === 'done') {
          setReport(v.report)
          setJob(null)
          window.scrollTo({ top: 0 })
        } else {
          const first = v.report?.instruments.find((i) => i.status === 'error')
          setError({ message: v.error ?? 'The backtest failed.', line: first?.line ?? null })
          setJob(null)
        }
      } catch (e) {
        setError({ message: e instanceof Error ? e.message : String(e), line: null })
        setJob(null)
      }
    }, 600)
    return () => clearTimeout(t)
  }, [job])

  async function start() {
    if (!market || !period || !picked) return
    setStarting(true)
    setError(null)
    try {
      const r = await api.start(market, period, picked.file)
      setJob({ id: r.id, status: 'running', error: null, progress: [], report: null })
    } catch (e) {
      setError(e instanceof ApiError ? { message: e.message, line: e.line } : { message: String(e), line: null })
    } finally {
      setStarting(false)
    }
  }

  return (
    <div className="min-h-full">
      <header className="no-print sticky top-0 z-10 border-b border-line bg-page/85 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-[1180px] items-center gap-2.5 px-4">
          <button type="button" onClick={() => { setReport(null); setJob(null) }} className="flex items-center gap-2.5">
            <span className="grid size-8 place-items-center rounded-lg bg-accent text-accent-ink"><LineChart size={17} /></span>
            <span className="text-[15px] font-semibold">Strategy Backtester</span>
          </button>
          <button type="button" onClick={toggle} aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
            className="ml-auto grid size-9 place-items-center rounded-lg text-ink-2 hover:bg-surface-2">
            {theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}
          </button>
        </div>
      </header>

      {loadError && (
        <div className="mx-auto mt-10 max-w-[560px] px-4">
          <div className="flex gap-3 rounded-xl border border-loss/40 bg-loss-soft p-4">
            <CircleAlert size={18} className="mt-0.5 text-loss" />
            <div><div className="font-semibold">Can't reach the backtesting server</div><div className="text-ink-2">{loadError}. Start it with <code>make dev</code>.</div></div>
          </div>
        </div>
      )}

      {options && !report && !job && (
        <Setup options={options} market={market} period={period} picked={picked}
          onMarket={setMarket} onPeriod={setPeriod} onFile={(f) => { setPicked(f); setError(null) }}
          onStart={start} starting={starting} error={error} />
      )}

      {options && job && <Running job={job} options={options} market={market} />}

      {report && <Report report={report} themeKey={theme} onNew={() => { setReport(null); window.scrollTo({ top: 0 }) }} />}
    </div>
  )
}

const STATUS_LABEL = { queued: 'Waiting', downloading: 'Downloading prices', testing: 'Running strategy', done: 'Done', error: 'Failed' }

function Running({ job, options, market }: { job: JobView; options: Options; market: string | null }) {
  const m = options.markets.find((x) => x.id === market)
  const rows = job.progress.length ? job.progress : (m?.instruments ?? []).map((i) => ({ ...i, status: 'queued' as const }))
  const done = rows.filter((r) => r.status === 'done' || r.status === 'error').length
  return (
    <div className="mx-auto max-w-[560px] px-4 py-16">
      <div className="rounded-2xl border border-line bg-surface p-6 shadow-card">
        <div className="flex items-center gap-3">
          <Loader2 size={20} className="animate-spin text-accent" />
          <h2 className="text-[16px] font-semibold">Backtesting on {m?.label ?? 'the market'}…</h2>
          <span className="tnum ml-auto text-[13px] text-muted">{done} / {rows.length}</span>
        </div>
        <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-surface-2" role="progressbar" aria-valuenow={done} aria-valuemax={rows.length}>
          <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${(100 * done) / Math.max(1, rows.length)}%` }} />
        </div>
        <ul className="mt-5 space-y-1">
          {rows.map((r) => (
            <li key={r.symbol} className="flex items-center gap-3 rounded-lg px-1 py-1.5">
              <span className="grid size-5 place-items-center">
                {r.status === 'done' ? <Check size={16} className="text-gain" strokeWidth={3} />
                  : r.status === 'error' ? <CircleAlert size={16} className="text-loss" />
                  : r.status === 'queued' ? <span className="size-2 rounded-full bg-line-strong" />
                  : <Loader2 size={15} className="animate-spin text-accent" />}
              </span>
              <span className="font-medium">{r.name}</span>
              <span className="text-[12px] text-muted">{r.symbol}</span>
              <span className="ml-auto text-[12.5px] text-ink-2">{STATUS_LABEL[r.status]}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}
