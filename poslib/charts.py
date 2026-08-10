"""
charts.py - works out the geometry for the charts on the dashboard.

WHY THERE IS NO CHARTING LIBRARY HERE
-------------------------------------
The charts are drawn as plain SVG shapes written straight into the page.
No JavaScript library, nothing downloaded from the internet. That means:

  - the dashboard works with no internet connection, forever
  - the charts print properly, which matters for the call list
  - they can be mirrored for Arabic, which most chart libraries do badly

This file contains no business rules. It only turns a list of numbers into
a list of rectangles and lines. Every number it is given was worked out in
metrics.py.
"""

from __future__ import annotations

import math
from typing import Any, Sequence

# The drawing area. Everything is drawn to this fixed size and the browser
# scales it to fit, so charts stay sharp at any window size.
WIDTH = 1000
HEIGHT = 340

MARGIN_TOP = 24
MARGIN_BOTTOM = 56
MARGIN_LEFT = 78
MARGIN_RIGHT = 78


def _nice_ceiling(value: float) -> float:
    """
    Round a maximum up to a round number, so the axis reads 0 / 25 / 50
    rather than 0 / 23.7 / 47.4.
    """
    if value <= 0:
        return 1.0
    exponent = math.floor(math.log10(value))
    base = 10 ** exponent
    for step in (1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10):
        if value <= base * step:
            return base * step
    return base * 10


def _axis_values(maximum: float, minimum: float = 0.0, count: int = 4) -> list[float]:
    """The values to write up the side of the chart."""
    if maximum <= minimum:
        maximum = minimum + 1
    top = minimum + _nice_ceiling(maximum - minimum)
    return [minimum + (top - minimum) * i / count for i in range(count + 1)]


def _safe(values: Sequence[Any]) -> list[float]:
    """Treat missing numbers as zero for drawing purposes."""
    out = []
    for v in values:
        try:
            f = float(v)
            out.append(0.0 if f != f else f)
        except (TypeError, ValueError):
            out.append(0.0)
    return out


class Plot:
    """The rectangle the data is drawn inside, and how to map values into it."""

    def __init__(self, width: int = WIDTH, height: int = HEIGHT,
                 rtl: bool = False,
                 margin_left: int = MARGIN_LEFT, margin_right: int = MARGIN_RIGHT):
        self.width = width
        self.height = height
        self.rtl = rtl
        # In Arabic the value axis sits on the right, so the margins swap.
        self.left = margin_right if rtl else margin_left
        self.right = margin_left if rtl else margin_right
        self.top = MARGIN_TOP
        self.bottom = MARGIN_BOTTOM

    @property
    def x0(self) -> float:
        return self.left

    @property
    def x1(self) -> float:
        return self.width - self.right

    @property
    def y0(self) -> float:
        return self.top

    @property
    def y1(self) -> float:
        return self.height - self.bottom

    @property
    def plot_width(self) -> float:
        return self.x1 - self.x0

    @property
    def plot_height(self) -> float:
        return self.y1 - self.y0

    def x_for(self, index: int, count: int) -> float:
        """
        Where the middle of item `index` sits.

        For Arabic the order is flipped so the first month is on the right
        and time still reads in the natural direction for the reader.
        """
        if count <= 0:
            return self.x0
        slot = self.plot_width / count
        i = (count - 1 - index) if self.rtl else index
        return self.x0 + slot * (i + 0.5)

    def y_for(self, value: float, low: float, high: float) -> float:
        if high == low:
            return self.y1
        frac = (value - low) / (high - low)
        return self.y1 - frac * self.plot_height


