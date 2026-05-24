<script setup lang="ts">
import {
  createChart,
  createSeriesMarkers,
  LineSeries,
  CandlestickSeries,
  BaselineSeries,
  HistogramSeries,
  ColorType,
} from "lightweight-charts"
import type {
  AutoscaleInfo,
  IChartApi,
  ISeriesApi,
  ISeriesMarkersPluginApi,
  LogicalRangeChangeEventHandler,
  MouseEventParams,
  UTCTimestamp,
  CandlestickData,
  Time,
} from "lightweight-charts"
import { useBotStore } from "~/stores/bot"
import {
  buildLedgerFillMarkers,
  buildTradeActivityHistogram,
  buildTradeMarkers,
  chartTradesForSymbol,
  formatChartPrice,
  tradeSummaryForSymbol,
} from "~/utils/chartTradeMarkers"
import {
  buildSessionBandPoints,
  bandMatchesMark,
  envHostHint,
  envTradingLabelAr,
  formatSessionDuration,
  markFeedAgeSec,
  normalizeSpotEnv,
  sessionBackdropColors,
  sessionStartSec,
  type SpotEnvKind,
} from "~/utils/chartLiveLayer"

const store = useBotStore()
const cfg = useRuntimeConfig()
const root = ref<HTMLDivElement | null>(null)
const klinesError = ref<string | null>(null)
/** Cleared in ``finally`` after every klines fetch (success, error, or empty). */
const isLoading = ref(true)
const showTrades = ref(true)
const showBand = ref(true)
const showVolume = ref(true)
const showGridLines = ref(true)
/** طبقة جلسة التداول الحي على الشارت (حسب بيئة المنصة). */
const showLiveLayer = ref(true)
const liveClock = ref(Date.now())
let liveClockTimer: ReturnType<typeof setInterval> | null = null
const crosshairTip = ref<{ visible: boolean; x: number; y: number; text: string }>({
  visible: false,
  x: 0,
  y: 0,
  text: "",
})

/** Must match ``interval`` query to ``/klines`` (seconds per bar). */
const KLINES_INTERVAL = "15m"
const KLINES_INTERVAL_SEC = 15 * 60
const KLINES_LIMIT = 300
/** In-memory series copy for live OHLC updates from mark WebSocket. */
let syncedCandles: CandlestickData<Time>[] = []

let chart: IChartApi | null = null
let candleSeries: ISeriesApi<"Candlestick"> | null = null
let lineSeries: ISeriesApi<"Line"> | null = null
let tradeMarkers: ISeriesMarkersPluginApi<Time> | null = null
let bandSeries: ISeriesApi<"Baseline"> | null = null
/** Session window fill — created before candles so it stays behind OHLC. */
let sessionBandSeries: ISeriesApi<"Baseline"> | null = null
let klineVolumeSeries: ISeriesApi<"Histogram"> | null = null
let tradeFlowSeries: ISeriesApi<"Histogram"> | null = null
const volumeByTime = new Map<number, number>()
/** Series that owns generator / grid price lines */
let priceLineHost: ISeriesApi<"Candlestick"> | ISeriesApi<"Line"> | null = null
const priceLines = shallowRef<object[]>([])
let klinesRetryTimer: ReturnType<typeof setTimeout> | null = null
/** User panned/zoomed — do not reset scale on live mark ticks. */
let viewportLockedByUser = false
let suppressViewportLock = false
let onVisibleLogicalRangeChange: LogicalRangeChangeEventHandler | null = null
let onChartWheel: (() => void) | null = null
let onCrosshairMove: ((p: MouseEventParams<Time>) => void) | null = null

const VOLUME_SCALE_ID = "volume"

const MARK_LINE_SERIES_OPTS = {
  color: "#38bdf8",
  lineWidth: 2,
  priceLineVisible: false,
  lastValueVisible: true,
  title: "Mark",
  priceScaleId: "right",
} as const

const CANDLE_SERIES_OPTS = {
  upColor: "#0ecb81",
  downColor: "#f6465d",
  borderVisible: true,
  wickVisible: true,
  wickUpColor: "#0ecb81",
  wickDownColor: "#f6465d",
  priceLineVisible: false,
  lastValueVisible: true,
  priceScaleId: "right",
} as const

