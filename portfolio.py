# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Trade validation and fee-inclusive FIFO or moving-average inventory."""

from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from heapq import merge
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from typing import Iterable

ZERO = Decimal("0")
CENT = Decimal("0.01")
# Yahoo validates instrument identity. Local syntax only bounds strings stored as keys.
SYMBOL_PATTERN = re.compile(r"[^\s/\\?#:%]{1,96}\Z")
THREE_DECIMALS = {"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"}
NO_DECIMALS = {
    "BIF",
    "CLP",
    "DJF",
    "GNF",
    "ISK",
    "JPY",
    "KMF",
    "KRW",
    "PYG",
    "RWF",
    "UGX",
    "UYI",
    "VND",
    "VUV",
    "XAF",
    "XOF",
    "XPF",
}


def currency_code(value) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z]{3}", value):
        raise PortfolioError("The listing needs a recognised currency code.")
    return value


def minor_unit(currency: str) -> Decimal:
    if currency in NO_DECIMALS:
        return Decimal("1")
    if currency in THREE_DECIMALS:
        return Decimal("0.001")
    return CENT


def currency_prefix(currency: str) -> str:
    return {"USD": "US$", "SGD": "S$"}.get(currency, currency + " ")


class PortfolioError(ValueError):
    """A user-correctable input, file, or accounting error."""


def clean_name(value: str) -> str:
    return " ".join(value.split())


def canonical_names(values):
    """Use the first recorded spelling for each name, ignoring case and extra spaces."""
    known = {}
    for value in values:
        cleaned = clean_name(value)
        if cleaned:
            known.setdefault(cleaned.casefold(), cleaned)
    return known


def number(value, label: str, places: int = 6, allow_zero: bool = False) -> Decimal:
    """Reject nonfinite values, scientific notation and unsupported precision."""
    raw = str(value).strip()
    if not re.fullmatch(rf"(?:[0-9]{{1,10}}(?:\.[0-9]{{1,{places}}})?|\.[0-9]{{1,{places}}})", raw):
        raise PortfolioError(
            f"{label} needs a non-negative number with up to {places} decimal places."
        )
    result = Decimal(raw)
    if result == 0 and not allow_zero:
        raise PortfolioError(f"{label} must be greater than zero.")
    return result


def fee_value(value, label="Fee", currency="USD") -> Decimal:
    amount = number("0" if value is None or str(value).strip() == "" else value, label, 6, True)
    if amount != cash(amount, currency):
        raise PortfolioError(f"{label} must be a multiple of {minor_unit(currency)} {currency}.")
    return amount


def ticker(value) -> str:
    symbol = str(value or "").strip().upper()
    if not SYMBOL_PATTERN.fullmatch(symbol) or not symbol.isprintable():
        raise PortfolioError("Enter an exchange ticker such as NVDA or ME8U.SI.")
    return symbol


DATE_HINT = "DD-MM-YYYY"
DATE_FORMAT = "%d-%m-%Y"


def display_date(day):
    return day.strftime(DATE_FORMAT) if day else ""


def parse_day(value):
    raw = str(value).strip()
    try:
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw):
            return date.fromisoformat(raw)  # Existing records and imports are stored in ISO format.
        if re.fullmatch(r"[0-9]{2}-[0-9]{2}-[0-9]{4}", raw):
            return datetime.strptime(raw, DATE_FORMAT).date()
    except ValueError:
        pass
    raise PortfolioError(f"Enter a valid date in {DATE_HINT} format.")


def today_sg():
    return datetime.now(timezone(timedelta(hours=8))).date()


def trade_day(value) -> date:
    result = parse_day(value)
    if result > today_sg():
        raise PortfolioError("A recorded transaction needs a date on or before today.")
    return result


def cash(value: Decimal, currency="USD") -> Decimal:
    return value.quantize(minor_unit(currency), rounding=ROUND_HALF_UP)


def units(value: Decimal) -> str:
    digits = format(value, "f")
    return digits.rstrip("0").rstrip(".") if "." in digits else digits


def quantity_from_amount(value, price, currency):
    amount = fee_value(value, "Trade amount", currency)
    if amount <= 0:
        raise PortfolioError("Trade amount must be greater than zero.")
    price = number(price, "Unit price", 8)
    qty = (amount / price).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    if not qty or cash(qty * price, currency) != amount:
        raise PortfolioError(
            "This amount cannot be represented to six share decimals at this price. Enter the executed quantity."
        )
    return number(units(qty), "Quantity")


# Direction markers for gains and losses. DM Sans (the bundled font) includes these glyphs.
# Swap for "▲"/"▼" only if the display font also carries them.
UP, DOWN = "↑", "↓"


