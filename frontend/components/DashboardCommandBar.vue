<script setup lang="ts">
import { useBotStore } from "~/stores/bot"

const store = useBotStore()
</script>

<template>
  <div v-if="store.credentialsConfigured" class="command-bar" role="region" aria-label="شريط الحالة">
    <div class="cmd-group">
      <span class="cmd-chip env" :class="store.binanceEnv || 'testnet'">
        {{ store.spotEnvLabelAr }}
      </span>
      <span class="cmd-chip" :class="store.wsConnected ? 'ok' : 'bad'">
        <span class="dot" aria-hidden="true" />
        {{ store.wsConnected ? "بث حي" : "WS غير متصل" }}
      </span>
      <span v-if="store.balanceIsLive" class="cmd-chip ok muted-weight">رصيد متزامن</span>
    </div>
    <div class="cmd-group cmd-center">
      <span class="cmd-symbol">{{ store.symbol }}</span>
      <span v-if="store.activeGridSymbols.length" class="cmd-grids">
        {{ store.activeGridSymbols.length }} شبكة نشطة
      </span>
    </div>
    <div class="cmd-group cmd-end">
      <span v-if="store.markPrice > 0" class="cmd-mark">
        Mark <strong>{{ store.markPrice.toFixed(6) }}</strong>
      </span>
      <span class="cmd-key muted">{{ store.binanceApiKeyPreview }}</span>
    </div>
  </div>
</template>

<style scoped>
.command-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  flex-wrap: wrap;
  padding: 0.55rem 0.85rem;
  border-radius: var(--radius-md);
  background: rgba(12, 16, 23, 0.85);
  border: 1px solid var(--border);
  backdrop-filter: blur(8px);
}

.cmd-group {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex-wrap: wrap;
}

.cmd-center {
  flex: 1;
  justify-content: center;
  min-width: 8rem;
}

.cmd-end {
  justify-content: flex-end;
}

.cmd-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.32rem;
  font-size: 0.68rem;
  font-weight: 700;
  padding: 0.24rem 0.52rem;
  border-radius: var(--radius-pill);
  border: 1px solid var(--border);
  background: rgba(7, 10, 15, 0.55);
  color: var(--muted);
}

.cmd-chip.env.demo {
  color: #7dd3fc;
  border-color: var(--info-border);
  background: var(--info-dim);
}

.cmd-chip.env.testnet {
  color: #fbbf24;
  border-color: rgba(245, 158, 11, 0.35);
  background: var(--warn-dim);
}

.cmd-chip.env.mainnet {
  color: #34d399;
  border-color: var(--accent-border);
  background: var(--accent-dim);
}

.cmd-chip.ok {
  color: #34d399;
  border-color: rgba(14, 203, 129, 0.25);
}

.cmd-chip.bad {
  color: #fbbf24;
  border-color: rgba(245, 158, 11, 0.3);
}

.cmd-chip .dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
  box-shadow: 0 0 6px currentColor;
}

.cmd-symbol {
  font-size: 0.86rem;
  font-weight: 800;
  letter-spacing: 0.03em;
  color: var(--text);
}

.cmd-grids {
  font-size: 0.68rem;
  font-weight: 700;
  color: var(--accent);
  padding: 0.14rem 0.48rem;
  border-radius: var(--radius-pill);
  background: var(--accent-dim);
  border: 1px solid var(--accent-border);
}

.cmd-mark {
  font-size: 0.7rem;
  color: var(--muted);
}

.cmd-mark strong {
  color: var(--info);
  font-variant-numeric: tabular-nums;
  font-weight: 700;
}

.cmd-key {
  font-size: 0.62rem;
  font-variant-numeric: tabular-nums;
}

.muted-weight {
  font-weight: 600;
}

@media (max-width: 720px) {
  .cmd-center {
    order: 3;
    width: 100%;
    justify-content: flex-start;
  }
}
</style>
