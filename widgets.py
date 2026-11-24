# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Shared Flet controls and display styles."""

from collections.abc import Callable
from dataclasses import dataclass

import flet as ft
import flet.canvas as cv

from frost_theme import (
    FROST,
    SANS,
    WHITE,
    glass,
    panel as surface,
    title as frost_title,
    txt,
)

ENTRY_TEXT_SIZE = 14
ENTRY_LABEL_SIZE = 12
ENTRY_HEIGHT = 48
ENTRY_MARGIN = 7

INK, MUTED, NAVY = FROST.ink, FROST.muted, FROST.accent
TINT, PAPER, RULE = FROST.wash, FROST.page, FROST.line
RED = FROST.negative
BLUE = "#2275A3"
AMBER = "#BD671F"
OUTLINE = "#778FA1"
ALLOCATION_COLOURS = FROST.chart
METRIC_ICON_FILLS = ("#DDEAF8", "#E6E4F7", "#D9EFEB", "#D9EBF6")


def app_theme():
    return ft.Theme(
        use_material3=True,
        font_family=SANS,
        scaffold_bgcolor=PAPER,
        canvas_color=WHITE,
        divider_color=RULE,
        dialog_theme=ft.DialogTheme(
            bgcolor="#F9FCFE",
            barrier_color="#69203850",
            shadow_color="#272D4D63",
            elevation=9,
            shape=ft.RoundedRectangleBorder(radius=20, side=ft.BorderSide(1, WHITE)),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        ),
        date_picker_theme=ft.DatePickerTheme(
            bgcolor="#F9FCFE",
            header_bgcolor=TINT,
            header_foreground_color=INK,
            divider_color=RULE,
            shape=ft.RoundedRectangleBorder(radius=20),
        ),
        popup_menu_theme=ft.PopupMenuTheme(
            color=WHITE,
            elevation=4,
            shadow_color="#392F526A",
            shape=ft.RoundedRectangleBorder(radius=13, side=ft.BorderSide(1, RULE)),
        ),
        snackbar_theme=ft.SnackBarTheme(bgcolor=NAVY, close_icon_color=WHITE),
        text_button_theme=ft.TextButtonTheme(style=ft.ButtonStyle(color=NAVY)),
        filled_button_theme=ft.FilledButtonTheme(
            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=10))
        ),
        color_scheme_seed=NAVY,
        color_scheme=ft.ColorScheme(
            primary=NAVY,
            on_primary=WHITE,
            primary_container=TINT,
            on_primary_container=NAVY,
            secondary=BLUE,
            on_secondary=WHITE,
            secondary_container=TINT,
            on_secondary_container=NAVY,
            error=RED,
            surface=WHITE,
            on_surface=INK,
            on_surface_variant=MUTED,
            outline=OUTLINE,
            outline_variant=RULE,
            surface_container_lowest=WHITE,
            surface_container_low=WHITE,
            surface_container=TINT,
            surface_container_high=TINT,
            surface_tint=WHITE,
        ),
    )


def text(value, size=14, color=INK, bold=False, **kwargs):
    return ft.Text(
        str(value),
        font_family=SANS,
        size=size,
        color=color,
        weight=ft.FontWeight.W_600 if bold else ft.FontWeight.NORMAL,
        **kwargs,
    )


def tone(value):
    return FROST.sign(value)


def heading(title, description, actions=(), size=46):
    """Page header shared by every tab, matching the Overview.

    A large title with a muted subtitle on the left and the page actions on the
    right. The Row wraps, so actions drop below the title on narrow windows.
    """
    name = frost_title(title, size)
    intro = ft.Column([name, txt(description, 13, MUTED, 500)], spacing=0, tight=True)
    body = intro
    if actions:
        body = ft.Row(
            [intro, ft.Row(list(actions), spacing=8, wrap=True, run_spacing=6, tight=True)],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.END,
            wrap=True,
            spacing=12,
            run_spacing=9,
        )

    def fit_title(event):
        font_size = min(size, 37) if event.width < 600 else size
        if name.size != font_size:
            name.size = font_size
            name.update()

    return ft.Container(body, on_size_change=fit_title)


