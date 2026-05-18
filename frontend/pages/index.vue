<script setup lang="ts">
import BotAssignedActivityPanel from "~/components/BotAssignedActivityPanel.vue"
import CompoundingRiskPanel from "~/components/CompoundingRiskPanel.vue"
import EmergencyBar from "~/components/EmergencyBar.vue"
import { useBotStore } from "~/stores/bot"

const store = useBotStore()

function closeTradesJournal() {
  store.tradesViewSymbol = null
}

/** Defer chart mount until dashboard (symbol + balances) is hydrated from the API. */
const dashboardLoaded = ref(false)

onMounted(async () => {
  store.connectWs()
  await store.bootstrapDashboard()
  dashboardLoaded.value = true
})

onBeforeUnmount(() => {
  store.disconnectWs()
})
</script>

<template>
  <div class="app-shell">
    <SymbolSidebar />
    <main class="dash">
      <header class="dash-header">
        <div class="title-row">
          <h1>AlKarrar Pro</h1>
          <span v-if="store.credentialsConfigured" class="spot-badge">Spot</span>
          <span
            v-if="store.credentialsConfigured"
            class="env-badge"
            :class="store.binanceEnv || (store.exchangeTestnet ? 'testnet' : 'mainnet')"
          >
            {{ store.spotEnvLabelAr }} · {{ store.spotEnvLabel }}
          </span>
          <span v-if="store.activeGridSymbols.length" class="grid-live-badge">
            {{ store.activeGridSymbols.length === 1 ? "شبكة نشطة" : `${store.activeGridSymbols.length} شبكات` }}
          </span>
        </div>
        <div class="header-meta">
          <div class="conn" :class="{ ok: store.wsConnected, bad: !store.wsConnected }">
            WS {{ store.wsConnected ? "live" : "offline" }}
            <span v-if="store.lastWsAt && store.wsConnected" class="muted tiny">
              · {{ new Date(store.lastWsAt).toLocaleTimeString() }}
            </span>
          </div>
          <span v-if="store.credentialsConfigured" class="key-preview muted tiny">
            {{ store.binanceApiKeyPreview }}
          </span>
        </div>
      </header>

      <div
        v-if="store.syncError || !store.apiReachable || !store.credentialsConfigured"
        class="status-banner"
        role="alert"
      >
        <template v-if="!store.apiReachable">
          خادم API غير متاح — شغّل <code>.\scripts\run_api.ps1</code>
        </template>
        <template v-else-if="!store.credentialsConfigured">
          لا توجد مفاتيح — عيّن <code>BINANCE_API_KEY</code> و <code>BINANCE_ENV</code> في <code>.env</code> ثم أعد تشغيل API
        </template>
        <template v-else-if="store.syncError">
          <strong>خطأ مزامنة Binance:</strong> {{ store.syncError }}
          <span v-if="store.syncErrorHint" class="status-hint"> — {{ store.syncErrorHint }}</span>
        </template>
      </div>

      <div
        v-else-if="store.balanceSyncState === 'pending'"
        class="status-banner status-pending"
        role="status"
      >
        جاري مزامنة الرصيد الحي من Binance…
      </div>

      <EmergencyBar v-if="dashboardLoaded" />

      <section class="balance-hero panel">
        <div class="balance-hero-head">
          <div>
            <h2>إجمالي Equity</h2>
            <p class="balance-source">
              <span v-if="store.balanceIsLive" class="live-dot" aria-hidden="true" />
              Binance Spot · مباشر من API
              <span v-if="store.syncOkAt" class="sync-time">
                · {{ new Date(store.syncOkAt).toLocaleTimeString() }}
              </span>
            </p>
          </div>
          <span class="sym-pill">{{ store.symbol }}</span>
        </div>
        <div class="balance-main">
          <div class="balance-primary">
            <span class="balance-num">
              {{
                store.balanceIsLive
                  ? store.liveEquityUsdt.toLocaleString(undefined, {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 2,
                    })
                  : "—"
              }}
            </span>
            <span class="balance-unit">USDT</span>
          </div>
          <div class="balance-sub-row">
            <div class="balance-sub">
              <span class="sub-label">متاح</span>
              <span class="sub-val">{{
                store.balanceIsLive ? store.availableBalance.toFixed(2) : "—"
              }}</span>
            </div>
            <div class="balance-sub">
              <span class="sub-label">Mark</span>
              <span class="sub-val">{{
                store.markPrice > 0 ? store.markPrice.toFixed(6) : "—"
              }}</span>
            </div>
          </div>
          <p v-if="store.balanceSyncState === 'pending'" class="balance-warn">
            في انتظار مزامنة الرصيد الحي — لا يُعرض رصيد وهمي.
          </p>
          <p v-else-if="store.balanceSyncState === 'error'" class="balance-warn">
            تعذّرت المزامنة — الرقم أعلاه غير متاح حتى يعود الاتصال بـ Binance.
          </p>
        </div>
      </section>

      <CompoundingRiskPanel v-if="dashboardLoaded && store.credentialsConfigured" />

      <section class="chart-wrap panel">
        <h2 class="chart-title">الشموع — {{ store.symbol }} (15m)</h2>
        <ClientOnly>
          <TradingChart v-if="dashboardLoaded" :key="store.symbol" />
          <div v-else class="chart-fallback">جاري تحميل الإعدادات والرسم…</div>
          <template #fallback>
            <div class="chart-fallback">جاري تحميل الرسم…</div>
          </template>
        </ClientOnly>
      </section>

      <GridSettingsPanel v-if="dashboardLoaded" />

      <p
        v-if="dashboardLoaded && store.credentialsConfigured && !store.showLiveBotPanels"
        class="idle-hint muted"
      >
        لا شبكة نشطة ولا أوامر معلّقة — يمكنك تشغيل الشبكة من إعدادات Spot أدناه.
      </p>

      <ActiveGridCards v-if="dashboardLoaded && store.activeGridSymbols.length" />

      <div
        v-if="dashboardLoaded && store.tradesViewSymbol"
        id="grid-trades-journal"
      >
        <TradeJournal
          :symbol="store.tradesViewSymbol"
          :since="store.gridsBySymbol[store.tradesViewSymbol]?.startedAt"
          embedded
          @close="closeTradesJournal"
        />
      </div>

      <BotAssignedActivityPanel
        v-if="
          dashboardLoaded &&
          store.credentialsConfigured &&
          store.showLiveBotPanels &&
          !store.activeGridSymbols.length
        "
      />
    </main>
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  min-height: 100vh;
  align-items: stretch;
}
.dash {
  flex: 1;
  min-width: 0;
  max-width: 1200px;
  margin: 0 auto;
  padding: 1rem 1.25rem 2rem;
  display: flex;
  flex-direction: column;
  gap: 1rem;
}
@media (max-width: 960px) {
  .app-shell {
    flex-direction: column;
  }
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
.header-meta {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 0.2rem;
}
.key-preview {
  font-variant-numeric: tabular-nums;
}
.spot-badge {
  font-size: 0.7rem;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  padding: 0.28rem 0.6rem;
  border-radius: 6px;
  background: rgba(14, 203, 129, 0.15);
  color: #34d399;
  border: 1px solid rgba(14, 203, 129, 0.45);
}
.env-badge {
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  padding: 0.28rem 0.55rem;
  border-radius: 6px;
  border: 1px solid transparent;
}
.env-badge.demo {
  background: rgba(56, 189, 248, 0.14);
  color: #7dd3fc;
  border-color: rgba(56, 189, 248, 0.4);
}
.env-badge.testnet {
  background: rgba(245, 158, 11, 0.18);
  color: #fbbf24;
  border-color: rgba(245, 158, 11, 0.5);
}
.env-badge.mainnet {
  background: rgba(14, 203, 129, 0.12);
  color: #34d399;
  border-color: rgba(14, 203, 129, 0.45);
}
.grid-live-badge {
  font-size: 0.68rem;
  font-weight: 700;
  padding: 0.28rem 0.55rem;
  border-radius: 6px;
  background: rgba(14, 203, 129, 0.2);
  color: #0ecb81;
  animation: pulse-grid 1.8s ease-in-out infinite;
}
@keyframes pulse-grid {
  50% {
    opacity: 0.65;
  }
}
.pnl-up {
  color: #0ecb81;
}
.pnl-down {
  color: #f6465d;
}
.metric-sm {
  font-size: 1rem;
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
.status-banner {
  background: rgba(246, 70, 93, 0.12);
  border: 1px solid rgba(246, 70, 93, 0.45);
  border-radius: 8px;
  padding: 0.55rem 0.85rem;
  font-size: 0.82rem;
  color: #ffb4c0;
  line-height: 1.45;
}
.status-banner.status-pending {
  background: rgba(240, 185, 11, 0.1);
  border-color: rgba(240, 185, 11, 0.45);
  color: #f0d78c;
}
.status-banner code {
  font-size: 0.78rem;
}
.status-hint {
  color: #fde68a;
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
  margin-bottom: 0.75rem;
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
.balance-source {
  margin: 0.25rem 0 0;
  font-size: 0.72rem;
  color: #848e9c;
  display: flex;
  align-items: center;
  gap: 0.35rem;
  flex-wrap: wrap;
}
.live-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #0ecb81;
  box-shadow: 0 0 6px rgba(14, 203, 129, 0.6);
}
.sync-time {
  color: #5e6673;
}
.balance-main {
  background: #181a20;
  border: 1px solid #2b3139;
  border-radius: 8px;
  padding: 1rem 1.15rem;
}
.balance-primary {
  display: flex;
  align-items: baseline;
  gap: 0.4rem;
  margin-bottom: 0.85rem;
}
.balance-num {
  font-size: 2rem;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.02em;
  color: #eaecef;
}
.balance-unit {
  font-size: 0.85rem;
  font-weight: 600;
  color: #848e9c;
}
.balance-sub-row {
  display: flex;
  gap: 1.5rem;
  flex-wrap: wrap;
}
.balance-sub {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}
.sub-label {
  font-size: 0.68rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: #848e9c;
}
.sub-val {
  font-size: 0.95rem;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: #eaecef;
}
.balance-warn {
  margin: 0.75rem 0 0;
  font-size: 0.78rem;
  color: #f0b90b;
  line-height: 1.45;
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
.muted {
  color: var(--muted);
}
.active-grids-title {
  margin: 0 0 0.65rem;
  font-size: 0.95rem;
  font-weight: 600;
  color: #e2e8f0;
}
.active-grids-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
}
.active-grids-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.5rem 0.65rem;
  border-radius: 8px;
  background: rgba(34, 197, 94, 0.08);
  border: 1px solid rgba(34, 197, 94, 0.25);
}
.ag-sym {
  font-weight: 700;
  font-size: 0.85rem;
  letter-spacing: 0.03em;
}
.idle-hint {
  margin: 0;
  font-size: 0.8rem;
  line-height: 1.5;
  padding: 0.55rem 0.75rem;
  border-radius: 8px;
  background: rgba(43, 49, 57, 0.35);
  border: 1px solid rgba(43, 49, 57, 0.6);
}
</style>
