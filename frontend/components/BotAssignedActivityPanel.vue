<script setup lang="ts">
import { computed, ref } from "vue"
import { useBotStore } from "~/stores/bot"

const store = useBotStore()
const loading = ref(false)

function baseFromSymbol(sym: string): string {
  const s = (sym || "").toUpperCase().replace("/", "")
  return s.endsWith("USDT") ? s.slice(0, -4) : s
}

const activeList = computed(() => {
  if (store.activeGridSymbols.length) return store.activeGridSymbols
  if (store.gridRunnerSymbol) return [store.gridRunnerSymbol]
  return store.symbol ? [store.symbol] : []
})

async function onHaltFlatten() {
  const sym = (store.gridRunnerSymbol || store.symbol).trim().toUpperCase()
  const msg =
    `تأكيد: إيقاف محرّك الشبكة، ثم إلغاء كل أوامر ${sym} المعلّقة، ثم بيع الرصيد الحرّ من عملة الأساس (${baseFromSymbol(sym)}) إلى السوق على Spot.\n` +
    "لن تُمحى سجلّات المنصّة التاريخية، لكن لن يبقى طلب نشط لهذا الزوج وتُسيَّل المراكز بسعر السوق."
  if (!confirm(msg)) return
  loading.value = true
  try {
    await store.stopGridAndFlattenSpot()
  } catch (e) {
    alert(String(e))
  } finally {
    loading.value = false
    void store.fetchGridStatus()
  }
}
</script>

<template>
  <section class="bot-assign panel" aria-labelledby="bot-assign-heading">
    <header class="bot-assign-head panel-header">
      <div>
        <h2 id="bot-assign-heading" class="panel-title">شبكة نشطة — إدارة</h2>
        <p class="panel-subtitle">
          على {{ store.spotEnvLabelAr }} ·
          {{ activeList.length === 1 ? activeList[0] : `${activeList.length} أزواج` }}
          · إيقاف كامل يلغي الأوامر ويبيع عملة الأساس بالسوق للزوج المختار في الشريط.
        </p>
      </div>
      <button
        type="button"
        class="btn-halt-flatten"
        :disabled="loading || !store.credentialsConfigured"
        @click="onHaltFlatten"
      >
        {{ loading ? "جاري الإيقاف…" : "إيقاف وتصفية (إلغاء أوامر + بيع الأساس بالسوق)" }}
      </button>
    </header>

    <ul class="assign-list">
      <li v-for="sym in activeList" :key="sym">
        <span class="lbl">شبكة نشطة</span>
        <span class="sym live">{{ sym }}</span>
        <span class="hint muted">{{ baseFromSymbol(sym) }}/USDT</span>
        <span v-if="store.gridsBySymbol[sym]?.ordersPlaced" class="hint muted">
          · {{ store.gridsBySymbol[sym]?.ordersPlaced }} أمر
        </span>
      </li>
      <li v-if="store.hasOpenExchangeOrders && !store.hasActiveGrids">
        <span class="lbl">أوامر معلّقة</span>
        <span class="sym">{{ store.orders.length }} على {{ store.symbol }}</span>
        <span class="hint muted">المحرّك متوقف — يمكن التصفية أدناه</span>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.bot-assign {
  padding-top: 0.9rem;
}
.bot-assign-head {
  margin-bottom: 0.75rem;
}
.btn-halt-flatten {
  border: 1px solid rgba(246, 70, 93, 0.45);
  background: var(--danger-dim);
  color: #fecdd3;
  font-size: 0.76rem;
  font-weight: 700;
  font-family: inherit;
  padding: 0.52rem 0.95rem;
  border-radius: var(--radius-sm);
  cursor: pointer;
  white-space: nowrap;
  transition: filter var(--transition);
}
.btn-halt-flatten:hover:not(:disabled) {
  filter: brightness(1.08);
}
.btn-halt-flatten:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.assign-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  font-size: 0.8rem;
}
.assign-list li {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.5rem 0.85rem;
  padding: 0.5rem 0.65rem;
  background: rgba(7, 10, 15, 0.55);
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
}
.assign-list .lbl {
  flex: 0 0 auto;
  min-width: 8.5rem;
  color: var(--muted);
  font-weight: 600;
}
.assign-list .sym {
  font-weight: 700;
  color: var(--warn);
  letter-spacing: 0.03em;
}
.assign-list .sym.live {
  color: var(--accent);
}
.assign-list .hint {
  font-size: 0.72rem;
  color: var(--muted);
}
@media (max-width: 560px) {
  .assign-list .lbl {
    min-width: 100%;
  }
}
</style>