def primary(label, handler, icon=None):
    return ft.FilledButton(
        label,
        icon=icon,
        on_click=handler,
        bgcolor=NAVY,
        color=WHITE,
        height=42,
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=10),
            text_style=ft.TextStyle(size=13, weight=ft.FontWeight.W_600),
        ),
    )


def secondary(label, handler, icon=None):
    return ft.OutlinedButton(
        label,
        icon=icon,
        on_click=handler,
        height=42,
        style=ft.ButtonStyle(
            color=NAVY,
            bgcolor="#D9FFFFFF",
            side=ft.BorderSide(1, RULE),
            shape=ft.RoundedRectangleBorder(radius=10),
            text_style=ft.TextStyle(size=13, weight=ft.FontWeight.W_600),
        ),
    )


def input_border():
    return {
        ft.ControlState.DEFAULT: ft.OutlineInputBorder(
            border_radius=10, side=ft.BorderSide(1, OUTLINE)
        ),
        ft.ControlState.FOCUSED: ft.OutlineInputBorder(
            border_radius=10, side=ft.BorderSide(2, BLUE)
        ),
    }


def field(label, value="", width=None, hint=None, **kwargs):
    kwargs.setdefault("text_size", ENTRY_TEXT_SIZE)
    kwargs.setdefault("label_style", ft.TextStyle(size=ENTRY_LABEL_SIZE, font_family=SANS))
    kwargs.setdefault("hint_style", ft.TextStyle(size=ENTRY_TEXT_SIZE, font_family=SANS))
    kwargs.setdefault("content_padding", ft.Padding.symmetric(horizontal=12, vertical=14))
    kwargs.setdefault("margin", ft.Margin.only(top=ENTRY_MARGIN))
    if not kwargs.get("multiline"):
        kwargs.setdefault("height", ENTRY_HEIGHT)
        if kwargs.get("max_length"):
            kwargs.setdefault("counter", "")
    return ft.TextField(
        label=label,
        value=value,
        width=width,
        hint_text=hint,
        dense=True,
        border=input_border(),
        filled=True,
        fill_color=FROST.input,
        **kwargs,
    )


def fit_width(label, items, minimum):
    """Width a dropdown needs so neither its label nor its longest option is cut off.

    Text is about 8.2 px a character at size 14 and 7 px at the label size. The extra covers the
    field padding and the arrow. The width never drops below the one the caller asked for.
    """
    longest = max((len(str(text_)) for _, text_ in items), default=0)
    return min(430, max(minimum, longest * 8.2 + 64, len(label) * 7 + 44))


def choose(label, items, selected, handler=None, width=210, *, option_controls=None):
    width = fit_width(label, items, width)
    return ft.Dropdown(
        label=label,
        tooltip=label,
        value=selected,
        options=option_controls or [ft.DropdownOption(key=k, text=v) for k, v in items],
        on_select=handler,
        width=width,
        margin=ft.Margin.only(top=ENTRY_MARGIN),
        height=ENTRY_HEIGHT,
        content_padding=ft.Padding.symmetric(horizontal=12, vertical=12),
        dense=True,
        text_size=ENTRY_TEXT_SIZE,
        text_style=ft.TextStyle(size=ENTRY_TEXT_SIZE, font_family=SANS),
        label_style=ft.TextStyle(size=ENTRY_LABEL_SIZE, font_family=SANS),
        border=input_border(),
        bgcolor=FROST.input,
        filled=True,
        fill_color=FROST.input,
        menu_height=340,
    )


def panel(controls, pad=19):
    return surface(FROST, ft.Column(controls, spacing=13), padding=pad)