/** OHLC + mark only — grid price lines must not squash the candle viewport. */
function tradePriceAutoscaleInfo(): AutoscaleInfo | null {
  let min = Number.POSITIVE_INFINITY
  let max = Number.NEGATIVE_INFINITY
  for (const c of syncedCandles) {
    min = Math.min(min, c.low as number)
    max = Math.max(max, c.high as number)
  }
  const mp = chartMarkPrice.value
  if (mp > 0) {
    min = Math.min(min, mp)
    max = Math.max(max, mp)
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return null
  if (min === max) {
    const eps = Math.max(min * 0.01, 1e-8)
    min -= eps
    max += eps
  }
  const pad = Math.max((max - min) * 0.1, max * 0.002)
  return {
    priceRange: { minValue: min - pad, maxValue: max + pad },
    margins: { above: 14, below: 14 },
  }
}

const AUTOSCALE_FROM_CANDLES = () => tradePriceAutoscaleInfo()

const gridMetaHere = computed(() => store.selectedGridMeta)

function resolveKlinesSymbol(): string {
  const s = String(store.symbol ?? "")
    .trim()
    .toUpperCase()
    .replace("/", "")
  if (/^[A-Z0-9]{4,}$/.test(s)) return s
  return "DOGEUSDT"
}

const chartMarkPrice = computed(() => {
  const sym = resolveKlinesSymbol()
  const room = store.symbolMark(sym)
  if (room > 0) return room
  return store.markPrice
})

/** Grid band only when it matches the symbol's live mark (avoids squashing OHLC). */
const effectiveGridBand = computed(() => {
  const hi = store.generatorUpper
  const lo = store.generatorLower
  const mark = chartMarkPrice.value
  if (!(hi > lo) || !(mark > 0)) return null
  if (!bandMatchesMark(hi, lo, mark)) return null
  return { hi, lo }
})

const bandStaleForChart = computed(() => {
  const hi = store.generatorUpper
  const lo = store.generatorLower
  const mark = chartMarkPrice.value
  return hi > lo && mark > 0 && !bandMatchesMark(hi, lo, mark)
})

const chartSessionSince = computed(() => {
  if (!store.isGridActiveForSelectedSymbol) return ""
  return gridMetaHere.value?.startedAt?.trim() ?? ""
})

const tradeStats = computed(() => {
  const sym = resolveKlinesSymbol()
  const pack = store.symbolTradesPack(sym)
  const merged = chartTradesForSymbol(sym, pack.trades, store.trades, chartSessionSince.value)
  return tradeSummaryForSymbol(merged, sym)
})

const markInsideBand = computed(() => {
  const band = effectiveGridBand.value
  if (!band) return false
  const m = chartMarkPrice.value
  return m >= band.lo && m <= band.hi
})

const spotEnv = computed((): SpotEnvKind =>
  normalizeSpotEnv(store.binanceEnv, store.exchangeTestnet),
)

const sessionStart = computed(() => sessionStartSec(gridMetaHere.value?.startedAt))

const openOrdersOnSymbol = computed(() => {
  const sym = resolveKlinesSymbol()
  return store.orders.filter(
    (o) => String((o as Record<string, unknown>).symbol ?? "").toUpperCase() === sym,
  ).length
})

const liveStrip = computed(() => {
  liveClock.value
  const env = spotEnv.value
  const sym = resolveKlinesSymbol()
  const gridHere = store.isGridActiveForSelectedSymbol
  const otherGrids = store.otherActiveGridSymbols.length
  const wsOk = store.wsConnected
  const feedSec = markFeedAgeSec(store.lastWsAt)
  const feedStale = !wsOk || (feedSec != null && feedSec > 25)
  const balanceLive = store.balanceIsLive
  const start = sessionStart.value
  const meta = gridMetaHere.value
  const sessionPnl = Number(meta?.sessionRealizedUsdt ?? 0)
  const alloc = Number(meta?.allocatedCapital ?? store.allocatedCapital ?? 0)

  let mode: "idle" | "live" | "other" | "orders" = "idle"
  if (gridHere) mode = "live"
  else if (otherGrids > 0) mode = "other"
  else if (openOrdersOnSymbol.value > 0) mode = "orders"

  return {
    env,
    envLabel: envTradingLabelAr(env),
    host: envHostHint(env),
    mode,
    sym,
    wsOk,
    feedSec,
    feedStale,
    balanceLive,
    gridHere,
    otherGrids,
    start,
    sessionDur: start != null ? formatSessionDuration(start) : "",
    sessionPnl,
    alloc,
    orders: openOrdersOnSymbol.value,
    virtualExec: Number(meta?.virtualExecutions ?? 0),
    placed: Number(meta?.ordersPlaced ?? 0),
    isMainnet: env === "mainnet",
  }
})

function seriesForPriceLines() {
  return priceLineHost
}

function clearPriceLines() {
  const s = seriesForPriceLines()
  if (!s) return
  for (const pl of priceLines.value) {
    s.removePriceLine(pl as never)
  }
  priceLines.value = []
}

function rebuildPriceLines() {
  const s = seriesForPriceLines()
  if (!s) return
  clearPriceLines()
  const band = effectiveGridBand.value
  if (!band || !showGridLines.value) return
  const next: object[] = []
  const hi = band.hi
  const lo = band.lo
  if (Number.isFinite(hi) && hi > 0) {
    next.push(
      s.createPriceLine({
        price: hi,
        color: "#f6465d",
        lineWidth: 2,
        axisLabelVisible: true,
        title: "generatorUpper",
      }),
    )
  }
  if (Number.isFinite(lo) && lo > 0) {
    next.push(
      s.createPriceLine({
        price: lo,
        color: "#0ecb81",
        lineWidth: 2,
        axisLabelVisible: true,
        title: "generatorLower",
      }),
    )
  }
  const levels = showGridLines.value ? store.gridLevels : []
  const mid = hi > lo ? (hi + lo) / 2 : 0
  if (mid > 0) {
    next.push(
      s.createPriceLine({
        price: mid,
        color: "rgba(240, 185, 11, 0.75)",
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: "mid",
      }),
    )
  }
  for (let i = 1; i < levels.length - 1; i++) {
    const p = levels[i]
    if (!Number.isFinite(p)) continue
    next.push(
      s.createPriceLine({
        price: p,
        color: "rgba(148,163,184,0.55)",
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: false,
        title: `grid-${i}`,
      }),
    )
  }
  priceLines.value = next
}

function bindViewportLockHandlers() {
  if (!chart) return
  if (onVisibleLogicalRangeChange) {
    chart.timeScale().unsubscribeVisibleLogicalRangeChange(onVisibleLogicalRangeChange)
  }
  onVisibleLogicalRangeChange = () => {
    if (suppressViewportLock || viewportLockedByUser) return
    viewportLockedByUser = true
    chart?.applyOptions({ rightPriceScale: { autoScale: false } })
  }
  chart.timeScale().subscribeVisibleLogicalRangeChange(onVisibleLogicalRangeChange)
  const el = root.value
  if (el) {
    onChartWheel = () => {
      if (suppressViewportLock) return
      viewportLockedByUser = true
      chart?.applyOptions({ rightPriceScale: { autoScale: false } })
    }
    el.addEventListener("wheel", onChartWheel, { passive: true })
  }
}

function unbindViewportLockHandlers() {
  if (chart && onVisibleLogicalRangeChange) {
    chart.timeScale().unsubscribeVisibleLogicalRangeChange(onVisibleLogicalRangeChange)
    onVisibleLogicalRangeChange = null
  }
  const el = root.value
  if (el && onChartWheel) {
    el.removeEventListener("wheel", onChartWheel)
    onChartWheel = null
  }
}

function applyDefaultViewport() {
  if (!chart) return
  suppressViewportLock = true
  try {
    chart.timeScale().fitContent()
    chart.timeScale().scrollToRealTime()
  } finally {
    suppressViewportLock = false
  }
}

function resetChartViewport() {
  if (!chart) return
  viewportLockedByUser = false
  chart.applyOptions({ rightPriceScale: { autoScale: true } })
  applyDefaultViewport()
}

/** Align mark ticks to candle buckets so the time scale matches OHLC bars. */
function bucketMarkSeries(data: { time: number; value: number }[]) {
  const byBucket = new Map<number, number>()
  for (const p of data) {
    if (!(p.value > 0)) continue
    const b = bucketStart(Math.floor(p.time))
    byBucket.set(b, p.value)
  }
  const mark = store.markPrice
  if (mark > 0) {
    byBucket.set(bucketStart(Math.floor(Date.now() / 1000)), mark)
  }
  const pts = [...byBucket.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([time, value]) => ({ time: time as UTCTimestamp, value }))
  if (pts.length === 0) return []
  if (pts.length === 1) {
    const p = pts[0]
    return [
      { time: p.time, value: p.value },
      { time: (p.time + KLINES_INTERVAL_SEC) as UTCTimestamp, value: p.value },
    ]
  }
  return pts
}

function parseKlinesPayload(rows: unknown): CandlestickData<Time>[] {
  volumeByTime.clear()
  if (!Array.isArray(rows)) return []
  const parsed: CandlestickData<Time>[] = []
  for (const raw of rows) {
    if (Array.isArray(raw) && raw.length >= 6) {
      const t0 = Number(raw[0])
      const t = Math.floor(t0 >= 1e12 ? t0 / 1000 : t0)
      const open = Number(raw[1])
      const high = Number(raw[2])
      const low = Number(raw[3])
      const close = Number(raw[4])
      const volume = Number(raw[5])
      if (
        !Number.isFinite(t) ||
        !Number.isFinite(open) ||
        !Number.isFinite(high) ||
        !Number.isFinite(low) ||
        !Number.isFinite(close)
      ) {
        continue
      }
      parsed.push({ time: t as UTCTimestamp, open, high, low, close })
      if (Number.isFinite(volume) && volume > 0) volumeByTime.set(t, volume)
      continue
    }
    if (!raw || typeof raw !== "object") continue
    const r = raw as Record<string, unknown>
    const tRaw = Number(r.time)
    const t = Math.floor(tRaw >= 1e12 ? tRaw / 1000 : tRaw)
    const open = Number(r.open)
    const high = Number(r.high)
    const low = Number(r.low)
    const close = Number(r.close)
    if (
      !Number.isFinite(t) ||
      !Number.isFinite(open) ||
      !Number.isFinite(high) ||
      !Number.isFinite(low) ||
      !Number.isFinite(close)
    ) {
      continue
    }
    const volume = Number(r.volume ?? (Array.isArray(raw) && raw.length > 5 ? raw[5] : 0))
    parsed.push({
      time: t as UTCTimestamp,
      open,
      high,
      low,
      close,
    })
    if (Number.isFinite(volume) && volume > 0) {
      volumeByTime.set(t, volume)
    }
  }
  parsed.sort((a, b) => (a.time as number) - (b.time as number))
  const dedup: CandlestickData<Time>[] = []
  for (const c of parsed) {
    const prev = dedup[dedup.length - 1]
    if (prev && prev.time === c.time) {
      dedup[dedup.length - 1] = c
      continue
    }
    dedup.push(c)
  }
  return dedup
}

function applyMarkLineStyle() {
  if (!lineSeries) return
  const inBand = markInsideBand.value
  lineSeries.applyOptions({
    color: inBand ? "#38bdf8" : "#f59e0b",
    title: inBand ? "Mark" : "Mark (خارج النطاق)",
  })
}

function syncBandArea() {
  if (!chart) return
  const hi = store.generatorUpper
  const lo = store.generatorLower
  if (!showBand.value || !(hi > lo) || syncedCandles.length === 0) {
    if (bandSeries) {
      bandSeries.setData([])
    }
    return
  }
  if (!bandSeries) {
    bandSeries = chart.addSeries(BaselineSeries, {
      baseValue: { type: "price", price: lo },
      topFillColor1: "rgba(14, 203, 129, 0.22)",
      topFillColor2: "rgba(14, 203, 129, 0.06)",
      bottomFillColor1: "rgba(14, 203, 129, 0.02)",
      bottomFillColor2: "rgba(14, 203, 129, 0.02)",
      lineVisible: false,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
      priceScaleId: "right",
    })
  } else {
    bandSeries.applyOptions({ baseValue: { type: "price", price: lo } })
  }
  bandSeries.setData(
    syncedCandles.map((c) => ({
      time: c.time,
      value: hi,
    })),
  )
}

function ensureSessionBandSeries() {
  if (!chart || sessionBandSeries) return
  const lo = Math.max(store.generatorLower || 0, 1e-12)
  sessionBandSeries = chart.addSeries(BaselineSeries, {
    baseValue: { type: "price", price: lo },
    lineVisible: false,
    lastValueVisible: false,
    priceLineVisible: false,
    crosshairMarkerVisible: false,
    priceScaleId: "right",
    ...sessionBackdropColors(spotEnv.value),
  })
}

function syncSessionBackdrop() {
  if (!chart) return
  const hi = store.generatorUpper
  const lo = store.generatorLower
  const start = sessionStart.value
  const active = store.isGridActiveForSelectedSymbol && start != null

  if (!showLiveLayer.value || !active || !(hi > lo) || syncedCandles.length === 0) {
    sessionBandSeries?.setData([])
    return
  }

  ensureSessionBandSeries()
  if (!sessionBandSeries) return

  const colors = sessionBackdropColors(spotEnv.value)
  sessionBandSeries.applyOptions({
    baseValue: { type: "price", price: lo },
    ...colors,
  })
  sessionBandSeries.setData(buildSessionBandPoints(syncedCandles, hi, start))
}

function syncVolumePanels() {
  if (!chart) return
  const show = showVolume.value && syncedCandles.length > 0
  if (!show) {
    klineVolumeSeries?.setData([])
    tradeFlowSeries?.setData([])
    return
  }
  if (!klineVolumeSeries) {
    klineVolumeSeries = chart.addSeries(HistogramSeries, {
      priceScaleId: VOLUME_SCALE_ID,
      priceFormat: { type: "volume" },
      color: "rgba(148, 163, 184, 0.35)",
    })
    tradeFlowSeries = chart.addSeries(HistogramSeries, {
      priceScaleId: VOLUME_SCALE_ID,
      priceFormat: { type: "volume" },
    })
    chart.priceScale(VOLUME_SCALE_ID).applyOptions({
      scaleMargins: { top: 0.82, bottom: 0 },
      borderVisible: false,
    })
    chart.priceScale("right").applyOptions({
      scaleMargins: { top: 0.06, bottom: 0.24 },
    })
  }
  const volData = syncedCandles.map((c) => {
    const t = c.time as number
    const v = volumeByTime.get(t) ?? 0
    return {
      time: c.time,
      value: v,
      color: "rgba(148, 163, 184, 0.4)",
    }
  })
  klineVolumeSeries.setData(volData)

  const sym = resolveKlinesSymbol()
  const pack = store.symbolTradesPack(sym)
  const merged = chartTradesForSymbol(sym, pack.trades, store.trades, chartSessionSince.value)
  const { min, max } = candleTimeBounds()
  const flow = buildTradeActivityHistogram(merged, {
    symbol: sym,
    intervalSec: KLINES_INTERVAL_SEC,
    minTimeSec: min,
    maxTimeSec: max,
  })
  tradeFlowSeries.setData(flow)
}

function applyMarkData() {
  if (!lineSeries) return
  applyMarkLineStyle()
  const pts = bucketMarkSeries(store.markSeriesData)
  if (pts.length === 0) {
    const bucket = bucketStart(Math.floor(Date.now() / 1000)) as UTCTimestamp
    const v =
      store.markPrice > 0 ? store.markPrice : Math.max(store.generatorLower || 0, 1e-8) || 1
    lineSeries.setData([
      { time: bucket, value: v },
      { time: (bucket + KLINES_INTERVAL_SEC) as UTCTimestamp, value: v },
    ])
    return
  }
  lineSeries.setData(pts)
}

function klinesUrl(botId: string, base: string): string {
  const prefix = base === "" ? "" : base.replace(/\/$/, "")
  return `${prefix}/api/bots/${encodeURIComponent(botId)}/klines`
}

/**
 * Fetch historical klines. ``isLoading`` is always cleared in ``finally`` so the UI never sticks on spinner.
 */
async function loadKlines(): Promise<CandlestickData<Time>[]> {
  isLoading.value = true
  klinesError.value = null
  try {
    const symbol = resolveKlinesSymbol()
    const botId = String(cfg.public.botId)
    const raw = cfg.public.apiBase
    const base = raw == null || raw === "" ? "" : String(raw).replace(/\/$/, "")
    const rows = await apiFetch<unknown>(klinesUrl(botId, base), {
      query: {
        symbol,
        interval: KLINES_INTERVAL,
        limit: KLINES_LIMIT,
      },
    })
    return parseKlinesPayload(rows)
  } catch (e) {
    klinesError.value = String(e)
    return []
  } finally {
    isLoading.value = false
  }
}

function bucketStart(tsSec: number): number {
  return Math.floor(tsSec / KLINES_INTERVAL_SEC) * KLINES_INTERVAL_SEC
}

/** Move the forming candle with live mark (same bucket = update OHLC; new bucket = append bar). */
function updateLiveCandleFromMark(price: number) {
  if (!candleSeries || !(price > 0)) return
  const now = Math.floor(Date.now() / 1000)
  const bucket = bucketStart(now)
  if (syncedCandles.length === 0) {
    const next: CandlestickData<Time> = {
      time: bucket as UTCTimestamp,
      open: price,
      high: price,
      low: price,
      close: price,
    }
    syncedCandles = [next]
    candleSeries.setData([next])
    return
  }
  const last = syncedCandles[syncedCandles.length - 1]
  const lastT = last.time as number
  if (bucket === lastT) {
    const next: CandlestickData<Time> = {
      time: last.time,
      open: last.open,
      high: Math.max(last.high, price),
      low: Math.min(last.low, price),
      close: price,
    }
    syncedCandles[syncedCandles.length - 1] = next
    candleSeries.update(next)
    return
  }
  if (bucket > lastT) {
    const next: CandlestickData<Time> = {
      time: bucket as UTCTimestamp,
      open: price,
      high: price,
      low: price,
      close: price,
    }
    syncedCandles.push(next)
    candleSeries.update(next)
    if (syncedCandles.length > 320) syncedCandles.splice(0, syncedCandles.length - 320)
    syncSessionBackdrop()
  }
}

/**
 * Ensure candlestick series exists and apply data (may be empty).
 * Empty history still initializes the series so live mark ticks can build bars via ``updateLiveCandleFromMark``.
 */
function ensureCandleSeries(candles: CandlestickData<Time>[]) {
  if (!chart) return
  try {
    if (!candleSeries) {
      candleSeries = chart.addSeries(CandlestickSeries, {
        ...CANDLE_SERIES_OPTS,
        autoscaleInfoProvider: AUTOSCALE_FROM_CANDLES,
      })
      priceLineHost = candleSeries
    }
    const data = candles.length ? candles : []
    candleSeries.setData(data)
    syncedCandles = data.map((c) => ({ ...c }))
    if (candles.length > 0 && !viewportLockedByUser) {
      applyDefaultViewport()
    }
    updateLiveCandleFromMark(store.markPrice)
    ensureTradeMarkersPlugin()
    applyTradeMarkers()
    syncBandArea()
    syncSessionBackdrop()
    syncVolumePanels()
  } catch (e) {
    klinesError.value = `lightweight-charts: ${String(e)}`
  }
}

function rebuildPriceLinesAfterSeriesReady() {
  if (!chart) return
  priceLineHost = candleSeries ?? lineSeries
  rebuildPriceLines()
}

function candleTimeBounds(): { min?: number; max?: number } {
  if (!syncedCandles.length) return {}
  const min = syncedCandles[0].time as number
  const max = (syncedCandles[syncedCandles.length - 1].time as number) + KLINES_INTERVAL_SEC
  return { min, max }
}

function ensureTradeMarkersPlugin() {
  if (!candleSeries) return
  if (!tradeMarkers) {
    tradeMarkers = createSeriesMarkers(candleSeries, [])
  }
}

function applyTradeMarkers() {
  if (!tradeMarkers || !candleSeries) return
  if (!showTrades.value) {
    tradeMarkers.setMarkers([])
    return
  }
  const sym = resolveKlinesSymbol()
  const pack = store.symbolTradesPack(sym)
  const merged = chartTradesForSymbol(sym, pack.trades, store.trades, chartSessionSince.value)
  const { min, max } = candleTimeBounds()
  const sessionMs = chartSessionSince.value ? Date.parse(chartSessionSince.value) : 0
  let markers = buildTradeMarkers(merged, {
    symbol: sym,
    intervalSec: KLINES_INTERVAL_SEC,
    minTimeSec: min,
    maxTimeSec: max,
    limit: 250,
  })
  if (markers.length === 0) {
    const ledger = store.gridLedgerPack(sym)
    if (ledger?.entries?.length) {
      markers = buildLedgerFillMarkers(ledger.entries, {
        symbol: sym,
        intervalSec: KLINES_INTERVAL_SEC,
        minTimeSec: min,
        maxTimeSec: max,
        sessionStartMs: sessionMs > 0 ? sessionMs : undefined,
      })
    }
  }
  tradeMarkers.setMarkers(markers)
  syncVolumePanels()
}

function bindCrosshairLegend() {
  if (!chart || !root.value) return
  if (onCrosshairMove) {
    chart.unsubscribeCrosshairMove(onCrosshairMove)
  }
  onCrosshairMove = (param: MouseEventParams<Time>) => {
    if (!param.point || !param.time || param.point.x < 0 || param.point.y < 0) {
      crosshairTip.value = { ...crosshairTip.value, visible: false }
      return
    }
    const t = param.time as number
    const candle = syncedCandles.find((c) => (c.time as number) === t)
    const close = candle ? (candle.close as number) : store.markPrice
    const hi = store.generatorUpper
    const lo = store.generatorLower
    let bandTxt = ""
    if (hi > lo && close > 0) {
      if (close > hi) bandTxt = " · فوق القمة"
      else if (close < lo) bandTxt = " · تحت القاع"
      else bandTxt = " · داخل النطاق"
    }
    crosshairTip.value = {
      visible: true,
      x: param.point.x + 12,
      y: param.point.y - 8,
      text: `${formatChartPrice(close)}${bandTxt}`,
    }
  }
  chart.subscribeCrosshairMove(onCrosshairMove)
}

async function loadTradesForChart() {
  const sym = resolveKlinesSymbol()
  await store.fetchTradesForSymbol(sym, { quiet: true })
  applyTradeMarkers()
}

onMounted(async () => {
  await nextTick()
  await new Promise<void>((r) => requestAnimationFrame(() => r()))
  if (!root.value) {
    isLoading.value = false
    return
  }

  const candles = await loadKlines()

  chart = createChart(root.value, {
    autoSize: true,
    height: 448,
    layout: {
      background: { type: ColorType.Solid, color: "#0b0e11" },
      textColor: "#c9d4e0",
    },
    grid: {
      vertLines: { color: "rgba(30,38,48,0.6)" },
      horzLines: { color: "rgba(30,38,48,0.6)" },
    },
    rightPriceScale: {
      borderColor: "#1e2630",
      autoScale: true,
      scaleMargins: { top: 0.08, bottom: 0.08 },
    },
    timeScale: {
      borderColor: "#1e2630",
      timeVisible: true,
      secondsVisible: false,
      rightOffset: 8,
    },
    handleScroll: {
      mouseWheel: true,
      pressedMouseMove: true,
      horzTouchDrag: true,
      vertTouchDrag: false,
    },
    handleScale: {
      mouseWheel: true,
      pinch: true,
      axisPressedMouseMove: { time: true, price: true },
      axisDoubleClickReset: { time: true, price: true },
    },
    crosshair: { mode: 1 },
  })

  // Do not call ``applyOptions({ width })`` while ``autoSize: true`` — lightweight-charts throws.
  ensureSessionBandSeries()
  ensureCandleSeries(candles)

  lineSeries = chart.addSeries(LineSeries, {
    ...MARK_LINE_SERIES_OPTS,
    autoscaleInfoProvider: AUTOSCALE_FROM_CANDLES,
  })
  bindViewportLockHandlers()
  bindCrosshairLegend()
  rebuildPriceLinesAfterSeriesReady()
  applyMarkData()
  void loadTradesForChart()

  liveClockTimer = globalThis.setInterval(() => {
    liveClock.value = Date.now()
  }, 30_000)

  if (candles.length === 0 && !klinesError.value) {
    klinesRetryTimer = globalThis.setTimeout(async () => {
      klinesRetryTimer = null
      if (!chart || !candleSeries) return
      const retry = await loadKlines()
      ensureCandleSeries(retry)
      rebuildPriceLinesAfterSeriesReady()
      void loadTradesForChart()
    }, 900)
  }

})

watch(
  () => [
    store.generatorUpper,
    store.generatorLower,
    store.generatorCount,
    store.gridLevels.join(","),
    showGridLines.value,
    showBand.value,
  ],
  () => {
    rebuildPriceLinesAfterSeriesReady()
    syncBandArea()
    syncSessionBackdrop()
    applyMarkLineStyle()
  },
)

watch(
  () => [
    showLiveLayer.value,
    store.isGridActiveForSelectedSymbol,
    gridMetaHere.value?.startedAt ?? "",
    store.binanceEnv,
    store.exchangeTestnet,
    store.generatorUpper,
    store.generatorLower,
  ],
  () => syncSessionBackdrop(),
)

watch([showTrades, showVolume], () => {
  applyTradeMarkers()
  syncVolumePanels()
})

watch(showBand, () => syncBandArea())

watch(
  () => store.markSeriesData,
  () => {
    applyMarkData()
    updateLiveCandleFromMark(store.markPrice)
  },
  { deep: true },
)

watch(
  () => store.markPrice,
  () => {
    applyMarkData()
    updateLiveCandleFromMark(store.markPrice)
    applyMarkLineStyle()
  },
)

watch(markInsideBand, () => applyMarkLineStyle())

async function reloadChartForSymbol() {
  if (!chart) return
  isLoading.value = true
  klinesError.value = null
  store.clearMarkSeries()
  syncedCandles = []
  viewportLockedByUser = false
  suppressViewportLock = true
  chart.applyOptions({ rightPriceScale: { autoScale: true } })
  try {
    const candles = await loadKlines()
    if (!candleSeries && chart) {
      candleSeries = chart.addSeries(CandlestickSeries, {
        ...CANDLE_SERIES_OPTS,
        autoscaleInfoProvider: AUTOSCALE_FROM_CANDLES,
      })
      priceLineHost = candleSeries
    }
    ensureCandleSeries(candles)
    rebuildPriceLinesAfterSeriesReady()
    applyMarkData()
    if (store.markPrice > 0) updateLiveCandleFromMark(store.markPrice)
    void loadTradesForChart()
  } finally {
    suppressViewportLock = false
    isLoading.value = false
  }
}

watch(
  () => store.symbol,
  () => {
    void reloadChartForSymbol()
  },
)

watch(
  () => {
    const sym = resolveKlinesSymbol()
    return [
      sym,
      store.trades.length,
      store.tradesBySymbol[sym]?.trades?.length ?? 0,
      store.tradesBySymbol[sym]?.updatedAt ?? "",
      store.gridLedgerBySymbol[sym]?.entries?.length ?? 0,
      chartSessionSince.value,
    ]
  },
  () => {
    applyTradeMarkers()
  },
)

onBeforeUnmount(() => {
  isLoading.value = false
  if (liveClockTimer) {
    clearInterval(liveClockTimer)
    liveClockTimer = null
  }
  if (klinesRetryTimer) {
    clearTimeout(klinesRetryTimer)
    klinesRetryTimer = null
  }
  unbindViewportLockHandlers()
  if (chart && onCrosshairMove) {
    chart.unsubscribeCrosshairMove(onCrosshairMove)
    onCrosshairMove = null
  }
  clearPriceLines()
  tradeMarkers?.detach()
  tradeMarkers = null
  chart?.remove()
  chart = null
  viewportLockedByUser = false
  candleSeries = null
  lineSeries = null
  bandSeries = null
  sessionBandSeries = null
  klineVolumeSeries = null
  tradeFlowSeries = null
  priceLineHost = null
  syncedCandles = []
  volumeByTime.clear()
})
</script>

<template>
  <div class="chart-wrap-inner">
    <div class="chart-toolbar">
      <button type="button" class="chart-reset-btn" @click="resetChartViewport">
        إعادة ضبط التكبير
      </button>
      <span class="chart-legend">
        <span class="legend-item buy">● شراء</span>
        <span class="legend-item sell">■ بيع</span>
        <span class="legend-item mark" :class="{ out: !markInsideBand }">— Mark</span>
      </span>
      <label class="layer-toggle"><input v-model="showTrades" type="checkbox" /> صفقات</label>
      <label class="layer-toggle"><input v-model="showBand" type="checkbox" /> نطاق</label>
      <label class="layer-toggle"><input v-model="showGridLines" type="checkbox" /> خطوط</label>
      <label class="layer-toggle"><input v-model="showVolume" type="checkbox" /> حجم</label>
      <label class="layer-toggle"><input v-model="showLiveLayer" type="checkbox" /> طبقة حية</label>
      <span class="chart-stats muted">
        {{ tradeStats.buys }} شراء · {{ tradeStats.sells }} بيع
        <template v-if="Math.abs(tradeStats.realized) >= 0.01">
          · PnL {{ tradeStats.realized >= 0 ? "+" : "" }}{{ tradeStats.realized.toFixed(2) }}
        </template>
      </span>
    </div>
    <p v-if="klinesError" class="klines-err" role="status">{{ klinesError }}</p>
    <div
      v-if="store.credentialsConfigured"
      class="chart-live-bar"
      :class="[
        `env-${liveStrip.env}`,
        `mode-${liveStrip.mode}`,
        { stale: liveStrip.feedStale, mainnet: liveStrip.isMainnet },
      ]"
      role="status"
      aria-live="polite"
    >
      <span class="live-bar-env">{{ liveStrip.envLabel }}</span>
      <span class="live-bar-host muted">{{ liveStrip.host }}</span>
      <span class="live-bar-sep" aria-hidden="true">·</span>
      <span class="live-bar-feed" :class="{ ok: !liveStrip.feedStale && liveStrip.wsOk }">
        {{
          liveStrip.feedStale
            ? "بث متأخر"
            : liveStrip.wsOk
              ? `بث حي${liveStrip.feedSec != null ? ` · ${liveStrip.feedSec}ث` : ""}`
              : "WS غير متصل"
        }}
      </span>
      <span class="live-bar-sep" aria-hidden="true">·</span>
      <span class="live-bar-bal" :class="{ ok: liveStrip.balanceLive }">
        {{ liveStrip.balanceLive ? "رصيد متزامن" : "رصيد…" }}
      </span>
      <template v-if="liveStrip.mode === 'live'">
        <span class="live-bar-sep" aria-hidden="true">·</span>
        <span class="live-bar-grid pulse">شبكة نشطة</span>
        <span v-if="liveStrip.sessionDur" class="live-bar-session muted">
          جلسة {{ liveStrip.sessionDur }}
        </span>
        <span v-if="liveStrip.alloc > 0" class="live-bar-alloc muted">
          · {{ liveStrip.alloc.toFixed(0) }} USDT
        </span>
        <span
          v-if="Math.abs(liveStrip.sessionPnl) >= 0.01"
          class="live-bar-pnl"
          :class="liveStrip.sessionPnl >= 0 ? 'up' : 'down'"
        >
          · جلسة {{ liveStrip.sessionPnl >= 0 ? "+" : "" }}{{ liveStrip.sessionPnl.toFixed(2) }}
        </span>
        <span v-if="liveStrip.orders > 0" class="live-bar-orders muted">
          · {{ liveStrip.orders }} أمر معلّق
        </span>
      </template>
      <template v-else-if="liveStrip.mode === 'other'">
        <span class="live-bar-sep" aria-hidden="true">·</span>
        <span class="live-bar-grid muted">
          شبكة على {{ liveStrip.otherGrids }} زوج آخر — المعاينة: {{ liveStrip.sym }}
        </span>
      </template>
      <template v-else-if="liveStrip.mode === 'orders'">
        <span class="live-bar-sep" aria-hidden="true">·</span>
        <span class="live-bar-grid muted">{{ liveStrip.orders }} أمر معلّق على {{ liveStrip.sym }}</span>
      </template>
      <template v-else>
        <span class="live-bar-sep" aria-hidden="true">·</span>
        <span class="live-bar-idle muted">جاهز — لا شبكة على هذا الزوج</span>
      </template>
      <span v-if="liveStrip.isMainnet" class="live-bar-warn">تداول حقيقي</span>
    </div>
    <div
      class="chart-stage"
      :class="[
        `env-${liveStrip.env}`,
        {
          'session-live': liveStrip.mode === 'live' && showLiveLayer,
          stale: liveStrip.feedStale,
        },
      ]"
    >
      <div v-if="isLoading" class="chart-loading" aria-live="polite">جاري تحميل الشموع…</div>
      <div
        v-show="crosshairTip.visible"
        class="crosshair-tip"
        :style="{ left: `${crosshairTip.x}px`, top: `${crosshairTip.y}px` }"
      >
        {{ crosshairTip.text }}
      </div>
      <div ref="root" class="chart-root" />
    </div>
  </div>
