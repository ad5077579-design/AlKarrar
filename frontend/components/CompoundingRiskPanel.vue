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
    <header class="panel-header">
      <div>
        <h2 class="panel-title">التكبير والمخاطر</h2>
        <p class="panel-subtitle">قراءة فقط — {{ isolatedLabel }}</p>
      </div>
    </header>

    <div class="risk-grid">
      <div class="risk-card inner-card">
        <div class="risk-label">Peak Equity</div>
        <div class="risk-value">{{ fmtUsdt(peakDisplay) }} <span class="unit">USDT</span></div>
        <p class="risk-hint">أعلى equity للشبكة المعزولة</p>
      </div>

      <div class="risk-card inner-card">
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

      <div class="risk-card inner-card">
        <div class="risk-label">Realized PnL (محقق · مُعاد حقنه)</div>
        <div class="risk-value pnl-up">+{{ fmtUsdt(reinjectedDisplay) }} <span class="unit">USDT</span></div>
        <p class="risk-hint">أرباح الشبكة المحققة (FIFO)</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.risk-panel {
  padding-top: 0.9rem;
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
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}
.risk-label {
  font-size: 0.66rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.07em;
  color: var(--muted);
}
.risk-value {
  font-size: 1.2rem;
  font-weight: 800;
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
  color: var(--text);
}
.risk-value .unit {
  font-size: 0.7rem;
  font-weight: 600;
  color: var(--muted);
}
.risk-value.warn {
  color: var(--warn);
}
.risk-value.bad {
  color: var(--danger);
}
.risk-value.pnl-up {
  color: var(--accent);
}
.risk-hint {
  margin: 0.2rem 0 0;
  font-size: 0.68rem;
  color: var(--muted);
  line-height: 1.35;
}
</style>
