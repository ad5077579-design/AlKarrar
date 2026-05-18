"""PATCH /settings must accept symbol-only body (sidebar symbol switch)."""

from backend.api.routers.dashboard import DashboardSettingsPatch


def test_dashboard_settings_patch_symbol_only() -> None:
    body = DashboardSettingsPatch(symbol="btcusdt")
    assert body.symbol == "BTCUSDT"
