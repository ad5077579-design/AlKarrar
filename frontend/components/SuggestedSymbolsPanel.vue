<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, watch } from "vue"
import { useBotStore, type SymbolSuggestion } from "~/stores/bot"

const store = useBotStore()
const applying = ref<string | null>(null)

let refreshTimer: ReturnType<typeof setInterval> | null = null

const list = computed(() => store.symbolSuggestions)

function scoreClass(score: number): string {
  if (score >= 75) return "high"
  if (score >= 55) return "mid"
  return "low"
}

function formatVol(n: number): string {
  if (!(n > 0)) return ""
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B`
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`
  return `${(n / 1e3).toFixed(0)}K`
}

async function onApply(row: SymbolSuggestion) {
  if (applying.value) return
  applying.value = row.symbol
  try {
    await store.applySymbolSuggestion(row)
  } catch (e) {
    alert(String(e))
  } finally {
    applying.value = null
  }
}

function runScan() {
  if (store.credentialsConfigured) void store.fetchSymbolSuggestions()
}

onMounted(() => {
  runScan()
  refreshTimer = setInterval(() => {
    void store.fetchSymbolSuggestions({ quiet: true })
  }, 120_000)
})

watch(
  () => store.credentialsConfigured,
  (ok) => {
    if (ok) void store.fetchSymbolSuggestions()
  },
)

onBeforeUnmount(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})
</script>

<template>
  <section class="suggested-panel" aria-label="العملات المقترحة">
    <div class="suggested-head">
      <h3 class="suggested-title">العملات المقترحة</h3>
      <button
        type="button"
        class="scan-btn"
        :disabled="store.suggestionsLoading || !store.credentialsConfigured"
        title="فحص صارم متوافق مع الشبكة"
        @click="runScan()"
      >
        {{ store.suggestionsLoading ? "…" : "فحص" }}
      </button>
    </div>
    <p v-if="!store.credentialsConfigured" class="suggested-err" role="status">
      لا مفاتيح Binance — عيّنها في <code>.env</code> ثم أعد تشغيل API.
    </p>
    <p v-else class="suggested-hint muted">
      فحص Spot USDT على {{ store.spotEnvLabelAr }} — سيولة، تذبذب، واقتصاديات خطوط (
      {{ store.allocatedCapital.toFixed(0) }} USDT)
    </p>

    <p v-if="store.credentialsConfigured && store.suggestionsError" class="suggested-err" role="alert">
      {{ store.suggestionsError }}
    </p>

    <div v-if="store.suggestionsLoading && !list.length" class="suggested-loading muted">
      جاري تحليل الأسواق…
    </div>

    <ul v-else-if="list.length" class="suggested-list" role="list">
      <li v-for="row in list" :key="row.symbol">
        <button
          type="button"
          class="suggested-row"
          :class="{ active: row.symbol === store.symbol, pending: applying === row.symbol }"
          :disabled="!!applying"
          @click="onApply(row)"
        >
          <span class="row-top">
            <span class="sym-rank">#{{ row.rank }}</span>
            <span class="sym-name">{{ row.baseAsset }}</span>
            <span class="sym-score" :class="scoreClass(row.score)">{{ row.score.toFixed(0) }}</span>
          </span>
          <span class="row-meta muted">
            Vol {{ formatVol(row.quoteVolume24h) }}
            · {{ row.dailyRangePct.toFixed(1) }}% نطاق
            · {{ row.generatorCount }} خط
          </span>
          <span v-if="row.reasons.length" class="row-reasons">
            {{ row.reasons.slice(0, 2).join(" · ") }}
          </span>
        </button>
      </li>
    </ul>

    <p v-else-if="!store.suggestionsLoading" class="empty muted">
      لا توجد أزواج تمرّ جميع شروط الشبكة برأس المال الحالي — زِد الرصيد أو وسّع النطاق.
    </p>

    <p v-if="store.suggestionsUpdatedAt" class="updated muted">
      آخر فحص: {{ new Date(store.suggestionsUpdatedAt).toLocaleTimeString() }}
      <template v-if="store.suggestionsRejectedCount > 0">
        · استُبعد {{ store.suggestionsRejectedCount }}
      </template>
    </p>
  </section>
</template>

<style scoped>
.suggested-panel {
  margin-top: 0.35rem;
  padding: 0.55rem 0.45rem 0.5rem;
  border-radius: 8px;
  border: 1px solid rgba(14, 203, 129, 0.35);
  background: rgba(14, 203, 129, 0.06);
}
.suggested-head {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
.suggested-title {
  margin: 0;
  flex: 1;
  font-size: 0.82rem;
  font-weight: 700;
  color: #e2e8f0;
}
.scan-btn {
  font-size: 0.68rem;
  font-weight: 700;
  padding: 0.2rem 0.5rem;
  border-radius: 5px;
  border: 1px solid rgba(56, 189, 248, 0.35);
  background: rgba(56, 189, 248, 0.1);
  color: #7dd3fc;
  cursor: pointer;
}
.scan-btn:hover:not(:disabled) {
  background: rgba(56, 189, 248, 0.2);
}
.scan-btn:disabled {
  opacity: 0.55;
}
.suggested-hint {
  margin: 0.25rem 0 0.4rem;
  font-size: 0.66rem;
  line-height: 1.35;
}
.suggested-err {
  margin: 0 0 0.35rem;
  font-size: 0.72rem;
  color: #fbbf24;
}
.suggested-list {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 220px;
  overflow-y: auto;
  scrollbar-width: thin;
}
.suggested-row {
  width: 100%;
  text-align: start;
  border: 1px solid transparent;
  border-radius: 6px;
  padding: 0.4rem 0.45rem;
  margin-bottom: 0.25rem;
  background: rgba(21, 26, 34, 0.75);
  cursor: pointer;
  color: inherit;
}
.suggested-row:hover:not(:disabled) {
  border-color: rgba(14, 203, 129, 0.35);
  background: rgba(14, 203, 129, 0.08);
}
.suggested-row.active {
  border-color: rgba(56, 189, 248, 0.5);
  background: rgba(56, 189, 248, 0.1);
}
.suggested-row.pending {
  opacity: 0.65;
}
.row-top {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  font-size: 0.78rem;
  font-weight: 700;
}
.sym-rank {
  font-size: 0.65rem;
  color: #64748b;
  min-width: 1.2rem;
}
.sym-name {
  flex: 1;
}
.sym-score {
  font-variant-numeric: tabular-nums;
  font-size: 0.72rem;
  padding: 0.1rem 0.35rem;
  border-radius: 4px;
}
.sym-score.high {
  color: #0ecb81;
  background: rgba(14, 203, 129, 0.15);
}
.sym-score.mid {
  color: #fbbf24;
  background: rgba(245, 158, 11, 0.12);
}
.sym-score.low {
  color: #94a3b8;
  background: rgba(148, 163, 184, 0.12);
}
.row-meta {
  display: block;
  font-size: 0.65rem;
  margin-top: 0.15rem;
}
.row-reasons {
  display: block;
  font-size: 0.62rem;
  color: #94a3b8;
  margin-top: 0.12rem;
  line-height: 1.3;
}
.suggested-loading,
.empty,
.updated {
  margin: 0.25rem 0 0;
  font-size: 0.68rem;
}
</style>
