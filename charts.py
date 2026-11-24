# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Browser and desktop charts with native tap targets and accessible value tables."""

from bisect import bisect_left
from calendar import month_abbr
from datetime import timedelta
from functools import lru_cache
from io import BytesIO
from math import atan2, degrees, hypot
from threading import Lock

import flet as ft
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import Circle, Wedge
from matplotlib.ticker import FuncFormatter, MaxNLocator

from decimal import Decimal

from portfolio import ZERO, dollars, percent
from widgets import AMBER, BLUE, MUTED, NAVY, OUTLINE, RED, RULE, TINT, pager, text, tone

CHART_LOCK = Lock()
HEIGHT = 210


def legend(items, *, dashed_second=False):
    def key(i, colour):
        if dashed_second and i == 1:
            return ft.Row(
                [ft.Container(width=7, height=3, bgcolor=colour) for _ in range(2)],
                spacing=3,
                tight=True,
            )
        return ft.Container(width=17, height=4, bgcolor=colour, border_radius=2)

    return ft.Row(
        [
            ft.Row(
                [key(i, c), text(n, 12, MUTED)],
                tight=True,
                spacing=6,
            )
            for i, (n, c) in enumerate(items)
        ],
        wrap=True,
        spacing=18,
    )


def png(figure):
    output = BytesIO()
    FigureCanvasAgg(figure).print_png(output)
    return output.getvalue()


@lru_cache(maxsize=24)
def ring_png(parts, background):
    with CHART_LOCK:
        fig = Figure(figsize=(2.38, 2.38), dpi=190, facecolor="none")
        ax = fig.add_axes([0, 0, 1, 1], aspect="equal", xlim=(-1.12, 1.12), ylim=(-1.12, 1.12))
        ax.set_axis_off()
        angle = 90.0
        for frac, shift, colour in parts:
            stop = angle - 360 * frac
            radius = 0.97 + max(-0.09, min(0.09, shift))
            ax.add_patch(
                Wedge(
                    (0, 0),
                    radius,
                    stop,
                    angle,
                    width=radius - 0.59,
                    facecolor=colour,
                    edgecolor=background,
                    linewidth=1,
                )
            )
            angle = stop
        ax.add_patch(
            Circle(
                (0, 0),
                0.97,
                fill=False,
                edgecolor=OUTLINE,
                linewidth=0.48,
                linestyle=(0, (2, 4)),
                alpha=0.72,
            )
        )
        return png(fig)


def doughnut(groups, total, basis, colours, background, select, size=238):
    visible = [(i, g) for i, g in enumerate(groups) if g.value > 0]
    parts = []
    for i, g in visible:
        weight = float(g.value / total)
        cost_weight = float(g.basis / basis) if basis and g.basis is not None else weight
        parts.append((weight, weight - cost_weight, colours[i]))

    def tapped(e):
        if e.local_position is None:
            return
        x, y = e.local_position.x - size / 2, e.local_position.y - size / 2
        radius = hypot(x, y) * 2.24 / size
        angle = (degrees(atan2(x, -y)) % 360) / 360
        end = 0
        for (_, g), (weight, shift, colour) in zip(visible, parts):
            end += weight
            if angle < end:
                if 0.59 <= radius <= 0.97 + max(-0.09, min(0.09, shift)):
                    select(g)
                return

    img = ft.Image(
        src=ring_png(tuple(parts), background),
        width=size,
        height=size,
        semantics_label="Allocation by current market value. Each group is also available in the table.",
    )
    return ft.GestureDetector(
        content=img, width=size, height=size, on_tap_down=tapped, mouse_cursor=ft.MouseCursor.CLICK
    )


def compact(value, _):
    for scale, unit in ((1e9, "b"), (1e6, "m"), (1e3, "k")):
        if abs(value) >= scale:
            return f"{value / scale:g}{unit}"
    return f"{value:g}"


