<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue"
import {
  bandFromMarkSpan,
  computeGridLineLimits,
  limitingFactorLabelAr,
} from "~/utils/gridLineLimits"
import { useBotStore } from "~/stores/bot"

const store = useBotStore()
const saving = ref(false)
const gridStarting = ref(false)
const showLevels = ref(false)

const form = reactive({
  generatorUpper: 0,
  generatorLower: 0,
  generatorCount: 5,
  allocatedCapital: 40,
})

watch(
  () => ({
    u: store.generatorUpper,
    l: store.generatorLower,
    c: store.generatorCount,
    a: store.allocatedCapital,
    avail: store.availableBalance,
    sym: store.symbol,
  }),
  (v) => {
    form.generatorUpper = v.u
    form.generatorLower = v.l
    form.generatorCount = v.c
    if (v.a > 0) form.allocatedCapital = v.a
    else if (v.avail > 0 && form.allocatedCapital <= 0) {
      form.allocatedCapital = Math.min(v.avail, store.maxAllocatableUsdt || v.avail)
    }
  },
  { immediate: true },
)

const mark = computed(() => store.markPrice)
const gridCount = computed(() => Math.max(2, Math.floor(form.generatorCount) || 2))

const lineLimits = computed(() =>
  computeGridLineLimits({
    generatorUpper: form.generatorUpper,
    generatorLower: form.generatorLower,
    allocatedCapital: form.allocatedCapital,
    generatorCount: gridCount.value,
  }),
)

const maxAllowedLines = computed(() =>
  lineLimits.value.valid ? lineLimits.value.maxGeneratorCount : 2,
)

const countOverMax = computed(
  () => lineLimits.value.valid && gridCount.value > maxAllowedLines.value,
)

const rangeValid = computed(
  () => form.generatorUpper > form.generatorLower && form.generatorLower > 0,
)

watch(maxAllowedLines, (maxN) => {
  if (lineLimits.value.valid && form.generatorCount > maxN) {
    form.generatorCount = maxN
  }
})

function applyBandFromMark(spanPct = 3.5) {
  const mk = mark.value
  if (!(mk > 0)) return
  const { lower, upper } = bandFromMarkSpan(mk, spanPct)
  form.generatorLower = lower
  form.generatorUpper = upper
}

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

const usdtPerLine = computed(() => {
  if (!rangeValid.value || form.allocatedCapital <= 0) return 0
  return form.allocatedCapital / gridCount.value
})

const allocationValid = computed(
  () =>
    store.balanceIsLive &&
    form.allocatedCapital > 0 &&
    form.allocatedCapital <= store.maxAllocatableUsdt + 1e-6,
)

const allocationHint = computed(() => {
  if (store.balanceSyncState === "error") {
    return "تعذّرت مزامنة الرصيد — أصلح خطأ Binance أعلاه قبل التخصيص"
  }
  if (store.balanceSyncState === "pending") {
    return "جاري جلب الرصيد المتاح من Binance…"
  }
  const avail = store.availableBalance
  const max = store.maxAllocatableUsdt
  const other = store.otherGridsAllocatedUsdt
  if (!(avail > 0)) return "لا يوجد USDT متاح بعد المزامنة"
  if (other > 0) {
    return `متاح لهذه الشبكة: ${max.toFixed(2)} USDT (محجوز لشبكات أخرى: ${other.toFixed(2)})`
  }
  return `الرصيد المتاح على المنصة: ${avail.toFixed(2)} USDT`
})

function buildPreviewLevels(lo: number, hi: number, n: number): number[] {
  if (n < 2) return [lo, hi]
  const step = (hi - lo) / (n - 1)
  return Array.from({ length: n }, (_, i) => lo + i * step)
}

const previewLevels = computed(() => {
  if (!rangeValid.value) return []
  return buildPreviewLevels(form.generatorLower, form.generatorUpper, gridCount.value)
})

function formatPrice(n: number): string {
  if (!(n > 0)) return "—"
  if (n >= 100) return n.toFixed(2)
  if (n >= 1) return n.toFixed(4)
  return n.toFixed(6)
}

async function onSave() {
  if (countOverMax.value) {
    alert(`عدد الخطوط يتجاوز الحد الأقصى (${maxAllowedLines.value})`)
    return
  }
  saving.value = true
  try {
    await store.saveGridBand({
      generatorUpper: form.generatorUpper,
      generatorLower: form.generatorLower,
      generatorCount: Math.min(gridCount.value, maxAllowedLines.value),
    })
  } finally {
    saving.value = false
  }
}