def dollars(value: Decimal | None, signed=False, currency="USD") -> str:
    """Format money. With signed=True a gain shows an up arrow and a loss a down arrow."""
    if value is None:
        return "—"
    rounded = cash(value, currency)
    if signed:
        mark = f"{UP} " if rounded > 0 else f"{DOWN} " if rounded < 0 else ""
    else:
        mark = "−" if rounded < 0 else ""
    return f"{mark}{currency_prefix(currency)}{abs(rounded):,f}"


def unit_price(value: Decimal | None, currency="USD") -> str:
    """Show cents normally and extra decimals when a per-share figure needs them."""
    if value is None:
        return "—"
    digits = f"{value:,.8f}".rstrip("0")
    whole, _, decimals = digits.partition(".")
    decimals = decimals.ljust(-minor_unit(currency).as_tuple().exponent, "0")
    return currency_prefix(currency) + whole + ("." + decimals if decimals else "")


def percent(value: Decimal | None) -> str:
    if value is None:
        return "—"
    mark = f"{UP} " if value > 0 else f"{DOWN} " if value < 0 else ""
    return f"{mark}{abs(value):,.2f}%"


@dataclass(frozen=True)
class Trade:
    id: int
    day: date
    symbol: str
    side: str
    qty: Decimal
    price: Decimal
    broker: str
    fee: Decimal
    currency: str = "USD"
    note: str = ""
    amount: Decimal | None = None
    import_key: str = ""

    @classmethod
    def from_input(cls, trade_id: int, data: dict) -> Trade:
        if type(trade_id) is not int or trade_id < 1:
            raise PortfolioError("A transaction has an invalid ID.")
        side = str(data["side"]).strip().upper()
        if side not in {"BUY", "SELL"}:
            raise PortfolioError("Choose Buy or Sell.")
        raw_broker = data["broker"]
        if (
            not isinstance(raw_broker, str)
            or not raw_broker.strip()
            or len(raw_broker.strip()) > 50
        ):
            raise PortfolioError("Enter a brokerage name of 1 to 50 characters.")
        currency = currency_code(data.get("currency", "USD"))
        note = data.get("note", "")
        if not isinstance(note, str) or len(note) > 1000:
            raise PortfolioError("Use a note of up to 1,000 characters.")
        key = data.get("import_key", "")
        if not isinstance(key, str) or (key and not re.fullmatch(r"[0-9a-f]{64}:\d+", key)):
            raise PortfolioError("Invalid import reference.")
        price = number(data["price"], "Unit price", 8)
        amount = data.get("amount")
        if amount is not None:
            qty = quantity_from_amount(amount, price, currency)
            if (
                data.get("quantity") not in (None, "")
                and number(data["quantity"], "Quantity") != qty
            ):
                raise PortfolioError("Quantity does not match the trade amount and unit price.")
            amount = fee_value(amount, "Trade amount", currency)
        else:
            qty = number(data["quantity"], "Quantity")
        return cls(
            trade_id,
            trade_day(data["date"]),
            ticker(data["symbol"]),
            side,
            qty,
            price,
            clean_name(raw_broker),
            fee_value(data.get("fee"), currency=currency),
            currency,
            note.strip(),
            amount,
            key,
        )

    @property
    def gross(self):
        return cash(self.qty * self.price, self.currency)

    def record(self) -> dict:
        return dict(
            id=self.id,
            date=self.day.isoformat(),
            symbol=self.symbol,
            side=self.side,
            quantity=units(self.qty),
            price=units(self.price),
            broker=self.broker,
            fee=format(cash(self.fee, self.currency), "f"),
            currency=self.currency,
            **({"note": self.note} if self.note else {}),
            **({"amount": units(self.amount)} if self.amount is not None else {}),
            **({"import_key": self.import_key} if self.import_key else {}),
        )


@dataclass
class Lot:
    purchase: Trade
    qty: Decimal
    basis: Decimal


@dataclass(frozen=True)
class Match:
    buy_id: int
    qty: Decimal
    basis: Decimal


@dataclass
class Position:
    symbol: str
    qty: Decimal = ZERO
    basis: Decimal = ZERO
    currency: str = "USD"
    # Each brokerage owns its own saleable inventory.
    accounts: dict[str, deque[Lot]] = field(default_factory=dict)

    @property
    def average(self) -> Decimal:
        return self.basis / self.qty


@dataclass(frozen=True)
class Sale:
    trade: Trade
    net: Decimal
    basis: Decimal
    profit: Decimal
    matches: tuple[Match, ...]
    # For AVCO, retain the actual pool just before this sale for the audit trail.
    pool_before: tuple[Decimal, Decimal] | None = None


@dataclass
class Month:
    count: int = 0
    net: Decimal = ZERO
    basis: Decimal = ZERO

    @property
    def profit(self):
        return self.net - self.basis


