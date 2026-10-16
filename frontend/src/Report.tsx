import { useMemo, useState } from 'react'
import { ArrowLeft, CircleAlert, Download, Printer, TriangleAlert } from 'lucide-react'
import type { InstrumentResult, Report as ReportT } from './types'
import { download, money, num, pct, price, tone, tradesCsv } from './lib'
import { DrawdownChart, EquityChart } from './charts'

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function Card({ title, children, className = '', aside }: { title?: string; children: React.ReactNode; className?: string; aside?: React.ReactNode }) {
  return (
    <section className={`rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5 ${className}`}>
      {title && (
        <div className="mb-3 flex items-center gap-2">
          <h3 className="text-[14px] font-semibold">{title}</h3>
          {aside && <div className="ml-auto">{aside}</div>}
        </div>
      )}
      {children}
    </section>
  )
}

function Stat({ label, value, sub, valueClass = '' }: { label: string; value: string; sub?: string; valueClass?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[12px] text-muted">{label}</div>
      <div className={`mt-0.5 truncate text-[20px] font-semibold leading-tight ${valueClass}`}>{value}</div>
      {sub && <div className="mt-0.5 truncate text-[12px] text-ink-2">{sub}</div>}
    </div>
  )
}

function Row({ label, value, valueClass = '' }: { label: string; value: string; valueClass?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-line py-1.5 last:border-0">
      <span className="text-ink-2">{label}</span>
      <span className={`tnum text-right font-medium ${valueClass}`}>{value}</span>
    </div>
  )
}

