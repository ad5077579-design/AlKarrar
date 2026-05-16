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
    document.getElementById("grid-trades-journal")?.scrollIntoView({ behavior: "smooth", block: "start" })
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
    <header class="grid-cards-head">
      <h2 class="grid-cards-title">الشبكات النشطة</h2>
      <span class="grid-cards-count muted">{{ symbols.length }} زوج</span>
    </header>

    <div class="grid-cards-grid">
      <GridCard
        v-for="sym in symbols"
        :key="sym"
        :symbol="sym"
        :meta="store.gridsBySymbol[sym] ?? {}"
        @view-trades="onViewTrades"
        @stop="onStop"
      />
    </div>
  </section>
</template>

<style scoped>
.grid-cards-wrap {
  padding: 1rem 1.1rem;
}
.grid-cards-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 0.75rem;
  margin-bottom: 0.85rem;
}
.grid-cards-title {
  margin: 0;
  font-size: 0.95rem;
  font-weight: 600;
  color: #e2e8f0;
}
.grid-cards-count {
  font-size: 0.78rem;
}
.grid-cards-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 0.85rem;
}
</style>
