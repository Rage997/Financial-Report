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
                if self.path == "/health":
                    body = json.dumps({"status": "ok"}).encode()
                else:
                    body = json.dumps({"object": "list", "data": [
                        {"id": "test-model", "object": "model", "aliases": []}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()

                def sse(payload):
                    return ("data: " + json.dumps(payload)).encode() + b"\n"

                first = {"id": "chatcmpl-test", "object": "chat.completion.chunk",
                         "choices": [{"index": 0,
                                      "delta": {"role": "assistant", "content": "{"},
                                      "finish_reason": None}]}
                self.wfile.write(sse(first))
                self.wfile.flush()
                entered.set()
                release.wait(10)
                rest = {"id": "chatcmpl-test", "object": "chat.completion.chunk",
                        "choices": [{"index": 0, "delta": {"content":
                        '"suggestions":[{"id":0,"category":"expense",'
                        '"label":"Merchant","reason":"Retail purchase"}]}'},
                        "finish_reason": None}]}
                stop = {"id": "chatcmpl-test", "object": "chat.completion.chunk",
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                try:
                    self.wfile.write(sse(rest))
                    self.wfile.write(sse(stop))
                    self.wfile.write(b"data: [DONE]\n")
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

    @contextmanager
    def model_endpoint(self, model):
        """Serve a local model endpoint that only accepts the loaded model."""
        loaded = {"model": model}
        pause, entered, release = threading.Event(), threading.Event(), threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _json(self, payload, status=200):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/health":
                    self._json({"status": "ok"})
                elif self.path == "/v1/models":
                    self._json({"object": "list", "data": [
                        {"id": loaded["model"], "object": "model", "aliases": []}]})
                else:
                    self._json({"error": "unknown endpoint"}, 404)

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if payload.get("model") != loaded["model"]:
                    self._json({"error": "model not loaded"}, 404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()

                def sse(delta, finish=None):
                    chunk = {"id": "chatcmpl-test", "object": "chat.completion.chunk",
                             "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                    self.wfile.write(("data: " + json.dumps(chunk)).encode() + b"\n")

                try:
                    sse({"role": "assistant", "content": "{"})
                    self.wfile.flush()
                    if pause.is_set():
                        pause.clear()
                        entered.set()
                        release.wait(10)
                    sse({"content": '"suggestions":[{"id":0,"category":"expense",'
                                    '"label":"Merchant","reason":"Retail purchase"}]}'})
                    sse({}, "stop")
                    self.wfile.write(b"data: [DONE]\n")
                except (BrokenPipeError, ConnectionResetError):
                    pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield {"url": f"http://127.0.0.1:{server.server_port}", "loaded": loaded,
                   "pause": pause, "entered": entered, "release": release}
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join()

    def make_model_store(self, url, model):
        store = ImportStore(self.root, "demo", self.rules, self.root / "approved.json",
                            url, model, threading.Lock(), autostart=False)
        self.addCleanup(store.close)
        return store

    def submit_to(self, store, counterparty):
        record, created = store.submit("statement.csv", (
            f"date;amount;currency;counterparty\n2025-01-02;-5.00;CHF;{counterparty}\n"
        ).encode())
        self.assertTrue(created)
        return record

    def test_pending_jobs_resume_with_reconfigured_model_after_restart(self):
        old_model = "qwen2.5:1.5b"
        new_model = "qwen2.5-1.5b-instruct-q4_k_m.gguf"
        with self.model_endpoint(old_model) as endpoint:
            store = self.make_model_store(endpoint["url"], old_model)
            store.start()
            finished = self.submit_to(store, "First Merchant")
            ready = self.wait_status(finished["id"], {"ready", "failed"})
            self.assertEqual(ready["status"], "ready", ready["error"])
            finished_folder = store.directory / finished["id"]
            finished_csv = (finished_folder / "transactions.csv").read_bytes()
            finished_rules = (finished_folder / "rules.json").read_bytes()
            # Keep the model worker busy so the next job stays durably waiting.
            endpoint["pause"].set()
            paused = self.submit_to(store, "Second Merchant")
            self.assertTrue(endpoint["entered"].wait(5), "Model request did not start")
            pending = self.submit_to(store, "Third Merchant")
            self.wait_status(pending["id"], {"waiting_ai"})
            pending_folder = store.directory / pending["id"]
            pending_csv = (pending_folder / "transactions.csv").read_bytes()
            store.close()
            self.assertEqual(self.store.get(paused["id"])["status"], "queued")
            self.assertEqual(self.store.get(pending["id"])["status"], "waiting_ai")
            # A crash while a cancellation was in flight must stay cancelled.
            cancelling = self.submit_to(store, "Fourth Merchant")
            with self.store._connect() as db:
                db.execute("UPDATE imports SET status='cancelling' WHERE id=?", (cancelling["id"],))
        # The backend now serves a different model identity for the same workspace.
        with self.model_endpoint(new_model) as endpoint:
            reopened = self.make_model_store(endpoint["url"], new_model)
            reopened.start()
            resumed = self.wait_status(pending["id"], {"ready", "failed"})
            self.assertEqual(resumed["status"], "ready", resumed["error"])
            self.assertEqual(resumed["model"], new_model)
            self.assertEqual((pending_folder / "transactions.csv").read_bytes(), pending_csv)
            metadata = json.loads((pending_folder / "normalization.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["configured_model"], old_model)
            resumed_paused = self.wait_status(paused["id"], {"ready", "failed"})
            self.assertEqual(resumed_paused["status"], "ready", resumed_paused["error"])
            self.assertEqual(resumed_paused["model"], new_model)
            finished_job = self.store.get(finished["id"])
            self.assertEqual((finished_job["status"], finished_job["model"]), ("ready", old_model))
            self.assertEqual((self.root / finished_job["file"]).read_bytes(), finished_csv)
            self.assertEqual((finished_folder / "rules.json").read_bytes(), finished_rules)
            cancelled_job = self.store.get(cancelling["id"])
            self.assertEqual((cancelled_job["status"], cancelled_job["model"]), ("cancelled", old_model))
            with self.store._connect() as db:
                models = {row[0] for row in db.execute("SELECT DISTINCT model FROM automatic_rules")}
            self.assertEqual(models, {old_model, new_model})

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
