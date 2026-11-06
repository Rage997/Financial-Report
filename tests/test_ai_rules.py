"""Consumer-visible boundaries for local AI suggestions and approved rules."""
import json
import socket
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from unittest.mock import patch
from fastapi import HTTPException
from starlette.requests import Request

import app
from finreport import ai_rules, networth
from finreport.classifier import classify_all
from finreport.models import Category, Direction, Transaction
from finreport.parser import load_transactions


class RuleApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / "rules.json"
        self.base.write_text(json.dumps({"rules": [
            {"category": "investment", "label": "Broker", "patterns": ["known broker"]}
        ]}))
        self.approved = self.root / "private" / "ai" / "demo" / "rules.json"
        self.dataset = self.root / "statement.csv"
        self.dataset.write_text("date;amount;counterparty\n"
                                "2024-01-01;-100;Known Broker\n"
                                "2024-01-02;-40;Example Shop\n"
                                "2024-01-03;10;Example Shop\n"
                                "2024-01-04;300;Unknown Sender\n")
        self.approval = {"counterparty": "Example Shop", "category": "expense", "label": "Shopping"}

    def current_revision(self):
        return ai_rules.revision(self.base, self.approved, self.dataset)

    def apply(self, approvals, revision=None):
        return ai_rules.apply_rules(self.base, self.approved, self.dataset,
                                    revision or self.current_revision(), approvals)

    def test_approval_preserves_base_rules_and_refund_accounting(self):
        original = self.base.read_bytes()
        self.assertEqual(self.apply([self.approval]), 1)
        rules = ai_rules.effective_rules(self.base, self.approved)
        txns = load_transactions(self.dataset)
        classify_all(txns, rules)
        self.assertEqual([t.category for t in txns], [Category.INVESTMENT, Category.EXPENSE,
                                                    Category.EXPENSE, Category.UNCATEGORIZED])
        self.assertEqual(networth.compute_series(txns)[2].networth, Decimal("-30"))
        self.assertEqual(self.base.read_bytes(), original)
        self.assertEqual([row["counterparty"] for row in ai_rules.candidates(txns, rules)], ["Unknown Sender"])
        # Explicit manual rules always win over conflicting learned rules.
        self.base.write_text(json.dumps({"rules": [{"category": "transfer", "label": "Own account",
                                                   "patterns": ["example shop"]}]}))
        classify_all(txns, ai_rules.effective_rules(self.base, self.approved))
        self.assertEqual(txns[1].category, Category.TRANSFER)

    def test_stale_base_data_or_overlay_never_overwrites_current_rules(self):
        for target in (self.base, self.dataset, self.approved):
            with self.subTest(target=target.name):
                revision = self.current_revision()
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(target.read_bytes() + b"\n" if target.exists() else b'{"rules": []}')
                before = self.approved.read_bytes() if self.approved.exists() else None
                with self.assertRaises(ai_rules.StaleRules):
                    self.apply([self.approval], revision)
                self.assertEqual(self.approved.read_bytes() if self.approved.exists() else None, before)

    def test_invalid_batch_is_atomic_and_cannot_replace_existing_rules(self):
        for bad in (
            {"counterparty": "Unknown Sender", "category": "uncategorized", "label": "Unsure"},
            {"counterparty": "Known Broker", "category": "expense", "label": "Wrong"},
            {"counterparty": "Unknown Sender", "category": "income", "label": " "},
            self.approval,
        ):
            with self.subTest(rule=bad), self.assertRaises((ValueError, ai_rules.StaleRules)):
                self.apply([self.approval, bad])
            self.assertFalse(self.approved.exists())

    def test_full_name_pattern_cannot_catch_another_unruled_counterparty(self):
        with self.dataset.open("a") as stream:
            stream.write("2024-01-05;-20;Example Shop Insurance\n")
        with self.assertRaisesRegex(ValueError, "another unruled counterparty"):
            self.apply([self.approval])
        self.assertFalse(self.approved.exists())

    def test_type_fallback_remains_a_candidate_but_explicit_rules_do_not(self):
        card = Transaction("1", date(2024, 1, 1), Decimal("20"), Direction.OUT,
                           "New Merchant", payment_type="card")
        rows = ai_rules.candidates([card], [])
        self.assertEqual(rows[0]["current_category"], "expense")
        self.assertEqual(rows[0]["payment_types"], ["card"])

    def test_approved_rules_are_isolated_by_profile(self):
        self.apply([self.approval])
        with patch.object(app, "ROOT", self.root), patch.object(app, "PROFILE", "private"):
            rules = ai_rules.effective_rules(self.base, app._approved_rules_path())
            txns = load_transactions(self.dataset)
            classify_all(txns, rules)
            self.assertEqual(txns[1].category, Category.UNCATEGORIZED)


