"""Check the published files offline: data consistency, charts, links, README numbers and a scan for secrets."""
import argparse
import csv
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import charts as schema


ALLOWED_HOSTS = {'app.spheron.ai', 'www.spheron.network'}


SKIP_DIRS = {'.git', '__pycache__', '.venv', 'venv'}


def validate(root):
    files = [p for p in root.rglob('*') if p.is_file() and not SKIP_DIRS & set(p.relative_to(root).parts) and p.name != '.DS_Store']
    for p in files:
        if p.suffix == '.png':
            data = p.read_bytes()
            assert data.startswith(b'\x89PNG\r\n\x1a\n'), f'Not a PNG: {p.relative_to(root)}'
            assert not any(chunk in data for chunk in (b'tEXt', b'iTXt', b'zTXt', b'eXIf')), f'PNG metadata in {p.relative_to(root)}'
            continue
        text = p.read_text(encoding='utf-8')
        if p.suffix == '.py':
            compile(text, str(p.relative_to(root)), 'exec')
        if p.suffix == '.svg':
            tree = ET.fromstring(text)
            ns = {'svg': 'http://www.w3.org/2000/svg'}
            assert tree.find('svg:title', ns) is not None
            assert tree.find('svg:desc', ns) is not None
        if p.suffix == '.md':
            for target in re.findall(r'\]\(([^)]+)\)', text):
                if target.startswith(('http://', 'https://')):
                    continue
                path, _, anchor = target.partition('#')
                dest = (p.parent / path).resolve() if path else p
                assert dest.exists(), f'Broken link in {p.name}: {target}'
                assert dest.is_relative_to(root.resolve()), 'Link outside package'
                if anchor:
                    linked = dest.read_text(encoding='utf-8')
                    explicit = re.findall(r'<a id="([^"]+)"', linked)
                    headers = [re.sub(r'[^\w -]', '', h.lower()).replace(' ', '-') for h in re.findall(r'^#{1,6} (.+)$', linked, re.M)]
                    assert anchor in explicit + headers, f'Broken anchor {target}'
        for pattern in [r'/Us' + r'ers/[^\s]+', r'/ro' + r'ot/\w', r'(?i)Bearer\s+[A-Za-z0-9_-]{20,}', r'\bsk-' + r'[A-Za-z0-9_-]{16,}', r'-----BEGIN ' + r'(?:RSA |EC )?PRIVATE KEY-----', r'\b(?:\d{1,3}\.){3}\d{1,3}\b']:
            for match in re.finditer(pattern, text):
                assert match.group() == '127.0.0.1', f'Sensitive-looking content in {p.relative_to(root)}'
        for host in re.findall(r'(?i)\b[\w.-]+\.spheron\.[a-z]{2,}\b', text):
            assert host.lower() in ALLOWED_HOSTS, f'Unexpected Spheron hostname in {p.relative_to(root)}'
        assert '\u2014' not in text, f'Em dash in {p.relative_to(root)}'
    readme = (root/'README.md').read_text()
    payload = json.loads((root/'data/catalog.json').read_text())
    rows = payload['rows']
    summary = json.loads((root/'data/summary.json').read_text())
    fields = schema.FIELDS
    assert all(set(row) == set(fields) for row in rows)
    assert len(rows) == summary['rows']
    assert [row['row_id'] for row in rows] == list(range(1, len(rows) + 1)), 'row_id must run 1..N'
    by_origin = {row['row_id']: row for row in rows}
    assert all(row['area'] in schema.AREA_TITLES for row in rows), 'Unmapped area label'
    assert summary['rows'] + sum(summary['excluded_rows'].values()) == summary['source_rows']
    with (root/'data/catalog.csv').open(newline='') as f:
        csvrows = list(csv.DictReader(f))
    assert len(csvrows) == len(rows)
    for row, csvrow in zip(rows, csvrows):
        assert csvrow == {k: '' if row[k] is None else str(row[k]) for k in fields}
    assert len(set(r['model'] for r in rows)) == summary['models']
    assert len(set(r['gpu'] for r in rows)) == summary['gpu_models']
    assert summary['release'] == schema.RELEASE
    assert all(r[f] not in (None, '') for r in rows for f in schema.REQUIRED), 'Published row misses a required field'
    assert all(schema.number(r['req_s']) != 0 and (schema.number(r['ttft_p50_ms']) or 0) <= schema.MAX_TTFT_P50_MS for r in rows), 'Overloaded level published'
    assert len({tuple(v for k, v in r.items() if k != 'row_id') for r in rows}) == len(rows), 'Duplicate rows published'
    assert sum(schema.eligible(r) for r in rows) == summary['chart_eligible_rows']
    charts = json.loads((root/'data/charts.json').read_text())['charts']
    assert len(charts) == summary['chart_groups']
    assert sum(len(c['points']) for c in charts) == summary['plotted_rows']
    for chart in charts:
        assert set(chart['group']) == set(schema.GROUP)
        assert len(set(p['concurrency'] for p in chart['points'])) >= 2
        assert (root/chart['asset']).exists()
        for point in chart['points']:
            row = by_origin[point['row_id']]
            assert schema.eligible(row)
            assert all(row[k] == v for k, v in chart['group'].items())
            assert all(row[k] == v for k, v in point.items())
    expected_assets = {root / chart['asset'] for chart in charts}
    counts = re.findall(r'\b(\d+) charts\b', readme)
    assert counts and all(int(n) == len(charts) for n in counts), 'README chart count is stale'
    stale = sorted(set((root/'assets/charts').glob('context-*.svg')) - expected_assets)
    assert not stale, f'Charts not in the manifest: {[p.name for p in stale]}'
    for chart in charts:
        asset = (root/chart['asset']).read_text()
        assert asset == schema.svg(chart['group'], chart['points']), 'Chart differs from scripts/charts.py output'
        assert '#000000' in asset and '#050608' in asset and '#0059ff' in asset
        assert '<script' not in asset and '<filter' not in asset
    expected_lines = [f"| Published measurements | {summary['rows']} |", f"| Models / GPU models | {summary['models']} / {summary['gpu_models']} |", f"| Charts (exact contexts) / rows plotted | {len(charts)} / {summary['plotted_rows']} |", f"| Date range | {summary['date_min']} to {summary['date_max']} |"]
    for line in expected_lines:
        assert line in readme, f'Stale README count: {line}'
    findings = 0
    for tok, ttft, origin in re.findall(r'^\|[^\n]*\| ([\d,]+) \| ([\d,]+) ms \| (\d+) \|$', readme, re.M):
        row = by_origin[int(origin)]
        assert round(float(row['output_tok_s'])) == int(tok.replace(',', '')), f'README finding differs from row {origin}'
        assert round(float(row['ttft_p50_ms'])) == int(ttft.replace(',', '')), f'README TTFT differs from row {origin}'
        findings += 1
    assert findings, 'README findings table not found'
    for asset in re.findall(r'\((assets/charts/context-\d+\.svg)\)', readme):
        assert (root/asset).exists(), f'README links missing chart {asset}'
    chart_rows = {c['asset']: {p['row_id'] for p in c['points']} for c in charts}
    for para in re.findall(r'^\*\*\d+\..*$', readme, re.M):
        cited = {int(n) for n in re.findall(r'\d+', ' '.join(re.findall(r'Rows? ([\d, ]+)', para)))}
        assert all(n in by_origin for n in cited), 'README finding cites a row that is not published'
        assert cited or '(assets/charts/' not in para, 'README finding links a chart but cites no rows'
        for asset in re.findall(r'\((assets/charts/context-\d+\.svg)\)', para):
            assert chart_rows[asset] & cited, f'README finding links {asset}, which plots none of its cited rows'
    for name in ['LICENSE', 'LICENSE-DATA', 'CITATION.cff', 'docs/DATA.md']:
        assert (root/name).exists(), f'Missing {name}'
    assert 'Attribution 4.0 International' in (root/'LICENSE-DATA').read_text()
    assert (root/'LICENSE').read_text().startswith('MIT License')
    data_doc = (root/'docs/DATA.md').read_text()
    for column in schema.FIELDS:
        assert f'`{column}`' in data_doc, f'Column {column} missing from docs/DATA.md'
    results_text = (root/'docs/RESULTS.md').read_text()
    assert len(re.findall(r'^### Context ', results_text, re.M)) == len(charts)
    assert f'{len(charts)} charts plot {summary["plotted_rows"]} of the {len(rows)} rows.' in results_text
    model_text = (root/'docs/MODELS.md').read_text()
    for model in set(r['model'] for r in rows):
        subset = [r for r in rows if r['model'] == model]
        match = re.search(r'^\| \[' + re.escape(model) + r'\]\(#model-\d+\) \| (\d+) \|', model_text, re.M)
        assert match, f'Model {model} missing from MODELS.md'
        assert int(match.group(1)) == len(subset)
    button = (root/'assets/deploy-on-spheron.svg').read_text()
    assert '<script' not in button
    assert '](https://app.spheron.ai)' in readme
    citation = (root/'CITATION.cff').read_text()
    assert f"{summary['rows']} dated" in citation, 'CITATION.cff row count is stale'
    assert f"version: \"{schema.RELEASE}\"" in citation, 'CITATION.cff version is stale'
    print(json.dumps({'validated_files': len(files), 'rows': len(rows), 'charts': len(charts), 'readme_findings_checked': findings}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    validate(Path(__file__).resolve().parents[1])
