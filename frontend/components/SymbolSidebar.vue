<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from "vue"
import { useBotStore, type MarketSymbol } from "~/stores/bot"

const store = useBotStore()
const query = ref("")
const selecting = ref<string | null>(null)

let refreshTimer: ReturnType<typeof setInterval> | null = null

const filtered = computed(() => {
  const q = query.value.trim().toUpperCase()
  const list = store.markets
  if (!q) return list
  return list.filter(
    (m) => m.symbol.includes(q) || m.baseAsset.toUpperCase().includes(q),
  )
})

function formatPrice(n: number): string {
  if (!(n > 0)) return "—"
  if (n >= 1000) return n.toLocaleString(undefined, { maximumFractionDigits: 2 })
  if (n >= 1) return n.toFixed(4)
  if (n >= 0.0001) return n.toFixed(6)
  return n.toPrecision(4)
}

function formatVol(n: number): string {
  if (!(n > 0)) return ""
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B`
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)}K`
  return String(Math.round(n))
}

async function onPick(row: MarketSymbol) {
  if (row.symbol === store.symbol || selecting.value) return
  selecting.value = row.symbol
  try {
    await store.selectSymbol(row.symbol)
  } catch (e) {
    const msg =
      e && typeof e === "object" && "data" in e && (e as { data?: { detail?: string } }).data?.detail
        ? String((e as { data?: { detail?: string } }).data?.detail)
        : String(e)
    alert(msg || "تعذّر تغيير العملة")
  } finally {
    selecting.value = null
  }
}

onMounted(() => {
  void store.fetchMarkets()
  refreshTimer = setInterval(() => {
    void store.fetchMarkets()
  }, 45_000)
})

onBeforeUnmount(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})
</script>

<template>
  <aside class="symbol-sidebar" aria-label="قائمة العملات">
    <div class="sidebar-head">
      <h2 class="sidebar-title">الأسواق</h2>
      <span v-if="store.credentialsConfigured" class="env-tag" :class="store.binanceEnv || 'testnet'">
        {{ store.spotEnvLabel }}
      </span>
      <button
        type="button"
        class="refresh-btn"
        :disabled="store.marketsLoading"
        title="تحديث من المنصة"
        @click="store.fetchMarkets()"
      >
        ↻
      </button>
    </div>
    <p class="sidebar-hint muted">Spot · USDT · {{ store.spotEnvLabelAr }}</p>
    <p v-if="store.excludedStableSymbols.length" class="sidebar-excluded muted">
      مستبعدة (مستقرة): {{ store.excludedStableSymbols.join("، ") }}
    </p>

    <input
      v-model="query"
      class="search field"
      type="search"
      placeholder="بحث (BTC, DOGE…)"
      autocomplete="off"
    />

    <p v-if="store.marketsError" class="markets-err" role="alert">{{ store.marketsError }}</p>

    <div v-if="store.marketsLoading && !store.markets.length" class="sidebar-loading">
      جاري جلب الأزواج…
    </div>

    <ul v-else class="symbol-list" role="listbox">
      <li
        v-for="row in filtered"
        :key="row.symbol"
        role="option"
        :aria-selected="row.symbol === store.symbol"
      >
        <button
          type="button"
          class="symbol-row"
          :class="{
            active: row.symbol === store.symbol,
            pending: selecting === row.symbol,
          }"
          :disabled="!!selecting"
          @click="onPick(row)"
        >
          <span class="sym-main">
            <span class="sym-base">{{ row.baseAsset }}</span>
            <span class="sym-quote">/{{ store.marketsQuote }}</span>
          </span>
          <span class="sym-stats">
            <span class="sym-price">{{ formatPrice(row.lastPrice) }}</span>
            <span
              class="sym-pct"
              :class="{ up: row.priceChangePercent >= 0, down: row.priceChangePercent < 0 }"
            >
              {{ row.priceChangePercent >= 0 ? "+" : "" }}{{ row.priceChangePercent.toFixed(2) }}%
            </span>
          </span>
          <span v-if="row.quoteVolume > 0" class="sym-vol muted">
            Vol {{ formatVol(row.quoteVolume) }}
          </span>
        </button>
      </li>
    </ul>

    <p v-if="!store.marketsLoading && filtered.length === 0" class="empty muted">لا نتائج</p>
    <p v-else-if="store.marketsUpdatedAt" class="updated muted">
      آخر تحديث: {{ new Date(store.marketsUpdatedAt).toLocaleTimeString() }}
    </p>
  </aside>
