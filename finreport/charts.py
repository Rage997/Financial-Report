"""PNG charts (matplotlib): networth curve, monthly income vs expenses, category donut.

A chart always depicts a single currency. Callers are expected to filter the
input to one currency; mixed-currency input is rejected rather than combined,
and labels/captions carry the denomination instead of a hardcoded one.
"""
from __future__ import annotations

import io
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from .currency import fraction_digits, normalize_currency
from .models import Category, Transaction
from .networth import compute_series
from .report import _bucketize

CHART_NAMES = ("networth", "monthly", "categories")


def _require_matplotlib():
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        return plt
    except ImportError as e:
        raise SystemExit("matplotlib is required for charts: python3 -m pip install --user matplotlib") from e


def _chart_currency(txns: list[Transaction], opening_currency: str | None) -> str:
    """Denomination a chart may depict; mixed input is a hard error."""
    currencies = {normalize_currency(t.currency) for t in txns}
    if len(currencies) > 1:
        raise ValueError(
            f"mixed currencies {sorted(currencies)} cannot be combined in one chart; "
            "select a single currency")
    if opening_currency is not None:
        code = normalize_currency(opening_currency)
        if currencies and code not in currencies:
            raise ValueError(
                f"opening currency {code} does not match transaction currency {currencies.pop()}")
        return code
    return currencies.pop() if currencies else "CHF"


def _fig_networth(plt, txns, opening_balance, opening_networth, cash_flow_only, currency):
    points = compute_series(txns, opening_balance, opening_networth, opening_currency=currency)
    fig, ax = plt.subplots(figsize=(11, 5.5), dpi=140)
    dates = [p.date for p in points]
    ax.plot(dates, [float(p.networth) for p in points], color="#1565c0", lw=2,
            label="Cash flow excluding transfers/investments" if cash_flow_only else "Networth")
    ax.plot(dates, [float(p.balance) for p in points], color="#ef6c00", lw=1.2, ls="--",
            label="Cumulative cash flow" if cash_flow_only else "Account balance")
    ax.set_title("Imported statement: zero-based cash flow" if cash_flow_only
                 else "Networth (bank flows) & account balance")
    ax.set_ylabel(currency)
    ax.legend()
    ax.grid(alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    return fig


def _fig_monthly(plt, txns, currency):
    buckets = _bucketize(txns)
    months = sorted(buckets)
    labels = [f"{y}-{m:02d}" for y, m, _cur in months]
    income = [float(buckets[k][Category.INCOME]) for k in months]
    expenses = [float(buckets[k][Category.EXPENSE]) for k in months]
    fig, ax = plt.subplots(figsize=(11, 5), dpi=140)
    x = range(len(labels))
    ax.bar([i - 0.2 for i in x], income, width=0.4, color="#2e7d32", label="Income")
    ax.bar([i + 0.2 for i in x], expenses, width=0.4, color="#c62828", label="Expenses")
    ax.set_xticks(list(x), labels, rotation=45, ha="right")
    ax.set_title("Monthly income vs expenses")
    ax.set_ylabel(currency)
    ax.legend()
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    return fig


def _fig_categories(plt, txns, currency):
    digits = fraction_digits(currency)
    totals: dict[Category, Decimal] = defaultdict(Decimal)
    for t in txns:
        if t.direction.value == "out":
            totals[t.category] += t.amount
    cats = [c for c in (Category.EXPENSE, Category.TRANSFER, Category.INVESTMENT,
                        Category.UNCATEGORIZED, Category.INCOME) if totals[c]]
    fig, ax = plt.subplots(figsize=(7, 7), dpi=140)
    ax.pie(
        [float(totals[c]) for c in cats],
        labels=[f"{c.value}\n{currency} {totals[c]:,.{digits}f}" for c in cats],
        colors=["#c62828", "#1565c0", "#6a1b9a", "#757575", "#2e7d32"][: len(cats)],
        startangle=90, counterclock=False,
    )
    ax.set_title("Outflows by category")
    fig.tight_layout()
    return fig


_BUILDERS = {
    "networth": lambda plt, txns, ob, onw, flows, cur: _fig_networth(plt, txns, ob, onw, flows, cur),
    "monthly": lambda plt, txns, ob, onw, flows, cur: _fig_monthly(plt, txns, cur),
    "categories": lambda plt, txns, ob, onw, flows, cur: _fig_categories(plt, txns, cur),
}


def render_chart_png(name: str, txns: list[Transaction],
                     opening_balance: Decimal = Decimal("0"),
                     opening_networth: Decimal = Decimal("0"), *, cash_flow_only: bool = False,
                     opening_currency: str | None = None) -> bytes:
    if name not in _BUILDERS:
        raise ValueError(f"unknown chart {name!r}; expected one of {CHART_NAMES}")
    currency = _chart_currency(txns, opening_currency)
    plt = _require_matplotlib()
    fig = _BUILDERS[name](plt, txns, opening_balance, opening_networth, cash_flow_only, currency)
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def make_charts(
    txns: list[Transaction],
    outdir: str | Path,
    opening_balance: Decimal = Decimal("0"),
    opening_networth: Decimal = Decimal("0"),
    *,
    opening_currency: str | None = None,
) -> list[Path]:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    written = []
    for name in CHART_NAMES:
        p = outdir / f"{name}.png"
        p.write_bytes(render_chart_png(name, txns, opening_balance, opening_networth,
                                       opening_currency=opening_currency))
        written.append(p)
    return written
