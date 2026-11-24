# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Cash distributions, payment schedules and SGD income totals."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta, timezone
from decimal import Decimal

from portfolio import (
    PortfolioError, StockSplit, ZERO, cash, clean_name, currency_code,
    fee_value, parse_day, ticker, today_sg,
)
from valuation import complete_sum, in_sgd

SGT = timezone(timedelta(hours=8))


payment_day = parse_day


@dataclass(frozen=True)
class Dividend:
    id: int
    symbol: str
    day: date
    currency: str
    gross: Decimal
    tax: Decimal
    status: str
    ex_day: date | None
    broker: str

    @property
    def net(self):
        return self.gross - self.tax

    def record(self):
        return dict(
            id=self.id,
            symbol=self.symbol,
            date=self.day.isoformat(),
            currency=self.currency,
            gross=str(self.gross),
            tax=str(self.tax),
            status=self.status,
            ex_date=self.ex_day.isoformat() if self.ex_day else "",
            broker=self.broker,
        )

    @classmethod
    def parse(cls, key, row):
        if type(key) is not int or key < 1:
            raise PortfolioError("Invalid dividend ID.")
        symbol = ticker(row.get("symbol"))
        ccy = currency_code(str(row.get("currency", "")).strip().upper())
        day = payment_day(row.get("date"))
        ex_day = payment_day(row["ex_date"]) if row.get("ex_date") else None
        status = row.get("status")
        if status not in ("Received", "Expected"):
            raise PortfolioError("Select Received or Expected.")
        if status == "Received" and day > today_sg():
            raise PortfolioError("A future payment must be marked Expected.")
        if ex_day and ex_day > day:
            raise PortfolioError("The ex-dividend date must be on or before the payment date.")
        gross = fee_value(row.get("gross"), "Gross amount", ccy)
        tax = fee_value(row.get("tax"), "Tax withheld", ccy)
        if gross <= ZERO or tax > gross:
            raise PortfolioError("Gross must be positive and withholding cannot exceed gross.")
        broker = row.get("broker", "")
        if not isinstance(broker, str) or len(clean_name(broker)) > 50:
            raise PortfolioError("Use a brokerage name of up to 50 characters.")
        return cls(key, symbol, day, ccy, gross, tax, status, ex_day, clean_name(broker))


@dataclass
class PaymentBook:
    revision: int
    next_id: int
    rows: dict[int, Dividend]

    @classmethod
    def load(cls, data):
        rev, nxt = data["revision"], data["next_id"]
        if (
            type(rev) is not int
            or rev < 0
            or type(nxt) is not int
            or not isinstance(data["payments"], list)
        ):
            raise PortfolioError("Invalid dividend ledger.")
        rows = {}
        for item in data["payments"]:
            d = Dividend.parse(item["id"], item)
            if d.id in rows:
                raise PortfolioError("Duplicate dividend ID.")
            rows[d.id] = d
        if nxt <= max(rows, default=0):
            raise PortfolioError("Invalid next dividend ID.")
        return cls(rev, nxt, rows)


def net_income(rows, rates):
    return complete_sum(in_sgd(d.net, d.currency, rates) for d in rows)


def income_breakdown(rows, rates):
    amounts = defaultdict(list)
    for d in rows:
        amounts[d.day.month, d.status].append(d)
    return {key: net_income(group, rates) for key, group in amounts.items()}


def income_estimates(book, payments, histories, rates, today):
    """Reapply each recorded net payout per eligible share to the currently held shares."""
    from bisect import bisect_left
    from calendar import monthrange

    start = today.replace(
        year=today.year - 1, day=min(today.day, monthrange(today.year - 1, today.month)[1])
    )
    changes = defaultdict(list)
    balances = defaultdict(lambda: ZERO)
    for t in book.events:
        if isinstance(t, StockSplit):
            for key in list(balances):
                if key[0] == t.symbol:
                    balances[key] = t.adjust(balances[key])
                    changes[key].append((t.day, balances[key]))
            continue
        change = t.qty if t.side == "BUY" else -t.qty
        for key in ((t.symbol, ""), (t.symbol, t.broker.casefold())):
            balances[key] += change
            changes[key].append((t.day, balances[key]))
    # Use only reviewed receipts. A payout assigned to one broker uses that broker's lots.
    estimates = defaultdict(list)
    seen = 0
    for d in payments:
        p = book.positions.get(d.symbol)
        if d.status != "Received" or not (start < d.day <= today) or not d.ex_day or not p:
            continue
        key = (d.symbol, d.broker.casefold())
        trail = changes[key]
        i = bisect_left(trail, d.ex_day, key=lambda x: x[0]) - 1
        if i < 0 or trail[i][1] <= 0 or balances[key] <= 0:
            continue
        series = histories.get(d.symbol)
        if series and any(d.ex_day <= day <= today and (d.symbol, day) not in book.splits_by_key for day in series.splits):
            continue
        eligible = trail[i][1]
        for split in book.splits:
            if split.symbol == d.symbol and d.ex_day <= split.day <= today:
                eligible = split.adjust(eligible)
        amount = cash(d.net * balances[key] / eligible, d.currency)
        estimates[d.symbol].append(in_sgd(amount, d.currency, rates))
        seen += 1
    return {symbol: complete_sum(values) for symbol, values in estimates.items()}, seen
