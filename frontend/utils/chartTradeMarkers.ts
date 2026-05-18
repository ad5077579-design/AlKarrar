import type { SeriesMarker, Time, UTCTimestamp } from "lightweight-charts"
import type { TradeRow } from "~/stores/bot"

const BUY_COLOR = "#0ecb81"
const SELL_COLOR = "#f6465d"

export function bucketTradeTimeSec(tradedAtMs: number, intervalSec: number): number {
  const ts = Math.floor(tradedAtMs / 1000)
  return Math.floor(ts / intervalSec) * intervalSec
}

/**
 * Build ascending markers for lightweight-charts (BUY green below, SELL red above).
 */
export function buildTradeMarkers(
  trades: TradeRow[],
  opts: {
    symbol: string
    intervalSec: number
    minTimeSec?: number
    maxTimeSec?: number
    limit?: number
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

  const markers: SeriesMarker<Time>[] = []
  for (const t of slice) {
    const ms = t.tradedAtMs > 0 ? t.tradedAtMs : Date.parse(t.tradedAt)
    const time = bucketTradeTimeSec(ms, opts.intervalSec) as UTCTimestamp
    const isBuy = t.side.toUpperCase() === "BUY"
    markers.push({
      time,
      position: isBuy ? "belowBar" : "aboveBar",
      shape: isBuy ? "arrowUp" : "arrowDown",
      color: isBuy ? BUY_COLOR : SELL_COLOR,
      text: isBuy ? "شراء" : "بيع",
      id: t.exchangeTradeId,
    })
  }

  markers.sort((a, b) => (a.time as number) - (b.time as number))
  return markers
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
