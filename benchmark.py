# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Reproduce the report's shuffled, in-memory FIFO replay benchmark."""

from datetime import date, timedelta
from decimal import Decimal
from random import Random
from statistics import median
from time import perf_counter

from portfolio import Ledger, Trade, dollars


def benchmark(trade_count: int) -> tuple[float, Decimal]:
    """Time five replays of the same valid history; exclude data creation and I/O."""
    trades = []
    for pair in range(trade_count // 2):
        day = date(2020, 1, 1) + timedelta(days=pair // 100)
        first_id = 2 * pair + 1
        trades.append(
            Trade(first_id, day, "IBM", "BUY", Decimal(1), Decimal("100.00"), "Demo", Decimal(1))
        )
        trades.append(
            Trade(
                first_id + 1, day, "IBM", "SELL", Decimal(1), Decimal("104.00"), "Demo", Decimal(1)
            )
        )
    Random(7).shuffle(trades)
    elapsed = []
    for _ in range(5):
        start = perf_counter()
        ledger = Ledger(trades)
        elapsed.append(perf_counter() - start)
        if ledger.realised_by_currency["USD"] != Decimal(trade_count):
            raise AssertionError("The synthetic trades must yield two dollars per completed sale.")
    return median(elapsed) * 1000, ledger.realised_by_currency["USD"]


if __name__ == "__main__":
    for size in (1_000, 10_000, 50_000):
        milliseconds, gain = benchmark(size)
        print(f"{size:>6,} trades   {milliseconds:>8.1f} ms   realised P/L {dollars(gain)}")
