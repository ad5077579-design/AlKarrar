import type { TradeRow } from "~/stores/bot"

export type GridSessionFillsSummary = {
  count: number
  uniqueOrderCount: number
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
  const orderIds = new Set<string>()
  for (const t of fills) {
    const oid = String(t.orderId || "").trim()
    if (oid) orderIds.add(oid)
    else if (t.exchangeTradeId) orderIds.add(`t:${t.exchangeTradeId}`)
  }
  return {
    count: fills.length,
    uniqueOrderCount: orderIds.size,
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

type Lot = { qty: number; unitCost: number; tradeId: string }

export type FifoPnlState = {
  realizedClosed: number
  /** Per SELL fill — Binance often reports 0 on Spot buys */
  sellPnlByTradeId: Map<string, number>
  openLots: Lot[]
}

export function buildFifoPnlState(trades: TradeRow[], startedAt?: string): FifoPnlState {
  const rows = [...gridSessionFills(trades, startedAt)].sort((a, b) => {
    const idA = Number(a.exchangeTradeId) || a.tradedAtMs || 0
    const idB = Number(b.exchangeTradeId) || b.tradedAtMs || 0
    if (idA !== idB) return idA - idB
    return a.tradedAtMs - b.tradedAtMs
  })

  const lots: Lot[] = []
  let realizedClosed = 0
  const sellPnlByTradeId = new Map<string, number>()

  for (const t of rows) {
    const side = t.side.toUpperCase()
    const qty = t.quantity
    if (!(qty > 0) || !(t.price > 0)) continue
    const comm = commissionToUsdt(t)
    const tradeKey = String(t.exchangeTradeId || t.orderId || "")

    if (side === "BUY") {
      const cost = t.quoteQty + comm
      lots.push({ qty, unitCost: cost / qty, tradeId: tradeKey })
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
    const pnl = proceeds - costBasis
    realizedClosed += pnl
    if (tradeKey) sellPnlByTradeId.set(tradeKey, pnl)
    else sellPnlByTradeId.set(String(t.orderId), pnl)
  }

  return { realizedClosed, sellPnlByTradeId, openLots: lots }
}

export function fifoUnrealizedUsdt(state: FifoPnlState, markPrice: number): number {
  if (!(markPrice > 0)) return 0
  return state.openLots.reduce((sum, lot) => {
    if (!(lot.qty > 0)) return sum
    return sum + lot.qty * markPrice - lot.qty * lot.unitCost
  }, 0)
}

export type RowPnlDisplay = {
  text: string
  value: number | null
  mode: "closed" | "floating" | "none"
}

export function rowFifoPnlDisplay(
  row: TradeRow,
  state: FifoPnlState,
  markPrice: number,
): RowPnlDisplay {
  const side = row.side.toUpperCase()
  const tradeKey = String(row.exchangeTradeId || row.orderId || "")

  if (side === "SELL") {
    const v =
      state.sellPnlByTradeId.get(tradeKey) ??
      state.sellPnlByTradeId.get(String(row.orderId || ""))
    if (v == null || !Number.isFinite(v)) return { text: "—", value: null, mode: "none" }
    const sign = v >= 0 ? "+" : ""
    return { text: `${sign}${v.toFixed(4)}`, value: v, mode: "closed" }
  }

  if (side !== "BUY") return { text: "—", value: null, mode: "none" }

  let openQty = 0
  let unitCost = 0
  for (const lot of state.openLots) {
    if (lot.tradeId === tradeKey) {
      openQty += lot.qty
      unitCost = lot.unitCost
    }
  }
  if (!(openQty > 0)) return { text: "—", value: null, mode: "none" }

  if (!(markPrice > 0)) return { text: "…", value: null, mode: "floating" }

  const u = openQty * markPrice - openQty * unitCost
  const sign = u >= 0 ? "+" : ""
  return { text: `${sign}${u.toFixed(4)}`, value: u, mode: "floating" }
}

/**
 * Spot realized USDT from SELL fills matched FIFO against prior BUYs (since grid start).
 * Binance Spot trade rows often have realizedPnl=0 — this is what the card should show.
 */
export function fifoRealizedUsdt(trades: TradeRow[], startedAt?: string): number {
  return buildFifoPnlState(trades, startedAt).realizedClosed
}