@dataclass(frozen=True)
class StockSplit:
    symbol: str
    day: date
    new: int
    old: int

    @classmethod
    def parse(cls, row):
        sides = []
        for key in ("new", "old"):
            raw = str(row[key]).strip()
            if not re.fullmatch(r"[1-9][0-9]{0,5}", raw):
                raise PortfolioError("Enter positive whole numbers for the split ratio.")
            sides.append(int(raw))
        if sides[0] == sides[1]:
            raise PortfolioError("A split must change the number of shares.")
        return cls(ticker(row["symbol"]), trade_day(row["date"]), *sides)

    def record(self):
        return dict(symbol=self.symbol, date=self.day.isoformat(), new=self.new, old=self.old)

    def adjust(self, qty):
        return qty * self.new / self.old



class Ledger:
    """One chronological replay builds all query indexes from the source trades."""

    def __init__(self, trades: Iterable[Trade], method="FIFO", splits=()):
        if method not in ("FIFO", "AVCO"):
            raise PortfolioError("Select FIFO or AVCO for the cost basis.")
        self.method = method
        self.trades = sorted(trades, key=lambda t: (t.day, t.id))
        self.splits = tuple(sorted(splits, key=lambda event: (event.day, event.symbol)))
        self.splits_by_key = {(event.symbol, event.day): event for event in self.splits}
        self.latest_split = {event.symbol: event.day for event in self.splits}
        if len(self.splits_by_key) != len(self.splits):
            raise PortfolioError("A stock split already exists for this listing and date.")
        # Both streams are ordered already. A split applies before trades on its effective day.
        self.events = list(
            merge(self.splits, self.trades, key=lambda event: (event.day, getattr(event, "id", 0)))
        )
        self.closed_sales = set()
        self.by_id: dict[int, Trade] = {}
        self.positions: dict[str, Position] = {}
        self.sales: dict[int, Sale] = {}
        self.months_by_currency: dict[str, dict[str, Month]] = {}
        self.realised_by_currency: dict[str, Decimal] = defaultdict(lambda: ZERO)
        self.currencies: dict[str, str] = {}
        for trade in self.events:
            if isinstance(trade, StockSplit):
                position = self.positions.get(trade.symbol)
                if position:
                    position.qty = ZERO
                    for queue in position.accounts.values():
                        for lot in queue:
                            lot.qty = trade.adjust(lot.qty)
                            position.qty += lot.qty
                continue
            if trade.id in self.by_id:
                raise PortfolioError(f"Transaction #{trade.id} appears more than once.")
            self.by_id[trade.id] = trade
            if self.currencies.setdefault(trade.symbol, trade.currency) != trade.currency:
                raise PortfolioError(
                    f"Trades for {trade.symbol} use different currencies. Check the listing and trade confirmation."
                )
            position = self.positions.setdefault(
                trade.symbol, Position(trade.symbol, currency=trade.currency)
            )
            account = trade.broker.casefold()
            queue = position.accounts.setdefault(account, deque())
            if trade.side == "BUY":
                cost = trade.gross + trade.fee
                if method == "AVCO" and queue:
                    queue[0].qty += trade.qty
                    queue[0].basis += cost
                else:
                    queue.append(Lot(trade, trade.qty, cost))
                position.qty += trade.qty
                position.basis += cost
                continue

            remaining = trade.qty
            pool_before = (queue[0].qty, queue[0].basis) if method == "AVCO" and queue else None
            matches = []
            basis = ZERO
            while remaining > 0:
                if not queue:
                    raise PortfolioError(
                        f"Sale #{trade.id} on {trade.day} exceeds {trade.symbol} shares available "
                        f"at {trade.broker} by {units(remaining)}. Check dates, quantity and brokerage."
                    )
                lot = queue[0]
                take = min(remaining, lot.qty)
                # Consuming the last share takes every residual cent from this lot.
                amount = (
                    lot.basis
                    if take == lot.qty
                    else cash(lot.basis * take / lot.qty, trade.currency)
                )
                if method == "FIFO":
                    matches.append(Match(lot.purchase.id, take, amount))
                basis += amount
                lot.qty -= take
                lot.basis -= amount
                remaining -= take
                if not lot.qty:
                    queue.popleft()
            net = trade.gross - trade.fee
            sale = Sale(trade, net, basis, net - basis, tuple(matches), pool_before)
            self.sales[trade.id] = sale
            self.realised_by_currency[trade.currency] += sale.profit
            months = self.months_by_currency.setdefault(trade.currency, {})
            month = months.setdefault(trade.day.strftime("%Y-%m"), Month())
            month.count += 1
            month.net += net
            month.basis += basis
            position.qty -= trade.qty
            position.basis -= basis
            if not queue:
                del position.accounts[account]
            if not position.qty:
                self.closed_sales.add(trade.id)
                del self.positions[trade.symbol]


