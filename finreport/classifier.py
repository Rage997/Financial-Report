"""Rule-based counterparty classification (rules.json, first match wins)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .models import Category, Transaction


@dataclass(frozen=True)
class Rule:
    category: Category
    label: str
    patterns: tuple[str, ...]  # lowercase; substrings for authored rules, full names for reviewed rules
    exact: bool = False  # False: authored substring match, True: reviewed full-name match


def rule_matches(rule: Rule, counterparty: str) -> bool:
    """Case-insensitive match: authored rules match substrings, reviewed rules the full name."""
    cp = counterparty.lower()
    if rule.exact:
        return any(cp == pattern for pattern in rule.patterns)
    return any(pattern in cp for pattern in rule.patterns)

# Fallback when no rule matches, by parser-detected payment type:
# a debit-card purchase is an expense, an ATM withdrawal moves money to cash
# (own asset), interest is income.
DEFAULT_BY_TYPE: dict[str, Category] = {
    "card": Category.EXPENSE,
    "atm": Category.TRANSFER,
    "interest": Category.INCOME,
}


def load_rules(path: str | Path, *, exact: bool = False) -> list[Rule]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rules: list[Rule] = []
    for i, r in enumerate(data.get("rules", [])):
        try:
            category = Category(r["category"])
        except ValueError as e:
            raise ValueError(f"rules.json: rule {i} has invalid category {r['category']!r}") from e
        patterns = tuple(p.lower() for p in r["patterns"])
        if not patterns:
            raise ValueError(f"rules.json: rule {i} ({r['label']}) has no patterns")
        rules.append(Rule(category, r["label"], patterns, exact))
    return rules


def classify(counterparty: str, rules: list[Rule], payment_type: str = "") -> tuple[Category, str]:
    for rule in rules:
        if rule_matches(rule, counterparty):
            return rule.category, rule.label
    if payment_type in DEFAULT_BY_TYPE:
        return DEFAULT_BY_TYPE[payment_type], ""
    return Category.UNCATEGORIZED, ""


def classify_all(txns: list[Transaction], rules: list[Rule]) -> None:
    for t in txns:
        t.category, t.label = classify(t.counterparty, rules, t.payment_type)
