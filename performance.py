# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Historical market observations, ex-dividend events and cash-flow-aware performance."""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import quote as urlquote

from dividends import payment_day
from market import ASSET_TYPES, SUBUNITS, NoListing, exchange_zone, yahoo_json
from portfolio import PortfolioError, ZERO, cash, currency_code, number, units
from valuation import ONE, SGD, complete_sum, positive_rate


@dataclass(frozen=True)
class Distribution:
    ex_day: date
    rate: Decimal
    currency: str | None
    pay_day: date | None = None


@dataclass
class Series:
    key: str
    currency: str
    start: date
    checked: date
    source: str
    closes: dict[date, Decimal]
    sector: str = ""
    payouts: tuple[Distribution, ...] = ()
    splits: tuple[date, ...] = ()
    split_factors: dict[date, Decimal] = field(default_factory=dict)

    def __post_init__(self):
        self.days = sorted(self.closes)

    def at(self, day):
        i = bisect_right(self.days, day) - 1
        if i >= 0:
            session = self.days[i]
            if (day - session).days <= 10 and not any(session < split <= day for split in self.splits):
                return self.closes[session]
        return None

    def comparable_closes(self, days):
        """Express consecutive prices in the share unit used on the final day."""
        days = list(days)
        if not days:
            return {}
        events = sorted((day, ratio) for day, ratio in self.split_factors.items()
                        if days[0] < day <= days[-1])
        factor = ONE
        adjusted = {}
        for day in reversed(days):
            while events and events[-1][0] > day:
                factor *= events.pop()[1]
            adjusted[day] = self.closes[day] / factor
        return adjusted

    def record(self):
        return dict(
            key=self.key,
            currency=self.currency,
            start=str(self.start),
            checked=str(self.checked),
            source=self.source,
            sector=self.sector,
            closes={str(d): str(v) for d, v in self.closes.items()},
            payouts=[[str(p.ex_day), str(p.rate), p.currency, str(p.pay_day) if p.pay_day else None] for p in self.payouts],
            splits=[str(d) for d in self.splits],
            split_factors={str(d): str(r) for d, r in self.split_factors.items()},
        )

    def include_earlier(self, other):
        """Retain older verified observations when another window refreshes a shorter range."""
        before = self.start
        return Series(
            self.key,
            self.currency,
            other.start,
            self.checked,
            self.source,
            {**{d: v for d, v in other.closes.items() if d < before}, **self.closes},
            self.sector or other.sector,
            tuple(p for p in other.payouts if p.ex_day < before) + self.payouts,
            tuple(d for d in other.splits if d < before) + self.splits,
            {**{d: r for d, r in other.split_factors.items() if d < before}, **self.split_factors},
        )

    @classmethod
    def parse(cls, row):
        start, checked = payment_day(row["start"]), payment_day(row["checked"])
        if not isinstance(row["key"], str) or not row["key"] or not isinstance(row["source"], str):
            raise PortfolioError("Invalid historical series.")
        closes = {payment_day(d): positive_rate(v) for d, v in row["closes"].items()}
        payouts = tuple(
            Distribution(payment_day(d), positive_rate(r), currency_code(c) if c else None,
                         payment_day(pay[0]) if pay and pay[0] else None)
            for d, r, c, *pay in row.get("payouts", [])
        )
        return cls(
            row["key"],
            currency_code(row["currency"]),
            start,
            checked,
            row["source"],
            closes,
            row.get("sector") or "",
            payouts,
            tuple(payment_day(d) for d in row.get("splits", [])),
            {payment_day(d): positive_rate(r) for d, r in row.get("split_factors", {}).items()},
        )


def fx_key(code):
    return code + SGD + "=X"


def history_inputs(book, payments):
    """All instruments ever traded, including fully sold positions and dividend currencies."""
    symbols = dict(book.currencies)
    currencies = set(symbols.values()) | {d.currency for d in payments}
    return {**symbols, **{fx_key(c): SGD for c in currencies if c != SGD}}


