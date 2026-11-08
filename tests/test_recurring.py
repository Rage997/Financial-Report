"""Cadence, amount, identity and observation-boundary regressions."""
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from finreport.models import Direction, Transaction
from finreport.recurring import recurring_rows


def payment(day, amount="100", **kwargs):
    return Transaction(day, date.fromisoformat(day), Decimal(amount),
                       kwargs.pop("direction", Direction.OUT),
                       kwargs.pop("counterparty", "Rent"), **kwargs)


class RecurringTests(unittest.TestCase):
    def test_cadences_and_calendar_next_dates(self):
        cases = [
            (["2025-01-01", "2025-01-08", "2025-01-15"], "weekly", "2025-01-22"),
            (["2025-01-01", "2025-01-15", "2025-01-29"], "biweekly", "2025-02-12"),
            (["2025-01-01", "2025-02-01", "2025-03-01"], "monthly", "2025-04-01"),
            (["2022-06-15", "2023-06-15", "2024-06-15"], "yearly", "2025-06-15"),
        ]
        for days, cadence, due in cases:
            with self.subTest(cadence=cadence):
                row, = recurring_rows([payment(day) for day in days])
                self.assertEqual((row["cadence"], row["next_due"]), (cadence, due))

    def test_unsorted_history_does_not_mutate_input_and_detects_rent_increase(self):
        txns = [payment("2025-03-01", "120"), payment("2025-01-01"), payment("2025-02-01")]
        before = list(txns)
        row, = recurring_rows(txns)
        self.assertEqual(txns, before)
        self.assertEqual(row["average_amount"], Decimal("106.67"))
        self.assertEqual((row["trend"], row["trend_amount"], row["trend_percent"]),
                         ("up", Decimal("20.00"), Decimal("20.00")))
        self.assertEqual((row["last_paid"], row["last_amount"]), ("2025-03-01", Decimal("120")))

    def test_trend_uses_prior_median_and_handles_decrease_flat_and_zero(self):
        for amounts, trend, delta, percent in (
                (("100", "120", "99"), "down", "-11.00", "-10.00"),
                (("100", "100", "100"), "flat", "0.00", "0.00"),
                (("0", "0", "10"), "up", "10.00", None)):
            with self.subTest(amounts=amounts):
                row, = recurring_rows([payment(day, amount) for day, amount in zip(
                    ("2025-01-01", "2025-02-01", "2025-03-01"), amounts)])
                self.assertEqual(row["trend"], trend)
                self.assertEqual(row["trend_amount"], Decimal(delta))
                self.assertEqual(row["trend_percent"], Decimal(percent) if percent is not None else None)

    def test_small_groups_and_distinct_identities_are_not_combined(self):
        pair = [payment("2025-01-01", label="Housing"), payment("2025-02-01", label="Housing")]
        for field, value in (("counterparty", "Other rent"), ("account", "Other account"),
                             ("currency", "USD"), ("direction", Direction.IN)):
            with self.subTest(field=field):
                self.assertEqual(recurring_rows(pair + [replace(t, **{field: value}) for t in pair]), [])
        self.assertEqual(recurring_rows([]), [])
        self.assertEqual(recurring_rows([payment(day, counterparty=" ") for day in
                                         ("2025-01-01", "2025-02-01", "2025-03-01")]), [])

    def test_same_day_repeats_do_not_qualify_as_recurring(self):
        for days in (
                ("2025-01-01", "2025-02-01", "2025-02-01"),
                ("2025-01-01", "2025-01-01", "2025-02-01", "2025-03-01")):
            with self.subTest(days=days):
                self.assertEqual(recurring_rows([payment(day) for day in days]), [])

    def test_three_erratic_payments_do_not_qualify_as_recurring(self):
        # Gaps of 1 and 59 days have a monthly-looking median, not a schedule.
        self.assertEqual(recurring_rows([payment(day) for day in (
            "2025-01-01", "2025-01-02", "2025-03-02")]), [])

    def test_one_inconsistent_gap_disqualifies_a_candidate(self):
        # A majority of monthly intervals must not hide the long final gap.
        self.assertEqual(recurring_rows([payment(day) for day in (
            "2025-01-01", "2025-02-01", "2025-03-01", "2025-05-01")]), [])

    def test_posting_date_tolerance_applies_to_every_interval(self):
        start = date(2020, 1, 1)
        for cadence, low, high in (("weekly", 6, 8), ("biweekly", 12, 16),
                                   ("monthly", 26, 35), ("yearly", 350, 380)):
            for gaps, qualifies in (((low, high), True), ((low - 1, high), False),
                                    ((low, high + 1), False)):
                with self.subTest(cadence=cadence, gaps=gaps):
                    days = (start, start + timedelta(days=gaps[0]),
                            start + timedelta(days=sum(gaps)))
                    rows = recurring_rows([payment(day.isoformat()) for day in days])
                    self.assertEqual([r["cadence"] for r in rows], [cadence] if qualifies else [])

    def test_next_due_preserves_month_end_and_day_after_short_month(self):
        for days, expected in (
                (("2024-12-31", "2025-01-31", "2025-02-28"), "2025-03-31"),
                (("2024-12-30", "2025-01-30", "2025-02-28"), "2025-03-30"),
                (("2024-10-15", "2024-11-15", "2024-12-15"), "2025-01-15"),
                (("2016-02-29", "2017-02-28", "2018-02-28"), "2019-02-28")):
            with self.subTest(days=days):
                row, = recurring_rows([payment(day) for day in days])
                self.assertEqual(row["next_due"], expected)

    def test_overdue_is_relative_to_same_account_currency_history_not_today(self):
        salary = [payment(day, "5000", direction=Direction.IN, counterparty="Employer", account="A")
                  for day in ("2025-01-01", "2025-02-01", "2025-03-01")]
        other = [payment("2025-06-01", currency="USD", account="A"),
                 payment("2025-07-01", account="B")]
        row, = recurring_rows(salary + other)
        self.assertEqual((row["observed_through"], row["overdue_days"]), ("2025-03-01", 0))
        row, = recurring_rows(salary + other + [payment("2025-04-05", account="A")])
        self.assertEqual((row["observed_through"], row["next_due"], row["overdue_days"]),
                         ("2025-04-05", "2025-04-01", 4))

    def test_amount_precision_and_currency_separation(self):
        txns = []
        for currency, amounts in (("KWD", ("0.001", "0.002", "0.002")),
                                  ("JPY", ("1", "1", "2")),
                                  ("CHF", ("0.10", "0.20", "0.30"))):
            txns.extend(payment(day, amount, currency=currency) for day, amount in zip(
                ("2025-01-01", "2025-02-01", "2025-03-01"), amounts))
        rows = {row["currency"]: row for row in recurring_rows(txns)}
        self.assertEqual({cur: row["average_amount"] for cur, row in rows.items()},
                         {"CHF": Decimal("0.20"), "JPY": Decimal("1"), "KWD": Decimal("0.002")})
        self.assertEqual(rows["CHF"]["trend_percent"], Decimal("100.00"))


