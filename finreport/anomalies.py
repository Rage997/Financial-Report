"""Explainable amount outliers against strictly earlier counterparty activity."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from itertools import groupby
from statistics import median

from .currency import fraction_digits, normalize_currency
from .models import Transaction
from .report import render_table


def anomaly_flags(txns: list[Transaction]) -> list[dict | None]:
    """Return flags aligned with input occurrences, never keyed by nonunique IDs.

    Require three earlier payments to the exact counterparty/account/direction/
    currency. All same-day occurrences use the same earlier-day baseline. Flag
    amounts strictly above median + max(3*MAD, 15 native units), OR 3*median.
    The native-unit floor is CHF 15 for CHF; it is not an exchange conversion.
    """
    groups = defaultdict(list)
    for index, txn in enumerate(txns):
        if txn.counterparty.strip():
            groups[(txn.counterparty, txn.account, txn.direction, normalize_currency(txn.currency))].append((index, txn))
    flags: list[dict | None] = [None] * len(txns)
    for group in groups.values():
        history: list[Decimal] = []
        for _, occurrences in groupby(sorted(group, key=lambda item: item[1].date), key=lambda item: item[1].date):
            batch = list(occurrences)
            if len(history) >= 3:
                typical = median(history)
                mad = median(abs(amount - typical) for amount in history)
                robust_limit = typical + max(3 * mad, Decimal(15))
                ratio_limit = 3 * typical
                for index, txn in batch:
                    exceeded = []
                    if txn.amount > robust_limit:
                        exceeded.append("median + max(3 × MAD, 15 native units)")
                    if txn.amount > ratio_limit:
                        exceeded.append("3 × median")
                    if exceeded:
                        digits = fraction_digits(txn.currency)
                        flags[index] = {
                            "history_count": len(history), "median": typical, "mad": mad,
                            "robust_limit": robust_limit, "ratio_limit": ratio_limit,
                            "reason": f"{txn.amount:.{digits}f} {txn.currency} exceeds {' and '.join(exceeded)}; "
                                      f"earlier median {typical:.{digits}f}, MAD {mad:.{digits}f} "
                                      f"across {len(history)} earlier payments to this counterparty/account/direction/currency. "
                                      "Review candidate, not proof of an error.",
                        }
            history.extend(txn.amount for _, txn in batch)
    return flags


def anomaly_report(txns: list[Transaction]) -> str:
    return render_table(["Date", "Counterparty", "Account", "Dir", "Currency", "Amount", "Reason"], [
        [txn.date, txn.counterparty, txn.account or "—", txn.direction.value, txn.currency,
         f"{txn.amount:.{fraction_digits(txn.currency)}f}", flag["reason"]]
        for txn, flag in zip(txns, anomaly_flags(txns)) if flag is not None
    ])
