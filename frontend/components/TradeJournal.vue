<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, watch } from "vue"
import { useBotStore } from "~/stores/bot"
import {
  fifoRealizedUsdt,
  gridSessionFills,
  summarizeGridSessionFills,
} from "~/utils/fifoSpotPnl"

const props = withDefaults(
  defineProps<{
    /** عند الفتح من كرت شبكة — يعرض صفقات هذا الزوج فقط */
    symbol?: string
    /** منذ بدء الشبكة — يعرض التنفيذات فقط (fills) وليس السجل الكامل */
    since?: string
    embedded?: boolean
  }>(),
  { embedded: false },
)

const emit = defineEmits<{
  close: []
}>()

const store = useBotStore()
const sideFilter = ref<"all" | "BUY" | "SELL">("all")
const search = ref("")

let refreshTimer: ReturnType<typeof setInterval> | null = null

const journalSym = computed(() => {
  const s = props.symbol?.trim().toUpperCase().replace("/", "") || ""
  return s || store.symbol
})

const pack = computed(() => store.symbolTradesPack(journalSym.value))

const sessionRows = computed(() => {
  const rows = props.symbol ? pack.value.trades : store.trades
  if (props.since?.trim()) {
    return gridSessionFills(rows, props.since)
  }
  return rows
})

const filtered = computed(() => {
  const q = search.value.trim().toUpperCase()
  return sessionRows.value.filter((t) => {
    if (props.symbol && t.symbol !== journalSym.value) return false
    if (sideFilter.value !== "all" && t.side !== sideFilter.value) return false
    if (!q) return true
    return (
      t.symbol.includes(q) ||
      t.orderId.includes(q) ||
      t.exchangeTradeId.includes(q)
    )
  })
})

const summary = computed(() => {
  if (props.since?.trim()) {
    return summarizeGridSessionFills(sessionRows.value)
  }
  return props.symbol ? pack.value.summary : store.tradesSummary
})

const closedFifoPnl = computed(() =>
  props.since?.trim() ? fifoRealizedUsdt(sessionRows.value) : summary.value.totalRealizedPnl,
)
const journalLoading = computed(() => (props.symbol ? pack.value.loading : store.tradesLoading))
const journalError = computed(() => (props.symbol ? pack.value.error : store.tradesError))
const journalSyncError = computed(() =>
  props.symbol ? pack.value.syncError : store.tradesSyncError,
)
const journalSource = computed(() => (props.symbol ? pack.value.source : store.tradesSource))

function formatTime(iso: string, ms: number): string {
  if (ms > 0) {
    return new Date(ms).toLocaleString(undefined, {
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    })
  }
  if (iso) return new Date(iso).toLocaleString()
  return "—"
}

function formatNum(n: number, digits = 4): string {
  if (!Number.isFinite(n)) return "—"
  if (Math.abs(n) >= 1000) return n.toLocaleString(undefined, { maximumFractionDigits: 2 })
  return n.toFixed(digits)
}

function formatPrice(n: number): string {
  if (!(n > 0)) return "—"
  if (n >= 100) return n.toFixed(2)
  if (n >= 1) return n.toFixed(4)
  return n.toFixed(6)
}

function pnlClass(n: number): string {
  if (n > 0) return "pos"
  if (n < 0) return "neg"
  return "flat"
}

