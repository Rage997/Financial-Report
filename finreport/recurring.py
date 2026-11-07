"""Deterministic recurring-payment candidates from one statement's history.

Never merges distinct counterparty names, accounts, directions or currencies.
Due dates are cadence estimates, not confirmed obligations or cash-flow forecasts.
"""
from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from statistics import median

from .currency import fraction_digits, normalize_currency
from .models import Transaction
from .report import render_table


# Inclusive day-gap windows tolerate ordinary posting-date shifts.
_CADENCES = (("weekly", 6, 8), ("biweekly", 12, 16),
             ("monthly", 26, 35), ("yearly", 350, 380))


def _cadence(gaps: list[int]) -> tuple[str, Decimal]:
    middle = Decimal(str(median(gaps)))
    # Same-day payments have no reliable ordering/cadence; preserve them in
    # counts and amounts, but do not manufacture a regular schedule from them.
    if all(gap > 0 for gap in gaps):
        for name, low, high in _CADENCES:
            if low <= middle <= high and sum(low <= gap <= high for gap in gaps) * 2 > len(gaps):
                return name, middle
    return "irregular", middle


def _next_due(payments: list[Transaction], cadence: str) -> date | None:
    last = payments[-1].date
    if cadence == "irregular":
        return None
    if cadence in ("weekly", "biweekly"):
        days = 7 if cadence == "weekly" else 14
        return last + timedelta(days=days) if last.toordinal() + days <= date.max.toordinal() else None
    year, month = last.year, last.month
    if cadence == "monthly":
        month += 1
        if month == 13:
            year, month = year + 1, 1
        # Retain a regular day after a short month, or retain month-end when
        # every observed payment is on the last day of its month.
        month_end = all(t.date.day == monthrange(t.date.year, t.date.month)[1] for t in payments)
        day = 31 if month_end else int(Decimal(str(median(t.date.day for t in payments))).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP))
    else:
        year += 1
        day = last.day
    if year > date.max.year:
        return None
    return date(year, month, min(day, monthrange(year, month)[1]))


def recurring_rows(txns: list[Transaction]) -> list[dict]:
    """Return groups with >=3 transactions, including explicitly irregular groups.

    Amounts remain Decimal, rounded half-up to currency precision for display.
    Trend = latest amount minus the median of earlier amounts (and its percent).
    An ambiguous latest date has no trend; a zero baseline has no percentage.
    Overdue days are relative to the last activity in that account/currency,
    never the wall clock. This cannot prove that a payment was missed.
    """
    groups: dict[tuple[str, str, str, str], list[Transaction]] = defaultdict(list)
    observed: dict[tuple[str, str], date] = {}
    for txn in txns:
        currency = normalize_currency(txn.currency)
        scope = (currency, txn.account)
        observed[scope] = max(observed.get(scope, txn.date), txn.date)
        if txn.counterparty.strip():
            groups[(currency, txn.account, txn.counterparty, txn.direction.value)].append(txn)

    rows = []
    for (currency, account, counterparty, direction), payments in sorted(groups.items()):
        if len(payments) < 3:
            continue
        payments.sort(key=lambda t: (t.date, t.txn_id))
        gaps = [(b.date - a.date).days for a, b in zip(payments, payments[1:])]
        cadence, median_gap = _cadence(gaps)
        unit = Decimal(1).scaleb(-fraction_digits(currency))
        average = (sum((t.amount for t in payments), Decimal(0)) / len(payments)).quantize(
            unit, rounding=ROUND_HALF_UP)
        last = payments[-1]
        baseline = median(t.amount for t in payments[:-1])
        unique_last = payments[-2].date != last.date
        delta = last.amount - baseline if unique_last else None
        percent = (delta * 100 / baseline).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) \
            if delta is not None and baseline else None
        due = _next_due(payments, cadence)
        as_of = observed[(currency, account)]
        rows.append({
            "counterparty": counterparty, "label": last.label or counterparty,
            "account": account, "currency": currency, "direction": direction,
            "count": len(payments), "cadence": cadence, "median_gap_days": median_gap,
            "average_amount": average, "last_amount": last.amount if unique_last else None,
            "trend": "unknown" if delta is None else "up" if delta > 0 else "down" if delta < 0 else "flat",
            "trend_amount": delta.quantize(unit, rounding=ROUND_HALF_UP) if delta is not None else None,
            "trend_percent": percent, "last_paid": last.date.isoformat(),
            "next_due": due.isoformat() if due else None, "observed_through": as_of.isoformat(),
            "overdue_days": max(0, (as_of - due).days) if due else None,
        })
    return rows


def recurring_report(txns: list[Transaction]) -> str:
    data = []
    for row in recurring_rows(txns):
        digits = fraction_digits(row["currency"])
        percent = row["trend_percent"]
        trend = (f"{percent:+.2f}%" if percent is not None else
                 f"{row['trend_amount']:+.{digits}f} (zero baseline)" if row["trend_amount"] is not None else "unknown")
        data.append([
            row["counterparty"], row["account"] or "—", row["direction"], row["currency"],
            row["count"], row["cadence"], f"{row['average_amount']:.{digits}f}", trend,
            row["last_paid"], row["next_due"] or "—", row["observed_through"],
            row["overdue_days"] if row["overdue_days"] is not None else "—",
        ])
    return render_table([
        "Counterparty", "Account", "Dir", "Currency", "Count", "Cadence", "Average",
        "Latest vs prior median", "Last paid", "Est. next due", "Observed through", "Days past due",
    ], data)
