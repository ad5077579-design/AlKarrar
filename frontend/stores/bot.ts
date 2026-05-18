import { defineStore } from "pinia"
import { computed, ref, shallowRef } from "vue"

type WsMsg =
  | { type: "snapshot"; data: Record<string, unknown> }
  | { type: "mark"; markPrice: number; t?: number }
  | { type: "settings"; data: Record<string, unknown> }
  | { type: "metrics"; data: Record<string, unknown> }
  | { type: "sync_error"; message: string }
  | { type: "order"; data: Record<string, unknown> }
  | { type: "trade"; data: Record<string, unknown> }
  | {
      type: "grid_metrics"
      symbol?: string
      data: Record<string, unknown>
    }
  | {
      type: "grid_ledger"
      symbol?: string
      cleared?: boolean
      frozen?: boolean
      freezeReason?: string
      entry?: Record<string, unknown>
      count?: number
      entries?: Record<string, unknown>[]
    }
  | { type: "emergency"; bot_id?: string; ts?: string }

function num(v: unknown, fallback = 0): number {
  const n = typeof v === "number" ? v : Number(v)
  return Number.isFinite(n) ? n : fallback
}

export type MarketSymbol = {
  symbol: string
  baseAsset: string
  lastPrice: number
  priceChangePercent: number
  quoteVolume: number
}

export type TradeRow = {
  id?: number
  exchangeTradeId: string
  orderId: string
  symbol: string
  side: string
  price: number
  quantity: number
  quoteQty: number
  realizedPnl: number
  commission: number
  commissionAsset: string
  isMaker: boolean
  positionSide: string
  tradedAt: string
  tradedAtMs: number
}

export type TradesSummary = {
  count: number
  uniqueOrderCount?: number
  buyCount: number
  sellCount: number
  totalQuoteVolume: number
  totalRealizedPnl: number
  totalCommission: number
}

export type SymbolTradesPack = {
  summary: TradesSummary
  trades: TradeRow[]
  updatedAt: string
  source: "database" | "binance"
  syncError: string | null
  loading: boolean
  error: string | null
}

export type GridLineTrailRow = {
  lineIndex: number
  phase: "idle" | "lock_profit" | "trailing" | string
  tpLevel?: number
  trailPeak?: number
  lockFloor?: number
  exchangeFillConfirmed?: boolean
  hasSessionBuy?: boolean
}

export type GridSymbolMeta = {
  ordersPlaced?: number
  virtualExecutions?: number
  virtualGrid?: boolean
  lastError?: string
  startedAt?: string
  running?: boolean
  sessionRealizedUsdt?: number
  cumulativeRealizedUsdt?: number
  allocatedCapital?: number
  deployCapitalUsdt?: number
  gridEquityUsdt?: number
  unrealizedPnlUsdt?: number
  peakEquityUsdt?: number
  currentDrawdownPct?: number
  trailingEquityStopTriggered?: boolean
  lineTrail?: GridLineTrailRow[]
}

const EMPTY_TRADES_SUMMARY: TradesSummary = {
  count: 0,
  uniqueOrderCount: 0,
  buyCount: 0,
  sellCount: 0,
  totalQuoteVolume: 0,
  totalRealizedPnl: 0,
  totalCommission: 0,
}

function emptySymbolTradesPack(): SymbolTradesPack {
  return {
    summary: { ...EMPTY_TRADES_SUMMARY },
    trades: [],
    updatedAt: "",
    source: "database",
    syncError: null,
    loading: false,
    error: null,
  }
}

export type AuditLogRow = {
  id: number
  timestamp: string | null
  eventType: string
  markPrice: number
  realizedUsdt: number
  details: Record<string, unknown>
}

export type GridLedgerEntry = {
  id: string
  timestampMs: number
  actionType: string
  triggerReason: string
  symbol: string
  targetPrice: number | null
  fillPrice: number | null
  slippagePct: number | null
  quantity: number | null
  netProfitUsdt: number | null
  commissionUsdt: number | null
  generatorUpper: number
  generatorLower: number
  generatorCount: number
  orderSize: number
  markPrice: number
  apiErrorCode: string | null
  apiErrorMessage: string | null
  extra: Record<string, unknown>
}

export type GridLedgerPack = {
  symbol: string
  frozen: boolean
  freezeReason: string
  entries: GridLedgerEntry[]
}

function ledgerEntryKey(row: GridLedgerEntry): string {
  const oid = row.extra?.orderId
  if (oid != null && String(oid).length > 0) {
    return `${row.actionType}:${String(oid)}`
  }
  return row.id
}

function mergeLedgerEntries(existing: GridLedgerEntry[], incoming: GridLedgerEntry[]): GridLedgerEntry[] {
  const byKey = new Map<string, GridLedgerEntry>()
  for (const row of existing) {
    byKey.set(ledgerEntryKey(row), row)
  }
  for (const row of incoming) {
    byKey.set(ledgerEntryKey(row), row)
  }
  return [...byKey.values()].sort((a, b) => a.timestampMs - b.timestampMs)
}

function parseGridLedgerEntry(raw: Record<string, unknown>): GridLedgerEntry | null {
  const id = String(raw.id ?? "")
  if (!id) return null
  const nul = (v: unknown): number | null => {
    if (v == null || v === "") return null
    const n = num(v, NaN)
    return Number.isFinite(n) ? n : null
  }
  return {
    id,
    timestampMs: num(raw.timestampMs),
    actionType: String(raw.actionType ?? ""),
    triggerReason: String(raw.triggerReason ?? ""),
    symbol: String(raw.symbol ?? "").toUpperCase(),
    targetPrice: nul(raw.targetPrice),
    fillPrice: nul(raw.fillPrice),
    slippagePct: nul(raw.slippagePct),
    quantity: nul(raw.quantity),
    netProfitUsdt: nul(raw.netProfitUsdt),
    commissionUsdt: nul(raw.commissionUsdt),
    generatorUpper: num(raw.generatorUpper),
    generatorLower: num(raw.generatorLower),
    generatorCount: Math.floor(num(raw.generatorCount)),
    orderSize: num(raw.orderSize),
    markPrice: num(raw.markPrice),
    apiErrorCode: raw.apiErrorCode != null ? String(raw.apiErrorCode) : null,
    apiErrorMessage: raw.apiErrorMessage != null ? String(raw.apiErrorMessage) : null,
    extra:
      raw.extra && typeof raw.extra === "object"
        ? (raw.extra as Record<string, unknown>)
        : {},
  }
}

