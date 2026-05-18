"""Hub must switch chart focus when PATCH contains only ``symbol``."""

from __future__ import annotations

import asyncio

from backend.api.bot_hub import BotHub


def test_merge_state_symbol_only_updates_focus() -> None:
    hub = BotHub()

    async def run() -> None:
        await hub.replace_state({"symbol": "ETHUSDT", "generatorCount": 5})
        assert hub.last_focus_symbol == "ETHUSDT"
        merged = await hub.merge_state({"symbol": "DOGEUSDT"})
        assert hub.last_focus_symbol == "DOGEUSDT"
        assert merged.get("symbol") == "DOGEUSDT"
        assert hub.state.get("symbol") == "DOGEUSDT"

    asyncio.run(run())
