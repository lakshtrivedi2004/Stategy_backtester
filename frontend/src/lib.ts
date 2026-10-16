import type { Options, JobView, Trade } from './types'

export class ApiError extends Error {
  line: number | null
  constructor(message: string, line: number | null = null) {
    super(message)
    this.line = line
  }
}

async function parse<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => null)
  if (!res.ok) {
    const d = body?.detail
    if (d && typeof d === 'object' && 'message' in d) throw new ApiError(d.message, d.line ?? null)
    throw new ApiError(typeof d === 'string' ? d : `Request failed (${res.status})`)
  }
  return body as T
}

export const api = {
  options: () => fetch('/api/options').then((r) => parse<Options>(r)),
  start: (market: string, period: string, file: File) => {
    const form = new FormData()
    form.append('market', market)
    form.append('period', period)
    form.append('file', file)
    return fetch('/api/backtests', { method: 'POST', body: form }).then((r) =>
      parse<{ id: string; strategy: { name: string; language: string } }>(r),
    )
  },
  job: (id: string) => fetch(`/api/backtests/${id}`).then((r) => parse<JobView>(r)),
}

export function detectLanguage(name: string, text: string): 'Python' | 'Pine Script' | null {
  const ext = name.toLowerCase().split('.').pop()
  if (ext === 'py') return 'Python'
  if (ext === 'pine' || ext === 'pinescript') return 'Pine Script'
  if (text.trimStart().startsWith('//') || text.includes('//@version')) return 'Pine Script'
  if (/^\s*(def |import |from )/m.test(text)) return 'Python'
  if (/\bstrategy\s*\(/.test(text)) return 'Pine Script'
  return null
}

const nf = (digits: number) => new Intl.NumberFormat('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })

export function money(v: number | null | undefined, sym: string, digits = 2): string {
  if (v == null) return '–'
  const s = nf(digits).format(Math.abs(v))
  return `${v < 0 ? '−' : ''}${sym}${s}`
}

export function pct(v: number | null | undefined, digits = 2, signed = true): string {
  if (v == null) return '–'
  const s = nf(digits).format(Math.abs(v))
  const sign = v < 0 ? '−' : signed && v > 0 ? '+' : ''
  return `${sign}${s}%`
}

export function num(v: number | null | undefined, digits = 2): string {
  if (v == null) return '–'
  return (v < 0 ? '−' : '') + nf(digits).format(Math.abs(v))
}

export function price(v: number): string {
  const digits = v >= 1000 ? 2 : v >= 10 ? 3 : v >= 1 ? 4 : 6
  return nf(digits).format(v)
}

export function tone(v: number | null | undefined): string {
  if (v == null || v === 0) return 'text-ink'
  return v > 0 ? 'text-gain' : 'text-loss'
}

export function tradesCsv(trades: Trade[]): string {
  const cols: (keyof Trade)[] = ['n', 'side', 'entry_time', 'entry_price', 'exit_time', 'exit_price', 'qty',
    'pnl', 'pnl_pct', 'commission', 'bars', 'entry_id', 'exit_id', 'exit_reason']
  const esc = (v: unknown) => {
    const s = String(v ?? '')
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  return [cols.join(','), ...trades.map((t) => cols.map((c) => esc(t[c])).join(','))].join('\n')
}

export function download(filename: string, text: string, type = 'text/csv') {
  const url = URL.createObjectURL(new Blob([text], { type }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}
