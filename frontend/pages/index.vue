<script setup lang="ts">
import { useBotStore } from "~/stores/bot"

const store = useBotStore()
/** Defer chart mount until dashboard (symbol + balances) is hydrated from the API. */
const dashboardLoaded = ref(false)
const saving = ref(false)
const killing = ref(false)

const form = reactive({
  generatorUpper: 0,
  generatorLower: 0,
  generatorCount: 5,
  initialCapital: 100,
})

watch(
  () => ({
    u: store.generatorUpper,
    l: store.generatorLower,
    c: store.generatorCount,
    i: store.initialCapital,
  }),
  (v) => {
    form.generatorUpper = v.u
    form.generatorLower = v.l
    form.generatorCount = v.c
    form.initialCapital = v.i
  },
  { immediate: true },
)

onMounted(async () => {
  await store.fetchDashboard()
  dashboardLoaded.value = true
  store.connectWs()
})

onBeforeUnmount(() => {
  store.disconnectWs()
})

async function onSave() {
  saving.value = true
  try {
    await store.saveSettings({
      generatorUpper: form.generatorUpper,
      generatorLower: form.generatorLower,
      generatorCount: form.generatorCount,
      initialCapital: form.initialCapital,
    })
  } finally {
    saving.value = false
  }
}

async function onKill() {
  if (!confirm("تأكيد: إيقاف طوارئ — إلغاء الأوامر وإغلاق المراكز بسعر السوق؟")) return
  killing.value = true
  try {
    await store.emergencyStop()
  } finally {
    killing.value = false
  }
}
</script>

