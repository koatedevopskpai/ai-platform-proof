"""Bounded Linux/Docker drill; no provider calls. Not discovered as a unit test."""
import copy
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/release'))
import release



def fault_bundle(output, baseline, manifest):
    source = manifest['source_revision']
    fault = output / 'bad-candidate'; fault.mkdir()
    for filename in ('compose.json', *release.GATE_FILES):
        shutil.copy2(baseline / filename, fault / filename)
    build_context = output / 'fault-build'; build_context.mkdir()
    shutil.copy2(ROOT / 'tests/fixtures/unsupported_rag.py', build_context / 'rag.py')
    base_tag = 'ai-platform/rag-api:' + source
    if release.inspect(base_tag)['Id'] != manifest['images']['rag-api']:
        raise ValueError('fault image base tag does not resolve to baseline image ID')
    (build_context / 'Dockerfile').write_text(
        f"FROM {base_tag}\nCOPY rag.py /app/app/services/rag.py\n", encoding='utf-8')
    tag = 'ai-platform/rag-api:o02-controlled-fault'
    release.run('docker', 'build', '--label', 'org.opencontainers.image.revision=' + source,
                '--label', 'portfolio.fault=unsupported-answer', '-t', tag, str(build_context))
    faulty_manifest = copy.deepcopy(manifest)
    faulty_manifest['images']['rag-api'] = release.inspect(tag)['Id']
    faulty_manifest['controlled_fault'] = {'kind': 'unsupported-application-answer',
        'fixture_sha256': release.sha(build_context / 'rag.py')}
    compose = json.loads((fault / 'compose.json').read_text())
    compose['services']['rag-api']['image'] = faulty_manifest['images']['rag-api']
    release.save(fault / 'compose.json', compose)
    release.run('docker', 'save', '-o', str(fault / 'images.tar'), *faulty_manifest['images'].values())
    faulty_manifest['compose_sha256'] = release.sha(fault / 'compose.json')
    faulty_manifest['archive_sha256'] = release.sha(fault / 'images.tar')
    release.save(fault / 'manifest.json', faulty_manifest)
    return fault


def main():
    output = ROOT / 'artifacts/O02-integration'
    output.mkdir(parents=True, exist_ok=False)
    baseline = output / 'baseline'
    manifest = release.build(ROOT, baseline)
    source = manifest['source_revision']
    release.deploy(baseline, source, output / 'baseline-receipt.json', 'o02-smoke')
    active_cmd = ['docker', 'compose', '-p', 'o02-smoke', '-f', str(baseline / 'compose.json')]
    before = release.running_images(active_cmd, manifest['images'])
    fault = fault_bundle(output, baseline, manifest)
    try:
        release.deploy(fault, source, output / 'bad-receipt.json', 'o02-smoke')
    except ValueError as exc:
        if str(exc) != 'candidate acceptance gate failed before promotion': raise
    else:
        raise AssertionError('bad candidate was promoted')
    bad = json.loads((output / 'bad-receipt.json').read_text())
    assert bad['promotion_started'] is False and not bad['candidate_gate']['passed']
    assert release.running_images(active_cmd, manifest['images']) == before
    # A physically absent dataset must block before touching active containers.
    (fault / 'acceptance.json').unlink()
    try:
        release.deploy(fault, source, output / 'missing-dataset-receipt.json', 'o02-smoke')
    except FileNotFoundError:
        pass
    else:
        raise AssertionError('missing dataset was accepted')
    assert not json.loads((output / 'missing-dataset-receipt.json').read_text())['promotion_started']
    assert release.running_images(active_cmd, manifest['images']) == before
    release.save(output / 'drill-summary.json', {'source_revision': source,
        'baseline_passed': True, 'bad_candidate_blocked_before_promotion': True,
        'missing_dataset_blocked_before_promotion': True, 'active_images_unchanged': True,
        'execution_mode': 'synthetic_mock_actual_http', 'sample_size': 3})
    print('O02 actual HTTP baseline passed; bad application and missing dataset blocked before promotion.')


if __name__ == '__main__':
    main()
