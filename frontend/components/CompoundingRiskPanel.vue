<script setup lang="ts">
import { computed } from "vue"
import { useBotStore } from "~/stores/bot"

const store = useBotStore()

const reinjectedDisplay = computed(() => store.gridReinjectedUsdt)
const peakDisplay = computed(() => store.gridPeakEquityUsdt)
const drawdownDisplay = computed(() => store.gridDrawdownPct)
const isolatedLabel = computed(() =>
  store.isGridActiveForSelectedSymbol ? `شبكة ${store.symbol} (معزولة)` : "محفظة المنصة",
)

function fmtUsdt(n: number): string {
  if (!(n >= 0) || !Number.isFinite(n)) return "—"
  return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtPct(n: number): string {
  if (!Number.isFinite(n)) return "—"
  return `${n.toFixed(2)}%`
}
</script>

<template>
  <section class="risk-panel panel" aria-label="مراقبة التكبير والمخاطر">
    <h2 class="risk-title">التكبير والمخاطر</h2>
    <p class="risk-sub muted">قراءة فقط — {{ isolatedLabel }}</p>

    <div class="risk-grid">
      <div class="risk-card">
        <div class="risk-label">Peak Equity</div>
        <div class="risk-value">{{ fmtUsdt(peakDisplay) }} <span class="unit">USDT</span></div>
        <p class="risk-hint">أعلى equity للشبكة المعزولة</p>
      </div>

      <div class="risk-card">
        <div class="risk-label">Current Drawdown</div>
        <div
          class="risk-value"
          :class="{
            warn: drawdownDisplay > 0 && drawdownDisplay < store.trailingEquityDrawdownLimitPct,
            bad: drawdownDisplay >= store.trailingEquityDrawdownLimitPct,
          }"
        >
          {{ fmtPct(drawdownDisplay) }}
        </div>
        <p class="risk-hint">من القمة الحالية</p>
      </div>

      <div class="risk-card">
        <div class="risk-label">Realized PnL (محقق · مُعاد حقنه)</div>
        <div class="risk-value pnl-up">+{{ fmtUsdt(reinjectedDisplay) }} <span class="unit">USDT</span></div>
        <p class="risk-hint">أرباح الشبكة المحققة (FIFO)</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.risk-panel {
  background: #1e2329;
  border-color: #2b3139;
}
.risk-title {
  margin: 0 0 0.2rem;
  font-size: 0.95rem;
  font-weight: 600;
  color: #eaecef;
}
.risk-sub {
  margin: 0 0 0.85rem;
  font-size: 0.72rem;
}
.risk-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 0.65rem;
}
@media (max-width: 900px) {
  .risk-grid {
    grid-template-columns: 1fr;
  }
}
.risk-card {
  background: #181a20;
  border: 1px solid #2b3139;
  border-radius: 8px;
  padding: 0.75rem 0.85rem;
}
.risk-label {
  font-size: 0.68rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: #848e9c;
  margin-bottom: 0.35rem;
}
.risk-value {
  font-size: 1.25rem;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: #eaecef;
}
.risk-value .unit {
  font-size: 0.72rem;
  font-weight: 600;
  color: #848e9c;
}
.risk-value.warn {
  color: #f0b90b;
}
.risk-value.bad {
  color: #f6465d;
}
.risk-value.pnl-up {
  color: #0ecb81;
}
.risk-hint {
  margin: 0.35rem 0 0;
  font-size: 0.68rem;
  color: #5e6673;
}
.muted {
  color: #848e9c;
}
</style>
