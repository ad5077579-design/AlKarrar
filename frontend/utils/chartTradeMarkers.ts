import type { SeriesMarker, Time, UTCTimestamp } from "lightweight-charts"
import type { GridLedgerEntry, TradeRow } from "~/stores/bot"
import { gridSessionFills } from "~/utils/fifoSpotPnl"

const BUY_COLOR = "#0ecb81"
const SELL_COLOR = "#f6465d"
export function formatChartPrice(n: number): string {
  if (!(n > 0) || !Number.isFinite(n)) return "—"
  if (n >= 1000) return n.toFixed(2)
  if (n >= 1) return n.toFixed(4)
  if (n >= 0.0001) return n.toFixed(6)
  return n.toExponential(2)
}

export function formatChartQty(n: number): string {
  if (!(n > 0) || !Number.isFinite(n)) return ""
  if (n >= 1000) return n.toFixed(1)
  if (n >= 1) return n.toFixed(2)
  return n.toFixed(4)
}

export function bucketTradeTimeSec(tradedAtMs: number, intervalSec: number): number {
  const ts = Math.floor(tradedAtMs / 1000)
  return Math.floor(ts / intervalSec) * intervalSec
}

function markerLabel(t: TradeRow, isBuy: boolean): string {
  const px = formatChartPrice(t.price)
  const qty = formatChartQty(t.quantity)
  const qtyPart = qty ? ` · ${qty}` : ""
  if (isBuy) return `B ${px}${qtyPart}`
  const pnl = t.realizedPnl
  if (Number.isFinite(pnl) && Math.abs(pnl) >= 0.0001) {
    const sign = pnl >= 0 ? "+" : ""
    return `S ${px} ${sign}${pnl.toFixed(2)}`
  }
  return `S ${px}${qtyPart}`
}

/**
 * Rich markers: BUY below bar (arrow + circle), SELL above with optional realized PnL.
 */
export function buildTradeMarkers(
  trades: TradeRow[],
  opts: {
    symbol: string
    intervalSec: number
    minTimeSec?: number
    maxTimeSec?: number
    limit?: number
    highlightLast?: boolean
  },
): SeriesMarker<Time>[] {
  const sym = opts.symbol.trim().toUpperCase().replace("/", "")
  const filtered = trades.filter((t) => {
    const ts = t.tradedAtMs > 0 ? t.tradedAtMs : Date.parse(t.tradedAt)
    if (!Number.isFinite(ts) || ts <= 0) return false
    const tSym = String(t.symbol ?? "")
      .trim()
      .toUpperCase()
      .replace("/", "")
    if (tSym && tSym !== sym) return false
    const sec = Math.floor(ts / 1000)
    if (opts.minTimeSec != null && sec < opts.minTimeSec) return false
    if (opts.maxTimeSec != null && sec > opts.maxTimeSec) return false
    return true
  })

  const byId = new Map<string, TradeRow>()
  for (const t of filtered) {
    byId.set(t.exchangeTradeId, t)
  }

  const rows = [...byId.values()].sort((a, b) => {
    const ta = a.tradedAtMs || Date.parse(a.tradedAt)
    const tb = b.tradedAtMs || Date.parse(b.tradedAt)
    return ta - tb
  })

  const cap = Math.max(1, opts.limit ?? 200)
  const slice = rows.length > cap ? rows.slice(-cap) : rows
  const lastId = opts.highlightLast !== false && slice.length ? slice[slice.length - 1].exchangeTradeId : ""

  const markers: SeriesMarker<Time>[] = []
  for (const t of slice) {
    const ms = t.tradedAtMs > 0 ? t.tradedAtMs : Date.parse(t.tradedAt)
    const time = bucketTradeTimeSec(ms, opts.intervalSec) as UTCTimestamp
    const isBuy = t.side.toUpperCase() === "BUY"
    const isLast = t.exchangeTradeId === lastId
    markers.push({
      time,
      position: isBuy ? "belowBar" : "aboveBar",
      shape: isLast ? (isBuy ? "arrowUp" : "arrowDown") : isBuy ? "circle" : "square",
      color: isBuy ? BUY_COLOR : SELL_COLOR,
      text: markerLabel(t, isBuy),
      id: t.exchangeTradeId,
      size: isLast ? 2 : 1,
    })
  }

  markers.sort((a, b) => (a.time as number) - (b.time as number))
  return markers
}

