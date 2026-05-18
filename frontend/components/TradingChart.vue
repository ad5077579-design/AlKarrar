<script setup lang="ts">
import {
  createChart,
  createSeriesMarkers,
  LineSeries,
  CandlestickSeries,
  ColorType,
} from "lightweight-charts"
import type {
  AutoscaleInfo,
  IChartApi,
  ISeriesApi,
  ISeriesMarkersPluginApi,
  LogicalRangeChangeEventHandler,
  UTCTimestamp,
  CandlestickData,
  Time,
} from "lightweight-charts"
import { useBotStore } from "~/stores/bot"
import { buildTradeMarkers, mergeTradesForSymbol } from "~/utils/chartTradeMarkers"

const store = useBotStore()
const cfg = useRuntimeConfig()
const root = ref<HTMLDivElement | null>(null)
const klinesError = ref<string | null>(null)
/** Cleared in ``finally`` after every klines fetch (success, error, or empty). */
const isLoading = ref(true)

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
/** Series that owns generator / grid price lines */
let priceLineHost: ISeriesApi<"Candlestick"> | ISeriesApi<"Line"> | null = null
const priceLines = shallowRef<object[]>([])
let klinesRetryTimer: ReturnType<typeof setTimeout> | null = null
/** User panned/zoomed — do not reset scale on live mark ticks. */
let viewportLockedByUser = false
let suppressViewportLock = false
let onVisibleLogicalRangeChange: LogicalRangeChangeEventHandler | null = null
let onChartWheel: (() => void) | null = null

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
  const mp = store.markPrice
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
  const next: object[] = []
  const hi = store.generatorUpper
  const lo = store.generatorLower
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
  const levels = store.gridLevels
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
    parsed.push({
      time: t as UTCTimestamp,
      open,
      high,
      low,
      close,
    })
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

function applyMarkData() {
  if (!lineSeries) return
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

/** Normalized Spot symbol for klines (backend rejects missing symbol). */
function resolveKlinesSymbol(): string {
  const s = String(store.symbol ?? "")
    .trim()
    .toUpperCase()
    .replace("/", "")
  if (/^[A-Z0-9]{4,}$/.test(s)) return s
  return "DOGEUSDT"
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
  const sym = resolveKlinesSymbol()
  const pack = store.symbolTradesPack(sym)
  const merged = mergeTradesForSymbol(sym, pack.trades, store.trades)
  const { min, max } = candleTimeBounds()
  const markers = buildTradeMarkers(merged, {
    symbol: sym,
    intervalSec: KLINES_INTERVAL_SEC,
    minTimeSec: min,
    maxTimeSec: max,
    limit: 250,
  })
  tradeMarkers.setMarkers(markers)
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
    height: 420,
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
  ensureCandleSeries(candles)

  lineSeries = chart.addSeries(LineSeries, {
    ...MARK_LINE_SERIES_OPTS,
    autoscaleInfoProvider: AUTOSCALE_FROM_CANDLES,
  })
  bindViewportLockHandlers()
  rebuildPriceLinesAfterSeriesReady()
  applyMarkData()
  void loadTradesForChart()

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
  ],
  () => {
    rebuildPriceLinesAfterSeriesReady()
  },
)

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
  },
)

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
    ]
  },
  () => {
    applyTradeMarkers()
  },
)

onBeforeUnmount(() => {
  isLoading.value = false
  if (klinesRetryTimer) {
    clearTimeout(klinesRetryTimer)
    klinesRetryTimer = null
  }
  unbindViewportLockHandlers()
  clearPriceLines()
  tradeMarkers?.detach()
  tradeMarkers = null
  chart?.remove()
  chart = null
  viewportLockedByUser = false
  candleSeries = null
  lineSeries = null
  priceLineHost = null
  syncedCandles = []
})
</script>

<template>
  <div class="chart-wrap-inner">
    <div class="chart-toolbar">
      <button type="button" class="chart-reset-btn" @click="resetChartViewport">
        إعادة ضبط التكبير
      </button>
      <span class="chart-legend">
        <span class="legend-item buy">▲ شراء</span>
        <span class="legend-item sell">▼ بيع</span>
      </span>
      <span class="chart-hint muted">عجلة الفأرة: تكبير · سحب: تحريك</span>
    </div>
    <p v-if="klinesError" class="klines-err" role="status">{{ klinesError }}</p>
    <div class="chart-stage">
      <div v-if="isLoading" class="chart-loading" aria-live="polite">جاري تحميل الشموع…</div>
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
  gap: 0.75rem;
  flex-wrap: wrap;
  margin-bottom: 0.5rem;
}
.chart-reset-btn {
  cursor: pointer;
  border: 1px solid var(--border, #1e2630);
  border-radius: 6px;
  padding: 0.35rem 0.65rem;
  font-size: 0.78rem;
  font-weight: 600;
  background: #0f1318;
  color: #e2e8f0;
}
.chart-reset-btn:hover {
  border-color: #38bdf8;
  color: #38bdf8;
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
.chart-hint {
  font-size: 0.72rem;
}
.chart-stage {
  position: relative;
  width: 100%;
  min-height: 400px;
  touch-action: none;
  overscroll-behavior: contain;
}
.chart-root {
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
</style>