@dataclass(frozen=True)
class Mark:
    value: Decimal | None
    profit: Decimal | None
    return_pct: Decimal | None


def mark(position: Position, price: Decimal | None) -> Mark:
    if price is None:
        return Mark(None, None, None)
    value = cash(position.qty * price, position.currency)
    profit = value - position.basis
    return Mark(value, profit, profit * 100 / position.basis if position.basis else None)


def break_even(basis: Decimal, qty: Decimal, exit_fee: Decimal, currency="USD") -> Decimal:
    """Conservative full-position estimate rounded up to the accounting minor unit."""
    return ((basis + exit_fee) / qty).quantize(minor_unit(currency), rounding=ROUND_CEILING)


@dataclass(frozen=True)
class Plan:
    qty: Decimal
    basis: Decimal
    outlay: Decimal
    profit: Decimal
    exit_price: Decimal
    extra: int | None
    further_outlay: Decimal | None


def floor_sum(count: int, modulus: int, slope: int, offset: int) -> int:
    """Sum floor((slope*i + offset) / modulus), i=0..count-1, by Euclidean reduction."""
    total = 0
    while True:
        whole, slope = divmod(slope, modulus)
        total += whole * count * (count - 1) // 2
        whole, offset = divmod(offset, modulus)
        total += whole * count
        high = slope * count + offset
        if high < modulus:
            return total
        count, offset = divmod(high, modulus)
        modulus, slope = slope, modulus


def minimum_additional(qty, required, target, buy, unit) -> int:
    """Find the first whole-share count whose rounded net sale covers all costs."""
    if target % unit == 0 and buy % unit == 0:
        proceeds = (qty * target / unit).to_integral_value(rounding=ROUND_HALF_UP) * unit
        return int(
            ((required - proceeds) / (target - buy)).to_integral_value(rounding=ROUND_CEILING)
        )
    # Express rounded sale and purchase amounts as floors with one integer denominator.
    sale_base, sell_step, buy_step = qty * target / unit, target / unit, buy / unit
    places = max(1, -min(v.as_tuple().exponent for v in (sale_base, sell_step, buy_step)))
    scale = 10**places
    origin, sell, purchase = (int(v * scale) for v in (sale_base, sell_step, buy_step))
    gain_step = sell - purchase
    cost = int(required / unit)
    lo = max(1, ((cost - 1) * scale - origin) // gain_step + 1)
    hi = max(lo, -((origin - cost * scale) // gain_step))
    # Before lo even rounding cannot help. From hi the raw margin suffices.
    # Between them the rounded difference is exactly cost or cost-1.
    base_lo = lo

    def any_success(end):
        count = end - base_lo + 1
        sale_sum = floor_sum(count, scale, sell, origin + sell * base_lo + scale // 2)
        buy_sum = floor_sum(count, scale, purchase, purchase * base_lo + scale // 2)
        return sale_sum - buy_sum > (cost - 1) * count

    while lo < hi:
        middle = (lo + hi) // 2
        if any_success(middle):
            hi = middle
        else:
            lo = middle + 1
    return lo


def scenario(position: Position, legs: list[dict], target, exit_fee, next_price, next_fee) -> Plan:
    """Model fixed order costs. Previously realised results do not enter this plan."""
    currency = position.currency
    target = number(target, "Target selling price", 8)
    exit_fee = fee_value(exit_fee, "Estimated selling fee", currency)
    next_price = number(next_price, "Further buy price", 8)
    next_fee = fee_value(next_fee, "Further buy fee", currency)
    qty, basis = position.qty, position.basis
    for i, leg in enumerate(legs, start=1):
        quantity = number(leg["quantity"], f"Buy {i} quantity")
        price = number(leg["price"], f"Buy {i} price", 8)
        fee = fee_value(leg.get("fee"), f"Buy {i} fee", currency)
        qty += quantity
        basis += cash(quantity * price, currency) + fee
    profit = cash(qty * target, currency) - exit_fee - basis
    if profit >= 0:
        extra = 0
    elif next_price >= target:
        extra = None
    else:
        # Handle small-price listings whose per-share quotes use fractions of a minor unit.
        extra = minimum_additional(
            qty, basis + exit_fee + next_fee, target, next_price, minor_unit(currency)
        )
    further = (
        None
        if extra is None
        else (cash(extra * next_price, currency) + (next_fee if extra else ZERO))
    )
    return Plan(
        qty,
        basis,
        basis - position.basis,
        profit,
        break_even(basis, qty, exit_fee, currency),
        extra,
        further,
    )
