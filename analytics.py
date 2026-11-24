# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Risk metrics, rebalancing plans and the daily digest.

Everything here is plain Python with no UI imports, so the Flet views, the AI copilot and the
command-line digest all share one tested set of calculations.

Complexity notes (n = holdings, d = daily observations per holding, m = trades):
  * risk_report       O(n * d)   one pass per holding series, then one pass over common dates
  * plan_rebalance    O(n)       one pass to total, one to size each trade
  * build_digest      O(n + p)   p = payment records
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_DOWN, Decimal
from math import sqrt
from statistics import pstdev

from portfolio import PortfolioError, ZERO, dollars, percent
from valuation import SGD, in_sgd

HUNDRED = Decimal(100)
TRADING_DAYS = 252

# Alert thresholds, in one place so they are easy to explain and to change.
SINGLE_POSITION_LIMIT = Decimal(25)  # % of portfolio value in one holding
TOP3_LIMIT = Decimal(60)  # % in the three largest holdings
GROUP_LIMIT = Decimal(40)  # % in one sector or market
FOREIGN_CURRENCY_LIMIT = Decimal(70)  # % in non-SGD currencies
DRAWDOWN_LIMIT = 20.0  # % below the 1-year high
VOLATILITY_LIMIT = 40.0  # annualised %


# --------------------------------------------------------------------------- price history


def recent_closes(series, today, days=365):
    """Dated closes in the trailing window, oldest first. O(d) over the stored days."""
    if series is None:
        return []
    start = today - timedelta(days=days)
    days = [d for d in series.days if start <= d <= today]
    if days:
        # Older caches record split dates without ratios. Start after the last such split.
        unpriced = [d for d in series.splits if days[0] < d <= days[-1] and d not in series.split_factors]
        if unpriced:
            days = [d for d in days if d >= max(unpriced)]
    values = series.comparable_closes(days)
    return [(d, float(values[d])) for d in days]


def sparkline_values(series, today, days=30):
    """Closing prices for a small trend line, or [] when fewer than five are cached."""
    closes = [c for _, c in recent_closes(series, today, days)]
    return closes if len(closes) >= 5 else []


def daily_returns(closes):
    return [b / a - 1 for (_, a), (_, b) in zip(closes, closes[1:]) if a > 0]


def annual_volatility(returns):
    """Annualised standard deviation of daily returns, in percent. None if too few points."""
    return pstdev(returns) * sqrt(TRADING_DAYS) * 100 if len(returns) >= 20 else None


def drawdowns(values):
    """(maximum drawdown %, current drawdown %) of a value path. Both are zero or negative."""
    peak, worst = None, 0.0
    for v in values:
        peak = v if peak is None or v > peak else peak
        worst = min(worst, (v / peak - 1) * 100)
    current = (values[-1] / peak - 1) * 100 if values and peak else 0.0
    return worst, current


# --------------------------------------------------------------------------- risk


@dataclass(frozen=True)
class HoldingRisk:
    symbol: str
    weight: Decimal | None
    volatility: float | None
    max_drawdown: float | None
    current_drawdown: float | None


@dataclass(frozen=True)
class Flag:
    level: str  # "High" or "Watch"
    title: str
    detail: str


@dataclass
class RiskReport:
    total: Decimal | None
    holdings: list[HoldingRisk]
    top1: Decimal | None
    top3: Decimal | None
    effective_holdings: float | None
    portfolio_volatility: float | None
    portfolio_drawdown: float | None
    exposures: dict[str, list[tuple[str, Decimal]]]
    flags: list[Flag] = field(default_factory=list)
    without_history: list[str] = field(default_factory=list)


def exposure(view, group):
    """Percent of SGD market value per group label, largest first."""
    if not view.value:
        return []
    return [(g.label, g.value * HUNDRED / view.value) for g in view.allocation_groups(group)]


