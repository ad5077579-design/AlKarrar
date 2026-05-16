<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue"
import { useBotStore } from "~/stores/bot"

const store = useBotStore()
const saving = ref(false)
const killing = ref(false)
const gridStarting = ref(false)
const showLevels = ref(false)
const showAdvanced = ref(false)

const form = reactive({
  generatorUpper: 0,
  generatorLower: 0,
  generatorCount: 5,
  maxGeneratorCount: 9999,
  initialCapital: 100,
  trailingOffset: 0.0003,
  trailing_stop_pct: 0.01,
  compoundingFactor: 0.05,
  profit_injection_mode: "expand_count" as "expand_count" | "compound_size",
  max_slippage_pct: 0.008,
  dca_mode: "equal" as "equal" | "log",
})

watch(
  () => ({
    u: store.generatorUpper,
    l: store.generatorLower,
    c: store.generatorCount,
    m: store.maxGeneratorCount,
    i: store.initialCapital,
  }),
  (v) => {
    form.generatorUpper = v.u
    form.generatorLower = v.l
    form.generatorCount = v.c
    form.maxGeneratorCount = v.m
    form.initialCapital = v.i
  },
  { immediate: true },
)

const mark = computed(() => store.markPrice)
const gridCount = computed(() => Math.max(2, Math.floor(form.generatorCount) || 2))
const rangeValid = computed(
  () => form.generatorUpper > form.generatorLower && form.generatorLower > 0,
)

const gridStep = computed(() => {
  if (!rangeValid.value) return 0
  return (form.generatorUpper - form.generatorLower) / (gridCount.value - 1)
})

const rangePct = computed(() => {
  if (!rangeValid.value) return 0
  return ((form.generatorUpper - form.generatorLower) / form.generatorLower) * 100
})

const markInRange = computed(
  () => rangeValid.value && mark.value >= form.generatorLower && mark.value <= form.generatorUpper,
)

const markPositionPct = computed(() => {
  if (!rangeValid.value || !(mark.value > 0)) return 50
  const span = form.generatorUpper - form.generatorLower
  if (span <= 0) return 50
  const p = ((mark.value - form.generatorLower) / span) * 100
  return Math.min(100, Math.max(0, p))
})

const investmentPerGrid = computed(() => {
  if (!rangeValid.value || form.initialCapital <= 0) return 0
  return form.initialCapital / gridCount.value
})

/** المنطق الهجين على الخادم يتطلّب أن يكون سقف الخطوط أكبر من عدد خطوط النطاق الحالي */
const hybridMaxLinesValid = computed(() => {
  const gc = Math.max(2, Math.floor(Number(form.generatorCount)) || 2)
  const mx = Math.floor(Number(form.maxGeneratorCount))
  if (!Number.isFinite(mx) || mx < 2) return false
  return mx > gc
})

function buildPreviewLevels(lo: number, hi: number, n: number, mode: "equal" | "log"): number[] {
  if (n < 2) return [lo, hi]
  if (mode === "equal") {
    const step = (hi - lo) / (n - 1)
    return Array.from({ length: n }, (_, i) => lo + i * step)
  }
  const weights = Array.from({ length: n }, (_, i) => Math.exp(i / (n - 1)))
  const sum = weights.reduce((a, b) => a + b, 0)
  let acc = 0
  return weights.map((w) => {
    acc += w
    return lo + (hi - lo) * (acc / sum)
  })
}

const previewLevels = computed(() => {
  if (!rangeValid.value) return []
  return buildPreviewLevels(
    form.generatorLower,
    form.generatorUpper,
    gridCount.value,
    form.dca_mode,
  )
})

function formatPrice(n: number): string {
  if (!(n > 0)) return "—"
  if (n >= 100) return n.toFixed(2)
  if (n >= 1) return n.toFixed(4)
  return n.toFixed(6)
}