def combo_chart(labels: Sequence[str],
                bars: Sequence[float] | None = None,
                bars2: Sequence[float] | None = None,
                line: Sequence[float] | None = None,
                rtl: bool = False,
                bar_label: str = "", bar2_label: str = "", line_label: str = "",
                line_is_percent: bool = False,
                max_labels: int = 14) -> dict[str, Any]:
    """
    Bars with an optional second set of bars beside them, and an optional
    line on its own scale up the other side.

    This one shape covers most of what the dashboard needs: revenue and
    profit by month with margin on top, tickets with average basket, and
    spend with average unit cost.
    """
    n = len(labels)
    p = Plot(rtl=rtl)

    if n == 0:
        return {"empty": True, "width": WIDTH, "height": HEIGHT}

    b1 = _safe(bars) if bars is not None else []
    b2 = _safe(bars2) if bars2 is not None else []
    ln = _safe(line) if line is not None else []

    bar_max = max([*b1, *b2, 0.0])
    axis = _axis_values(bar_max)
    top = axis[-1]

    slot = p.plot_width / n
    # Two bars share the slot; one bar gets a wider single bar.
    groups = (1 if not b2 else 2)
    bar_w = max(2.0, min(30.0, (slot * 0.62) / groups))

    def build(values: list[float], offset: float) -> list[dict[str, Any]]:
        out = []
        for i, v in enumerate(values):
            cx = p.x_for(i, n)
            y = p.y_for(max(v, 0.0), 0.0, top)
            h = max(0.0, p.y1 - y)
            out.append({
                "x": round(cx + offset - bar_w / 2, 2),
                "y": round(y, 2),
                "w": round(bar_w, 2),
                "h": round(h, 2),
                "value": v,
                "label": labels[i],
            })
        return out

    if b2:
        series1 = build(b1, -bar_w / 2)
        series2 = build(b2, bar_w / 2)
    else:
        series1 = build(b1, 0.0)
        series2 = []

    # The line has its own scale so a percentage is readable next to millions.
    line_points: list[dict[str, Any]] = []
    line_axis: list[dict[str, Any]] = []
    if ln:
        real = [v for v, raw in zip(ln, line) if raw is not None]
        lo = min(real) if real else 0.0
        hi = max(real) if real else 1.0
        lo = min(lo, 0.0) if not line_is_percent else max(0.0, lo - (hi - lo) * 0.2)
        hi = hi + (hi - lo) * 0.15 if hi > lo else hi + 1
        for i, raw in enumerate(line):
            if raw is None:
                continue
            v = float(raw) if float(raw) == float(raw) else None
            if v is None:
                continue
            line_points.append({
                "x": round(p.x_for(i, n), 2),
                "y": round(p.y_for(v, lo, hi), 2),
                "value": v,
                "label": labels[i],
            })
        for v in _axis_values(hi, lo, 4):
            line_axis.append({"y": round(p.y_for(v, lo, hi), 2), "value": v})

    polyline = " ".join(f"{pt['x']},{pt['y']}" for pt in line_points)

    # Only write some of the labels along the bottom, or they overlap.
    every = max(1, math.ceil(n / max_labels))
    x_labels = [{"x": round(p.x_for(i, n), 2), "text": labels[i]}
                for i in range(n) if (n - 1 - i) % every == 0]

    return {
        "empty": False,
        "width": WIDTH, "height": HEIGHT, "rtl": rtl,
        "x0": p.x0, "x1": p.x1, "y0": p.y0, "y1": p.y1,
        "value_axis_x": p.x1 + 8 if rtl else p.x0 - 10,
        "value_anchor": "start" if rtl else "end",
        "line_axis_x": p.x0 - 10 if rtl else p.x1 + 8,
        "line_anchor": "end" if rtl else "start",
        "grid": [{"y": round(p.y_for(v, 0.0, top), 2), "value": v} for v in axis],
        "bars": series1, "bars2": series2,
        "line_points": line_points, "polyline": polyline, "line_axis": line_axis,
        "x_labels": x_labels,
        "bar_label": bar_label, "bar2_label": bar2_label, "line_label": line_label,
        "line_is_percent": line_is_percent,
    }


