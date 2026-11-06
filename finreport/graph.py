"""Recurring-counterparty graph as a self-contained HTML file (inline SVG, no network).

Layout: the bank account is the hub in the center; counterparties sit on a
circle, ordered by total flow. Node radius ~ frequency, edge width ~ total
flow, color = category. Counterparties are normalized by rule label, so all
name variants for the same counterparty collapse into a single node.

The graph depicts a single currency: mixed-currency input is rejected rather
than combined, and every node carries its currency for labelling.
"""
from __future__ import annotations

import html
import math
from collections import defaultdict
from decimal import Decimal

from .currency import fraction_digits, normalize_currency
from .models import Category, Transaction

COLORS = {
    Category.INCOME: "#2e7d32",
    Category.EXPENSE: "#c62828",
    Category.TRANSFER: "#1565c0",
    Category.INVESTMENT: "#6a1b9a",
    Category.UNCATEGORIZED: "#757575",
}


def build_graph(txns: list[Transaction], min_count: int = 1) -> dict[str, dict]:
    currencies = {normalize_currency(t.currency) for t in txns}
    if len(currencies) > 1:
        raise ValueError(
            f"mixed currencies {sorted(currencies)} cannot be combined in one graph; "
            "select a single currency")
    currency = currencies.pop() if currencies else "CHF"
    stats: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "total": Decimal("0"), "category": Category.UNCATEGORIZED,
                 "currency": currency})
    for t in txns:
        key = t.label or t.counterparty
        s = stats[key]
        s["count"] += 1
        s["total"] += t.amount
        s["category"] = t.category
    return {k: v for k, v in stats.items() if v["count"] >= min_count}


def _node_radius(count: int) -> float:
    return min(14 + 5 * math.sqrt(count), 42)


def _edge_width(total: Decimal) -> float:
    return min(1 + math.sqrt(float(total)) / 6, 16)


def render_svg(nodes: dict[str, dict], hub_name: str = "BPS Account") -> str:
    W, H = 980, 760
    cx, cy = W / 2, H / 2
    R = 265
    items = sorted(nodes.items(), key=lambda kv: -kv[1]["total"])
    n = len(items)

    parts: list[str] = []
    parts.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
                 f'font-family="-apple-system, Helvetica, Arial, sans-serif">')

    # edges first (under nodes)
    for i, (name, s) in enumerate(items):
        a = -math.pi / 2 + 2 * math.pi * i / n
        x, y = cx + R * math.cos(a), cy + R * math.sin(a)
        parts.append(
            f'<line x1="{cx:.1f}" y1="{cy:.1f}" x2="{x:.1f}" y2="{y:.1f}" '
            f'stroke="{COLORS[s["category"]]}" stroke-opacity="0.35" '
            f'stroke-width="{_edge_width(s["total"]):.1f}"/>'
        )

    # hub
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="46" fill="#37474f"/>')
    parts.append(f'<text x="{cx}" y="{cy + 4}" text-anchor="middle" fill="#fff" font-size="13" '
                 f'font-weight="bold">{html.escape(hub_name)}</text>')

    # nodes
    for i, (name, s) in enumerate(items):
        a = -math.pi / 2 + 2 * math.pi * i / n
        x, y = cx + R * math.cos(a), cy + R * math.sin(a)
        r = _node_radius(s["count"])
        color = COLORS[s["category"]]
        anchor = "middle"
        lx, ly = x, y + r + 14
        if math.cos(a) > 0.35:
            anchor, lx, ly = "start", x + r + 6, y - 4
        elif math.cos(a) < -0.35:
            anchor, lx, ly = "end", x - r - 6, y - 4
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{color}" fill-opacity="0.85"/>')
        parts.append(f'<text x="{x:.1f}" y="{y + 4:.1f}" text-anchor="middle" fill="#fff" '
                     f'font-size="11" font-weight="bold">{s["count"]}</text>')
        label = name if len(name) <= 26 else name[:25] + "…"
        parts.append(f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" fill="#222" '
                     f'font-size="12">{html.escape(label)}</text>')
        cur = s.get("currency", "CHF")
        parts.append(f'<text x="{lx:.1f}" y="{ly + 13:.1f}" text-anchor="{anchor}" fill="#666" '
                     f'font-size="11">{cur} {s["total"]:,.{fraction_digits(cur)}f}</text>')

    # legend
    ly = H - 24
    lx = 20
    for cat in (Category.INCOME, Category.EXPENSE, Category.TRANSFER, Category.INVESTMENT, Category.UNCATEGORIZED):
        parts.append(f'<circle cx="{lx + 6}" cy="{ly - 4}" r="6" fill="{COLORS[cat]}"/>')
        parts.append(f'<text x="{lx + 18}" y="{ly}" fill="#222" font-size="12">{cat.value}</text>')
        lx += 18 + 13 * len(cat.value) + 24

    parts.append("</svg>")
    return "\n".join(parts)


def render_html(nodes: dict[str, dict], hub_name: str = "BPS Account") -> str:
    svg = render_svg(nodes, hub_name)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Counterparty graph</title>
<style>body{{margin:0;background:#fafafa;display:flex;justify-content:center}}
svg{{max-width:100%;height:auto}}</style>
</head><body>
{svg}
</body></html>
"""
