# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Add more illustrative holdings to the bundled demo portfolio.

Everything written here is invented sample data, not real prices or dividends. Prices are a seeded
random walk that ends at a stated level, dividends follow a fixed schedule that grows each year,
and trades happen on days that have a close. Existing demo records are never changed, and a
symbol that is already present is skipped, so the script is safe to run twice.

    python scripts/make_demo_data.py
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "assets"
START, END = date(2021, 12, 31), date(2026, 11, 16)  # history window of the new series
RECEIVED_UNTIL = date(2026, 11, 22)  # payments dated later are Expected
LAST_EXPECTED = date(2027, 12, 31)
WITHHOLDING = Decimal("0.15")  # tax on US dividends, as in the existing demo payments

# symbol, name, exchange, kind, currency, sector, final price, drift %/yr, volatility %/yr
STOCKS = [
    ("KO", "The Coca-Cola Company", "NYSE", "EQUITY", "USD", "Consumer Defensive", 71.40, 6, 15),
    ("PEP", "PepsiCo, Inc.", "NasdaqGS", "EQUITY", "USD", "Consumer Defensive", 148.90, 1, 16),
    ("PG", "The Procter & Gamble Company", "NYSE", "EQUITY", "USD", "Consumer Defensive", 163.20, 3, 15),
    ("JPM", "JPMorgan Chase & Co.", "NYSE", "EQUITY", "USD", "Financial Services", 281.60, 17, 24),
    ("XOM", "Exxon Mobil Corporation", "NYSE", "EQUITY", "USD", "Energy", 112.30, 8, 22),
    ("O", "Realty Income Corporation", "NYSE", "REIT", "USD", "Real Estate", 57.80, 1, 19),
    ("HD", "The Home Depot, Inc.", "NYSE", "EQUITY", "USD", "Consumer Cyclical", 392.50, 9, 21),
    ("VOO", "Vanguard S&P 500 ETF", "NYSE", "ETF", "USD", "", 618.30, 14, 16),
    ("D05.SI", "DBS Group Holdings Ltd", "SES", "EQUITY", "SGD", "Financial Services", 49.60, 16, 18),
    ("C38U.SI", "CapitaLand Integrated Commercial Trust", "SES", "REIT", "SGD", "Real Estate", 2.17, 4, 15),
    ("SBUX", "Starbucks Corporation", "NasdaqGS", "EQUITY", "USD", "Consumer Cyclical", 96.40, 1, 27),
]

# symbol: (rate per payout in 2026, yearly growth, [(ex month, ex day)], days from ex-date to pay date)
DIVIDENDS = {
    "KO": ("0.51", 0.045, [(3, 14), (6, 13), (9, 14), (11, 28)], 17),
    "PEP": ("1.4225", 0.07, [(3, 6), (6, 5), (9, 5), (12, 5)], 28),
    "PG": ("1.0568", 0.05, [(1, 23), (4, 21), (7, 21), (10, 20)], 21),
    "JPM": ("1.50", 0.09, [(1, 5), (4, 6), (7, 6), (10, 5)], 25),
    "XOM": ("1.03", 0.04, [(2, 12), (5, 14), (8, 14), (11, 13)], 19),
    "O": ("0.2705", 0.02, [(m, 1) for m in range(1, 13)], 14),
    "HD": ("2.33", 0.07, [(3, 12), (6, 12), (9, 11), (12, 4)], 21),
    "VOO": ("1.82", 0.06, [(3, 27), (6, 26), (9, 26), (12, 22)], 4),
    "D05.SI": ("0.60", 0.10, [(2, 12), (5, 12), (8, 11), (11, 10)], 14),
    "C38U.SI": ("0.0590", 0.02, [(3, 6), (9, 4)], 21),
    "SBUX": ("0.61", 0.05, [(2, 13), (5, 15), (8, 14), (11, 14)], 14),
}

# symbol: broker, [(date, side, quantity)]
TRADES = {
    "KO": ("Syfe", [("2025-02-10", "BUY", 40), ("2025-09-22", "BUY", 25)]),
    "PEP": ("moomoo", [("2025-03-03", "BUY", 15), ("2026-03-16", "BUY", 10)]),
    "PG": ("IBKR", [("2025-01-21", "BUY", 20), ("2026-06-08", "SELL", 8)]),
    "JPM": ("IBKR", [("2025-04-07", "BUY", 12), ("2025-10-13", "BUY", 6)]),
    "XOM": ("moomoo", [("2025-05-12", "BUY", 30), ("2026-08-17", "SELL", 10)]),
    "O": ("Syfe", [("2025-02-03", "BUY", 60), ("2026-01-12", "BUY", 40)]),
    "HD": ("IBKR", [("2025-06-02", "BUY", 6)]),
    "VOO": ("Syfe", [("2025-01-06", "BUY", 12), ("2025-07-07", "BUY", 8), ("2026-02-02", "BUY", 6)]),
    "D05.SI": ("Syfe", [("2025-03-17", "BUY", 200), ("2026-04-13", "BUY", 100)]),
    "C38U.SI": ("Syfe", [("2025-08-18", "BUY", 2000)]),
    "SBUX": ("moomoo", [("2025-11-17", "BUY", 25)]),
}


def weekdays(first, last):
    day = first
    while day <= last:
        if day.weekday() < 5:
            yield day
        day += timedelta(days=1)