def axes(width):
    fig = Figure(figsize=(width / 100, HEIGHT / 100), dpi=160, facecolor="none")
    # Fixed pixel margins also locate a tapped month or date on a resized chart.
    ax = fig.add_axes([55 / width, 0.18, 1 - 72 / width, 0.75])
    ax.set_facecolor("none")
    ax.set_axisbelow(True)
    ax.grid(axis="y", color=RULE, linewidth=0.7)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color(RULE)
    ax.tick_params(axis="both", colors=MUTED, labelsize=8, length=0, pad=6)
    ax.yaxis.set_major_formatter(FuncFormatter(compact))
    ax.yaxis.set_major_locator(MaxNLocator(nbins=4, min_n_ticks=2))
    return fig, ax


PLOT_LEFT, PLOT_RIGHT_GAP = 55, 17  # pixel margins set by axes()
PLOT_TOP, PLOT_BOTTOM = round(HEIGHT * 0.07), round(HEIGHT * 0.82)  # axes box: 0.18 to 0.93 high


def tappable(image, width, label, handler, guide=False):
    """A chart picture that reports the figures under a tap.

    guide=True is for time-series lines: a thin vertical line follows the pointer across the plot
    area and the figures update as it moves, so a value can be read against the axis.
    """
    detail = text("Select a point on the chart for exact figures.", 12, MUTED)
    line = ft.Container(
        left=PLOT_LEFT,
        top=PLOT_TOP,
        width=1,
        height=PLOT_BOTTOM - PLOT_TOP,
        bgcolor=NAVY,
        opacity=0.55,
        visible=False,
    )

    def locate(event):
        if event.local_position is None:
            return None
        x = max(PLOT_LEFT, min(width - PLOT_RIGHT_GAP, event.local_position.x))
        return x, (x - PLOT_LEFT) / (width - PLOT_LEFT - PLOT_RIGHT_GAP)

    def tapped(event):
        found = locate(event)
        if found is None:
            return
        x, fraction = found
        detail.value = handler(fraction)
        detail.update()
        if guide:
            line.left, line.visible = x, True
            line.update()

    def hovered(event):
        found = locate(event)
        if found is None:
            return
        x, fraction = found
        line.left, line.visible = x, True
        detail.value = handler(fraction)
        line.update()
        detail.update()

    def left(event):
        line.visible = False
        line.update()

    picture = ft.Image(
        src=image, width=width, height=HEIGHT, fit=ft.BoxFit.FILL, semantics_label=label
    )
    content = ft.Stack([picture, line], width=width, height=HEIGHT) if guide else picture
    return [
        ft.Row(
            [
                ft.GestureDetector(
                    content=content,
                    width=width,
                    height=HEIGHT,
                    on_tap_down=tapped,
                    on_hover=hovered if guide else None,
                    on_exit=left if guide else None,
                    mouse_cursor=ft.MouseCursor.PRECISE if guide else ft.MouseCursor.CLICK,
                )
            ],
            scroll=ft.ScrollMode.AUTO,
            spacing=0,
        ),
        detail,
    ]


def bars_png(data, width):
    with CHART_LOCK:
        fig, ax = axes(width)
        for i, colour in enumerate((NAVY, AMBER)):
            for m, values in enumerate(data, 1):
                if values[i] is not None:
                    ax.bar(
                        m + (i - 0.5) * 0.34,
                        float(values[i]),
                        width=0.31,
                        color=colour,
                        hatch="//" if i else None,
                        edgecolor="#76450D" if i else NAVY,
                        linewidth=0.5,
                    )
        peak = max([float(n) for pair in data for n in pair if n is not None] + [1])
        ax.set_ylim(0, peak * 1.11)
        ax.set_xlim(0.5, 12.5)
        ax.set_xticks(
            range(1, 13),
            [month_abbr[m] if width >= 450 else month_abbr[m][0] for m in range(1, 13)],
        )
        return png(fig)


def income_chart(months, width):
    data = tuple(
        tuple(months.get((m, status), ZERO) for status in ("Received", "Expected"))
        for m in range(1, 13)
    )

    def detail(f):
        i = min(11, int(f * 12))
        return f"{month_abbr[i + 1]}  ·  Received {dollars(data[i][0], currency='SGD')}  ·  Expected {dollars(data[i][1], currency='SGD')}"

    return [
        legend([("Received", NAVY), ("Expected (hatched)", AMBER)]),
        *tappable(
            bars_png(data, width),
            width,
            "Monthly net income. Received and expected dividends are shown in SGD.",
            detail,
        ),
    ]