def hbar_chart(labels: Sequence[str], values: Sequence[float],
               rtl: bool = False, height_per_bar: int = 30) -> dict[str, Any]:
    """
    Bars lying on their side. Used wherever the labels are names - customers,
    suppliers, brands - because names do not fit under upright bars.
    """
    n = len(labels)
    if n == 0:
        return {"empty": True}

    vals = _safe(values)
    top = _nice_ceiling(max(vals + [0.0]))
    height = MARGIN_TOP + n * height_per_bar + 30
    label_w = 240
    x0 = 20 if rtl else label_w
    x1 = WIDTH - label_w if rtl else WIDTH - 40
    plot_w = x1 - x0

    bars = []
    for i, v in enumerate(vals):
        w = (max(v, 0.0) / top) * plot_w if top else 0.0
        y = MARGIN_TOP + i * height_per_bar
        bars.append({
            "x": round(x1 - w, 2) if rtl else round(x0, 2),
            "y": round(y, 2),
            "w": round(w, 2),
            "h": height_per_bar - 10,
            "value": v,
            "label": labels[i],
            "label_x": round(x1 + 12, 2) if rtl else round(x0 - 12, 2),
            "label_anchor": "start" if rtl else "end",
            "value_x": round(x1 - w - 8, 2) if rtl else round(x0 + w + 8, 2),
            "value_anchor": "end" if rtl else "start",
            "text_y": round(y + (height_per_bar - 10) / 2 + 4, 2),
        })

    return {"empty": False, "width": WIDTH, "height": height,
            "bars": bars, "max": top, "rtl": rtl}


def line_chart(x_values: Sequence[float], y_values: Sequence[float],
               rtl: bool = False, x_label: str = "", y_label: str = "",
               reference: float | None = None) -> dict[str, Any]:
    """
    A plain line, used for the Pareto curve where both axes are percentages.
    """
    if not x_values or not y_values:
        return {"empty": True}

    p = Plot(rtl=rtl)
    xs, ys = _safe(x_values), _safe(y_values)
    x_top = max(xs + [1.0])
    y_top = max(ys + [1.0])

    points = []
    for x, y in zip(xs, ys):
        frac = x / x_top if x_top else 0.0
        px = p.x1 - frac * p.plot_width if rtl else p.x0 + frac * p.plot_width
        points.append({"x": round(px, 2), "y": round(p.y_for(y, 0.0, y_top), 2)})

    polyline = " ".join(f"{pt['x']},{pt['y']}" for pt in points)
    area = f"{points[0]['x']},{p.y1} " + polyline + f" {points[-1]['x']},{p.y1}"

    grid = [{"y": round(p.y_for(v, 0.0, y_top), 2), "value": v}
            for v in _axis_values(y_top, 0.0, 4)]
    x_grid = []
    for v in _axis_values(x_top, 0.0, 4):
        frac = v / x_top if x_top else 0.0
        px = p.x1 - frac * p.plot_width if rtl else p.x0 + frac * p.plot_width
        x_grid.append({"x": round(px, 2), "value": v})

    ref = None
    if reference is not None:
        ref = {"y": round(p.y_for(reference, 0.0, y_top), 2), "value": reference}

    return {"empty": False, "width": WIDTH, "height": HEIGHT, "rtl": rtl,
            "x0": p.x0, "x1": p.x1, "y0": p.y0, "y1": p.y1,
            "polyline": polyline, "area": area, "grid": grid, "x_grid": x_grid,
            "value_axis_x": p.x1 + 8 if rtl else p.x0 - 10,
            "value_anchor": "start" if rtl else "end",
            "x_label": x_label, "y_label": y_label, "reference": ref}


def stacked_bar(segments: Sequence[dict[str, Any]], rtl: bool = False,
                height: int = 76) -> dict[str, Any]:
    """
    One wide bar split into coloured parts. Used for the stock split into
    selling, slow and dead - the clearest way to show three shares of one
    total at a glance.
    """
    total = sum(max(float(s.get("value") or 0.0), 0.0) for s in segments)
    if total <= 0:
        return {"empty": True}

    x = 0.0
    parts = []
    for s in segments:
        v = max(float(s.get("value") or 0.0), 0.0)
        w = (v / total) * WIDTH
        parts.append({
            "x": round(WIDTH - x - w, 2) if rtl else round(x, 2),
            "w": round(w, 2),
            "value": v,
            "share": v / total,
            "label": s.get("label", ""),
            "colour": s.get("colour", "var(--c1)"),
            "centre": round((WIDTH - x - w / 2) if rtl else (x + w / 2), 2),
            "wide": w > 110,
        })
        x += w

    return {"empty": False, "width": WIDTH, "height": height,
            "parts": parts, "total": total, "rtl": rtl}


