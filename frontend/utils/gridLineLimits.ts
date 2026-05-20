/** Mirror backend ``compute_grid_line_limits`` for live UI feedback. */

export const MIN_USDT_PER_LINE = 11
export const MIN_LINE_SPACING_PCT = 0.0015
export const ABSOLUTE_MAX_GENERATOR_COUNT = 64

export type GridLineLimits = {
  valid: boolean
  maxGeneratorCount: number
  minGeneratorCount: number
  maxFromSpacing: number
  maxFromCapital: number
  limitingFactor: "range" | "spacing" | "capital" | "platform_cap"
  bandSpanPct: number
  lineSpacingPct: number
  usdtPerLine: number
  minUsdtPerLine: number
  minLineSpacingPct: number
  economicsOk: boolean
  generatorCount: number
  absoluteMaxGeneratorCount: number
}

export function computeGridLineLimits(opts: {
  generatorUpper: number
  generatorLower: number
  allocatedCapital: number
  generatorCount?: number
}): GridLineLimits {
  const upper = Number(opts.generatorUpper)
  const lower = Number(opts.generatorLower)
  const alloc = Number(opts.allocatedCapital)
  const count = Math.max(2, Math.floor(opts.generatorCount ?? 2))

  const base: GridLineLimits = {
    valid: false,
    maxGeneratorCount: 2,
    minGeneratorCount: 2,
    maxFromSpacing: 2,
    maxFromCapital: 2,
    limitingFactor: "range",
    bandSpanPct: 0,
    lineSpacingPct: 0,
    usdtPerLine: 0,
    minUsdtPerLine: MIN_USDT_PER_LINE,
    minLineSpacingPct: MIN_LINE_SPACING_PCT * 100,
    economicsOk: false,
    generatorCount: count,
    absoluteMaxGeneratorCount: ABSOLUTE_MAX_GENERATOR_COUNT,
  }

  if (!(alloc > 0) || !(lower < upper)) return base

  const mid = (upper + lower) / 2
  if (!(mid > 0)) return base

  const spanPct = (upper - lower) / mid
  const maxFromSpacing =
    MIN_LINE_SPACING_PCT > 0
      ? Math.max(2, Math.floor(spanPct / MIN_LINE_SPACING_PCT) + 1)
      : ABSOLUTE_MAX_GENERATOR_COUNT
  const maxFromCapital =
    MIN_USDT_PER_LINE > 0
      ? Math.max(2, Math.floor(alloc / MIN_USDT_PER_LINE))
      : ABSOLUTE_MAX_GENERATOR_COUNT

  let maxCount = Math.min(maxFromSpacing, maxFromCapital, ABSOLUTE_MAX_GENERATOR_COUNT)
  maxCount = Math.max(2, maxCount)

  let limiting: GridLineLimits["limitingFactor"] = "spacing"
  if (maxCount >= ABSOLUTE_MAX_GENERATOR_COUNT) limiting = "platform_cap"
  else if (maxFromSpacing > maxFromCapital) limiting = "capital"

  const spacingAt = spanPct / Math.max(count - 1, 1)
  const usdtAt = alloc / count
  const economicsOk = spacingAt >= MIN_LINE_SPACING_PCT && usdtAt >= MIN_USDT_PER_LINE

  return {
    ...base,
    valid: true,
    maxGeneratorCount: maxCount,
    maxFromSpacing,
    maxFromCapital,
    limitingFactor: limiting,
    bandSpanPct: spanPct * 100,
    lineSpacingPct: spacingAt * 100,
    usdtPerLine: usdtAt,
    economicsOk,
  }
}

export function bandFromMarkSpan(mark: number, spanPct = 3.5): { lower: number; upper: number } {
  const m = Number(mark)
  if (!(m > 0)) return { lower: 0, upper: 0 }
  const half = m * (spanPct / 200)
  let lower = m - half
  let upper = m + half
  if (lower <= 0) lower = m * 0.985
  return { lower, upper }
}

export function limitingFactorLabelAr(factor: GridLineLimits["limitingFactor"]): string {
  switch (factor) {
    case "spacing":
      return "عرض النطاق (المسافة بين الخطوط)"
    case "capital":
      return "رأس المال المخصص (USDT لكل خط)"
    case "platform_cap":
      return "حد المنصة الداخلي"
    default:
      return "النطاق أو التخصيص"
  }
}
