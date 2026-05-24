<script setup lang="ts">
import AuditLogPanel from "~/components/AuditLogPanel.vue"
import BotAssignedActivityPanel from "~/components/BotAssignedActivityPanel.vue"
import CompoundingRiskPanel from "~/components/CompoundingRiskPanel.vue"
import DashboardCommandBar from "~/components/DashboardCommandBar.vue"
import EmergencyBar from "~/components/EmergencyBar.vue"
import PortfolioStrip from "~/components/PortfolioStrip.vue"
import SymbolContextBar from "~/components/SymbolContextBar.vue"
import SuggestedSymbolsPanel from "~/components/SuggestedSymbolsPanel.vue"
import { useBotStore, type DashboardTabId } from "~/stores/bot"

const store = useBotStore()

const dashboardLoaded = ref(false)

const tabs: { id: DashboardTabId; label: string; hint: string; icon: string }[] = [
  { id: "watch", label: "مراقبة", hint: "شارت وحالة السوق", icon: "◉" },
  { id: "operate", label: "تشغيل", hint: "إعدادات الشبكة", icon: "⚙" },
  { id: "logs", label: "سجلات", hint: "شبكات وصفقات", icon: "▤" },
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
      <!-- ── Top bar ── -->
      <header class="dash-topbar panel">
        <div class="brand-block">
          <div class="brand-mark" aria-hidden="true">AK</div>
          <div>
            <h1 class="brand-name">AlKarrar Pro</h1>
            <p class="brand-tagline">Spot Grid · Binance</p>
          </div>
        </div>

        <div v-if="store.credentialsConfigured" class="topbar-badges">
          <span class="badge badge-spot">Spot</span>
          <span
            class="badge"
            :class="
              store.binanceEnv === 'demo'
                ? 'badge-demo'
                : store.binanceEnv === 'mainnet'
                  ? 'badge-mainnet'
                  : 'badge-testnet'
            "
          >
            {{ store.spotEnvLabelAr }}
          </span>
          <span v-if="store.activeGridSymbols.length" class="badge badge-live">
            {{ store.activeGridSymbols.length === 1 ? "شبكة نشطة" : `${store.activeGridSymbols.length} شبكات` }}
          </span>
        </div>
      </header>

      <!-- ── Alerts ── -->
      <div
        v-if="store.syncError || !store.apiReachable || !store.credentialsConfigured"
        class="status-banner"
        role="alert"
      >
        <template v-if="!store.apiReachable">
          خادم API غير متاح — شغّل <code>.\scripts\run_api.ps1</code>
        </template>
        <template v-else-if="!store.credentialsConfigured">
          لا توجد مفاتيح — عيّن <code>BINANCE_API_KEY</code> و <code>BINANCE_ENV</code> في <code>.env</code> ثم أعد
          تشغيل API
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

      <!-- ── Metrics strip ── -->
      <section v-if="dashboardLoaded" class="metrics-strip" aria-label="ملخص الحساب">
        <article class="metric-card metric-card--primary">
          <span class="metric-label">إجمالي USDT</span>
          <div class="metric-row">
            <span class="metric-value">
              {{
                store.balanceIsLive
                  ? store.liveEquityUsdt.toLocaleString(undefined, {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 2,
                    })
                  : "—"
              }}
            </span>
            <span class="metric-unit">USDT</span>
          </div>
          <p class="metric-foot">
            <span v-if="store.balanceIsLive" class="live-dot" aria-hidden="true" />
            Binance Spot · مباشر
            <span v-if="store.syncOkAt" class="sync-time">
              · {{ new Date(store.syncOkAt).toLocaleTimeString() }}
            </span>
          </p>
        </article>

        <article class="metric-card">
          <span class="metric-label">متاح للتداول</span>
          <span class="metric-value metric-value--sm">{{
            store.balanceIsLive ? store.availableBalance.toFixed(2) : "—"
          }}</span>
        </article>

        <article class="metric-card">
          <span class="metric-label">Mark · {{ store.symbol }}</span>
          <span class="metric-value metric-value--sm">{{
            store.markPrice > 0 ? store.markPrice.toFixed(6) : "—"
          }}</span>
        </article>

        <article class="metric-card metric-card--symbol">
          <span class="metric-label">الزوج النشط</span>
          <span class="sym-display">{{ store.symbol.replace("USDT", "") }}<small>/USDT</small></span>
        </article>
      </section>

      <PortfolioStrip
        v-if="dashboardLoaded && store.credentialsConfigured && store.activeGridSymbols.length"
      />

      <!-- ── Tabs ── -->
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
          <span class="tab-icon" aria-hidden="true">{{ t.icon }}</span>
          <span class="tab-body">
            <span class="tab-label">{{ t.label }}</span>
            <span class="tab-hint">{{ t.hint }}</span>
          </span>
        </button>
      </nav>

      <!-- ── Tab panels ── -->
      <div v-if="dashboardLoaded" class="dash-panels">
        <div
          v-show="store.dashboardTab === 'watch'"
          id="dash-tab-watch"
          class="tab-panel"
          role="tabpanel"
        >
          <SymbolContextBar v-if="store.credentialsConfigured" />
          <div class="watch-workspace">
            <aside
              v-if="store.credentialsConfigured"
              class="watch-symbols panel"
              aria-label="اختيار من العملات المقترحة"
            >
              <header class="panel-header">
                <div>
                  <h2 class="panel-title">عملات مقترحة</h2>
                  <p class="panel-subtitle">فحص تلقائي متوافق مع الشبكة</p>
                </div>
              </header>
              <SuggestedSymbolsPanel layout="column" />
            </aside>

            <section id="trading-chart-panel" class="watch-chart panel">
              <header class="watch-chart-head">
                <div>
                  <h2 class="panel-title">الشموع وخطوط الشبكة</h2>
                  <p class="panel-subtitle">
                    {{ store.symbol }} · 15m
                    <template v-if="store.generatorUpper > store.generatorLower">
                      · {{ store.generatorLower.toFixed(6) }} – {{ store.generatorUpper.toFixed(6) }}
                      · {{ store.generatorCount }} خط
                    </template>
                  </p>
                </div>
              </header>
              <ClientOnly>
                <TradingChart :key="store.symbol" />
                <template #fallback>
                  <div class="chart-fallback">جاري تحميل الرسم…</div>
                </template>
              </ClientOnly>
            </section>
          </div>
        </div>

        <div
          v-show="store.dashboardTab === 'operate'"
          id="dash-tab-operate"
          class="tab-panel tab-panel--operate"
          role="tabpanel"
        >
          <header class="tab-section-intro">
            <h2>تشغيل الشبكة</h2>
            <p>ضبط النطاق، تخصيص رأس المال، وتشغيل أو إيقاف الشبكة على {{ store.symbol }}</p>
          </header>
          <p
            v-if="store.credentialsConfigured && !store.showLiveBotPanels"
            class="idle-hint"
          >
            لا شبكة نشطة — عيّن النطاق والرأسمال ثم شغّل الشبكة من هنا.
          </p>
          <CompoundingRiskPanel v-if="store.credentialsConfigured" />
          <GridSettingsPanel />
        </div>

        <div
          v-show="store.dashboardTab === 'logs'"
          id="dash-tab-logs"
          class="tab-panel tab-panel--logs"
          role="tabpanel"
        >
          <header class="tab-section-intro">
            <h2>سجلات التداول</h2>
            <p>الشبكات النشطة، سجل الصفقات، وعمليات المحرك على Binance Spot</p>
          </header>
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
        <div class="loading-spinner" aria-hidden="true" />
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
  max-width: 1360px;
  margin: 0 auto;
  padding: 1.1rem 1.35rem 2.75rem;
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
}

