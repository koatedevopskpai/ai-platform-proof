"""Real container regression/rollback and HTTP-provider-failure/fallback drills."""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/release'))
import candidate_gate
import release
from integration_o02 import fault_bundle


def now():
    return datetime.now(timezone.utc).isoformat()


def assess(path, provenance):
    report = candidate_gate.evaluate('http://127.0.0.1:3002', ROOT / 'scripts/release/acceptance.json', provenance)
    release.save(path, report)
    return report


def main():
    output = ROOT / 'artifacts/O03-integration'; output.mkdir(parents=True, exist_ok=False)
    baseline = output / 'baseline'
    manifest = release.build(ROOT, baseline)
    source = manifest['source_revision']
    project = 'o03-smoke'
    active_cmd = ['docker', 'compose', '-p', project, '-f', str(baseline / 'compose.json')]
    release.deploy(baseline, source, output / 'baseline-receipt.json', project)
    good_images = release.running_images(active_cmd, manifest['images'])
    provenance = {'source_revision': source, 'images': good_images,
                  'manifest_sha256': release.sha(baseline / 'manifest.json')}
    fault = fault_bundle(output, baseline, manifest)
    application = {'triggered_at_utc': now(), 'trigger': 'explicit drill bypass installs controlled unsupported-answer image',
                   'operator_steps': ['docker compose up controlled fault (drill only)',
                                      'evaluate actual gateway answers', 'release.py deploy last known-good immutable bundle']}
    trigger = time.monotonic()
    # Deliberate incident simulation bypasses the normal O02 gate. Never a release option.
    release.run('docker', 'compose', '-p', project, '-f', str(fault / 'compose.json'),
                'up', '-d', '--no-build', '--pull', 'never', '--wait')
    release.wait_health('http://127.0.0.1:3002')
    fault_manifest = json.loads((fault / 'manifest.json').read_text())
    fault_cmd = ['docker', 'compose', '-p', project, '-f', str(fault / 'compose.json')]
    faulty_images = release.running_images(fault_cmd, fault_manifest['images'])
    report = assess(output / 'application-regression-report.json', dict(provenance,
        images=faulty_images, manifest_sha256=release.sha(fault / 'manifest.json'),
        controlled_fault=fault_manifest['controlled_fault']))
    assert not report['gate']['passed'], 'application regression must be observed'
    application.update(detected_at_utc=now(), detection_seconds=time.monotonic()-trigger)
    recovery_start = time.monotonic()
    release.deploy(baseline, source, output / 'application-rollback-receipt.json', project)
    assert release.running_images(active_cmd, manifest['images']) == good_images
    application.update(recovered_at_utc=now(), recovery_seconds=time.monotonic()-recovery_start,
                       recovery_quality_passed=True, restored_images=good_images)
    release.save(output / 'application-incident.json', application)

    provider = 'o03-provider'
    release.run('docker', 'run', '-d', '--name', provider, '--network', project+'_default',
                '-v', str(ROOT / 'tests/fixtures/failing_provider.py')+':/fixture.py:ro',
                'python:3.12-slim', 'python', '/fixture.py')
    for attempt in range(30):
        try:
            release.run('docker', 'exec', provider, 'python', '-c',
                        "import urllib.request; urllib.request.urlopen('http://localhost:8090').read()")
            break
        except subprocess.CalledProcessError:
            time.sleep(.2)
    else:
        raise RuntimeError('provider simulator readiness failed')
    configuration = copy.deepcopy(json.loads((baseline / 'compose.json').read_text()))
    configuration['services']['rag-api']['environment'].update(
        OPENAI_API_KEY='synthetic-provider-test-key', OPENAI_BASE_URL='http://o03-provider:8090/v1')
    provider_config = output / 'provider-failure-compose.json'; release.save(provider_config, configuration)
    incident = {'triggered_at_utc': now(), 'trigger': 'isolated HTTP provider returns 503',
                'configuration_sha256': release.sha(provider_config), 'provider': 'local simulator, no external model calls',
                'operator_steps': ['start local HTTP 503 provider', 'point running RAG at simulator',
                                   'compare fallback answers with same three acceptance cases',
                                   'restore last known-good configuration and artifact']}
    trigger = time.monotonic()
    try:
        release.run('docker', 'compose', '-p', project, '-f', str(provider_config),
                    'up', '-d', '--no-build', '--pull', 'never', '--wait', 'rag-api')
        release.wait_health('http://127.0.0.1:3002')
        first = candidate_gate.post('http://127.0.0.1:3002', '/rag', {'query': 'What is the refund window?'})
        assert first['fallback_reason'] == 'provider_http_503'
        incident.update(detected_at_utc=now(), detection_seconds=time.monotonic()-trigger)
        fallback = assess(output / 'provider-fallback-report.json', dict(provenance,
            failure_mode='simulated_provider_http_503', incident_configuration_sha256=release.sha(provider_config)))
        assert fallback['gate']['passed']
        assert all(r['fallback_reason'] == 'provider_http_503' for r in fallback['rows'])
        stats = release.run('docker', 'exec', provider, 'python', '-c',
                            "import urllib.request; print(urllib.request.urlopen('http://localhost:8090').read().decode())")
        assert json.loads(stats)['failed_provider_calls'] == 4
        incident.update(fallback_acceptance_passed=True, failed_provider_calls=4)
        recovery_start = time.monotonic()
        release.deploy(baseline, source, output / 'provider-recovery-receipt.json', project)
        restored = candidate_gate.post('http://127.0.0.1:3002', '/rag', {'query': 'What is the refund window?'})
        assert restored['fallback_reason'] == 'mock_generation'
        incident.update(recovered_at_utc=now(), recovery_seconds=time.monotonic()-recovery_start,
                        restored_images=release.running_images(active_cmd, manifest['images']))
    finally:
        release.run('docker', 'rm', '-f', provider)
    release.save(output / 'provider-incident.json', incident)
    release.save(output / 'drill-summary.json', {'source_revision': source,
        'execution_mode': 'synthetic_mock_with_local_http_503_provider', 'sample_size': 3,
        'application_regression_detected': True, 'last_known_good_restored': True,
        'provider_failure_detected': True, 'fallback_acceptance_passed': True,
        'external_provider_calls': 0})
    print('O03 application regression detected and rolled back; provider HTTP 503 fallback passed three cases.')


if __name__ == '__main__':
    main()
