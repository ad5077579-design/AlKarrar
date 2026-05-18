"""
تصفير البيانات المحلية: alkarrar.db (صفقات، أوامر، سجل عمليات، إعدادات محفوظة)
و trader.db (لقطات الشبكة) وملفات data/logs.

لا يمسّ .env ولا مفاتيح Binance على المنصة.

الاستخدام (أوقف API أولاً إن أمكن):
  python scripts/reset_local_data.py
  python scripts/reset_local_data.py --yes   # بدون تأكيد تفاعلي
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.project_paths import data_dir, project_root  # noqa: E402


def _remove_sqlite_files(base: Path) -> list[str]:
    removed: list[str] = []
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = Path(f"{base}{suffix}") if suffix else base
        if p.exists():
            p.unlink()
            removed.append(p.name)
    return removed


async def _recreate_alkarrar_schema() -> None:
    from backend.database import init_db

    await init_db()


async def _clear_trader_db() -> None:
    from backend.strategies.alkarrar_pro_shifting_grid import _ensure_shifting_grid_table

    path = data_dir() / "trader.db"
    _remove_sqlite_files(path)
    await _ensure_shifting_grid_table(path)


def _clear_log_dir() -> int:
    logs = data_dir() / "logs"
    n = 0
    if not logs.is_dir():
        return 0
    for child in logs.iterdir():
        if child.name == ".gitkeep":
            continue
        if child.is_file():
            child.unlink()
            n += 1
        elif child.is_dir():
            shutil.rmtree(child)
            n += 1
    return n


def _reset_trailing_risk() -> None:
    try:
        from backend.api.portfolio_risk import reset_trailing_equity_baseline

        reset_trailing_equity_baseline(0.0)
    except Exception:
        pass


async def run(*, skip_confirm: bool) -> None:
    data = data_dir()
    alk = data / "alkarrar.db"
    trader = data / "trader.db"

    print("AlKarrar Pro - reset local data")
    print(f"  data dir: {data}")
    print(f"  alkarrar.db: exists={alk.exists()} size={alk.stat().st_size if alk.exists() else 0}")
    print(f"  trader.db: exists={trader.exists()} size={trader.stat().st_size if trader.exists() else 0}")
    print("  .env is NOT modified")

    if not skip_confirm:
        ans = input("\nContinue? type yes: ").strip().lower()
        if ans not in ("yes", "y"):
            print("Cancelled.")
            return

    removed_main = _remove_sqlite_files(alk)
    removed_trader = _remove_sqlite_files(trader)
    log_files = _clear_log_dir()

    await _recreate_alkarrar_schema()
    await _clear_trader_db()
    _reset_trailing_risk()

    print("\nDone:")
    if removed_main:
        print(f"  alkarrar.db removed ({', '.join(removed_main)}), empty schema recreated")
    else:
        print("  alkarrar.db created empty")
    if removed_trader:
        print(f"  trader.db removed ({', '.join(removed_trader)})")
    print(f"  data/logs files removed: {log_files}")
    print("  trailing risk peak reset (current process only)")
    print("\nRestart: .\\restart_all.ps1")


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset local SQLite DBs and logs")
    parser.add_argument("--yes", "-y", action="store_true", help="بدون تأكيد")
    args = parser.parse_args()
    asyncio.run(run(skip_confirm=args.yes))


if __name__ == "__main__":
    main()
