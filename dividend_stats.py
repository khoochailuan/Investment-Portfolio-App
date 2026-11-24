# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Dividend growth, upcoming events, portfolio insights and price-move explanations.

Plain Python with no UI imports, so the views and the unit tests share one set of calculations.
Per-share history comes from the cached Yahoo series (Series.payouts). Cash records come from
the PaymentBook. Nothing here fetches data.

Complexity (p = payment records, d = distributions, n = holdings):
  * annual_dps / dps_growth / growth_streak   O(d)
  * upcoming_events                           O(p + d)
  * monthly_by_year                           O(p)
  * build_insights                            O(n + p)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from analytics import day_moves, recent_closes
from dividends import net_income
from portfolio import ZERO, dollars, unit_price
from valuation import in_sgd

HUNDRED = Decimal(100)

INCOME_CONCENTRATION_LIMIT = Decimal(40)  # % of trailing income from one holding
DEAD_WEIGHT_LOSS = Decimal(10)  # open loss (%) that marks a holding as a candidate
MOVE_THRESHOLD = 3.0  # minimum absolute daily move shown on Overview
NEWS_WINDOW = timedelta(days=4)


# --------------------------------------------------------------------------- per-share growth


def annual_dps(series, today):
    """Dividends per share by calendar year, using complete years only.

    A year is complete when the cached history reaches back to 1 January of that year.
    Payouts in another currency than the listing are skipped rather than guessed.
    """
    if series is None:
        return {}
    totals = defaultdict(lambda: ZERO)
    for p in series.payouts:
        if p.currency not in (None, series.currency):
            continue
        totals[p.ex_day.year] += p.rate
    return {
        year: amount
        for year, amount in totals.items()
        if year < today.year and series.start <= date(year, 1, 1)
    }


def dps_growth(series, today, years=5):
    """Compound annual growth of dividends per share, as (percent, span in years) or None."""
    dps = annual_dps(series, today)
    usable = sorted(y for y, v in dps.items() if v > 0 and y >= today.year - 1 - years)
    if len(usable) < 2 or usable[-1] != today.year - 1:
        return None
    # Use the longest unbroken run that ends in the latest complete year.
    first = usable[-1]
    while first - 1 in usable:
        first -= 1
    span = usable[-1] - first
    if span < 1:
        return None
    ratio = float(dps[usable[-1]] / dps[first])
    return (ratio ** (1 / span) - 1) * 100, span


def growth_streak(series, today):
    """Consecutive complete years in which dividends per share rose, ending last year."""
    dps = annual_dps(series, today)
    year, streak = today.year - 1, 0
    while year in dps and year - 1 in dps and dps[year] > dps[year - 1] > 0:
        streak += 1
        year -= 1
    return streak


@dataclass(frozen=True)
class DividendFacts:
    symbol: str
    annual_per_share: Decimal | None  # last 12 months, per share, listing currency
    yield_pct: Decimal | None
    yield_on_cost: Decimal | None
    growth: tuple[float, int] | None
    streak: int
    next_ex: date | None
    next_pay: date | None
    frequency: str


def trailing_per_share(series, today):
    if series is None:
        return None
    start = today - timedelta(days=365)
    rows = [
        p.rate
        for p in series.payouts
        if start < p.ex_day <= today and p.currency in (None, series.currency)
    ]
    return sum(rows, ZERO) if rows else None


def frequency_label(series, today):
    if series is None:
        return "—"
    count = sum(1 for p in series.payouts if today - timedelta(days=365) < p.ex_day <= today)
    return {0: "None recorded", 1: "Annual", 2: "Semi-annual", 3: "Triannual", 4: "Quarterly"}.get(
        count, "Monthly" if count >= 10 else f"{count} a year"
    )


def dividend_facts(symbol, position, price, series, payments, today):
    """Dividend statistics for one holding. Missing inputs give None, never a guess."""
    annual = trailing_per_share(series, today)
    yield_pct = annual * HUNDRED / price if annual is not None and price else None
    avg = position.average if position else None
    on_cost = annual * HUNDRED / avg if annual is not None and avg else None
    future_ex = sorted(
        {p.ex_day for p in (series.payouts if series else ()) if p.ex_day >= today}
        | {d.ex_day for d in payments if d.symbol == symbol and d.ex_day and d.ex_day >= today}
    )
    future_pay = sorted(d.day for d in payments if d.symbol == symbol and d.day >= today)
    return DividendFacts(
        symbol,
        annual,
        yield_pct,
        on_cost,
        dps_growth(series, today),
        growth_streak(series, today),
        future_ex[0] if future_ex else None,
        future_pay[0] if future_pay else None,
        frequency_label(series, today),
    )


