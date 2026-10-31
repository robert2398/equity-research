"""Dependency-free SVG line charts for the daily report."""
from __future__ import annotations

from pathlib import Path

PALETTE = ["#1f77b4", "#2ca02c", "#d62728", "#9467bd", "#ff7f0e", "#8c564b"]


def line_chart(path: Path, title: str, ylabel: str, series: dict[str, tuple[list, list[float]]],
               y_range: tuple[float, float] | None = None, y_ticks: list[float] | None = None,
               hlines: list[tuple[float, str, str]] | None = None, width=980, height=360) -> None:
    """series: name -> (dates, values). Dates label the x axis sparsely."""
    import math

    W, H = width, height
    ml, mr, mt, mb = 64, 16, 34, 40
    pw, ph = W - ml - mr, H - mt - mb

    all_vals = [v for _, vals in series.values() for v in vals if v == v]
    if not all_vals:
        path.write_text(f"<svg xmlns='http://www.w3.org/2000/svg' width='{W}' height='{H}'>"
                        f"<text x='20' y='40'>no data</text></svg>", encoding="utf-8")
        return
    lo, hi = y_range if y_range else (min(all_vals), max(all_vals))
    if lo == hi:
        lo, hi = lo - 1, hi + 1
    pad = (hi - lo) * 0.05
    lo, hi = lo - pad, hi + pad

    def X(i, n):
        return ml + (pw * i / max(n - 1, 1))

    def Y(v):
        return mt + ph - ph * (v - lo) / (hi - lo)

    parts = [f"<svg xmlns='http://www.w3.org/2000/svg' width='{W}' height='{H}' "
             f"viewBox='0 0 {W} {H}' font-family='Segoe UI, Arial, sans-serif'>",
             f"<rect width='{W}' height='{H}' fill='white'/>",
             f"<text x='{ml}' y='20' font-size='15' font-weight='600' fill='#222'>{title}</text>"]

    ticks = y_ticks if y_ticks else [lo + (hi - lo) * k / 5 for k in range(6)]
    for tv in ticks:
        y = Y(tv)
        parts.append(f"<line x1='{ml}' y1='{y:.1f}' x2='{W - mr}' y2='{y:.1f}' "
                     f"stroke='#e8e8e8' stroke-width='1'/>")
        parts.append(f"<text x='{ml - 8}' y='{y + 4:.1f}' font-size='11' fill='#666' "
                     f"text-anchor='end'>{tv:.0f}</text>")

    for val, color, label in (hlines or []):
        y = Y(val)
        parts.append(f"<line x1='{ml}' y1='{y:.1f}' x2='{W - mr}' y2='{y:.1f}' "
                     f"stroke='{color}' stroke-width='1' stroke-dasharray='5,4'/>")
        parts.append(f"<text x='{W - mr - 4}' y='{y - 4:.1f}' font-size='10' fill='{color}' "
                     f"text-anchor='end'>{label}</text>")

    legend_x = ml
    for k, (name, (dates, vals)) in enumerate(series.items()):
        n = len(vals)
        pts = " ".join(f"{X(i, n):.1f},{Y(v):.1f}" for i, v in enumerate(vals) if v == v)
        color = PALETTE[k % len(PALETTE)]
        parts.append(f"<polyline points='{pts}' fill='none' stroke='{color}' stroke-width='2'/>")
        parts.append(f"<rect x='{legend_x}' y='{mt - 16}' width='12' height='4' fill='{color}'/>")
        parts.append(f"<text x='{legend_x + 16}' y='{mt - 10}' font-size='12' fill='#333'>{name}</text>")
        legend_x += 24 + 7.2 * len(name)
        if dates:
            for frac, anchor in ((0, "start"), (0.5, "middle"), (1, "end")):
                i = min(int(frac * (n - 1)), n - 1)
                parts.append(f"<text x='{X(i, n):.1f}' y='{H - 14}' font-size='10' fill='#666' "
                             f"text-anchor='{anchor}'>{str(dates[i])[:10]}</text>")
    parts.append(f"<text x='14' y='{mt + ph / 2}' font-size='11' fill='#444' "
                 f"transform='rotate(-90 14 {mt + ph / 2})'>{ylabel}</text>")
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")
