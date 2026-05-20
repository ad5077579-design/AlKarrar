<script setup lang="ts">
import { computed, ref } from "vue"
import { useBotStore, type GridLineTrailRow, type GridSymbolMeta } from "~/stores/bot"
import {
  buildFifoPnlState,
  fifoRealizedUsdt,
  fifoUnrealizedUsdt,
  gridSessionFills,
  summarizeGridSessionFills,
} from "~/utils/fifoSpotPnl"
import { uniqueOrderCount } from "~/utils/tradeDisplay"

const props = defineProps<{
  symbol: string
  meta: GridSymbolMeta
}>()

const emit = defineEmits<{
  viewTrades: [symbol: string]
  viewChart: [symbol: string]
  stop: [symbol: string]
}>()

const store = useBotStore()
const stopping = ref(false)
const isChartSymbol = computed(
  () => store.symbol.trim().toUpperCase().replace("/", "") === props.symbol,
)

const pack = computed(() => store.symbolTradesPack(props.symbol))

const sessionFills = computed(() => {
  const fills = gridSessionFills(pack.value.trades, props.meta.startedAt)
  return summarizeGridSessionFills(fills)
})

const sessionOrderCount = computed(() =>
  uniqueOrderCount(gridSessionFills(pack.value.trades, props.meta.startedAt)),
)

const fifoClosedPnl = computed(() =>
  fifoRealizedUsdt(gridSessionFills(pack.value.trades, props.meta.startedAt)),
)

/** WS session realized (FIFO ledger on runner); falls back to trade journal FIFO. */
const closedPnl = computed(() => {
  const ws = props.meta.sessionRealizedUsdt
  if (ws != null && Number.isFinite(ws)) return Number(ws)
  return fifoClosedPnl.value
})

const liveMark = computed(() => store.symbolMark(props.symbol))

const floatingPnl = computed(() => {
  const ws = props.meta.unrealizedPnlUsdt
  if (ws != null && Number.isFinite(ws)) return Number(ws)
  const fills = gridSessionFills(pack.value.trades, props.meta.startedAt)
  return fifoUnrealizedUsdt(buildFifoPnlState(fills, props.meta.startedAt), liveMark.value)
})

const totalPnl = computed(() => closedPnl.value + floatingPnl.value)

const activeTrailLines = computed(() =>
  (props.meta.lineTrail ?? []).filter((r) => {
    if (!r.phase || r.phase === "idle") return false
    if (r.hasSessionBuy === false) return false
    if (r.exchangeFillConfirmed === false) return false
    return true
  }),
)

const phaseLabel: Record<string, string> = {
  idle: "خمول",
  lock_profit: "تثبيت ربح",
  trailing: "ملاحقة",
}

function baseAsset(sym: string): string {
  const s = sym.toUpperCase().replace("/", "")
  return s.endsWith("USDT") ? s.slice(0, -4) : s
}

function formatStarted(iso?: string): string {
  if (!iso?.trim()) return "—"
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString()
}

function pnlClass(n: number): string {
  if (n > 0.0001) return "up"
  if (n < -0.0001) return "down"
  return "flat"
}

function formatPnl(n: number, digits = 4): string {
  if (!Number.isFinite(n)) return "—"
  return `${n >= 0 ? "+" : ""}${n.toFixed(digits)}`
}

function formatPhase(phase: string): string {
  return phaseLabel[phase] ?? phase
}

async function onStop() {
  stopping.value = true
  try {
    emit("stop", props.symbol)
  } finally {
    stopping.value = false
  }
}

</script>

<template>
  <article
    class="grid-card"
    :class="{
      selected: store.tradesViewSymbol === symbol,
      'chart-focus': isChartSymbol,
    }"
  >
    <div class="grid-card-top">
      <div>
        <h3 class="grid-card-sym">{{ symbol }}</h3>
        <p class="grid-card-pair muted">{{ baseAsset(symbol) }}/USDT · Spot</p>
      </div>
      <span class="grid-card-live">● نشط</span>
    </div>

    <dl class="grid-card-stats">
      <div>
        <dt>خطوط مسلّحة / تنفيذ</dt>
        <dd>
          {{ meta.ordersPlaced ?? 0 }}
          <span v-if="meta.virtualExecutions" class="fills-split muted">
            · {{ meta.virtualExecutions }} مملوء على المنصة
          </span>
        </dd>
      </div>
      <div>
        <dt>بدء التشغيل</dt>
        <dd class="dd-sm">{{ formatStarted(meta.startedAt) }}</dd>
      </div>
      <div :class="['stat-pnl', pnlClass(closedPnl)]">
        <dt>ربح مغلق</dt>
        <dd>{{ formatPnl(closedPnl, 4) }} USDT</dd>
        <p class="pnl-hint">بعد كل بيع</p>
      </div>
      <div :class="['stat-pnl', pnlClass(floatingPnl)]">
        <dt>ربح عائم</dt>
        <dd>{{ formatPnl(floatingPnl, 4) }} USDT</dd>
        <p class="pnl-hint">يتحرك مع Mark</p>
      </div>
      <div :class="['stat-pnl', pnlClass(totalPnl)]">
        <dt>إجمالي الجلسة</dt>
        <dd>{{ formatPnl(totalPnl, 4) }} USDT</dd>
      </div>
      <div>
        <dt>تنفيذات</dt>
        <dd>
          <template v-if="sessionFills.count">
            {{ sessionOrderCount }} أمر
            <span class="fills-split muted"> · {{ sessionFills.count }} fill</span>
            <span class="fills-split muted">
              · {{ sessionFills.buyCount }} شراء · {{ sessionFills.sellCount }} بيع
            </span>
          </template>
          <span v-else class="fills-split muted">لا تنفيذ بعد</span>
        </dd>
      </div>
      <div>
        <dt>حجم تنفيذات</dt>
        <dd>{{ sessionFills.totalQuoteVolume.toFixed(2) }} USDT</dd>
      </div>
    </dl>

    <section v-if="activeTrailLines.length" class="trail-panel" aria-label="مراحل الملاحقة">
      <h4 class="trail-title">ملاحقة نشطة</h4>
      <ul class="trail-list">
        <li v-for="row in activeTrailLines" :key="row.lineIndex" class="trail-row">
          <span class="trail-idx">خط {{ row.lineIndex + 1 }}</span>
          <span class="trail-phase" :class="row.phase">{{ formatPhase(row.phase) }}</span>
          <span v-if="row.phase === 'trailing' && row.trailPeak > 0" class="trail-peak muted">
            قمة {{ row.trailPeak.toFixed(6) }}
          </span>
        </li>
      </ul>
    </section>

    <p v-if="meta.lastError" class="grid-card-err" role="alert">{{ meta.lastError }}</p>
    <p v-else-if="pack.error" class="grid-card-err" role="alert">{{ pack.error }}</p>

    <div class="grid-card-actions">
      <button
        type="button"
        class="btn-card btn-chart"
        :class="{ active: isChartSymbol }"
        @click="emit('viewChart', symbol)"
      >
        {{ isChartSymbol ? "الشارت ✓" : "الشارت" }}
      </button>
      <button
        type="button"
        class="btn-card btn-trades"
        :class="{ active: store.tradesViewSymbol === symbol }"
        @click="emit('viewTrades', symbol)"
      >
        سجل الصفقات
      </button>
      <button type="button" class="btn-card btn-stop" :disabled="stopping" @click="onStop">
        {{ stopping ? "جاري الإيقاف…" : "إيقاف الشبكة" }}
      </button>
    </div>
  </article>
