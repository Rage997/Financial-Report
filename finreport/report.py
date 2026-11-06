"""Aggregation: structured rows (JSON-ready) + text-table rendering.

Every aggregate is keyed by currency as well as period/counterparty: amounts
in different currencies are never summed, ranked, or compared against each
other. Rows carry their currency so consumers can filter or format them.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from .currency import fraction_digits, normalize_currency
from .models import Category, Direction, Transaction

MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _fmt(v: Decimal, currency: str = "CHF") -> str:
    return f"{v:,.{fraction_digits(currency)}f}"


def _bucketize(txns: list[Transaction]) -> dict[tuple[int, int, str], dict[Category, Decimal]]:
    out: dict[tuple[int, int, str], dict[Category, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for t in txns:
        m = out[(t.date.year, t.date.month, normalize_currency(t.currency))]
        if t.category == Category.INCOME:
            m[Category.INCOME] += t.amount if t.direction == Direction.IN else -t.amount
        elif t.category == Category.EXPENSE:
            m[Category.EXPENSE] += t.amount if t.direction == Direction.OUT else -t.amount
        elif t.category in (Category.TRANSFER, Category.INVESTMENT):
            m[t.category] += t.amount
        else:
            m[Category.UNCATEGORIZED] += t.amount
    return out


def _row(period: str, currency: str, b: dict[Category, Decimal]) -> dict:
    return {
        "period": period,
        "currency": currency,
        "income": b[Category.INCOME],
        "expenses": b[Category.EXPENSE],
        "transfers": b[Category.TRANSFER],
        "investments": b[Category.INVESTMENT],
        "uncategorized": b[Category.UNCATEGORIZED],
        "net": b[Category.INCOME] - b[Category.EXPENSE],
    }


def monthly_rows(txns: list[Transaction], year: int | None = None) -> list[dict]:
    buckets = _bucketize(txns)
    keys = sorted(k for k in buckets if year is None or k[0] == year)
    return [_row(f"{y}-{mo:02d}", cur, buckets[(y, mo, cur)]) for y, mo, cur in keys]


def yearly_rows(txns: list[Transaction], year: int | None = None) -> list[dict]:
    buckets = _bucketize(txns)
    yearly: dict[tuple[int, str], dict[Category, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for (y, _mo, cur), b in buckets.items():
        if year is not None and y != year:
            continue
        for cat, v in b.items():
            yearly[(y, cur)][cat] += v
    return [_row(str(y), cur, yearly[(y, cur)]) for y, cur in sorted(yearly)]


def counterparty_rows(txns: list[Transaction], min_count: int = 1) -> list[dict]:
    stats: dict[tuple[str, str, str], list] = defaultdict(lambda: [0, Decimal("0")])
    for t in txns:
        key = (t.label or t.counterparty, t.category.value, normalize_currency(t.currency))
        stats[key][0] += 1
        stats[key][1] += t.amount
    rows = []
    # Group by currency, then rank by total within each currency only.
    for (name, cat, cur), (count, total) in sorted(stats.items(), key=lambda kv: (kv[0][2], -kv[1][1])):
        if count < min_count:
            continue
        rows.append({"name": name, "category": cat, "currency": cur,
                     "count": count, "total": total, "avg": total / count})
    return rows


def uncategorized_rows(txns: list[Transaction]) -> list[dict]:
    return [
        {"date": t.date.isoformat(), "counterparty": t.counterparty,
         "amount": t.amount, "currency": t.currency, "direction": t.direction.value}
        for t in txns if t.category == Category.UNCATEGORIZED
    ]


def render_table(headers: list[str], rows: list[list[str]]) -> str:
    rows = [[str(c) for c in r] for r in rows]
    widths = [len(h) for h in headers]
    for r in rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], len(c))

    def line(cells: list[str]) -> str:
        return "  ".join(c.ljust(widths[i]) for i, c in enumerate(cells)).rstrip()

    out = [line(headers), line("─" * w for w in widths)]
    out.extend(line(r) for r in rows)
    if not rows:
        out.append("(none)")
    return "\n".join(out)


_PERIOD_HEADERS = ["Period", "Currency", "Income", "Expenses", "Transfers", "Investments", "Uncat.", "Net (I−E)"]


def _period_table(rows: list[dict]) -> str:
    data = [[r["period"], r["currency"], _fmt(r["income"], r["currency"]),
             _fmt(r["expenses"], r["currency"]), _fmt(r["transfers"], r["currency"]),
             _fmt(r["investments"], r["currency"]), _fmt(r["uncategorized"], r["currency"]),
             _fmt(r["net"], r["currency"])] for r in rows]
    return render_table(_PERIOD_HEADERS, data)


def monthly_report(txns: list[Transaction], year: int | None = None) -> str:
    return _period_table(monthly_rows(txns, year))


def yearly_report(txns: list[Transaction], year: int | None = None) -> str:
    return _period_table(yearly_rows(txns, year))


def counterparty_report(txns: list[Transaction], min_count: int = 1) -> str:
    data = [[r["name"], r["category"], r["currency"], str(r["count"]),
             _fmt(r["total"], r["currency"]), _fmt(r["avg"], r["currency"])]
            for r in counterparty_rows(txns, min_count)]
    return render_table(["Counterparty", "Category", "Currency", "Count", "Total", "Avg"], data)


def uncategorized_report(txns: list[Transaction]) -> str:
    data = [[r["date"], r["counterparty"], _fmt(r["amount"], r["currency"]), r["currency"], r["direction"]]
            for r in uncategorized_rows(txns)]
    return render_table(["Date", "Counterparty", "Amount", "Currency", "Dir"], data)
