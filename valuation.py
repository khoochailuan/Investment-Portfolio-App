# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Verified SGD exchange rates and a consolidated view of the trading ledger."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import quote as urlquote

from market import NoListing, Quote, UTC, yahoo_json
from portfolio import Ledger, Mark, PortfolioError, Position, Sale, ZERO, cash, currency_code, mark

SGD = "SGD"
ONE = Decimal(1)


def positive_rate(raw):
    try:
        value = Decimal(str(raw))
        if value.is_finite() and Decimal("1e-18") <= value <= Decimal("1e18"):
            return value
    except InvalidOperation:
        pass
    raise PortfolioError("The provider did not supply a usable exchange rate.")


def check_time(observed):
    if observed.tzinfo is None or not datetime(1970, 1, 1, tzinfo=UTC) < observed <= datetime.now(
        UTC
    ) + timedelta(minutes=5):
        raise PortfolioError("The exchange rate needs a valid observation time.")
    return observed.astimezone(UTC)


@dataclass(frozen=True)
class FxRate:
    currency: str
    rate: Decimal  # SGD per unit of the trading currency
    asof: datetime
    pairs: tuple[str, ...]

    def record(self):
        return dict(
            currency=self.currency,
            target=SGD,
            rate=str(self.rate),
            asof=self.asof.isoformat(timespec="seconds"),
            pairs=list(self.pairs),
        )

    @classmethod
    def from_record(cls, row):
        try:
            currency = currency_code(row["currency"])
            pairs = row["pairs"]
            if row["target"] != SGD or currency == SGD or not isinstance(pairs, list):
                raise ValueError
            # Each source instrument must connect the stated endpoints, in either direction.
            path = [currency, SGD] if len(pairs) == 1 else [currency, "USD", SGD]
            if len(pairs) != len(path) - 1 or len(set(path)) != len(path):
                raise ValueError
            for pair, base, target in zip(pairs, path, path[1:]):
                if pair not in (base + target + "=X", target + base + "=X"):
                    raise ValueError
            stamp = check_time(datetime.fromisoformat(row["asof"]))
            return cls(currency, positive_rate(row["rate"]), stamp, tuple(pairs))
        except (KeyError, TypeError, ValueError, AttributeError):
            raise PortfolioError("Invalid SGD exchange-rate record.") from None


def quoted_pair(base, target):
    symbol = base + target + "=X"
    data = yahoo_json(
        "/v8/finance/chart/" + urlquote(symbol, safe=""), {"range": "5d", "interval": "1d"}
    )
    try:
        chart = data["chart"]
        if not chart.get("result"):
            if not chart.get("error") or chart["error"].get("code") == "Not Found":
                raise NoListing(f"No quote for {base}/{target}.")
            raise PortfolioError(f"Yahoo Finance could not supply {base}/{target}.")
        meta = chart["result"][0]["meta"]
        if (meta["symbol"], meta["instrumentType"], meta["currency"]) != (
            symbol,
            "CURRENCY",
            target,
        ):
            raise PortfolioError(f"Yahoo Finance did not confirm the direction of {base}/{target}.")
        stamp = check_time(datetime.fromtimestamp(meta["regularMarketTime"], UTC))
        return positive_rate(meta["regularMarketPrice"]), stamp, symbol
    except PortfolioError:
        raise
    except (KeyError, IndexError, TypeError, ValueError, OverflowError, OSError, AttributeError):
        raise PortfolioError(f"Incomplete exchange quote for {base}/{target}.") from None


def rate_leg(base, target):
    try:
        return quoted_pair(base, target)
    except NoListing:
        price, stamp, symbol = quoted_pair(target, base)
        return ONE / price, stamp, symbol


def fetch_sgd_rate(currency):
    currency = currency_code(currency)
    if currency == SGD:
        raise PortfolioError("SGD is the reporting currency and needs no exchange quote.")
    try:
        rate, asof, pair = rate_leg(currency, SGD)
        return FxRate(currency, rate, asof, (pair,))
    except NoListing:
        if currency == "USD":
            raise
    first, time1, pair1 = rate_leg(currency, "USD")
    second, time2, pair2 = rate_leg("USD", SGD)
    return FxRate(currency, first * second, min(time1, time2), (pair1, pair2))


