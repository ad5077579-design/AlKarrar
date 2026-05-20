<script setup lang="ts">
import { computed } from "vue"
import { useBotStore } from "~/stores/bot"

const store = useBotStore()

const allocatedTotal = computed(() => {
  let sum = 0
  for (const s of store.activeGridSymbols) {
    sum += Number(store.gridsBySymbol[s]?.allocatedCapital ?? 0)
  }
  return sum
})

const sessionPnlTotal = computed(() => {
  let closed = 0
  let floating = 0
  for (const s of store.activeGridSymbols) {
    const g = store.gridsBySymbol[s]
    if (!g) continue
    closed += Number(g.sessionRealizedUsdt ?? 0)
    floating += Number(g.unrealizedPnlUsdt ?? 0)
  }
  return { closed, floating, total: closed + floating }
})

const pnlClass = (n: number) => (n > 0.001 ? "up" : n < -0.001 ? "down" : "")

function fmt(n: number, d = 2): string {
  if (!Number.isFinite(n)) return "—"
  const sign = n >= 0 ? "+" : ""
  return `${sign}${n.toFixed(d)}`
}
</script>

<template>
  <section class="portfolio-strip" aria-label="ملخص المحفظة">
    <article class="kpi">
      <span class="kpi-label">متاح للتداول</span>
      <span class="kpi-value">{{ store.balanceIsLive ? store.availableBalance.toFixed(2) : "—" }}</span>
      <span class="kpi-unit">USDT</span>
    </article>
    <article class="kpi">
      <span class="kpi-label">مخصص للشبكات</span>
      <span class="kpi-value">{{ allocatedTotal.toFixed(0) }}</span>
      <span class="kpi-unit">USDT · {{ store.activeGridSymbols.length }} زوج</span>
    </article>
    <article class="kpi">
      <span class="kpi-label">ربح جلسات نشطة</span>
      <span class="kpi-value" :class="pnlClass(sessionPnlTotal.closed)">{{ fmt(sessionPnlTotal.closed) }}</span>
      <span class="kpi-unit">USDT مغلق</span>
    </article>
    <article class="kpi">
      <span class="kpi-label">عائم (Mark)</span>
      <span class="kpi-value" :class="pnlClass(sessionPnlTotal.floating)">{{ fmt(sessionPnlTotal.floating) }}</span>
      <span class="kpi-unit">USDT</span>
    </article>
    <article class="kpi kpi-total">
      <span class="kpi-label">إجمالي الشبكات</span>
      <span class="kpi-value" :class="pnlClass(sessionPnlTotal.total)">{{ fmt(sessionPnlTotal.total) }}</span>
      <span class="kpi-unit">USDT</span>
    </article>
  </section>
</template>

<style scoped>
.portfolio-strip {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(128px, 1fr));
  gap: 0.55rem;
}
.kpi {
  padding: 0.65rem 0.75rem;
  border-radius: 10px;
  background: #12161c;
  border: 1px solid #1e2630;
  display: flex;
  flex-direction: column;
  gap: 0.12rem;
  transition: border-color 0.2s ease;
}
.kpi:hover {
  border-color: rgba(56, 189, 248, 0.25);
}
.kpi-total {
  border-color: rgba(14, 203, 129, 0.25);
  background: linear-gradient(135deg, rgba(14, 203, 129, 0.08), rgba(18, 22, 28, 0.95));
}
.kpi-label {
  font-size: 0.65rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #64748b;
}
.kpi-value {
  font-size: 1.15rem;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: #e2e8f0;
}
.kpi-value.up {
  color: #0ecb81;
}
.kpi-value.down {
  color: #f6465d;
}
.kpi-unit {
  font-size: 0.68rem;
  color: #94a3b8;
}
</style>
