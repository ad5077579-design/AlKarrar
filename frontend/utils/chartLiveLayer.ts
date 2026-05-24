import type { Time } from "lightweight-charts"

export type SpotEnvKind = "demo" | "testnet" | "mainnet"

export function normalizeSpotEnv(
  binanceEnv: string,
  exchangeTestnet: boolean,
): SpotEnvKind {
  const e = String(binanceEnv ?? "").trim().toLowerCase()
  if (e === "demo") return "demo"
  if (e === "testnet") return "testnet"
  if (e === "mainnet") return "mainnet"
  return exchangeTestnet ? "testnet" : "mainnet"
}

export function sessionStartSec(iso?: string): number | undefined {
  if (!iso?.trim()) return undefined
  const ms = Date.parse(iso.trim())
  if (!Number.isFinite(ms)) return undefined
  return Math.floor(ms / 1000)
}

/** Human duration since grid session start (Arabic-friendly units). */
export function formatSessionDuration(startSec: number, nowMs = Date.now()): string {
  const sec = Math.max(0, Math.floor(nowMs / 1000) - startSec)
  if (sec < 60) return `${sec}ث`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min}د`
  const h = Math.floor(min / 60)
  const rm = min % 60
  if (h < 48) return rm > 0 ? `${h}س ${rm}د` : `${h}س`
  const d = Math.floor(h / 24)
  const rh = h % 24
  return rh > 0 ? `${d}ي ${rh}س` : `${d}ي`
}

export function envHostHint(env: SpotEnvKind): string {
  if (env === "demo") return "demo-api.binance.com"
  if (env === "testnet") return "testnet.binance.vision"
  return "api.binance.com"
}

export function envTradingLabelAr(env: SpotEnvKind): string {
  if (env === "demo") return "تجريبي — Spot Demo"
  if (env === "testnet") return "ورقي — Testnet"
  return "إنتاج — أموال حقيقية"
}

export function sessionBackdropColors(env: SpotEnvKind): {
  topFillColor1: string
  topFillColor2: string
  bottomFillColor1: string
  bottomFillColor2: string
} {
  if (env === "demo") {
    return {
      topFillColor1: "rgba(56, 189, 248, 0.18)",
      topFillColor2: "rgba(56, 189, 248, 0.05)",
      bottomFillColor1: "rgba(56, 189, 248, 0.03)",
      bottomFillColor2: "rgba(56, 189, 248, 0.02)",
    }
  }
  if (env === "testnet") {
    return {
      topFillColor1: "rgba(245, 158, 11, 0.16)",
      topFillColor2: "rgba(245, 158, 11, 0.05)",
      bottomFillColor1: "rgba(245, 158, 11, 0.03)",
      bottomFillColor2: "rgba(245, 158, 11, 0.02)",
    }
  }
  return {
    topFillColor1: "rgba(239, 68, 68, 0.14)",
    topFillColor2: "rgba(239, 68, 68, 0.05)",
    bottomFillColor1: "rgba(239, 68, 68, 0.03)",
    bottomFillColor2: "rgba(239, 68, 68, 0.02)",
  }
}

export function buildSessionBandPoints(
  candles: { time: Time }[],
  upper: number,
  sessionStartSec: number,
): { time: Time; value: number }[] {
  if (!(upper > 0) || !candles.length) return []
  const startBucket = sessionStartSec
  return candles
    .filter((c) => (c.time as number) >= startBucket)
    .map((c) => ({ time: c.time, value: upper }))
}

export function markFeedAgeSec(lastWsAtMs: number, nowMs = Date.now()): number | null {
  if (!(lastWsAtMs > 0)) return null
  return Math.max(0, Math.floor((nowMs - lastWsAtMs) / 1000))
}

/** Fractional distance between band midpoint and live mark (matches backend). */
export function bandMidDeviationPct(
  generatorUpper: number,
  generatorLower: number,
  markPrice: number,
): number {
  const upper = Number(generatorUpper)
  const lower = Number(generatorLower)
  const mark = Number(markPrice)
  if (!(mark > 0) || !(lower < upper)) return 0
  const mid = (upper + lower) / 2
  return Math.abs(mid - mark) / mark
}

/** True when the stored grid band belongs to the symbol's current mark. */
export function bandMatchesMark(
  generatorUpper: number,
  generatorLower: number,
  markPrice: number,
  maxMidDeviationPct = 0.35,
): boolean {
  const hi = Number(generatorUpper)
  const lo = Number(generatorLower)
  const mark = Number(markPrice)
  if (!(mark > 0) || !(lo < hi)) return false
  return bandMidDeviationPct(hi, lo, mark) <= maxMidDeviationPct
}
