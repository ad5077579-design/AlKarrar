"""
Virtual spot grid: grid lines live in RAM/DB only — no pre-placed LIMIT orders on Binance.

Triggers on mark cross; execution is LIMIT+IOC by default (or MARKET only if env overrides).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from backend.main_engine import Side

Bucket = Literal["all", "lower", "upper"]


class ExecOrderStyle(str, Enum):
    MARKET = "MARKET"
    LIMIT_IOC = "LIMIT_IOC"


@dataclass
class VirtualGridLine:
    line_index: int
    price: float
    price_s: str
    qty_s: str
    side: Side
    bucket: Bucket = "all"
    armed: bool = True
    triggered: bool = False
    created_ms: int = 0


@dataclass
class ExecutionThrottle:
    """Per-symbol rate limit for exchange fires (not for RAM tick work)."""

    min_interval_s: float = 0.25
    max_per_window: int = 12
    window_s: float = 10.0
    _last_fire: float = 0.0
    _window_start: float = 0.0
    _window_count: int = 0

    def allow(self) -> bool:
        now = time.monotonic()
        if now - self._window_start > self.window_s:
            self._window_start = now
            self._window_count = 0
        if self._window_count >= self.max_per_window:
            return False
        if now - self._last_fire < self.min_interval_s:
            return False
        self._last_fire = now
        self._window_count += 1
        return True


def grid_exec_settings() -> dict[str, Any]:
    style = str(os.getenv("ALKARRAR_GRID_EXEC_ORDER_TYPE", "LIMIT_IOC")).strip().upper()
    if style not in ("MARKET", "LIMIT_IOC"):
        style = "LIMIT_IOC"
    try:
        slip = float(os.getenv("ALKARRAR_GRID_MAX_SLIPPAGE_PCT", "0.008"))
    except ValueError:
        slip = 0.008
    try:
        min_iv = float(os.getenv("ALKARRAR_GRID_EXEC_MIN_INTERVAL_S", "0.25"))
    except ValueError:
        min_iv = 0.25
    return {
        "order_style": ExecOrderStyle(style),
        "max_slippage_pct": max(0.0, slip),
        "throttle": ExecutionThrottle(
            min_interval_s=max(0.05, min_iv),
            max_per_window=int(os.getenv("ALKARRAR_GRID_EXEC_BURST", "12") or 12),
        ),
    }


@dataclass
class VirtualGridBook:
    """Local order book for one symbol grid session."""

    symbol: str
    lines: dict[int, VirtualGridLine] = field(default_factory=dict)
    throttle: ExecutionThrottle = field(default_factory=ExecutionThrottle)
    max_slippage_pct: float = 0.008
    order_style: ExecOrderStyle = ExecOrderStyle.LIMIT_IOC
    executions: int = 0

    @classmethod
    def from_env(cls, symbol: str) -> VirtualGridBook:
        cfg = grid_exec_settings()
        return cls(
            symbol=symbol.upper().replace("/", ""),
            throttle=cfg["throttle"],
            max_slippage_pct=float(cfg["max_slippage_pct"]),
            order_style=cfg["order_style"],
        )

    def armed_count(self) -> int:
        return sum(1 for ln in self.lines.values() if ln.armed and not ln.triggered)

    def register(
        self,
        *,
        line_index: int,
        price: float,
        price_s: str,
        qty_s: str,
        side: Side,
        bucket: Bucket = "all",
    ) -> VirtualGridLine:
        ln = VirtualGridLine(
            line_index=int(line_index),
            price=float(price),
            price_s=str(price_s),
            qty_s=str(qty_s),
            side=side,
            bucket=bucket,
            armed=True,
            triggered=False,
            created_ms=int(time.time() * 1000),
        )
        self.lines[line_index] = ln
        return ln

    def disarm_line(self, line_index: int) -> None:
        ln = self.lines.get(line_index)
        if ln:
            ln.armed = False

    def disarm_bucket(self, bucket: Bucket, *, lo: float, hi: float, span: float) -> list[int]:
        """Remove lower-band virtual lines (grid lift). Returns disarmed indices."""
        removed: list[int] = []
        for idx, ln in list(self.lines.items()):
            if ln.triggered or not ln.armed:
                continue
            if bucket == "lower" and ln.bucket in ("lower", "all"):
                if abs(ln.price - lo) <= span * 0.08:
                    ln.armed = False
                    removed.append(idx)
            elif bucket == "upper" and ln.bucket in ("upper", "all"):
                if abs(ln.price - hi) <= span * 0.08:
                    ln.armed = False
                    removed.append(idx)
        return removed

    def slippage_ok(self, line: VirtualGridLine, mark: float) -> bool:
        if not (mark > 0 and line.price > 0):
            return False
        slip = abs(mark - line.price) / line.price
        return slip <= self.max_slippage_pct

    def crossed_lines(self, prev_mark: float, mark: float, *, first_side: Side) -> list[VirtualGridLine]:
        """
        Grid-style cross: BUY fires when mark moves down through line; SELL when up through line.
        Uses strict crossing (prev on one side, mark on trigger side) to avoid repeat fires.
        """
        if not (prev_mark > 0 and mark > 0):
            return []
        out: list[VirtualGridLine] = []
        for ln in self.lines.values():
            if not ln.armed or ln.triggered:
                continue
            p = ln.price
            if ln.side == Side.BUY:
                if prev_mark > p >= mark:
                    out.append(ln)
            else:
                if prev_mark < p <= mark:
                    out.append(ln)
        out.sort(key=lambda x: abs(x.price - mark))
        return out

    def to_snapshot_rows(self) -> list[dict[str, Any]]:
        return [
            {
                "line_index": ln.line_index,
                "price": ln.price,
                "price_s": ln.price_s,
                "qty_s": ln.qty_s,
                "side": ln.side.value,
                "bucket": ln.bucket,
                "armed": ln.armed,
                "triggered": ln.triggered,
            }
            for ln in sorted(self.lines.values(), key=lambda x: x.line_index)
        ]

    def load_snapshot_rows(self, rows: list[dict[str, Any]]) -> None:
        self.lines.clear()
        for row in rows:
            if not isinstance(row, dict):
                continue
            idx = int(row.get("line_index", -1))
            if idx < 0:
                continue
            side = Side.BUY if str(row.get("side", "buy")).lower() != "sell" else Side.SELL
            self.lines[idx] = VirtualGridLine(
                line_index=idx,
                price=float(row.get("price", 0)),
                price_s=str(row.get("price_s", "")),
                qty_s=str(row.get("qty_s", "")),
                side=side,
                bucket=row.get("bucket", "all"),  # type: ignore[arg-type]
                armed=bool(row.get("armed", True)),
                triggered=bool(row.get("triggered", False)),
            )
