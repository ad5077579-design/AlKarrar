<script setup lang="ts">
import { computed } from "vue"
import { useBotStore } from "~/stores/bot"

const store = useBotStore()

const sym = computed(() => store.symbol.trim().toUpperCase().replace("/", ""))

const meta = computed(() => store.selectedGridMeta)

const gridHere = computed(() => store.isGridActiveForSelectedSymbol)

const mark = computed(() => store.symbolMark(sym.value) || store.markPrice)

const inBand = computed(() => {
  const m = mark.value
  const hi = store.generatorUpper
  const lo = store.generatorLower
  return hi > lo && m >= lo && m <= hi
})

const sessionDur = computed(() => {
  const iso = meta.value?.startedAt
  if (!iso?.trim()) return ""
  const ms = Date.parse(iso)
  if (!Number.isFinite(ms)) return ""
  const sec = Math.max(0, Math.floor((Date.now() - ms) / 1000))
  if (sec < 3600) return `${Math.floor(sec / 60)} د`
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  return m > 0 ? `${h} س ${m} د` : `${h} س`
})

function baseAsset(s: string): string {
  return s.endsWith("USDT") ? s.slice(0, -4) : s
}
</script>

<template>
  <div class="symbol-ctx" role="status">
    <div class="ctx-main">
      <span class="ctx-sym">{{ baseAsset(sym) }}/USDT</span>
      <span v-if="gridHere" class="ctx-badge live">شبكة نشطة</span>
      <span v-else class="ctx-badge idle">بدون شبكة</span>
    </div>
    <div class="ctx-stats">
      <span v-if="mark > 0" class="ctx-item">
        Mark <strong>{{ mark.toFixed(6) }}</strong>
      </span>
      <span v-if="store.generatorUpper > store.generatorLower" class="ctx-item" :class="{ warn: !inBand }">
        {{
          inBand
            ? "داخل النطاق"
            : mark > store.generatorUpper
              ? "فوق القمة"
              : mark < store.generatorLower
                ? "تحت القاع"
                : "خارج النطاق"
        }}
      </span>
      <span v-if="gridHere && meta" class="ctx-item muted">
        {{ store.generatorCount }} خط · {{ Number(meta.allocatedCapital ?? 0).toFixed(0) }} USDT
      </span>
      <span v-if="sessionDur" class="ctx-item muted">جلسة {{ sessionDur }}</span>
      <span v-if="meta?.virtualExecutions" class="ctx-item muted">
        {{ meta.virtualExecutions }} تنفيذ
      </span>
    </div>
  </div>
</template>

<style scoped>
.symbol-ctx {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  flex-wrap: wrap;
  padding: 0.65rem 0.9rem;
  margin-bottom: 0.15rem;
  border-radius: var(--radius-md);
  background: linear-gradient(90deg, var(--info-dim), rgba(56, 189, 248, 0.02));
  border: 1px solid var(--info-border);
}
.ctx-main {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.ctx-sym {
  font-size: 1rem;
  font-weight: 800;
  color: #f1f5f9;
}
.ctx-badge {
  font-size: 0.68rem;
  font-weight: 700;
  padding: 0.15rem 0.45rem;
  border-radius: 999px;
}
.ctx-badge.live {
  color: #0ecb81;
  background: rgba(14, 203, 129, 0.15);
}
.ctx-badge.idle {
  color: #94a3b8;
  background: rgba(148, 163, 184, 0.12);
}
.ctx-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem 0.85rem;
  font-size: 0.78rem;
  color: #cbd5e1;
}
.ctx-item strong {
  color: #38bdf8;
  font-variant-numeric: tabular-nums;
}
.ctx-item.warn {
  color: #fbbf24;
  font-weight: 600;
}
.ctx-item.muted {
  color: #94a3b8;
}
</style>
