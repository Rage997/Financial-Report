"""Running balance and networth computation.

- Account balance: each account's opening balance + its flows (in − out).
- Networth: opening networth + income − expenses. Transfers (own accounts)
  and investments (brokers, pillar 3) move money between own assets and are
  neutral. Uncategorized flows are treated as income/expense (conservative).
- With multiple accounts in one currency, account balances start at zero:
  a source-wide opening balance cannot be allocated without reconciliation.
  The source-wide networth estimate still applies its opening once per currency.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from .currency import fraction_digits, normalize_currency
from .models import Category, Direction, Transaction


@dataclass
class NetworthPoint:
    date: date
    event: str
    balance: Decimal
    networth: Decimal
    account: str
    currency: str = "CHF"


def config_currency(path: str | Path) -> str:
    """Currency of the explicit opening values; legacy configurations use CHF."""
    p = Path(path)
    if not p.exists():
        return "CHF"
    return normalize_currency(json.loads(p.read_text(encoding="utf-8")).get("currency", "CHF"))


def load_config(path: str | Path, currency: str = "CHF") -> tuple[Decimal, Decimal]:
    """Return opening values only for their declared currency, never convert them."""
    currency = normalize_currency(currency)
    p = Path(path)
    if not p.exists():
        return Decimal("0"), Decimal("0")
    data = json.loads(p.read_text(encoding="utf-8"))
    if normalize_currency(data.get("currency", "CHF")) != currency:
        return Decimal("0"), Decimal("0")
    values = (Decimal(str(data.get("opening_balance", "0"))),
              Decimal(str(data.get("opening_networth", "0"))))
    if not all(value.is_finite() for value in values):
        raise ValueError("Opening values must be finite.")
    return values


def compute_series(
    txns: list[Transaction],
    opening_balance: Decimal = Decimal("0"),
    opening_networth: Decimal = Decimal("0"),
    *,
    opening_currency: str = "CHF",
) -> list[NetworthPoint]:
    opening_currency = normalize_currency(opening_currency)
    accounts: dict[str, set[str]] = {}
    for txn in txns:
        accounts.setdefault(normalize_currency(txn.currency), set()).add(txn.account)
    balances: dict[tuple[str, str], Decimal] = {}
    networths: dict[str, Decimal] = {}
    points: list[NetworthPoint] = []
    for t in txns:
        currency = normalize_currency(t.currency)
        key = (currency, t.account)
        opening = opening_balance if currency == opening_currency and len(accounts[currency]) == 1 else Decimal("0")
        bal = balances.get(key, opening)
        nw = networths.get(currency, opening_networth if currency == opening_currency else Decimal("0"))
        if t.direction == Direction.IN:
            bal += t.amount
        else:
            bal -= t.amount
        if t.category in (Category.INCOME, Category.UNCATEGORIZED):
            nw += t.amount if t.direction == Direction.IN else -t.amount
        elif t.category == Category.EXPENSE:
            nw -= t.amount if t.direction == Direction.OUT else -t.amount
        # TRANSFER / INVESTMENT: neutral for networth
        balances[key], networths[currency] = bal, nw
        points.append(NetworthPoint(t.date, t.label or t.counterparty, bal, nw, t.account, currency))
    return points


def export_csv(points: list[NetworthPoint], path: str | Path) -> None:
    with Path(path).open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "event", "currency", "account", "account_balance", "networth"])
        for p in points:
            digits = fraction_digits(p.currency)
            event = "'" + p.event if p.event.startswith(("=", "+", "-", "@", "\t", "\r", "\n")) else p.event
            account = "'" + p.account if p.account.startswith(("=", "+", "-", "@", "\t", "\r", "\n")) else p.account
            w.writerow([p.date.isoformat(), event, p.currency, account,
                        f"{p.balance:.{digits}f}", f"{p.networth:.{digits}f}"])


def render_table(points: list[NetworthPoint]) -> str:
    rows = [
        [p.date.isoformat(), p.event[:40], p.currency, p.account or "—",
         f"{p.balance:,.{fraction_digits(p.currency)}f}",
         f"{p.networth:,.{fraction_digits(p.currency)}f}"]
        for p in points
    ]
    from .report import render_table as _rt
    return _rt(["Date", "Event", "Currency", "Account", "Account balance", "Networth"], rows)
