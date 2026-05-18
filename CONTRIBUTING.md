# Contributing to AlKarrar Pro

Thank you for helping improve an open-source Spot grid engine. This project handles **real money** on mainnet — we review changes carefully, especially around execution, risk, and credentials.

Repository: [github.com/ad5077579-design/AlKarrar](https://github.com/ad5077579-design/AlKarrar)

---

## Before you start

1. Read [README.md](README.md), [ARCHITECTURE.md](ARCHITECTURE.md), and [docs/TRADE_LOGIC.md](docs/TRADE_LOGIC.md).
2. Test on **Binance Spot Demo** or **testnet** — never commit API keys.
3. Run the test suite locally:

```bash
pip install -r requirements.txt
python -m pytest backend/tests -q
```

4. For env/key changes:

```bash
python scripts/probe_binance_env.py --no-cache
python scripts/health_check.py
```

---

## How to contribute

### 1. Open an issue (recommended)

- Bug reports: include env (`demo`/`mainnet`), symbol, steps, logs (redact keys).
- Features: describe **risk impact** and default behavior.

### 2. Fork and branch

```bash
git checkout -b feature/short-description
# or fix/issue-123-description
```

### 3. Make focused changes

- **One concern per PR** when possible (e.g. “ledger dedupe fix” separate from “UI tweak”).
- Match existing style; avoid drive-by refactors.
- Do not rename contractual JSON fields (see below).

### 4. Pull request checklist

- [ ] Tests added or updated for behavior changes
- [ ] `python -m pytest backend/tests -q` passes
- [ ] No secrets in code, logs, or commits
- [ ] `.env.example` updated if new environment variables
- [ ] README / ARCHITECTURE / TRADE_LOGIC updated if behavior changes
- [ ] PR description explains **why**, not only **what**

---

## Protected areas (extra review required)

Changes in these paths need maintainer attention:

| Area | Risk |
|------|------|
| `backend/strategies/alkarrar_pro_shifting_grid.py` | Live order placement |
| `backend/api/grid_runner.py`, `grid_manager.py` | Lifecycle, allocation |
| `backend/api/emergency_service.py`, `portfolio_risk.py` | Capital loss |
| `backend/api/credential_resolver.py`, `binance_key_probe.py` | Wrong exchange host |
| `backend/api/spot_realized_ledger.py` → `validate_grid_economics` | Must not weaken guards |
| `backend/api/grid_recovery.py`, `grid_snapshot_store.py` | Resume on wrong env |

**Do not:**

- Lower `MIN_USDT_PER_LINE` or spacing limits without discussion.
- Disable auto-resume env/key checks by default.
- Log full API keys or listen keys.

---

## Contract field names (do not rename)

Dashboard, API, and DB use stable keys:

`generatorUpper`, `generatorLower`, `generatorCount`, `initialCapital`, `allocatedCapital`, `binanceApiKey`, `binanceApiSecret`, `binanceTestnet`, `bot_id`, `binanceEnv`.

Display-only: `exchangeTestnet`. Breaking these breaks the Nuxt client and saved settings.

---

## Coding guidelines

### Python (backend)

- Type hints on new public functions.
- Async I/O for exchange calls; no blocking the event loop in hot paths.
- Use `BinanceSpotClient.create_for_env(env=...)` — never hardcode `testnet=True` with demo keys.
- Prefer structured logging (`logging.getLogger(__name__)`).

### TypeScript / Vue (frontend)

- Pinia store is the single WS truth — avoid duplicate WS clients.
- Keep proxy config in `nuxt.config.ts` for local dev.

### Tests

- Put tests under `backend/tests/test_*.py`.
- Mock exchange calls; no live keys in CI (future GitHub Actions should use mocks only).

---

## Commit messages

Use clear, imperative subjects:

- `fix(grid): prevent duplicate TRAILING_ARM without BUY fill`
- `docs: add trade logic section on bootstrap`
- `feat(probe): cache env detection per fingerprint`

---

## Code of conduct

Be respectful and constructive. Harassment or spam will not be tolerated.

---

## New strategies (plugins)

You are **encouraged** to add alternative strategies:

- Inherit `BaseStrategy` (`backend/strategies/base_strategy.py`).
- Add tests with mocked Binance responses.
- Document behavior with a mermaid diagram in `docs/`.
- See **[docs/PLUGINS.md](docs/PLUGINS.md)** and **[docs/STRATEGY_DIAGRAMS.md](docs/STRATEGY_DIAGRAMS.md)**.

The default runner wires `alkarrar_pro_shifting_grid` today; PRs that add a `strategy_key` registry without breaking existing bots are especially welcome.

## Questions

Open a [GitHub Discussion](https://github.com/ad5077579-design/AlKarrar/discussions) or issue if unsure about a change. When in doubt, ask before implementing large strategy changes.