def raw_history(symbol, expected, start, end, is_fx=False):
    def ts(day):
        return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())

    payload = yahoo_json(
        "/v8/finance/chart/" + urlquote(symbol, safe=""),
        {
            "period1": ts(start),
            "period2": ts(end) + 86400,
            "interval": "1d",
            "events": "div,splits",
            "includeAdjustedClose": "false",
        },
    )
    try:
        chart = payload["chart"]
        if not chart.get("result"):
            if not chart.get("error") or chart["error"].get("code") == "Not Found":
                raise NoListing(f"No price history for {symbol}.")
            raise PortfolioError(f"Yahoo Finance could not return history for {symbol}.")
        item = chart["result"][0]
        meta = item["meta"]
        code, divisor = SUBUNITS.get(meta["currency"], (meta["currency"], 1))
        if (
            meta["symbol"] != symbol
            or code != expected
            or meta["instrumentType"] not in ({"CURRENCY"} if is_fx else ASSET_TYPES)
        ):
            raise PortfolioError(f"The historical listing or currency for {symbol} did not match.")
        zone = exchange_zone(meta)

        def local_day(stamp):
            return datetime.fromtimestamp(stamp, zone).date()

        events = item.get("events") or {}
        factors = {}
        for event in (events.get("splits") or {}).values():
            day = local_day(event["date"])
            ratio = positive_rate(event["numerator"]) / positive_rate(event["denominator"])
            factors[day] = factors.get(day, ONE) * ratio
        # Yahoo Close is split-adjusted. Restore the historical trading unit.
        splits = sorted(factors, reverse=True)
        split_ix = 0
        factor = ONE
        closes = {}
        market = (meta.get("currentTradingPeriod") or {}).get("regular", {})
        close_time = market.get("end", 0)
        open_day = (
            local_day(close_time) if close_time > datetime.now(timezone.utc).timestamp() else None
        )
        for stamp, raw in reversed(
            list(
                zip(item.get("timestamp") or [], item["indicators"]["quote"][0].get("close") or [])
            )
        ):
            day = local_day(stamp)
            if raw is None or raw <= 0 or day > end or day == open_day:
                continue
            while split_ix < len(splits) and splits[split_ix] > day:
                factor *= factors[splits[split_ix]]
                split_ix += 1
            closes[day] = positive_rate(raw) * factor / divisor
        if not closes:
            raise NoListing(f"No daily closes available for {symbol}.")
        payouts = []
        for event in (events.get("dividends") or {}).values():
            if event.get("amount") is None or event["amount"] <= 0:
                continue
            unit = event.get("currency")
            ccy, scale = SUBUNITS.get(unit, (unit, 1))
            if ccy and (not isinstance(ccy, str) or len(ccy) != 3 or not ccy.isupper()):
                ccy = None
            payouts.append(
                Distribution(local_day(event["date"]), positive_rate(event["amount"]) / scale, ccy,
                             local_day(event["paymentDate"]) if isinstance(event.get("paymentDate"), (int, float)) and event["paymentDate"] > 0 else None)
            )
        return Series(
            symbol,
            expected,
            start,
            end,
            "Yahoo Finance",
            closes,
            meta.get("sector") or "",
            tuple(payouts),
            tuple(sorted(factors)),
            factors,
        )
    except PortfolioError:
        raise
    except (KeyError, IndexError, TypeError, ValueError, OSError, ArithmeticError, AttributeError):
        raise PortfolioError(f"Incomplete daily history for {symbol}.") from None


