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
  let total = 0
  for (const s of store.activeGridSymbols) {
    const g = store.gridsBySymbol[s]
    if (!g) continue
    total +=
      Number(g.sessionRealizedUsdt ?? 0) + Number(g.unrealizedPnlUsdt ?? 0)
  }
  return total
})

const pnlClass = (n: number) => (n > 0.001 ? "up" : n < -0.001 ? "down" : "")

function fmtPnl(n: number): string {
  if (!Number.isFinite(n)) return "—"
  const sign = n >= 0 ? "+" : ""
  return `${sign}${n.toFixed(2)}`
}
</script>

<template>
  <p
    v-if="store.activeGridSymbols.length"
    class="portfolio-inline"
    aria-label="ملخص الشبكات النشطة"
  >
    <span class="pill">{{ store.activeGridSymbols.length }} شبكة</span>
    <span class="sep" aria-hidden="true">·</span>
    <span>مخصص {{ allocatedTotal.toFixed(0) }} USDT</span>
    <span class="sep" aria-hidden="true">·</span>
    <span>
      PnL
      <strong :class="pnlClass(sessionPnlTotal)">{{ fmtPnl(sessionPnlTotal) }}</strong>
      USDT
    </span>
  </p>
</template>

<style scoped>
.portfolio-inline {
  margin: 0;
  padding: 0.55rem 0.85rem;
  border-radius: var(--radius-md);
  background: linear-gradient(90deg, var(--accent-dim), rgba(14, 203, 129, 0.03));
  border: 1px solid var(--accent-border);
  font-size: 0.78rem;
  color: var(--text-secondary);
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.4rem;
}
.pill {
  font-weight: 700;
  color: #0ecb81;
}
.sep {
  opacity: 0.45;
}
strong {
  font-variant-numeric: tabular-nums;
  font-weight: 700;
  color: #e2e8f0;
}
strong.up {
  color: #0ecb81;
}
strong.down {
  color: #f6465d;
}
</style>
