import { useRef, useState } from 'react'
import { Bitcoin, Check, ChevronDown, CircleAlert, Download, FileCode2, IndianRupee, Play, Upload, X, Euro } from 'lucide-react'
import type { Options } from './types'
import { detectLanguage } from './lib'

export interface PickedFile {
  file: File
  text: string
  language: 'Python' | 'Pine Script' | null
}

interface Props {
  options: Options
  market: string | null
  period: string | null
  picked: PickedFile | null
  onMarket: (id: string) => void
  onPeriod: (id: string) => void
  onFile: (f: PickedFile | null) => void
  onStart: () => void
  starting: boolean
  error: { message: string; line: number | null } | null
}

const MARKET_ICON: Record<string, typeof Euro> = { FOREX: Euro, CRYPTO: Bitcoin, INDIA: IndianRupee }

function Step({ n, title, done, children, hint }: { n: number; title: string; done: boolean; hint?: string; children: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-line bg-surface p-5 shadow-card sm:p-6">
      <div className="mb-4 flex items-center gap-3">
        <span className={`grid size-7 shrink-0 place-items-center rounded-full text-[13px] font-semibold ${done ? 'bg-accent text-accent-ink' : 'bg-surface-2 text-ink-2'}`}>
          {done ? <Check size={15} strokeWidth={3} /> : n}
        </span>
        <h2 className="text-[15px] font-semibold">{title}</h2>
        {hint && <span className="ml-auto text-[12px] text-muted">{hint}</span>}
      </div>
      {children}
    </section>
  )
}

