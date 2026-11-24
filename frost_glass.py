# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""The fixed light field shared by portfolio pages and forms."""

import flet as ft
import flet.canvas as cv


def frost_light(width, height):
    """Draw one static translucent ribbon across the shared header."""
    path = cv.Path
    w, h = width, height
    base = ft.PaintLinearGradient(
        begin=(0, h * 0.56),
        end=(w, h * 0.20),
        colors=["#E4F3FB", "#BADFEF", "#C9DAF6", "#CADBFC", "#D9EEF2"],
        color_stops=[0, 0.24, 0.50, 0.72, 1],
    )
    ribbon = [
        path.MoveTo(-0.07 * w, 0.94 * h),
        path.CubicTo(0.15 * w, 0.22 * h, 0.37 * w, 1.18 * h, 0.66 * w, 0.50 * h),
        path.CubicTo(0.83 * w, 0.10 * h, 1.02 * w, 0.03 * h, 1.09 * w, 0.18 * h),
        path.LineTo(1.09 * w, 0.50 * h),
        path.CubicTo(0.91 * w, 0.34 * h, 0.82 * w, 0.55 * h, 0.64 * w, 0.78 * h),
        path.CubicTo(0.32 * w, 1.18 * h, 0.21 * w, 0.65 * h, -0.07 * w, 1.14 * h),
        path.Close(),
    ]
    finish = ft.PaintLinearGradient(
        begin=(0, 0.82 * h),
        end=(w, 0.18 * h),
        colors=["#9D77C9DF", "#AD87BCD9", "#7BACBFEF", "#9DAEBAED", "#78F7FFFF"],
        color_stops=[0, 0.20, 0.48, 0.73, 1],
    )
    rim = ft.PaintLinearGradient(
        begin=(0, h),
        end=(w, 0),
        colors=["#00FFFFFF", "#9DFFFFFF", "#D9FFFFFF", "#31FFFFFF"],
        color_stops=[0, 0.28, 0.63, 1],
    )
    shapes = [
        cv.Rect(0, 0, w, h, paint=ft.Paint(gradient=base)),
        cv.Path(ribbon, ft.Paint(gradient=finish)),
        cv.Path(
            ribbon[:3],
            ft.Paint(style=ft.PaintingStyle.STROKE, stroke_width=2.3, gradient=rim),
        ),
    ]
    # Fade into the page's own colours at the top and bottom of the ribbon.
    return ft.ShaderMask(
        content=cv.Canvas(shapes, width=w, height=h),
        width=w,
        height=h,
        blend_mode=ft.BlendMode.DST_IN,
        shader=ft.LinearGradient(
            begin=ft.Alignment.TOP_CENTER,
            end=ft.Alignment.BOTTOM_CENTER,
            colors=["#00FFFFFF", "#A6FFFFFF", "#A6FFFFFF", "#00FFFFFF"],
            stops=[0, 0.30, 0.66, 1],
        ),
    )


def backdrop(width, *, compact=False):
    # Only the foreground sets the height. The painted layer cannot intercept taps or focus.
    height = (205 if width >= 900 else 290) if compact else (310 if width >= 900 else 420)
    return ft.Container(
        ft.Semantics(
            content=frost_light(width, height),
            hidden=True,
            exclude_semantics=True,
        ),
        bottom=0,
        left=0,
        width=width,
        height=height,
        ignore_interactions=True,
    )


def frost_header(content, width, *, compact=False):
    return ft.Stack(
        [backdrop(width, compact=compact), content],
        width=width,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
    )
