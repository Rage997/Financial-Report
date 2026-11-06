"""Multi-currency boundaries for report, graph, chart, and CLI selection."""
import unittest
from datetime import date
from decimal import Decimal

from finreport import graph, report
from finreport.__main__ import _require_single_currency, _select_currency
from finreport.charts import render_chart_png
from finreport.models import Category, Direction, Transaction


def txn(identifier, day, amount, currency, category, counterparty="Example"):
    value = Decimal(amount)
    return Transaction(identifier, date(2025, 1, day), abs(value),
                       Direction.IN if value >= 0 else Direction.OUT,
                       counterparty, category=category, currency=currency)




class ReportCurrencyTests(unittest.TestCase):
    def test_monthly_rows_never_sum_across_currencies(self):
        txns = [
            txn("1", 1, "100", "CHF", Category.INCOME),
            txn("2", 2, "50", "USD", Category.INCOME),
            txn("3", 3, "-20", "CHF", Category.EXPENSE),
            txn("4", 4, "-10", "USD", Category.EXPENSE),
        ]
        rows = report.monthly_rows(txns)
        self.assertEqual([(r["period"], r["currency"]) for r in rows],
                         [("2025-01", "CHF"), ("2025-01", "USD")])
        chf, usd = rows
        self.assertEqual((chf["income"], chf["expenses"], chf["net"]),
                         (Decimal("100"), Decimal("20"), Decimal("80")))
        self.assertEqual((usd["income"], usd["expenses"], usd["net"]),
                         (Decimal("50"), Decimal("10"), Decimal("40")))

    def test_yearly_rows_separate_currencies(self):
        txns = [
            txn("1", 1, "300", "CHF", Category.INCOME),
            txn("2", 2, "900", "USD", Category.INCOME),
            txn("3", 3, "-50", "USD", Category.EXPENSE),
        ]
        rows = report.yearly_rows(txns)
        self.assertEqual([(r["period"], r["currency"]) for r in rows],
                         [("2025", "CHF"), ("2025", "USD")])
        self.assertEqual(rows[0]["income"], Decimal("300"))
        self.assertEqual((rows[1]["income"], rows[1]["expenses"]),
                         (Decimal("900"), Decimal("50")))

    def test_counterparty_rows_split_by_currency_and_rank_within_currency(self):
        txns = [
            txn("1", 1, "500", "USD", Category.EXPENSE, counterparty="Shop"),
            txn("2", 2, "100", "CHF", Category.EXPENSE, counterparty="Shop"),
            txn("3", 3, "400", "CHF", Category.EXPENSE, counterparty="Bakery"),
        ]
        rows = report.counterparty_rows(txns)
        # CHF group first (ranked 400 > 100), then USD; no cross-currency ranking.
        self.assertEqual([(r["name"], r["currency"], r["total"]) for r in rows],
                         [("Bakery", "CHF", Decimal("400")),
                          ("Shop", "CHF", Decimal("100")),
                          ("Shop", "USD", Decimal("500"))])
        self.assertEqual(rows[2]["avg"], Decimal("500"))


    def test_report_tables_label_currency_and_use_denomination_precision(self):
        txns = [
            txn("1", 1, "1.234", "KWD", Category.INCOME),
            txn("2", 2, "-25", "USD", Category.EXPENSE, counterparty="Shop"),
        ]
        monthly = report.monthly_report(txns)
        self.assertIn("Currency", monthly)
        self.assertIn("KWD", monthly)
        self.assertIn("1.234", monthly)          # 3-decimal currency kept exact
        self.assertIn("USD", monthly)
        self.assertNotIn("CHF", monthly)
        counterparties = report.counterparty_report(txns)
        self.assertIn("USD", counterparties)
        self.assertNotIn("CHF", counterparties)
        uncategorized = report.uncategorized_report([
            txn("3", 3, "-0.001", "KWD", Category.UNCATEGORIZED),
        ])
        self.assertIn("0.001", uncategorized)


class GraphCurrencyTests(unittest.TestCase):
    def test_mixed_currencies_are_rejected(self):
        with self.assertRaises(ValueError):
            graph.build_graph([
                txn("1", 1, "10", "CHF", Category.EXPENSE, counterparty="A"),
                txn("2", 2, "10", "USD", Category.EXPENSE, counterparty="B"),
            ])

    def test_nodes_carry_currency_and_svg_labels_use_it(self):
        nodes = graph.build_graph([
            txn("1", 1, "500", "JPY", Category.EXPENSE, counterparty="Japan"),
            txn("2", 2, "200", "JPY", Category.EXPENSE, counterparty="Japan"),
        ])
        self.assertEqual(nodes["Japan"]["total"], Decimal("700"))
        svg = graph.render_svg(nodes)
        self.assertIn("JPY 700", svg)
        self.assertNotIn("CHF", svg)


class ChartCurrencyTests(unittest.TestCase):
    def test_mixed_currencies_are_rejected_before_rendering(self):
        with self.assertRaises(ValueError):
            render_chart_png("monthly", [
                txn("1", 1, "10", "CHF", Category.EXPENSE),
                txn("2", 2, "10", "USD", Category.EXPENSE),
            ])

    def test_opening_currency_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            render_chart_png("networth", [txn("1", 1, "10", "CHF", Category.INCOME)],
                             Decimal("1"), Decimal("1"), opening_currency="EUR")



class CliCurrencySelectionTests(unittest.TestCase):
    def setUp(self):
        self.txns = [
            txn("1", 1, "10", "CHF", Category.EXPENSE),
            txn("2", 2, "20", "USD", Category.EXPENSE),
        ]

    def test_valid_selection_filters_to_that_currency(self):
        selected = _select_currency(self.txns, "USD")
        self.assertEqual([t.currency for t in selected], ["USD"])

    def test_invalid_selection_fails_clearly(self):
        with self.assertRaises(SystemExit) as ctx:
            _select_currency(self.txns, "EUR")
        self.assertIn("EUR", str(ctx.exception))
        self.assertIn("CHF", str(ctx.exception))

    def test_mixed_input_without_selection_fails_for_single_currency_commands(self):
        with self.assertRaises(SystemExit) as ctx:
            _require_single_currency(self.txns, None, "graph")
        self.assertIn("--currency", str(ctx.exception))



if __name__ == "__main__":
    unittest.main()