def risk_report(view, histories, today):
    """Concentration, volatility and drawdown for the current holdings.

    Portfolio volatility and drawdown replay *today's* weights over the common trading days,
    in each stock's own currency, so they describe the present mix rather than past trading.
    """
    rows = [r for r in view.rows if r.value is not None]
    weights = {r.position.symbol: view.weight(r) for r in rows}
    holdings, paths, missing = [], {}, []
    for r in rows:
        symbol = r.position.symbol
        closes = recent_closes(histories.get(symbol), today)
        returns = daily_returns(closes)
        vol = annual_volatility(returns)
        if len(closes) < 20:
            missing.append(symbol)
            holdings.append(HoldingRisk(symbol, weights[symbol], None, None, None))
            continue
        worst, current = drawdowns([c for _, c in closes])
        paths[symbol] = dict(closes)
        holdings.append(HoldingRisk(symbol, weights[symbol], vol, worst, current))

    ordered = sorted((w for w in weights.values() if w is not None), reverse=True)
    top1 = ordered[0] if ordered else None
    top3 = sum(ordered[:3], ZERO) if ordered else None
    hhi = sum(((w / HUNDRED) ** 2 for w in ordered), ZERO)
    effective = float(1 / hhi) if hhi else None

    port_vol = port_dd = None
    if paths and view.value is not None:
        common = sorted(set.intersection(*(set(p) for p in paths.values())))
        scale = sum((weights[s] for s in paths), ZERO)
        if len(common) >= 21 and scale:
            index, level = [], 1.0
            for a, b in zip(common, common[1:]):
                level *= 1 + sum(
                    float(weights[s] / scale) * (paths[s][b] / paths[s][a] - 1) for s in paths
                )
                index.append(level)
            returns = [index[0] - 1] + [y / x - 1 for x, y in zip(index, index[1:])]
            port_vol = annual_volatility(returns)
            port_dd = drawdowns([1.0, *index])[0]

    exposures = {g: exposure(view, g) for g in ("Holding", "Sector", "Market", "Currency")}
    report = RiskReport(
        view.value, holdings, top1, top3, effective, port_vol, port_dd, exposures,
        without_history=missing,
    )
    report.flags = risk_flags(report)
    return report


def risk_flags(report):
    """Plain-language warnings, most severe first. O(n)."""
    flags = []
    for h in report.holdings:
        if h.weight is not None and h.weight > SINGLE_POSITION_LIMIT:
            flags.append(Flag(
                "High", f"{h.symbol} is {h.weight:.1f}% of the portfolio",
                f"One holding above {SINGLE_POSITION_LIMIT}% means a single bad result moves the whole portfolio.",
            ))
    if report.top3 is not None and report.top3 > TOP3_LIMIT:
        flags.append(Flag(
            "Watch", f"Top three holdings make up {report.top3:.1f}%",
            f"Concentration above {TOP3_LIMIT}% in three stocks reduces diversification.",
        ))
    for group in ("Sector", "Market"):
        for label, share in report.exposures.get(group, []):
            if share > GROUP_LIMIT and label not in ("Unclassified", "ETF"):
                flags.append(Flag(
                    "Watch", f"{share:.1f}% in {label} ({group.lower()})",
                    f"More than {GROUP_LIMIT}% in one {group.lower()} ties results to that area.",
                ))
    foreign = sum((s for c, s in report.exposures.get("Currency", []) if c != SGD), ZERO)
    if foreign > FOREIGN_CURRENCY_LIMIT:
        flags.append(Flag(
            "Watch", f"{foreign:.1f}% is in foreign currencies",
            "Exchange-rate moves against SGD will change your SGD value even if prices are flat.",
        ))
    for h in report.holdings:
        if h.current_drawdown is not None and h.current_drawdown <= -DRAWDOWN_LIMIT:
            flags.append(Flag(
                "Watch", f"{h.symbol} is {abs(h.current_drawdown):.0f}% below its 1-year high",
                "Review whether the original reason for holding it still applies.",
            ))
        if h.volatility is not None and h.volatility >= VOLATILITY_LIMIT:
            flags.append(Flag(
                "Watch", f"{h.symbol} is highly volatile ({h.volatility:.0f}% a year)",
                "Expect large price swings. Size the position accordingly.",
            ))
    return sorted(flags, key=lambda f: f.level != "High")


# --------------------------------------------------------------------------- rebalance


@dataclass(frozen=True)
class RebalanceLine:
    symbol: str
    value: Decimal
    weight: Decimal
    target: Decimal | None  # None means "hold as is"
    drift: Decimal | None  # current minus target, in percentage points
    shares: Decimal  # signed shares (whole, or six decimals if fractional): positive buys, negative sells
    amount: Decimal  # signed SGD value of those shares
    after: Decimal  # weight after the trade


@dataclass
class RebalancePlan:
    lines: list[RebalanceLine]
    total: Decimal
    cash_used: Decimal
    cash_left: Decimal
    reserve: Decimal  # target weight deliberately left uninvested


def parse_percent(raw, label="Target"):
    try:
        value = Decimal(str(raw).strip().rstrip("%"))
    except Exception:
        raise PortfolioError(f"{label} must be a number between 0 and 100.") from None
    if not value.is_finite() or not ZERO <= value <= HUNDRED:
        raise PortfolioError(f"{label} must be between 0 and 100.")
    return value


FRACTION = Decimal("0.000001")  # fractional shares use six decimal places, as in the ledger