def heatmap(grid: Sequence[dict[str, Any]], columns: Sequence[str],
            rtl: bool = False) -> dict[str, Any]:
    """
    A grid of squares, darker where there is more. Used for which hour of
    which day the money actually comes in.
    """
    if not grid or not columns:
        return {"empty": True}

    n_cols = len(columns)
    n_rows = len(grid)
    label_w = 110
    cell_w = min(58.0, (WIDTH - label_w - 30) / n_cols)
    cell_h = 34.0
    width = label_w + cell_w * n_cols + 30
    height = 34 + n_rows * cell_h + 26

    biggest = max((max(r["values"]) if r["values"] else 0.0) for r in grid) or 1.0

    cells = []
    for ri, row in enumerate(grid):
        for ci, value in enumerate(row["values"]):
            col = (n_cols - 1 - ci) if rtl else ci
            x = (label_w + col * cell_w) if not rtl else (30 + col * cell_w)
            cells.append({
                "x": round(x, 2),
                "y": round(34 + ri * cell_h, 2),
                "w": round(cell_w - 3, 2),
                "h": round(cell_h - 3, 2),
                "value": value,
                # A gentle curve so the busy hours stand out without the
                # quiet ones disappearing entirely.
                "intensity": round((value / biggest) ** 0.6, 3) if value > 0 else 0.0,
                "row": row["name"],
                "col": columns[ci],
            })

    col_labels = []
    for ci, name in enumerate(columns):
        col = (n_cols - 1 - ci) if rtl else ci
        x = (label_w + col * cell_w) if not rtl else (30 + col * cell_w)
        col_labels.append({"x": round(x + (cell_w - 3) / 2, 2), "text": name})

    row_labels = [{
        "y": round(34 + ri * cell_h + cell_h / 2, 2),
        "x": round(label_w - 14, 2) if not rtl else round(30 + n_cols * cell_w + 14, 2),
        "anchor": "end" if not rtl else "start",
        "text": row["name"],
    } for ri, row in enumerate(grid)]

    return {"empty": False, "width": round(width), "height": round(height),
            "cells": cells, "col_labels": col_labels, "row_labels": row_labels,
            "max": biggest, "rtl": rtl}


def diverging_bars(labels: Sequence[str], values: Sequence[float],
                   rtl: bool = False) -> dict[str, Any]:
    """
    Bars going up or down from a middle line. Used for which months run
    above and below the average.
    """
    n = len(labels)
    if n == 0:
        return {"empty": True}

    p = Plot(rtl=rtl)
    vals = _safe(values)
    extent = _nice_ceiling(max(abs(v) for v in vals) if vals else 1.0)
    zero_y = p.y_for(0.0, -extent, extent)

    slot = p.plot_width / n
    bar_w = max(6.0, min(46.0, slot * 0.6))

    bars = []
    for i, v in enumerate(vals):
        cx = p.x_for(i, n)
        y = p.y_for(max(v, 0.0), -extent, extent)
        h = abs(p.y_for(v, -extent, extent) - zero_y)
        bars.append({
            "x": round(cx - bar_w / 2, 2), "y": round(y, 2),
            "w": round(bar_w, 2), "h": round(max(h, 1.0), 2),
            "value": v, "label": labels[i], "positive": v >= 0,
            "text_x": round(cx, 2),
            "text_y": round((y - 8) if v >= 0 else (y + h + 16), 2),
        })

    return {"empty": False, "width": WIDTH, "height": HEIGHT, "rtl": rtl,
            "x0": p.x0, "x1": p.x1, "zero_y": round(zero_y, 2),
            "bars": bars, "extent": extent,
            "grid": [{"y": round(p.y_for(v, -extent, extent), 2), "value": v}
                     for v in (-extent, -extent / 2, 0.0, extent / 2, extent)],
            "value_axis_x": p.x1 + 8 if rtl else p.x0 - 10,
            "value_anchor": "start" if rtl else "end"}
