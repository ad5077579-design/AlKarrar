import { defineStore } from "pinia"
import { computed, ref, shallowRef } from "vue"

type WsMsg =
  | { type: "snapshot"; data: Record<string, unknown> }
  | { type: "mark"; markPrice: number; t?: number }
  | { type: "settings"; data: Record<string, unknown> }
  | { type: "metrics"; data: Record<string, unknown> }
  | { type: "sync_error"; message: string }
  | { type: "order"; data: Record<string, unknown> }
  | { type: "emergency"; bot_id?: string; ts?: string }

function num(v: unknown, fallback = 0): number {
  const n = typeof v === "number" ? v : Number(v)
  return Number.isFinite(n) ? n : fallback
}

export const useBotStore = defineStore("bot", () => {
  const ws = shallowRef<WebSocket | null>(null)
  const wsConnected = ref(false)
  const wsError = ref<string | null>(null)
  const lastWsAt = ref(0)

  const credentialsConfigured = ref(false)
  const binanceApiKeyPreview = ref("")
  const binanceTestnetStored = ref(true)
  const syncError = ref("")
  const syncOkAt = ref("")
  const exchangeTestnet = ref(false)

  const symbol = ref("DOGEUSDT")
  const markPrice = ref(0)
  const generatorUpper = ref(0)
  const generatorLower = ref(0)
  const generatorCount = ref(5)
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

  function applySnapshot(data: Record<string, unknown>) {
    if (typeof data.credentialsConfigured === "boolean") {
      credentialsConfigured.value = data.credentialsConfigured
    }
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
    if (typeof data.symbol === "string" && data.symbol.trim()) {
      symbol.value = data.symbol.trim().toUpperCase().replace("/", "")
    }
    markPrice.value = num(data.markPrice)
    generatorUpper.value = num(data.generatorUpper)
    generatorLower.value = num(data.generatorLower)
    generatorCount.value = Math.max(2, Math.floor(num(data.generatorCount, 5)))
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
    if (msg.type === "emergency") {
      wsError.value = `Emergency stop @ ${msg.ts ?? ""}`
    }
  }

  let reconnectTimer: ReturnType<typeof setTimeout> | null = null

  function disconnectWs() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    ws.value?.close()
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

  function connectWs() {
    const url = wsConnectUrl()
    disconnectWs()
    try {
      const socket = new WebSocket(url)
      ws.value = socket
      socket.onopen = () => {
        wsConnected.value = true
        wsError.value = null
      }
      socket.onmessage = (ev) => handleWsPayload(String(ev.data))
      socket.onerror = () => {
        wsError.value = "WebSocket error"
      }
      socket.onclose = () => {
        wsConnected.value = false
        ws.value = null
        reconnectTimer = setTimeout(connectWs, 2000)
      }
    } catch (e) {
      wsError.value = String(e)
      reconnectTimer = setTimeout(connectWs, 3000)
    }
  }

  async function fetchDashboard() {
    const cfg = useRuntimeConfig()
    const botId = String(cfg.public.botId)
    const data = await $fetch<Record<string, unknown>>(
      `${publicApiPrefix()}/api/bots/${botId}/dashboard`,
    )
    applySnapshot(data)
  }

  async function fetchCredentials() {
    const cfg = useRuntimeConfig()
    const botId = String(cfg.public.botId)
    const data = await $fetch<{
      hasKeys: boolean
      binanceApiKeyPreview: string
      binanceTestnet: boolean
    }>(`${publicApiPrefix()}/api/bots/${botId}/credentials`)
    credentialsConfigured.value = data.hasKeys
    binanceApiKeyPreview.value = data.binanceApiKeyPreview || ""
    binanceTestnetStored.value = data.binanceTestnet
    exchangeTestnet.value = Boolean(data.hasKeys && data.binanceTestnet)
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
    credentialsConfigured.value = res.hasKeys
    binanceApiKeyPreview.value = res.binanceApiKeyPreview || ""
    binanceTestnetStored.value = res.binanceTestnet
    exchangeTestnet.value = Boolean(res.hasKeys && res.binanceTestnet)
    await fetchDashboard()
  }

  async function clearCredentials() {
    const cfg = useRuntimeConfig()
    const botId = String(cfg.public.botId)
    await $fetch(`${publicApiPrefix()}/api/bots/${botId}/credentials`, { method: "DELETE" })
    credentialsConfigured.value = false
    binanceApiKeyPreview.value = ""
    exchangeTestnet.value = false
    await fetchDashboard()
  }

  async function saveSettings(payload: {
    generatorUpper: number
    generatorLower: number
    generatorCount: number
    initialCapital: number
  }) {
    const cfg = useRuntimeConfig()
    const botId = String(cfg.public.botId)
    const merged = await $fetch<Record<string, unknown>>(
      `${publicApiPrefix()}/api/bots/${botId}/settings`,
      { method: "PATCH", body: payload },
    )
    applySnapshot(merged)
  }

  async function emergencyStop() {
    const cfg = useRuntimeConfig()
    const botId = String(cfg.public.botId)
    await $fetch(`${publicApiPrefix()}/api/emergency_stop`, {
      method: "POST",
      body: { bot_id: botId },
    })
  }

  return {
    ws,
    wsConnected,
    wsError,
    lastWsAt,
    credentialsConfigured,
    binanceApiKeyPreview,
    binanceTestnetStored,
    syncError,
    syncOkAt,
    exchangeTestnet,
    symbol,
    markPrice,
    generatorUpper,
    generatorLower,
    generatorCount,
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
    gridLevels,
    applySnapshot,
    connectWs,
    disconnectWs,
    fetchDashboard,
    fetchCredentials,
    saveCredentials,
    clearCredentials,
    saveSettings,
    emergencyStop,
    wsConnectUrl,
  }
})