def growth_by_holding(book, histories, today):
    """(symbol, growth percent, span) for each open holding with enough history, best first."""
    rows = []
    for symbol in book.positions:
        g = dps_growth(histories.get(symbol), today)
        if g is not None:
            rows.append((symbol, g[0], g[1]))
    return sorted(rows, key=lambda r: (-r[1], r[0]))


# --------------------------------------------------------------------------- monthly comparison


def monthly_by_year(payments, rates, status="Received"):
    """{year: [SGD net for Jan..Dec]} and the set of years with an unconverted amount."""
    groups = defaultdict(list)
    for d in payments:
        if d.status == status:
            groups[d.day.year, d.day.month].append(d)
    out, incomplete = defaultdict(lambda: [ZERO] * 12), set()
    for (year, month), rows in groups.items():
        value = net_income(rows, rates)
        if value is None:
            incomplete.add(year)
        else:
            out[year][month - 1] = value
    return dict(out), incomplete


# --------------------------------------------------------------------------- events strip


@dataclass(frozen=True)
class Event:
    day: date
    kind: str  # "Ex-dividend date" or "Dividend payout"
    symbol: str
    detail: str
    expected: bool


def upcoming_events(book, payments, histories, today, back=7, ahead=60):
    """Recent and upcoming ex-dates and payouts for open holdings, soonest first."""
    start, end = today - timedelta(days=back), today + timedelta(days=ahead)
    events, seen_ex = [], set()
    for d in payments:
        if d.symbol not in book.positions:
            continue
        net = dollars(d.net, currency=d.currency)
        status = "expected" if d.status == "Expected" else "received"
        if d.ex_day and start <= d.ex_day <= end:
            events.append(Event(d.ex_day, "Ex-dividend date", d.symbol, f"{net} net, {status}", False))
            seen_ex.add((d.symbol, d.ex_day))
        if start <= d.day <= end:
            events.append(
                Event(d.day, "Dividend payout", d.symbol, f"{net} net, {status}", d.status == "Expected")
            )
    for symbol in book.positions:
        series = histories.get(symbol)
        for p in series.payouts if series else ():
            if start <= p.ex_day <= end and (symbol, p.ex_day) not in seen_ex:
                ccy = p.currency or series.currency
                events.append(
                    Event(p.ex_day, "Ex-dividend date", symbol, f"{unit_price(p.rate, ccy)}/share", False)
                )
    return sorted(events, key=lambda e: (e.day, e.kind, e.symbol))


# --------------------------------------------------------------------------- insights


@dataclass(frozen=True)
class Insight:
    key: str
    level: str  # "Warning" or "Info"
    title: str
    summary: str
    detail: tuple[str, ...]


def trailing_income_by_symbol(payments, rates, today):
    """Net SGD received in the last 12 months per symbol. Symbols with a missing rate are left out."""
    start = today - timedelta(days=365)
    totals = {}
    for d in payments:
        if d.status == "Received" and start < d.day <= today:
            value = in_sgd(d.net, d.currency, rates)
            if value is not None:
                totals[d.symbol] = totals.get(d.symbol, ZERO) + value
    return totals