def equal_height_row(cards, *, spacing=16, intrinsic_height=True):
    return ft.Row(cards, spacing=spacing, intrinsic_height=intrinsic_height,
                  vertical_alignment=(ft.CrossAxisAlignment.STRETCH if intrinsic_height
                                      else ft.CrossAxisAlignment.START))


def card_rows(cards, columns, spacing=12):
    return [
        equal_height_row(cards[start : start + columns], spacing=spacing)
        for start in range(0, len(cards), columns)
    ]


def pairs(cards, wide, weights=(1, 1), heights=None):
    """Lay cards out two to a row on wide windows. A last odd card takes the full row.

    heights gives one fixed height per row so the two cards in a row line up. A card whose
    content is taller than its row scrolls inside itself instead of spilling over.
    """
    if not wide:
        return list(cards)
    rows = []
    for n, i in enumerate(range(0, len(cards), 2)):
        chunk = list(cards[i : i + 2])
        if len(chunk) == 1:
            rows.append(chunk[0])
            continue
        height = heights[min(n, len(heights) - 1)] if heights else None
        for card, weight in zip(chunk, weights):
            card.expand = weight
            if height:
                card.height = height
                if isinstance(card.content, ft.Column):
                    card.content.scroll = ft.ScrollMode.AUTO
        rows.append(equal_height_row(chunk, spacing=16, intrinsic_height=height is None))
    return rows


@dataclass(frozen=True)
class Metric:
    label: str
    value: str
    note: str
    icon: ft.IconData
    percentage: str | None = None
    color: str = INK
    on_click: Callable | None = None
    tooltip: str | None = None


def metric_card(item, width, fill):
    compact = width < 215
    padding = 14 if compact else min(25, round(width * 0.08))
    room = width - 2 * padding - 4
    has_percentage = item.percentage not in (None, "—")
    inline = has_percentage and width >= 290
    if inline:
        room -= 8 + len(item.percentage) * 9
    fit = int(room / (max(1, len(item.value)) * 0.54))
    size = min(26 if compact else 28, max(14, fit))
    glyph = ft.Container(
        ft.Icon(item.icon, size=14, color=NAVY),
        width=23,
        height=23,
        alignment=ft.Alignment.CENTER,
        bgcolor=fill,
        border_radius=7,
    )
    label = txt(item.label, 12 if compact else 14, INK, 600, expand=True)
    title = ft.Row([glyph, label], spacing=7, vertical_alignment=ft.CrossAxisAlignment.CENTER)
    value = txt(
        item.value, size, item.color, 600, tracking=-0.65,
        max_lines=1, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS,
        expand=inline, expand_loose=inline, tooltip=item.value if fit < 17 else None,
    )
    figures = value
    if has_percentage:
        ratio = txt(item.percentage, 16, item.color, 700, no_wrap=True)
        figures = (
            ft.Row([value, ratio], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)
            if inline else ft.Column([value, ratio], spacing=0, tight=True)
        )
    return glass(
        ft.Column(
            [
                title,
                ft.Container(figures, padding=ft.Padding.only(top=4 if compact else 6)),
                txt(
                    item.note, 12 if compact else 13, MUTED, 500,
                    max_lines=2, overflow=ft.TextOverflow.ELLIPSIS,
                    tooltip=item.note if compact and len(item.note) > 32 else None,
                ),
            ],
            spacing=3,
            tight=True,
        ),
        width=width,
        height=146,
        padding=ft.Padding.only(
            left=padding, right=padding, top=16 if compact else 26, bottom=8,
        ),
        on_click=item.on_click,
        tooltip=item.tooltip,
    )


