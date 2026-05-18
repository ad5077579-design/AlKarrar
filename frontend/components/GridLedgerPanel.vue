<script setup lang="ts">
import { computed, onMounted, watch } from "vue"
import { useBotStore, type GridLedgerEntry } from "~/stores/bot"

const props = defineProps<{
  symbol: string
}>()

const store = useBotStore()

const sym = computed(() => props.symbol.trim().toUpperCase().replace("/", ""))
const pack = computed(() => store.gridLedgerPack(sym.value))
const HIDDEN_FAILURE_CTX = new Set([
  "virtual_grid_no_exchange_fill",
  "virtual_grid_fill_no_exchange_fill",
  "virtual_grid_slippage",
])

function isNoiseRow(row: GridLedgerEntry): boolean {
  if (row.actionType !== "API_FAILURE") return false
  const ctx = String(row.extra?.context ?? row.triggerReason ?? "")
  if (HIDDEN_FAILURE_CTX.has(ctx)) return true
  return ctx.endsWith("_no_exchange_fill")
}

const rows = computed(() =>
  [...(pack.value?.entries ?? [])].filter((r) => !isNoiseRow(r)).reverse(),
)
const showPanel = computed(
  () =>
    store.isGridActiveForSelectedSymbol ||
    Boolean(pack.value && (pack.value.entries.length > 0 || pack.value.frozen)),
)

function formatTs(ms: number): string {
  if (!(ms > 0)) return "—"
  const d = new Date(ms)
  return `${d.toLocaleDateString()} ${d.toLocaleTimeString()}.${String(d.getMilliseconds()).padStart(3, "0")}`
}

function fmtNum(n: number | null | undefined, digits = 6): string {
  if (n == null || !Number.isFinite(n)) return "—"
  if (Math.abs(n) >= 1000) return n.toLocaleString(undefined, { maximumFractionDigits: 2 })
  return n.toFixed(digits)
}

function actionClass(action: string): string {
  const a = action.toUpperCase()
  if (a.includes("BUY")) return "act-buy"
  if (a.includes("SELL")) return "act-sell"
  if (a.includes("EMERGENCY") || a.includes("FAIL")) return "act-bad"
  if (a.includes("SHIFT") || a.includes("RESIZE")) return "act-warn"
  return "act-neutral"
}

async function refresh() {
  await store.fetchGridLedger(sym.value)
}

async function onClear() {
  if (!pack.value?.frozen) return
  if (!confirm("مسح سجل تدقيق الشبكة المجمّد؟")) return
  await store.clearGridLedger(sym.value)
}

onMounted(() => {
  void refresh()
})

watch(sym, () => {
  void refresh()
})
</script>

<template>
  <section v-if="showPanel" class="grid-ledger panel" aria-label="سجل تدقيق الشبكة">
    <header class="ledger-head">
      <div>
        <h3 class="ledger-title">سجل تدقيق الشبكة</h3>
        <p class="ledger-sub muted">قراءة فقط · ذاكرة مؤقتة (لا يُحفظ في قاعدة البيانات)</p>
      </div>
      <div class="ledger-actions">
        <span v-if="pack?.frozen" class="frozen-badge">مجمّد — سبب التوقف محفوظ</span>
        <button type="button" class="btn-ghost" @click="refresh">تحديث</button>
        <button
          v-if="pack?.frozen"
          type="button"
          class="btn-clear"
          @click="onClear"
        >
          مسح السجل
        </button>
      </div>
    </header>
    <p v-if="pack?.freezeReason" class="freeze-reason" role="status">
      {{ pack.freezeReason }}
    </p>
    <div class="table-wrap">
      <table class="ledger-table">
        <thead>
          <tr>
            <th>الوقت</th>
            <th>الإجراء</th>
            <th>السبب</th>
            <th>هدف / تنفيذ</th>
            <th>انزلاق</th>
            <th>حجم</th>
            <th>الشبكة</th>
            <th>خطأ</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.id">
            <td class="col-time">{{ formatTs(row.timestampMs) }}</td>
            <td>
              <span class="action-pill" :class="actionClass(row.actionType)">{{
                row.actionType
              }}</span>
            </td>
            <td class="col-reason">{{ row.triggerReason }}</td>
            <td class="col-num">
              {{ fmtNum(row.targetPrice) }}
              <span v-if="row.fillPrice != null" class="muted"> → {{ fmtNum(row.fillPrice) }}</span>
            </td>
            <td class="col-num">
              <span
                v-if="row.slippagePct != null"
                :class="Math.abs(row.slippagePct) > 0.15 ? 'slip-warn' : ''"
              >
                {{ row.slippagePct!.toFixed(3) }}%
              </span>
              <span v-else>—</span>
            </td>
            <td class="col-num">{{ fmtNum(row.orderSize, 4) }}</td>
            <td class="col-grid muted">
              {{ fmtNum(row.generatorLower, 4) }} – {{ fmtNum(row.generatorUpper, 4) }}
              <span> · n={{ row.generatorCount }}</span>
            </td>
            <td class="col-err">
              <template v-if="row.apiErrorCode">
                <span class="err-code">{{ row.apiErrorCode }}</span>
              </template>
              <span v-else>—</span>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="!rows.length" class="empty muted">لا أحداث بعد — ستظهر هنا مع تشغيل الشبكة.</p>
    </div>
  </section>
