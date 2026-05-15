<script setup lang="ts">
import {
  createChart,
  LineSeries,
  CandlestickSeries,
  ColorType,
} from "lightweight-charts"
import type {
  IChartApi,
  ISeriesApi,
  UTCTimestamp,
  CandlestickData,
  Time,
} from "lightweight-charts"
import { useBotStore } from "~/stores/bot"

const store = useBotStore()
const cfg = useRuntimeConfig()
const root = ref<HTMLDivElement | null>(null)
const klinesError = ref<string | null>(null)
/** Cleared in ``finally`` after every klines fetch (success, error, or empty). */
const isLoading = ref(true)

/** Must match ``interval`` query to ``/klines`` (seconds per bar). */
const KLINES_INTERVAL_SEC = 5 * 60
/** In-memory series copy for live OHLC updates from mark WebSocket. */
let syncedCandles: CandlestickData<Time>[] = []

let chart: IChartApi | null = null
let candleSeries: ISeriesApi<"Candlestick"> | null = null
let lineSeries: ISeriesApi<"Line"> | null = null
/** Series that owns generator / grid price lines */
let priceLineHost: ISeriesApi<"Candlestick"> | ISeriesApi<"Line"> | null = null
const priceLines = shallowRef<object[]>([])
let resizeObserver: ResizeObserver | null = null
let klinesRetryTimer: ReturnType<typeof setTimeout> | null = null

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

function normalizeLinePoints(data: { time: number; value: number }[]) {
  if (data.length === 0) return []
  if (data.length === 1) {
    const p = data[0]
    return [
      { time: p.time as UTCTimestamp, value: p.value },
      { time: (p.time + 1) as UTCTimestamp, value: p.value },
    ]
  }
  return data.map((x) => ({ time: x.time as UTCTimestamp, value: x.value }))
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
  const d = store.markSeriesData
  const pts = normalizeLinePoints(d)
  if (pts.length === 0) {
    const t = Math.floor(Date.now() / 1000) as UTCTimestamp
    const v =
      store.markPrice > 0 ? store.markPrice : Math.max(store.generatorLower || 0, 1e-8) || 1
    lineSeries.setData([
      { time: t, value: v },
      { time: (t + 1) as UTCTimestamp, value: v },
    ])
    return
  }
  lineSeries.setData(pts)
  chart?.timeScale().scrollToRealTime()
}

function klinesUrl(botId: string, base: string): string {
  const prefix = base === "" ? "" : base.replace(/\/$/, "")
  return `${prefix}/api/bots/${encodeURIComponent(botId)}/klines`
}

/** Normalized perpetual symbol for klines (backend rejects missing symbol). */
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
    const rows = await $fetch<unknown>(klinesUrl(botId, base), {
      query: {
        symbol,
        interval: "5m",
        limit: 200,
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
      candleSeries = chart.addSeries(CandlestickSeries, { ...CANDLE_SERIES_OPTS })
      priceLineHost = candleSeries
    }
    const data = candles.length ? candles : []
    candleSeries.setData(data)
    syncedCandles = data.map((c) => ({ ...c }))
    if (candles.length > 0) chart.timeScale().fitContent()
    updateLiveCandleFromMark(store.markPrice)
  } catch (e) {
    klinesError.value = `lightweight-charts: ${String(e)}`
  }
}

function rebuildPriceLinesAfterSeriesReady() {
  if (!chart) return
  priceLineHost = candleSeries ?? lineSeries
  rebuildPriceLines()
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
    rightPriceScale: { borderColor: "#1e2630" },
    timeScale: { borderColor: "#1e2630" },
    crosshair: { mode: 1 },
  })

  // Do not call ``applyOptions({ width })`` while ``autoSize: true`` — lightweight-charts throws.
  ensureCandleSeries(candles)

  lineSeries = chart.addSeries(LineSeries, { ...MARK_LINE_SERIES_OPTS })
  rebuildPriceLinesAfterSeriesReady()
  applyMarkData()

  if (candles.length === 0 && !klinesError.value) {
    klinesRetryTimer = globalThis.setTimeout(async () => {
      klinesRetryTimer = null
      if (!chart || !candleSeries) return
      const retry = await loadKlines()
      ensureCandleSeries(retry)
      rebuildPriceLinesAfterSeriesReady()
    }, 900)
  }

  resizeObserver = new ResizeObserver(() => {
    if (!chart || !root.value) return
    requestAnimationFrame(() => chart?.timeScale().fitContent())
  })
  resizeObserver.observe(root.value)
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

watch(
  () => store.symbol,
  async () => {
    if (!chart || !candleSeries) return
    syncedCandles = []
    const candles = await loadKlines()
    ensureCandleSeries(candles)
    rebuildPriceLinesAfterSeriesReady()
  },
)

onBeforeUnmount(() => {
  isLoading.value = false
  if (klinesRetryTimer) {
    clearTimeout(klinesRetryTimer)
    klinesRetryTimer = null
  }
  resizeObserver?.disconnect()
  resizeObserver = null
  clearPriceLines()
  chart?.remove()
  chart = null
  candleSeries = null
  lineSeries = null
  priceLineHost = null
  syncedCandles = []
})
</script>

<template>
  <div class="chart-wrap-inner">
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
.chart-stage {
  position: relative;
  width: 100%;
  min-height: 400px;
}
.chart-root {
  width: 100%;
  min-width: 200px;
  min-height: 400px;
  height: 420px;
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