def metric_cards(width, items, *, header=False):
    """Use the Overview grid for every row of summary figures."""
    count = len(items)
    if count == 4 and width >= 900:
        columns = 4
    elif count == 3 and width >= 690:
        columns = 3
    else:
        columns = 2 if count > 1 and width >= 328 else 1
    gap = 24 if columns >= 3 else 11
    indexed = list(enumerate(items))
    rows = [indexed[i : i + columns] for i in range(0, count, columns)]
    if count == 3 and columns == 2:
        rows = [indexed[:1], indexed[1:]]
    controls = []
    for row in rows:
        card_width = (width - (len(row) - 1) * gap) / len(row)
        cards = [
            metric_card(item, card_width, METRIC_ICON_FILLS[i % len(METRIC_ICON_FILLS)])
            for i, item in row
        ]
        controls.append(ft.Row(cards, spacing=gap))
    return ft.Column(controls, spacing=gap, data="page-summary" if header else None)


def empty(title, message, action=None):
    controls = [
        ft.Icon(ft.Icons.INBOX_OUTLINED, size=26, color=BLUE),
        text(title, 18, bold=True),
        text(message, 14, MUTED),
    ]
    if action:
        controls.append(action)
    return panel(controls)


def control_width(control):
    """Rough pixel width of a table cell, found by walking its text."""
    if isinstance(control, ft.Text):
        size = control.size or 14
        bold = control.weight in (ft.FontWeight.BOLD, ft.FontWeight.W_600, ft.FontWeight.W_700)
        return len(str(control.value or "")) * size * (0.6 if bold else 0.55)
    if isinstance(control, ft.Row):
        kids = control.controls
        return sum(control_width(k) for k in kids) + (control.spacing or 0) * max(0, len(kids) - 1)
    if isinstance(control, ft.Column):
        return max((control_width(k) for k in control.controls), default=0)
    inner = getattr(control, "content", None)
    if inner is not None:
        return control_width(inner)
    return getattr(control, "width", None) or 40  # icon buttons, sparklines, images


def grid(headers, rows, min_width=720, width=1060, numeric=()):
    """Data table. It is as wide as its content needs, or the card if that is wider, and it
    scrolls left and right (with a visible bar) when the card is narrower than the content."""
    columns = [
        ft.DataColumn(text(name, 12, MUTED, True), numeric=i in numeric)
        for i, name in enumerate(headers)
    ]
    cells = [[v if isinstance(v, ft.Control) else text(v, 14) for v in row] for row in rows]
    data = [ft.DataRow(cells=[ft.DataCell(c) for c in row]) for row in cells]
    need = 20 + 12 * max(0, len(headers) - 1)
    for i, name in enumerate(headers):
        need += max(
            [control_width(text(name, 12, MUTED, True))] + [control_width(row[i]) for row in cells if i < len(row)]
        ) + 6
    table = ft.DataTable(
        columns=columns,
        rows=data,
        width=max(need, width),
        column_spacing=12,
        horizontal_margin=10,
        heading_row_height=33,
        data_row_min_height=46,
        data_row_max_height=54,
        heading_row_color=TINT,
        horizontal_lines=ft.BorderSide(0.6, RULE),
        divider_thickness=0.6,
        show_checkbox_column=False,
    )
    return ft.Row([table], scroll=ft.ScrollMode.ALWAYS, spacing=0)


