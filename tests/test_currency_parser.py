"""Currency and precision boundaries on saved and legacy CLI CSV inputs."""
import csv
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from finreport.models import Direction, Transaction
from finreport.parser import load_transactions, write_normalized


class CurrencyParserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "statement.csv"

    def test_bps_div_currency_and_debit_direction_survive_legacy_loading(self):
        self.path.write_text("Data esecuzione;Valuta;Beneficiario;Totale;Div.;Conto di addebito\n"
                             "01.01.2025;02.01.2025;Example;19.60;EUR;Account\n"
                             "02.01.2025;03.01.2025;Example;0.001;KWD;Account\n")
        txns = load_transactions(self.path)
        self.assertEqual([(t.amount, t.currency, t.direction) for t in txns],
                         [(Decimal("19.60"), "EUR", Direction.OUT),
                          (Decimal("0.001"), "KWD", Direction.OUT)])

    def test_format_b_currency_is_preserved_and_conflicting_amount_label_fails(self):
        with self.path.open("w", newline="") as stream:
            writer = csv.writer(stream, quoting=csv.QUOTE_ALL)
            writer.writerow(["date", "value_date", "description", "amount", "currency", "direction", "details", "booking_id"])
            writer.writerow(["2025-01-01", "", "Example", "1.234", "KWD", "in", "", "1"])
        txn = load_transactions(self.path)[0]
        self.assertEqual((txn.amount, txn.currency), (Decimal("1.234"), "KWD"))
        self.path.write_text(self.path.read_text().replace('"1.234"', '"USD 1.234"'))
        with self.assertRaises(ValueError):
            load_transactions(self.path)

    def test_amount_currency_cannot_disappear_when_no_currency_column_exists(self):
        self.path.write_text("date;amount;counterparty\n2025-01-01;EUR -12.50;Example\n")
        txn = load_transactions(self.path)[0]
        self.assertEqual((txn.amount, txn.currency, txn.direction),
                         (Decimal("12.50"), "EUR", Direction.OUT))

    def test_saved_currency_roundtrip_and_invalid_minor_units(self):
        txns = [Transaction("1", date(2025, 1, 1), Decimal("1.234"), Direction.IN,
                            "Example", currency="KWD"),
                Transaction("2", date(2025, 1, 2), Decimal("500"), Direction.OUT,
                            "Example", currency="JPY")]
        write_normalized(self.path, txns)
        self.assertEqual(load_transactions(self.path), txns)
        self.path.write_text(self.path.read_text().replace(";500;", ";500.5;"))
        with self.assertRaises(ValueError):
            load_transactions(self.path)
        txns[1].amount = Decimal("500.5")
        with self.assertRaises(ValueError):
            write_normalized(self.path, txns)


if __name__ == "__main__":
    unittest.main()