/** Per-candle buy/sell notional (USDT) for activity histogram. */
export function buildTradeActivityHistogram(
  trades: TradeRow[],
  opts: { symbol: string; intervalSec: number; minTimeSec?: number; maxTimeSec?: number },
): { time: UTCTimestamp; value: number; color: string }[] {
  const sym = opts.symbol.trim().toUpperCase().replace("/", "")
  const buckets = new Map<number, { buy: number; sell: number }>()

  for (const t of trades) {
    const tSym = String(t.symbol ?? "")
      .trim()
      .toUpperCase()
      .replace("/", "")
    if (tSym && tSym !== sym) continue
    const ms = t.tradedAtMs > 0 ? t.tradedAtMs : Date.parse(t.tradedAt)
    if (!Number.isFinite(ms)) continue
    const sec = bucketTradeTimeSec(ms, opts.intervalSec)
    if (opts.minTimeSec != null && sec < opts.minTimeSec) continue
    if (opts.maxTimeSec != null && sec > opts.maxTimeSec) continue
    const quote = t.quoteQty > 0 ? t.quoteQty : t.price * t.quantity
    if (!(quote > 0)) continue
    const row = buckets.get(sec) ?? { buy: 0, sell: 0 }
    if (t.side.toUpperCase() === "BUY") row.buy += quote
    else row.sell += quote
    buckets.set(sec, row)
  }

  return [...buckets.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([sec, v]) => {
      const net = v.buy - v.sell
      const total = v.buy + v.sell
      const color =
        net >= 0 ? "rgba(14, 203, 129, 0.55)" : "rgba(246, 70, 93, 0.55)"
      return {
        time: sec as UTCTimestamp,
        value: total,
        color,
      }
    })
}

export function mergeTradesForSymbol(
  symbol: string,
  packTrades: TradeRow[],
  globalTrades: TradeRow[],
): TradeRow[] {
  const sym = symbol.trim().toUpperCase().replace("/", "")
  const byId = new Map<string, TradeRow>()
  for (const t of packTrades) {
    byId.set(t.exchangeTradeId, t)
  }
  for (const t of globalTrades) {
    const tSym = String(t.symbol ?? "")
      .trim()
      .toUpperCase()
      .replace("/", "")
    if (tSym && tSym !== sym) continue
    if (!tSym && sym) {
      byId.set(t.exchangeTradeId, t)
      continue
    }
    byId.set(t.exchangeTradeId, t)
  }
  return [...byId.values()]
}

/** Fills from grid audit ledger when Binance trade sync is empty (virtual / pending). */
export function buildLedgerFillMarkers(
  entries: GridLedgerEntry[],
  opts: {
    symbol: string
    intervalSec: number
    minTimeSec?: number
    maxTimeSec?: number
    sessionStartMs?: number
  },
): SeriesMarker<Time>[] {
  const sym = opts.symbol.trim().toUpperCase().replace("/", "")
  const markers: SeriesMarker<Time>[] = []
  for (const e of entries) {
    const act = String(e.actionType ?? "").toUpperCase()
    if (act !== "ORDER_BUY" && act !== "ORDER_SELL") continue
    const px = Number(e.fillPrice)
    if (!(px > 0)) continue
    const ms = Number(e.timestampMs)
    if (!(ms > 0)) continue
    if (opts.sessionStartMs != null && ms < opts.sessionStartMs) continue
    const sec = bucketTradeTimeSec(ms, opts.intervalSec)
    if (opts.minTimeSec != null && sec < opts.minTimeSec) continue
    if (opts.maxTimeSec != null && sec > opts.maxTimeSec) continue
    const isBuy = act === "ORDER_BUY"
    const qty = Number(e.quantity)
    const pnl = Number(e.netProfitUsdt)
    let text = isBuy ? `B ${formatChartPrice(px)}` : `S ${formatChartPrice(px)}`
    if (!isBuy && Number.isFinite(pnl) && Math.abs(pnl) >= 0.0001) {
      text += ` ${pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}`
    } else if (qty > 0) {
      text += ` · ${formatChartQty(qty)}`
    }
    markers.push({
      time: sec as UTCTimestamp,
      position: isBuy ? "belowBar" : "aboveBar",
      shape: isBuy ? "circle" : "square",
      color: isBuy ? BUY_COLOR : SELL_COLOR,
      text: `${text} · سجل`,
      id: `ledger-${e.id}`,
      size: 1,
    })
  }
  markers.sort((a, b) => (a.time as number) - (b.time as number))
  return markers
}

export function chartTradesForSymbol(
  symbol: string,
  packTrades: TradeRow[],
  globalTrades: TradeRow[],
  sessionStartedAt?: string,
): TradeRow[] {
  const merged = mergeTradesForSymbol(symbol, packTrades, globalTrades)
  if (sessionStartedAt?.trim()) {
    return gridSessionFills(merged, sessionStartedAt)
  }
  return merged
}

export function tradeSummaryForSymbol(trades: TradeRow[], symbol: string) {
  const sym = symbol.trim().toUpperCase().replace("/", "")
  let buys = 0
  let sells = 0
  let realized = 0
  for (const t of trades) {
    const tSym = String(t.symbol ?? "")
      .trim()
      .toUpperCase()
      .replace("/", "")
    if (tSym && tSym !== sym) continue
    if (t.side.toUpperCase() === "BUY") buys += 1
    else sells += 1
    realized += t.realizedPnl || 0
  }
  return { buys, sells, realized, total: buys + sells }
}
