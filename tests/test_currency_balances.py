"""Financial invariants for interleaved native-currency cash flows."""
import csv
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

from finreport.models import Category, Direction, Transaction
from finreport.networth import compute_series, config_currency, export_csv, load_config


class CurrencyBalanceTests(unittest.TestCase):
    def transaction(self, identifier, amount, currency, category):
        value = Decimal(amount)
        return Transaction(identifier, date(2025, 1, int(identifier)), abs(value),
                           Direction.IN if value >= 0 else Direction.OUT,
                           "Example counterparty", category=category, currency=currency)

    def test_interleaved_currencies_refunds_and_transfers_have_independent_histories(self):
        transactions = [
            self.transaction("1", "100", "CHF", Category.INCOME),
            self.transaction("2", "200", "EUR", Category.INCOME),
            self.transaction("3", "-20", "CHF", Category.EXPENSE),
            self.transaction("4", "-50", "EUR", Category.TRANSFER),
            self.transaction("5", "5", "CHF", Category.EXPENSE),
            self.transaction("6", "-30", "EUR", Category.INVESTMENT),
            self.transaction("7", "-10", "EUR", Category.EXPENSE),
        ]
        points = compute_series(transactions, Decimal("1000"), Decimal("3000"))
        self.assertEqual([(p.currency, p.balance, p.networth) for p in points], [
            ("CHF", Decimal("1100"), Decimal("3100")),
            ("EUR", Decimal("200"), Decimal("200")),
            ("CHF", Decimal("1080"), Decimal("3080")),
            ("EUR", Decimal("150"), Decimal("200")),
            ("CHF", Decimal("1085"), Decimal("3085")),
            ("EUR", Decimal("120"), Decimal("200")),
            ("EUR", Decimal("110"), Decimal("190")),
        ])

    def test_opening_currency_is_explicit_and_legacy_openings_are_chf(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"opening_balance": "100", "opening_networth": "250"}))
            self.assertEqual(config_currency(path), "CHF")
            self.assertEqual(load_config(path, "EUR"), (Decimal("0"), Decimal("0")))
            path.write_text(json.dumps({"currency": "EUR", "opening_balance": "100",
                                        "opening_networth": "250"}))
            self.assertEqual(config_currency(path), "EUR")
            self.assertEqual(load_config(path), (Decimal("0"), Decimal("0")))
            points = compute_series([
                self.transaction("1", "20", "CHF", Category.INCOME),
                self.transaction("2", "30", "EUR", Category.INCOME),
            ], *load_config(path, "EUR"), opening_currency="EUR")
            self.assertEqual([(p.balance, p.networth) for p in points],
                             [(Decimal("20"), Decimal("20")), (Decimal("130"), Decimal("280"))])

    def test_accounts_keep_independent_balances_without_inventing_openings(self):
        txns = [
            replace(self.transaction("1", "100", "CHF", Category.INCOME), account="A"),
            replace(self.transaction("2", "200", "CHF", Category.INCOME), account="B"),
            replace(self.transaction("3", "-20", "CHF", Category.EXPENSE), account="A"),
            replace(self.transaction("4", "-50", "CHF", Category.TRANSFER), account="B"),
        ]
        points = compute_series(txns, Decimal("1000"), Decimal("3000"))
        self.assertEqual([(p.account, p.balance) for p in points],
                         [("A", Decimal(100)), ("B", Decimal(200)),
                          ("A", Decimal(80)), ("B", Decimal(150))])
        self.assertEqual([p.networth for p in points],
                         [Decimal(3100), Decimal(3300), Decimal(3280), Decimal(3280)])
        single = compute_series([txns[0], txns[2]], Decimal(1000))
        self.assertEqual([p.balance for p in single], [Decimal(1100), Decimal(1080)])

    def test_export_preserves_account_provenance_and_escapes_formulas(self):
        txns = [replace(self.transaction("1", "10", "CHF", Category.INCOME), account="=unsafe"),
                replace(self.transaction("2", "20", "CHF", Category.INCOME), account="B")]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "balances.csv"
            export_csv(compute_series(txns), path)
            with path.open(newline="") as stream:
                rows = list(csv.DictReader(stream))
        self.assertEqual([(r["account"], r["account_balance"]) for r in rows],
                         [("'=unsafe", "10.00"), ("B", "20.00")])

    def test_balance_export_preserves_three_and_zero_decimal_denominations(self):
        points = compute_series([
            self.transaction("1", "1.234", "KWD", Category.INCOME),
            self.transaction("2", "500", "JPY", Category.INCOME),
            self.transaction("3", "-0.001", "KWD", Category.EXPENSE),
        ])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "balances.csv"
            export_csv(points, path)
            with path.open(newline="") as stream:
                rows = list(csv.DictReader(stream))
        self.assertEqual([(r["currency"], r["account_balance"], r["networth"]) for r in rows],
                         [("KWD", "1.234", "1.234"), ("JPY", "500", "500"),
                          ("KWD", "1.233", "1.233")])


if __name__ == "__main__":
    unittest.main()
