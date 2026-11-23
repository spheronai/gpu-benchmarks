"""Dependency-free closed-loop OpenAI-compatible SSE benchmark (Python 3.9+)."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import random
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def events(response):
    """Decode SSE data frames, including multi-line data and comments."""
    data = []
    for raw in response:
        line = raw.decode('utf-8').rstrip('\r\n')
        if not line:
            if data:
                yield '\n'.join(data)
                data = []
        elif line.startswith('data:'):
            data.append(line[5:].lstrip(' '))
    if data:
        yield '\n'.join(data)


def prompt(words):
    rng = random.Random()
    filler = 'computing history GPUs transformers memory scheduling inference latency throughput science systems'.split()
    text = ' '.join(rng.choice(filler) for _ in range(words))
    return f'Request {uuid.uuid4()}.\n{text}\nWrite a detailed explanation of transformer inference.'


def token_count(usage, key):
    value = usage.get(key) if isinstance(usage, dict) else None
    return value if type(value) is int and value >= 0 else None


def one_request(url, model, key, input_words, max_tokens, timeout):
    sample = {'ok': False, 'error': None, 'ttft_ms': None, 'elapsed_s': None, 'prompt_tokens': None, 'completion_tokens': None, 'tpot_estimate_ms': None, 'content_chunks': 0}
    start = time.perf_counter()
    try:
        body = {'model': model, 'messages': [{'role': 'user', 'content': prompt(input_words)}], 'stream': True, 'stream_options': {'include_usage': True}, 'max_tokens': max_tokens, 'temperature': 0}
        request = urllib.request.Request(url, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key, 'Accept': 'text/event-stream'})
        opener = urllib.request.build_opener(NoRedirect)
        first, done, finished = None, False, False
        with opener.open(request, timeout=timeout) as response:
            if 'text/event-stream' not in response.headers.get('Content-Type', '').lower():
                raise ValueError('not_sse')
            for event in events(response):
                if event.strip() == '[DONE]':
                    done = True
                    break
                chunk = json.loads(event)
                if chunk.get('error'):
                    raise ValueError('api_error')
                usage = chunk.get('usage')
                if usage is not None:
                    sample['prompt_tokens'] = token_count(usage, 'prompt_tokens')
                    sample['completion_tokens'] = token_count(usage, 'completion_tokens')
                for choice in chunk.get('choices') or []:
                    delta = choice.get('delta') or {}
                    if any(delta.get(k) for k in ['content', 'reasoning_content', 'reasoning', 'tool_calls']):
                        now = time.perf_counter()
                        if first is None:
                            first = now
                            sample['ttft_ms'] = (now - start) * 1000
                        sample['content_chunks'] += 1
                    if choice.get('finish_reason') is not None:
                        finished = True
        if not done or not finished:
            raise ValueError('incomplete_stream')
        sample['ok'] = True
    except urllib.error.HTTPError as exc:
        sample['error'] = f'http_{exc.code}'
    except Exception as exc:
        # Never persist response bodies, exception messages, URLs, prompts or keys.
        sample['error'] = 'stream_or_transport_' + type(exc).__name__
    sample['elapsed_s'] = time.perf_counter() - start
    tokens = sample['completion_tokens']
    if sample['ok'] and tokens is not None and tokens > 1 and sample['ttft_ms'] is not None:
        sample['tpot_estimate_ms'] = max(0, sample['elapsed_s'] * 1000 - sample['ttft_ms']) / (tokens - 1)
    return sample


def percentile(values, p):
    if not values:
        return None
    values = sorted(values)
    return values[round((len(values) - 1) * p)]


def run_level(args, concurrency, key):
    request = lambda: one_request(args.url, args.model, key, args.input_words, args.max_tokens, args.timeout)
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        warmup = list(pool.map(lambda _: request(), range(args.warmup_requests)))
        start = time.perf_counter()
        samples = list(pool.map(lambda _: request(), range(args.requests)))
        wall = time.perf_counter() - start
    ok = [s for s in samples if s['ok']]
    completion_complete = bool(ok) and all(s['completion_tokens'] is not None for s in ok)
    prompt_complete = bool(ok) and all(s['prompt_tokens'] is not None for s in ok)
    output = sum(s['completion_tokens'] for s in ok) if completion_complete else None
    inputs = sum(s['prompt_tokens'] for s in ok) if prompt_complete else None
    ttft = [s['ttft_ms'] for s in ok if s['ttft_ms'] is not None]
    tpots = [s['tpot_estimate_ms'] for s in ok if s['tpot_estimate_ms'] is not None]
    return {'concurrency': concurrency, 'attempted_requests': len(samples), 'successful_requests': len(ok), 'errors': len(samples) - len(ok), 'warmup_errors': sum(not s['ok'] for s in warmup), 'wall_s': wall, 'usage_completion_requests': sum(s['completion_tokens'] is not None for s in ok), 'usage_prompt_requests': sum(s['prompt_tokens'] is not None for s in ok), 'completion_tokens': output, 'prompt_tokens': inputs, 'output_tok_s': output / wall if output is not None else None, 'total_tok_s': (output + inputs) / wall if output is not None and inputs is not None else None, 'req_s': len(ok) / wall, 'ttft_samples': len(ttft), 'ttft_p50_ms': percentile(ttft, .5), 'ttft_p95_ms': percentile(ttft, .95), 'tpot_estimate_p50_ms': statistics.median(tpots) if tpots else None, 'samples': samples}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True, help='Full /v1/chat/completions URL of an existing endpoint')
    parser.add_argument('--model', required=True)
    parser.add_argument('--concurrency', default='1,4,8')
    parser.add_argument('--requests', type=int, default=32, help='Measured request count per level')
    parser.add_argument('--warmup-requests', type=int, default=4, help='Excluded requests before EACH level')
    parser.add_argument('--input-words', type=int, default=200)
    parser.add_argument('--max-tokens', type=int, default=256)
    parser.add_argument('--timeout', type=float, default=120, help='Socket operation timeout, not absolute request deadline')
    parser.add_argument('--output', type=Path, required=True, help='NEW JSON path; exclusive creation')
    args = parser.parse_args()
    try:
        levels = [int(c) for c in args.concurrency.split(',')]
    except ValueError:
        parser.error('Concurrency must be comma-separated integers')
    if not levels or any(c < 1 for c in levels) or len(set(levels)) != len(levels):
        parser.error('Concurrency levels must be distinct positive integers')
    if args.requests < max(levels) or args.warmup_requests < 0 or args.input_words < 1 or args.max_tokens < 1 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('Requests must cover max concurrency; counts and timeout must be valid')
    url = urllib.parse.urlsplit(args.url)
    if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
        parser.error('Use an HTTP(S) endpoint URL without credentials, query or fragment')
    if url.scheme == 'http' and url.hostname not in ('localhost', '127.0.0.1', '::1'):
        parser.error('Use HTTPS for non-loopback endpoints')
    key = os.environ.get('OPENAI_API_KEY')
    if not key or '\n' in key or '\r' in key:
        parser.error('Set a valid OPENAI_API_KEY environment variable; no CLI keys accepted')
    if args.output.exists() or not args.output.parent.is_dir():
        parser.error('Output must be a new file inside an existing directory')
    results = [run_level(args, level, key) for level in levels]
    report = {'schema': 'spheron-sse-run-v1', 'created_utc': datetime.now(timezone.utc).isoformat(), 'model': args.model, 'runtime': 'python-stdlib', 'method': 'fixed-count-closed-loop', 'input_words': args.input_words, 'max_tokens': args.max_tokens, 'requests_per_level': args.requests, 'warmup_requests_per_level': args.warmup_requests, 'socket_timeout_s': args.timeout, 'temperature': 0, 'prompt_policy': 'unique UUID prefix plus random synthetic words; not tokenizer-sized', 'endpoint_recorded': False, 'levels': results}
    with args.output.open('x', encoding='utf-8') as f:
        json.dump(report, f, indent=2, allow_nan=False)
        f.write('\n')
    print(json.dumps({'levels': len(results), 'errors': sum(r['errors'] for r in results), 'usage_complete_levels': sum(r['completion_tokens'] is not None for r in results)}))
    return 1 if any(r['errors'] or r['warmup_errors'] for r in results) else 0


if __name__ == '__main__':
    raise SystemExit(main())
