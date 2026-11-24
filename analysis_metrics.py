# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Group dividend cash, project recorded payouts, and compare cash-flow-aware returns."""

from bisect import bisect_left, bisect_right
from calendar import monthrange
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from dividends import income_estimates, net_income
from performance import fx_key, performance, period_growth
from portfolio import Ledger, ZERO, cash
from valuation import SGD, complete_sum, in_sgd, market_name

GROUPINGS = ("Holdings", "Asset classes", "Sectors", "Markets", "Currencies")
PAYOUT_SOURCES = ("Current-share estimate", "Received past 12m", "Expected next 12m")
BENCHMARKS = {
    "S&P 500 ETF · SPY": ("SPY", "USD"),
    "Straits Times ETF · ES3.SI": ("ES3.SI", SGD),
}
RANGES = (
    "1 day",
    "7 days",
    "1 month",
    "3 months",
    "6 months",
    "YTD",
    "1 year",
    "5 years",
    "Since inception",
)


def shift_month(day, count):
    year, month = divmod(day.year * 12 + day.month - 1 + count, 12)
    return date(year, month + 1, min(day.day, monthrange(year, month + 1)[1]))


def month_keys(first, last):
    start = first.replace(day=1)
    while start <= last:
        yield start
        start = shift_month(start, 1)


def group_label(symbol, by, currency, quotes, histories):
    quote = quotes.get(symbol)
    listing = quote.listing if quote else None
    if by == "Holdings":
        return symbol
    if by == "Currencies":
        return currency
    if by == "Markets":
        return market_name(quote)
    if by == "Sectors":
        series = histories.get(symbol)
        return (series.sector if series else "") or "Unclassified"
    if by == "Asset classes":
        if not listing:
            return "Unclassified"
        return {"EQUITY": "Equity", "ETF": "ETF", "REIT": "REIT"}[listing.kind]
    raise ValueError("Unknown grouping")


@dataclass(frozen=True)
class YieldGroup:
    label: str
    payout: Decimal | None
    value: Decimal | None
    basis: Decimal | None
    covered: int
    holdings: int

    def percentage(self, base):
        return self.payout * 100 / base if self.payout is not None and base else None


def yield_groups(
    book, payments, histories, quotes, rates, today, by, source=PAYOUT_SOURCES[0]
):
    if source == PAYOUT_SOURCES[0]:
        estimates, events = income_estimates(book, payments, histories, rates, today)
    else:
        rows = defaultdict(list)
        start, end = shift_month(today, -12), shift_month(today, 12)
        for p in payments:
            received = (
                source == PAYOUT_SOURCES[1] and p.status == "Received" and start < p.day <= today
            )
            expected = (
                source == PAYOUT_SOURCES[2] and p.status == "Expected" and today <= p.day < end
            )
            if p.symbol in book.positions and (received or expected):
                rows[p.symbol].append(p)
        estimates = {s: net_income(group, rates) for s, group in rows.items()}
        events = sum(map(len, rows.values()))
    members = defaultdict(list)
    for symbol, position in book.positions.items():
        label = group_label(symbol, by, position.currency, quotes, histories)
        quote = quotes.get(symbol)
        value = None
        if quote and quote.currency == position.currency:
            value = in_sgd(
                cash(position.qty * quote.price, position.currency), position.currency, rates
            )
        members[label].append((symbol, value, in_sgd(position.basis, position.currency, rates)))

    def combine(label, rows):
        receipts = [estimates[s] for s, _, _ in rows if s in estimates]
        return YieldGroup(
            label,
            complete_sum(receipts) if receipts else None,
            complete_sum(v for _, v, _ in rows),
            complete_sum(b for _, _, b in rows),
            len(receipts),
            len(rows),
        )

    groups = [combine(label, rows) for label, rows in members.items()]
    groups.sort(key=lambda row: (row.payout is None, -(row.payout or ZERO), row.label))
    return groups, combine("Portfolio", [r for rows in members.values() for r in rows]), events