function exportCsv() {
  const rows = filtered.value
  if (!rows.length) return
  const header = [
    "time",
    "symbol",
    "side",
    "price",
    "quantity",
    "quoteQty",
    "realizedPnl",
    "commission",
    "orderId",
    "tradeId",
  ]
  const lines = [
    header.join(","),
    ...rows.map((r) =>
      [
        r.tradedAt,
        r.symbol,
        r.side,
        r.price,
        r.quantity,
        r.quoteQty,
        r.realizedPnl,
        r.commission,
        r.orderId,
        r.exchangeTradeId,
      ].join(","),
    ),
  ]
  const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" })
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = `trades-${journalSym.value}-${Date.now()}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

function refreshTrades() {
  if (props.symbol) {
    return store.fetchTradesForSymbol(journalSym.value)
  }
  return store.fetchTrades()
}

watch(
  () => (props.symbol ? journalSym.value : store.symbol),
  () => {
    void refreshTrades()
  },
)

onMounted(() => {
  void refreshTrades()
  refreshTimer = setInterval(() => {
    if (props.symbol) {
      void store.fetchTradesForSymbol(journalSym.value, { quiet: true })
    } else {
      void store.fetchTrades({ quiet: true })
    }
  }, 60_000)
})

onBeforeUnmount(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})
</script>

<template>
  <section class="trade-journal panel" aria-label="سجل الصفقات">
    <header class="journal-head">
      <div class="journal-title-block">
        <h2 class="journal-title">
          {{ since ? "تنفيذات الشبكة" : "سجل الصفقات" }}
          <span v-if="symbol" class="sym-tag">{{ journalSym }}</span>
        </h2>
        <p class="journal-sub muted">
          {{ since ? "fills منذ التشغيل فقط" : "تنفيذات Spot" }} · {{ store.spotEnvLabelAr }}
          <span v-if="journalSource === 'binance'" class="src live">· متزامن</span>
          <span v-else class="src">· محلي</span>
          <span v-if="!symbol && store.tradesSymbol" class="sym-tag">{{ store.tradesSymbol }}</span>
        </p>
      </div>
      <div class="journal-actions">
        <button
          type="button"
          class="btn btn-ghost"
          :disabled="journalLoading"
          @click="refreshTrades()"
        >
          {{ journalLoading ? "…" : "تحديث" }}
        </button>
        <button
          type="button"
          class="btn btn-ghost"
          :disabled="!filtered.length"
          @click="exportCsv"
        >
          تصدير CSV
        </button>
        <button
          v-if="embedded"
          type="button"
          class="btn btn-close"
          aria-label="إغلاق السجل"
          @click="emit('close')"
        >
          إغلاق
        </button>
      </div>
    </header>

    <p v-if="journalError" class="journal-err" role="alert">{{ journalError }}</p>
    <p v-else-if="journalSyncError" class="journal-warn" role="status">
      تعذّر المزامنة مع المنصة — يُعرض السجل المحفوظ. {{ journalSyncError }}
    </p>

    <div class="summary-row">
      <div class="summary-card">
        <span class="summary-label">{{ since ? "تنفيذات" : "صفقات" }}</span>
        <span class="summary-value">{{ summary.count }}</span>
      </div>
      <div class="summary-card">
        <span class="summary-label">شراء / بيع</span>
        <span class="summary-value">
          <span class="buy">{{ summary.buyCount }}</span>
          <span class="sep">/</span>
          <span class="sell">{{ summary.sellCount }}</span>
        </span>
      </div>
      <div class="summary-card">
        <span class="summary-label">حجم (USDT)</span>
        <span class="summary-value">{{ formatNum(summary.totalQuoteVolume, 2) }}</span>
      </div>
      <div class="summary-card" :class="pnlClass(closedFifoPnl)">
        <span class="summary-label">{{ since ? "ربح مغلق" : "ربح محقق" }}</span>
        <span class="summary-value">
          {{ closedFifoPnl >= 0 ? "+" : "" }}{{ formatNum(closedFifoPnl, 4) }}
        </span>
      </div>
      <div class="summary-card">
        <span class="summary-label">عمولة</span>
        <span class="summary-value">{{ formatNum(summary.totalCommission, 4) }}</span>
      </div>
    </div>

    <div class="toolbar">
      <div class="filters" role="tablist">
        <button
          type="button"
          class="filter-btn"
          :class="{ active: sideFilter === 'all' }"
          @click="sideFilter = 'all'"
        >
          الكل
        </button>
        <button
          type="button"
          class="filter-btn buy"
          :class="{ active: sideFilter === 'BUY' }"
          @click="sideFilter = 'BUY'"
        >
          شراء
        </button>
        <button
          type="button"
          class="filter-btn sell"
          :class="{ active: sideFilter === 'SELL' }"
          @click="sideFilter = 'SELL'"
        >
          بيع
        </button>
      </div>
      <input
        v-model="search"
        class="search field"
        type="search"
        placeholder="بحث برقم الأمر أو الصفقة…"
        autocomplete="off"
      />
    </div>

    <div v-if="store.tradesLoading && !store.trades.length" class="table-empty">
      جاري تحميل السجل…
    </div>
    <div v-else-if="!filtered.length" class="table-empty muted">
      لا توجد صفقات منفّذة لهذا الرمز بعد.
    </div>

    <div v-else class="table-wrap">
      <table class="trades-table">
        <thead>
          <tr>
            <th>الوقت</th>
            <th>الرمز</th>
            <th>الاتجاه</th>
            <th>السعر</th>
            <th>الكمية</th>
            <th>القيمة</th>
            <th>ربح محقق</th>
            <th>عمولة</th>
            <th>النوع</th>
            <th>رقم الأمر</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in filtered" :key="row.exchangeTradeId">
            <td class="col-time">{{ formatTime(row.tradedAt, row.tradedAtMs) }}</td>
            <td class="col-sym">{{ row.symbol }}</td>
            <td>
              <span class="side-pill" :class="row.side === 'BUY' ? 'buy' : 'sell'">
                {{ row.side === "BUY" ? "شراء" : "بيع" }}
              </span>
            </td>
            <td class="num">{{ formatPrice(row.price) }}</td>
            <td class="num">{{ formatNum(row.quantity, 6) }}</td>
            <td class="num">{{ formatNum(row.quoteQty, 2) }}</td>
            <td class="num" :class="pnlClass(row.realizedPnl)">
              {{ row.realizedPnl >= 0 ? "+" : "" }}{{ formatNum(row.realizedPnl, 4) }}
            </td>
            <td class="num muted">
              {{ formatNum(row.commission, 4) }}
              <span class="asset">{{ row.commissionAsset }}</span>
            </td>
            <td>
              <span class="role-pill">{{ row.isMaker ? "Maker" : "Taker" }}</span>
            </td>
            <td class="col-id" :title="row.orderId">{{ row.orderId }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <p v-if="store.tradesUpdatedAt" class="footer-meta muted">
      آخر تحديث: {{ new Date(store.tradesUpdatedAt).toLocaleString() }}
    </p>
  </section>
</template>

<style scoped>
.trade-journal {
  display: flex;
  flex-direction: column;
  gap: 0.85rem;
}
.journal-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
}
.journal-title {
  margin: 0;
  font-size: 1.05rem;
  font-weight: 700;
}
.journal-sub {
  margin: 0.2rem 0 0;
  font-size: 0.78rem;
}
.src.live {
  color: var(--accent);
}
.sym-tag {
  margin-inline-start: 0.35rem;
  padding: 0.1rem 0.4rem;
  border-radius: 4px;
  background: rgba(56, 189, 248, 0.12);
  color: #7dd3fc;
  font-weight: 600;
}
.journal-actions {
  display: flex;
  gap: 0.4rem;
}
button.btn-ghost {
  background: #1a222c;
  color: var(--text);
  border: 1px solid var(--border);
  padding: 0.4rem 0.75rem;
  font-size: 0.8rem;
  font-weight: 600;
}
button.btn-ghost:hover:not(:disabled) {
  border-color: rgba(56, 189, 248, 0.45);
}
button.btn-close {
  background: transparent;
  color: #94a3b8;
  border: 1px solid #3d4450;
  padding: 0.4rem 0.75rem;
  font-size: 0.8rem;
  font-weight: 600;
}
button.btn-close:hover {
  color: #e2e8f0;
  border-color: #5c6573;
}
.journal-err {
  margin: 0;
  font-size: 0.8rem;
  color: #ffb4c0;
  background: rgba(246, 70, 93, 0.1);
  border: 1px solid rgba(246, 70, 93, 0.35);
  border-radius: 8px;
  padding: 0.5rem 0.75rem;
}
.journal-warn {
  margin: 0;
  font-size: 0.78rem;
  color: #fbbf24;
}
.summary-row {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 0.5rem;
}
@media (max-width: 900px) {
  .summary-row {
    grid-template-columns: repeat(2, 1fr);
  }
}
.summary-card {
  background: linear-gradient(160deg, rgba(22, 28, 36, 0.95), rgba(12, 14, 18, 0.98));
  border: 1px solid rgba(30, 38, 48, 0.9);
  border-radius: 10px;
  padding: 0.65rem 0.75rem;
}
.summary-card.pos {
  border-color: rgba(14, 203, 129, 0.35);
}
.summary-card.neg {
  border-color: rgba(246, 70, 93, 0.35);
}
.summary-label {
  display: block;
  font-size: 0.65rem;
  text-transform: uppercase;
  letter-spacing: 0.07em;
  color: var(--muted);
  margin-bottom: 0.2rem;
}
.summary-value {
  font-size: 1.05rem;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.summary-value .buy {
  color: var(--accent);
}
.summary-value .sell {
  color: var(--danger);
}
.summary-value .sep {
  color: var(--muted);
  margin: 0 0.15rem;
}
.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  align-items: center;
}
.filters {
  display: flex;
  gap: 0.25rem;
  background: #0f1318;
  padding: 0.2rem;
  border-radius: 8px;
  border: 1px solid var(--border);
}
.filter-btn {
  border: none;
  background: transparent;
  color: var(--muted);
  padding: 0.35rem 0.7rem;
  border-radius: 6px;
  font-size: 0.78rem;
  font-weight: 600;
  cursor: pointer;
}
.filter-btn.active {
  background: #1e2630;
  color: var(--text);
}
.filter-btn.buy.active {
  background: rgba(14, 203, 129, 0.15);
  color: var(--accent);
}
.filter-btn.sell.active {
  background: rgba(246, 70, 93, 0.12);
  color: #ff8a9a;
}
.search {
  flex: 1;
  min-width: 160px;
  max-width: 280px;
}
.table-wrap {
  overflow: auto;
  border: 1px solid var(--border);
  border-radius: 10px;
  max-height: 420px;
}
.trades-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.8rem;
}
.trades-table thead {
  position: sticky;
  top: 0;
  z-index: 1;
  background: #151a22;
}
.trades-table th {
  text-align: start;
  padding: 0.55rem 0.65rem;
  font-size: 0.68rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--muted);
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
.trades-table td {
  padding: 0.5rem 0.65rem;
  border-bottom: 1px solid rgba(30, 38, 48, 0.6);
  vertical-align: middle;
}
.trades-table tbody tr:hover {
  background: rgba(30, 38, 48, 0.45);
}
.col-time {
  white-space: nowrap;
  color: #94a3b8;
  font-variant-numeric: tabular-nums;
}
.col-sym {
  font-weight: 600;
}
.col-id {
  max-width: 88px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: ui-monospace, monospace;
  font-size: 0.72rem;
  color: var(--muted);
}
.num {
  font-variant-numeric: tabular-nums;
  text-align: end;
}
.num.pos {
  color: var(--accent);
}
.num.neg {
  color: var(--danger);
}
.side-pill {
  display: inline-block;
  padding: 0.15rem 0.45rem;
  border-radius: 4px;
  font-size: 0.72rem;
  font-weight: 700;
}
.side-pill.buy {
  background: rgba(14, 203, 129, 0.15);
  color: var(--accent);
}
.side-pill.sell {
  background: rgba(246, 70, 93, 0.12);
  color: #ff8a9a;
}
.role-pill {
  font-size: 0.68rem;
  color: var(--muted);
  padding: 0.1rem 0.35rem;
  border: 1px solid var(--border);
  border-radius: 4px;
}
.asset {
  font-size: 0.65rem;
  margin-inline-start: 0.15rem;
}
.table-empty {
  padding: 2.5rem 1rem;
  text-align: center;
  border: 1px dashed var(--border);
  border-radius: 10px;
}
.footer-meta {
  margin: 0;
  font-size: 0.72rem;
  text-align: end;
}
</style>