def stock_profit_chart(
    amounts,
    currency,
    width,
    page,
    navigate,
    *,
    as_percent=False,
    missing="FX needed",
    signed=True,
    cost_yields=None,
):
    """Show each ticker against a common zero line, keeping the scale across pages."""

    def formatted(value):
        if value is None:
            return missing
        return (
            (percent(value) if signed else f"{value:,.2f}%")
            if as_percent
            else dollars(value, signed, currency)
        )

    values = [v for _, v in amounts if v is not None]
    if cost_yields is not None:
        values.extend(v for v in cost_yields.values() if v is not None)
    low, high = min([ZERO, *values]), max([ZERO, *values])
    track = max(60, round(width * 0.42))
    span = high - low or 1
    origin = float(-low / span) * (track - 2) + 1
    size = 8
    page = min(page, max(0, (len(amounts) - 1) // size))
    rows = []
    for symbol, value in amounts[page * size : (page + 1) * size]:
        marks = [ft.Container(left=0, top=8, width=track, height=1, bgcolor=RULE)]
        if value:
            length = max(1, float(abs(value) / span) * (track - 2))
            marks.append(
                ft.Container(
                    left=origin if value > 0 else max(0, origin - length),
                    top=3,
                    width=length,
                    height=11,
                    border_radius=2,
                    bgcolor=tone(value) if signed else BLUE,
                )
            )
        marks.append(ft.Container(left=origin, top=0, width=1, height=18, bgcolor=OUTLINE))
        reference = cost_yields.get(symbol) if cost_yields is not None else None
        if reference is not None:
            x = max(0, min(track - 3, origin + float(reference / span) * (track - 2) - 1))
            marks.append(ft.Container(left=x, top=0, width=3, height=17, bgcolor=AMBER))

        row = ft.Row(
            [
                ft.Container(text(symbol, 13, bold=True), expand=3),
                ft.Stack(marks, width=track, height=18),
                ft.Container(
                    text(
                        formatted(value),
                        13,
                        tone(value) if signed else BLUE,
                        True,
                        text_align=ft.TextAlign.RIGHT,
                    ),
                    expand=4,
                ),
            ],
            spacing=7,
        )
        spoken = formatted(value)
        if reference is not None:
            spoken += f" · yield on cost {formatted(reference)}"
        rows.append(
            ft.Semantics(
                content=row, label=f"{symbol} · {spoken}", container=True, exclude_semantics=True
            )
        )
    if len(amounts) > size:
        rows.append(pager(page, len(amounts), size, navigate))
    return ft.Column(rows, spacing=12)


@lru_cache(maxsize=24)
def monthly_profit_png(months, width, selected):
    with CHART_LOCK:
        fig, ax = axes(width)
        if selected:
            ax.axvspan(selected - 0.45, selected + 0.45, color=TINT, zorder=0)
        known = [float(value) for value, count in months if value is not None and count]
        low, high = min([0, *known]), max([0, *known])
        padding = (high - low) * 0.15 or 1
        ax.set_ylim(low - padding, high + padding)
        for month, (value, count) in enumerate(months, 1):
            if not count:
                continue
            if value is None:
                ax.annotate("?", (month, 0), ha="center", va="bottom", color=MUTED, weight="bold")
            elif value == 0:
                ax.plot(month, 0, marker="_", color=MUTED, markersize=7)
            else:
                ax.bar(month, float(value), width=0.58, color=tone(value))
        ax.axhline(0, color=OUTLINE, linewidth=0.9)
        ax.set_xlim(0.5, 12.5)
        ax.set_xticks(
            range(1, 13), [month_abbr[m] if width >= 405 or m % 2 else "" for m in range(1, 13)]
        )
        return png(fig)


def monthly_profit_chart(report, year, currency, width, selected):
    months = []
    for i in range(1, 13):
        key = f"{year}-{i:02}"
        amounts = report.totals.get(key)
        months.append((amounts.profit, len(report.rows_by_month[key])) if amounts else (ZERO, 0))

    def detail(fraction):
        index = min(11, int(fraction * 12))
        value, count = months[index]
        amount = dollars(value, True, currency) if value is not None else "Awaiting exchange rate"
        return f"{month_abbr[index + 1]} {year} · {amount} · {count} completed " + (
            "sale" if count == 1 else "sales"
        )

    chart = tappable(
        monthly_profit_png(tuple(months), width, selected),
        width,
        f"Monthly realised P/L for {year} in {currency}. Exact values are in Monthly summary.",
        detail,
    )
    if any(value is None for value, _ in months):
        chart.append(text("A question mark indicates a missing exchange rate.", 12, MUTED))
    return chart


@lru_cache(maxsize=16)
def lines_png(xvals, lines, labels, width):
    with CHART_LOCK:
        fig, ax = axes(width)
        for index, (values, colour) in enumerate(lines):
            ax.plot(
                xvals,
                [float(v) if v is not None else float("nan") for v in values],
                color=colour,
                linewidth=1.8,
                linestyle="--" if index == 1 else "-",
                solid_capstyle="round",
            )
            for i, v in enumerate(values):
                if (
                    v is not None
                    and (i == 0 or values[i - 1] is None)
                    and (i == len(values) - 1 or values[i + 1] is None)
                ):
                    ax.plot(xvals[i], float(v), "o", color=colour, markersize=4)
        ax.axhline(0, color=OUTLINE, linewidth=0.7)
        ax.set_xlim(0, max(1, xvals[-1]))
        ax.set_xticks([a for a, _ in labels], [t for _, t in labels])
        return png(fig)


def trend_chart(rows, specs, width, *, as_percent=False):
    if not any(getattr(r, k) is not None for r in rows for k, _, _ in specs):
        return [text("Waiting for historical prices or exchange rates for this period.", 14, MUTED)]
    xvals = tuple((r.day - rows[0].day).days for r in rows)
    end = xvals[-1]
    ticks = sorted({0, end // 2, end} if width < 580 else {round(i * end / 4) for i in range(5)})
    fmt = "%b %y" if end > 480 else "%d %b"
    labels = tuple((i, (rows[0].day + timedelta(days=i)).strftime(fmt)) for i in ticks)
    lines = tuple((tuple(getattr(r, k) for r in rows), c) for k, _, c in specs)

    def detail(f):
        x = f * end
        i = bisect_left(xvals, x)
        if i and (i == len(rows) or x - xvals[i - 1] < xvals[i] - x):
            i -= 1
        r = rows[i]
        vals = []
        for key, name, colour in specs:
            v = getattr(r, key)
            vals.append(f"{name}  {percent(v) if as_percent else dollars(v, currency='SGD')}")
        return f"{r.day:%d %b %Y}  ·  " + "  ·  ".join(vals)

    return [
        legend([(n, c) for _, n, c in specs], dashed_second=True),
        *tappable(
            lines_png(xvals, lines, labels, width),
            width,
            "Historical chart of " + ", ".join(n for _, n, _ in specs),
            detail,
            guide=True,
        ),
    ]


@lru_cache(maxsize=32)
def columns_png(labels, series, width, mode, percentage, annotate):
    """Render signed, grouped or stacked bars. Missing inputs are marked at the baseline."""
    with CHART_LOCK:
        fig, ax = axes(width)
        n = len(labels)
        bottoms = [0.0] * n
        values = [0.0]
        for idx, (_, data, colour) in enumerate(series):
            for i, amount in enumerate(data):
                x = i if mode not in ("paired", "grouped") else i + ((idx + 0.5) / len(series) - 0.5) * 0.79
                if amount is None:
                    ax.text(x, 0, "?", color=MUTED, fontsize=7, ha="center", va="bottom")
                    continue
                val = float(amount)
                bottom = bottoms[i] if mode == "stacked" else 0.0
                if mode == "stacked":
                    bottoms[i] += val
                shade = (tone(val) if val else MUTED) if mode == "signed" else colour
                ax.bar(
                    x,
                    val,
                    bottom=bottom,
                    width=0.68 / (len(series) if mode in ("paired", "grouped") else 1),
                    color=shade,
                    hatch="//" if mode == "paired" and idx == 1 else None,
                    edgecolor=shade,
                    linewidth=0.4,
                )
                values.append(bottom + val)
                if annotate and val and n <= 15 and (width >= 505 or n <= 6):
                    word = f"{val:.1f}%" if percentage else compact(round(val, 2), None)
                    ax.annotate(
                        word,
                        (x, val),
                        xytext=(0, 3 if val >= 0 else -3),
                        textcoords="offset points",
                        ha="center",
                        va="bottom" if val >= 0 else "top",
                        fontsize=7,
                        color=MUTED,
                    )
        lo, hi = min(values), max(values)
        padding = (hi - lo) * 0.24 or 1.0
        ax.set_ylim(lo - (padding if lo < 0 else 0), hi + padding)
        ax.axhline(0, color=OUTLINE, linewidth=0.8)
        ax.set_xlim(-0.5, max(0.5, n - 0.5))
        # Names of groups are never skipped, or an unlabelled bar would be unreadable. They tilt
        # when crowded. Dated axes (month labels) may skip labels to stay readable.
        longest = max((len(str(x).split("\n")[0]) for x in labels), default=1)
        dated = all(any(ch.isdigit() for ch in str(x)) for x in labels)
        if n <= 30 and not dated:
            ax.set_xticks(range(n), [str(x) for x in labels])
            if longest * 6.2 > (width - 72) / max(1, n):
                for label in ax.get_xticklabels():
                    label.set_rotation(40)
                    label.set_ha("right")
                    label.set_fontsize(8)
                ax.set_position([55 / width, 0.30, 1 - 72 / width, 0.63])
        else:
            stride = max(1, (n + max(3, width // 60) - 1) // max(3, width // 60))
            ticks = list(range(0, n, stride))
            ax.set_xticks(ticks, [labels[i] for i in ticks])
        if percentage:
            ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}%"))
        return png(fig)


def columns_chart(
    labels, series, width, title, *, mode="stacked", percentage=False, annotate=False
):
    labels = tuple(labels)
    series = tuple((name, tuple(nums), colour) for name, nums, colour in series)
    if not labels or not series:
        return [text("No matching records for this selection.", 14, MUTED)]

    def detail(f):
        i = min(len(labels) - 1, int(f * len(labels)))
        fmt = percent if percentage else lambda v: dollars(v, currency="SGD")
        return "  ·  ".join(
            [labels[i].replace("\n", " "), *(f"{name}  {fmt(v[i])}" for name, v, _ in series)]
        )

    image = columns_png(labels, series, width, mode, percentage, annotate)
    return [
        legend([(name, colour) for name, _, colour in series])
        if mode != "signed"
        else legend([("Gain", tone(1)), ("Loss", RED)]),
        *tappable(image, width, title, detail),
    ]


PRICE_PERIODS = ("1M", "3M", "6M", "1Y", "All")
PERIOD_DAYS = {"1M": 31, "3M": 92, "6M": 183, "1Y": 366}


@lru_cache(maxsize=24)
def price_png(xvals, values, cost, buys, sells, colour, labels, width):
    """Closing-price line with a soft fill, a dashed average-cost line and trade markers.

    All arguments are tuples so the picture can be cached. buys and sells are (x, price) pairs.
    """
    with CHART_LOCK:
        fig, ax = axes(width)
        ax.plot(xvals, values, color=colour, linewidth=1.8, solid_capstyle="round")
        ax.fill_between(xvals, values, min(values), color=colour, alpha=0.08, linewidth=0)
        if cost is not None:
            ax.axhline(cost, color=AMBER, linewidth=1.2, linestyle=(0, (4, 3)))
        if buys:
            ax.plot(*zip(*buys), "^", color=NAVY, markersize=8, markeredgecolor="white")
        if sells:
            ax.plot(*zip(*sells), "v", color=AMBER, markersize=8, markeredgecolor="white")
        low, high = min(values), max(values)
        if cost is not None:
            low, high = min(low, cost), max(high, cost)
        pad = (high - low) * 0.12 or 1
        ax.set_ylim(low - pad, high + pad)
        ax.set_xlim(0, max(1, xvals[-1]))
        ax.set_xticks([a for a, _ in labels], [t for _, t in labels])
        return png(fig)


def price_chart(series, symbol, currency, average, trades, period, width, choose_period):
    """Market price history for one stock, with period buttons. Returns a list of controls.

    Cost: O(d) to slice the cached daily closes, O(log d) per trade marker (bisect on the
    sorted day list). The picture is cached per period and width.
    """
    chips = ft.Row(
        [
            ft.TextButton(
                name,
                on_click=lambda e, n=name: choose_period(n),
                style=ft.ButtonStyle(
                    color="#FFFFFF" if name == period else NAVY,
                    bgcolor=NAVY if name == period else "#D9FFFFFF",
                    side=ft.BorderSide(1, RULE),
                    shape=ft.RoundedRectangleBorder(radius=16),
                    padding=ft.Padding.symmetric(horizontal=14),
                ),
            )
            for name in PRICE_PERIODS
        ],
        spacing=6,
        wrap=True,
    )
    mine = [t for t in trades if t.symbol == symbol]
    if period == "All" and mine:
        start = min(t.day for t in mine) - timedelta(days=7)
    elif period in PERIOD_DAYS and series and series.days:
        start = series.days[-1] - timedelta(days=PERIOD_DAYS[period])
    else:
        start = series.days[0] if series and series.days else None
    days = [d for d in (series.days if series else ()) if start is not None and d >= start]
    if len(days) < 2:
        return [chips, text("No price history for this period yet. Use Refresh quote or open Performance.", 14, MUTED)]
    unknown_splits = [d for d in series.splits if days[0] < d <= days[-1] and d not in series.split_factors]
    if unknown_splits:
        days = [d for d in days if d >= max(unknown_splits)]
    if len(days) < 2:
        return [chips, text("Refresh market history for comparable prices across the stock split.", 14, MUTED)]
    first = days[0]
    xvals = tuple((d - first).days for d in days)
    adjusted = series.comparable_closes(days)
    values = tuple(float(adjusted[d]) for d in days)
    end = xvals[-1]
    ticks = sorted({0, end // 2, end} if width < 580 else {round(i * end / 4) for i in range(5)})
    fmt = "%b %y" if end > 400 else "%d %b"
    labels = tuple((i, (first + timedelta(days=i)).strftime(fmt)) for i in ticks)

    def marks(side):
        out = []
        for t in mine:
            if t.side == side and first <= t.day <= days[-1]:
                price = adjusted.get(t.day)
                if price is not None:
                    out.append(((t.day - first).days, float(price)))
        return tuple(out)

    change = (values[-1] / values[0] - 1) * 100 if values[0] else 0
    colour = "#00513F" if values[-1] >= values[0] else RED
    cost = float(average) if average else None

    def detail(f):
        i = min(range(len(xvals)), key=lambda k: abs(xvals[k] - f * end))
        line = f"{days[i]:%d %b %Y}  ·  Close {dollars(Decimal(str(values[i])), currency=currency)}"
        if cost:
            line += f"  ·  {percent(Decimal(str(round((values[i] / cost - 1) * 100, 2))))} versus your average cost"
        return line

    summary = text(
        f"{period} change {percent(Decimal(str(round(change, 2))))}   ·   "
        f"High {dollars(Decimal(str(max(values))), currency=currency)}   ·   "
        f"Low {dollars(Decimal(str(min(values))), currency=currency)}",
        13, colour, True,
    )
    key = [("Close", colour)] + ([("Your average cost", AMBER)] if cost else [])
    return [
        chips,
        summary,
        legend(key),
        *tappable(
            price_png(xvals, values, cost, marks("BUY"), marks("SELL"), colour, labels, width),
            width,
            f"{symbol} closing price, {period}",
            detail,
            guide=True,
        ),
        text("Triangles mark your buys (up) and sells (down). Daily closes from the cached history.", 12, MUTED),
    ]
