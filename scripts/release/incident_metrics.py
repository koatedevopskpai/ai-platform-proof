"""Local bounded-smoke indicators/alerts, not a monitoring service or measured SLO."""
import math

TARGETS = {'maximum_error_rate': .05, 'p95_latency_ms': 1000}


def summarize(rows):
    if not rows or any(not isinstance(r.get('success'), bool) or
        not isinstance(r.get('latency_ms'), (int, float)) or isinstance(r['latency_ms'], bool) or
        not math.isfinite(r['latency_ms']) or r['latency_ms'] < 0 for r in rows):
        raise ValueError('missing or malformed load observations')
    latencies = sorted(r['latency_ms'] for r in rows)
    errors = sum(not r['success'] for r in rows)
    p95 = latencies[math.ceil(.95*len(latencies))-1]
    alerts = []
    if errors/len(rows) > TARGETS['maximum_error_rate']:
        alerts.append('user_acceptance_error_rate')
    if p95 > TARGETS['p95_latency_ms']:
        alerts.append('request_latency')
    return {'requests': len(rows), 'accepted': len(rows)-errors, 'errors': errors,
            'error_rate': errors/len(rows), 'p50_latency_ms': latencies[math.ceil(.5*len(latencies))-1],
            'p95_latency_ms': p95, 'percentile_method': 'nearest rank; tiny descriptive sample',
            'configured_demo_targets': TARGETS, 'alert_state': 'firing' if alerts else 'clear',
            'alerts': alerts}
