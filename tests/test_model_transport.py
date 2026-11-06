"""Exercise the real local HTTP transport, not mocked network forwarding."""
import json
import socket
import threading
import time
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from finreport import ai_rules


@contextmanager
def local_model(mode, chunks=()):
    entered, peer_closed = threading.Event(), threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            if mode == 'redirect':
                self.send_response(302)
                self.send_header('Location', 'http://remote.example/private')
                self.end_headers()
                return
            try:
                if mode != 'headers':
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/x-ndjson')
                    self.end_headers()
                    for chunk in chunks:
                        self.wfile.write(json.dumps(chunk, ensure_ascii=False).encode() + b'\n')
                        self.wfile.flush()
                entered.set()
                if mode in ('headers', 'hold'):
                    self.connection.settimeout(3)
                    if self.connection.recv(1) == b'':
                        peer_closed.set()
                elif mode == 'trickle':
                    for _ in range(30):
                        self.wfile.write(b'{"message":{"content":"x"},"done":false}\n')
                        self.wfile.flush()
                        time.sleep(0.02)
            except (BrokenPipeError, ConnectionResetError):
                peer_closed.set()
            except socket.timeout:
                pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', entered, peer_closed
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class StreamingModelTests(unittest.TestCase):
    def test_stream_assembly_preserves_unicode_and_reports_actual_progress(self):
        chunks = [{'message': {'content': 'café '}, 'done': False},
                  {'message': {'content': 'CHF'}, 'done': True, 'done_reason': 'stop'}]
        progress = []
        control = ai_rules.InferenceControl(threading.Lock(), progress=lambda phase, chars: progress.append((phase, chars)))
        with local_model('complete', chunks) as (url, _, _), ai_rules.inference_session(control):
            result = ai_rules.model_json(url, '/api/chat', {'model': 'test', 'stream': False})
        self.assertEqual(result['message']['content'], 'café CHF')
        self.assertTrue(result['done'])
        self.assertEqual([chars for phase, chars in progress if phase == 'generating'], [5, 8])

    def test_cancel_aborts_header_and_stream_waits_and_releases_the_model(self):
        for mode in ('headers', 'hold'):
            with self.subTest(mode=mode), local_model(mode, [
                {'message': {'content': '{'}, 'done': False}
            ]) as (url, entered, peer_closed):
                lock, done = threading.Lock(), threading.Event()
                control = ai_rules.InferenceControl(lock)
                outcome = []
                def request():
                    try:
                        with ai_rules.inference_session(control):
                            ai_rules.model_json(url, '/api/chat', {'model': 'test'}, timeout=10)
                    except Exception as exc:
                        outcome.append(exc)
                    finally:
                        done.set()
                thread = threading.Thread(target=request, daemon=True)
                thread.start()
                self.assertTrue(entered.wait(2))
                control.cancel()
                self.assertTrue(done.wait(2), 'Cancelled model request kept running')
                thread.join()
                self.assertIsInstance(outcome[0], InterruptedError)
                self.assertTrue(peer_closed.wait(2), 'The server did not observe client disconnect')
                self.assertTrue(lock.acquire(blocking=False))
                lock.release()

    def test_trickled_chunks_cannot_extend_the_overall_deadline(self):
        with local_model('trickle') as (url, _, _):
            with self.assertRaises(ai_rules.ModelTimeout):
                ai_rules.model_json(url, '/api/chat', {'model': 'test'}, timeout=0.1)

    def test_truncated_oversized_and_redirected_generations_never_become_results(self):
        cases = [
            ('complete', [{'message': {'content': '{}'}, 'done': False}]),
            ('complete', [{'message': {'content': 'x' * 1_000_001}, 'done': True}]),
            ('redirect', []),
        ]
        for mode, chunks in cases:
            with self.subTest(mode=mode, oversized=bool(chunks and len(chunks[0]['message']['content']) > 100)), local_model(mode, chunks) as (url, _, _):
                with self.assertRaises(ai_rules.ModelError):
                    ai_rules.model_json(url, '/api/chat', {'model': 'test'})

    def test_deterministic_lane_defers_readiness_instead_of_contacting_a_model(self):
        control = ai_rules.InferenceControl(threading.Lock(), allow_ai=False)
        with ai_rules.inference_session(control), self.assertRaises(ai_rules.InferenceRequired):
            ai_rules.model_json('http://127.0.0.1:9', '/api/tags')


if __name__ == '__main__':
    unittest.main()
