"""Schema of the published catalog and the chart renderer shared by the validator."""
import html
import math

COLUMNS = 'model config date area params_b precision engine engine_version gpu gpu_count workload input_tokens output_tokens concurrency output_tok_s total_tok_s req_s ttft_p50_ms ttft_p95_ms tpot_p50_ms itl_p50_ms'.split()
METRICS = COLUMNS[14:]
FIELDS = ['row_id'] + COLUMNS
GROUP = 'model config engine engine_version gpu gpu_count area workload precision input_tokens output_tokens date'.split()
# A published row must record all of these: one model on one GPU type and engine, at a known load.
REQUIRED = ['model', 'date', 'area', 'engine', 'gpu', 'gpu_count', 'concurrency', 'output_tok_s']
# Overloaded levels are not steady-state measurements: requests were still queued when timing ended.
MAX_TTFT_P50_MS = 60000
AREA_TITLES = {
    'dedicated-endpoints': 'Spheron Dedicated Endpoints',
    'multi-gpu-tuning': 'Multi-GPU tuning on B200 and B300',
    'large-model-validation': 'Large-model validation on B300',
    'single-node-sweeps': 'Single-node sweeps',
}
AREA_NOTES = {
    'dedicated-endpoints': 'November 2026 runs on Spheron Dedicated Endpoints: one GPU, vLLM or SGLang as installed by default.',
    'multi-gpu-tuning': 'October 2026 tuning sweeps on B200 and B300, one to eight GPUs: tensor, data and expert parallelism, speculative decoding, KV cache and context options.',
    'large-model-validation': 'October 2026 validation sweeps of large mixture-of-experts models on 4x B300.',
    'single-node-sweeps': 'November 2026 `vllm bench serve` sweeps and single-request image tests on one node.',
}
AREA_ORDER = list(AREA_TITLES)
RELEASE = '2026.11.23'

def number(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def eligible(row):
    c, t = number(row['concurrency']), number(row['output_tok_s'])
    return c is not None and c > 0 and c.is_integer() and t is not None and t >= 0


def heading(group):
    gpu = group['gpu'] or 'GPU not recorded'
    count = group['gpu_count']
    if count and not gpu.lower().startswith(f'{count}x'):
        gpu = f'{count}x {gpu}'
    engine = group['engine'] or 'engine not recorded'
    version = group['engine_version'] or 'version not recorded'
    return f'**{group["model"]}** on **{gpu}**, {engine} ({version}), {group["date"] or "date not recorded"}'.replace('|', '/')


def svg(group, points):
    import textwrap
    esc = html.escape
    label = ' | '.join(f'{key}: {group[key] if group[key] is not None else "not recorded"}' for key in GROUP)
    lines = textwrap.wrap(label, 86, break_long_words=True)
    top = 140 + len(lines) * 20
    bottom = top + 270
    height = bottom + 110
    levels = sorted(set(float(p['concurrency']) for p in points))
    min_x, max_x = levels[0], levels[-1]
    log_axis = max_x / min_x >= 8
    axis_label = 'CONCURRENT REQUESTS (LOG SCALE)' if log_axis else 'CONCURRENT REQUESTS'
    if log_axis:
        span = math.log2(max_x) - math.log2(min_x)
        xpos = lambda c: 130 + 30 + (math.log2(c) - math.log2(min_x)) / span * 750
    else:
        xpos = lambda c: 130 + c / max_x * 780
    max_y = max(1, max(float(p['output_tok_s']) for p in points))
    desc = label + '. Each dot is one recorded run. The numeric table with row references is in docs/RESULTS.md.'
    elements = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 {height}" role="img" aria-labelledby="title desc"><title id="title">Output throughput versus concurrency</title><desc id="desc">{esc(desc)}</desc>', '<defs><linearGradient id="shine"><stop stop-color="#ffffff"/><stop offset="45%" stop-color="#8fd8ff"/><stop offset="100%" stop-color="#2f7bff"/></linearGradient></defs>', f'<rect width="1000" height="{height}" fill="#000000"/>', f'<rect x="20" y="20" width="960" height="{height-40}" fill="#050608" stroke="#ffffff" stroke-opacity="0.08"/>', f'<path d="M30 20H20V30 M970 20H980V30 M20 {height-30}V{height-20}H30 M970 {height-20}H980V{height-30}" fill="none" stroke="#ffffff" stroke-opacity="0.27"/>', '<g font-family="SF Pro Display, SF Pro Text, Inter, Arial, sans-serif" font-size="15" font-weight="400" fill="#ffffff">', '<text x="64" y="52" font-family="OCR A Std, Share Tech Mono, monospace" font-size="11" letter-spacing="0.16em" fill="#ffffff" fill-opacity="0.55">RECORDED RUNS</text>', '<path d="M300 48H936" stroke="#ffffff" stroke-opacity="0.08"/>', '<text x="64" y="94" font-size="30" letter-spacing="-0.035em">Output throughput <tspan fill="url(#shine)">versus concurrency</tspan></text>']
    for i, line in enumerate(lines):
        elements.append(f'<text x="64" y="{126+i*20}" fill-opacity="0.55">{esc(line)}</text>')
    elements.append(f'<rect x="130" y="{top}" width="806" height="270" fill="#0a0b0e"/><path d="M130 {top}V{bottom}H936" fill="none" stroke="#ffffff" stroke-opacity="0.14"/>')
    for i in range(5):
        y = bottom - i * 67.5
        elements.append(f'<path d="M130 {y}H936" stroke="#ffffff" stroke-opacity="0.08"/><text x="118" y="{y+5}" text-anchor="end" font-family="OCR A Std, Share Tech Mono, monospace">{max_y*i/4:.1f}</text>')
    for c in sorted(set(float(p['concurrency']) for p in points)):
        x = xpos(c)
        elements.append(f'<text x="{x:.2f}" y="{bottom+26}" text-anchor="middle" font-family="OCR A Std, Share Tech Mono, monospace">{c:g}</text>')
    for p in points:
        x = xpos(float(p['concurrency']))
        y = bottom - float(p['output_tok_s'])/max_y * 270
        elements.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="5" fill="#0059ff" stroke="#8fd8ff" stroke-width="1"><title>row {p["row_id"]}: concurrency {esc(p["concurrency"])}; output {esc(p["output_tok_s"])} tok/s</title></circle>')
    elements += [f'<text x="530" y="{bottom+53}" text-anchor="middle" font-family="OCR A Std, Share Tech Mono, monospace" font-size="11" letter-spacing="0.16em">{axis_label}</text>', f'<text x="49" y="{top+135}" transform="rotate(-90 49 {top+135})" text-anchor="middle">Output tokens / second</text>', f'<text x="64" y="{height-35}" fill="#f59e0b">Each dot is one run. Compare only charts with the same workload. Numbers: docs/RESULTS.md</text>', '</g></svg>']
    return '\n'.join(elements) + '\n'
