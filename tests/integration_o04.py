"""Bounded real gateway load, database failure, correlated traces and local alerts."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/release'))
import incident_metrics
import release


def request(_):
    trace = uuid.uuid4().hex
    body = {'query': 'What is the refund window?', 'top_k': 3}
    req = Request('http://127.0.0.1:3002/rag', data=json.dumps(body).encode(), method='POST',
                  headers={'content-type': 'application/json', 'x-trace-id': trace,
                           'x-private-demo': 'O04-private-header-canary'})
    started = time.perf_counter(); success = False; status = 0
    try:
        with urlopen(req, timeout=20) as response:
            status = response.status; payload = json.load(response)
            success = (status == 200 and '30 days' in payload.get('answer', '').lower() and
                       'o02-refund' in payload.get('sources', []) and payload.get('fallback') is True and
                       response.headers.get('x-trace-id') == trace)
    except HTTPError as exc:
        status = exc.code
    except (URLError, TimeoutError, ValueError):
        pass
    return {'trace_id': trace, 'success': success, 'http_status': status,
            'latency_ms': round((time.perf_counter()-started)*1000, 3)}


def window(count):
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(request, range(count)))
    return {'observations': rows, 'summary': incident_metrics.summarize(rows)}


def main():
    output = ROOT / 'artifacts/O04-integration'; output.mkdir(parents=True, exist_ok=False)
    baseline = output / 'baseline'
    manifest = release.build(ROOT, baseline)
    release.deploy(baseline, manifest['source_revision'], output / 'baseline-receipt.json', 'o04-smoke')
    command = ['docker', 'compose', '-p', 'o04-smoke', '-f', str(baseline / 'compose.json')]
    healthy = window(12)
    release.save(output / 'healthy-load.json', healthy)
    assert healthy['summary']['accepted'] == 12
    trigger = datetime.now(timezone.utc).isoformat()
    release.run(*command, 'stop', 'postgres')
    try:
        failure = window(6)
        release.save(output / 'failure-load.json', failure)
        assert failure['summary']['errors'] == 6
        assert 'user_acceptance_error_rate' in failure['summary']['alerts']
    finally:
        release.run(*command, 'start', 'postgres')
    release.wait_health('http://127.0.0.1:3002')
    recovery = window(6)
    release.save(output / 'recovery-load.json', recovery)
    assert recovery['summary']['accepted'] == 6
    assert recovery['summary']['alert_state'] == 'clear'
    traces = []
    for service in ('gateway', 'rag-api'):
        container = release.run(*command, 'ps', '-q', service)
        logs = release.run('docker', 'logs', container)
        (output / (service+'-logs.txt')).write_text(logs, encoding='utf-8')
        for line in logs.splitlines():
            try: row = json.loads(line)
            except ValueError: continue
            if row.get('event') == 'span': traces.append(row)
    assert all('O04-private-header-canary' not in json.dumps(t) for t in traces)
    operations = {t['operation'] for t in traces}
    assert {'http.request','tool.ingest','retrieval.search','model.generate','embedding.embed'} <= operations
    failing_trace = failure['observations'][0]['trace_id']
    failure_spans = [t for t in traces if t['trace_id'] == failing_trace]
    assert any(t['component'] == 'pgvector' and t['status'] == 'error' for t in failure_spans)
    assert any(t['component'] == 'gateway' and t['http_status'] == 503 for t in failure_spans)
    success_trace = healthy['observations'][0]['trace_id']
    success_spans = [t for t in traces if t['trace_id'] == success_trace]
    span_ids = {t['span_id'] for t in success_spans}
    assert any(t['component'] == 'gateway' and t['operation'] == 'http.request' for t in success_spans)
    assert all(t.get('parent_span_id') in span_ids for t in success_spans if t.get('parent_span_id'))
    release.save(output / 'success-trace.json', success_spans)
    release.save(output / 'failure-trace.json', failure_spans)
    release.save(output / 'alert-events.json', {
        'mechanism': 'local bounded-window alert evaluator; no external notification service',
        'triggered_at_utc': trigger,
        'healthy': healthy['summary']['alert_state'],
        'failure': failure['summary']['alert_state'], 'recovery': recovery['summary']['alert_state'],
        'firing_alerts': failure['summary']['alerts']})
    release.save(output / 'drill-summary.json', {'source_revision': manifest['source_revision'],
        'images': manifest['images'], 'configuration_sha256': manifest['compose_sha256'],
        'execution_mode': 'synthetic_mock_actual_http', 'total_load_requests': 24, 'maximum_concurrency': 2,
        'healthy_accepted': 12, 'failure_errors': 6, 'recovery_accepted': 6,
        'trace_identifies_failed_component': 'pgvector', 'alert_demonstrated': True,
        'external_provider_calls': 0})
    print('O04: 12 healthy, 6 failed, 6 recovered requests; pgvector error trace; local alert fired and cleared.')


if __name__ == '__main__':
    main()
