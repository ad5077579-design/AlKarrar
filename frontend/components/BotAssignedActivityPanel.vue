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
    <div class="bot-assign-head">
      <div>
        <h2 id="bot-assign-heading">شبكة نشطة — إدارة</h2>
        <p class="bot-assign-desc muted">
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
    </div>

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
  background: #1e2329;
  border: 1px solid #2b3139;
  border-radius: 8px;
  padding: 0.95rem 1.1rem 1rem;
}
.bot-assign-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 1rem;
  flex-wrap: wrap;
  margin-bottom: 0.75rem;
}
.bot-assign-head h2 {
  margin: 0 0 0.35rem;
  font-size: 0.95rem;
  font-weight: 600;
}
.bot-assign-desc {
  margin: 0;
  font-size: 0.76rem;
  line-height: 1.45;
  max-width: 560px;
}
.btn-halt-flatten {
  border: 1px solid rgba(246, 70, 93, 0.55);
  background: rgba(246, 70, 93, 0.18);
  color: #ff9aa8;
  font-size: 0.78rem;
  font-weight: 700;
  padding: 0.5rem 0.95rem;
  border-radius: 6px;
  cursor: pointer;
  white-space: nowrap;
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
  gap: 0.55rem;
  font-size: 0.8rem;
}
.assign-list li {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.5rem 0.85rem;
  padding: 0.45rem 0.55rem;
  background: rgba(24, 26, 32, 0.85);
  border-radius: 6px;
  border: 1px solid rgba(43, 49, 57, 0.75);
}
.assign-list .lbl {
  flex: 0 0 auto;
  min-width: 8.5rem;
  color: #848e9c;
  font-weight: 600;
}
.assign-list .sym {
  font-weight: 700;
  color: #f0b90b;
  letter-spacing: 0.03em;
}
.assign-list .sym.live {
  color: #34d399;
}
.assign-list .hint {
  font-size: 0.72rem;
}
@media (max-width: 560px) {
  .assign-list .lbl {
    min-width: 100%;
  }
}
</style>