def trade_reference(quote, day, cached, today):
    """An exact exchange date. Never carry a Friday price into a weekend trade."""
    if day == quote.session_day:
        value, source = quote.price, "Yahoo latest trade"
    else:
        series = cached
        if not (
            series
            and series.key == quote.symbol
            and series.currency == quote.currency
            and series.source == "Yahoo Finance"
            and series.checked >= today
            and day in series.closes
        ):
            # Include later split events so the close uses the shares traded on that date.
            series = raw_history(quote.symbol, quote.currency, day - timedelta(days=1), today)
        value = series.closes.get(day)
        if value is None:
            raise PortfolioError(
                f"No closing price for {quote.symbol} on {day}. Enter the execution price from your broker."
            )
        # Daily bars arrive as binary floats. Seven significant digits remove display noise.
        value, source = Decimal(format(value, ".7g")), "Yahoo closing price"
    return number(units(value.quantize(Decimal("0.00000001"))), "Reference price", 8), source


def fx_history(base, target, start, end):
    try:
        return raw_history(base + target + "=X", target, start, end, True)
    except NoListing:
        inverse = raw_history(target + base + "=X", base, start, end, True)
        return Series(
            base + target + "=X",
            target,
            start,
            end,
            "Yahoo Finance · inverse",
            {day: ONE / rate for day, rate in inverse.closes.items()},
        )


def fetch_history(key, code, start, end):
    if not key.endswith("=X"):
        h = raw_history(key, code, start, end)
        if not h.sector:
            try:
                info = yahoo_json(
                    "/v1/finance/search", {"q": key, "quotesCount": 5, "newsCount": 0}
                )
                for row in info.get("quotes", []):
                    if row.get("symbol") == key and row.get("quoteType") in ASSET_TYPES:
                        h.sector = (
                            "ETF"
                            if row["quoteType"] == "ETF"
                            else row.get("sectorDisp") or row.get("sector") or ""
                        )
                        break
            except (PortfolioError, AttributeError, TypeError):
                pass  # Sector enrichment is optional; verified price history stays available.
        return h
    ccy = key[:3]
    try:
        return fx_history(ccy, SGD, start, end)
    except NoListing:
        if ccy == "USD":
            raise
    left, right = fx_history(ccy, "USD", start, end), fx_history("USD", SGD, start, end)
    shared = left.closes.keys() & right.closes.keys()
    if not shared:
        raise PortfolioError(f"No overlapping FX observations for {ccy}/SGD.")
    return Series(
        key,
        SGD,
        start,
        end,
        "Yahoo Finance · via USD",
        {day: left.closes[day] * right.closes[day] for day in shared},
    )


@dataclass(frozen=True)
class Observation:
    day: date
    value: Decimal | None
    invested: Decimal | None
    income: Decimal | None

    @property
    def gain(self):
        if self.value is None or self.invested is None or self.income is None:
            return None
        return self.value - self.invested + self.income


def performance(book, payments, histories, today):
    trades = defaultdict(list)
    receipts = defaultdict(list)
    for t in book.trades:
        trades[t.day].append(t)
    for d in payments:
        if d.status == "Received" and d.day <= today:
            receipts[d.day].append(d)
    if not trades and not receipts:
        return [], set()
    start = min(set(trades) | set(receipts))
    required = history_inputs(book, payments)
    dates = {start, today, *trades, *receipts}
    splits = defaultdict(set)
    for key in required:
        if key in histories:
            h = histories[key]
            dates.update(h.days)
            # Insert a missing observation if *every* market stops reporting at once.
            # Otherwise a line could bridge a multi-week data gap.
            for earlier, later in zip(h.days, h.days[1:] + [today]):
                if (later - earlier).days > 10:
                    dates.add(earlier + timedelta(days=11))
            for d in h.splits:
                splits[d].add(key)
                dates.add(d)
    for split in book.splits:
        splits[split.day].add(split.symbol)
        dates.add(split.day)
    balances = defaultdict(lambda: ZERO)
    unresolved = set()
    warnings = set()

    def convert(amount, ccy, day):
        if amount == 0 or ccy == SGD:
            return cash(amount, SGD)
        key = fx_key(ccy)
        rate = histories[key].at(day) if key in histories else None
        if rate is None:
            warnings.add(key)
            return None
        return cash(amount * rate, SGD)

    invested = income = ZERO
    result = []
    for day in sorted(d for d in dates if start <= d <= today):
        for symbol in splits[day]:
            event = book.splits_by_key.get((symbol, day))
            if event:
                balances[symbol] = event.adjust(balances[symbol])
            elif balances[symbol]:
                unresolved.add(symbol)
        for t in trades[day]:
            buy = t.side == "BUY"
            balances[t.symbol] += t.qty if buy else -t.qty
            if balances[t.symbol] == 0:
                unresolved.discard(t.symbol)
            flow = t.gross + t.fee if buy else -(t.gross - t.fee)
            invested = complete_sum((invested, convert(flow, t.currency, day)))
        for payment in receipts[day]:
            income = complete_sum((income, convert(payment.net, payment.currency, day)))
        marks = []
        for symbol, qty in balances.items():
            if not qty:
                continue
            ccy = book.currencies[symbol]
            h = histories.get(symbol)
            price = h.at(day) if h and h.currency == ccy else None
            if price is None or symbol in unresolved:
                warnings.add(
                    symbol + " (split across an open position)" if symbol in unresolved else symbol
                )
                marks.append(None)
            else:
                marks.append(convert(cash(price * qty, ccy), ccy, day))
        result.append(Observation(day, complete_sum(marks), invested, income))
    return result, warnings


