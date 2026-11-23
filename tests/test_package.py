import io
import json
from pathlib import Path
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import benchmark
from scripts import charts


class Server(BaseHTTPRequestHandler):
    requests = []
    lock = threading.Lock()

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        with self.lock:
            self.requests.append((body, self.headers.get('Authorization')))
        if body['model'] == 'http-error':
            self.send_response(429)
            self.end_headers()
            self.wfile.write(b'private-error-content')
            return
        if body['model'] == 'redirect':
            self.send_response(307)
            self.send_header('Location', '/destination')
            self.end_headers()
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        if body['model'] == 'malformed':
            self.wfile.write(b'data: not-json\n\n')
            return
        frames = [{'choices': [{'delta': {'role': 'assistant'}}]}, {'choices': [{'delta': {'reasoning_content': 'thinking'}}]}, {'choices': [{'delta': {'content': 'answer'}, 'finish_reason': 'stop'}]}]
        if body['model'] != 'no-usage':
            frames.append({'choices': [], 'usage': {'prompt_tokens': 10, 'completion_tokens': 4}})
        for frame in frames:
            self.wfile.write(('data: ' + json.dumps(frame) + '\n\n').encode())
        if body['model'] != 'truncated':
            self.wfile.write(b'data: [DONE]\n\n')
        self.wfile.flush()


class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Server)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}/v1/chat/completions'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def sample(self, model):
        return benchmark.one_request(self.url, model, 'offline-test-key', 20, 8, 3)

    def test_usage_and_reasoning(self):
        sample = self.sample('test')
        self.assertTrue(sample['ok'])
        self.assertEqual(sample['completion_tokens'], 4)
        self.assertEqual(sample['prompt_tokens'], 10)
        self.assertEqual(sample['content_chunks'], 2)
        self.assertIsNotNone(sample['ttft_ms'])
        self.assertIsNotNone(sample['tpot_estimate_ms'])

    def test_no_usage_is_null_not_chunks(self):
        sample = self.sample('no-usage')
        self.assertTrue(sample['ok'])
        self.assertIsNone(sample['completion_tokens'])
        self.assertIsNone(sample['tpot_estimate_ms'])

    def test_errors_are_redacted(self):
        for model in ['http-error', 'malformed', 'truncated', 'redirect']:
            sample = self.sample(model)
            self.assertFalse(sample['ok'])
            self.assertNotIn('private-error-content', json.dumps(sample))
            self.assertNotIn('offline-test-key', json.dumps(sample))
            self.assertIsNotNone(sample['error'])

    def test_level_warmup_and_unique_prompts(self):
        args = SimpleNamespace(url=self.url, model='test', input_words=20, max_tokens=8, timeout=3, requests=6, warmup_requests=2)
        with Server.lock:
            Server.requests.clear()
        result = benchmark.run_level(args, 2, 'offline-test-key')
        self.assertEqual(result['successful_requests'], 6)
        self.assertEqual(result['completion_tokens'], 24)
        self.assertEqual(result['prompt_tokens'], 60)
        self.assertEqual(result['errors'], 0)
        with Server.lock:
            calls = list(Server.requests)
        self.assertEqual(len(calls), 8)
        self.assertEqual(len(set(b['messages'][0]['content'] for b, _ in calls)), 8)
        self.assertTrue(all(auth == 'Bearer offline-test-key' for _, auth in calls))
        self.assertGreater(result['output_tok_s'], 0)

    def test_missing_aggregate_usage(self):
        args = SimpleNamespace(url=self.url, model='no-usage', input_words=20, max_tokens=8, timeout=3, requests=2, warmup_requests=0)
        result = benchmark.run_level(args, 1, 'offline-test-key')
        self.assertIsNone(result['output_tok_s'])
        self.assertIsNone(result['total_tok_s'])
        self.assertEqual(result['usage_completion_requests'], 0)

    def test_sse_comments_multiline(self):
        data = io.BytesIO(b': heartbeat\r\n\r\ndata: {"choices":\r\ndata: []}\r\n\r\ndata: [DONE]\n\n')
        frames = list(benchmark.events(data))
        self.assertEqual(json.loads(frames[0]), {'choices': []})
        self.assertEqual(frames[1], '[DONE]')

    def test_token_validation(self):
        for value in [None, True, -1, 2.5, '4']:
            self.assertIsNone(benchmark.token_count({'completion_tokens': value}, 'completion_tokens'))
        self.assertEqual(benchmark.token_count({'completion_tokens': 0}, 'completion_tokens'), 0)


class ChartTests(unittest.TestCase):
    GROUP = {'model': 'm', 'config': None, 'engine': 'vLLM', 'engine_version': '0.0.1', 'gpu': 'GPU X', 'gpu_count': '1', 'area': 'single-node-sweeps', 'workload': 'w', 'precision': None, 'input_tokens': None, 'output_tokens': None, 'date': '2026-11-01'}

    def test_chart_eligibility(self):
        self.assertTrue(charts.eligible({'concurrency': '4', 'output_tok_s': '99'}))
        for value in [None, 'nan', 'inf', '0', '-1', '1.5']:
            self.assertFalse(charts.eligible({'concurrency': value, 'output_tok_s': '9'}))
        self.assertFalse(charts.eligible({'concurrency': '4', 'output_tok_s': None}))

    def test_svg_is_self_contained(self):
        import xml.etree.ElementTree as ET
        out = charts.svg(self.GROUP, [{'row_id': 1, 'concurrency': '1', 'output_tok_s': '10'}, {'row_id': 2, 'concurrency': '8', 'output_tok_s': '70'}])
        root = ET.fromstring(out)
        self.assertTrue(root.tag.endswith('svg'))
        self.assertNotIn('<script', out)
        self.assertIn('row 2: concurrency 8', out)

    def test_heading(self):
        self.assertEqual(charts.heading(self.GROUP), '**m** on **1x GPU X**, vLLM (0.0.1), 2026-11-01')


if __name__ == '__main__':
    unittest.main()
