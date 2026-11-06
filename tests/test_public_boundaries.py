"""Regression coverage for keeping private datasets outside the demo API boundary."""
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from threading import Lock
from unittest.mock import patch

from fastapi import HTTPException, UploadFile

import app
from finreport.imports import ImportStore


class PublicationBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.data = self.root / "examples"
        self.data.mkdir()
        self.uploads = self.root / "private" / "uploads" / "demo"
        self.sample = b"date;amount;counterparty\n2024-01-01;50.00;Example Employer\n"
        (self.data / "demo.csv").write_bytes(self.sample)
        self.private = self.root / "private" / "data"
        self.private.mkdir(parents=True)
        (self.private / "secret.csv").write_bytes(self.sample)
        rules = self.root / "rules.json"
        rules.write_text('{"rules": [{"category": "income", "label": "Employer", "patterns": ["example employer"]}]}', encoding="utf-8")
        self.store = ImportStore(self.root, "demo", rules, self.root / "private" / "ai" / "demo" / "rules.json",
                                 "", "offline-test-model", Lock(), autostart=False)
        self.addCleanup(self.store.close)
        patch.object(app, "_store", return_value=self.store).start()
        self.addCleanup(patch.stopall)
        for name, value in {"ROOT": self.root, "DATA": self.data,
                            "UPLOADS": self.uploads, "RULES": rules}.items():
            patch.object(app, name, value).start()

    async def test_demo_cannot_open_private_or_traversal_paths(self):
        _, transactions = app._load("examples/demo.csv")
        self.assertEqual(str(transactions[0].amount), "50.00")
        for path in ("private/data/secret.csv", "examples/../private/data/secret.csv",
                     str(self.private / "secret.csv"), "../secret.csv"):
            with self.subTest(path=path), self.assertRaises(HTTPException) as error:
                app._load(path)
            self.assertEqual(error.exception.status_code, 404)
        (self.data / "linked.csv").symlink_to(self.private / "secret.csv")
        self.assertEqual(app.api_files()["files"], ["examples/demo.csv"])

    async def test_import_stays_private_and_never_overwrites_sources(self):
        for name in ("../escape.csv", "/tmp/escape.csv", "folder\\escape.csv"):
            with self.subTest(name=name), self.assertRaises(HTTPException) as error:
                await app.api_import(UploadFile(filename=name, file=io.BytesIO(self.sample)))
            self.assertEqual(error.exception.status_code, 400)
        response = await app.api_import(UploadFile(filename="statement.CSV", file=io.BytesIO(self.sample)))
        result = json.loads(response.body)
        self.store.process(result["id"])
        saved = self.store.get(result["id"])
        source, _ = self.store.asset(result["id"], "source")
        self.assertTrue(source.is_relative_to(self.root / "private"))
        self.assertEqual(source.read_bytes(), self.sample)
        with self.assertRaises(HTTPException):
            app._load(str(source.relative_to(self.root)))
        _, transactions = app._load(saved["file"])
        self.assertEqual(str(transactions[0].amount), "50.00")
        repeated = await app.api_import(UploadFile(filename="renamed.csv", file=io.BytesIO(self.sample)))
        self.assertEqual(json.loads(repeated.body)["id"], result["id"])
        replacement = await app.api_import(UploadFile(filename="statement.CSV", file=io.BytesIO(b"replacement")))
        self.assertNotEqual(json.loads(replacement.body)["id"], result["id"])
        self.assertEqual(source.read_bytes(), self.sample)
        self.assertEqual((self.data / "demo.csv").read_bytes(), self.sample)

    async def test_import_never_inherits_demo_capital_and_selection_is_scoped(self):
        self.assertIsNone(app.api_files()["default"])
        response = await app.api_import(UploadFile(filename="statement.csv", file=io.BytesIO(self.sample)))
        result = json.loads(response.body)
        self.store.process(result["id"])
        saved = self.store.get(result["id"])
        data = app.api_transactions(saved["file"])
        self.assertEqual(data["balance_basis_by_currency"], {"CHF": "cash_flow"})
        self.assertEqual(data["opening_values"], {"CHF": {"balance": "0", "networth": "0"}})
        self.assertEqual(data["transactions"][0]["balance"], "50.00")
        app.api_session(app.SessionRequest(file="examples/demo.csv"))
        self.assertEqual(app.api_files()["default"], "examples/demo.csv")
        with self.assertRaises(HTTPException):
            app.api_session(app.SessionRequest(file="private/data/secret.csv"))
        self.assertEqual(app.api_files()["default"], "examples/demo.csv")

    async def test_normalized_download_escapes_formulas_without_changing_plain_text_or_amounts(self):
        accounts = ["retirement", "tax-account", "-1+2", "+1+2", "=1+2", "@SUM(A1:A2)"]
        source = io.StringIO(newline="")
        writer = csv.writer(source, delimiter=";")
        writer.writerow(["date", "amount", "currency", "counterparty", "account"])
        for account in accounts:
            writer.writerow(["2025-04-05", "10.25", "CHF", "Example Employer", account])
        original = source.getvalue().encode()
        response = await app.api_import(UploadFile(filename="accounts.csv", file=io.BytesIO(original)))
        identifier = json.loads(response.body)["id"]
        self.store.process(identifier)
        path, _ = self.store.asset(identifier, "normalized")
        with path.open(newline="") as stream:
            stored = list(csv.reader(stream, delimiter=";"))
        download = app.api_import_normalized(identifier)
        exported = list(csv.reader(io.StringIO(download.body.decode()), delimiter=";"))
        self.assertEqual(exported[0], stored[0])
        self.assertEqual([row[:6] for row in exported[1:]], [row[:6] for row in stored[1:]])
        self.assertEqual([row[6] for row in exported[1:]],
                         ["retirement", "tax-account", "'-1+2", "'+1+2", "'=1+2", "'@SUM(A1:A2)"])
        source_path, _ = self.store.asset(identifier, "source")
        self.assertEqual(source_path.read_bytes(), original)
        with path.open(newline="") as stream:
            self.assertEqual(list(csv.reader(stream, delimiter=";")), stored)

    async def test_mixed_currency_import_balances_and_download_keep_denominations(self):
        content = (b"date;amount;currency;counterparty\n"
                   b"2025-01-01;100.00;CHF;Example Employer\n"
                   b"2025-01-01;200.00;EUR;Example Employer\n"
                   b"2025-01-02;-10.25;EUR;Example Employer\n"
                   b"2025-01-03;1.234;KWD;Example Employer\n")
        response = await app.api_import(UploadFile(filename="mixed.csv", file=io.BytesIO(content)))
        identifier = json.loads(response.body)["id"]
        self.store.process(identifier)
        saved = self.store.get(identifier)
        data = app.api_transactions(saved["file"])
        self.assertEqual(
            [(t["currency"], t["balance"]) for t in data["transactions"]],
            [("CHF", "100.00"), ("EUR", "200.00"), ("EUR", "189.75"), ("KWD", "1.234")])
        summary = app.api_data(saved["file"])
        self.assertEqual(summary["totals"], {
            "CHF": {"in": "100.00", "out": "0"},
            "EUR": {"in": "200.00", "out": "10.25"},
            "KWD": {"in": "1.234", "out": "0"}})
        selected = app.api_transactions(saved["file"], currency="EUR")
        self.assertEqual([t["balance"] for t in selected["transactions"]], ["200.00", "189.75"])
        for request in (lambda: app.api_chart("monthly", saved["file"]),
                        lambda: app.api_graph(saved["file"], min_count=1),
                        lambda: app.api_transactions(saved["file"], currency="USD")):
            with self.assertRaises(HTTPException) as error:
                request()
            self.assertEqual(error.exception.status_code, 400)
        downloaded = app.api_import_normalized(identifier)
        rows = list(csv.DictReader(io.StringIO(downloaded.body.decode()), delimiter=";"))
        self.assertEqual([(row["currency"], row["amount"]) for row in rows],
                         [("CHF", "100.00"), ("EUR", "200.00"), ("EUR", "10.25"), ("KWD", "1.234")])

    async def test_year_filter_retains_earlier_native_currency_balance(self):
        (self.data / "history.csv").write_text(
            "date;amount;currency;counterparty\n"
            "2024-12-31;100.00;EUR;Example Employer\n"
            "2025-01-02;20.00;EUR;Example Employer\n")
        result = app.api_networth("examples/history.csv", year=2025)
        self.assertEqual([(point["date"], point["currency"], point["balance"])
                          for point in result["points"]], [("2025-01-02", "EUR", "120.00")])

    async def test_pre_currency_saved_import_download_gets_explicit_chf_without_rewriting_it(self):
        response = await app.api_import(UploadFile(filename="legacy.csv", file=io.BytesIO(self.sample)))
        identifier = json.loads(response.body)["id"]
        self.store.process(identifier)
        path, _ = self.store.asset(identifier, "normalized")
        old_content = "id;date;amount;direction;counterparty;payment_type;account\n2;2024-01-01;50.00;in;Example Employer;;\n"
        path.write_text(old_content)
        downloaded = app.api_import_normalized(identifier)
        rows = list(csv.DictReader(io.StringIO(downloaded.body.decode()), delimiter=";"))
        self.assertEqual((rows[0]["currency"], rows[0]["amount"]), ("CHF", "50.00"))
        self.assertEqual(path.read_text(), old_content)


if __name__ == "__main__":
    unittest.main()
