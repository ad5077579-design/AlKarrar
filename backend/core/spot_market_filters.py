"""
Spot symbol filters for grid trading — stablecoins and fiat-like bases are unsuitable
(نطاق سعري ضيق جداً، لا منطق شبكة ذو معنى).
"""

from __future__ import annotations

# عملات مستقرة / نقدية — لا تُعرض في قائمة الأسواق ولا يُنصح بشبكة عليها
NON_GRID_STABLE_BASE_ASSETS: frozenset[str] = frozenset(
    {
        "USDT",
        "USDC",
        "BUSD",
        "FDUSD",
        "TUSD",
        "DAI",
        "USDP",
        "USDD",
        "AEUR",
        "EURI",
        "EUR",
        "GBP",
        "TRY",
        "BIDR",
        "NGN",
        "RUB",
        "AUD",
        "BRL",
        "UAH",
        "ZAR",
        "PLN",
        "RON",
        "ARS",
        "MXN",
        "CZK",
        "JPY",
        "IDR",
        "VAI",
        "UST",
        "SUSD",
        "GUSD",
        "LUSD",
        "FRAX",
        "PAX",
        "BKRW",
        "BVND",
        "IDRT",
    }
)


def normalize_symbol(sym: str) -> str:
    return sym.strip().upper().replace("/", "")


def base_asset_from_symbol(symbol: str, quote: str = "USDT") -> str:
    s = normalize_symbol(symbol)
    q = quote.strip().upper()
    if s.endswith(q) and len(s) > len(q):
        return s[: -len(q)]
    return s


def is_grid_tradable_base(base_asset: str) -> bool:
    return base_asset.strip().upper() not in NON_GRID_STABLE_BASE_ASSETS


def is_grid_tradable_symbol(symbol: str, quote: str = "USDT") -> bool:
    return is_grid_tradable_base(base_asset_from_symbol(symbol, quote))


def list_excluded_stable_usdt_pairs(symbols: list[dict]) -> list[str]:
    """From exchangeInfo-like rows with baseAsset + symbol."""
    out: list[str] = []
    for row in symbols:
        if not isinstance(row, dict):
            continue
        base = str(row.get("baseAsset") or "").upper()
        sym = str(row.get("symbol") or "").upper()
        if base in NON_GRID_STABLE_BASE_ASSETS and sym.endswith("USDT"):
            out.append(sym)
    return sorted(out)
