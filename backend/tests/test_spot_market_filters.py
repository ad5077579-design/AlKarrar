from backend.core.spot_market_filters import (
    is_grid_tradable_base,
    is_grid_tradable_symbol,
    list_excluded_stable_usdt_pairs,
)


def test_stable_bases_not_tradable() -> None:
    assert not is_grid_tradable_base("USDC")
    assert not is_grid_tradable_symbol("USDCUSDT")
    assert is_grid_tradable_symbol("DOGEUSDT")


def test_list_excluded_from_rows() -> None:
    rows = [
        {"symbol": "USDCUSDT", "baseAsset": "USDC", "status": "TRADING"},
        {"symbol": "DOGEUSDT", "baseAsset": "DOGE", "status": "TRADING"},
    ]
    assert list_excluded_stable_usdt_pairs(rows) == ["USDCUSDT"]
