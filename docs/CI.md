# Continuous integration (GitHub Actions)

Every **push** to `main` and every **pull request** targeting `main` runs the workflow [`.github/workflows/ci.yml`](../.github/workflows/ci.yml).

## What runs in CI

| Step | Purpose |
|------|---------|
| **Financial guard suite** | `pytest -m financial` — execution safety, per-grid isolation, session ledger, auto-resume / env fingerprint, portfolio trailing, DB retention |
| **Full backend suite** | All tests under `backend/tests/` |

Tests use a **temporary SQLite database** (`ALKARRAR_DATA_DIR` set in `backend/tests/conftest.py`). No Binance API keys are required; exchange calls are mocked.

Safe defaults in CI:

- `ALKARRAR_GRID_BOOTSTRAP_MARKET=0` — no market bootstrap buy on grid start
- `ALKARRAR_AUTO_DETECT_BINANCE_ENV=false` — no live key probing

## Required status check (block bad merges)

To reject PRs automatically before manual review:

1. Open the repo on GitHub → **Settings** → **Branches** → **Branch protection rules** → **Add rule** (or edit `main`).
2. Branch name pattern: `main`
3. Enable **Require status checks to pass before merging**
4. Search and select: **`Backend tests (pytest)`**
5. Enable **Require branches to be up to date before merging** (recommended)
6. Save

Contributors will see a red ✗ on the PR if any test fails; merge stays disabled until CI is green.

## Run the same checks locally

```bash
pip install -r requirements.txt
python -m pytest backend/tests -m financial -q
python -m pytest backend/tests -q
```

## Adding tests for new strategies

- Place files under `backend/tests/test_*.py`
- Mock Binance; never commit keys
- Mark financial / risk tests with `@pytest.mark.financial` or `pytestmark = pytest.mark.financial`

See [CONTRIBUTING.md](../CONTRIBUTING.md) and [PLUGINS.md](PLUGINS.md).