export function Report({ report, themeKey, onNew }: { report: ReportT; themeKey: string; onNew: () => void }) {
  const ok = report.instruments.filter((i) => i.status === 'ok')
  const [sel, setSel] = useState(ok[0]?.symbol ?? report.instruments[0]?.symbol)
  const s = report.summary
  const current = report.instruments.find((i) => i.symbol === sel)
  const intraday = report.period.interval !== '1d'
  const created = new Date(report.created_at).toLocaleString()

  return (
    <div className="mx-auto flex max-w-[1180px] flex-col gap-4 px-4 py-6 sm:py-8">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
        <div className="min-w-0 flex-1">
          <div className="text-[12.5px] text-muted">Backtest report · {created}</div>
          <h1 className="mt-0.5 text-[24px] font-semibold tracking-tight">{report.strategy.name}</h1>
          <div className="mt-1 flex flex-wrap gap-1.5 text-[12.5px]">
            {[report.strategy.language, report.market.label, `${report.period.label} history`, `${report.period.interval_label} bars`, report.strategy.filename].map((t) => (
              <span key={t} className="rounded-md bg-surface-2 px-2 py-0.5 text-ink-2">{t}</span>
            ))}
          </div>
        </div>
        <div className="no-print flex gap-2">
          <button type="button" onClick={() => window.print()} className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-2 text-[13px] hover:bg-surface-2">
            <Printer size={15} /> Print / PDF
          </button>
          <button type="button" onClick={onNew} className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3.5 py-2 text-[13px] font-semibold text-accent-ink hover:brightness-110">
            <ArrowLeft size={15} /> New backtest
          </button>
        </div>
      </div>

      <Card>
        <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 lg:grid-cols-6">
          <Stat label="Average return" value={pct(s.avg_return_pct)} valueClass={tone(s.avg_return_pct)}
            sub={`Buy & hold ${pct(s.avg_buy_hold_pct)}`} />
          <Stat label="Profitable" value={`${s.profitable} of ${s.instruments_tested}`} sub="instruments" />
          <Stat label="Beat buy & hold" value={`${s.beat_buy_hold} of ${s.instruments_tested}`} sub="instruments" />
          <Stat label="Win rate" value={pct(s.win_rate, 1, false)} sub={`${s.total_trades.toLocaleString()} trades in total`} />
          <Stat label="Avg max drawdown" value={pct(s.avg_max_drawdown_pct == null ? null : -s.avg_max_drawdown_pct, 1)} valueClass="text-loss" />
          <Stat label="Best / worst" value={s.best ? s.best.name : '–'} valueClass="text-[16px]"
            sub={s.best && s.worst ? `${pct(s.best.return_pct, 1)} / ${s.worst.name} ${pct(s.worst.return_pct, 1)}` : undefined} />
        </div>
      </Card>

      <Card title="Results by instrument" aside={<span className="hidden text-[12px] text-muted sm:inline">Select a row for its full report</span>}>
        <div className="-mx-4 overflow-x-auto sm:-mx-5">
          <table className="tnum w-full min-w-[720px] text-[13px]">
            <thead>
              <tr className="border-b border-line text-left text-[12px] text-muted">
                <th className="py-2 pl-4 font-medium sm:pl-5">Instrument</th>
                {['Net profit', 'Return', 'Buy & hold', 'Trades', 'Win rate', 'Profit factor', 'Max drawdown', 'Sharpe'].map((h) => (
                  <th key={h} className="px-3 py-2 text-right font-medium last:pr-4 sm:last:pr-5">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {report.instruments.map((r) => {
                const on = r.symbol === sel
                if (r.status !== 'ok') {
                  return (
                    <tr key={r.symbol} className="border-b border-line last:border-0">
                      <td className="py-2.5 pl-4 sm:pl-5"><div className="font-medium">{r.name}</div><div className="text-[11.5px] text-muted">{r.symbol}</div></td>
                      <td colSpan={8} className="px-3 py-2.5 pr-5 text-[12.5px] text-loss">
                        <CircleAlert size={13} className="mr-1 inline" />{r.error}{r.line ? ` (line ${r.line})` : ''}
                      </td>
                    </tr>
                  )
                }
                const m = r.metrics
                const sym = r.settings.currency_symbol
                return (
                  <tr key={r.symbol} onClick={() => setSel(r.symbol)} tabIndex={0} aria-selected={on}
                    onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && setSel(r.symbol)}
                    className={`cursor-pointer border-b border-line last:border-0 ${on ? 'bg-accent-soft' : 'hover:bg-surface-2'}`}>
                    <td className={`py-2.5 pl-4 sm:pl-5 ${on ? 'shadow-[inset_3px_0_0_var(--accent)]' : ''}`}>
                      <div className="font-medium">{r.name}</div><div className="text-[11.5px] text-muted">{r.symbol}</div>
                    </td>
                    <td className={`px-3 text-right ${tone(m.net_profit)}`}>{money(m.net_profit, sym, 0)}</td>
                    <td className={`px-3 text-right font-semibold ${tone(m.net_profit_pct)}`}>{pct(m.net_profit_pct)}</td>
                    <td className="px-3 text-right text-ink-2">{pct(m.buy_hold_pct)}</td>
                    <td className="px-3 text-right">{m.total_trades}</td>
                    <td className="px-3 text-right">{pct(m.win_rate, 1, false)}</td>
                    <td className="px-3 text-right">{num(m.profit_factor)}</td>
                    <td className="px-3 text-right text-loss">{pct(-m.max_drawdown_pct, 1)}</td>
                    <td className="px-3 pr-4 text-right sm:pr-5">{num(m.sharpe)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </Card>

      {current && current.status === 'ok' && (
        <InstrumentDetail key={current.symbol} r={current} report={report} intraday={intraday} themeKey={themeKey} />
      )}

      <p className="text-center text-[12px] text-muted">
        Hypothetical results on historical data from Yahoo Finance. Past performance does not guarantee future results.
      </p>
    </div>
  )
}

function InstrumentDetail({ r, report, intraday, themeKey }: { r: InstrumentResult; report: ReportT; intraday: boolean; themeKey: string }) {
  const m = r.metrics
  const sym = r.settings.currency_symbol
  const tz = report.market.id === 'INDIA' ? 'Asia/Kolkata' : 'UTC'
  const inputs = Object.entries(r.inputs)

  return (
    <>
      <div className="mt-2 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="text-[19px] font-semibold">{r.name}</h2>
        <span className="text-[13px] text-muted">{r.symbol} · {r.window.start} → {r.window.end} · {r.window.bars.toLocaleString()} bars</span>
      </div>

      {r.warnings.length > 0 && (
        <div className="flex gap-3 rounded-xl border border-warn-ink/30 bg-warn-soft p-3.5 text-[13px]">
          <TriangleAlert size={17} className="mt-0.5 shrink-0 text-warn-ink" />
          <ul className="space-y-0.5"><li className="font-semibold">Check these before trusting the results</li>
            {r.warnings.map((w) => <li key={w} className="text-ink-2">{w}</li>)}</ul>
        </div>
      )}

      <Card>
        <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 lg:grid-cols-6">
          <Stat label="Net profit" value={money(m.net_profit, sym, 0)} valueClass={tone(m.net_profit)} sub={pct(m.net_profit_pct)} />
          <Stat label="Buy & hold" value={pct(m.buy_hold_pct)} sub={m.annual_return_pct != null ? `Strategy ${pct(m.annual_return_pct, 1)} a year` : 'same period'} />
          <Stat label="Max drawdown" value={pct(-m.max_drawdown_pct, 1)} valueClass="text-loss" sub={money(-m.max_drawdown, sym, 0)} />
          <Stat label="Trades" value={String(m.total_trades)} sub={`${pct(m.win_rate, 1, false)} winners`} />
          <Stat label="Profit factor" value={num(m.profit_factor)} sub={`Avg trade ${money(m.avg_trade, sym)}`} />
          <Stat label="Sharpe ratio" value={num(m.sharpe)} sub={`Sortino ${num(m.sortino)}`} />
        </div>
      </Card>

      <Card title="Equity">
        <EquityChart t={r.curve.t} equity={r.curve.equity} buyHold={r.curve.buy_hold} currency={sym} intraday={intraday} tz={tz} themeKey={themeKey} />
        <div className="mt-4 mb-1 text-[12.5px] font-medium text-ink-2">Drawdown from peak</div>
        <DrawdownChart t={r.curve.t} dd={r.curve.drawdown_pct} intraday={intraday} tz={tz} themeKey={themeKey} />
      </Card>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Trade analysis">
          <Row label="Winning / losing trades" value={`${m.winning_trades} / ${m.losing_trades}`} />
          <Row label="Average win" value={money(m.avg_win, sym)} valueClass="text-gain" />
          <Row label="Average loss" value={money(m.avg_loss, sym)} valueClass="text-loss" />
          <Row label="Win / loss size ratio" value={num(m.win_loss_ratio)} />
          <Row label="Largest win" value={money(m.largest_win, sym)} valueClass="text-gain" />
          <Row label="Largest loss" value={money(m.largest_loss, sym)} valueClass="text-loss" />
          <Row label="Most wins / losses in a row" value={`${m.max_consecutive_wins} / ${m.max_consecutive_losses}`} />
          <Row label="Average bars in a trade" value={num(m.avg_bars_in_trade, 1)} />
        </Card>
        <Card title="Returns & risk">
          <Row label="Starting capital" value={money(m.initial_capital, sym, 0)} />
          <Row label="Final equity" value={money(m.final_equity, sym, 0)} />
          <Row label="Gross profit / loss" value={`${money(m.gross_profit, sym, 0)} / ${money(-m.gross_loss, sym, 0)}`} />
          <Row label="Net profit ÷ max drawdown" value={num(m.return_over_max_dd)} />
          <Row label="Annualized return" value={pct(m.annual_return_pct)} />
          <Row label="Time in market" value={pct(m.exposure_pct, 1, false)} />
          <Row label="Fees & charges paid" value={money(m.commission_paid, sym)} />
          <Row label="Long / short trades" value={`${r.long.trades} / ${r.short.trades}`} />
        </Card>
        <Card title="Long vs short">
          <div className="grid grid-cols-2 gap-4">
            {([['Long', r.long], ['Short', r.short]] as const).map(([label, side]) => (
              <div key={label}>
                <div className="mb-1 text-[12px] font-semibold text-ink-2">{label}</div>
                <Row label="Trades" value={String(side.trades)} />
                <Row label="Win rate" value={pct(side.win_rate, 1, false)} />
                <Row label="Net" value={money(side.net_profit, sym, 0)} valueClass={tone(side.net_profit)} />
              </div>
            ))}
          </div>
          <div className="mt-4 mb-1 text-[12px] font-semibold text-ink-2">Test settings</div>
          <Row label="Position size" value={r.settings.sizing} />
          <Row label="Fees" value={r.settings.costs} />
          <div className="mt-1 text-[12px] leading-snug text-muted">{r.settings.fills}. Indicators were warmed up on {r.window.warmup_bars} earlier bars.</div>
        </Card>
      </div>

      {inputs.length > 0 && (
        <Card title="Strategy inputs used">
          <div className="flex flex-wrap gap-2">
            {inputs.map(([k, v]) => (
              <span key={k} className="rounded-lg bg-surface-2 px-2.5 py-1 text-[12.5px]"><span className="text-muted">{k}</span> <span className="font-medium">{String(v ?? '–')}</span></span>
            ))}
          </div>
        </Card>
      )}

      {r.monthly.length > 1 && <Monthly rows={r.monthly} />}

      <Trades r={r} report={report} />
    </>
  )
}

function Monthly({ rows }: { rows: InstrumentResult['monthly'] }) {
  const years = [...new Set(rows.map((r) => r.year))]
  const max = Math.max(...rows.map((r) => Math.abs(r.return_pct ?? 0)), 0.01)
  const cell = (v: number | null | undefined) => {
    if (v == null) return {}
    const a = Math.min(1, Math.abs(v) / max)
    const pole = v >= 0 ? 'var(--series-1)' : 'var(--drawdown)'
    return { background: `color-mix(in oklab, ${pole} ${Math.round(10 + a * 50)}%, var(--surface-2))` }
  }
  const yearTotal = (y: number) => rows.filter((r) => r.year === y).reduce((acc, r) => acc * (1 + (r.return_pct ?? 0) / 100), 1) * 100 - 100

  return (
    <Card title="Monthly returns" aside={<span className="text-[12px] text-muted">Blue = gain, red = loss; darker = larger</span>}>
      <div className="-mx-4 overflow-x-auto px-4 sm:-mx-5 sm:px-5">
        <table className="tnum w-full min-w-[760px] border-separate border-spacing-[2px] text-[12px]">
          <thead>
            <tr className="text-muted">
              <th className="w-14 text-left font-medium">Year</th>
              {MONTHS.map((mo) => <th key={mo} className="font-medium">{mo}</th>)}
              <th className="font-medium">Year</th>
            </tr>
          </thead>
          <tbody>
            {years.map((y) => (
              <tr key={y}>
                <td className="font-medium text-ink-2">{y}</td>
                {MONTHS.map((mo, k) => {
                  const r = rows.find((x) => x.year === y && x.month === k + 1)
                  return (
                    <td key={mo} style={cell(r?.return_pct)} title={r ? `${mo} ${y}: ${pct(r.return_pct)}` : undefined}
                      className="rounded-[4px] px-1 py-1.5 text-center text-ink">{r ? pct(r.return_pct, 1) : ''}</td>
                  )
                })}
                <td className={`px-1 text-center font-semibold ${tone(yearTotal(y))}`}>{pct(yearTotal(y), 1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

const PAGE = 50

function Trades({ r, report }: { r: InstrumentResult; report: ReportT }) {
  const [page, setPage] = useState(0)
  const [filter, setFilter] = useState<'all' | 'win' | 'loss'>('all')
  const sym = r.settings.currency_symbol
  const rows = useMemo(() => {
    const t = [...r.trades].reverse()
    return filter === 'all' ? t : t.filter((x) => (filter === 'win' ? x.pnl > 0 : x.pnl <= 0))
  }, [r.trades, filter])
  const pages = Math.max(1, Math.ceil(rows.length / PAGE))
  const shown = rows.slice(page * PAGE, page * PAGE + PAGE)

  return (
    <Card title={`Trades (${r.metrics.total_trades})`} aside={
      <div className="no-print flex items-center gap-2">
        <div className="flex rounded-lg border border-line p-0.5 text-[12px]">
          {(['all', 'win', 'loss'] as const).map((f) => (
            <button key={f} type="button" onClick={() => { setFilter(f); setPage(0) }}
              className={`rounded-md px-2 py-0.5 ${filter === f ? 'bg-surface-2 font-semibold text-ink' : 'text-ink-2'}`}>
              {f === 'all' ? 'All' : f === 'win' ? 'Winners' : 'Losers'}
            </button>
          ))}
        </div>
        <button type="button" disabled={!r.trades.length}
          onClick={() => download(`${report.strategy.name}-${r.symbol}-trades.csv`.replace(/[^\w.-]+/g, '_'), tradesCsv(r.trades))}
          className="inline-flex items-center gap-1 rounded-lg border border-line px-2.5 py-1 text-[12px] hover:bg-surface-2 disabled:opacity-40">
          <Download size={13} /> CSV
        </button>
      </div>
    }>
      {r.trades.length === 0 ? (
        <div className="py-8 text-center text-ink-2">The strategy didn't place any trades in this period.</div>
      ) : (
        <>
          {r.trades_truncated && <div className="mb-2 text-[12px] text-muted">Showing the latest {r.trades.length.toLocaleString()} trades.</div>}
          <div className="-mx-4 overflow-x-auto sm:-mx-5">
            <table className="tnum w-full min-w-[860px] text-[12.5px]">
              <thead>
                <tr className="border-b border-line text-left text-[11.5px] text-muted">
                  {['#', 'Side', 'Entry', 'Entry price', 'Exit', 'Exit price', 'Exit by', 'Qty', 'P&L', 'P&L %', 'Bars'].map((h, k) => (
                    <th key={h} className={`px-3 py-2 font-medium first:pl-4 last:pr-4 sm:first:pl-5 sm:last:pr-5 ${k >= 7 || k === 3 || k === 5 ? 'text-right' : ''}`}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {shown.map((t) => (
                  <tr key={t.n} className="border-b border-line last:border-0 hover:bg-surface-2">
                    <td className="px-3 py-1.5 pl-4 text-muted sm:pl-5">{t.n}</td>
                    <td className="px-3"><span className={`rounded px-1.5 py-0.5 text-[11px] font-semibold ${t.side === 'Long' ? 'bg-accent-soft text-accent' : 'bg-surface-2 text-ink-2'}`}>{t.side}</span></td>
                    <td className="whitespace-nowrap px-3 text-ink-2">{t.entry_time}</td>
                    <td className="px-3 text-right">{price(t.entry_price)}</td>
                    <td className="whitespace-nowrap px-3 text-ink-2">{t.exit_time}</td>
                    <td className="px-3 text-right">{price(t.exit_price)}</td>
                    <td className="px-3 text-ink-2">{t.exit_reason}</td>
                    <td className="px-3 text-right">{num(t.qty, t.qty >= 100 ? 0 : 4)}</td>
                    <td className={`px-3 text-right font-medium ${tone(t.pnl)}`}>{money(t.pnl, sym)}</td>
                    <td className={`px-3 text-right ${tone(t.pnl_pct)}`}>{pct(t.pnl_pct)}</td>
                    <td className="px-3 pr-4 text-right text-ink-2 sm:pr-5">{t.bars}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {pages > 1 && (
            <div className="no-print mt-3 flex items-center justify-end gap-2 text-[12.5px]">
              <span className="text-muted">Page {page + 1} of {pages}</span>
              <button type="button" disabled={page === 0} onClick={() => setPage(page - 1)} className="rounded-lg border border-line px-2.5 py-1 hover:bg-surface-2 disabled:opacity-40">Previous</button>
              <button type="button" disabled={page >= pages - 1} onClick={() => setPage(page + 1)} className="rounded-lg border border-line px-2.5 py-1 hover:bg-surface-2 disabled:opacity-40">Next</button>
            </div>
          )}
        </>
      )}
    </Card>
  )
}
