import type { TradeRow } from "~/stores/bot"

export type GridSessionFillsSummary = {
  count: number
  buyCount: number
  sellCount: number
  totalQuoteVolume: number
  totalCommission: number
}

/** تنفيذات Spot الفعلية (fills) منذ بدء الشبكة فقط — لا أوامر معلّقة ولا سجل قديم */
export function gridSessionFills(trades: TradeRow[], startedAt?: string): TradeRow[] {
  const sinceMs = startedAt?.trim() ? Date.parse(startedAt) : 0
  return trades.filter((t) => {
    if (!(t.quantity > 0) || !(t.price > 0)) return false
    if (!sinceMs) return true
    const ms = t.tradedAtMs > 0 ? t.tradedAtMs : Date.parse(t.tradedAt)
    return !ms || ms >= sinceMs
  })
}

export function summarizeGridSessionFills(fills: TradeRow[]): GridSessionFillsSummary {
  let buyCount = 0
  let sellCount = 0
  let totalQuoteVolume = 0
  let totalCommission = 0
  for (const t of fills) {
    totalQuoteVolume += t.quoteQty
    totalCommission += commissionToUsdt(t)
    const side = t.side.toUpperCase()
    if (side === "BUY") buyCount += 1
    else if (side === "SELL") sellCount += 1
  }
  return {
    count: fills.length,
    buyCount,
    sellCount,
    totalQuoteVolume,
    totalCommission,
  }
}

function commissionToUsdt(t: TradeRow): number {
  const comm = t.commission
  if (!(comm > 0)) return 0
  const asset = (t.commissionAsset || "").toUpperCase()
  const sym = t.symbol.toUpperCase().replace("/", "")
  if (asset === "USDT" || asset === "USDC" || asset === "BUSD" || asset === "FDUSD") {
    return comm
  }
  const base = sym.endsWith("USDT") ? sym.slice(0, -4) : sym
  if (asset === base) return comm * t.price
  return 0
}

type Lot = { qty: number; unitCost: number }

/**
 * Spot realized USDT from SELL fills matched FIFO against prior BUYs (since grid start).
 * Binance Spot trade rows often have realizedPnl=0 — this is what the card should show.
 */
export function fifoRealizedUsdt(trades: TradeRow[], startedAt?: string): number {
  const rows = [...gridSessionFills(trades, startedAt)].sort((a, b) => {
      const idA = Number(a.exchangeTradeId) || a.tradedAtMs || 0
      const idB = Number(b.exchangeTradeId) || b.tradedAtMs || 0
      if (idA !== idB) return idA - idB
      return a.tradedAtMs - b.tradedAtMs
    })

  const lots: Lot[] = []
  let realized = 0

  for (const t of rows) {
    const side = t.side.toUpperCase()
    const qty = t.quantity
    if (!(qty > 0) || !(t.price > 0)) continue
    const comm = commissionToUsdt(t)

    if (side === "BUY") {
      const cost = t.quoteQty + comm
      const unit = cost / qty
      lots.push({ qty, unitCost: unit })
      continue
    }
    if (side !== "SELL") continue

    let rem = qty
    let costBasis = 0
    const proceeds = Math.max(t.quoteQty - comm, 0)
    const eps = Math.max(qty * 1e-9, 1e-12)

    while (rem > eps && lots.length > 0) {
      const head = lots[0]!
      const take = Math.min(rem, head.qty)
      costBasis += take * head.unitCost
      rem -= take
      head.qty -= take
      if (head.qty <= eps) lots.shift()
    }
    realized += proceeds - costBasis
  }

  return realized
}
