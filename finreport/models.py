"""Core data model."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum


class Direction(Enum):
    IN = "in"
    OUT = "out"


class Category(Enum):
    INCOME = "income"
    EXPENSE = "expense"
    TRANSFER = "transfer"
    INVESTMENT = "investment"
    UNCATEGORIZED = "uncategorized"


@dataclass
class Transaction:
    txn_id: str
    date: date
    amount: Decimal          # always positive
    direction: Direction
    counterparty: str
    category: Category = Category.UNCATEGORIZED
    label: str = ""          # rule label (e.g. "IBKR"), empty when uncategorized
    payment_type: str = ""
    account: str = ""
    currency: str = "CHF"