def plan_rebalance(
    view,
    targets,
    new_cash=ZERO,
    mode="Full rebalance",
    band=Decimal(2),
    *,
    fractional=False,
    min_trade=ZERO,
    withdraw=ZERO,
):
    """Size the trades that move current weights to the targets.

    targets: {symbol: percent}. A holding without a target is left alone and keeps its weight.
    new_cash: extra SGD to invest. mode "Buy only" never sells and spends at most new_cash.
    band: ignore drifts smaller than this many percentage points to avoid pointless fees.
    Trades are whole shares, or six-decimal fractions with fractional=True, rounded toward zero,
    so the result never overspends or oversells. Brokerage fees and tax are not modelled.
    min_trade: skip any single trade worth less than this many SGD.
    withdraw: SGD to take out. Sales fund it, so it needs a mode that allows selling. A negative
    cash_left is the amount the suggested sales fall short of the withdrawal.
    """
    rows = [r for r in view.rows if r.value is not None and r.position.qty > 0]
    if len(rows) != len(view.rows) or not rows:
        raise PortfolioError("Prices or exchange rates are missing for some holdings.")
    if new_cash < 0 or withdraw < 0 or min_trade < 0:
        raise PortfolioError("Cash, withdrawal and minimum trade cannot be negative.")
    if withdraw and mode == "Buy only":
        raise PortfolioError("A withdrawal needs sales. Turn on Allow sales.")
    if withdraw > view.value + new_cash:
        raise PortfolioError("The withdrawal is larger than the portfolio value.")
    total = view.value + new_cash - withdraw
    if total <= 0:
        raise PortfolioError("Nothing would remain invested after the withdrawal.")
    current = {r.position.symbol: r.value * HUNDRED / total for r in rows}
    fixed = sum((current[s] for s in current if s not in targets), ZERO)
    wanted = sum((targets[s] for s in current if s in targets), ZERO)
    # Allow half a point of slack so one-decimal weights that add to 100.2% still work.
    if wanted + fixed > HUNDRED + Decimal("0.5"):
        raise PortfolioError(
            f"Targets add up to {wanted + fixed:.1f}% including holdings without a target. "
            "Lower a target so the total is at most 100%."
        )
    desired = {s: total * targets[s] / HUNDRED for s in current if s in targets}
    gaps = {r.position.symbol: desired[r.position.symbol] - r.value
            for r in rows if r.position.symbol in desired}
    traded = {s: g for s, g in gaps.items() if abs(g) * HUNDRED / total >= band}

    # Sells come first (whole shares, never more than held); they fund the buys.
    prices = {r.position.symbol: r.value / r.position.qty for r in rows}
    step = FRACTION if fractional else Decimal(1)

    def round_shares(amount, price):
        return (amount / price).quantize(step, rounding=ROUND_DOWN)

    sell_shares = {}
    if mode != "Buy only":
        for r in rows:
            s = r.position.symbol
            if traded.get(s, ZERO) < 0:
                shares = max(round_shares(traded[s], prices[s]), -r.position.qty)
                if abs(shares) * prices[s] >= min_trade:
                    sell_shares[s] = shares
    funds = new_cash - withdraw - sum((sh * prices[s] for s, sh in sell_shares.items()), ZERO)
    wants = {s: g for s, g in traded.items() if g > 0}
    need = sum(wants.values(), ZERO)
    scale = min(Decimal(1), funds / need) if need else ZERO
    buy_shares = {s: round_shares(g * scale, prices[s]) for s, g in wants.items()}
    buy_shares = {s: n for s, n in buy_shares.items() if n * prices[s] >= min_trade}

    lines, spent = [], ZERO
    for r in rows:
        symbol, price = r.position.symbol, prices[r.position.symbol]
        shares = buy_shares.get(symbol, sell_shares.get(symbol, ZERO))
        amount = shares * price
        spent += amount
        target = targets.get(symbol)
        lines.append(RebalanceLine(
            symbol, r.value, current[symbol], target,
            current[symbol] - target if target is not None else None,
            shares, amount, (r.value + amount) * HUNDRED / total,
        ))
    # Buys never exceed new cash plus sale proceeds. Only a withdrawal can leave a shortfall.
    left = new_cash - withdraw - spent
    return RebalancePlan(lines, total, max(spent, ZERO), left, max(ZERO, HUNDRED - wanted - fixed))


# --------------------------------------------------------------------------- digest


@dataclass(frozen=True)
class Mover:
    symbol: str
    pct: float
    change: Decimal | None  # SGD value change on today's holding


@dataclass
class Digest:
    as_of: date
    value: Decimal | None
    day_change: Decimal | None
    day_change_pct: float | None
    movers: list[Mover]
    unrealised: Decimal | None
    realised_month: Decimal | None
    dividends_month: Decimal | None
    upcoming: list[tuple[str, date, Decimal | None]]
    alerts: list[str]
    flags: list[Flag]
    headlines: list[str]
    stale: list[str]


