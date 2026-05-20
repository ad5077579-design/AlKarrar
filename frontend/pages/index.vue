<script setup lang="ts">
import AuditLogPanel from "~/components/AuditLogPanel.vue"
import BotAssignedActivityPanel from "~/components/BotAssignedActivityPanel.vue"
import CompoundingRiskPanel from "~/components/CompoundingRiskPanel.vue"
import DashboardCommandBar from "~/components/DashboardCommandBar.vue"
import EmergencyBar from "~/components/EmergencyBar.vue"
import PortfolioStrip from "~/components/PortfolioStrip.vue"
import SymbolContextBar from "~/components/SymbolContextBar.vue"
import { useBotStore, type DashboardTabId } from "~/stores/bot"

const store = useBotStore()

const dashboardLoaded = ref(false)

const tabs: { id: DashboardTabId; label: string; hint: string }[] = [
  { id: "watch", label: "مراقبة", hint: "شارت وحالة السوق" },
  { id: "operate", label: "تشغيل", hint: "إعدادات الشبكة" },
  { id: "logs", label: "سجلات", hint: "شبكات وصفقات" },
]

function closeTradesJournal() {
  store.tradesViewSymbol = null
}

function setTab(id: DashboardTabId) {
  store.setDashboardTab(id)
}

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

      <DashboardCommandBar v-if="dashboardLoaded && store.credentialsConfigured" />

      <section v-if="dashboardLoaded" class="balance-hero panel">
        <div class="balance-hero-head">
          <div>
            <h2>إجمالي Equity</h2>
            <p class="balance-source">
              <span v-if="store.balanceIsLive" class="live-dot" aria-hidden="true" />
              Binance Spot · مباشر
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
        </div>
      </section>

      <PortfolioStrip v-if="dashboardLoaded && store.credentialsConfigured" />

      <nav v-if="dashboardLoaded" class="dash-tabs" role="tablist" aria-label="أقسام اللوحة">
        <button
          v-for="t in tabs"
          :key="t.id"
          type="button"
          role="tab"
          class="dash-tab"
          :class="{ active: store.dashboardTab === t.id }"
          :aria-selected="store.dashboardTab === t.id"
          @click="setTab(t.id)"
        >
          <span class="tab-label">{{ t.label }}</span>
          <span class="tab-hint">{{ t.hint }}</span>
        </button>
      </nav>

      <div v-if="dashboardLoaded" class="dash-panels">
        <!-- مراقبة -->
        <div
          v-show="store.dashboardTab === 'watch'"
          id="dash-tab-watch"
          class="tab-panel"
          role="tabpanel"
        >
          <SymbolContextBar v-if="store.credentialsConfigured" />
          <section id="trading-chart-panel" class="chart-wrap panel">
            <h2 class="section-title">الشموع — {{ store.symbol }} (15m)</h2>
            <ClientOnly>
              <TradingChart :key="store.symbol" />
              <template #fallback>
                <div class="chart-fallback">جاري تحميل الرسم…</div>
              </template>
            </ClientOnly>
          </section>
        </div>

        <!-- تشغيل -->
        <div
          v-show="store.dashboardTab === 'operate'"
          id="dash-tab-operate"
          class="tab-panel"
          role="tabpanel"
        >
          <p
            v-if="store.credentialsConfigured && !store.showLiveBotPanels"
            class="idle-hint muted"
          >
            لا شبكة نشطة — عيّن النطاق والرأسمال ثم شغّل الشبكة من هنا.
          </p>
          <CompoundingRiskPanel v-if="store.credentialsConfigured" />
          <GridSettingsPanel />
        </div>

        <!-- سجلات -->
        <div
          v-show="store.dashboardTab === 'logs'"
          id="dash-tab-logs"
          class="tab-panel"
          role="tabpanel"
        >
          <ActiveGridCards v-if="store.activeGridSymbols.length" />

          <div v-if="store.tradesViewSymbol" id="grid-trades-journal">
            <TradeJournal
              :symbol="store.tradesViewSymbol"
              :since="store.gridsBySymbol[store.tradesViewSymbol]?.startedAt"
              embedded
              @close="closeTradesJournal"
            />
          </div>

          <AuditLogPanel v-if="store.credentialsConfigured" />

          <BotAssignedActivityPanel
            v-if="store.showLiveBotPanels && !store.activeGridSymbols.length"
          />
        </div>
      </div>

      <div v-else class="dash-loading panel">
        <p class="muted">جاري تحميل اللوحة…</p>
      </div>
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
  max-width: 1280px;
  margin: 0 auto;
  padding: 1rem 1.25rem 2.5rem;
  display: flex;
  flex-direction: column;
  gap: 0.85rem;
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
}
.title-row {
  display: flex;
  align-items: center;
  gap: 0.65rem;
  flex-wrap: wrap;
}
.dash-header h1 {
  margin: 0;
  font-size: 1.35rem;
  font-weight: 800;
  letter-spacing: -0.02em;
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
  margin-bottom: 0.65rem;
}
.sym-pill {
  font-size: 0.75rem;
  font-weight: 700;
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
  padding: 0.85rem 1rem;
}
.balance-primary {
  display: flex;
  align-items: baseline;
  gap: 0.4rem;
  margin-bottom: 0.65rem;
}
.balance-num {
  font-size: 1.85rem;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
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
  gap: 0.12rem;
}
.sub-label {
  font-size: 0.68rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: #848e9c;
}
.sub-val {
  font-size: 0.92rem;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: #eaecef;
}
.dash-tabs {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 0.4rem;
  padding: 0.35rem;
  border-radius: 12px;
  background: #0f1318;
  border: 1px solid var(--border);
}
.dash-tab {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.1rem;
  padding: 0.55rem 0.5rem;
  border: 1px solid transparent;
  border-radius: 9px;
  background: transparent;
  color: #94a3b8;
  cursor: pointer;
  transition:
    background 0.2s ease,
    color 0.2s ease,
    border-color 0.2s ease;
}
.dash-tab:hover {
  background: rgba(56, 189, 248, 0.08);
  color: #e2e8f0;
}
.dash-tab.active {
  background: linear-gradient(180deg, rgba(56, 189, 248, 0.18), rgba(56, 189, 248, 0.06));
  border-color: rgba(56, 189, 248, 0.45);
  color: #f1f5f9;
}
.tab-label {
  font-size: 0.88rem;
  font-weight: 700;
}
.tab-hint {
  font-size: 0.62rem;
  font-weight: 500;
  opacity: 0.75;
}
.dash-panels {
  display: flex;
  flex-direction: column;
  gap: 0.85rem;
  animation: panel-in 0.25s ease;
}
@keyframes panel-in {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
.tab-panel {
  display: flex;
  flex-direction: column;
  gap: 0.85rem;
}
.section-title {
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
.dash-loading {
  min-height: 200px;
  display: flex;
  align-items: center;
  justify-content: center;
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
.muted {
  color: var(--muted);
}
</style>