class RecurringCliTests(unittest.TestCase):
    def test_cli_uses_full_history_and_currency_filter_without_inference(self):
        from finreport.__main__ import main
        with tempfile.TemporaryDirectory() as folder:
            source, rules = Path(folder) / "statement.csv", Path(folder) / "rules.json"
            source.write_text("date;amount;currency;counterparty\n"
                              "2024-12-01;-10;CHF;Rent\n2025-01-01;-10;CHF;Rent\n2025-02-01;-12;CHF;Rent\n"
                              "2024-12-01;-5;USD;Other\n2025-01-01;-5;USD;Other\n2025-02-01;-5;USD;Other\n"
                              "2025-01-01;-7;CHF;RandomShop\n2025-01-02;-7;CHF;RandomShop\n2025-03-02;-7;CHF;RandomShop\n",
                              encoding="utf-8")
            rules.write_text('{"rules":[]}', encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                result = main(["recurring", "--file", str(source), "--rules", str(rules), "--currency", "CHF"])
            text = output.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("Rent", text)
            self.assertIn("monthly", text)
            self.assertIn("10.67", text)
            self.assertIn("+20.00%", text)
            self.assertIn("2025-03-01", text)
            self.assertNotIn("Other", text)
            self.assertNotIn("USD", text)
            self.assertNotIn("RandomShop", text)


if __name__ == "__main__":
    unittest.main()
