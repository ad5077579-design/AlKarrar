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
    <header class="audit-head panel-header">
      <div>
        <h2 class="panel-title">سجل العمليات</h2>
        <p class="panel-subtitle">Audit Logs · مراقبة قرارات المحرك وحالة المخاطر</p>
      </div>
      <button type="button" class="btn-ghost" :disabled="loading" @click="refresh">
        {{ loading ? "…" : "تحديث" }}
      </button>
    </header>

    <p v-if="errorMsg" class="audit-error" role="alert">{{ errorMsg }}</p>
    <p v-else-if="!loading && !logs.length" class="muted audit-empty">
      لا توجد أحداث بعد. عند تشغيل الشبكة ستظهر هنا قرارات مثل GRID_SHIFT وحقن الأرباح وبدء التتبّع الخ.
    </p>

    <div v-else class="data-table-wrap">
      <table class="data-table audit-table">
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
  padding-top: 0.9rem;
}
.audit-error {
  color: var(--danger);
  font-size: 0.82rem;
  margin-bottom: 0.5rem;
}
.audit-empty {
  font-size: 0.82rem;
  margin: 0.5rem 0 0;
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
  color: var(--text-secondary);
  line-height: 1.35;
  white-space: pre-wrap;
}
</style>
