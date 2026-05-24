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
  padding: 0.7rem 1rem;
  background: linear-gradient(180deg, rgba(19, 26, 36, 0.95), rgba(15, 19, 24, 0.98));
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
}
.trailing-pill {
  display: flex;
  align-items: center;
  gap: 0.65rem;
  padding: 0.5rem 0.8rem;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: rgba(7, 10, 15, 0.55);
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
  border-radius: var(--radius-sm);
  padding: 0.68rem 1.4rem;
  font-size: 0.86rem;
  font-weight: 700;
  font-family: inherit;
  cursor: pointer;
  color: #fff;
  background: linear-gradient(180deg, #ff5569 0%, var(--danger) 100%);
  box-shadow: 0 2px 12px rgba(246, 70, 93, 0.28);
  transition:
    filter var(--transition),
    transform 0.12s ease;
}
.btn-emergency:hover:not(:disabled) {
  filter: brightness(1.08);
}
.btn-emergency:active:not(:disabled) {
  transform: scale(0.98);
}
.btn-emergency:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