async function onSave() {
  if (!hybridMaxLinesValid.value) {
    alert(
      "يجب أن يكون «الحد الأقصى للخطوط» أكبر من «عدد الشبكات» (وليس مساوياً أو أصغر) حتى لا يُكسر المسار الهجين.",
    )
    return
  }
  saving.value = true
  try {
    await store.saveSettings({
      generatorUpper: form.generatorUpper,
      generatorLower: form.generatorLower,
      generatorCount: form.generatorCount,
      maxGeneratorCount: form.maxGeneratorCount,
      initialCapital: form.initialCapital,
      trailingOffset: form.trailingOffset,
      trailing_stop_pct: form.trailing_stop_pct,
      compoundingFactor: form.compoundingFactor,
      profit_injection_mode: form.profit_injection_mode,
      max_slippage_pct: form.max_slippage_pct,
      dca_mode: form.dca_mode,
    })
  } finally {
    saving.value = false
  }
}

async function onStartGrid() {
  if (!store.credentialsConfigured) {
    alert("أضف مفاتيح Binance في .env أولاً")
    return
  }
  if (!hybridMaxLinesValid.value) {
    alert(
      "أصلح إعداد الخطوط: الحد الأقصى للخطوط يجب أن يكون أكبر من عدد الشبكات قبل التشغيل.",
    )
    return
  }
  const envLabel = `Spot · ${store.spotEnvLabel}`
  if (
    !confirm(
      `تشغيل شبكة ${store.symbol} على ${envLabel}؟\n` +
        `سيتُستخدم النطاق اليدوي: ${form.generatorLower} – ${form.generatorUpper} (${gridCount.value} خطوط).\n` +
        "شبكة افتراضية — التنفيذ عند تقاطع Mark (LIMIT+IOC).",
    )
  ) {
    return
  }
  gridStarting.value = true
  try {
    await store.startGrid({
      calibrate: false,
      generatorUpper: form.generatorUpper,
      generatorLower: form.generatorLower,
      generatorCount: Math.max(2, Math.floor(form.generatorCount)),
      maxGeneratorCount: Math.floor(form.maxGeneratorCount),
      initialCapital: form.initialCapital,
      trailingOffset: form.trailingOffset,
      trailing_stop_pct: form.trailing_stop_pct,
      compoundingFactor: form.compoundingFactor,
      profit_injection_mode: form.profit_injection_mode,
      max_slippage_pct: form.max_slippage_pct,
      dca_mode: form.dca_mode,
    })
  } catch (e) {
    alert(String(e))
  } finally {
    gridStarting.value = false
  }
}

async function onStopGrid() {
  const sym = store.symbol.trim().toUpperCase()
  if (!confirm(`إيقاف شبكة ${sym} فقط (تبقى الشبكات على الأزواج الأخرى إن وُجدت)؟`)) return
  await store.stopGrid(sym)
}

const gridActiveHere = computed(() => store.isGridActiveForSelectedSymbol)
const otherActiveGrids = computed(() => store.otherActiveGridSymbols)
const selectedGridOrders = computed(
  () => store.gridsBySymbol[store.symbol.trim().toUpperCase()]?.ordersPlaced ?? 0,
)
const selectedGridError = computed(() => {
  const e = store.gridsBySymbol[store.symbol.trim().toUpperCase()]?.lastError
  return typeof e === "string" && e.trim() ? e.trim() : ""
})

onMounted(() => {
  void store.fetchGridStatus()
})

async function onKill() {
  const sym = store.symbol.trim().toUpperCase()
  if (
    !confirm(
      `تأكيد: إيقاف طوارئ لـ ${sym} — إلغاء أوامر هذا الزوج وبيع عملة الأساس بالسوق؟`,
    )
  ) {
    return
  }
  killing.value = true
  try {
    await store.stopGrid(sym)
    await store.emergencyStop()
  } finally {
    killing.value = false
  }
}
</script>

