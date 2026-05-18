import type { TradeRow } from "~/stores/bot"

export type TradeDisplayRow = TradeRow & {
  orderKey: string
  fillIndex: number
  fillCount: number
  isMultiFill: boolean
}

/** Binance order history = 1 row; myTrades = 1 row per fill (same orderId). */
export function tradesForDisplay(rows: TradeRow[]): TradeDisplayRow[] {
  const sorted = [...rows].sort((a, b) => {
    const ta = a.tradedAtMs > 0 ? a.tradedAtMs : Date.parse(a.tradedAt)
    const tb = b.tradedAtMs > 0 ? b.tradedAtMs : Date.parse(b.tradedAt)
    return tb - ta
  })
  const byOrder = new Map<string, TradeRow[]>()
  for (const r of sorted) {
    const key = String(r.orderId || r.exchangeTradeId || "").trim() || `trade-${r.exchangeTradeId}`
    const list = byOrder.get(key) ?? []
    list.push(r)
    byOrder.set(key, list)
  }
  const out: TradeDisplayRow[] = []
  for (const group of byOrder.values()) {
    const n = group.length
    group.forEach((r, i) => {
      const orderKey = String(r.orderId || r.exchangeTradeId || "")
      out.push({
        ...r,
        orderKey,
        fillIndex: i + 1,
        fillCount: n,
        isMultiFill: n > 1,
      })
    })
  }
  return out.sort((a, b) => {
    const ta = a.tradedAtMs > 0 ? a.tradedAtMs : Date.parse(a.tradedAt)
    const tb = b.tradedAtMs > 0 ? b.tradedAtMs : Date.parse(b.tradedAt)
    return tb - ta
  })
}

export function uniqueOrderCount(rows: TradeRow[]): number {
  const ids = new Set<string>()
  for (const r of rows) {
    const id = String(r.orderId || "").trim()
    if (id) ids.add(id)
    else if (r.exchangeTradeId) ids.add(`t:${r.exchangeTradeId}`)
  }
  return ids.size
}
