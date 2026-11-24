# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Compare selected trade quantities and cash with independently entered statement totals."""

from dataclasses import dataclass, field
from decimal import Decimal, localcontext

from portfolio import ZERO, PortfolioError, minor_unit
from trade_import import decimal_text

FEES = ("fees", "")
NET_CASH = ("net_cash", "")


@dataclass
class TradeTotals:
    broker: str
    currency: str
    amounts: dict = field(default_factory=lambda: {FEES: ZERO, NET_CASH: ZERO})

    @property
    def symbols(self):
        return sorted({symbol for _, symbol in self.amounts if symbol})


def selected_trade_totals(rows):
    groups = {}
    for draft in rows:
        trade = draft.trade
        if trade is None or draft.imported:
            continue
        key = (trade.broker.casefold(), trade.currency)
        if key not in groups:
            groups[key] = TradeTotals(trade.broker, trade.currency)
        amounts = groups[key].amounts
        for side in ("BUY", "SELL"):
            amounts.setdefault((side, trade.symbol), ZERO)
        if draft.selected:
            amounts[trade.side, trade.symbol] += trade.qty
            amounts[FEES] += trade.fee
            amounts[NET_CASH] += (-trade.gross if trade.side == "BUY" else trade.gross) - trade.fee
    return groups


def statement_difference(raw, selected, *, currency=None, signed=False):
    """Selected less reported. A blank input remains unchecked."""
    value = decimal_text(raw)
    if not value:
        return None
    reported = Decimal(value)
    if reported < 0 and not signed:
        raise PortfolioError("Enter zero or a positive total.")
    places = -minor_unit(currency).as_tuple().exponent if currency else 6
    if reported.as_tuple().exponent < -places:
        raise PortfolioError(f"Enter up to {places} decimal places." if places else "Enter a whole amount.")
    with localcontext() as ctx:
        # Keep the subtraction exact for large totals entered from external statements.
        ctx.prec = 50
        return selected - reported
