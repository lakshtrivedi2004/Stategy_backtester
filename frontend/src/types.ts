export interface MarketOption {
  id: string
  label: string
  description: string
  currency: string
  currency_symbol: string
  initial_capital: number
  cost_note: string
  instruments: { symbol: string; name: string }[]
}

export interface PeriodOption {
  id: string
  label: string
  interval: string
  interval_label: string
}

export interface Options {
  markets: MarketOption[]
  periods: PeriodOption[]
  examples: { filename: string; language: string }[]
}

export interface Metrics {
  initial_capital: number
  final_equity: number
  net_profit: number
  net_profit_pct: number
  buy_hold_pct: number | null
  annual_return_pct: number | null
  max_drawdown: number
  max_drawdown_pct: number
  return_over_max_dd: number | null
  total_trades: number
  winning_trades: number
  losing_trades: number
  win_rate: number | null
  gross_profit: number
  gross_loss: number
  profit_factor: number | null
  avg_trade: number | null
  avg_trade_pct: number | null
  avg_win: number | null
  avg_loss: number | null
  win_loss_ratio: number | null
  largest_win: number | null
  largest_loss: number | null
  max_consecutive_wins: number
  max_consecutive_losses: number
  avg_bars_in_trade: number | null
  exposure_pct: number | null
  commission_paid: number
  sharpe: number | null
  sortino: number | null
}

export interface Trade {
  n: number
  side: 'Long' | 'Short'
  entry_id: string
  exit_id: string
  exit_reason: string
  entry_time: string
  exit_time: string
  entry_price: number
  exit_price: number
  qty: number
  pnl: number
  pnl_pct: number
  commission: number
  bars: number
}

export interface SideStats {
  trades: number
  win_rate: number | null
  net_profit: number
  avg_trade: number | null
}

export interface InstrumentResult {
  symbol: string
  name: string
  status: 'ok' | 'error'
  error?: string
  line?: number | null
  settings: {
    initial_capital: number
    currency: string
    currency_symbol: string
    sizing: string
    costs: string
    pyramiding: number
    fills: string
  }
  inputs: Record<string, string | number | boolean | null>
  warnings: string[]
  metrics: Metrics
  long: SideStats
  short: SideStats
  curve: { t: number[]; equity: number[]; buy_hold: number[]; drawdown_pct: number[] }
  monthly: { year: number; month: number; return_pct: number }[]
  trades: Trade[]
  trades_truncated: boolean
  window: { start: string; end: string; bars: number; warmup_bars: number }
}

export interface Summary {
  instruments_tested: number
  instruments_failed: number
  profitable: number
  avg_return_pct: number | null
  median_return_pct: number | null
  avg_buy_hold_pct: number | null
  beat_buy_hold: number
  avg_max_drawdown_pct: number | null
  total_trades: number
  win_rate: number | null
  best: { symbol: string; name: string; return_pct: number } | null
  worst: { symbol: string; name: string; return_pct: number } | null
}

export interface Report {
  id: string
  created_at: string
  market: MarketOption
  period: PeriodOption
  strategy: { name: string; language: string; filename: string }
  summary: Summary
  instruments: InstrumentResult[]
}

export interface Progress {
  symbol: string
  name: string
  status: 'queued' | 'downloading' | 'testing' | 'done' | 'error'
}

export interface JobView {
  id: string
  status: 'running' | 'done' | 'error'
  error: string | null
  progress: Progress[]
  report: Report | null
}