def reporting_currencies(book):
    """Include fully liquidated currencies for both proceeds and FIFO cost."""
    return {p.currency for p in book.positions.values()} | book.months_by_currency.keys()


def in_sgd(amount, currency, rates):
    if amount is None:
        return None
    if currency == SGD or amount == 0:
        return cash(amount, SGD)
    fx = rates.get(currency)
    return cash(amount * fx.rate, SGD) if fx else None


@dataclass(frozen=True)
class SaleAmounts:
    net: Decimal | None
    basis: Decimal | None
    profit: Decimal | None

    @property
    def return_pct(self):
        if self.basis and self.profit is not None:
            return self.profit * 100 / self.basis
        return None


@dataclass(frozen=True)
class ReportedSale:
    sale: Sale
    amounts: SaleAmounts


class SalesReport:
    """One set of rounded sale lines feeds the overview, months and drill-down."""

    def __init__(self, book, rates, native_currency=None):
        rows = []
        self.rows_by_month = defaultdict(list)
        for sale in reversed(book.sales.values()):
            code = sale.trade.currency
            if native_currency is not None:
                if code != native_currency:
                    continue
                net, basis = sale.net, sale.basis
            else:
                net = in_sgd(sale.net, code, rates)
                basis = in_sgd(sale.basis, code, rates)
            profit = net - basis if net is not None and basis is not None else None
            if sale.profit == ZERO:
                profit = ZERO
            line = ReportedSale(sale, SaleAmounts(net, basis, profit))
            rows.append(line)
            self.rows_by_month[sale.trade.day.strftime("%Y-%m")].append(line)
        self.months = sorted(self.rows_by_month, reverse=True)
        self.rows_by_month["all"] = rows
        self.currencies_by_month = {
            month: {line.sale.trade.currency for line in lines}
            for month, lines in self.rows_by_month.items()
        }
        self.totals = {
            month: SaleAmounts(
                complete_sum(r.amounts.net for r in lines),
                complete_sum(r.amounts.basis for r in lines),
                complete_sum(r.amounts.profit for r in lines),
            )
            for month, lines in self.rows_by_month.items()
        }

    def profit_by_stock(self, month="all"):
        by_symbol = defaultdict(list)
        for row in self.rows_by_month[month]:
            by_symbol[row.sale.trade.symbol].append(row.amounts.profit)
        amounts = [(symbol, complete_sum(values)) for symbol, values in by_symbol.items()]
        return sorted(amounts, key=lambda item: (item[1] is None, -(item[1] or ZERO), item[0]))


def market_name(quote):
    if quote is None or quote.listing is None:
        return "Unverified exchange"
    exchange = quote.listing.exchange
    return {
        "SES": "SGX",
        "Singapore": "SGX",
        "NMS": "Nasdaq",
        "NGM": "Nasdaq",
        "NCM": "Nasdaq",
        "NasdaqGS": "Nasdaq",
        "NasdaqGM": "Nasdaq",
        "NasdaqCM": "Nasdaq",
        "NYQ": "NYSE",
        "LSE": "London",
        "HKG": "Hong Kong",
        "HKSE": "Hong Kong",
        "JPX": "Tokyo",
    }.get(exchange, exchange)


@dataclass(frozen=True)
class HoldingValue:
    position: Position
    quote: Quote | None
    market: str
    local: Mark
    value: Decimal | None
    basis: Decimal | None
    gain: Decimal | None

    @property
    def return_pct(self):
        return self.local.return_pct


@dataclass(frozen=True)
class AllocationEntry:
    label: str
    holding: HoldingValue
    qty: Decimal
    value: Decimal
    basis: Decimal | None


