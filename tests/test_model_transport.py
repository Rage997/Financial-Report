"""Exercise the real local HTTP transport, not mocked network forwarding."""
import json
import socket
import threading
import time
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from finreport import ai_rules


def delta_chunk(content=None, finish=None, role_only=False, **extra):
    if role_only:
        delta = {"role": "assistant", "content": None}
    elif content is not None:
        delta = {"content": content}
    else:
        delta = {}
    choice = {"index": 0, "delta": delta}
    if finish is not None:
        choice["finish_reason"] = finish
    payload = {"id": "chatcmpl-test", "object": "chat.completion.chunk",
               "created": 1, "model": "test.gguf", "choices": [choice]}
    payload.update(extra)
    return "data: " + json.dumps(payload, ensure_ascii=False)


@contextmanager
def local_model(mode, events=(), models=(("test.gguf", ()),)):
    entered, peer_closed = threading.Event(), threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _json(self, status_code, payload):
            body = json.dumps(payload).encode()
            self.send_response(status_code)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == '/health':
                if mode == 'warming':
                    self._json(503, {"error": {"code": 503, "message": "Loading model",
                                               "type": "unavailable_error"}})
                else:
                    self._json(200, {"status": "ok"})
                return
            if self.path == '/v1/models':
                self._json(200, {"object": "list", "data": [
                    {"id": name, "object": "model", "aliases": list(aliases)}
                    for name, aliases in models]})
                return
            self._json(404, {"error": {"message": "not found"}})

        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            if mode == 'redirect':
                self.send_response(302)
                self.send_header('Location', 'http://remote.example/private')
                self.end_headers()
                return
            if mode == 'error':
                self._json(500, {"error": {"code": 500, "message": "slot busy",
                                           "type": "server_error"}})
                return
            try:
                if mode != 'headers':
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.end_headers()
                    for line in events:
                        self.wfile.write(line.encode() + b'\n')
                        self.wfile.flush()
                entered.set()
                if mode in ('headers', 'hold'):
                    self.connection.settimeout(3)
                    if self.connection.recv(1) == b'':
                        peer_closed.set()
                elif mode == 'trickle':
                    for _ in range(30):
                        self.wfile.write((delta_chunk('x') + '\n').encode())
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
        events = [
            delta_chunk(role_only=True),
            ': ping',
            delta_chunk('café '),
            '',
            delta_chunk('CHF'),
            delta_chunk(finish='stop'),
            'data: ' + json.dumps({"id": "chatcmpl-test", "object": "chat.completion.chunk",
                                   "choices": [],
                                   "usage": {"prompt_tokens": 10, "completion_tokens": 8,
                                             "total_tokens": 18}}),
            'data: [DONE]',
        ]
        progress = []
        control = ai_rules.InferenceControl(threading.Lock(), progress=lambda phase, chars: progress.append((phase, chars)))
        with local_model('complete', events) as (url, _, _), ai_rules.inference_session(control):
            result = ai_rules.model_json(url, '/v1/chat/completions', {'model': 'test.gguf'})
        choice = result['choices'][0]
        self.assertEqual(result['object'], 'chat.completion')
        self.assertEqual(choice['message']['content'], 'café CHF')
        self.assertEqual(choice['finish_reason'], 'stop')
        self.assertEqual(result['usage']['total_tokens'], 18)
        self.assertEqual([chars for phase, chars in progress if phase == 'generating'], [5, 8])

    def test_cancel_aborts_header_and_stream_waits_and_releases_the_model(self):
        for mode in ('headers', 'hold'):
            with self.subTest(mode=mode), local_model(mode, [delta_chunk(role_only=True)]) as (url, entered, peer_closed):
                lock, done = threading.Lock(), threading.Event()
                control = ai_rules.InferenceControl(lock)
                outcome = []
                def request():
                    try:
                        with ai_rules.inference_session(control):
                            ai_rules.model_json(url, '/v1/chat/completions', {'model': 'test.gguf'}, timeout=10)
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
                ai_rules.model_json(url, '/v1/chat/completions', {'model': 'test.gguf'}, timeout=0.1)

    def test_non_stop_finishes_are_not_success(self):
        cases = [
            ('no stop event before the stream ends', [delta_chunk('partial')]),
            ('length finish', [delta_chunk('partial'), delta_chunk(finish='length'), 'data: [DONE]']),
        ]
        for label, events in cases:
            with self.subTest(label=label), local_model('complete', events) as (url, _, _):
                with self.assertRaises(ai_rules.ModelTruncated):
                    ai_rules.model_json(url, '/v1/chat/completions', {'model': 'test.gguf'})
        with local_model('complete', [delta_chunk(finish='tool_calls'), 'data: [DONE]']) as (url, _, _):
            with self.assertRaises(ai_rules.ModelError) as raised:
                ai_rules.model_json(url, '/v1/chat/completions', {'model': 'test.gguf'})
        self.assertNotIsInstance(raised.exception, ai_rules.ModelTruncated)
        self.assertNotIsInstance(raised.exception, ai_rules.ModelTimeout)

    def test_protocol_errors_never_become_results(self):
        cases = [
            ('malformed data line', 'complete', ['data: not-json']),
            ('oversized stream', 'complete', [delta_chunk('x' * 1_000_001)]),
            ('redirect', 'redirect', []),
            ('server error body', 'error', []),
        ]
        for label, mode, events in cases:
            with self.subTest(label=label), local_model(mode, events) as (url, _, _):
                with self.assertRaises(ai_rules.ModelError) as raised:
                    ai_rules.model_json(url, '/v1/chat/completions', {'model': 'test.gguf'})
            self.assertNotIsInstance(raised.exception, ai_rules.ModelTimeout)

    def test_deterministic_lane_defers_instead_of_contacting_a_model(self):
        control = ai_rules.InferenceControl(threading.Lock(), allow_ai=False)
        with ai_rules.inference_session(control), self.assertRaises(ai_rules.InferenceRequired):
            ai_rules.model_json('http://127.0.0.1:9', '/v1/chat/completions', {'model': 'test.gguf'})


class ReadinessTests(unittest.TestCase):
    def test_warming_server_is_unavailable_not_an_error(self):
        with local_model('warming') as (url, _, _):
            result = ai_rules.status(url, 'test.gguf')
        self.assertFalse(result['available'])

    def test_exact_alias_match_is_available(self):
        with local_model('complete', models=(('test.gguf', ()),)) as (url, _, _):
            result = ai_rules.status(url, 'test.gguf')
        self.assertTrue(result['available'])

    def test_alias_list_match_is_available(self):
        with local_model('complete', models=(('qwen2.5-1.5b-instruct-q4_k_m.gguf', ('test.gguf',)),)) as (url, _, _):
            result = ai_rules.status(url, 'test.gguf')
        self.assertTrue(result['available'])

    def test_other_alias_is_not_available(self):
        with local_model('complete', models=(('other.gguf', ()),)) as (url, _, _):
            result = ai_rules.status(url, 'test.gguf')
        self.assertFalse(result['available'])


if __name__ == '__main__':
    unittest.main()