</template>

<style scoped>
.chart-wrap-inner {
  width: 100%;
  min-height: 420px;
}
.chart-toolbar {
  display: flex;
  align-items: center;
  gap: 0.65rem;
  flex-wrap: wrap;
  margin-bottom: 0.55rem;
  padding: 0.45rem 0.55rem;
  border-radius: var(--radius-sm);
  background: rgba(7, 10, 15, 0.45);
  border: 1px solid var(--border);
}
.chart-reset-btn {
  cursor: pointer;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-sm);
  padding: 0.32rem 0.62rem;
  font-size: 0.74rem;
  font-weight: 600;
  font-family: inherit;
  background: rgba(15, 19, 24, 0.85);
  color: var(--text-secondary);
  transition:
    border-color var(--transition),
    color var(--transition);
}
.chart-reset-btn:hover {
  border-color: var(--info-border);
  color: var(--info);
}
.chart-legend {
  display: flex;
  align-items: center;
  gap: 0.65rem;
  font-size: 0.72rem;
  font-weight: 600;
}
.legend-item.buy {
  color: #0ecb81;
}
.legend-item.sell {
  color: #f6465d;
}
.legend-item.mark {
  color: #38bdf8;
}
.legend-item.mark.out {
  color: #f59e0b;
}
.layer-toggle {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  font-size: 0.72rem;
  color: #94a3b8;
  cursor: pointer;
  user-select: none;
}
.chart-stats {
  font-size: 0.72rem;
  font-variant-numeric: tabular-nums;
}
.crosshair-tip {
  position: absolute;
  z-index: 4;
  pointer-events: none;
  padding: 0.25rem 0.45rem;
  border-radius: 4px;
  font-size: 0.72rem;
  font-weight: 600;
  color: #e2e8f0;
  background: rgba(15, 19, 24, 0.92);
  border: 1px solid #334155;
  white-space: nowrap;
}
.chart-stage {
  position: relative;
  width: 100%;
  min-height: 400px;
  touch-action: none;
  overscroll-behavior: contain;
}
.chart-root {
  position: relative;
  z-index: 1;
  width: 100%;
  min-width: 200px;
  min-height: 400px;
  height: 420px;
  touch-action: none;
}
.chart-loading {
  position: absolute;
  inset: 0;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.9rem;
  color: #94a3b8;
  background: rgba(11, 14, 17, 0.72);
  pointer-events: none;
}
.klines-err {
  margin: 0 0 0.5rem;
  font-size: 0.8rem;
  color: #fbbf24;
}
.chart-live-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.35rem 0.5rem;
  margin-bottom: 0.5rem;
  padding: 0.45rem 0.7rem;
  border-radius: var(--radius-sm);
  font-size: 0.7rem;
  font-weight: 600;
  border: 1px solid var(--border);
  background: rgba(12, 16, 23, 0.85);
}
.chart-live-bar.env-demo {
  border-color: rgba(56, 189, 248, 0.35);
}
.chart-live-bar.env-testnet {
  border-color: rgba(245, 158, 11, 0.4);
}
.chart-live-bar.env-mainnet {
  border-color: rgba(14, 203, 129, 0.35);
}
.chart-live-bar.mainnet {
  box-shadow: inset 0 0 0 1px rgba(239, 68, 68, 0.15);
}
.chart-live-bar.stale {
  border-color: rgba(251, 191, 36, 0.55);
}
.chart-live-bar.mode-live .live-bar-env {
  color: #0ecb81;
}
.live-bar-host {
  font-weight: 500;
  font-size: 0.68rem;
}
.live-bar-sep {
  opacity: 0.45;
}
.live-bar-feed.ok,
.live-bar-bal.ok {
  color: #34d399;
}
.live-bar-grid.pulse {
  color: #0ecb81;
  animation: live-bar-pulse 1.6s ease-in-out infinite;
}
@keyframes live-bar-pulse {
  50% {
    opacity: 0.55;
  }
}
.live-bar-pnl.up {
  color: #0ecb81;
}
.live-bar-pnl.down {
  color: #f6465d;
}
.live-bar-warn {
  margin-inline-start: auto;
  padding: 0.12rem 0.45rem;
  border-radius: 4px;
  font-size: 0.65rem;
  font-weight: 800;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: #fecaca;
  background: rgba(239, 68, 68, 0.22);
  border: 1px solid rgba(239, 68, 68, 0.45);
}
.chart-stage.env-demo.session-live {
  box-shadow: inset 0 0 0 1px rgba(56, 189, 248, 0.12);
}
.chart-stage.env-testnet.session-live {
  box-shadow: inset 0 0 0 1px rgba(245, 158, 11, 0.12);
}
.chart-stage.env-mainnet.session-live {
  box-shadow: inset 0 0 0 1px rgba(239, 68, 68, 0.1);
}
.chart-stage.session-live::before {
  content: "";
  position: absolute;
  inset: 0;
  z-index: 0;
  pointer-events: none;
  border-radius: 6px;
  background: linear-gradient(
    90deg,
    rgba(14, 203, 129, 0.04) 0%,
    transparent 18%,
    transparent 82%,
    rgba(14, 203, 129, 0.04) 100%
  );
}
.chart-stage.stale::after {
  content: "";
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 2px;
  z-index: 3;
  pointer-events: none;
  background: linear-gradient(90deg, transparent, #f59e0b, transparent);
}
</style>