<template>
  <main class="dash">
    <header class="dash-header">
      <div class="title-row">
        <h1>AlKarrar Pro</h1>
        <span
          v-if="store.credentialsConfigured && store.exchangeTestnet"
          class="testnet-badge"
          title="ورق / ديمو — مفاتيح من demo.binance.com (REST: demo-fapi.binance.com)"
        >
          Testnet Mode
        </span>
      </div>
      <div class="conn" :class="{ ok: store.wsConnected, bad: !store.wsConnected }">
        WS: {{ store.wsConnected ? "live" : "offline" }}
        <span v-if="store.lastWsAt && store.wsConnected" class="muted tiny">
          · آخر حدث: {{ new Date(store.lastWsAt).toLocaleTimeString() }}
        </span>
        <span v-if="store.wsError" class="err">{{ store.wsError }}</span>
      </div>
    </header>

    <section class="balance-hero panel">
      <div class="balance-hero-head">
        <h2>رصيد الحساب (حي)</h2>
        <span class="sym-pill">{{ store.symbol }}</span>
      </div>
      <p class="muted balance-hint">
        يُحدَّث عند التحميل من REST (<code>futures_account</code>) ثم عبر WebSocket: أحداث User Stream تطلق
        مزامنة كاملة وتبث <code>metrics</code> بقيم <code>totalWalletBalance</code> / <code>totalMarginBalance</code> / <code>availableBalance</code>.
      </p>
      <div class="balance-cards">
        <div class="balance-card">
          <div class="balance-label">Available Balance</div>
          <div class="balance-num">{{ store.availableBalance.toFixed(2) }} <span class="unit">USDT</span></div>
        </div>
        <div class="balance-card">
          <div class="balance-label">Total Margin Balance</div>
          <div class="balance-num">{{ store.totalMarginBalance.toFixed(2) }} <span class="unit">USDT</span></div>
        </div>
        <div class="balance-card">
          <div class="balance-label">Total Wallet Balance</div>
          <div class="balance-num">{{ store.totalWalletBalance.toFixed(2) }} <span class="unit">USDT</span></div>
        </div>
      </div>
    </section>

    <section class="panel cred-env-panel">
      <h2>اتصال Binance (السيرفر)</h2>
      <p class="muted">
        تُقرأ المفاتيح من ملف <code>.env</code> عند تشغيل الـ API:
        <code>BINANCE_API_KEY</code>، <code>BINANCE_API_SECRET</code>، <code>BINANCE_TESTNET</code>
        (ورق/ديمو: <a href="https://demo.binance.com" target="_blank" rel="noopener">demo.binance.com</a>
        → <code>demo-fapi.binance.com</code>). إن وُجدت مفاتيح في قاعدة البيانات فلها الأولوية على <code>.env</code>.
        راجع <code>.env.example</code>. إذا ظهر <strong>WS: offline</strong> فتأكد أن الـ API يعمل على المنفذ
        <code>8090</code> أو عيّن <code>NUXT_PUBLIC_WS_URL=ws://127.0.0.1:8090/ws</code> في بيئة الواجه.
      </p>
      <div v-if="store.syncError" class="sync-err" role="alert">
        <strong>مزامنة Binance:</strong> {{ store.syncError }}
      </div>
      <div v-else-if="store.syncOkAt" class="sync-ok muted">آخر مزامنة ناجحة: {{ store.syncOkAt }}</div>
      <div v-if="store.credentialsConfigured" class="key-status">
        مفعّل: <code>{{ store.binanceApiKeyPreview }}</code> —
        {{ store.binanceTestnetStored ? "Demo / Testnet" : "Mainnet" }}
      </div>
      <div v-else class="sync-err" role="alert">
        لا توجد مفاتيح صالحة في <code>.env</code> أو قاعدة البيانات. أضف القيم وأعد تشغيل خادم الـ API.
      </div>
    </section>

    <section class="grid-cards panel metrics">
      <div>
        <div class="metric-label">Realized PnL</div>
        <div class="metric-value">{{ store.realizedPnl.toFixed(4) }}</div>
      </div>
      <div>
        <div class="metric-label">Floating PnL</div>
        <div class="metric-value">{{ store.floatingPnl.toFixed(4) }}</div>
      </div>
      <div>
        <div class="metric-label">Wallet Balance</div>
        <div class="metric-value">{{ store.totalWalletBalance.toFixed(2) }}</div>
      </div>
      <div>
        <div class="metric-label">Active Grid Lines</div>
        <div class="metric-value">{{ store.activeGridLines }}</div>
      </div>
      <div>
        <div class="metric-label">Mark</div>
        <div class="metric-value">{{ store.markPrice.toFixed(6) }}</div>
      </div>
    </section>

    <section class="chart-wrap panel">
      <h2 class="chart-title">الشموع — {{ store.symbol }} (5m)</h2>
      <ClientOnly>
        <TradingChart v-if="dashboardLoaded" />
        <div v-else class="chart-fallback">جاري تحميل الإعدادات والرسم…</div>
        <template #fallback>
          <div class="chart-fallback">جاري تحميل الرسم…</div>
        </template>
      </ClientOnly>
    </section>

    <section class="controls">
      <form class="panel form-grid" @submit.prevent="onSave">
        <h2>إعدادات الشبكة (BFF)</h2>
        <label>
          <span class="metric-label">generatorUpper</span>
          <input v-model.number="form.generatorUpper" class="field" type="number" step="any" required />
        </label>
        <label>
          <span class="metric-label">generatorLower</span>
          <input v-model.number="form.generatorLower" class="field" type="number" step="any" required />
        </label>
        <label>
          <span class="metric-label">generatorCount</span>
          <input v-model.number="form.generatorCount" class="field" type="number" min="2" required />
        </label>
        <label>
          <span class="metric-label">initialCapital</span>
          <input v-model.number="form.initialCapital" class="field" type="number" step="any" min="0.01" required />
        </label>
        <button class="btn btn-primary" type="submit" :disabled="saving">
          {{ saving ? "…" : "حفظ وإرسال" }}
        </button>
      </form>

      <div class="panel kill-wrap">
        <h2>Kill Switch</h2>
        <p class="muted">POST /api/emergency_stop — إلغاء الأوامر وإغلاق المراكز (MARKET)</p>
        <button class="btn btn-danger" type="button" :disabled="killing" @click="onKill">
          {{ killing ? "…" : "إيقاف طوارئ" }}
        </button>
      </div>
    </section>
  </main>
</template>

