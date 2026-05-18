# Custom strategies (plugins)

**Yes — you can add a new strategy.** The engine defines a clear plugin contract; the default production path today is **AlKarrar Shifting Grid**, but we welcome well-tested alternatives.

---

## Contract: `BaseStrategy`

File: `backend/strategies/base_strategy.py`

| Method | When called |
|--------|-------------|
| `on_start(bot_id, settings)` | Grid start — validate config, init state |
| `on_tick(bot_id, market)` | Each mark update (`price`, `deploy_usdt`, `realized_delta`, …) |
| `on_stop(bot_id)` | Grid stop — cleanup |

Your class should set `name = "your_strategy_id"` (stable string).

---

## How to contribute a new strategy

1. **Fork** [AlKarrar](https://github.com/ad5077579-design/AlKarrar).
2. Add `backend/strategies/your_strategy.py` inheriting `BaseStrategy`.
3. Export in `backend/strategies/__init__.py`.
4. Wire `GridRunner` (or a sibling runner) to instantiate your class — today `grid_runner.py` uses `AlKarrarProShiftingGridStrategy`; a PR can add selection via `bot_settings.strategy_key`.
5. **Tests** under `backend/tests/` with mocked exchange (no live keys).
6. **Docs**: short section in `docs/TRADE_LOGIC.md` or a new `docs/YOUR_STRATEGY.md` with a mermaid diagram.
7. Open a PR — see [CONTRIBUTING.md](../CONTRIBUTING.md).

---

## What we expect from a new strategy PR

- Respects `validate_grid_economics` (or equivalent guards) before placing orders.
- No secrets in code; uses `BinanceSpotClient.create_for_env`.
- Documented risk behavior (max deploy, emergency stop interaction).
- Optional: snapshot support for auto-resume (see `grid_snapshot_store.py`).

---

## Ideas the community might build

- Mean-reversion grid without band shift  
- DCA-only ladder (no virtual book)  
- Multi-timeframe filter before firing lines  
- Paper-sim mode without exchange calls  

Discuss in [GitHub Discussions](https://github.com/ad5077579-design/AlKarrar/discussions) before large rewrites.

---

## Current wiring (reference)

```text
grid_manager.start()
  → GridRunner.start()
    → AlKarrarProShiftingGridStrategy.on_start()
    → on_mark() → strategy.on_tick()
```

A future `strategy_key` switch might look like:

```python
# illustrative only
STRATEGIES = {
    "alkarrar_pro_shifting_grid": AlKarrarProShiftingGridStrategy,
    "your_strategy": YourStrategy,
}
```

**Pull requests that add this registry cleanly are encouraged.**