def day_moves(view, histories, rates):
    """Latest close versus the one before it, for every holding with at least two closes."""
    moves = []
    for r in view.rows:
        series = histories.get(r.position.symbol)
        if not series or len(series.days) < 2:
            continue
        before, after = series.days[-2:]
        if any(before < day <= after and day not in series.split_factors for day in series.splits):
            continue
        values = series.comparable_closes([before, after])
        previous, last = values[before], values[after]
        change = in_sgd((last - previous) * r.position.qty, r.position.currency, rates)
        moves.append(Mover(r.position.symbol, float((last / previous - 1) * 100), change))
    return sorted(moves, key=lambda m: -abs(m.pct))


def build_digest(view, sales, histories, payments, watch_state, headlines, risk, rates, today):
    """Collect what changed and what needs attention into one structure.

    The figures come from cached quotes and daily history, so the digest is only as fresh as the
    last refresh. Sections that lack data are left empty rather than guessed.
    """
    moves = day_moves(view, histories, rates)
    changes = [m.change for m in moves if m.change is not None]
    day_change = sum(changes, ZERO) if changes and len(changes) == len(moves) else None
    start_value = view.value - day_change if day_change is not None and view.value else None
    month_key = today.strftime("%Y-%m")
    realised = sales.totals[month_key].profit if month_key in sales.totals else ZERO
    received = [d for d in payments if d.status == "Received" and d.day.strftime("%Y-%m") == month_key]
    soon = sorted(
        (d for d in payments if d.status == "Expected" and today <= d.day <= today + timedelta(days=30)),
        key=lambda d: d.day,
    )
    unread = [e["message"] for e in watch_state.get("events", []) if not e.get("read")]
    stale = [r.position.symbol for r in view.rows if r.quote is None or (today - r.quote.asof.date()).days > 4]
    return Digest(
        today, view.value, day_change,
        float(day_change * 100 / start_value) if day_change is not None and start_value else None,
        moves[:3], view.gain, realised,
        sum((in_sgd(d.net, d.currency, rates) or ZERO for d in received), ZERO),
        [(d.symbol, d.day, in_sgd(d.net, d.currency, rates)) for d in soon[:5]],
        unread[:5], risk.flags[:3], headlines[:3], stale,
    )


def digest_text(d):
    """Plain-text version used by the email and the --digest command."""
    def pct(x):
        return percent(Decimal(str(round(x, 2))))

    def money(v, signed=False):
        return dollars(v, signed, SGD) if v is not None else "n/a"

    lines = [f"Folio Ledger daily digest · {d.as_of:%d %b %Y}", ""]
    change = "n/a" if d.day_change is None else f"{money(d.day_change, True)} ({pct(d.day_change_pct)})"
    lines += [
        f"Portfolio value: {money(d.value)}",
        f"Change at latest close: {change}",
        f"Unrealised P/L: {money(d.unrealised, True)}",
        f"Realised P/L this month: {money(d.realised_month, True)}",
        f"Dividends received this month: {money(d.dividends_month)}",
        "",
    ]
    if d.movers:
        lines.append("Biggest movers: " + ", ".join(f"{m.symbol} {pct(m.pct)}" for m in d.movers))
    if d.upcoming:
        lines.append("Dividends due in 30 days: " + ", ".join(
            f"{s} {day:%d %b} {money(v)}" for s, day, v in d.upcoming))
    if d.alerts:
        lines += ["", "Unread price alerts:", *(f"  - {a}" for a in d.alerts)]
    if d.flags:
        lines += ["", "Risk watch:", *(f"  - {f.title}" for f in d.flags)]
    if d.headlines:
        lines += ["", "Headlines:", *(f"  - {h}" for h in d.headlines)]
    if d.stale:
        lines += ["", "Prices may be out of date for: " + ", ".join(d.stale)]
    lines += ["", "Information only, not financial advice."]
    return "\n".join(lines)


@dataclass(frozen=True)
class Slice:
    label: str
    value: Decimal
    basis: Decimal | None = None  # doughnut() reads this to size the cost-basis tick


def revised_allocation(view, plan, group, after=True):
    """Slices of SGD value by group, optionally after the planned trades, with unspent cash.

    group is Holding, Sector, Market or Currency. Every group is returned. Trades are not tied to a brokerage account, so
    Brokerage is not offered here. A shortfall (negative cash) is not drawn.
    """
    change = {l.symbol: l.amount for l in plan.lines} if after else {}
    totals = {}
    for r in view.rows:
        label = view.row_label(r, group)
        totals[label] = totals.get(label, ZERO) + r.value + change.get(r.position.symbol, ZERO)
    if after and plan.cash_left > 0:
        totals["Cash"] = plan.cash_left
    slices = sorted(
        (Slice(k, v) for k, v in totals.items() if v > 0), key=lambda x: (-x.value, x.label)
    )
    return slices