<style scoped>
.dash {
  max-width: 1200px;
  margin: 0 auto;
  padding: 1rem 1.25rem 2rem;
  display: flex;
  flex-direction: column;
  gap: 1rem;
}
.dash-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
}
.title-row {
  display: flex;
  align-items: center;
  gap: 0.65rem;
  flex-wrap: wrap;
}
.testnet-badge {
  font-size: 0.7rem;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  padding: 0.28rem 0.6rem;
  border-radius: 6px;
  background: rgba(245, 158, 11, 0.18);
  color: #fbbf24;
  border: 1px solid rgba(245, 158, 11, 0.5);
}
.tiny {
  font-size: 0.72rem;
  opacity: 0.85;
}
.dash-header h1 {
  margin: 0;
  font-size: 1.35rem;
}
.conn {
  font-size: 0.85rem;
  color: var(--muted);
}
.conn.ok {
  color: var(--accent);
}
.conn.bad {
  color: var(--warn);
}
.err {
  display: block;
  color: var(--danger);
  font-size: 0.75rem;
}
.balance-hero h2 {
  margin: 0;
  font-size: 1rem;
}
.balance-hero-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  flex-wrap: wrap;
}
.sym-pill {
  font-size: 0.75rem;
  font-weight: 700;
  letter-spacing: 0.04em;
  padding: 0.25rem 0.6rem;
  border-radius: 999px;
  background: rgba(56, 189, 248, 0.12);
  color: #7dd3fc;
  border: 1px solid rgba(56, 189, 248, 0.35);
}
.balance-hint {
  margin: 0.35rem 0 0.85rem;
}
.balance-cards {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 0.75rem;
}
@media (max-width: 720px) {
  .balance-cards {
    grid-template-columns: 1fr;
  }
}
.balance-card {
  background: linear-gradient(160deg, rgba(30, 38, 48, 0.95), rgba(15, 18, 22, 0.98));
  border: 1px solid rgba(56, 189, 248, 0.15);
  border-radius: 12px;
  padding: 1rem 1.1rem;
}
.balance-card.neg {
  border-color: rgba(246, 70, 93, 0.35);
}
.balance-card.pos {
  border-color: rgba(14, 203, 129, 0.35);
}
.balance-label {
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--muted);
  margin-bottom: 0.35rem;
}
.balance-num {
  font-size: 1.45rem;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.02em;
}
.balance-num .unit {
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--muted);
  margin-inline-start: 0.2rem;
}
.chart-title {
  margin: 0 0 0.65rem;
  font-size: 0.95rem;
  font-weight: 600;
  color: #e2e8f0;
}
.chart-wrap {
  min-height: 460px;
}
.chart-fallback {
  height: 420px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--muted);
}
.controls {
  display: grid;
  grid-template-columns: 1fr 280px;
  gap: 1rem;
}
@media (max-width: 900px) {
  .controls {
    grid-template-columns: 1fr;
  }
}
.form-grid {
  display: flex;
  flex-direction: column;
  gap: 0.65rem;
}
.cred-env-panel h2 {
  margin: 0 0 0.35rem;
  font-size: 1rem;
}
.cred-env-panel a {
  color: #38bdf8;
}
.sync-err {
  background: rgba(246, 70, 93, 0.12);
  border: 1px solid rgba(246, 70, 93, 0.45);
  border-radius: 8px;
  padding: 0.65rem 0.85rem;
  margin-bottom: 0.75rem;
  font-size: 0.85rem;
  color: #ffb4c0;
}
.sync-ok {
  margin-bottom: 0.5rem;
}
.key-status {
  font-size: 0.85rem;
  margin-bottom: 0.75rem;
  color: var(--accent);
}
button.btn-secondary {
  background: #2a3340;
  color: var(--text);
}
.form-grid h2,
.kill-wrap h2 {
  margin: 0 0 0.35rem;
  font-size: 1rem;
}
.muted {
  color: var(--muted);
  font-size: 0.8rem;
  margin: 0 0 0.75rem;
}
.kill-wrap {
  display: flex;
  flex-direction: column;
}
</style>