</template>

<style scoped>
.grid-ledger {
  margin-top: 1rem;
  background: #181a20;
  border: 1px solid #2b3139;
  border-radius: 8px;
  overflow: hidden;
}
.ledger-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem;
  flex-wrap: wrap;
  padding: 0.75rem 1rem;
  border-bottom: 1px solid #2b3139;
  background: #1e2329;
}
.ledger-title {
  margin: 0;
  font-size: 0.92rem;
  font-weight: 600;
  color: #eaecef;
}
.ledger-sub {
  margin: 0.2rem 0 0;
  font-size: 0.68rem;
}
.ledger-actions {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  flex-wrap: wrap;
}
.frozen-badge {
  font-size: 0.68rem;
  font-weight: 700;
  padding: 0.2rem 0.5rem;
  border-radius: 4px;
  background: rgba(246, 70, 93, 0.15);
  color: #f6465d;
  border: 1px solid rgba(246, 70, 93, 0.4);
}
.freeze-reason {
  margin: 0;
  padding: 0.45rem 1rem;
  font-size: 0.78rem;
  color: #f0b90b;
  background: rgba(240, 185, 11, 0.08);
  border-bottom: 1px solid rgba(240, 185, 11, 0.2);
}
.btn-ghost,
.btn-clear {
  font-size: 0.72rem;
  font-weight: 600;
  padding: 0.3rem 0.6rem;
  border-radius: 4px;
  cursor: pointer;
  border: 1px solid #2b3139;
  background: #0f1318;
  color: #eaecef;
}
.btn-clear {
  border-color: rgba(246, 70, 93, 0.45);
  color: #f6465d;
}
.table-wrap {
  overflow-x: auto;
  max-height: 320px;
  overflow-y: auto;
}
.ledger-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.72rem;
}
.ledger-table th {
  position: sticky;
  top: 0;
  z-index: 1;
  text-align: start;
  padding: 0.45rem 0.5rem;
  background: #1e2329;
  color: #848e9c;
  font-weight: 600;
  white-space: nowrap;
}
.ledger-table td {
  padding: 0.4rem 0.5rem;
  border-top: 1px solid rgba(43, 49, 57, 0.6);
  color: #cbd5e1;
  vertical-align: top;
}
.ledger-table tbody tr:hover td {
  background: rgba(43, 49, 57, 0.35);
}
.col-time {
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.col-reason {
  max-width: 220px;
  line-height: 1.35;
}
.col-num {
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.col-grid {
  font-size: 0.68rem;
  white-space: nowrap;
}
.action-pill {
  display: inline-block;
  padding: 0.12rem 0.35rem;
  border-radius: 4px;
  font-weight: 700;
  font-size: 0.65rem;
  letter-spacing: 0.03em;
}
.act-buy {
  background: rgba(14, 203, 129, 0.18);
  color: #0ecb81;
}
.act-sell {
  background: rgba(246, 70, 93, 0.18);
  color: #f6465d;
}
.act-bad {
  background: rgba(246, 70, 93, 0.25);
  color: #ff707e;
}
.act-warn {
  background: rgba(240, 185, 11, 0.15);
  color: #f0b90b;
}
.act-neutral {
  background: rgba(148, 163, 184, 0.12);
  color: #94a3b8;
}
.slip-warn {
  color: #f0b90b;
}
.err-code {
  color: #f6465d;
  font-weight: 700;
}
.empty {
  padding: 1rem;
  text-align: center;
}
.muted {
  color: #848e9c;
}
</style>