export function Setup({ options, market, period, picked, onMarket, onPeriod, onFile, onStart, starting, error }: Props) {
  const input = useRef<HTMLInputElement>(null)
  const [drag, setDrag] = useState(false)
  const [showCode, setShowCode] = useState(false)
  const [showHelp, setShowHelp] = useState(false)
  const selectedPeriod = options.periods.find((p) => p.id === period)

  async function take(file: File | undefined) {
    if (!file) return
    const text = await file.text()
    onFile({ file, text, language: detectLanguage(file.name, text) })
    setShowCode(false)
  }

  const ready = market && period && picked

  return (
    <div className="mx-auto flex max-w-[880px] flex-col gap-4 px-4 py-8 sm:py-12">
      <header className="mb-2">
        <h1 className="text-[26px] font-semibold tracking-tight">Backtest a trading strategy</h1>
        <p className="mt-1 text-ink-2">Pick a market and how much history to test on, upload your strategy, and get a full performance report.</p>
      </header>

      <Step n={1} title="Market" done={!!market}>
        <div className="grid gap-3 sm:grid-cols-3">
          {options.markets.map((m) => {
            const Icon = MARKET_ICON[m.id] ?? Euro
            const on = market === m.id
            return (
              <button key={m.id} type="button" onClick={() => onMarket(m.id)} aria-pressed={on}
                className={`group flex flex-col items-start gap-2 rounded-xl border p-4 text-left transition ${on ? 'border-accent bg-accent-soft ring-1 ring-accent' : 'border-line hover:border-line-strong hover:bg-surface-2'}`}>
                <span className={`grid size-9 place-items-center rounded-lg ${on ? 'bg-accent text-accent-ink' : 'bg-surface-2 text-ink-2'}`}><Icon size={18} /></span>
                <span className="text-[15px] font-semibold">{m.label}</span>
                <span className="text-[12.5px] leading-snug text-ink-2">{m.description}</span>
                <span className="mt-1 flex flex-wrap gap-1">
                  {m.instruments.map((i) => (
                    <span key={i.symbol} className="rounded-md bg-surface-2 px-1.5 py-0.5 text-[11px] text-ink-2">{i.name}</span>
                  ))}
                </span>
              </button>
            )
          })}
        </div>
      </Step>

      <Step n={2} title="History to test" done={!!period}
        hint={selectedPeriod ? `Tested on ${selectedPeriod.interval_label} bars` : undefined}>
        <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="History period">
          {options.periods.map((p) => {
            const on = period === p.id
            return (
              <button key={p.id} type="button" role="radio" aria-checked={on} onClick={() => onPeriod(p.id)} title={p.label}
                className={`min-w-[56px] rounded-lg border px-3.5 py-2 text-[14px] font-semibold transition ${on ? 'border-accent bg-accent text-accent-ink' : 'border-line hover:border-line-strong hover:bg-surface-2'}`}>
                {p.id}
              </button>
            )
          })}
        </div>
      </Step>

      <Step n={3} title="Strategy file" done={!!picked} hint="Python (.py) or Pine Script (.pine)">
        <input ref={input} type="file" accept=".py,.pine,.txt,.pinescript" className="hidden"
          onChange={(e) => { take(e.target.files?.[0]); e.target.value = '' }} />
        {!picked ? (
          <button type="button" onClick={() => input.current?.click()}
            onDragOver={(e) => { e.preventDefault(); setDrag(true) }} onDragLeave={() => setDrag(false)}
            onDrop={(e) => { e.preventDefault(); setDrag(false); take(e.dataTransfer.files?.[0]) }}
            className={`flex w-full flex-col items-center gap-2 rounded-xl border-2 border-dashed px-4 py-9 text-center transition ${drag ? 'border-accent bg-accent-soft' : 'border-line-strong hover:border-accent hover:bg-surface-2'}`}>
            <Upload size={22} className="text-ink-2" />
            <span className="font-semibold">Drop your strategy here, or click to choose a file</span>
            <span className="text-[12.5px] text-muted">A TradingView Pine Script strategy, or a Python file with generate_signals(data)</span>
          </button>
        ) : (
          <div className="rounded-xl border border-line">
            <div className="flex items-center gap-3 p-3.5">
              <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-surface-2 text-ink-2"><FileCode2 size={18} /></span>
              <div className="min-w-0 flex-1">
                <div className="truncate font-semibold">{picked.file.name}</div>
                <div className="text-[12px] text-muted">
                  {picked.language ?? 'Unknown language'} · {picked.text.split('\n').length} lines · {(picked.file.size / 1024).toFixed(1)} KB
                </div>
              </div>
              <button type="button" onClick={() => setShowCode((v) => !v)} className="hidden rounded-lg px-2.5 py-1.5 text-[13px] text-ink-2 hover:bg-surface-2 sm:block">
                {showCode ? 'Hide code' : 'View code'}
              </button>
              <button type="button" onClick={() => input.current?.click()} className="rounded-lg px-2.5 py-1.5 text-[13px] text-ink-2 hover:bg-surface-2">Change</button>
              <button type="button" onClick={() => onFile(null)} aria-label="Remove file" className="rounded-lg p-1.5 text-muted hover:bg-surface-2 hover:text-ink"><X size={16} /></button>
            </div>
            {showCode && (
              <pre className="max-h-[320px] overflow-auto border-t border-line bg-surface-2 p-3.5 font-mono text-[12px] leading-relaxed text-ink-2">{picked.text}</pre>
            )}
          </div>
        )}

        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[12.5px] text-muted">
          <span>Examples:</span>
          {options.examples.map((e) => (
            <a key={e.filename} href={`/api/examples/${e.filename}`} download className="inline-flex items-center gap-1 text-accent hover:underline">
              <Download size={12} />{e.filename}
            </a>
          ))}
          <button type="button" onClick={() => setShowHelp((v) => !v)} className="ml-auto inline-flex items-center gap-1 text-ink-2 hover:text-ink">
            How to write a strategy <ChevronDown size={14} className={showHelp ? 'rotate-180' : ''} />
          </button>
        </div>
        {showHelp && <Help />}
      </Step>

      {error && (
        <div role="alert" className="flex gap-3 rounded-xl border border-loss/40 bg-loss-soft p-4 text-[13.5px]">
          <CircleAlert size={18} className="mt-0.5 shrink-0 text-loss" />
          <div>
            <div className="font-semibold">The strategy can't be run{error.line ? ` (line ${error.line})` : ''}</div>
            <div className="mt-0.5 text-ink-2">{error.message}</div>
          </div>
        </div>
      )}

      <div className="flex flex-col items-stretch gap-2 sm:flex-row sm:items-center sm:justify-end">
        {!ready && <span className="text-[12.5px] text-muted sm:mr-auto">Choose a market, a history period and a strategy file to start.</span>}
        <button type="button" disabled={!ready || starting} onClick={onStart}
          className="inline-flex items-center justify-center gap-2 rounded-xl bg-accent px-6 py-3 text-[15px] font-semibold text-accent-ink shadow-card transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40">
          <Play size={16} fill="currentColor" /> {starting ? 'Starting…' : 'Start backtest'}
        </button>
      </div>
    </div>
  )
}

function Help() {
  const code = 'rounded bg-surface-2 px-1 py-0.5 font-mono text-[11.5px]'
  return (
    <div className="mt-3 grid gap-4 rounded-xl bg-surface-2 p-4 text-[13px] leading-relaxed text-ink-2 sm:grid-cols-2">
      <div>
        <div className="mb-1 font-semibold text-ink">Pine Script</div>
        Upload a TradingView strategy as it is (v4, v5 or v6). It must declare <span className={code}>strategy(…)</span> and place
        orders with <span className={code}>strategy.entry</span>, <span className={code}>strategy.exit</span> or <span className={code}>strategy.close</span>.
        Inputs use their default values. Plots and alerts are ignored. Arrays and libraries aren't supported yet.
      </div>
      <div>
        <div className="mb-1 font-semibold text-ink">Python</div>
        Define <span className={code}>generate_signals(data)</span>. <span className={code}>data</span> is a DataFrame with
        <span className={code}>open high low close volume</span>. Return a Series of the position you want (1 long, −1 short, 0 flat),
        or a DataFrame with a <span className={code}>signal</span> column and optional <span className={code}>stop_loss</span> / <span className={code}>take_profit</span> prices.
      </div>
      <div className="sm:col-span-2 text-[12.5px] text-muted">
        Each market is tested on 5 instruments. Signals are computed on a bar's close and filled at the next bar's open, with realistic fees and slippage.
      </div>
    </div>
  )
}