def history_buckets(payments, rates, year, interval, status, label_for):
    matching = [d for d in payments if d.status == status]
    keys = list(range(1, 13)) if interval == "Monthly" else sorted({d.day.year for d in payments})
    groups = defaultdict(lambda: defaultdict(list))
    for d in matching:
        if interval == "Monthly" and d.day.year != year:
            continue
        groups[label_for(d)][d.day.month if interval == "Monthly" else d.day.year].append(d)
    return keys, {
        label: tuple(net_income(months[key], rates) for key in keys)
        for label, months in sorted(groups.items())
    }


def future_payments(payments, rates, day):
    end = shift_month(day, 12)
    dates = list(month_keys(day, end - timedelta(days=1)))
    bins = {m: [] for m in dates}
    for p in payments:
        if p.status == "Expected" and day <= p.day < end:
            bins[p.day.replace(day=1)].append(p)
    return end, bins, tuple(net_income(items, rates) for items in bins.values())


def range_start(label, today):
    if label == "Since inception":
        return None
    if label == "YTD":
        return today.replace(month=1, day=1)
    if label in ("1 day", "7 days"):
        return today - timedelta(days=int(label.split()[0]) - 1)
    n = int(label.split()[0])
    return shift_month(today, -n * (12 if "year" in label else 1)) + timedelta(days=1)


def monthly_returns(observations, cutoff=None):
    """Restart Dietz in each period, including the opening value before its first flow."""
    if not observations:
        return []
    days = [r.day for r in observations]
    start = max(days[0], cutoff or days[0])
    results = []
    for month in month_keys(start, days[-1]):
        a = max(month, start)
        b = min(shift_month(month, 1) - timedelta(days=1), days[-1])
        left, right = bisect_left(days, a), bisect_right(days, b)
        if left == right:
            results.append((month, None, None, None))
            continue
        group, opening_day = period_growth(observations[max(0, left - 1) : right], a)
        results.append((month, group[-1].return_pct, opening_day, group[-1].day))
    return results


def holding_series(book, payments, histories, today):
    trade_groups, dividends = defaultdict(list), defaultdict(list)
    for t in book.trades:
        trade_groups[t.symbol].append(t)
    for p in payments:
        dividends[p.symbol].append(p)
    return {
        s: performance(Ledger(trade_groups[s], book.method, [split for split in book.splits if split.symbol == s]), dividends[s], histories, today)[0]
        for s in book.positions
    }


def holding_returns(series, histories, period, today):
    result = []
    for symbol, rows in series.items():
        cutoff = range_start(period, today)
        if period == "1 day":
            hist = histories.get(symbol)
            sessions = hist.days[: bisect_right(hist.days, today)] if hist else []
            if len(sessions) < 2 or (today - sessions[-1]).days > 10:
                result.append((symbol, None))
                continue
            # An overnight move spans the two latest exchange closes. FX-only weekend
            # observations between them must not reset the opening valuation.
            rows = [r for r in rows if r.day <= sessions[-1]]
            cutoff = sessions[-2] + timedelta(days=1)
        points, _ = period_growth(rows, cutoff)
        result.append((symbol, points[-1].return_pct if points else None))
    return sorted(result, key=lambda x: (x[1] is None, -(x[1] or ZERO), x[0]))


def benchmark_return(histories, choice, opening, closing):
    if not opening or not closing or choice not in BENCHMARKS:
        return None
    symbol, ccy = BENCHMARKS[choice]
    h = histories.get(symbol)
    if not h or h.currency != ccy or any(opening < d <= closing for d in h.splits):
        return None
    fx = histories.get(fx_key(ccy)) if ccy != SGD else None
    prices = []
    for d in (opening, closing):
        p = h.at(d)
        rate = 1 if ccy == SGD else fx.at(d) if fx else None
        if not p or not rate:
            return None
        prices.append(p * rate)
    return (prices[1] / prices[0] - 1) * 100
