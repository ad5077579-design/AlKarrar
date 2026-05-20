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
      <span v-if="store.balanceIsLive" class="cmd-chip ok muted-weight">
        رصيد متزامن
      </span>
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
  padding: 0.55rem 0.75rem;
  border-radius: 10px;
  background: linear-gradient(180deg, rgba(18, 22, 28, 0.95), rgba(15, 19, 24, 0.9));
  border: 1px solid var(--border);
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
  gap: 0.3rem;
  font-size: 0.7rem;
  font-weight: 700;
  padding: 0.22rem 0.5rem;
  border-radius: 6px;
  border: 1px solid rgba(30, 38, 48, 0.9);
  background: rgba(15, 19, 24, 0.8);
  color: #94a3b8;
}
.cmd-chip.env.demo {
  color: #7dd3fc;
  border-color: rgba(56, 189, 248, 0.35);
}
.cmd-chip.env.testnet {
  color: #fbbf24;
  border-color: rgba(245, 158, 11, 0.4);
}
.cmd-chip.env.mainnet {
  color: #34d399;
  border-color: rgba(14, 203, 129, 0.4);
}
.cmd-chip.ok {
  color: #34d399;
}
.cmd-chip.bad {
  color: #fbbf24;
}
.cmd-chip .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}
.cmd-symbol {
  font-size: 0.88rem;
  font-weight: 800;
  letter-spacing: 0.04em;
  color: #f1f5f9;
}
.cmd-grids {
  font-size: 0.72rem;
  font-weight: 600;
  color: #0ecb81;
  padding: 0.15rem 0.45rem;
  border-radius: 999px;
  background: rgba(14, 203, 129, 0.12);
}
.cmd-mark {
  font-size: 0.72rem;
  color: #94a3b8;
}
.cmd-mark strong {
  color: #38bdf8;
  font-variant-numeric: tabular-nums;
}
.cmd-key {
  font-size: 0.65rem;
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