@dataclass(frozen=True)
class AllocationGroup:
    label: str
    value: Decimal
    entries: list[AllocationEntry]

    @property
    def basis(self):
        return complete_sum(e.basis for e in self.entries)


class Overview:
    def __init__(self, book: Ledger, quotes: dict, rates: dict, sales=None, sectors=None):
        self.rows = []
        self.rates = rates
        self.sectors = sectors or {}
        self.currencies = reporting_currencies(book)
        self.missing_fx = sorted(self.currencies - {SGD} - rates.keys())
        self.missing_quotes = []

        for p in sorted(book.positions.values(), key=lambda p: p.symbol):
            quote = quotes.get(p.symbol)
            latest_split = book.latest_split.get(p.symbol)
            if quote and (
                quote.currency != p.currency
                or (latest_split and (quote.session_day or quote.asof.date()) < latest_split)
            ):
                quote = None
            if quote is None:
                self.missing_quotes.append(p.symbol)
            local = mark(p, quote.price if quote else None)
            value, basis = (
                in_sgd(local.value, p.currency, rates),
                in_sgd(p.basis, p.currency, rates),
            )
            gain = value - basis if value is not None and basis is not None else None
            self.rows.append(HoldingValue(p, quote, market_name(quote), local, value, basis, gain))
        self.value = complete_sum(r.value for r in self.rows)
        self.basis = complete_sum(r.basis for r in self.rows)
        self.gain = complete_sum(r.gain for r in self.rows)
        sold = (sales or SalesReport(book, rates)).totals["all"]
        self.realised, self.realised_return_pct = sold.profit, sold.return_pct
        self.return_pct = (
            self.gain * 100 / self.basis if self.basis and self.gain is not None else None
        )

    def weight(self, row):
        return row.value * 100 / self.value if self.value and row.value is not None else None

    def allocation(self, group):
        return [(item.label, item.value) for item in self.allocation_groups(group)]

    def row_label(self, row, group):
        """Group name of one holding for Market, Currency, Holding or Sector."""
        pos = row.position
        sector = self.sectors.get(pos.symbol, "Unclassified")
        if row.quote and row.quote.listing and row.quote.listing.kind == "ETF":
            sector = "ETF"
        return {
            "Market": row.market,
            "Currency": pos.currency,
            "Holding": pos.symbol,
            "Sector": sector,
        }[group]

    def allocation_groups(self, group):
        if not self.value:
            return []
        members = defaultdict(list)
        broker_names = {}
        for row in self.rows:
            pos = row.position
            if group == "Brokerage":
                # Each cent of the displayed holding belongs to one brokerage. Use
                # cumulative quantities so that partial rounding cannot lose a cent.
                quantity, allocated, local_cost, allocated_cost = ZERO, ZERO, ZERO, ZERO
                for account, lots in sorted(pos.accounts.items()):
                    label = broker_names.setdefault(account, lots[0].purchase.broker)
                    account_qty = sum((lot.qty for lot in lots), ZERO)
                    quantity += account_qty
                    cumulative = cash(row.value * quantity / pos.qty, SGD)
                    local_cost += sum((lot.basis for lot in lots), ZERO)
                    cumulative_cost = in_sgd(local_cost, pos.currency, self.rates)
                    cost = (
                        cumulative_cost - allocated_cost
                        if cumulative_cost is not None and allocated_cost is not None
                        else None
                    )
                    members[label].append(
                        AllocationEntry(label, row, account_qty, cumulative - allocated, cost)
                    )
                    allocated, allocated_cost = cumulative, cumulative_cost
            else:
                label = self.row_label(row, group)
                members[label].append(AllocationEntry(label, row, pos.qty, row.value, row.basis))
        groups = [
            AllocationGroup(label, sum((e.value for e in entries), ZERO), entries)
            for label, entries in members.items()
        ]
        groups.sort(key=lambda item: (-item.value, item.label))
        return groups


def complete_sum(amounts):
    total = ZERO
    for amount in amounts:
        if amount is None:
            return None
        total += amount
    return total