</template>

<style scoped>
.symbol-sidebar {
  width: 280px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  padding: 1rem 0.85rem;
  background: linear-gradient(180deg, #0d1117 0%, #0b0e11 100%);
  border-inline-end: 1px solid var(--border);
  min-height: 100vh;
  box-sizing: border-box;
}
.sidebar-head {
  display: flex;
  align-items: center;
  gap: 0.45rem;
}
.sidebar-title {
  margin: 0;
  font-size: 0.95rem;
  font-weight: 700;
  flex: 1;
}
.env-tag {
  font-size: 0.62rem;
  font-weight: 700;
  text-transform: uppercase;
  padding: 0.15rem 0.4rem;
  border-radius: 4px;
  border: 1px solid transparent;
}
.env-tag.demo {
  background: rgba(56, 189, 248, 0.14);
  color: #7dd3fc;
  border-color: rgba(56, 189, 248, 0.35);
}
.env-tag.testnet {
  background: rgba(245, 158, 11, 0.15);
  color: #fbbf24;
  border-color: rgba(245, 158, 11, 0.35);
}
.env-tag.mainnet {
  background: rgba(14, 203, 129, 0.12);
  color: #34d399;
  border-color: rgba(14, 203, 129, 0.4);
}
.refresh-btn {
  width: 2rem;
  height: 2rem;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: #151a22;
  color: var(--muted);
  cursor: pointer;
  font-size: 1rem;
  line-height: 1;
}
.refresh-btn:hover:not(:disabled) {
  color: var(--text);
  border-color: rgba(56, 189, 248, 0.4);
}
.refresh-btn:disabled {
  opacity: 0.5;
}
.sidebar-hint {
  margin: 0;
  font-size: 0.72rem;
}
.sidebar-excluded {
  margin: 0.2rem 0 0;
  font-size: 0.65rem;
  line-height: 1.35;
  color: #6b7280;
}
.search {
  margin-top: 0.15rem;
}
.markets-err {
  margin: 0;
  font-size: 0.75rem;
  color: #fbbf24;
}
.sidebar-loading,
.empty,
.updated {
  margin: 0;
  font-size: 0.78rem;
}
.symbol-list {
  list-style: none;
  margin: 0;
  padding: 0;
  overflow-y: auto;
  flex: 1;
  min-height: 120px;
  max-height: calc(100vh - 11rem);
  scrollbar-width: thin;
  scrollbar-color: #2a3340 transparent;
}
.symbol-list li {
  margin: 0 0 0.2rem;
}
.symbol-row {
  width: 100%;
  display: grid;
  grid-template-columns: 1fr auto;
  grid-template-rows: auto auto;
  gap: 0.1rem 0.5rem;
  align-items: center;
  text-align: start;
  padding: 0.5rem 0.55rem;
  border-radius: 8px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--text);
  cursor: pointer;
  font: inherit;
}
.symbol-row:hover:not(:disabled) {
  background: rgba(30, 38, 48, 0.65);
  border-color: rgba(30, 38, 48, 0.9);
}
.symbol-row.active {
  background: rgba(56, 189, 248, 0.1);
  border-color: rgba(56, 189, 248, 0.45);
}
.symbol-row.pending {
  opacity: 0.65;
}
.symbol-row:disabled {
  cursor: wait;
}
.sym-main {
  grid-row: 1 / 3;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
}
.sym-base {
  font-weight: 700;
  font-size: 0.88rem;
  letter-spacing: 0.02em;
}
.sym-quote {
  font-size: 0.68rem;
  color: var(--muted);
}
.sym-stats {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 0.05rem;
}
.sym-price {
  font-size: 0.78rem;
  font-variant-numeric: tabular-nums;
}
.sym-pct {
  font-size: 0.72rem;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.sym-pct.up {
  color: var(--accent);
}
.sym-pct.down {
  color: var(--danger);
}
.sym-vol {
  grid-column: 2;
  font-size: 0.65rem;
  text-align: end;
}
@media (max-width: 960px) {
  .symbol-sidebar {
    width: 100%;
    min-height: auto;
    max-height: 42vh;
    border-inline-end: none;
    border-bottom: 1px solid var(--border);
  }
  .symbol-list {
    max-height: 28vh;
  }
}
</style>