async function onToggleCompound() {
  await store.setAutoCompounding(!store.autoCompoundingEnabled)
}

async function onStartGrid() {
  if (!store.credentialsConfigured) {
    alert("أضف مفاتيح Binance في .env أولاً")
    return
  }
  if (!rangeValid.value) {
    alert("عيّن نطاقاً صالحاً: generatorLower < generatorUpper")
    return
  }
  if (countOverMax.value || !lineLimits.value.economicsOk) {
    alert(
      `عدد الخطوط غير مسموح: الحد الأقصى ${maxAllowedLines.value} — ${limitingFactorLabelAr(lineLimits.value.limitingFactor)}`,
    )
    return
  }
  if (!store.autoCompoundingEnabled) {
    alert("فعّل «التكبير المركب التلقائي» قبل التشغيل")
    return
  }
  if (!allocationValid.value) {
    alert("رأس المال المخصص يتجاوز السيولة المتاحة أو غير صالح (Insufficient Live Balance)")
    return
  }
  const envLabel = `Spot · ${store.spotEnvLabel}`
  if (
    !confirm(
      `تشغيل شبكة ${store.symbol} على ${envLabel}؟\n` +
        `النطاق: ${form.generatorLower} – ${form.generatorUpper} (${gridCount.value} خطوط)\n` +
        `تخصيص معزول: ${form.allocatedCapital.toFixed(2)} USDT · حجم كل خط ≈ ${form.allocatedCapital.toFixed(2)} ÷ ${gridCount.value}`,
    )
  ) {
    return
  }
  gridStarting.value = true
  try {
    await store.startGrid({
      calibrate: false,
      allocatedCapital: form.allocatedCapital,
      generatorUpper: form.generatorUpper,
      generatorLower: form.generatorLower,
      generatorCount: Math.max(2, Math.floor(form.generatorCount)),
    })
  } catch (e) {
    const msg =
      e && typeof e === "object" && "data" in e && (e as { data?: { detail?: string } }).data?.detail
        ? String((e as { data?: { detail?: string } }).data?.detail)
        : String(e)
    alert(msg.includes("Insufficient") ? "رصيد غير كافٍ: التخصيص أكبر من USDT المتاح على Binance" : msg)
  } finally {
    gridStarting.value = false
  }
}

