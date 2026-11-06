"""Consumer-visible import queue, cancellation, and retained-data boundaries."""
import json
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from finreport.imports import ImportStore


class ImportLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.rules = self.root / "rules.json"
        self.rules.write_text(json.dumps({"rules": [
            {"category": "expense", "label": "Bakery", "patterns": ["example bakery"]}
        ]}))
        self.store = self.make_store()
        self.addCleanup(lambda: self.store.close())

    def make_store(self):
        return ImportStore(self.root, "demo", self.rules, self.root / "approved.json",
                           "", "test-model", threading.Lock(), autostart=False)

    def submit(self, counterparty, amount="-5.00"):
        return self.store.submit("statement.csv", (
            f"date;amount;currency;counterparty\n2025-01-02;{amount};CHF;{counterparty}\n"
        ).encode())[0]

    def wait_status(self, identifier, statuses):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = self.store.get(identifier)
            if job["status"] in statuses:
                return job
            time.sleep(0.01)
        self.fail(f"Import {identifier} did not reach {statuses}: {job}")

    @contextmanager
    def paused_model(self):
        entered, release = threading.Event(), threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                body = json.dumps({"models": [{"name": "test-model"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                first = {"message": {"content": "{"}, "done": False}
                self.wfile.write(json.dumps(first).encode() + b"\n")
                self.wfile.flush()
                entered.set()
                release.wait(10)
                rest = {'message': {'content': '"suggestions":[{"id":0,"category":"expense",'
                        '"label":"Merchant","reason":"Retail purchase"}]}'},
                        'done': True, 'done_reason': 'stop'}
                try:
                    self.wfile.write(json.dumps(rest).encode() + b"\n")
                except (BrokenPipeError, ConnectionResetError):
                    pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.store.ai_url = f"http://127.0.0.1:{server.server_port}"
        try:
            yield entered, release
        finally:
            release.set()
            self.store.close()
            server.shutdown()
            server.server_close()
            thread.join()

    def test_slow_ai_does_not_block_known_imports_and_cancellation_cannot_publish_late_results(self):
        with self.paused_model() as (entered, release):
            slow = self.submit("New Merchant")
            self.store.start()
            self.assertTrue(entered.wait(3), "Model request did not start")
            another = self.submit("Another Merchant")
            known = self.submit("Example Bakery")
            ready = self.wait_status(known["id"], {"ready", "failed"})
            self.assertEqual(ready["status"], "ready", ready["error"])
            self.assertEqual(self.store.get(slow["id"])["status"], "categorizing")
            self.wait_status(another["id"], {"waiting_ai"})
            with self.assertRaises(ValueError):
                self.store.delete(slow["id"])
            self.store.cancel(another["id"])
            self.assertEqual(self.store.get(another["id"])["status"], "cancelled")
            # Another API process has no in-memory control for this job.
            observer = self.make_store()
            self.addCleanup(observer.close)
            self.assertEqual(observer.cancel(slow["id"])["status"], "cancelling")
            cancelled = self.wait_status(slow["id"], {"cancelled"})
            self.assertIsNone(cancelled["file"])
            release.set()
            self.store.close()
            self.assertEqual(self.store.get(slow["id"])["status"], "cancelled")
            with self.store._connect() as db:
                self.assertEqual(db.execute("SELECT count(*) FROM automatic_rules").fetchone()[0], 0)
            self.assertEqual(self.store.ready_files(), [self.root / ready["file"]])

    def test_cancelled_import_can_retry_without_replacing_original_or_normalization(self):
        with self.paused_model() as (entered, _):
            job = self.submit("New Merchant")
            self.store.start()
            self.assertTrue(entered.wait(3))
            folder = self.store.directory / job["id"]
            original = (folder / "source.csv").read_bytes()
            normalized = (folder / "transactions.csv").read_bytes()
            checkpoint_time = (folder / "normalization.json").stat().st_mtime_ns
            self.store.cancel(job["id"])
            self.wait_status(job["id"], {"cancelled"})
            self.rules.write_text(json.dumps({"rules": [
                {"category": "expense", "label": "Merchant", "patterns": ["new merchant"]}
            ]}))
            self.store.retry(job["id"])
            saved = self.wait_status(job["id"], {"ready", "failed"})
            self.assertEqual(saved["status"], "ready", saved["error"])
            self.assertEqual((folder / "source.csv").read_bytes(), original)
            self.assertEqual((folder / "transactions.csv").read_bytes(), normalized)
            self.assertEqual((folder / "normalization.json").stat().st_mtime_ns, checkpoint_time)

    def test_dismissal_survives_restart_and_delete_clears_only_owned_data_and_selection(self):
        first = self.submit("Example Bakery")
        self.store.process(first["id"])
        second = self.submit("Example Bakery", "-7.00")
        self.store.process(second["id"])
        first = self.store.get(first["id"])
        second = self.store.get(second["id"])
        self.assertEqual(self.store.active_file(), first["file"])
        self.store.dismiss(first["id"], True)
        self.assertIn(self.root / first["file"], self.store.ready_files())
        self.store.close()
        self.store = self.make_store()
        self.assertTrue(self.store.get(first["id"])["dismissed"])
        self.assertFalse(self.store.dismiss(first["id"], False)["dismissed"])
        self.store.delete(first["id"])
        self.assertIsNone(self.store.active_file())
        self.assertFalse((self.store.directory / first["id"]).exists())
        self.assertEqual(self.store.ready_files(), [self.root / second["file"]])
        with self.assertRaises(KeyError):
            self.store.asset(first["id"], "source")
        imported_again = self.submit("Example Bakery")
        self.assertNotEqual(imported_again["id"], first["id"])

    def test_existing_workspace_upgrade_preserves_saved_files_and_selection(self):
        job = self.submit("Example Bakery")
        self.store.process(job["id"])
        saved = self.store.get(job["id"])
        content = (self.root / saved["file"]).read_bytes()
        self.store.close()
        # Recreate the pre-upgrade schema around a real saved import.
        with self.store._connect() as db:
            for name in ("dismissed", "started_at", "finished_at", "stage_started_at", "ai_phase", "ai_chars"):
                db.execute(f"ALTER TABLE imports DROP COLUMN {name}")
        self.store = self.make_store()
        upgraded = self.store.get(job["id"])
        self.assertEqual((upgraded["status"], upgraded["file"], upgraded["count"]),
                         ("ready", saved["file"], 1))
        self.assertEqual((self.root / upgraded["file"]).read_bytes(), content)
        self.assertEqual(self.store.active_file(), saved["file"])
        self.assertFalse(upgraded["dismissed"])
        self.store.dismiss(job["id"], True)
        self.assertTrue(self.store.get(job["id"])["dismissed"])

    def test_queued_cancel_and_cross_profile_mutations_cannot_reach_other_sources(self):
        job = self.submit("Example Bakery")
        other = ImportStore(self.root, "private", self.rules, self.root / "other-approved.json",
                            "", "test-model", threading.Lock(), autostart=False)
        self.addCleanup(other.close)
        for action in (other.cancel, other.delete, lambda identifier: other.dismiss(identifier, True)):
            with self.assertRaises(KeyError):
                action(job["id"])
        self.assertEqual(self.store.cancel(job["id"])["status"], "cancelled")
        self.store.start()
        self.store.close()
        self.assertEqual(self.store.get(job["id"])["status"], "cancelled")
        self.assertEqual(self.store.ready_files(), [])


if __name__ == "__main__":
    unittest.main()
