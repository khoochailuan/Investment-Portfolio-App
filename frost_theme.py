# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Shared Frost colours, type, surfaces and navigation controls."""

from dataclasses import dataclass

import flet as ft

SANS = "Ledger Sans"
WHITE = "#FFFFFF"
FONTS = {SANS: "fonts/DM-Sans.ttf"}


@dataclass(frozen=True)
class FrostPalette:
    page: str = "#DFEAF5"
    input: str = "#E1EDF6"
    ink: str = "#152F45"
    muted: str = "#344C5E"
    line: str = "#D2E0E8"
    accent: str = "#215C78"
    wash: str = "#E8F3F8"
    positive: str = "#00513F"
    negative: str = "#8B223B"
    radius: int = 18
    display: str = SANS
    chart: tuple[str, ...] = (
        "#2B6E98",
        "#2FA59C",
        "#DF965C",
        "#897AC0",
        "#71A5C8",
        "#C26B89",
        "#648D76",
        "#9D895E",
    )

    def colours(self, count):
        """One colour per slice. Past the base palette the same hues return in lighter and
        darker shades, so a portfolio with many holdings still gets a colour for each."""
        out = []
        for i in range(count):
            base, round_ = self.chart[i % len(self.chart)], i // len(self.chart)
            if round_ == 0:
                out.append(base)
                continue
            mix, amount = ("#FFFFFF", 0.38) if round_ % 2 else ("#000000", 0.28)
            channels = [int(base[k : k + 2], 16) for k in (1, 3, 5)]
            target = [int(mix[k : k + 2], 16) for k in (1, 3, 5)]
            shade = [round(c + (t - c) * amount * ((round_ + 1) // 2)) for c, t in zip(channels, target)]
            out.append("#" + "".join(f"{max(0, min(255, v)):02X}" for v in shade))
        return tuple(out)

    def sign(self, value):
        if value is None or value == 0:
            return self.muted
        return self.positive if value > 0 else self.negative


FROST = FrostPalette()
WEIGHTS = {
    400: ft.FontWeight.W_400,
    500: ft.FontWeight.W_500,
    600: ft.FontWeight.W_600,
    700: ft.FontWeight.W_700,
}


def txt(value, size, color, weight=400, *, font=SANS, tracking=None, **kwargs):
    return ft.Text(
        str(value),
        size=size,
        color=color,
        font_family=font,
        weight=WEIGHTS[weight],
        style=ft.TextStyle(letter_spacing=tracking) if tracking is not None else None,
        **kwargs,
    )


def title(value, size, s=FROST):
    return txt(value, size, s.ink, 700, tracking=-0.75)


def page_gradient():
    return ft.LinearGradient(
        begin=ft.Alignment(-1, -0.84),
        end=ft.Alignment(1, 0.95),
        colors=["#CFE6F0", "#DFECF6", "#D7E3F3"],
        stops=[0, 0.5, 1],
    )


def panel(s, child, *, width=None, padding=0):
    return ft.Container(
        child,
        width=width,
        gradient=ft.LinearGradient(
            begin=ft.Alignment.TOP_LEFT,
            end=ft.Alignment.BOTTOM_RIGHT,
            colors=["#BEE8F4FA", "#A7DEEDF6", "#AACFDFF1"],
            stops=[0, 0.58, 1],
        ),
        padding=padding,
        border_radius=s.radius,
        border=ft.Border.all(1, s.line),
        shadow=ft.BoxShadow(color="#0B264661", blur_radius=13, offset=ft.Offset(0, 3)),
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
    )


def glass(
    content,
    *,
    padding=0,
    width=None,
    height=None,
    col=None,
    on_click=None,
    tooltip=None,
):
    """Blur only behind the face. The foreground owns layout, focus and taps."""
    blur = ft.Container(
        left=0,
        top=0,
        right=0,
        bottom=0,
        blur=ft.Blur(14, 14),
        border_radius=FROST.radius,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        ignore_interactions=True,
    )
    face = ft.Container(
        content,
        width=width,
        height=height,
        padding=padding,
        border_radius=FROST.radius,
        border=ft.Border.all(1.25, "#EBFFFFFF"),
        gradient=ft.LinearGradient(
            begin=ft.Alignment.TOP_LEFT,
            end=ft.Alignment.BOTTOM_RIGHT,
            colors=["#B5FFFFFF", "#8BF9FCFF", "#60ECF6FF"],
            stops=[0, 0.48, 1],
        ),
        on_click=on_click,
        ink=bool(on_click),
        tooltip=tooltip,
    )
    return ft.Container(
        ft.Stack([blur, face], clip_behavior=ft.ClipBehavior.NONE),
        width=width,
        height=height,
        col=col,
        border_radius=FROST.radius,
        shadow=ft.BoxShadow(
            color="#1A365875",
            blur_radius=13,
            offset=ft.Offset(0, 4),
            blur_style=ft.BlurStyle.OUTER,
        ),
    )


def frost_dialog(*, title, **kwargs):
    banner = ft.Container(
        title,
        padding=ft.Padding.symmetric(horizontal=16, vertical=13),
        border_radius=13,
        border=ft.Border.all(1, WHITE),
        gradient=ft.LinearGradient(
            begin=ft.Alignment.TOP_LEFT,
            end=ft.Alignment.BOTTOM_RIGHT,
            colors=["#ECF8FC", "#D8EBF7", "#E5EAFB"],
        ),
    )
    return ft.AlertDialog(
        title=banner, title_padding=ft.Padding.only(left=16, right=16, top=16), **kwargs
    )


def rule(color, size=1):
    return ft.Divider(height=size, thickness=size, color=color)


def action(s, name, on_click, *, icon=None, primary=False, subtle=False, tooltip=None):
    fg = WHITE if primary else s.accent
    children = [ft.Icon(icon, size=16, color=fg)] if icon else []
    children.append(txt(name, 13, fg, 600))
    return ft.TextButton(
        ft.Row(children, spacing=7, tight=True),
        on_click=on_click,
        tooltip=tooltip,
        height=42,
        style=ft.ButtonStyle(
            bgcolor=s.accent if primary else None if subtle else "#D9FFFFFF",
            side=None if primary or subtle else ft.BorderSide(1, s.line),
            padding=ft.Padding.symmetric(horizontal=13),
            shape=ft.RoundedRectangleBorder(radius=10),
        ),
    )


def menu(s, caption, current, choices, change, *, prefix="", icon=None, show_value=True):
    face = prefix + current if show_value else caption
    children = [ft.Icon(icon, size=16, color=s.accent)] if icon else []
    children += [
        txt(face, 13, s.ink, 600),
        ft.Icon(ft.Icons.EXPAND_MORE, size=15, color=s.muted),
    ]
    return ft.PopupMenuButton(
        tooltip=caption,
        bgcolor=WHITE,
        content=ft.Container(
            ft.Row(children, spacing=6, tight=True),
            height=42,
            padding=ft.Padding.symmetric(horizontal=11),
            border_radius=10,
            bgcolor="#DFFFFFFF",
            border=ft.Border.all(1, s.line),
        ),
        items=[
            ft.PopupMenuItem(
                content=txt(v, 14, s.ink, 600 if v == current else 400),
                height=46,
                checked=v == current,
                on_click=lambda e, value=v: change(value),
            )
            for v in choices
        ],
    )