def next_weekday(day):
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def closes_for(symbol, final, drift, vol):
    """A seeded random walk that is pinned to end exactly at `final`."""
    seed = int(hashlib.sha256(symbol.encode()).hexdigest()[:12], 16)
    rng = random.Random(seed)
    days = list(weekdays(START, END))
    sigma = vol / 100 / math.sqrt(252)
    steps = [rng.gauss(0, sigma) for _ in days[1:]]
    walk = [0.0]
    for s in steps:
        walk.append(walk[-1] + s)
    years = (END - START).days / 365.25
    target = drift / 100 * years  # intended total log return
    n = len(walk) - 1
    # Brownian bridge to the target, plus a slow wave so charts are not a straight trend.
    path = [w - walk[-1] * i / n + target * i / n for i, w in enumerate(walk)]
    log_start = math.log(final) - target
    digits = 3 if final < 10 else 2
    return {d: round(math.exp(log_start + p), digits) for d, p in zip(days, path)}


def money(value, digits=4):
    text = f"{Decimal(str(value)).quantize(Decimal(1).scaleb(-digits), ROUND_HALF_UP):f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def rate_for(symbol, year):
    base, growth, _, _ = DIVIDENDS[symbol]
    return Decimal(base) / Decimal(str((1 + growth) ** (2026 - year)))


def payout_days(symbol, last):
    _, _, schedule, lag = DIVIDENDS[symbol]
    for year in range(2022, last.year + 1):
        for month, day in schedule:
            ex = next_weekday(date(year, month, day))
            yield ex, next_weekday(ex + timedelta(days=lag))


def main():
    trades_file = ASSETS / "demo_transactions.json"
    divs_file = ASSETS / "demo_dividends.json"
    quotes_file = ASSETS / "demo_quotes.json"
    hist_file = ASSETS / "demo_history.json"
    trades, divs = json.loads(trades_file.read_text()), json.loads(divs_file.read_text())
    quotes, hist = json.loads(quotes_file.read_text()), json.loads(hist_file.read_text())
    known = {s["key"] for s in hist["series"]}
    added = []
    for symbol, name, exchange, kind, currency, sector, final, drift, vol in STOCKS:
        if symbol in known:
            continue
        closes = closes_for(symbol, final, drift, vol)
        digits = 3 if final < 10 else 2
        payouts = [
            [ex.isoformat(), money(rate_for(symbol, ex.year)), currency]
            for ex, _ in payout_days(symbol, END)
            if ex <= END and ex >= START
        ]
        hist["series"].append(
            {
                "key": symbol,
                "currency": currency,
                "source": "Demo history",
                "start": START.isoformat(),
                "checked": END.isoformat(),
                "sector": sector,
                "closes": {d.isoformat(): f"{p:.{digits}f}" for d, p in closes.items()},
                "payouts": payouts,
                "splits": [],
            }
        )
        quotes["quotes"].append(
            {
                "symbol": symbol,
                "price": f"{closes[END]:.{digits}f}",
                "asof": "2026-11-16T21:00:00+00:00",
                "source": "Demo snapshot",
                "currency": currency,
                "listing": {
                    "symbol": symbol,
                    "name": name,
                    "exchange": exchange,
                    "kind": kind,
                    "currency": currency,
                    "price_unit": currency,
                },
            }
        )
        broker, lots = TRADES[symbol]
        held = []  # (date, signed quantity) for dividend sizing
        for day, side, qty in lots:
            when = date.fromisoformat(day)
            price = closes[when]
            fee = max(1.0, round(qty * price * 0.0008, 2))
            row = {
                "id": trades["next_id"],
                "date": day,
                "symbol": symbol,
                "side": side,
                "quantity": str(qty),
                "price": f"{price:.{digits}f}",
                "broker": broker,
                "fee": f"{fee:.2f}",
            }
            if currency != "USD":
                row["currency"] = currency
            trades["next_id"] += 1
            trades["trades"].append(row)
            held.append((when, qty if side == "BUY" else -qty))
        for ex, pay in payout_days(symbol, LAST_EXPECTED):
            if pay > LAST_EXPECTED:
                continue
            qty = sum(q for d, q in held if d < ex)
            if qty <= 0:
                continue
            gross = Decimal(money(rate_for(symbol, ex.year))) * qty
            tax = (gross * WITHHOLDING).quantize(Decimal("0.01")) if currency == "USD" else Decimal(0)
            divs["payments"].append(
                {
                    "id": divs["next_id"],
                    "symbol": symbol,
                    "date": pay.isoformat(),
                    "currency": currency,
                    "gross": f"{gross.quantize(Decimal('0.01'), ROUND_HALF_UP):f}",
                    "tax": f"{tax:f}",
                    "status": "Received" if pay <= RECEIVED_UNTIL else "Expected",
                    "ex_date": ex.isoformat(),
                    "broker": broker,
                }
            )
            divs["next_id"] += 1
        added.append(symbol)
    trades["trades"].sort(key=lambda t: (t["date"], t["id"]))
    divs["payments"].sort(key=lambda p: (p["date"], p["id"]))
    for path, data in (
        (trades_file, trades),
        (divs_file, divs),
        (quotes_file, quotes),
        (hist_file, hist),
    ):
        path.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False) + "\n")
    print("Added:", ", ".join(added) or "nothing (already present)")


if __name__ == "__main__":
    main()
