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
  store.setDashboardTab("watch")
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
  width: 292px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 0.55rem;
  padding: 1.1rem 0.95rem;
  background: linear-gradient(180deg, rgba(12, 16, 23, 0.98) 0%, rgba(7, 10, 15, 1) 100%);
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
  font-size: 0.92rem;
  font-weight: 800;
  letter-spacing: -0.02em;
  flex: 1;
}

.env-tag {
  font-size: 0.6rem;
  font-weight: 700;
  text-transform: uppercase;
  padding: 0.18rem 0.42rem;
  border-radius: var(--radius-pill);
  border: 1px solid transparent;
}

.env-tag.demo {
  background: var(--info-dim);
  color: #7dd3fc;
  border-color: var(--info-border);
}

.env-tag.testnet {
  background: var(--warn-dim);
  color: #fbbf24;
  border-color: rgba(245, 158, 11, 0.35);
}

.env-tag.mainnet {
  background: var(--accent-dim);
  color: #34d399;
  border-color: var(--accent-border);
}

.refresh-btn {
  width: 2rem;
  height: 2rem;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-strong);
  background: rgba(15, 19, 24, 0.8);
  color: var(--muted);
  cursor: pointer;
  font-size: 1rem;
  line-height: 1;
  transition:
    color var(--transition),
    border-color var(--transition),
    background var(--transition);
}

.refresh-btn:hover:not(:disabled) {
  color: var(--info);
  border-color: var(--info-border);
  background: var(--info-dim);
}

.refresh-btn:disabled {
  opacity: 0.45;
}

.sidebar-hint {
  margin: 0;
  font-size: 0.7rem;
}

.search {
  margin-top: 0.1rem;
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
  font-size: 0.76rem;
}

.symbol-list {
  list-style: none;
  margin: 0;
  padding: 0.15rem 0 0;
  overflow-y: auto;
  flex: 1;
  min-height: 120px;
  max-height: calc(100vh - 21rem);
}

.symbol-list li {
  margin: 0 0 0.22rem;
}

.symbol-row {
  width: 100%;
  display: grid;
  grid-template-columns: 1fr auto;
  grid-template-rows: auto auto;
  gap: 0.08rem 0.55rem;
  align-items: center;
  text-align: start;
  padding: 0.55rem 0.6rem;
  border-radius: var(--radius-sm);
  border: 1px solid transparent;
  background: transparent;
  color: var(--text);
  cursor: pointer;
  font: inherit;
  transition:
    background var(--transition),
    border-color var(--transition);
}

.symbol-row:hover:not(:disabled) {
  background: rgba(24, 32, 48, 0.75);
  border-color: var(--border);
}

.symbol-row.active {
  background: var(--info-dim);
  border-color: var(--info-border);
  box-shadow: inset 0 0 0 1px rgba(56, 189, 248, 0.08);
}

.symbol-row.pending {
  opacity: 0.6;
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
  font-size: 0.86rem;
  letter-spacing: 0.01em;
}

.sym-quote {
  font-size: 0.66rem;
  color: var(--muted);
}

.sym-stats {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 0.04rem;
}

.sym-price {
  font-size: 0.76rem;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.sym-pct {
  font-size: 0.7rem;
  font-weight: 700;
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
  font-size: 0.62rem;
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
