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
  lineTrail?: GridLineTrailRow[]
}

const EMPTY_TRADES_SUMMARY: TradesSummary = {
  count: 0,
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
  const generatorUpper = ref(0)
  const generatorLower = ref(0)
  const generatorCount = ref(5)
  const maxGeneratorCount = ref(9999)
  const initialCapital = ref(100)
  const realizedPnl = ref(0)
  const floatingPnl = ref(0)
  const totalWalletBalance = ref(0)
  const totalMarginBalance = ref(0)
  const currentCapital = ref(0)
  const marginBalance = ref(0)
  const availableBalance = ref(0)
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

  function applyCredentialsFields(data: Record<string, unknown>) {
    if (data.credentialsConfigured === true) {
      credentialsConfigured.value = true
      credentialsLocked = true
    } else if (data.credentialsConfigured === false && !credentialsLocked) {
      credentialsConfigured.value = false
    }
  }

  function applySnapshot(data: Record<string, unknown>) {
    applyCredentialsFields(data)
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
      const p = num(msg.markPrice)
      if (p > 0) {
        markPrice.value = p
        pushMarkPoint(p)
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
          },
        }
      }
      return
    }
    if (msg.type === "metrics" && msg.data) {
      const d = msg.data
      if (d.totalWalletBalance != null) totalWalletBalance.value = num(d.totalWalletBalance)
      if (d.totalMarginBalance != null) totalMarginBalance.value = num(d.totalMarginBalance)
      if (d.currentCapital != null) currentCapital.value = num(d.currentCapital)
      if (d.marginBalance != null) marginBalance.value = num(d.marginBalance)
      if (d.availableBalance != null) availableBalance.value = num(d.availableBalance)
      if (d.floatingPnl != null) floatingPnl.value = num(d.floatingPnl)
      if (d.realizedPnl != null) realizedPnl.value = num(d.realizedPnl)
      if (typeof d.syncError === "string") syncError.value = d.syncError
      if (typeof d.syncOkAt === "string") syncOkAt.value = d.syncOkAt
      if (typeof d.exchangeTestnet === "boolean") exchangeTestnet.value = d.exchangeTestnet
      return
    }
    if (msg.type === "sync_error" && typeof msg.message === "string") {
      syncError.value = msg.message
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
    if (msg.type === "emergency") {
      wsError.value = `Emergency stop @ ${msg.ts ?? ""}`
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
      const data = await $fetch<Record<string, unknown>>(
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
    const cfg = useRuntimeConfig()
    const botId = String(cfg.public.botId)
    try {
      const cred = await $fetch<{
        hasKeys: boolean
        binanceApiKeyPreview: string
        binanceTestnet: boolean
      }>(`${publicApiPrefix()}/api/bots/${botId}/credentials`, { timeout: 8_000 })
      if (cred.hasKeys) {
        credentialsConfigured.value = true
        credentialsLocked = true
        binanceApiKeyPreview.value = cred.binanceApiKeyPreview || ""
        binanceTestnetStored.value = cred.binanceTestnet
        exchangeTestnet.value = Boolean(cred.binanceTestnet)
      }
      apiReachable.value = true
    } catch {
      /* credentials endpoint optional if dashboard succeeds */
    }
    try {
      await fetchDashboard()
    } catch {
      /* keep locked credentials; WS may still stream hub state */
    }
  }

  async function fetchCredentials() {
    const cfg = useRuntimeConfig()
    const botId = String(cfg.public.botId)
    const data = await $fetch<{
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
    const res = await $fetch<{
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
    await $fetch(`${publicApiPrefix()}/api/bots/${botId}/credentials`, { method: "DELETE" })
    credentialsConfigured.value = false
    credentialsLocked = false
    binanceApiKeyPreview.value = ""
    exchangeTestnet.value = false
    await fetchDashboard()
  }

  async function saveSettings(payload: {
    generatorUpper: number
    generatorLower: number
    generatorCount: number
    maxGeneratorCount?: number
    initialCapital: number
    trailingOffset?: number
    trailing_stop_pct?: number
    compoundingFactor?: number
    profit_injection_mode?: string
    max_slippage_pct?: number
    dca_mode?: string
  }) {
    const cfg = useRuntimeConfig()
    const botId = String(cfg.public.botId)
    const merged = await $fetch<Record<string, unknown>>(
      `${publicApiPrefix()}/api/bots/${botId}/settings`,
      { method: "PATCH", body: payload },
    )
    applySnapshot(merged)
  }

  async function fetchMarkets() {
    const cfg = useRuntimeConfig()
    const botId = String(cfg.public.botId)
    marketsLoading.value = true
    marketsError.value = null
    try {
      const data = await $fetch<{
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
    const cfg = useRuntimeConfig()
    const botId = String(cfg.public.botId)
    const merged = await $fetch<Record<string, unknown>>(
      `${publicApiPrefix()}/api/bots/${botId}/settings`,
      { method: "PATCH", body: { symbol: normalized } },
    )
    clearMarkSeries()
    applySnapshot(merged)
    await fetchTrades()
  }

  function symbolTradesPack(sym: string): SymbolTradesPack {
    const key = sym.trim().toUpperCase().replace("/", "")
    return tradesBySymbol.value[key] ?? emptySymbolTradesPack()
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
      const data = await $fetch<{
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
      const data = await $fetch<{
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
      const data = await $fetch<{
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
    const mx =
      opts?.maxGeneratorCount != null
        ? Math.max(2, Math.floor(opts.maxGeneratorCount))
        : Math.max(lv + 1, Math.floor(maxGeneratorCount.value))
    const cap =
      opts?.initialCapital != null && opts.initialCapital > 0
        ? opts.initialCapital
        : initialCapital.value > 0
          ? initialCapital.value
          : 40

    const manualBand =
      typeof opts?.generatorUpper === "number" &&
      typeof opts?.generatorLower === "number" &&
      opts.generatorUpper > opts.generatorLower
    const useManual = opts?.calibrate === false || manualBand

    const body: Record<string, unknown> = {
      symbol: sym,
      initialCapital: cap,
    }
    const advKeys = [
      "trailingOffset",
      "trailing_stop_pct",
      "compoundingFactor",
      "profit_injection_mode",
      "max_slippage_pct",
      "dca_mode",
    ] as const
    for (const k of advKeys) {
      const v = opts?.[k]
      if (v != null && v !== "") body[k] = v
    }

    if (useManual) {
      body.calibrate = false
      body.generatorUpper = manualBand ? opts!.generatorUpper! : generatorUpper.value
      body.generatorLower = manualBand ? opts!.generatorLower! : generatorLower.value
      body.generatorCount = lv
      body.maxGeneratorCount = mx
    } else {
      body.calibrate = true
      body.levels = lv
      body.maxGeneratorCount = mx
      if (opts?.generatorCount != null) {
        body.generatorCount = lv
      }
    }

    const res = await $fetch<Record<string, unknown>>(
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
    return res
  }

  async function stopGrid(symbol?: string) {
    const cfg = useRuntimeConfig()
    const botId = String(cfg.public.botId)
    const sym = symbol?.trim().toUpperCase().replace("/", "") || ""
    const body = sym ? { symbol: sym } : {}
    await $fetch(`${publicApiPrefix()}/api/bots/${botId}/grid/stop`, {
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
    await $fetch(`${publicApiPrefix()}/api/emergency_stop`, {
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
    const data = await $fetch<{ logs?: AuditLogRow[] }>(
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
    generatorUpper,
    generatorLower,
    generatorCount,
    maxGeneratorCount,
    initialCapital,
    realizedPnl,
    floatingPnl,
    totalWalletBalance,
    totalMarginBalance,
    currentCapital,
    marginBalance,
    availableBalance,
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
