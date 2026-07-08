"""Inline SVG charts for the web UI.

Server-rendered SVG only — no JavaScript, no external assets, so it works
under the UI's strict CSP and in both light and dark themes (axis text and
labels use `currentColor`; data marks use an accessible fixed palette).
Every chart carries a <title> for screen readers.
"""
from __future__ import annotations

import html
import math

# Accessible, theme-neutral palette (semantic).
OK = "#1b7a2f"       # success / unchanged
CHANGE = "#0b57d0"   # change / info
FAIL = "#b3261e"     # failure / critical
WARN = "#b26a00"     # warning / stale
MUTED = "#98a2ad"    # never / n-a

# Categorical palette for arbitrary series.
CATEGORICAL = ["#0b57d0", "#1b7a2f", "#b26a00", "#7b3fb8", "#0b8f8f",
               "#b3261e", "#5b6570"]


def _t(x: float) -> str:
    return f"{x:.1f}".rstrip("0").rstrip(".")


def donut(value: int, total: int, label: str = "", color: str = OK,
          size: int = 140) -> str:
    """A ring gauge: `value` of `total`, with a centred count."""
    r = 42
    circumference = 2 * math.pi * r
    frac = (value / total) if total else 0.0
    dash = circumference * frac
    pct = f"{frac * 100:.0f}%"
    return (
        f"<svg viewBox='0 0 120 120' width='{size}' height='{size}' "
        f"role='img' aria-label='{html.escape(label)}: {value} of {total}'>"
        f"<title>{html.escape(label)}: {value}/{total} ({pct})</title>"
        f"<circle cx='60' cy='60' r='{r}' fill='none' stroke='#dde3e8' "
        "stroke-width='13'/>"
        f"<circle cx='60' cy='60' r='{r}' fill='none' stroke='{color}' "
        f"stroke-width='13' stroke-linecap='round' "
        f"stroke-dasharray='{_t(dash)} {_t(circumference - dash)}' "
        "transform='rotate(-90 60 60)'/>"
        f"<text x='60' y='58' text-anchor='middle' font-size='24' "
        "font-weight='bold' fill='currentColor'>"
        f"{value}</text>"
        f"<text x='60' y='77' text-anchor='middle' font-size='11' "
        f"fill='currentColor'>{html.escape(label)}</text></svg>"
    )


def vbars(series: list[tuple[str, float]], colors: list[str] | None = None,
          width: int = 480, height: int = 150, unit: str = "") -> str:
    """Vertical bar chart from (label, value) pairs. Bars auto-scale; every
    Nth label is shown to avoid crowding."""
    if not series:
        return "<p class='muted'>no data</p>"
    n = len(series)
    peak = max((v for _l, v in series), default=0) or 1
    pad_l, pad_b, pad_t = 30, 22, 10
    plot_w = width - pad_l - 8
    plot_h = height - pad_b - pad_t
    bar_w = plot_w / n
    label_step = max(1, n // 12)
    bars = ""
    for i, (lbl, val) in enumerate(series):
        color = (colors[i] if colors and i < len(colors)
                 else CATEGORICAL[i % len(CATEGORICAL)])
        bh = plot_h * (val / peak)
        x = pad_l + i * bar_w
        y = pad_t + (plot_h - bh)
        bars += (
            f"<rect x='{_t(x + bar_w * 0.15)}' y='{_t(y)}' "
            f"width='{_t(bar_w * 0.7)}' height='{_t(bh)}' fill='{color}'>"
            f"<title>{html.escape(lbl)}: {_t(val)}{html.escape(unit)}</title>"
            "</rect>"
        )
        if i % label_step == 0:
            bars += (
                f"<text x='{_t(x + bar_w / 2)}' y='{height - 8}' "
                "text-anchor='middle' font-size='9' fill='currentColor'>"
                f"{html.escape(str(lbl))}</text>"
            )
    axis = (
        f"<line x1='{pad_l}' y1='{pad_t}' x2='{pad_l}' "
        f"y2='{pad_t + plot_h}' stroke='currentColor' stroke-opacity='0.3'/>"
        f"<line x1='{pad_l}' y1='{pad_t + plot_h}' x2='{width - 8}' "
        f"y2='{pad_t + plot_h}' stroke='currentColor' stroke-opacity='0.3'/>"
        f"<text x='4' y='{pad_t + 8}' font-size='9' fill='currentColor'>"
        f"{_t(peak)}</text>"
    )
    return (
        f"<svg viewBox='0 0 {width} {height}' width='100%' "
        f"style='max-width:{width}px' role='img'>{axis}{bars}</svg>"
    )


def stacked_hbar(segments: list[tuple[str, float, str]],
                 width: int = 480, height: int = 34) -> str:
    """A single horizontal stacked bar; segments = (label, value, color)."""
    total = sum(v for _l, v, _c in segments) or 1
    x = 0.0
    rects = ""
    for lbl, val, color in segments:
        w = width * (val / total)
        if w <= 0:
            continue
        rects += (
            f"<rect x='{_t(x)}' y='0' width='{_t(w)}' height='{height}' "
            f"fill='{color}'><title>{html.escape(lbl)}: {_t(val)}</title>"
            "</rect>"
        )
        if w > 30:
            rects += (
                f"<text x='{_t(x + w / 2)}' y='{height / 2 + 4}' "
                "text-anchor='middle' font-size='11' fill='#fff'>"
                f"{_t(val)}</text>"
            )
        x += w
    return (
        f"<svg viewBox='0 0 {width} {height}' width='100%' "
        f"style='max-width:{width}px' role='img'>{rects}</svg>"
    )


def legend(items: list[tuple[str, str]]) -> str:
    """items = (label, color)."""
    spans = "".join(
        f"<span style='display:inline-flex;align-items:center;margin-right:1rem;"
        f"font-size:.82rem'><span style='width:11px;height:11px;border-radius:"
        f"2px;background:{color};display:inline-block;margin-right:.35rem'>"
        f"</span>{html.escape(label)}</span>"
        for label, color in items
    )
    return f"<div style='margin:.4rem 0'>{spans}</div>"
