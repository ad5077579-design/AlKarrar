<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, watch } from "vue"
import GridCard from "~/components/GridCard.vue"
import { useBotStore } from "~/stores/bot"

const store = useBotStore()

let pollTimer: ReturnType<typeof setInterval> | null = null

const symbols = computed(() => store.activeGridSymbols)

async function refreshAll() {
  await store.fetchGridStatus()
  await store.refreshActiveGridTrades()
}

async function onStop(sym: string) {
  if (!confirm(`إيقاف شبكة ${sym} وإلغاء أوامرها المعلّقة على المنصة؟`)) return
  try {
    await store.stopGrid(sym)
  } catch (e) {
    alert(String(e))
  }
}

function onViewTrades(sym: string) {
  store.openTradesForSymbol(sym)
  nextTick(() => {
    document.getElementById("dash-tab-logs")?.scrollIntoView({ behavior: "smooth", block: "start" })
  })
}

async function onViewChart(sym: string) {
  const normalized = sym.trim().toUpperCase().replace("/", "")
  store.setDashboardTab("watch")
  if (normalized !== store.symbol.trim().toUpperCase().replace("/", "")) {
    try {
      await store.selectSymbol(normalized)
    } catch (e) {
      alert(String(e))
      return
    }
  }
  void store.fetchGridLedger(normalized)
  nextTick(() => {
    document.getElementById("trading-chart-panel")?.scrollIntoView({ behavior: "smooth", block: "start" })
  })
}

watch(
  () => store.activeGridSymbols.join(","),
  () => {
    void store.refreshActiveGridTrades()
  },
)

onMounted(() => {
  void refreshAll()
  pollTimer = setInterval(() => {
    void refreshAll()
  }, 30_000)
})

onBeforeUnmount(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<template>
  <section class="grid-cards-wrap panel" aria-label="الشبكات النشطة">
    <header class="panel-header grid-cards-head">
      <div>
        <h2 class="panel-title">الشبكات النشطة</h2>
        <p class="panel-subtitle">حالة كل شبكة · PnL · إجراءات سريعة</p>
      </div>
      <span class="grid-cards-count chip chip-env-mainnet">{{ symbols.length }} زوج</span>
    </header>

    <div class="grid-cards-grid">
      <GridCard
        v-for="sym in symbols"
        :key="sym"
        :symbol="sym"
        :meta="store.gridsBySymbol[sym] ?? {}"
        @view-trades="onViewTrades"
        @view-chart="onViewChart"
        @stop="onStop"
      />
    </div>
  </section>
</template>

<style scoped>
.grid-cards-wrap {
  padding-top: 0.9rem;
}
.grid-cards-head {
  margin-bottom: 0.85rem;
}
.grid-cards-count {
  flex-shrink: 0;
}
.grid-cards-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
  gap: 0.75rem;
}
</style>