async function onStopGrid() {
  const sym = store.symbol.trim().toUpperCase()
  if (!confirm(`إيقاف شبكة ${sym}؟`)) return
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
</script>

<template>
  <section class="grid-panel panel" aria-label="إعدادات شبكة التداول">
    <header class="grid-panel-head panel-header">
      <div class="head-left">
        <div>
          <h2 class="panel-title">شبكة Spot</h2>
          <p class="panel-subtitle">إعدادات النطاق والتخصيص — {{ store.symbol }}</p>
        </div>
        <span class="chip chip-symbol">{{ store.symbol }}</span>
        <span
          v-if="store.credentialsConfigured"
          class="chip"
          :class="
            store.binanceEnv === 'demo'
              ? 'chip-env-demo'
              : store.binanceEnv === 'mainnet'
                ? 'chip-env-mainnet'
                : 'chip-env-testnet'
          "
        >
          {{ store.spotEnvLabel }}
        </span>
      </div>
    </header>

    <div class="compound-toggle-row">
      <label class="toggle-wrap">
        <input
          type="checkbox"
          class="toggle-input"
          :checked="store.autoCompoundingEnabled"
          @change="onToggleCompound"
        />
        <span class="toggle-ui" aria-hidden="true" />
        <span class="toggle-text">
          <span class="toggle-title">تفعيل التكبير المركب التلقائي</span>
          <span class="toggle-hint">compound_size · 100% داخل التخصيص المعزول لكل خط</span>
        </span>
      </label>
    </div>

    <div class="grid-panel-body">
      <form class="grid-form" @submit.prevent="onSave">
        <div class="form-block">
          <h3 class="block-title">تخصيص رأس المال (معزول)</h3>
          <p class="block-hint muted">{{ allocationHint }}</p>
          <div class="field-row">
            <label class="field-label" for="grid-alloc">
              <span>allocatedCapital</span>
            </label>
            <div class="field-input-wrap">
              <input
                id="grid-alloc"
                v-model.number="form.allocatedCapital"
                class="bn-input"
                type="number"
                step="any"
                min="1"
                :max="store.maxAllocatableUsdt || undefined"
                required
              />
              <span class="field-unit">USDT</span>
            </div>
          </div>
          <p v-if="!allocationValid && form.allocatedCapital > 0" class="alloc-warn" role="alert">
            التخصيص يتجاوز السيولة المتاحة لهذه الشبكة
          </p>
          <p v-if="usdtPerLine > 0" class="alloc-preview muted">
            حجم كل خط ≈ {{ usdtPerLine.toFixed(2) }} USDT ({{ form.allocatedCapital.toFixed(2) }} ÷
            {{ gridCount }})
          </p>
        </div>

        <div class="form-block">
          <h3 class="block-title">نطاق الشبكة</h3>
          <p class="block-hint muted">الحقول العقدية فقط — مرتبطة مباشرة بالخادم</p>
          <div v-if="mark > 0" class="mark-band-row">
            <span class="mark-band-label muted">Mark {{ formatPrice(mark) }}</span>
            <button type="button" class="btn-band" @click="applyBandFromMark(3.5)">
              نطاق ±3.5% حول Mark
            </button>
            <button type="button" class="btn-band subtle" @click="applyBandFromMark(7)">
              ±7%
            </button>
          </div>
          <div v-if="rangeValid && lineLimits.valid" class="lines-limit-card">
            <div class="lines-limit-head">
              <span class="lines-max-badge">الحد الأقصى: {{ maxAllowedLines }} خط</span>
              <span class="lines-limit-reason muted">
                يحدّه: {{ limitingFactorLabelAr(lineLimits.limitingFactor) }}
              </span>
            </div>
            <p class="lines-limit-detail muted">
              عرض النطاق {{ lineLimits.bandSpanPct.toFixed(2) }}% · مسافة الخط
              {{ lineLimits.lineSpacingPct.toFixed(3) }}% (أدنى
              {{ lineLimits.minLineSpacingPct }}%) · USDT/خط
              {{ lineLimits.usdtPerLine.toFixed(2) }}
            </p>
            <p v-if="countOverMax || !lineLimits.economicsOk" class="lines-limit-warn" role="alert">
              {{
                countOverMax
                  ? `عدد الخطوط (${gridCount}) يتجاوز الحد — اختر حتى ${maxAllowedLines} خطوط`
                  : "الإعداد الحالي لا يجتاز حدود المسافة أو USDT لكل خط"
              }}
            </p>
          </div>
          <div class="field-row">
            <label class="field-label" for="grid-upper">
              <span>generatorUpper (قمة)</span>
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
            </div>
          </div>
          <div class="field-row">
            <label class="field-label" for="grid-lower">
              <span>generatorLower (قاع)</span>
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
            </div>
          </div>
          <div class="field-row">
            <label class="field-label" for="grid-count">
              <span>generatorCount</span>
            </label>
            <div class="field-input-wrap count-wrap">
              <input
                id="grid-count"
                v-model.number="form.generatorCount"
                class="bn-input"
                :class="{ 'input-over-max': countOverMax }"
                type="number"
                min="2"
                :max="maxAllowedLines"
                required
              />
              <input
                v-model.number="form.generatorCount"
                class="count-slider"
                type="range"
                min="2"
                :max="maxAllowedLines"
              />
              <span class="count-cap muted">2 – {{ maxAllowedLines }} خط مسموح</span>
            </div>
          </div>
        </div>

        <div class="action-row">
          <button
            class="btn-save"
            type="submit"
            :disabled="saving || !rangeValid || countOverMax"
          >
            {{ saving ? "جاري الحفظ…" : "حفظ النطاق" }}
          </button>
          <button
            v-if="!gridActiveHere"
            type="button"
            class="btn-run"
            :disabled="
              gridStarting ||
              !store.credentialsConfigured ||
              !store.autoCompoundingEnabled ||
              !allocationValid ||
              countOverMax ||
              !lineLimits.economicsOk
            "
            @click="onStartGrid"
          >
            {{ gridStarting ? "جاري التشغيل…" : `تشغيل ${store.symbol}` }}
          </button>
          <button v-else type="button" class="btn-stop-grid" @click="onStopGrid">
            إيقاف {{ store.symbol }}
          </button>
        </div>
        <p v-if="gridActiveHere" class="grid-live muted">
          ● شبكة نشطة · أوامر {{ selectedGridOrders }}
        </p>
        <p v-else-if="otherActiveGrids.length" class="grid-live-other muted">
          شبكات أخرى: {{ otherActiveGrids.join("، ") }}
        </p>
        <p v-if="gridActiveHere && selectedGridError" class="grid-live-warn" role="alert">
          {{ selectedGridError }}
        </p>

        <GridLedgerPanel
          v-if="gridActiveHere || store.gridLedgerPack(store.symbol)?.frozen"
          :symbol="store.symbol"
        />
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
            <dt>Mark</dt>
            <dd>{{ formatPrice(mark) }}</dd>
          </div>
          <div class="stat">
            <dt>عرض النطاق</dt>
            <dd>{{ rangeValid ? `${rangePct.toFixed(2)}%` : "—" }}</dd>
          </div>
          <div class="stat">
            <dt>USDT / خط (حي)</dt>
            <dd>{{ usdtPerLine > 0 ? `${usdtPerLine.toFixed(2)}` : "—" }}</dd>
          </div>
          <div class="stat">
            <dt>الخطوط (حالي / أقصى)</dt>
            <dd>{{ gridCount }} / {{ rangeValid ? maxAllowedLines : "—" }}</dd>
          </div>
        </dl>
        <button type="button" class="levels-toggle" @click="showLevels = !showLevels">
          {{ showLevels ? "إخفاء" : "عرض" }} المستويات ({{ previewLevels.length }})
        </button>
        <ul v-if="showLevels && previewLevels.length" class="levels-list">
          <li v-for="(p, i) in previewLevels" :key="i">
            <span class="lv-idx">{{ i + 1 }}</span>
            <span class="lv-price">{{ formatPrice(p) }}</span>
          </li>
        </ul>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.grid-panel {
  overflow: hidden;
  padding: 0;
}
.grid-panel-head {
  margin-bottom: 0;
  padding: 0.9rem 1.1rem;
  border-bottom: 1px solid var(--border);
  background: rgba(7, 10, 15, 0.45);
}
.head-left {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  flex-wrap: wrap;
}
.compound-toggle-row {
  padding: 0.75rem 1.1rem;
  border-bottom: 1px solid var(--border);
  background: var(--warn-dim);
}
.toggle-wrap {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  cursor: pointer;
  user-select: none;
}
.toggle-input {
  position: absolute;
  opacity: 0;
  width: 0;
  height: 0;
}
.toggle-ui {
  width: 44px;
  height: 24px;
  border-radius: 12px;
  background: #474d57;
  position: relative;
  flex-shrink: 0;
  transition: background 0.2s;
}
.toggle-ui::after {
  content: "";
  position: absolute;
  top: 3px;
  left: 3px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #fff;
  transition: transform 0.2s;
}
.toggle-input:checked + .toggle-ui {
  background: #f0b90b;
}
.toggle-input:checked + .toggle-ui::after {
  transform: translateX(20px);
}
.toggle-title {
  display: block;
  font-size: 0.88rem;
  font-weight: 600;
  color: #eaecef;
}
.toggle-hint {
  display: block;
  font-size: 0.72rem;
  color: #848e9c;
  margin-top: 0.15rem;
}
.grid-panel-body {
  display: grid;
  grid-template-columns: 1fr minmax(220px, 300px);
  gap: 0;
}
@media (max-width: 860px) {
  .grid-panel-body {
    grid-template-columns: 1fr;
  }
}
.grid-form {
  padding: 1rem 1.1rem;
  border-inline-end: 1px solid var(--border);
}
.block-title {
  margin: 0 0 0.25rem;
  font-size: 0.82rem;
  font-weight: 600;
  color: #eaecef;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.block-hint {
  margin: 0 0 0.75rem;
  font-size: 0.72rem;
}
.field-row {
  margin-bottom: 0.65rem;
}
.field-label {
  display: block;
  font-size: 0.75rem;
  font-weight: 600;
  color: #848e9c;
  margin-bottom: 0.25rem;
  font-family: ui-monospace, monospace;
}
.bn-input {
  width: 100%;
  box-sizing: border-box;
  padding: 0.5rem 0.65rem;
  border-radius: 4px;
  border: 1px solid #474d57;
  background: #0b0e11;
  color: #eaecef;
  font-size: 0.9rem;
}
.bn-input:focus {
  outline: none;
  border-color: #f0b90b;
}
.count-wrap {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
.count-slider {
  width: 100%;
  accent-color: #f0b90b;
}
.action-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-top: 0.85rem;
}
.btn-save {
  border: 1px solid #474d57;
  background: #2b3139;
  color: #eaecef;
  border-radius: 4px;
  padding: 0.5rem 1rem;
  font-weight: 600;
  cursor: pointer;
}
.btn-run {
  border: none;
  background: #f0b90b;
  color: #181a20;
  border-radius: 4px;
  padding: 0.5rem 1rem;
  font-weight: 700;
  cursor: pointer;
}
.btn-stop-grid {
  border: 1px solid #f6465d;
  background: transparent;
  color: #f6465d;
  border-radius: 4px;
  padding: 0.5rem 1rem;
  font-weight: 600;
  cursor: pointer;
}
.btn-save:disabled,
.btn-run:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.grid-preview {
  padding: 1rem;
  background: #181a20;
}
.preview-title {
  margin: 0 0 0.65rem;
  font-size: 0.82rem;
  color: #848e9c;
}
.preview-range {
  height: 140px;
  position: relative;
  margin-bottom: 0.75rem;
  border: 1px solid #2b3139;
  border-radius: 6px;
  background: #0b0e11;
}
.preview-range.invalid {
  opacity: 0.5;
}
.range-labels {
  position: absolute;
  inset: 0;
  pointer-events: none;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 0.35rem 0.5rem;
  font-size: 0.68rem;
  color: #848e9c;
}
.range-track {
  position: absolute;
  inset: 0.5rem 1.5rem;
}
.grid-line {
  position: absolute;
  left: 0;
  right: 0;
  height: 2px;
  background: rgba(240, 185, 11, 0.35);
}
.mark-dot {
  position: absolute;
  left: 50%;
  width: 10px;
  height: 10px;
  margin-left: -5px;
  margin-bottom: -5px;
  border-radius: 50%;
  background: #0ecb81;
  box-shadow: 0 0 6px rgba(14, 203, 129, 0.6);
}
.mark-dot.out {
  background: #f6465d;
}
.preview-stats {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.35rem 0.75rem;
  margin: 0;
  font-size: 0.78rem;
}
.stat dt {
  color: #848e9c;
  margin: 0;
}
.stat dd {
  margin: 0.1rem 0 0;
  font-weight: 600;
  color: #eaecef;
}
.levels-toggle {
  margin-top: 0.5rem;
  background: none;
  border: none;
  color: #f0b90b;
  font-size: 0.75rem;
  cursor: pointer;
  padding: 0;
}
.field-unit {
  margin-inline-start: 0.35rem;
  font-size: 0.72rem;
  color: #848e9c;
}
.alloc-warn {
  margin: 0.35rem 0 0;
  font-size: 0.75rem;
  color: #f6465d;
}
.alloc-preview {
  margin: 0.35rem 0 0;
  font-size: 0.72rem;
}
.levels-list {
  list-style: none;
  margin: 0.5rem 0 0;
  padding: 0;
  max-height: 120px;
  overflow-y: auto;
  font-size: 0.72rem;
}
.levels-list li {
  display: flex;
  gap: 0.5rem;
  padding: 0.2rem 0;
}
.lv-idx {
  color: #848e9c;
  width: 1.5rem;
}
.lv-price {
  font-variant-numeric: tabular-nums;
}
.grid-live-warn {
  color: #f6465d;
  font-size: 0.78rem;
  margin: 0.5rem 0 0;
}
.muted {
  color: #848e9c;
}
.mark-band-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.4rem;
  margin-bottom: 0.65rem;
}
.mark-band-label {
  font-size: 0.72rem;
  margin-inline-end: 0.25rem;
}
.btn-band {
  border: 1px solid rgba(240, 185, 11, 0.45);
  background: rgba(240, 185, 11, 0.08);
  color: #f0b90b;
  border-radius: 4px;
  padding: 0.28rem 0.55rem;
  font-size: 0.72rem;
  font-weight: 600;
  cursor: pointer;
}
.btn-band.subtle {
  border-color: #474d57;
  background: transparent;
  color: #848e9c;
}
.lines-limit-card {
  margin-bottom: 0.75rem;
  padding: 0.55rem 0.65rem;
  border-radius: 6px;
  border: 1px solid rgba(240, 185, 11, 0.25);
  background: rgba(240, 185, 11, 0.06);
}
.lines-limit-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.35rem 0.65rem;
  margin-bottom: 0.25rem;
}
.lines-max-badge {
  font-size: 0.82rem;
  font-weight: 700;
  color: #f0b90b;
}
.lines-limit-reason {
  font-size: 0.68rem;
}
.lines-limit-detail {
  margin: 0;
  font-size: 0.68rem;
  line-height: 1.4;
}
.lines-limit-warn {
  margin: 0.35rem 0 0;
  font-size: 0.72rem;
  color: #f6465d;
}
.input-over-max {
  border-color: #f6465d !important;
}
.count-cap {
  font-size: 0.68rem;
}
</style>
