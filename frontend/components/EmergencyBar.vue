<script setup lang="ts">
import { computed, ref } from "vue"
import { useBotStore } from "~/stores/bot"

const store = useBotStore()
const stopping = ref(false)

const trailingLabel = computed(() => {
  if (!store.trailingEquityStopEnabled) return "غير مفعّل"
  if (store.trailingEquityStopTriggered) return "مُفعّل — تم التدخل"
  return `مفعّل · حد ${store.trailingEquityDrawdownLimitPct.toFixed(0)}%`
})

const trailingClass = computed(() => {
  if (!store.trailingEquityStopEnabled) return "off"
  if (store.trailingEquityStopTriggered) return "triggered"
  return "on"
})

async function onEmergency() {
  if (
    !confirm(
      "إيقاف طوارئ: إيقاف كل الشبكات، إلغاء الأوامر المعلّقة، وبيع عملة الأساس بالسوق. متابعة؟",
    )
  ) {
    return
  }
  stopping.value = true
  try {
    await store.stopGridAndFlattenSpot()
  } catch (e) {
    alert(String(e))
  } finally {
    stopping.value = false
  }
}
</script>

<template>
  <section class="emergency-bar" aria-label="طوارئ ومخاطر">
    <div class="trailing-pill" :class="trailingClass">
      <span class="trail-dot" aria-hidden="true" />
      <span class="trail-text">
        <span class="trail-title">Trailing Equity Stop</span>
        <span class="trail-state">{{ trailingLabel }}</span>
      </span>
    </div>

    <button
      type="button"
      class="btn-emergency"
      :disabled="stopping || !store.credentialsConfigured"
      @click="onEmergency"
    >
      {{ stopping ? "جاري الإيقاف…" : "إيقاف طوارئ" }}
    </button>
  </section>
</template>

<style scoped>
.emergency-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
  padding: 0.65rem 1rem;
  background: #181a20;
  border: 1px solid #2b3139;
  border-radius: 8px;
}
.trailing-pill {
  display: flex;
  align-items: center;
  gap: 0.65rem;
  padding: 0.45rem 0.75rem;
  border-radius: 6px;
  border: 1px solid #2b3139;
  background: #1e2329;
  min-width: 0;
  flex: 1;
}
.trail-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  background: #848e9c;
}
.trailing-pill.on .trail-dot {
  background: #0ecb81;
  box-shadow: 0 0 8px rgba(14, 203, 129, 0.55);
}
.trailing-pill.off .trail-dot {
  background: #848e9c;
}
.trailing-pill.triggered .trail-dot {
  background: #f6465d;
  box-shadow: 0 0 8px rgba(246, 70, 93, 0.55);
}
.trail-text {
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  min-width: 0;
}
.trail-title {
  font-size: 0.68rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #848e9c;
}
.trail-state {
  font-size: 0.82rem;
  font-weight: 600;
  color: #eaecef;
}
.trailing-pill.on .trail-state {
  color: #0ecb81;
}
.trailing-pill.triggered .trail-state {
  color: #f6465d;
}
.btn-emergency {
  flex-shrink: 0;
  border: none;
  border-radius: 6px;
  padding: 0.65rem 1.35rem;
  font-size: 0.9rem;
  font-weight: 700;
  cursor: pointer;
  color: #fff;
  background: #f6465d;
  transition: background 0.15s ease, transform 0.1s ease;
}
.btn-emergency:hover:not(:disabled) {
  background: #ff707e;
}
.btn-emergency:active:not(:disabled) {
  transform: scale(0.98);
}
.btn-emergency:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
