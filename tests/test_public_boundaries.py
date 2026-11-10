"""Regression coverage for keeping private datasets outside the demo API boundary."""
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from threading import Lock
from unittest.mock import patch
from urllib.parse import urlsplit

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

    async def request(self, target, *, host="localhost:8010", method="GET",
                      headers=(), body=b""):
        url = urlsplit(target)
        received = False
        messages = []

        async def receive():
            nonlocal received
            if received:
                return {"type": "http.disconnect"}
            received = True
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            messages.append(message)

        request_headers = [] if host is None else [(b"host", host.encode())]
        await app.app({
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1", "method": method, "scheme": "http",
            "path": url.path, "raw_path": url.path.encode(), "root_path": "",
            "query_string": url.query.encode(), "server": ("127.0.0.1", 8010),
            "client": ("127.0.0.1", 50000), "headers": request_headers + list(headers),
        }, receive, send)
        status = next(message["status"] for message in messages
                      if message["type"] == "http.response.start")
        content = b"".join(message.get("body", b"") for message in messages
                           if message["type"] == "http.response.body")
        return status, content

    async def test_untrusted_host_cannot_read_any_financial_or_browser_surface(self):
        response = await app.api_import(UploadFile(filename="statement.csv",
                                                   file=io.BytesIO(self.sample)))
        identifier = json.loads(response.body)["id"]
        self.store.process(identifier)
        paths = (
            "/", "/static/imports.js", "/docs", "/openapi.json", "/api/files",
            "/api/data?file=examples/demo.csv", "/api/transactions?file=examples/demo.csv",
            "/api/networth?file=examples/demo.csv", "/api/graph?file=examples/demo.csv",
            "/api/recurring?file=examples/demo.csv",
            "/api/anomalies?file=examples/demo.csv",
            "/api/chart?name=monthly&file=examples/demo.csv", "/api/workspace",
            "/api/ai/status", "/api/ai/candidates?file=examples/demo.csv",
            f"/api/imports/{identifier}", f"/api/imports/{identifier}/source",
            f"/api/imports/{identifier}/normalized",
        )
        for path in paths:
            with self.subTest(path=path):
                status, content = await self.request(
                    path, host="attacker.example",
                    headers=((b"origin", b"http://attacker.example"),
                             (b"sec-fetch-site", b"same-origin")))
                self.assertEqual(status, 403)
                self.assertNotIn(b"Example Employer", content)
        status, content = await self.request("/api/transactions?file=examples/demo.csv")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(content)["transactions"][0]["counterparty"], "Example Employer")

    async def test_host_authority_validation_keeps_local_ports_and_ipv6(self):
        for host in ("localhost", "LOCALHOST:8010", "127.0.0.1:8010", "[::1]",
                     "[::1]:8010", "app:8010", "testserver"):
            with self.subTest(host=host):
                status, content = await self.request("/api/files", host=host)
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(content)["files"], ["examples/demo.csv"])
        for host in (None, "", "attacker.example", "localhost.attacker.example",
                     "localhost:8010@attacker.example", "127.0.0.1.nip.io",
                     "[::1].attacker.example", "localhost:8010:9000", "localhost:bad",
                     "localhost:65536", "localhost/path", "localhost#attacker",
                     "localhost?attacker"):
            with self.subTest(host=host):
                status, _ = await self.request("/api/files", host=host)
                self.assertEqual(status, 403)
        for first, second in (("localhost", "attacker.example"), ("attacker.example", "localhost")):
            with self.subTest(first=first):
                status, _ = await self.request("/api/files", host=first,
                                               headers=((b"host", second.encode()),))
                self.assertEqual(status, 403)

    async def test_trusted_host_does_not_bypass_mutation_origin_guards(self):
        body = b'{"file":"examples/demo.csv"}'
        for headers, expected in (
            (((b"origin", b"http://attacker.example"), (b"content-type", b"application/json")), 403),
            (((b"origin", b"http://localhost:8010"), (b"sec-fetch-site", b"cross-site"),
              (b"content-type", b"application/json")), 403),
            (((b"origin", b"http://localhost:8010"), (b"content-type", b"text/plain")), 415),
        ):
            with self.subTest(headers=headers):
                status, _ = await self.request("/api/session", method="POST", headers=headers, body=body)
                self.assertEqual(status, expected)
                self.assertIsNone(app.api_files()["default"])
        status, _ = await self.request(
            "/api/session", method="POST", body=body,
            headers=((b"origin", b"http://localhost:8010"), (b"content-type", b"application/json")))
        self.assertEqual(status, 200)
        self.assertEqual(app.api_files()["default"], "examples/demo.csv")

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

    async def test_sources_and_accounts_keep_provenance_and_independent_balances(self):
        (self.data / "demo.csv").write_text(
            "id;date;amount;direction;counterparty;payment_type;account;currency\n"
            "1;2025-01-01;50;in;Example Employer;;A;CHF\n"
            "2;2025-01-02;100;in;Example Employer;;B;CHF\n"
            "3;2025-01-03;20;out;Shop;;A;CHF\n", encoding="utf-8")
        (self.data / "second.csv").write_text(
            "id;date;amount;direction;counterparty;payment_type;account;currency\n1;2025-01-01;7;in;Example Employer;;A;CHF\n",
            encoding="utf-8")
        config = self.root / "config.json"
        config.write_text('{"opening_balance":1000,"opening_networth":2000,"currency":"CHF"}')
        with patch.object(app, "CONFIG", config):
            first = app.api_transactions("examples/demo.csv")
            second = app.api_transactions("examples/second.csv")
            networth = app.api_networth("examples/demo.csv")
        self.assertEqual([(r["source"], r["account"], r["balance"]) for r in first["transactions"]],
                         [("examples/demo.csv", "A", "50"), ("examples/demo.csv", "B", "100"),
                          ("examples/demo.csv", "A", "30")])
        self.assertEqual(first["opening_values"]["CHF"]["balance"], "0")
        self.assertEqual(second["transactions"][0]["balance"], "1007")
        self.assertEqual(second["transactions"][0]["source"], "examples/second.csv")
        self.assertEqual(networth["points"][-1]["networth"], "2130")

    async def test_anomaly_api_keeps_earlier_history_and_decimal_thresholds(self):
        (self.data / "demo.csv").write_text(
            "date;amount;currency;counterparty\n"
            "2025-01-01;-100.001;KWD;Rent\n2025-02-01;-100.001;KWD;Rent\n"
            "2025-03-01;-100.001;KWD;Rent\n2025-04-01;-115.002;KWD;Rent\n", encoding="utf-8")
        status, content = await self.request("/api/anomalies?file=examples/demo.csv&currency=KWD")
        self.assertEqual(status, 200)
        row, = json.loads(content)["rows"]
        self.assertEqual((row["index"], row["amount"], row["robust_limit"]), (3, "115.002", "115.001"))
        status, content = await self.request("/api/transactions?file=examples/demo.csv")
        self.assertEqual(status, 200)
        rows = json.loads(content)["transactions"]
        self.assertEqual([r["anomaly"] is not None for r in rows], [False, False, False, True])
        for target, expected in (("/api/anomalies?file=private/data/secret.csv", 404),
                                 ("/api/anomalies?file=examples/demo.csv&currency=USD", 400)):
            status, _ = await self.request(target)
            self.assertEqual(status, expected)

    async def test_recurring_history_preserves_precision_and_dataset_currency_boundaries(self):
        (self.data / "demo.csv").write_text(
            "date;amount;currency;counterparty\n"
            "2024-12-01;-0.001;KWD;Rent\n"
            "2025-01-01;-0.002;KWD;Rent\n"
            "2025-02-01;-0.003;KWD;Rent\n"
            "2025-03-05;-1.000;KWD;Other\n"
            "2024-12-01;500;CHF;Salary\n"
            "2025-01-01;500;CHF;Salary\n"
            "2025-02-01;600;CHF;Salary\n", encoding="utf-8")
        status, content = await self.request("/api/recurring?file=examples/demo.csv&currency=KWD")
        self.assertEqual(status, 200)
        row, = json.loads(content)["rows"]
        self.assertEqual((row["counterparty"], row["currency"], row["direction"], row["count"]),
                         ("Rent", "KWD", "out", 3))
        self.assertEqual((row["average_amount"], row["trend_percent"]), ("0.002", "100.00"))
        self.assertEqual((row["next_due"], row["overdue_days"]), ("2025-03-01", 4))
        status, content = await self.request("/api/recurring?file=examples/demo.csv")
        self.assertEqual(status, 200)
        self.assertEqual({r["currency"] for r in json.loads(content)["rows"]}, {"CHF", "KWD"})
        for target, expected in (
                ("/api/recurring?file=private/data/secret.csv", 404),
                ("/api/recurring?file=examples/../private/data/secret.csv", 404),
                ("/api/recurring?file=examples/demo.csv&currency=USD", 400),
                ("/api/recurring?file=examples/demo.csv&currency=unknown", 400)):
            with self.subTest(target=target):
                status, _ = await self.request(target)
                self.assertEqual(status, expected)

    async def test_recurring_api_excludes_repeats_without_a_consistent_schedule(self):
        (self.data / "demo.csv").write_text(
            "date;amount;currency;counterparty\n"
            "2025-01-01;-100;CHF;Rent\n2025-02-01;-100;CHF;Rent\n2025-03-01;-120;CHF;Rent\n"
            "2025-01-01;-7;CHF;RandomShop\n2025-01-02;-7;CHF;RandomShop\n2025-03-02;-7;CHF;RandomShop\n"
            "2025-01-01;-10;CHF;Patchy\n2025-02-01;-10;CHF;Patchy\n2025-03-01;-10;CHF;Patchy\n2025-05-01;-10;CHF;Patchy\n"
            "2025-01-01;-9;CHF;SameDay\n2025-02-01;-9;CHF;SameDay\n2025-02-01;-9;CHF;SameDay\n",
            encoding="utf-8")
        status, content = await self.request("/api/recurring?file=examples/demo.csv")
        self.assertEqual(status, 200)
        self.assertEqual([(r["counterparty"], r["count"], r["cadence"])
                          for r in json.loads(content)["rows"]], [("Rent", 3, "monthly")])
        status, content = await self.request("/api/transactions?file=examples/demo.csv")
        self.assertEqual(status, 200)
        self.assertEqual({r["counterparty"] for r in json.loads(content)["transactions"]},
                         {"Rent", "RandomShop", "Patchy", "SameDay"})

    async def test_recurring_income_excludes_extras_without_changing_financial_totals(self):
        (self.data / "demo.csv").write_text(
            "date;amount;currency;counterparty\n"
            "2025-01-31;5000;CHF;Example Employer\n"
            "2025-02-10;50;CHF;Example Employer\n"
            "2025-02-28;5000;CHF;Example Employer\n"
            "2025-03-31;5500;CHF;Example Employer\n", encoding="utf-8")
        status, content = await self.request("/api/recurring?file=examples/demo.csv")
        self.assertEqual(status, 200)
        row, = json.loads(content)["rows"]
        self.assertEqual((row["direction"], row["cadence"], row["count"], row["unscheduled_count"]),
                         ("in", "monthly", 3, 1))
        self.assertEqual((row["average_amount"], row["trend_percent"]), ("5166.67", "10.00"))
        status, content = await self.request("/api/data?file=examples/demo.csv")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(content)["yearly"][0]["income"], 15550)

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
