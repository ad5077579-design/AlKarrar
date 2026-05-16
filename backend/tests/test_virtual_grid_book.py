from backend.main_engine import Side
from backend.strategies.virtual_grid_book import VirtualGridBook


def test_buy_cross_down() -> None:
    book = VirtualGridBook(symbol="DOGEUSDT", max_slippage_pct=0.05)
    book.register(line_index=0, price=0.10, price_s="0.10", qty_s="100", side=Side.BUY)
    hit = book.crossed_lines(0.105, 0.099, first_side=Side.BUY)
    assert len(hit) == 1
    assert hit[0].line_index == 0


def test_sell_cross_up() -> None:
    book = VirtualGridBook(symbol="DOGEUSDT", max_slippage_pct=0.05)
    book.register(line_index=1, price=0.12, price_s="0.12", qty_s="50", side=Side.SELL)
    hit = book.crossed_lines(0.115, 0.121, first_side=Side.BUY)
    assert len(hit) == 1


def test_no_double_trigger() -> None:
    book = VirtualGridBook(symbol="DOGEUSDT")
    ln = book.register(line_index=0, price=1.0, price_s="1", qty_s="1", side=Side.BUY)
    ln.triggered = True
    assert book.crossed_lines(1.1, 0.9, first_side=Side.BUY) == []


def test_throttle() -> None:
    from backend.strategies.virtual_grid_book import ExecutionThrottle

    t = ExecutionThrottle(min_interval_s=10.0, max_per_window=1)
    assert t.allow()
    assert not t.allow()
