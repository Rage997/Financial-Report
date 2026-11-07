"""Consumer-visible anomaly thresholds, chronology and identity boundaries."""
import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal

from finreport.anomalies import anomaly_flags
from finreport.models import Direction, Transaction


def series(amounts, **kwargs):
    return [Transaction('duplicate-id', date(2025, 1, index + 1), Decimal(amount),
                        Direction.OUT, 'Rent', **kwargs) for index, amount in enumerate(amounts)]


class AnomalyTests(unittest.TestCase):
    def test_strict_threshold_and_native_minor_unit_precision(self):
        for currency, base, equal, above in [('CHF', '100.00', '115.00', '115.01'),
                                            ('KWD', '100.001', '115.001', '115.002'),
                                            ('JPY', '100', '115', '116')]:
            with self.subTest(currency=currency):
                self.assertEqual(anomaly_flags(series([base] * 3 + [equal], currency=currency)), [None] * 4)
                flags = anomaly_flags(series([base] * 3 + [above], currency=currency))
                self.assertEqual(flags[:3], [None] * 3)
                self.assertEqual(flags[3]['robust_limit'], Decimal(equal))
                self.assertEqual(flags[3]['history_count'], 3)

    def test_mad_and_ratio_are_independent_alternatives(self):
        flag = anomaly_flags(series(['0', '100', '200', '301']))[-1]
        self.assertEqual((flag['median'], flag['mad'], flag['robust_limit'], flag['ratio_limit']),
                         (Decimal(100), Decimal(100), Decimal(400), Decimal(300)))
        self.assertIn('exceeds 3 × median;', flag['reason'])
        self.assertEqual(anomaly_flags(series(['0', '100', '200', '300'])), [None] * 4)

    def test_first_payments_never_borrow_future_history(self):
        self.assertEqual(anomaly_flags(series(['1000', '100', '100', '100'])), [None] * 4)
        self.assertEqual(anomaly_flags(series(['100', '100', '9999'])), [None] * 3)
        self.assertEqual(anomaly_flags([]), [])

    def test_order_and_duplicate_ids_preserve_occurrence_identity(self):
        ordered = series(['100', '100', '100', '200'])
        txns = list(reversed(ordered))
        original = list(txns)
        flags = anomaly_flags(txns)
        self.assertEqual(txns, original)
        self.assertIsNotNone(flags[0])
        self.assertEqual(flags[1:], [None] * 3)

    def test_same_day_amounts_cannot_establish_each_others_history(self):
        txns = series(['100', '100', '100', '200'])
        txns = [replace(t, date=date(2025, 1, 1)) for t in txns]
        self.assertEqual(anomaly_flags(txns), [None] * 4)
        past = series(['100', '100', '100'])
        high = replace(past[-1], date=date(2025, 1, 4), amount=Decimal(200))
        normal = replace(high, amount=Decimal(100))
        flags = anomaly_flags(past + [high, normal])
        self.assertEqual(flags[-2]['history_count'], 3)
        self.assertIsNone(flags[-1])

    def test_accounts_currencies_directions_and_raw_names_never_merge(self):
        baseline = series(['100', '100', '100'], label='Shared')
        for change in ({'account': 'Other'}, {'currency': 'USD'}, {'direction': Direction.IN},
                       {'counterparty': 'Other'}, {'counterparty': ''}):
            with self.subTest(change=change):
                candidate = replace(baseline[-1], date=date(2025, 1, 4), amount=Decimal(999), **change)
                self.assertEqual(anomaly_flags(baseline + [candidate]), [None] * 4)

    def test_zero_baseline_uses_the_explicit_ratio_rule_without_division(self):
        flag = anomaly_flags(series(['0', '0', '0', '0.001'], currency='KWD'))[-1]
        self.assertEqual(flag['ratio_limit'], Decimal(0))
        self.assertIn('exceeds 3 × median;', flag['reason'])