class ModelBoundaryTests(unittest.TestCase):
    def test_slow_worker_is_a_timeout_not_a_connection_failure(self):
        release = Event()

        class SlowWorker(BaseHTTPRequestHandler):
            def do_GET(self):
                release.wait()

        with ThreadingHTTPServer(("127.0.0.1", 0), SlowWorker) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with self.assertRaises(ai_rules.ModelTimeout):
                    ai_rules.model_json(f"http://127.0.0.1:{server.server_port}",
                                        "/api/tags", timeout=0.1)
            finally:
                release.set()
                server.shutdown()
                thread.join()

    def test_disconnected_worker_is_not_reported_as_slow_inference(self):
        class DisconnectedWorker(BaseHTTPRequestHandler):
            def do_GET(self):
                self.connection.shutdown(socket.SHUT_RDWR)

        with ThreadingHTTPServer(("127.0.0.1", 0), DisconnectedWorker) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with self.assertRaises(ai_rules.ModelError) as raised:
                    ai_rules.model_json(f"http://127.0.0.1:{server.server_port}", "/api/tags")
                self.assertNotIsInstance(raised.exception, ai_rules.ModelTimeout)
            finally:
                server.shutdown()
                thread.join()

    def test_model_cannot_invent_targets_or_patterns(self):
        selected = [{"counterparty": "Example Shop", "count": 2},
                    {"counterparty": "Unknown Sender", "count": 1}]
        good = {"id": 0, "category": "expense", "label": "Shopping", "reason": "Merchant purchase"}
        other = {"id": 1, "category": "uncategorized", "label": "Unknown", "reason": "Purpose unknown"}
        for rows in ([good], [good, good], [good, {**other, "id": 2}],
                     [good, {**other, "category": "salary"}],
                     [good, {**other, "patterns": [""]}],
                     [good, {**other, "label": ""}]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                ai_rules.validate_suggestions({"suggestions": rows}, selected)
        result = ai_rules.validate_suggestions({"suggestions": [other, good]}, selected)
        self.assertEqual([(row["pattern"], row["category"]) for row in result],
                         [("example shop", "expense"), ("unknown sender", "uncategorized")])

    def test_model_endpoints_cannot_be_remote(self):
        for url in ("https://ollama.com", "http://remote.example:11434", "http://localhost@remote.example",
                    "http://localhost:11434/proxy", "http://localhost:11434?remote=1"):
            with self.subTest(url=url), self.assertRaises(ai_rules.ModelError):
                ai_rules.local_url(url)

    def test_browser_requests_cannot_cross_origins_or_use_rebound_hosts(self):
        for host, origin, content_type in (
            ("127.0.0.1:8010", "https://external.example", "application/json"),
            ("external.example", "http://external.example", "application/json"),
            ("127.0.0.1:8010", "http://127.0.0.1:8010", "text/plain"),
        ):
            request = Request({"type": "http", "method": "POST", "path": "/api/ai/apply",
                               "scheme": "http", "server": ("127.0.0.1", 8010),
                               "query_string": b"", "headers": [
                                   (b"host", host.encode()), (b"origin", origin.encode()),
                                   (b"content-type", content_type.encode())]})
            with self.subTest(host=host, origin=origin), self.assertRaises(HTTPException):
                app._same_origin(request)


if __name__ == "__main__":
    unittest.main()