function parseTradeRow(raw: Record<string, unknown>): TradeRow | null {
  const exchangeTradeId = String(raw.exchangeTradeId ?? "")
  if (!exchangeTradeId) return null
  return {
    id: raw.id != null ? num(raw.id) : undefined,
    exchangeTradeId,
    orderId: String(raw.orderId ?? ""),
    symbol: String(raw.symbol ?? "").toUpperCase(),
    side: String(raw.side ?? "").toUpperCase(),
    price: num(raw.price),
    quantity: num(raw.quantity),
    quoteQty: num(raw.quoteQty),
    realizedPnl: num(raw.realizedPnl),
    commission: num(raw.commission),
    commissionAsset: String(raw.commissionAsset ?? "USDT"),
    isMaker: Boolean(raw.isMaker),
    positionSide: String(raw.positionSide ?? "BOTH"),
    tradedAt: String(raw.tradedAt ?? ""),
    tradedAtMs: num(raw.tradedAtMs),
  }
}

export const useBotStore = defineStore("bot", () => {
  const ws = shallowRef<WebSocket | null>(null)
  const wsConnected = ref(false)
  const wsError = ref<string | null>(null)
  const lastWsAt = ref(0)

  const credentialsConfigured = ref(false)
  /** Once keys were confirmed, do not flash "no keys" on transient API/WS errors. */
  let credentialsLocked = false
  const apiReachable = ref(false)
  const binanceApiKeyPreview = ref("")
  const binanceTestnetStored = ref(true)
  const syncError = ref("")
  const syncOkAt = ref("")
  const exchangeTestnet = ref(false)
  const binanceEnv = ref("")

  const symbol = ref("DOGEUSDT")
  const markPrice = ref(0)
  const markBySymbol = ref<Record<string, number>>({})
  const generatorUpper = ref(0)
  const generatorLower = ref(0)
  const generatorCount = ref(5)
  const maxGeneratorCount = ref(9999)
  const initialCapital = ref(100)
  const allocatedCapital = ref(100)
  const realizedPnl = ref(0)
  const floatingPnl = ref(0)
  const totalWalletBalance = ref(0)
  const totalMarginBalance = ref(0)
  const currentCapital = ref(0)
  const marginBalance = ref(0)
  const availableBalance = ref(0)
  const balanceSource = ref("")
  const peakEquityUsdt = ref(0)
  const currentDrawdownPct = ref(0)
  const trailingEquityStopEnabled = ref(true)
  const trailingEquityDrawdownLimitPct = ref(10)
  const trailingEquityStopTriggered = ref(false)
  const reinjectedRealizedUsdt = ref(0)
  const autoCompoundingEnabled = ref(true)
  const activeGridLines = ref(5)
  const orders = ref<Record<string, unknown>[]>([])

  const markSeriesData = ref<{ time: number; value: number }[]>([])
  let lastChartTime = 0

  const markets = ref<MarketSymbol[]>([])
  const marketsQuote = ref("USDT")
  const marketsLoading = ref(false)
  const marketsError = ref<string | null>(null)
  const marketsUpdatedAt = ref("")
  const marketsExchangeTestnet = ref(false)
  const excludedStableSymbols = ref<string[]>([])

  const trades = ref<TradeRow[]>([])
  const tradesSummary = ref<TradesSummary>({
    count: 0,
    buyCount: 0,
    sellCount: 0,
    totalQuoteVolume: 0,
    totalRealizedPnl: 0,
    totalCommission: 0,
  })
  const tradesLoading = ref(false)
  const tradesError = ref<string | null>(null)
  const tradesSyncError = ref<string | null>(null)
  const tradesSource = ref<"database" | "binance">("database")
  const tradesSymbol = ref("")
  const tradesUpdatedAt = ref("")
  const gridRunning = ref(false)
  const gridStatusText = ref("")
  const gridOrdersPlaced = ref(0)
  const gridLastError = ref("")
  /** الزوج الذي تشغّل عليه شبكة المنصّة حالياً (من /grid/status)، إذا كانت نشطة */
  const gridRunnerSymbol = ref("")
  /** بدء شبكة المنصّة (ISO)، إن وُجد من API */
  const gridStartedAt = ref("")
  const activeGridSymbols = ref<string[]>([])
  const gridsBySymbol = ref<Record<string, GridSymbolMeta>>({})
  const tradesBySymbol = ref<Record<string, SymbolTradesPack>>({})
  /** عند النقر على «سجل الصفقات» من كرت شبكة */
  const tradesViewSymbol = ref<string | null>(null)
  const gridLedgerBySymbol = ref<Record<string, GridLedgerPack>>({})

  /** live = آخر مزامنة ناجحة من Binance؛ pending = مفاتيح موجودة لكن لم تُجلب الأرصدة بعد؛ error = فشل المزامنة */
  const balanceSyncState = computed((): "no_keys" | "pending" | "live" | "error" => {
    if (!credentialsConfigured.value) return "no_keys"
    if (syncError.value) return "error"
    if (syncOkAt.value) return "live"
    return "pending"
  })

  const balanceIsLive = computed(() => balanceSyncState.value === "live")

  const liveEquityUsdt = computed(() => totalWalletBalance.value)

  const syncErrorHint = computed(() => {
    const err = syncError.value
    if (!err) return ""
    if (err.includes("-2015") || /invalid api-key/i.test(err)) {
      return (
        "المفتاح مرفوض — طابق BINANCE_ENV مع مصدر المفتاح: " +
        "demo.binance.com → BINANCE_ENV=demo، testnet.binance.vision → testnet، الإنتاج → mainnet. " +
        "فعّل صلاحية Spot Trading للمفتاح."
      )
    }
    return ""
  })

  /** عرض موحّد لبيئة Spot (demo | testnet | mainnet) في الواجهة */
  const spotEnvLabel = computed(() => {
    const e = binanceEnv.value
    if (e === "demo") return "Demo"
    if (e === "testnet") return "Testnet"
    if (e === "mainnet") return "Mainnet"
    return exchangeTestnet.value ? "Testnet" : "Mainnet"
  })

  const spotEnvLabelAr = computed(() => {
    const e = binanceEnv.value
    if (e === "demo") return "تجريبي"
    if (e === "testnet") return "Testnet"
    if (e === "mainnet") return "إنتاج"
    return exchangeTestnet.value ? "ورقي" : "إنتاج"
  })

  const hasActiveGrids = computed(
    () => activeGridSymbols.value.length > 0 || gridRunning.value,
  )

  const hasOpenExchangeOrders = computed(() => orders.value.length > 0)

  /** أقسام التشغيل الحي فقط — لا تُعرض عند التوقف ولا أوامر معلّقة */
  const showLiveBotPanels = computed(
    () => hasActiveGrids.value || hasOpenExchangeOrders.value,
  )

  /** هل الشبكة نشطة على الزوج المختار في الشريط (وليس أي زوج آخر) */
  const isGridActiveForSelectedSymbol = computed(() => {
    const s = symbol.value.trim().toUpperCase().replace("/", "")
    return Boolean(s) && activeGridSymbols.value.includes(s)
  })

  const otherActiveGridSymbols = computed(() => {
    const cur = symbol.value.trim().toUpperCase().replace("/", "")
    return activeGridSymbols.value.filter((s) => s !== cur)
  })

  const selectedGridMeta = computed(() => {
    const s = symbol.value.trim().toUpperCase().replace("/", "")
    return s ? gridsBySymbol.value[s] : undefined
  })

  const otherGridsAllocatedUsdt = computed(() => {
    const cur = symbol.value.trim().toUpperCase().replace("/", "")
    let sum = 0
    for (const s of activeGridSymbols.value) {
      if (s === cur) continue
      sum += Number(gridsBySymbol.value[s]?.allocatedCapital ?? 0)
    }
    return sum
  })

  const maxAllocatableUsdt = computed(() =>
    Math.max(0, availableBalance.value - otherGridsAllocatedUsdt.value),
  )

  function symbolMark(sym: string): number {
    const s = sym.trim().toUpperCase().replace("/", "")
    if (!s) return 0
    const room = markBySymbol.value[s]
    if (room > 0) return room
    const focus = symbol.value.trim().toUpperCase().replace("/", "")
    if (s === focus && markPrice.value > 0) return markPrice.value
    return 0
  }

  const gridPeakEquityUsdt = computed(() => {
    const g = selectedGridMeta.value
    if (isGridActiveForSelectedSymbol.value && g?.peakEquityUsdt != null) {
      return Number(g.peakEquityUsdt)
    }
    return peakEquityUsdt.value
  })

  const gridDrawdownPct = computed(() => {
    const g = selectedGridMeta.value
    if (isGridActiveForSelectedSymbol.value && g?.currentDrawdownPct != null) {
      return Number(g.currentDrawdownPct)
    }
    return currentDrawdownPct.value
  })

  const gridReinjectedUsdt = computed(() => {
    const g = selectedGridMeta.value
    if (isGridActiveForSelectedSymbol.value && g?.cumulativeRealizedUsdt != null) {
      return Number(g.cumulativeRealizedUsdt)
    }
    return reinjectedRealizedUsdt.value
  })

  const gridLevels = computed(() => {
    const lo = generatorLower.value
    const hi = generatorUpper.value
    const n = Math.max(2, Math.floor(generatorCount.value))
    if (!(hi > lo)) return [lo, hi]
    const step = (hi - lo) / (n - 1)
    return Array.from({ length: n }, (_, i) => lo + i * step)
  })

  function publicApiPrefix(): string {
    const cfg = useRuntimeConfig()
    const b = cfg.public.apiBase
    if (b == null || b === "") return ""
    return String(b).replace(/\/$/, "")
  }

  function applyRiskFields(data: Record<string, unknown>) {
    if (data.peakEquityUsdt != null) peakEquityUsdt.value = num(data.peakEquityUsdt)
    if (data.currentDrawdownPct != null) currentDrawdownPct.value = num(data.currentDrawdownPct)
    if (typeof data.trailingEquityStopEnabled === "boolean") {
      trailingEquityStopEnabled.value = data.trailingEquityStopEnabled
    }
    if (data.trailingEquityDrawdownLimitPct != null) {
      trailingEquityDrawdownLimitPct.value = num(data.trailingEquityDrawdownLimitPct, 10)
    }
    if (typeof data.trailingEquityStopTriggered === "boolean") {
      trailingEquityStopTriggered.value = data.trailingEquityStopTriggered
    }
    if (data.reinjectedRealizedUsdt != null) {
      reinjectedRealizedUsdt.value = num(data.reinjectedRealizedUsdt)
    }
    if (typeof data.autoCompoundingEnabled === "boolean") {
      autoCompoundingEnabled.value = data.autoCompoundingEnabled
    } else if (typeof data.profit_injection_mode === "string") {
      autoCompoundingEnabled.value =
        data.profit_injection_mode.toLowerCase() === "compound_size"
    }
    if (typeof data.balanceSource === "string") balanceSource.value = data.balanceSource
  }

  function applyCredentialsFields(data: Record<string, unknown>) {
    if (data.credentialsConfigured === true) {
      credentialsConfigured.value = true
      credentialsLocked = true
    } else if (data.credentialsConfigured === false && !credentialsLocked) {
      credentialsConfigured.value = false
    }
  }

  function applyAccountMetrics(data: Record<string, unknown>) {
    if (data.totalWalletBalance != null) totalWalletBalance.value = num(data.totalWalletBalance)
    if (data.totalMarginBalance != null) totalMarginBalance.value = num(data.totalMarginBalance)
    if (data.currentCapital != null) currentCapital.value = num(data.currentCapital)
    if (data.marginBalance != null) marginBalance.value = num(data.marginBalance)
    if (data.availableBalance != null) availableBalance.value = num(data.availableBalance)
    if (data.floatingPnl != null) floatingPnl.value = num(data.floatingPnl)
    if (data.realizedPnl != null) realizedPnl.value = num(data.realizedPnl)
    if (typeof data.syncError === "string") syncError.value = data.syncError
    if (typeof data.syncOkAt === "string") syncOkAt.value = data.syncOkAt
    if (typeof data.exchangeTestnet === "boolean") exchangeTestnet.value = data.exchangeTestnet
    if (typeof data.binanceEnv === "string" && data.binanceEnv) {
      binanceEnv.value = data.binanceEnv
    }
    if (typeof data.balanceSource === "string") balanceSource.value = data.balanceSource
    applyCredentialsFields(data)
    if (typeof data.binanceApiKeyPreview === "string") {
      binanceApiKeyPreview.value = data.binanceApiKeyPreview
    }
    applyRiskFields(data)
  }

  function applySnapshot(data: Record<string, unknown>) {
    applyCredentialsFields(data)
    applyRiskFields(data)
    if (typeof data.binanceApiKeyPreview === "string") {
      binanceApiKeyPreview.value = data.binanceApiKeyPreview
    }
    if (typeof data.binanceTestnet === "boolean") {
      binanceTestnetStored.value = data.binanceTestnet
    }
    if (typeof data.syncError === "string") {
      syncError.value = data.syncError
    }
    if (typeof data.syncOkAt === "string") {
      syncOkAt.value = data.syncOkAt
    }
    if (typeof data.exchangeTestnet === "boolean") {
      exchangeTestnet.value = data.exchangeTestnet
    }
    if (typeof data.binanceEnv === "string") {
      binanceEnv.value = data.binanceEnv
    }
    if (typeof data.symbol === "string" && data.symbol.trim()) {
      symbol.value = data.symbol.trim().toUpperCase().replace("/", "")
    }
    markPrice.value = num(data.markPrice)
    generatorUpper.value = num(data.generatorUpper)
    generatorLower.value = num(data.generatorLower)
    generatorCount.value = Math.max(2, Math.floor(num(data.generatorCount, 5)))
    maxGeneratorCount.value = Math.max(
      generatorCount.value,
      Math.floor(num(data.maxGeneratorCount, maxGeneratorCount.value)),
    )
    initialCapital.value = num(data.initialCapital, 100)
    allocatedCapital.value = num(
      data.allocatedCapital ?? data.initialCapital,
      initialCapital.value,
    )
    realizedPnl.value = num(data.realizedPnl)
    floatingPnl.value = num(data.floatingPnl)
    totalWalletBalance.value = num(data.totalWalletBalance, 0)
    totalMarginBalance.value = num(data.totalMarginBalance, 0)
    currentCapital.value = num(data.currentCapital, 0)
    marginBalance.value = num(data.marginBalance, 0)
    availableBalance.value = num(data.availableBalance, 0)
    activeGridLines.value = Math.max(
      2,
      Math.floor(num(data.activeGridLines, generatorCount.value)),
    )
    if (Array.isArray(data.orders)) {
      orders.value = data.orders as Record<string, unknown>[]
    }
    pushMarkPoint(markPrice.value)
  }

  function clearMarkSeries() {
    markSeriesData.value = []
    lastChartTime = 0
  }

  function pushMarkPoint(price: number) {
    if (!(price > 0)) return
    let t = Math.floor(Date.now() / 1000)
    if (t <= lastChartTime) t = lastChartTime + 1
    lastChartTime = t
    const next = [...markSeriesData.value, { time: t, value: price }]
    if (next.length > 400) next.splice(0, next.length - 400)
    markSeriesData.value = next
  }

  function handleWsPayload(raw: string) {
    let msg: WsMsg
    try {
      msg = JSON.parse(raw) as WsMsg
    } catch {
      return
    }
    lastWsAt.value = Date.now()
    if (msg.type === "snapshot" && msg.data) {
      applySnapshot(msg.data)
      return
    }
    if (msg.type === "mark" && msg.markPrice != null) {
      const markSym = String(msg.symbol ?? "")
        .trim()
        .toUpperCase()
        .replace("/", "")
      const p = num(msg.markPrice)
      if (p > 0 && markSym) {
        markBySymbol.value = { ...markBySymbol.value, [markSym]: p }
        const focus = symbol.value.trim().toUpperCase().replace("/", "")
        if (!focus || markSym === focus) {
          markPrice.value = p
          pushMarkPoint(p)
        }
      }
      return
    }
    if (msg.type === "settings" && msg.data) {
      applySnapshot(msg.data)
      return
    }
    if (msg.type === "grid_metrics" && msg.data) {
      const d = msg.data as Record<string, unknown>
      const sym = String(msg.symbol ?? d.symbol ?? "")
        .trim()
        .toUpperCase()
        .replace("/", "")
      if (sym) {
        const prev = gridsBySymbol.value[sym] ?? {}
        gridsBySymbol.value = {
          ...gridsBySymbol.value,
          [sym]: {
            ...prev,
            ordersPlaced: d.ordersPlaced != null ? Number(d.ordersPlaced) : prev.ordersPlaced,
            virtualExecutions:
              d.virtualExecutions != null
                ? Number(d.virtualExecutions)
                : prev.virtualExecutions,
            sessionRealizedUsdt:
              d.sessionRealizedUsdt != null
                ? num(d.sessionRealizedUsdt)
                : prev.sessionRealizedUsdt,
            cumulativeRealizedUsdt:
              d.cumulativeRealizedUsdt != null
                ? num(d.cumulativeRealizedUsdt)
                : prev.cumulativeRealizedUsdt,
            lineTrail: Array.isArray(d.lineTrail)
              ? (d.lineTrail as GridLineTrailRow[])
              : prev.lineTrail,
            lastError:
              typeof d.lastError === "string" ? d.lastError : prev.lastError,
            startedAt:
              typeof d.startedAt === "string" ? d.startedAt : prev.startedAt,
            running: d.running != null ? Boolean(d.running) : prev.running,
            unrealizedPnlUsdt:
              d.unrealizedPnlUsdt != null
                ? num(d.unrealizedPnlUsdt)
                : prev.unrealizedPnlUsdt,
            gridEquityUsdt:
              d.gridEquityUsdt != null ? num(d.gridEquityUsdt) : prev.gridEquityUsdt,
            deployCapitalUsdt:
              d.deployCapitalUsdt != null
                ? num(d.deployCapitalUsdt)
                : prev.deployCapitalUsdt,
          },
        }
        const cum = num(d.cumulativeRealizedUsdt)
        if (cum > reinjectedRealizedUsdt.value) reinjectedRealizedUsdt.value = cum
      }
      return
    }
    if (msg.type === "metrics" && msg.data) {
      applyAccountMetrics(msg.data as Record<string, unknown>)
      return
    }
    if (msg.type === "sync_error" && typeof msg.message === "string") {
      syncError.value = msg.message
      syncOkAt.value = ""
      balanceSource.value = ""
      return
    }
    if (msg.type === "trades_refresh") {
      const sym = String(msg.symbol ?? "")
        .trim()
        .toUpperCase()
        .replace("/", "")
      if (sym) {
        void fetchTradesForSymbol(sym, { quiet: true })
        if (tradesViewSymbol.value === sym) {
          void fetchTrades({ quiet: true })
        }
      }
      return
    }
    if (msg.type === "order" && msg.data) {
      orders.value = [...orders.value, msg.data]
      return
    }
    if (msg.type === "trade" && msg.data) {
      const row = parseTradeRow(msg.data)
      if (row) {
        const exists = trades.value.some((t) => t.exchangeTradeId === row.exchangeTradeId)
        if (!exists) {
          trades.value = [row, ...trades.value].slice(0, 500)
          tradesSummary.value = {
            count: trades.value.length,
            buyCount: trades.value.filter((t) => t.side === "BUY").length,
            sellCount: trades.value.filter((t) => t.side === "SELL").length,
            totalQuoteVolume: trades.value.reduce((s, t) => s + t.quoteQty, 0),
            totalRealizedPnl: trades.value.reduce((s, t) => s + t.realizedPnl, 0),
            totalCommission: trades.value.reduce((s, t) => s + t.commission, 0),
          }
        }
      }
      return
    }
    if (msg.type === "grid_ledger") {
      applyGridLedgerWs(msg)
      return
    }
    if (msg.type === "emergency") {
      wsError.value = `Emergency stop @ ${msg.ts ?? ""}`
      void fetchGridStatus()
      void fetchGridLedger(symbol.value)
    }
  }

  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let wsPingTimer: ReturnType<typeof setInterval> | null = null
  let reconnectAttempt = 0

  function disconnectWs() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    if (wsPingTimer) {
      clearInterval(wsPingTimer)
      wsPingTimer = null
    }
    const sock = ws.value
    if (sock) {
      sock.onclose = null
      sock.onerror = null
      sock.close()
    }
    ws.value = null
    wsConnected.value = false
  }

  function wsConnectUrl(): string {
    const cfg = useRuntimeConfig()
    const fromEnv = String(cfg.public.wsUrl ?? "").trim()
    if (fromEnv) return fromEnv
    if (typeof window === "undefined") return "ws://127.0.0.1:8090/ws"
    const apiBase = String(cfg.public.apiBase ?? "").trim()
    const host = window.location.hostname
    const isLocal = host === "localhost" || host === "127.0.0.1" || host === "[::1]"
    if (isLocal && !apiBase && import.meta.dev) {
      const h = host === "[::1]" ? "127.0.0.1" : host
      return `ws://${h}:8090/ws`
    }
    const pr = window.location.protocol === "https:" ? "wss:" : "ws:"
    return `${pr}//${window.location.host}/ws`
  }

  function scheduleWsReconnect() {
    if (reconnectTimer) return
    const delay = Math.min(15_000, Math.round(400 * 1.45 ** reconnectAttempt))
    reconnectAttempt += 1
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      connectWs()
    }, delay)
  }

  function connectWs() {
    if (typeof WebSocket === "undefined") return
    const url = wsConnectUrl()
    const existing = ws.value
    if (existing && (existing.readyState === WebSocket.OPEN || existing.readyState === WebSocket.CONNECTING)) {
      return
    }
    if (existing) {
      existing.onclose = null
      existing.close()
    }
    try {
      const socket = new WebSocket(url)
      ws.value = socket
      socket.onopen = () => {
        wsConnected.value = true
        wsError.value = null
        reconnectAttempt = 0
        apiReachable.value = true
        if (wsPingTimer) clearInterval(wsPingTimer)
        wsPingTimer = setInterval(() => {
          if (socket.readyState === WebSocket.OPEN) socket.send("ping")
        }, 20_000)
      }
      socket.onmessage = (ev) => {
        const raw = String(ev.data)
        if (raw === "pong") return
        handleWsPayload(raw)
      }
      socket.onerror = () => {
        wsError.value = "WebSocket error"
      }
      socket.onclose = () => {
        wsConnected.value = false
        if (wsPingTimer) {
          clearInterval(wsPingTimer)
          wsPingTimer = null
        }
        if (ws.value === socket) ws.value = null
        scheduleWsReconnect()
      }
    } catch (e) {
      wsError.value = String(e)
      scheduleWsReconnect()
    }
  }

  async function fetchDashboard() {
    const cfg = useRuntimeConfig()
    const botId = String(cfg.public.botId)
    try {
      const data = await apiFetch<Record<string, unknown>>(
        `${publicApiPrefix()}/api/bots/${botId}/dashboard`,
        { timeout: 12_000 },
      )
      apiReachable.value = true
      applySnapshot(data)
    } catch (e) {
      wsError.value = String(e)
      if (!credentialsLocked) apiReachable.value = false
      throw e
    }
  }

  async function bootstrapDashboard() {
    try {
      await fetchDashboard()
    } catch {
      /* WS may still stream hub state after API comes up */
    }
  }

  async function fetchCredentials() {
    const cfg = useRuntimeConfig()
    const botId = String(cfg.public.botId)
    const data = await apiFetch<{
      hasKeys: boolean
      binanceApiKeyPreview: string
      binanceTestnet: boolean
    }>(`${publicApiPrefix()}/api/bots/${botId}/credentials`)
    if (data.hasKeys) {
      credentialsConfigured.value = true
      credentialsLocked = true
    } else {
      credentialsConfigured.value = false
      credentialsLocked = false
    }
    binanceApiKeyPreview.value = data.binanceApiKeyPreview || ""
    binanceTestnetStored.value = data.binanceTestnet
    exchangeTestnet.value = Boolean(data.hasKeys && data.binanceTestnet)
    apiReachable.value = true
  }

  async function saveCredentials(payload: {
    binanceApiKey: string
    binanceApiSecret: string
    binanceTestnet: boolean
  }) {
    const cfg = useRuntimeConfig()
    const botId = String(cfg.public.botId)
    const res = await apiFetch<{
      ok: boolean
      hasKeys: boolean
      binanceApiKeyPreview: string
      binanceTestnet: boolean
    }>(`${publicApiPrefix()}/api/bots/${botId}/credentials`, { method: "POST", body: payload })
    if (res.hasKeys) {
      credentialsConfigured.value = true
      credentialsLocked = true
    } else {
      credentialsConfigured.value = false
      credentialsLocked = false
    }
    binanceApiKeyPreview.value = res.binanceApiKeyPreview || ""
    binanceTestnetStored.value = res.binanceTestnet
    exchangeTestnet.value = Boolean(res.hasKeys && res.binanceTestnet)
    apiReachable.value = true
    await fetchDashboard()
  }

  async function clearCredentials() {
    const cfg = useRuntimeConfig()
    const botId = String(cfg.public.botId)
    await apiFetch(`${publicApiPrefix()}/api/bots/${botId}/credentials`, { method: "DELETE" })
    credentialsConfigured.value = false
    credentialsLocked = false
    binanceApiKeyPreview.value = ""
    exchangeTestnet.value = false
    await fetchDashboard()
  }

  async function saveGridBand(payload: {
    generatorUpper: number
    generatorLower: number
    generatorCount: number
  }) {
    const cfg = useRuntimeConfig()
    const botId = String(cfg.public.botId)
    const merged = await apiFetch<Record<string, unknown>>(
      `${publicApiPrefix()}/api/bots/${botId}/settings`,
      { method: "PATCH", body: payload },
    )
    applySnapshot(merged)
  }

  async function setAutoCompounding(enabled: boolean) {
    const cfg = useRuntimeConfig()
    const botId = String(cfg.public.botId)
    const merged = await apiFetch<Record<string, unknown>>(
      `${publicApiPrefix()}/api/bots/${botId}/settings`,
      {
        method: "PATCH",
        body: {
          autoCompoundingEnabled: enabled,
          ...(enabled
            ? { profit_injection_mode: "compound_size", compoundingFactor: 1.0 }
            : {}),
        },
      },
    )
    applySnapshot(merged)
  }

  /** @deprecated use saveGridBand — kept for compatibility */
  async function saveSettings(payload: {
    generatorUpper: number
    generatorLower: number
    generatorCount: number
  }) {
    await saveGridBand(payload)
  }

  async function fetchMarkets() {
    const cfg = useRuntimeConfig()
    const botId = String(cfg.public.botId)
    marketsLoading.value = true
    marketsError.value = null
    try {
      const data = await apiFetch<{
        quote: string
        exchangeTestnet: boolean
        updatedAt: string
        symbols: MarketSymbol[]
        excludedStableSymbols?: string[]
      }>(`${publicApiPrefix()}/api/bots/${botId}/markets`, {
        query: { quote: marketsQuote.value },
      })
      marketsQuote.value = data.quote || "USDT"
      marketsExchangeTestnet.value = Boolean(data.exchangeTestnet)
      marketsUpdatedAt.value = data.updatedAt || ""
      markets.value = (data.symbols ?? []).map((row) => ({
        symbol: String(row.symbol).toUpperCase(),
        baseAsset: String(row.baseAsset),
        lastPrice: num(row.lastPrice),
        priceChangePercent: num(row.priceChangePercent),
        quoteVolume: num(row.quoteVolume),
      }))
      excludedStableSymbols.value = Array.isArray(data.excludedStableSymbols)
        ? data.excludedStableSymbols.map((s) => String(s).toUpperCase())
        : []
    } catch (e) {
      marketsError.value = String(e)
    } finally {
      marketsLoading.value = false
    }
  }

  async function selectSymbol(sym: string) {
    const normalized = sym.trim().toUpperCase().replace("/", "")
    if (!normalized || normalized === symbol.value) return
    const prev = symbol.value
    symbol.value = normalized
    clearMarkSeries()
    const cfg = useRuntimeConfig()
    const botId = String(cfg.public.botId)
    try {
      const merged = await apiFetch<Record<string, unknown>>(
        `${publicApiPrefix()}/api/bots/${botId}/settings`,
        { method: "PATCH", body: { symbol: normalized } },
      )
      applySnapshot(merged)
      await fetchTrades()
    } catch (e) {
      symbol.value = prev
      throw e
    }
  }

  function symbolTradesPack(sym: string): SymbolTradesPack {
    const key = sym.trim().toUpperCase().replace("/", "")
    return tradesBySymbol.value[key] ?? emptySymbolTradesPack()
  }

  function gridLedgerPack(sym: string): GridLedgerPack | null {
    const key = sym.trim().toUpperCase().replace("/", "")
    return gridLedgerBySymbol.value[key] ?? null
  }

  function applyGridLedgerWs(msg: Extract<WsMsg, { type: "grid_ledger" }>) {
    const key = String(msg.symbol ?? "")
      .trim()
      .toUpperCase()
      .replace("/", "")
    if (!key) return
    if (msg.cleared) {
      const next = { ...gridLedgerBySymbol.value }
      delete next[key]
      gridLedgerBySymbol.value = next
      return
    }
    const prev = gridLedgerBySymbol.value[key] ?? {
      symbol: key,
      frozen: false,
      freezeReason: "",
      entries: [],
    }
    let entries = [...prev.entries]
    if (Array.isArray(msg.entries)) {
      const parsed = msg.entries
        .map((r) => parseGridLedgerEntry(r as Record<string, unknown>))
        .filter((r): r is GridLedgerEntry => r != null)
      entries = mergeLedgerEntries(entries, parsed)
    } else if (msg.entry) {
      const row = parseGridLedgerEntry(msg.entry)
      if (row) {
        entries = mergeLedgerEntries(entries, [row])
      }
    }
    gridLedgerBySymbol.value = {
      ...gridLedgerBySymbol.value,
      [key]: {
        symbol: key,
        frozen: Boolean(msg.frozen ?? prev.frozen),
        freezeReason: String(msg.freezeReason ?? prev.freezeReason ?? ""),
        entries,
      },
    }
  }

  async function fetchGridLedger(sym: string) {
    const cfg = useRuntimeConfig()
    const botId = String(cfg.public.botId)
    const normalized = sym.trim().toUpperCase().replace("/", "")
    const data = await apiFetch<{
      symbol?: string
      frozen?: boolean
      freezeReason?: string
      entries?: Record<string, unknown>[]
    }>(`${publicApiPrefix()}/api/bots/${botId}/grid/ledger`, {
      query: { symbol: normalized },
      timeout: 10_000,
    })
    const entries = mergeLedgerEntries(
      [],
      (data.entries ?? [])
        .map((r) => parseGridLedgerEntry(r))
        .filter((r): r is GridLedgerEntry => r != null),
    )
    gridLedgerBySymbol.value = {
      ...gridLedgerBySymbol.value,
      [normalized]: {
        symbol: normalized,
        frozen: Boolean(data.frozen),
        freezeReason: String(data.freezeReason ?? ""),
        entries,
      },
    }
  }

  async function clearGridLedger(sym: string) {
    const cfg = useRuntimeConfig()
    const botId = String(cfg.public.botId)
    const normalized = sym.trim().toUpperCase().replace("/", "")
    await apiFetch(`${publicApiPrefix()}/api/bots/${botId}/grid/ledger/clear`, {
      method: "POST",
      query: { symbol: normalized },
    })
    const next = { ...gridLedgerBySymbol.value }
    delete next[normalized]
    gridLedgerBySymbol.value = next
  }

  async function fetchTradesForSymbol(sym: string, opts?: { quiet?: boolean }) {
    const normalized = sym.trim().toUpperCase().replace("/", "")
    if (!normalized) return
    const prev = tradesBySymbol.value[normalized] ?? emptySymbolTradesPack()
    tradesBySymbol.value = {
      ...tradesBySymbol.value,
      [normalized]: { ...prev, loading: !opts?.quiet, error: null },
    }
    try {
      const cfg = useRuntimeConfig()
      const botId = String(cfg.public.botId)
      const data = await apiFetch<{
        symbol: string
        source: string
        syncError: string | null
        updatedAt: string
        summary: TradesSummary
        trades: Record<string, unknown>[]
      }>(`${publicApiPrefix()}/api/bots/${botId}/trades`, {
        query: { symbol: normalized, limit: 150, sync: true },
      })
      tradesBySymbol.value = {
        ...tradesBySymbol.value,
        [normalized]: {
          summary: {
            count: num(data.summary?.count),
            buyCount: num(data.summary?.buyCount),
            sellCount: num(data.summary?.sellCount),
            totalQuoteVolume: num(data.summary?.totalQuoteVolume),
            totalRealizedPnl: num(data.summary?.totalRealizedPnl),
            totalCommission: num(data.summary?.totalCommission),
          },
          trades: (data.trades ?? [])
            .map((r) => parseTradeRow(r))
            .filter((r): r is TradeRow => r != null),
          updatedAt: data.updatedAt || "",
          source: data.source === "binance" ? "binance" : "database",
          syncError: data.syncError || null,
          loading: false,
          error: null,
        },
      }
    } catch (e) {
      tradesBySymbol.value = {
        ...tradesBySymbol.value,
        [normalized]: {
          ...(tradesBySymbol.value[normalized] ?? emptySymbolTradesPack()),
          loading: false,
          error: String(e),
        },
      }
    }
  }

  async function refreshActiveGridTrades() {
    await Promise.all(
      activeGridSymbols.value.map((s) => fetchTradesForSymbol(s, { quiet: true })),
    )
  }

  function openTradesForSymbol(sym: string) {
    const normalized = sym.trim().toUpperCase().replace("/", "")
    tradesViewSymbol.value = normalized
    void fetchTradesForSymbol(normalized)
  }

  async function fetchTrades(opts?: { quiet?: boolean }) {
    const cfg = useRuntimeConfig()
    const botId = String(cfg.public.botId)
    if (!opts?.quiet) tradesLoading.value = true
    tradesError.value = null
    try {
      const data = await apiFetch<{
        symbol: string
        source: string
        syncError: string | null
        updatedAt: string
        summary: TradesSummary
        trades: Record<string, unknown>[]
      }>(`${publicApiPrefix()}/api/bots/${botId}/trades`, {
        query: { symbol: symbol.value, limit: 150, sync: true },
      })
      tradesSymbol.value = data.symbol || symbol.value
      tradesSource.value = data.source === "binance" ? "binance" : "database"
      tradesSyncError.value = data.syncError || null
      tradesUpdatedAt.value = data.updatedAt || ""
      tradesSummary.value = {
        count: num(data.summary?.count),
        buyCount: num(data.summary?.buyCount),
        sellCount: num(data.summary?.sellCount),
        totalQuoteVolume: num(data.summary?.totalQuoteVolume),
        totalRealizedPnl: num(data.summary?.totalRealizedPnl),
        totalCommission: num(data.summary?.totalCommission),
      }
      trades.value = (data.trades ?? [])
        .map((r) => parseTradeRow(r))
        .filter((r): r is TradeRow => r != null)
    } catch (e) {
      if (!opts?.quiet) tradesError.value = String(e)
    } finally {
      if (!opts?.quiet) tradesLoading.value = false
    }
  }

  async function fetchGridStatus() {
    const cfg = useRuntimeConfig()
    const botId = String(cfg.public.botId)
    try {
      const data = await apiFetch<{
        running?: boolean
        symbol?: string
        startedAt?: string
        lastError?: string
        ordersPlaced?: number
        activeSymbols?: string[]
        count?: number
        grids?: Record<string, GridSymbolMeta>
      }>(`${publicApiPrefix()}/api/bots/${botId}/grid/status`)
      const syms = Array.isArray(data.activeSymbols)
        ? data.activeSymbols.map((s) => String(s).toUpperCase().replace("/", ""))
        : []
      activeGridSymbols.value = syms
      gridsBySymbol.value = data.grids ?? {}
      gridRunning.value = syms.length > 0 || Boolean(data.running)
      const totalOrders = syms.reduce(
        (n, s) => n + Number(gridsBySymbol.value[s]?.ordersPlaced ?? 0),
        0,
      )
      gridOrdersPlaced.value = totalOrders
      const primary = syms[0] ?? (typeof data.symbol === "string" ? data.symbol : "")
      gridRunnerSymbol.value = primary.trim().toUpperCase().replace("/", "")
      const firstGrid = gridRunnerSymbol.value ? gridsBySymbol.value[gridRunnerSymbol.value] : null
      gridStartedAt.value =
        typeof firstGrid?.startedAt === "string" && firstGrid.startedAt.trim()
          ? firstGrid.startedAt.trim()
          : typeof data.startedAt === "string" && data.startedAt.trim()
            ? data.startedAt.trim()
            : ""
      const errParts = syms
        .map((s) => {
          const e = String(gridsBySymbol.value[s]?.lastError ?? "").trim()
          return e ? `${s}: ${e}` : ""
        })
        .filter(Boolean)
      gridLastError.value = errParts.join(" · ")
      if (syms.length > 0) {
        gridStatusText.value = `شبكات نشطة: ${syms.join(", ")} · أوامر ${totalOrders}`
        void refreshActiveGridTrades()
      } else {
        gridStatusText.value =
          typeof data.lastError === "string" && data.lastError.trim() ? data.lastError.trim() : ""
        tradesViewSymbol.value = null
      }
    } catch {
      gridRunning.value = false
      gridRunnerSymbol.value = ""
      gridStartedAt.value = ""
      activeGridSymbols.value = []
      gridsBySymbol.value = {}
    }
  }

  async function startGrid(opts?: {
    calibrate?: boolean
    levels?: number
    maxGeneratorCount?: number
    initialCapital?: number
    allocatedCapital?: number
    generatorUpper?: number
    generatorLower?: number
    generatorCount?: number
    trailingOffset?: number
    trailing_stop_pct?: number
    compoundingFactor?: number
    profit_injection_mode?: string
    max_slippage_pct?: number
    dca_mode?: string
  }) {
    const cfg = useRuntimeConfig()
    const botId = String(cfg.public.botId)
    const sym = symbol.value.trim().toUpperCase().replace("/", "") || "DOGEUSDT"
    const lv =
      opts?.generatorCount != null
        ? Math.max(2, Math.floor(opts.generatorCount))
        : opts?.levels != null
          ? Math.max(2, Math.floor(opts.levels))
          : Math.max(2, Math.floor(generatorCount.value))
    const manualBand =
      typeof opts?.generatorUpper === "number" &&
      typeof opts?.generatorLower === "number" &&
      opts.generatorUpper > opts.generatorLower

    const body: Record<string, unknown> = {
      symbol: sym,
      calibrate: false,
      generatorUpper: manualBand ? opts!.generatorUpper! : generatorUpper.value,
      generatorLower: manualBand ? opts!.generatorLower! : generatorLower.value,
      generatorCount: lv,
    }
    if (autoCompoundingEnabled.value) {
      body.autoCompoundingEnabled = true
      body.profit_injection_mode = "compound_size"
      body.compoundingFactor = 1.0
    }
    const alloc =
      opts?.allocatedCapital ?? opts?.initialCapital ?? allocatedCapital.value
    if (alloc > 0) {
      body.allocatedCapital = alloc
      body.initialCapital = alloc
    }

    const res = await apiFetch<Record<string, unknown>>(
      `${publicApiPrefix()}/api/bots/${botId}/grid/start`,
      {
        method: "POST",
        body,
      },
    )
    if (res.settings && typeof res.settings === "object") {
      applySnapshot({ ...res.settings, symbol: sym } as Record<string, unknown>)
    }
    await fetchDashboard()
    await fetchGridStatus()
    await fetchTrades()
    void fetchGridLedger(sym)
    return res
  }

  async function stopGrid(symbol?: string) {
    const cfg = useRuntimeConfig()
    const botId = String(cfg.public.botId)
    const sym = symbol?.trim().toUpperCase().replace("/", "") || ""
    const body = sym ? { symbol: sym } : {}
    await apiFetch(`${publicApiPrefix()}/api/bots/${botId}/grid/stop`, {
      method: "POST",
      body,
    })
    if (sym && tradesViewSymbol.value === sym) {
      tradesViewSymbol.value = null
    }
    await fetchDashboard()
    await fetchGridStatus()
  }

  async function emergencyStop() {
    const cfg = useRuntimeConfig()
    const botId = String(cfg.public.botId)
    await apiFetch(`${publicApiPrefix()}/api/emergency_stop`, {
      method: "POST",
      body: { bot_id: botId },
    })
  }

  /** إيقاف الشبكة فوراً ثم طوارئ Spot: إلغاء كل أوامر الزوج وبيع الأساس بالسوق (حسب حالة الـ Hub). */
  async function stopGridAndFlattenSpot() {
    await stopGrid()
    await emergencyStop()
    await fetchDashboard()
    await fetchGridStatus()
    await fetchTrades()
  }

  async function fetchAuditLogs(limit = 200): Promise<AuditLogRow[]> {
    const cfg = useRuntimeConfig()
    const botId = String(cfg.public.botId)
    const data = await apiFetch<{ logs?: AuditLogRow[] }>(
      `${publicApiPrefix()}/api/bots/${botId}/audit`,
      {
        query: { limit },
        timeout: 12_000,
      },
    )
    return Array.isArray(data.logs) ? data.logs : []
  }

  return {
    ws,
    wsConnected,
    wsError,
    lastWsAt,
    credentialsConfigured,
    apiReachable,
    binanceApiKeyPreview,
    binanceTestnetStored,
    syncError,
    syncErrorHint,
    syncOkAt,
    exchangeTestnet,
    binanceEnv,
    spotEnvLabel,
    spotEnvLabelAr,
    hasActiveGrids,
    hasOpenExchangeOrders,
    showLiveBotPanels,
    isGridActiveForSelectedSymbol,
    otherActiveGridSymbols,
    symbol,
    markPrice,
    markBySymbol,
    symbolMark,
    generatorUpper,
    generatorLower,
    generatorCount,
    maxGeneratorCount,
    initialCapital,
    allocatedCapital,
    selectedGridMeta,
    otherGridsAllocatedUsdt,
    maxAllocatableUsdt,
    gridPeakEquityUsdt,
    gridDrawdownPct,
    gridReinjectedUsdt,
    realizedPnl,
    floatingPnl,
    totalWalletBalance,
    totalMarginBalance,
    currentCapital,
    marginBalance,
    availableBalance,
    balanceSource,
    balanceSyncState,
    balanceIsLive,
    liveEquityUsdt,
    peakEquityUsdt,
    currentDrawdownPct,
    trailingEquityStopEnabled,
    trailingEquityDrawdownLimitPct,
    trailingEquityStopTriggered,
    reinjectedRealizedUsdt,
    autoCompoundingEnabled,
    activeGridLines,
    orders,
    markSeriesData,
    clearMarkSeries,
    markets,
    marketsQuote,
    marketsLoading,
    marketsError,
    marketsUpdatedAt,
    marketsExchangeTestnet,
    excludedStableSymbols,
    trades,
    tradesSummary,
    tradesLoading,
    tradesError,
    tradesSyncError,
    tradesSource,
    tradesSymbol,
    tradesUpdatedAt,
    gridRunning,
    gridStatusText,
    gridOrdersPlaced,
    gridLastError,
    gridRunnerSymbol,
    gridStartedAt,
    activeGridSymbols,
    gridsBySymbol,
    tradesBySymbol,
    tradesViewSymbol,
    gridLedgerBySymbol,
    gridLedgerPack,
    fetchGridLedger,
    clearGridLedger,
    symbolTradesPack,
    fetchTradesForSymbol,
    refreshActiveGridTrades,
    openTradesForSymbol,
    gridLevels,
    applySnapshot,
    connectWs,
    disconnectWs,
    fetchDashboard,
    bootstrapDashboard,
    fetchCredentials,
    saveCredentials,
    clearCredentials,
    saveSettings,
    saveGridBand,
    setAutoCompounding,
    fetchMarkets,
    selectSymbol,
    fetchTrades,
    fetchGridStatus,
    startGrid,
    stopGrid,
    emergencyStop,
    stopGridAndFlattenSpot,
    wsConnectUrl,
    fetchAuditLogs,
  }
})