def pager(start, total, size, navigate):
    last = max(0, (total - 1) // size)
    prev = ft.IconButton(
        icon=ft.Icon(ft.Icons.CHEVRON_LEFT, semantics_label="Previous page"),
        tooltip="Previous page",
        on_click=lambda e: navigate(start - 1),
        disabled=start <= 0,
    )
    nxt = ft.IconButton(
        icon=ft.Icon(ft.Icons.CHEVRON_RIGHT, semantics_label="Next page"),
        tooltip="Next page",
        on_click=lambda e: navigate(start + 1),
        disabled=start >= last,
    )
    return ft.Row(
        [
            text(
                f"{start * size + 1}–{min(total, (start + 1) * size)} of {total}",
                13,
                MUTED,
            ),
            prev,
            nxt,
        ],
        spacing=4,
    )


def when(q):
    return q.asof.strftime("%d %b %Y, %H:%M UTC")


def tag(label):
    return ft.Container(
        text(label, 12, NAVY, True),
        bgcolor=TINT,
        padding=ft.Padding.symmetric(vertical=4, horizontal=9),
        border_radius=6,
    )


POSITIVE = FROST.positive


def spark(values, width=84, height=26):
    """Tiny trend line drawn with the canvas. Green when the last value is at or above the first."""
    if len(values) < 2:
        return ft.Container(width=width, height=height)
    low, high = min(values), max(values)
    span = (high - low) or 1
    step = (width - 2) / (len(values) - 1)
    points = [(1 + i * step, height - 2 - (v - low) / span * (height - 4)) for i, v in enumerate(values)]
    path = [cv.Path.MoveTo(*points[0]), *(cv.Path.LineTo(x, y) for x, y in points[1:])]
    colour = POSITIVE if values[-1] >= values[0] else RED
    line = cv.Path(
        path, ft.Paint(color=colour, stroke_width=1.6, style=ft.PaintingStyle.STROKE)
    )
    return cv.Canvas([line], width=width, height=height)


def range_bar(position, width=110, colour=NAVY):
    """Where a price sits between its 52-week low and high (0 to 1)."""
    position = min(1.0, max(0.0, position))
    return ft.Container(
        ft.Stack(
            [
                ft.Container(height=5, width=width, bgcolor=RULE, border_radius=3, top=4),
                ft.Container(height=5, width=max(3, position * width), bgcolor=colour, border_radius=3, top=4),
                ft.Container(
                    width=11, height=11, bgcolor=WHITE, border_radius=6,
                    border=ft.Border.all(2, colour), left=position * (width - 11), top=1,
                ),
            ]
        ),
        width=width,
        height=13,
    )


def bar_row(label, value, maximum, caption, colour=NAVY, label_width=130):
    """Horizontal bar used for exposure lists. value and maximum share one unit."""
    share = 0 if not maximum else max(0.0, min(1.0, float(value) / float(maximum)))
    return ft.Row(
        [
            ft.Container(text(label, 13, INK, True, no_wrap=True), width=label_width),
            ft.Row(
                [
                    ft.Container(bgcolor=colour, border_radius=4, height=10, expand=max(1, int(share * 1000))),
                    ft.Container(expand=max(0, 1000 - int(share * 1000)) or None),
                ],
                spacing=0, expand=True,
            ),
            ft.Container(text(caption, 13, INK, True), width=62, alignment=ft.Alignment.CENTER_RIGHT),
        ],
        spacing=10,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


def sticky_grid(headers, rows, weights, numeric=(), width=1060, min_width=900, height=560):
    """Table whose header row stays visible while the body scrolls.

    The header is a separate fixed row above a vertically scrolling body, and both use the same
    column weights so they line up. Rows are lists of strings or controls.
    """
    def cells(items, header):
        return ft.Row(
            [
                ft.Container(
                    item if isinstance(item, ft.Control) else text(item, 12 if header else 14, MUTED if header else INK, header),
                    expand=weight,
                    alignment=ft.Alignment.CENTER_RIGHT if i in numeric else ft.Alignment.CENTER_LEFT,
                )
                for i, (item, weight) in enumerate(zip(items, weights))
            ],
            spacing=8,
        )

    total = max(min_width, width)
    head = ft.Container(
        cells(headers, True), height=33, bgcolor=TINT, padding=ft.Padding.symmetric(horizontal=10)
    )
    body = ft.Column(
        [
            ft.Container(
                cells(row, False), height=50, padding=ft.Padding.symmetric(horizontal=10),
                border=ft.Border(bottom=ft.BorderSide(0.6, RULE)),
            )
            for row in rows
        ],
        spacing=0,
        scroll=ft.ScrollMode.AUTO,
        height=min(height, 50 * max(1, len(rows))),
    )
    return ft.Row([ft.Container(ft.Column([head, body], spacing=0), width=total)],
                  scroll=ft.ScrollMode.AUTO, spacing=0)
