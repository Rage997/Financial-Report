"""Import lifecycle, saved financial facts, and rule-precedence regressions."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from threading import Lock
from unittest.mock import patch

from finreport import ai_rules, networth
from finreport.classifier import classify_all
from finreport.imports import ImportStore, _rule_key
from finreport.models import Category
from finreport.parser import load_transactions


class SavedImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.base = self.root / "rules.json"
        self.base.write_text(json.dumps({"rules": [
            {"category": "expense", "label": "Bakery", "patterns": ["example bakery"]}
        ]}))
        self.approved = self.root / "private" / "ai" / "demo" / "rules.json"
        self.store = self.make_store()
        self.addCleanup(lambda: self.store.close())

    def make_store(self):
        return ImportStore(self.root, "demo", self.base, self.approved, "", "offline-test-model",
                           Lock(), autostart=False)

    def wait_for_result(self, identifier):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            record = self.store.get(identifier)
            if record["status"] in {"ready", "failed"}:
                return record
            time.sleep(0.02)
        self.fail("Background import did not finish.")

    def submit(self, text, name="statement.csv"):
        record, created = self.store.submit(name, text.encode())
        self.assertTrue(created)
        self.store.start()
        return self.wait_for_result(record["id"])

    def transactions(self, record):
        path = self.root / record["file"]
        transactions = load_transactions(path)
        rules = ai_rules.effective_rules(self.base, self.approved)
        classify_all(transactions, rules)
        self.store.classify(path, transactions, rules)
        return transactions

    def test_duplicates_refunds_and_saved_selection_survive_restart(self):
        content = ("date;amount;counterparty\n2025-01-02;-25.20;Example Bakery\n"
                   "2025-01-02;-25.20;Example Bakery\n2025-01-03;5.00;Example Bakery\n")
        record = self.submit(content)
        self.assertEqual(record["status"], "ready", record["error"])
        transactions = self.transactions(record)
        self.assertEqual([(str(t.amount), t.direction.value) for t in transactions],
                         [("25.20", "out"), ("25.20", "out"), ("5.00", "in")])
        self.assertEqual(str(networth.compute_series(transactions)[-1].balance), "-45.40")
        source, name = self.store.asset(record["id"], "source")
        self.assertEqual((name, source.read_bytes()), ("statement.csv", content.encode()))
        self.store.close()
        self.store = self.make_store()
        self.assertEqual(self.store.active_file(), record["file"])
        same, created = self.store.submit("renamed.csv", content.encode())
        self.assertFalse(created)
        self.assertEqual((same["id"], same["status"], same["count"]), (record["id"], "ready", 3))
        self.assertEqual(len(self.store.ready_files()), 1)

    def test_failure_is_visible_and_retry_reuses_completed_normalization(self):
        record = self.submit("date;amount;counterparty\n2025-02-01;200;Independent Client\n")
        self.assertEqual(record["status"], "failed")
        self.assertIsNone(record["file"])
        self.assertFalse(self.store.ready_files())
        self.base.write_text(json.dumps({"rules": [
            {"category": "income", "label": "Freelance", "patterns": ["independent client"]}
        ]}))
        with patch("finreport.import_formats.normalize_file", side_effect=AssertionError("Normalization repeated")):
            self.store.retry(record["id"])
            saved = self.wait_for_result(record["id"])
        self.assertEqual(saved["status"], "ready", saved["error"])
        self.assertEqual(self.transactions(saved)[0].category, Category.INCOME)

    def test_cached_income_never_applies_to_new_outgoing_payment_and_manual_rule_wins(self):
        record = self.submit("date;amount;counterparty\n2025-02-01;200;Independent Client\n")
        self.assertEqual(record["status"], "failed")
        path = self.store.directory / record["id"] / "transactions.csv"
        candidate = ai_rules.candidates(load_transactions(path), [])[0]
        decision = {**candidate, "category": "income", "label": "Independent Client",
                    "reason": "Previously classified freelance payment"}
        # Fixture represents a completed batch persisted before an interrupted import.
        with self.store._connect() as db:
            db.execute("INSERT INTO automatic_rules VALUES (?,?,?)",
                       (_rule_key(candidate), "offline-test-model", json.dumps(decision)))
        self.store.retry(record["id"])
        saved = self.wait_for_result(record["id"])
        self.assertEqual(saved["status"], "ready", saved["error"])
        self.assertEqual(self.transactions(saved)[0].category, Category.INCOME)
        outgoing = self.submit("date;amount;counterparty\n2025-03-01;-60;Independent Client\n")
        self.assertEqual(outgoing["status"], "failed")
        self.assertIsNone(outgoing["file"])
        self.base.write_text(json.dumps({"rules": [
            {"category": "expense", "label": "Refund", "patterns": ["independent client"]}
        ]}))
        self.assertEqual(self.transactions(saved)[0].category, Category.EXPENSE)

    def test_unambiguous_payment_types_do_not_require_model_inference(self):
        record = self.submit('"date","direction","booking_id","amount","description"\n'
                             '"2025-04-01","out","1","-30.00","PRELEVAMENTO ATM SVIZZERA BANCA POP. SONDRIO (SUISS 01.04.2025 / 10:00:00 LUGANO"\n'
                             '"2025-04-02","out","2","-15.00","ACQUISTO DEBIT CARD SVIZZERA BANCA POP. SONDRIO (SUISS 02.04.2025 / 10:00:00 Example Cafe"\n')
        self.assertEqual(record["status"], "ready", record["error"])
        self.assertEqual([t.category for t in self.transactions(record)],
                         [Category.TRANSFER, Category.EXPENSE])

    def test_source_assets_and_deduplication_are_profile_scoped(self):
        content = b"date;amount;counterparty\n2025-02-01;-8;Example Bakery\n"
        record, _ = self.store.submit("statement.csv", content)
        other = ImportStore(self.root, "private", self.base, self.approved, "", "offline-test-model",
                            Lock(), autostart=False)
        self.addCleanup(other.close)
        with self.assertRaises(KeyError):
            other.asset(record["id"], "source")
        private_record, created = other.submit("statement.csv", content)
        self.assertTrue(created)
        self.assertNotEqual(private_record["id"], record["id"])
        self.assertEqual(other.active_file(), None)


if __name__ == "__main__":
    unittest.main()
