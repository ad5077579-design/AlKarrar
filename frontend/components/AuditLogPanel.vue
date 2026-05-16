<script setup lang="ts">
import { onMounted, ref, watch } from "vue"
import { useBotStore, type AuditLogRow } from "~/stores/bot"

const store = useBotStore()

const logs = ref<AuditLogRow[]>([])
const loading = ref(false)
const errorMsg = ref("")

const eventTitle: Record<string, string> = {
  TRAILING_STARTED: "بدء ملاحقة (Trailing)",
  TAKE_PROFIT_MARKET: "خروج سوق بعد Trailing Stop",
  PROFIT_INJECT_EXPAND: "حقن أرباح — زيادة خطوط الشبكة",
  PROFIT_INJECT_COMPOUND: "حقن أرباح — تكبير حجم الطلبات",
  GRID_SHIFT: "رفع نطاق الشبكة (اختراق علوي)",
  SYSTEM_ERROR: "خطأ المنصة / تنفيذ",
}

function prettyEvent(t: string): string {
  return eventTitle[t] ? `${eventTitle[t]} (${t})` : t
}

async function refresh() {
  loading.value = true
  errorMsg.value = ""
  try {
    logs.value = await store.fetchAuditLogs(250)
  } catch (e) {
    errorMsg.value = String(e)
    logs.value = []
  } finally {
    loading.value = false
  }
}

function formatTs(iso: string): string {
  if (!iso) return "—"
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

function fmtUsdt(v: unknown): string {
  const n = typeof v === "number" ? v : Number(v)
  return Number.isFinite(n) ? n.toFixed(4) : "—"
}

function summarizeDetails(details: Record<string, unknown> | null | undefined): string {
  const d = details && typeof details === "object" ? details : {}
  try {
    return JSON.stringify(d, null, 0)
  } catch {
    return String(d)
  }
}

watch(
  () => store.symbol,
  () => {
    void refresh()
  },
)

onMounted(() => {
  void refresh()
})

defineExpose({ refresh })
</script>

<template>
  <section class="audit-panel panel" aria-label="سجل عمليات البوت">
    <header class="audit-head">
      <div>
        <h2>سجل العمليات</h2>
        <p class="audit-sub muted">Audit Logs · مراقبة قرارات المحرك وحالة المخاطر</p>
      </div>
      <button type="button" class="btn-refresh" :disabled="loading" @click="refresh">
        {{ loading ? "…" : "تحديث" }}
      </button>
    </header>

    <p v-if="errorMsg" class="audit-error" role="alert">{{ errorMsg }}</p>
    <p v-else-if="!loading && !logs.length" class="muted audit-empty">
      لا توجد أحداث بعد. عند تشغيل الشبكة ستظهر هنا قرارات مثل GRID_SHIFT وحقن الأرباح وبدء التتبّع الخ.
    </p>

    <div v-else class="table-scroll">
      <table class="audit-table">
        <thead>
          <tr>
            <th>وقت الحدث</th>
            <th>نوع الحدث</th>
            <th>Mark</th>
            <th>محقّق USDT</th>
            <th>التفاصيل (JSON)</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in logs" :key="row.id">
            <td class="mono">{{ formatTs(row.timestamp) }}</td>
            <td>{{ prettyEvent(row.eventType) }}</td>
            <td class="mono">
              {{
                row.markPrice != null && Number.isFinite(Number(row.markPrice))
                  ? Number(row.markPrice).toFixed(8)
                  : "—"
              }}
            </td>
            <td class="mono">{{ fmtUsdt(row.realizedUsdt) }}</td>
            <td class="details-cell">
              <code class="details-json">{{ summarizeDetails(row.details) }}</code>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.audit-panel {
  background: #1e2329;
  border: 1px solid #2b3139;
  border-radius: 8px;
  padding: 1rem 1rem 1.25rem;
}
.audit-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 1rem;
  flex-wrap: wrap;
  margin-bottom: 0.75rem;
}
.audit-head h2 {
  margin: 0 0 0.25rem;
  font-size: 1.05rem;
  font-weight: 600;
}
.audit-sub {
  margin: 0;
  font-size: 0.78rem;
}
.btn-refresh {
  border: 1px solid #474d57;
  background: #2b3139;
  color: #eaecef;
  border-radius: 4px;
  padding: 0.45rem 0.85rem;
  font-size: 0.82rem;
  cursor: pointer;
}
.btn-refresh:disabled {
  opacity: 0.55;
}
.audit-error {
  color: #f6465d;
  font-size: 0.85rem;
  margin-bottom: 0.5rem;
}
.audit-empty {
  font-size: 0.85rem;
  margin: 0.5rem 0 0;
}
.table-scroll {
  overflow-x: auto;
  margin-top: 0.35rem;
  border-radius: 6px;
  border: 1px solid #2b3139;
}
.audit-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.78rem;
}
.audit-table th,
.audit-table td {
  padding: 0.45rem 0.55rem;
  text-align: start;
  border-bottom: 1px solid rgba(43, 49, 57, 0.85);
  vertical-align: top;
}
.audit-table th {
  color: #848e9c;
  font-weight: 600;
  background: #181a20;
  white-space: nowrap;
}
.audit-table tr:hover td {
  background: rgba(240, 185, 11, 0.04);
}
.mono {
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.details-cell {
  max-width: 420px;
  word-break: break-word;
}
.details-json {
  display: block;
  font-family: ui-monospace, monospace;
  font-size: 0.68rem;
  color: #b7bdc6;
  line-height: 1.35;
  white-space: pre-wrap;
}
</style>