def build_insights(view, risk, histories, payments, rates, today):
    """Concentration, dead weight and income-concentration cards from existing calculations."""
    cards = []
    if risk is not None:
        asset = [f for f in risk.flags if "of the portfolio" in f.title or "Top three" in f.title]
        if asset:
            cards.append(
                Insight(
                    "asset",
                    "Warning",
                    "Holdings are concentrated",
                    asset[0].title,
                    tuple(f"{f.title}. {f.detail}" for f in asset),
                )
            )
        spread = [f for f in risk.flags if "(sector)" in f.title or "(market)" in f.title]
        if spread:
            cards.append(
                Insight(
                    "sector",
                    "Warning",
                    "Sector or market concentration",
                    spread[0].title,
                    tuple(f"{f.title}. {f.detail}" for f in spread),
                )
            )
    weak = []
    for r in view.rows:
        if r.return_pct is None or r.return_pct > -DEAD_WEIGHT_LOSS:
            continue
        closes = recent_closes(histories.get(r.position.symbol), today)
        if len(closes) >= 20 and closes[-1][1] < closes[0][1]:
            change = (closes[-1][1] / closes[0][1] - 1) * 100
            weak.append((r.position.symbol, r.return_pct, change))
    if weak:
        weak.sort(key=lambda w: w[1])
        cards.append(
            Insight(
                "dead",
                "Warning",
                "Holdings below cost and still falling",
                f"{weak[0][0]} is {abs(weak[0][1]):.1f}% below cost",
                tuple(
                    f"{s}: {abs(open_):.1f}% below your cost and {abs(year):.1f}% lower over the past year."
                    for s, open_, year in weak
                ),
            )
        )
    income = trailing_income_by_symbol(payments, rates, today)
    total = sum(income.values(), ZERO)
    if total > 0:
        top = max(income, key=income.get)
        share = income[top] * HUNDRED / total
        if share > INCOME_CONCENTRATION_LIMIT:
            cards.append(
                Insight(
                    "income",
                    "Warning",
                    "Dividends concentrated in one holding",
                    f"{top} pays {share:.1f}% of trailing income",
                    (
                        f"{top} supplied {share:.1f}% of net dividends received in the last 12 months.",
                        f"Limit used: {INCOME_CONCENTRATION_LIMIT}% from one holding.",
                    ),
                )
            )
    return cards


# --------------------------------------------------------------------------- why is it moving


@dataclass(frozen=True)
class MoveReason:
    symbol: str
    pct: float
    headline: str | None
    source: str
    url: str | None


def move_reasons(view, histories, rates, feeds, now=None, threshold=MOVE_THRESHOLD):
    """Holdings that moved more than the threshold, with the newest saved headline for each."""
    now = now or datetime.now(timezone.utc)
    out = []
    for m in day_moves(view, histories, rates):
        if abs(m.pct) < threshold:
            continue
        best = None
        for a in feeds.get(m.symbol, {}).get("articles", []):
            try:
                when = datetime.fromisoformat(a["time"])
            except (KeyError, ValueError):
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if now - when <= NEWS_WINDOW and (best is None or when > best[0]):
                best = (when, a)
        article = best[1] if best else None
        out.append(
            MoveReason(
                m.symbol,
                m.pct,
                article["title"] if article else None,
                article["source"] if article else "",
                article.get("url") if article else None,
            )
        )
    return out


# --------------------------------------------------------------------------- dividends received


TRAILING, YEAR_TO_DATE, ALL_TIME = "12 trailing months", "Year to date", "All time"
INTERVALS = ("Monthly", "Quarterly", "Annual")


def _month_after(day, n):
    index = day.year * 12 + day.month - 1 + n
    return date(index // 12, index % 12 + 1, 1)


def period_months(payments, period, today):
    """First day of each month in the chosen period, oldest first."""
    if period == TRAILING:
        first, count = _month_after(today, -11), 12
    elif period == YEAR_TO_DATE:
        first, count = date(today.year, 1, 1), today.month
    elif period == ALL_TIME:
        days = [d.day for d in payments if d.status == "Received"]
        first = min(days).replace(day=1) if days else today.replace(day=1)
        count = (today.year - first.year) * 12 + today.month - first.month + 1
    else:
        first, count = date(int(period), 1, 1), 12
    return [_month_after(first, i) for i in range(max(1, count))]


def received_buckets(payments, rates, period, interval, today):
    """Return income chart labels, net cash per bucket and the matching payment records."""
    months = period_months(payments, period, today)
    lower, upper = months[0], min(today, _month_after(months[-1], 1) - timedelta(days=1))
    rows = [d for d in payments if d.status == "Received" and lower <= d.day <= upper]

    def bucket(day):
        if interval == "Annual":
            return str(day.year)
        if interval == "Quarterly":
            return f"Q{(day.month - 1) // 3 + 1} {day.year % 100:02d}"
        return f"{day:%b %y}"

    # Prepopulate empty buckets so timing and gaps remain visible on the chart.
    members = {bucket(month): [] for month in months}
    for row in rows:
        members[bucket(row.day)].append(row)
    return list(members), [net_income(group, rates) for group in members.values()], rows


def totals_by_group(payments, rates, status, label_for):
    """[(label, SGD net)] for every group, largest first. Nothing is folded into Other."""
    groups = defaultdict(list)
    for d in payments:
        if d.status == status:
            groups[label_for(d)].append(d)
    rows = [(k, net_income(v, rates)) for k, v in groups.items()]
    rows.sort(key=lambda r: (r[1] is None, -(r[1] or ZERO), r[0]))
    return rows