@media (max-width: 960px) {
  .app-shell {
    flex-direction: column;
  }
  .dash {
    padding: 0.85rem 1rem 2rem;
  }
}

/* ── Top bar ── */
.dash-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
  padding: 0.85rem 1.15rem;
}

.brand-block {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.brand-mark {
  width: 2.5rem;
  height: 2.5rem;
  border-radius: 10px;
  display: grid;
  place-items: center;
  font-size: 0.72rem;
  font-weight: 800;
  letter-spacing: 0.04em;
  color: #fff;
  background: linear-gradient(135deg, #0ecb81 0%, #059669 100%);
  box-shadow: 0 4px 14px rgba(14, 203, 129, 0.28);
}

.brand-name {
  margin: 0;
  font-size: 1.15rem;
  font-weight: 800;
  letter-spacing: -0.03em;
  line-height: 1.2;
}

.brand-tagline {
  margin: 0.1rem 0 0;
  font-size: 0.68rem;
  font-weight: 500;
  color: var(--muted);
  letter-spacing: 0.02em;
}

.topbar-badges {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  flex-wrap: wrap;
}

/* ── Alerts ── */
.status-banner {
  background: var(--danger-dim);
  border: 1px solid rgba(246, 70, 93, 0.35);
  border-radius: var(--radius-md);
  padding: 0.65rem 0.95rem;
  font-size: 0.82rem;
  color: #fecdd3;
  line-height: 1.5;
}

.status-banner.status-pending {
  background: var(--warn-dim);
  border-color: rgba(240, 185, 11, 0.35);
  color: #fde68a;
}

.status-hint {
  color: #fde68a;
}

/* ── Metrics ── */
.metrics-strip {
  display: grid;
  grid-template-columns: 1.6fr repeat(3, 1fr);
  gap: 0.65rem;
}

@media (max-width: 900px) {
  .metrics-strip {
    grid-template-columns: repeat(2, 1fr);
  }
  .metric-card--primary {
    grid-column: 1 / -1;
  }
}

@media (max-width: 520px) {
  .metrics-strip {
    grid-template-columns: 1fr;
  }
}

.metric-card {
  padding: 0.85rem 1rem;
  border-radius: var(--radius-md);
  background: linear-gradient(180deg, rgba(19, 26, 36, 0.95), rgba(12, 16, 23, 0.98));
  border: 1px solid var(--border);
  box-shadow: var(--shadow-sm);
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.metric-card--primary {
  border-color: rgba(14, 203, 129, 0.22);
  box-shadow: var(--shadow-glow-accent);
}

.metric-card--symbol {
  align-items: flex-start;
  justify-content: center;
}

.metric-row {
  display: flex;
  align-items: baseline;
  gap: 0.35rem;
}

.metric-value {
  font-size: 1.65rem;
  font-weight: 800;
  letter-spacing: -0.03em;
  font-variant-numeric: tabular-nums;
  color: var(--text);
  line-height: 1.1;
}

.metric-value--sm {
  font-size: 1.15rem;
  font-weight: 700;
}

.metric-unit {
  font-size: 0.78rem;
  font-weight: 600;
  color: var(--muted);
}

.metric-foot {
  margin: 0.35rem 0 0;
  font-size: 0.68rem;
  color: var(--muted);
  display: flex;
  align-items: center;
  gap: 0.35rem;
  flex-wrap: wrap;
}

.live-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 8px rgba(14, 203, 129, 0.65);
  animation: live-blink 2s ease-in-out infinite;
}

@keyframes live-blink {
  50% {
    opacity: 0.45;
  }
}

.sync-time {
  color: var(--muted);
}

.sym-display {
  font-size: 1.1rem;
  font-weight: 800;
  letter-spacing: 0.02em;
  color: var(--info);
}

.sym-display small {
  font-size: 0.72rem;
  font-weight: 600;
  color: var(--muted);
}

/* ── Tabs ── */
.dash-tabs {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 0.35rem;
  padding: 0.3rem;
  border-radius: var(--radius-md);
  background: rgba(7, 10, 15, 0.75);
  border: 1px solid var(--border);
}

.dash-tab {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  padding: 0.65rem 0.75rem;
  border: 1px solid transparent;
  border-radius: 10px;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  transition:
    background var(--transition),
    color var(--transition),
    border-color var(--transition),
    box-shadow var(--transition);
  text-align: start;
  font-family: inherit;
}

.dash-tab:hover {
  background: rgba(56, 189, 248, 0.06);
  color: var(--text-secondary);
}

.dash-tab.active {
  background: linear-gradient(180deg, rgba(56, 189, 248, 0.14), rgba(56, 189, 248, 0.04));
  border-color: rgba(56, 189, 248, 0.35);
  color: var(--text);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04);
}

.tab-icon {
  font-size: 0.85rem;
  opacity: 0.75;
  flex-shrink: 0;
}

.dash-tab.active .tab-icon {
  opacity: 1;
  color: var(--info);
}

.tab-body {
  display: flex;
  flex-direction: column;
  gap: 0.05rem;
  min-width: 0;
}

.tab-label {
  font-size: 0.86rem;
  font-weight: 700;
  letter-spacing: -0.01em;
}

.tab-hint {
  font-size: 0.62rem;
  font-weight: 500;
  opacity: 0.72;
}

/* ── Panels ── */
.dash-panels {
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
  animation: panel-in 0.28s ease;
}

@keyframes panel-in {
  from {
    opacity: 0;
    transform: translateY(6px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.tab-panel {
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
}

.watch-workspace {
  display: grid;
  grid-template-columns: minmax(240px, 280px) minmax(0, 1fr);
  gap: 0.75rem;
  align-items: stretch;
  min-height: 520px;
}

.watch-symbols {
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding-top: 0.85rem;
  overflow: hidden;
}

.watch-symbols .panel-header {
  margin-bottom: 0.5rem;
  padding-inline: 0.1rem;
}

.watch-chart {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 480px;
}

.watch-chart-head {
  margin-bottom: 0.65rem;
}

@media (max-width: 960px) {
  .watch-workspace {
    grid-template-columns: 1fr;
    min-height: auto;
  }
  .watch-symbols {
    max-height: 42vh;
    overflow-y: auto;
  }
  .watch-chart {
    min-height: 420px;
  }
}

.chart-fallback {
  height: 420px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--muted);
  font-size: 0.88rem;
}

.dash-loading {
  min-height: 220px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.75rem;
}

.loading-spinner {
  width: 1.75rem;
  height: 1.75rem;
  border: 2px solid var(--border-strong);
  border-top-color: var(--info);
  border-radius: 50%;
  animation: spin 0.75s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.tab-panel--operate,
.tab-panel--logs {
  gap: 0.85rem;
}

#grid-trades-journal :deep(.trade-journal) {
  margin-top: 0.15rem;
}

.idle-hint {
  margin: 0;
  font-size: 0.8rem;
  line-height: 1.55;
  padding: 0.65rem 0.85rem;
  border-radius: var(--radius-sm);
  background: rgba(56, 189, 248, 0.05);
  border: 1px solid rgba(56, 189, 248, 0.15);
  color: var(--text-secondary);
}
</style>