</template>

<style scoped>
.grid-card {
  background: #0f1318;
  border: 1px solid #1e2630;
  border-radius: 10px;
  padding: 0.9rem 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.grid-card.selected {
  border-color: rgba(56, 189, 248, 0.45);
  box-shadow: 0 0 0 1px rgba(56, 189, 248, 0.12);
}
.grid-card.chart-focus {
  border-color: rgba(14, 203, 129, 0.4);
  box-shadow: 0 0 0 1px rgba(14, 203, 129, 0.1);
}
.grid-card.selected.chart-focus {
  border-color: rgba(56, 189, 248, 0.55);
}
.grid-card-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.5rem;
}
.grid-card-sym {
  margin: 0;
  font-size: 1rem;
  font-weight: 700;
  color: #f1f5f9;
}
.grid-card-pair {
  margin: 0.15rem 0 0;
  font-size: 0.72rem;
}
.grid-card-live {
  font-size: 0.72rem;
  font-weight: 600;
  color: #0ecb81;
  white-space: nowrap;
}
.grid-card-stats {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.55rem 0.75rem;
  margin: 0;
}
.grid-card-stats dt {
  margin: 0;
  font-size: 0.68rem;
  color: #94a3b8;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.grid-card-stats dd {
  margin: 0.1rem 0 0;
  font-size: 0.88rem;
  font-weight: 600;
  color: #e2e8f0;
}
.dd-sm {
  font-size: 0.75rem !important;
  font-weight: 500 !important;
}
.stat-pnl dd.up {
  color: #0ecb81;
}
.stat-pnl dd.down {
  color: #f6465d;
}
.pnl-hint {
  margin: 0.15rem 0 0;
  font-size: 0.65rem;
  color: #64748b;
}
.fills-split {
  font-weight: 400;
  font-size: 0.72rem;
}
.trail-panel {
  border-top: 1px solid #1e2630;
  padding-top: 0.55rem;
}
.trail-title {
  margin: 0 0 0.4rem;
  font-size: 0.72rem;
  font-weight: 600;
  color: #94a3b8;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.trail-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
.trail-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.4rem 0.65rem;
  font-size: 0.78rem;
}
.trail-idx {
  color: #cbd5e1;
}
.trail-phase {
  font-weight: 600;
  padding: 0.1rem 0.4rem;
  border-radius: 4px;
  background: rgba(148, 163, 184, 0.12);
}
.trail-phase.lock_profit {
  color: #fcd535;
  background: rgba(252, 213, 53, 0.12);
}
.trail-phase.trailing {
  color: #38bdf8;
  background: rgba(56, 189, 248, 0.12);
}
.trail-peak {
  font-size: 0.7rem;
}
.grid-card-err {
  margin: 0;
  font-size: 0.75rem;
  color: #fbbf24;
}
.grid-card-actions {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}
.btn-card {
  flex: 1;
  min-width: 7rem;
  cursor: pointer;
  border-radius: 8px;
  border: 1px solid #1e2630;
  padding: 0.45rem 0.65rem;
  font-size: 0.8rem;
  font-weight: 600;
  background: #12161c;
  color: #e2e8f0;
}
.btn-card.btn-chart {
  border-color: rgba(14, 203, 129, 0.35);
  color: #34d399;
}
.btn-card.btn-chart.active {
  border-color: #0ecb81;
  color: #0ecb81;
  background: rgba(14, 203, 129, 0.1);
}
.btn-card.btn-trades.active {
  border-color: #38bdf8;
  color: #38bdf8;
}
.btn-card.btn-stop {
  border-color: rgba(246, 70, 93, 0.45);
  color: #f6465d;
}
.btn-card:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
</style>