def dividend_candidates(book, payments, histories):
    recorded = {(d.symbol, d.ex_day) for d in payments if d.ex_day}
    candidates = []
    by_symbol = defaultdict(list)
    for t in book.trades:
        by_symbol[t.symbol].append(t)
    for symbol, trades in by_symbol.items():
        h = histories.get(symbol)
        if not h:
            continue
        entered = {s.day: s for s in book.splits if s.symbol == symbol}
        days = sorted({t.day for t in trades} | {p.ex_day for p in h.payouts} | set(h.splits) | entered.keys())
        changes = defaultdict(list)
        events = defaultdict(list)
        for t in trades:
            changes[t.day].append(t.qty if t.side == "BUY" else -t.qty)
        for event in h.payouts:
            events[event.ex_day].append(event)
        qty = ZERO
        split = False
        for day in days:
            if day in entered:
                qty = entered[day].adjust(qty)
            elif day in h.splits and qty:
                split = True
            if qty and not split and (symbol, day) not in recorded:
                for event in events[day]:
                    candidates.append((symbol, event, qty))
            for delta in changes[day]:
                qty += delta
                if not qty:
                    split = False
    return sorted(candidates, key=lambda x: (x[1].ex_day, x[0]), reverse=True)


@dataclass(frozen=True)
class Growth:
    day: date
    gain: Decimal | None
    return_pct: Decimal | None


def period_growth(observations, cutoff=None):
    """Modified Dietz weights each net external flow by the fraction of the period remaining."""
    if not observations:
        return [], None
    i = bisect_right([r.day for r in observations], cutoff - timedelta(days=1)) if cutoff else 0
    if i == len(observations):
        return [], None
    before = observations[i - 1] if i else None
    start = before.day if before else observations[0].day
    opening = before.value if before else ZERO
    flows = weighted_days = ZERO
    previous = (
        before.invested - before.income
        if before and before.invested is not None and before.income is not None
        else (None if before else ZERO)
    )
    points = []
    for r in observations[i:]:
        net = r.invested - r.income if r.invested is not None and r.income is not None else None
        elapsed = (r.day - start).days
        if net is None or previous is None:
            flows = None
        if flows is not None:
            delta = net - previous
            flows += delta
            weighted_days += delta * elapsed
        previous = net
        gain = (
            r.value - opening - flows
            if r.value is not None and opening is not None and flows is not None
            else None
        )
        capital = (
            opening + flows - (weighted_days / elapsed if elapsed else ZERO)
            if gain is not None
            else None
        )
        pct = gain * 100 / capital if capital is not None and capital > 0 else None
        points.append(Growth(r.day, gain, pct))
    return points, start