<template>
  <section class="grid-panel" aria-label="إعدادات شبكة التداول">
    <header class="grid-panel-head">
      <div class="head-left">
        <h2 class="grid-panel-title">شبكة Spot</h2>
        <span class="symbol-chip">{{ store.symbol }}</span>
        <span v-if="store.credentialsConfigured" class="mode-chip" :class="store.binanceEnv || 'testnet'">
          Spot · {{ store.spotEnvLabel }}
        </span>
      </div>
    </header>

    <div class="grid-panel-body">
      <form class="grid-form" @submit.prevent="onSave">
        <div class="form-block">
          <h3 class="block-title">نطاق السعر</h3>
          <div class="field-row">
            <label class="field-label" for="grid-upper">
              <span>السعر الأعلى</span>
              <span class="field-hint">Upper Price</span>
            </label>
            <div class="field-input-wrap">
              <input
                id="grid-upper"
                v-model.number="form.generatorUpper"
                class="bn-input"
                type="number"
                step="any"
                min="0"
                required
              />
              <span class="field-suffix">USDT</span>
            </div>
          </div>
          <div class="field-row">
            <label class="field-label" for="grid-lower">
              <span>السعر الأدنى</span>
              <span class="field-hint">Lower Price</span>
            </label>
            <div class="field-input-wrap">
              <input
                id="grid-lower"
                v-model.number="form.generatorLower"
                class="bn-input"
                type="number"
                step="any"
                min="0"
                required
              />
              <span class="field-suffix">USDT</span>
            </div>
          </div>
        </div>

        <div class="form-block">
          <h3 class="block-title">إعدادات الشبكة</h3>
          <div class="field-row">
            <label class="field-label" for="grid-count">
              <span>عدد الشبكات</span>
              <span class="field-hint">Grid Count</span>
            </label>
            <div class="field-input-wrap count-wrap">
              <input
                id="grid-count"
                v-model.number="form.generatorCount"
                class="bn-input"
                type="number"
                min="2"
                max="200"
                required
              />
              <input
                v-model.number="form.generatorCount"
                class="count-slider"
                type="range"
                min="2"
                max="50"
              />
            </div>
          </div>
          <div class="field-row" :class="{ 'field-invalid': !hybridMaxLinesValid }">
            <label class="field-label" for="grid-max-count">
              <span>الحد الأقصى للخطوط</span>
              <span class="field-hint">سقف التوسعة (أكبر من عدد الشبكات)</span>
            </label>
            <div class="field-input-wrap">
              <input
                id="grid-max-count"
                v-model.number="form.maxGeneratorCount"
                class="bn-input"
                type="number"
                min="3"
                max="99999"
                step="1"
                required
              />
            </div>
          </div>
          <p v-if="!hybridMaxLinesValid" class="field-error" role="alert">
            يجب أن يكون السقف أكبر من عدد شبكات النطاق الحالية ({{ gridCount }}) لتجنب تعطيل المسار الهجين.
          </p>
          <div class="field-row">
            <label class="field-label" for="grid-capital">
              <span>الاستثمار</span>
              <span class="field-hint">Investment</span>
            </label>
            <div class="field-input-wrap">
              <input
                id="grid-capital"
                v-model.number="form.initialCapital"
                class="bn-input"
                type="number"
                step="any"
                min="0.01"
                required
              />
              <span class="field-suffix">USDT</span>
            </div>
          </div>
        </div>

          <details
            class="advanced-block"
            :open="showAdvanced"
            @toggle="showAdvanced = ($event.target as HTMLDetailsElement).open"
          >
            <summary class="advanced-summary">Advanced — متقدم</summary>
            <div class="advanced-fields">
              <div class="field-row">
                <label class="field-label" for="trail-offset">
                  <span>إزاحة جني الربح</span>
                  <span class="field-hint">trailingOffset</span>
                </label>
                <input id="trail-offset" v-model.number="form.trailingOffset" class="bn-input" type="number" step="any" min="0" />
              </div>
              <div class="field-row">
                <label class="field-label" for="trail-stop-pct">
                  <span>نسبة إيقاف الملاحقة</span>
                  <span class="field-hint">trailing_stop_pct</span>
                </label>
                <input id="trail-stop-pct" v-model.number="form.trailing_stop_pct" class="bn-input" type="number" step="any" min="0.001" max="0.5" />
              </div>
              <div class="field-row">
                <label class="field-label" for="compound-factor">
                  <span>عامل التضخيم</span>
                  <span class="field-hint">compoundingFactor</span>
                </label>
                <input id="compound-factor" v-model.number="form.compoundingFactor" class="bn-input" type="number" step="any" min="0" />
              </div>
              <div class="field-row">
                <label class="field-label" for="profit-mode">
                  <span>حقن الربح</span>
                  <span class="field-hint">profit_injection_mode</span>
                </label>
                <select id="profit-mode" v-model="form.profit_injection_mode" class="bn-input">
                  <option value="expand_count">توسعة عدد الخطوط</option>
                  <option value="compound_size">تضخيم اللوت</option>
                </select>
              </div>
              <div class="field-row">
                <label class="field-label" for="max-slip">
                  <span>حد الانزلاق</span>
                  <span class="field-hint">max_slippage_pct</span>
                </label>
                <input id="max-slip" v-model.number="form.max_slippage_pct" class="bn-input" type="number" step="any" min="0" max="0.05" />
              </div>
              <div class="field-row">
                <label class="field-label" for="dca-mode">
                  <span>توزيع الخطوط</span>
                  <span class="field-hint">dca_mode</span>
                </label>
                <select id="dca-mode" v-model="form.dca_mode" class="bn-input">
                  <option value="equal">متساوٍ (equal)</option>
                  <option value="log">تكديس قاع (log)</option>
                </select>
              </div>
            </div>
          </details>

        <div class="action-row">
          <button
            class="btn-create"
            type="submit"
            :disabled="saving || !rangeValid || !hybridMaxLinesValid"
          >
            {{ saving ? "جاري الحفظ…" : "حفظ الإعدادات" }}
          </button>
          <button
            v-if="!gridActiveHere"
            type="button"
            class="btn-run"
            :disabled="gridStarting || !store.credentialsConfigured"
            @click="onStartGrid"
          >
            {{ gridStarting ? "جاري التشغيل…" : `تشغيل شبكة ${store.symbol}` }}
          </button>
          <button
            v-else
            type="button"
            class="btn-stop-grid"
            @click="onStopGrid"
          >
            إيقاف {{ store.symbol }}
          </button>
        </div>
        <p v-if="gridActiveHere" class="grid-live muted">
          ● شبكة {{ store.symbol }} نشطة · أوامر {{ selectedGridOrders }}
        </p>
        <p v-else-if="otherActiveGrids.length" class="grid-live-other muted">
          شبكات أخرى نشطة: {{ otherActiveGrids.join("، ") }} — يمكنك تشغيل
          {{ store.symbol }} دون إيقافها.
        </p>
        <p v-if="gridActiveHere && selectedGridError" class="grid-live-warn" role="alert">
          خطأ الشبكة: {{ selectedGridError }}
        </p>
      </form>

      <aside class="grid-preview">
        <h3 class="preview-title">معاينة</h3>

        <div class="preview-range" :class="{ invalid: !rangeValid }">
          <div class="range-labels">
            <span class="range-high">{{ formatPrice(form.generatorUpper) }}</span>
            <span class="range-low">{{ formatPrice(form.generatorLower) }}</span>
          </div>
          <div class="range-track">
            <div
              v-for="(lv, i) in previewLevels"
              :key="i"
              class="grid-line"
              :style="{ bottom: `${(i / Math.max(previewLevels.length - 1, 1)) * 100}%` }"
            />
            <div
              v-if="mark > 0 && rangeValid"
              class="mark-dot"
              :class="{ out: !markInRange }"
              :style="{ bottom: `${markPositionPct}%` }"
              :title="`Mark ${formatPrice(mark)}`"
            />
          </div>
        </div>

        <dl class="preview-stats">
          <div class="stat">
            <dt>سعر Mark</dt>
            <dd>{{ formatPrice(mark) }}</dd>
          </div>
          <div class="stat">
            <dt>عرض النطاق</dt>
            <dd>{{ rangeValid ? `${rangePct.toFixed(2)}%` : "—" }}</dd>
          </div>
          <div class="stat">
            <dt>المسافة / شبكة</dt>
            <dd>{{ rangeValid ? formatPrice(gridStep) : "—" }}</dd>
          </div>
          <div class="stat">
            <dt>لكل شبكة</dt>
            <dd>{{ investmentPerGrid > 0 ? `${investmentPerGrid.toFixed(2)} USDT` : "—" }}</dd>
          </div>
          <div class="stat">
            <dt>خطوط (حالي / أقصى)</dt>
            <dd>{{ store.generatorCount }} / {{ store.maxGeneratorCount }}</dd>
          </div>
        </dl>

        <button type="button" class="levels-toggle" @click="showLevels = !showLevels">
          {{ showLevels ? "إخفاء" : "عرض" }} مستويات الشبكة ({{ previewLevels.length }})
        </button>
        <ul v-if="showLevels && previewLevels.length" class="levels-list">
          <li v-for="(p, i) in previewLevels" :key="i">
            <span class="lv-idx">{{ i + 1 }}</span>
            <span class="lv-price">{{ formatPrice(p) }}</span>
            <span
              v-if="mark > 0 && Math.abs(p - mark) / mark < 0.002"
              class="lv-near"
            >Mark</span>
          </li>
        </ul>

        <div class="preview-pnl">
          <div class="pnl-row">
            <span>ربح محقق</span>
            <span :class="store.realizedPnl >= 0 ? 'up' : 'down'">
              {{ store.realizedPnl >= 0 ? "+" : "" }}{{ store.realizedPnl.toFixed(4) }}
            </span>
          </div>
        </div>

        <button
          v-if="gridActiveHere"
          type="button"
          class="btn-stop"
          :disabled="killing"
          @click="onKill"
        >
          {{ killing ? "…" : "إيقاف الشبكة (طوارئ)" }}
        </button>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.grid-panel {
  background: #1e2329;
  border: 1px solid #2b3139;
  border-radius: 8px;
  overflow: hidden;
}
.grid-panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  flex-wrap: wrap;
  padding: 0.85rem 1rem;
  border-bottom: 1px solid #2b3139;
  background: #181a20;
}
.head-left {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}
.grid-panel-title {
  margin: 0;
  font-size: 1rem;
  font-weight: 600;
  color: #eaecef;
}
.symbol-chip {
  font-size: 0.78rem;
  font-weight: 600;
  padding: 0.15rem 0.45rem;
  border-radius: 4px;
  background: #2b3139;
  color: #f0b90b;
}
.mode-chip {
  font-size: 0.65rem;
  font-weight: 700;
  text-transform: uppercase;
  padding: 0.12rem 0.4rem;
  border-radius: 4px;
  border: 1px solid transparent;
}
.mode-chip.demo {
  background: rgba(56, 189, 248, 0.12);
  color: #7dd3fc;
  border-color: rgba(56, 189, 248, 0.35);
}
.mode-chip.testnet {
  background: rgba(240, 185, 11, 0.12);
  color: #f0b90b;
  border-color: rgba(240, 185, 11, 0.35);
}
.mode-chip.mainnet {
  background: rgba(14, 203, 129, 0.12);
  color: #34d399;
  border-color: rgba(14, 203, 129, 0.35);
}
.grid-panel-body {
  display: grid;
  grid-template-columns: 1fr minmax(240px, 320px);
  gap: 0;
}
@media (max-width: 860px) {
  .grid-panel-body {
    grid-template-columns: 1fr;
  }
}
.grid-form {
  padding: 1rem;
  border-inline-end: 1px solid #2b3139;
  display: flex;
  flex-direction: column;
  gap: 1rem;
}
@media (max-width: 860px) {
  .grid-form {
    border-inline-end: none;
    border-bottom: 1px solid #2b3139;
  }
}
.form-block {
  display: flex;
  flex-direction: column;
  gap: 0.65rem;
}
.block-title {
  margin: 0 0 0.15rem;
  font-size: 0.72rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: #848e9c;
}
.field-row {
  display: grid;
  grid-template-columns: 1fr 1.4fr;
  gap: 0.65rem;
  align-items: center;
}
@media (max-width: 520px) {
  .field-row {
    grid-template-columns: 1fr;
  }
}
.field-label {
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  font-size: 0.82rem;
  color: #eaecef;
  cursor: pointer;
}
.field-hint {
  font-size: 0.68rem;
  color: #848e9c;
  font-weight: 400;
}
.field-input-wrap {
  display: flex;
  align-items: center;
  background: #2b3139;
  border: 1px solid transparent;
  border-radius: 4px;
  transition: border-color 0.15s;
}
.field-input-wrap:focus-within {
  border-color: #f0b90b;
}
.bn-input {
  flex: 1;
  min-width: 0;
  border: none;
  background: transparent;
  color: #eaecef;
  padding: 0.5rem 0.65rem;
  font-size: 0.88rem;
  font-variant-numeric: tabular-nums;
  outline: none;
}
.field-suffix {
  padding: 0 0.65rem;
  font-size: 0.75rem;
  color: #848e9c;
  border-inline-start: 1px solid #474d57;
  white-space: nowrap;
}
.count-wrap {
  flex-direction: column;
  align-items: stretch;
  gap: 0.35rem;
  padding: 0.35rem 0.5rem 0.5rem;
}
.count-slider {
  width: 100%;
  accent-color: #f0b90b;
  height: 4px;
}
.btn-create {
  width: 100%;
  margin-top: 0.25rem;
  padding: 0.75rem 1rem;
  border: none;
  border-radius: 4px;
  background: #fcd535;
  color: #181a20;
  font-size: 0.92rem;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s;
}
.btn-create:hover:not(:disabled) {
  background: #f0b90b;
}
.btn-create:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.field-invalid .field-input-wrap {
  border-color: rgba(246, 70, 93, 0.55);
}
.field-error {
  margin: -0.35rem 0 0;
  font-size: 0.74rem;
  line-height: 1.35;
  color: #f6465d;
}
.advanced-block {
  margin-top: 0.75rem;
  border: 1px solid #1e2630;
  border-radius: 8px;
  padding: 0.5rem 0.65rem;
  background: rgba(15, 19, 24, 0.6);
}
.advanced-summary {
  cursor: pointer;
  font-size: 0.82rem;
  font-weight: 600;
  color: #94a3b8;
  list-style: none;
}
.advanced-summary::-webkit-details-marker {
  display: none;
}
.advanced-fields {
  margin-top: 0.65rem;
  display: flex;
  flex-direction: column;
  gap: 0.55rem;
}
.action-row {
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
  margin-top: 0.25rem;
}
.btn-run {
  width: 100%;
  padding: 0.7rem 1rem;
  border: none;
  border-radius: 4px;
  background: #0ecb81;
  color: #181a20;
  font-size: 0.88rem;
  font-weight: 600;
  cursor: pointer;
}
.btn-run:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.btn-stop-grid {
  width: 100%;
  padding: 0.65rem;
  border-radius: 4px;
  border: 1px solid #f0b90b;
  background: rgba(240, 185, 11, 0.12);
  color: #f0b90b;
  font-weight: 600;
  cursor: pointer;
}
.grid-live-other {
  margin: 0.35rem 0 0;
  font-size: 0.76rem;
  line-height: 1.45;
  color: #94a3b8;
}
.grid-live {
  margin: 0;
  font-size: 0.78rem;
  color: #0ecb81;
}
.grid-live-warn {
  margin: 0.35rem 0 0;
  font-size: 0.78rem;
  line-height: 1.4;
  color: #f6465d;
  border-left: 3px solid rgba(246, 70, 93, 0.6);
  padding: 0.35rem 0.5rem;
  background: rgba(246, 70, 93, 0.08);
  border-radius: 4px;
}
.grid-preview {
  padding: 1rem;
  background: #181a20;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.preview-title {
  margin: 0;
  font-size: 0.72rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: #848e9c;
}
.preview-range {
  display: flex;
  gap: 0.5rem;
  min-height: 160px;
}
.preview-range.invalid .range-track {
  opacity: 0.45;
}
.range-labels {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  font-size: 0.68rem;
  font-variant-numeric: tabular-nums;
  color: #848e9c;
  padding: 0.15rem 0;
}
.range-high {
  color: #f6465d;
}
.range-low {
  color: #0ecb81;
}
.range-track {
  flex: 1;
  position: relative;
  background: linear-gradient(180deg, rgba(246, 70, 93, 0.08) 0%, rgba(14, 203, 129, 0.08) 100%);
  border: 1px solid #2b3139;
  border-radius: 4px;
}
.grid-line {
  position: absolute;
  left: 0;
  right: 0;
  height: 1px;
  background: rgba(132, 142, 156, 0.35);
}
.mark-dot {
  position: absolute;
  left: 50%;
  width: 10px;
  height: 10px;
  margin-inline-start: -5px;
  margin-bottom: -5px;
  border-radius: 50%;
  background: #f0b90b;
  border: 2px solid #181a20;
  box-shadow: 0 0 0 1px #f0b90b;
  z-index: 2;
}
.mark-dot.out {
  background: #848e9c;
  box-shadow: 0 0 0 1px #848e9c;
}
.preview-stats {
  margin: 0;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.45rem 0.75rem;
}
.stat dt {
  margin: 0;
  font-size: 0.68rem;
  color: #848e9c;
}
.stat dd {
  margin: 0.1rem 0 0;
  font-size: 0.82rem;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: #eaecef;
}
.levels-toggle {
  border: none;
  background: transparent;
  color: #f0b90b;
  font-size: 0.75rem;
  cursor: pointer;
  text-align: start;
  padding: 0;
}
.levels-list {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 120px;
  overflow-y: auto;
  border: 1px solid #2b3139;
  border-radius: 4px;
}
.levels-list li {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.3rem 0.5rem;
  font-size: 0.75rem;
  border-bottom: 1px solid rgba(43, 49, 57, 0.6);
}
.lv-idx {
  color: #848e9c;
  min-width: 1.2rem;
}
.lv-price {
  flex: 1;
  font-variant-numeric: tabular-nums;
  color: #eaecef;
}
.lv-near {
  font-size: 0.65rem;
  color: #f0b90b;
  font-weight: 600;
}
.preview-pnl {
  border-top: 1px solid #2b3139;
  padding-top: 0.65rem;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
.pnl-row {
  display: flex;
  justify-content: space-between;
  font-size: 0.78rem;
  color: #848e9c;
}
.pnl-row span:last-child {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.pnl-row .up {
  color: #0ecb81;
}
.pnl-row .down {
  color: #f6465d;
}
.btn-stop {
  margin-top: auto;
  width: 100%;
  padding: 0.55rem;
  border-radius: 4px;
  border: 1px solid rgba(246, 70, 93, 0.55);
  background: transparent;
  color: #f6465d;
  font-size: 0.8rem;
  font-weight: 600;
  cursor: pointer;
}
.btn-stop:hover:not(:disabled) {
  background: rgba(246, 70, 93, 0.1);
}
.btn-stop:disabled {
  opacity: 0.5;
}
</style>
